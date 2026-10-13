"""完整性能v2：两种冻结代码起点，不依赖医院数据或Git工作树。"""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]


def frozen(name):
    return json.loads(gzip.decompress((ROOT / 'tests/fixtures' / name).read_bytes()))


@pytest.fixture(params=['history-dates-v1'])
def install_env(tmp_path, request):
    package = tmp_path / 'package'
    shutil.copytree(ROOT / 'delivery/web_performance_v2', package)
    shutil.copyfile(package / 'v2-manifest.json', package / 'manifest.json')
    m = json.loads((package / 'manifest.json').read_text())
    for name in m['files']:
        target = package / 'files' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    spec = importlib.util.spec_from_file_location('performance_v2_installer', package / 'installer.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.manifest()
    code = frozen('249_lis_v1_code.json.gz')
    code.update(frozen('249_web_performance_v2_baseline_extra.json.gz'))
    code.update(frozen('249_history_dates_v1_code.json.gz'))
    root = tmp_path / 'hospital'
    root.mkdir()
    (root / 'DEPLOY_COMMIT').write_text(m['base_commit'])
    for name in set(m['files']) | set(m['dependencies']):
        if name in code:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code[name], encoding='utf-8')
    (root / '.env').write_bytes(b'SYNTHETIC_CONFIG_DO_NOT_TOUCH')
    (root / '.env').chmod(0o600)
    (root / 'data').mkdir()
    (root / 'data/SYNTHETIC.sqlite').write_bytes(b'SYNTHETIC_DATA_DO_NOT_TOUCH')
    backup = root / 'output/web-performance-backup.SYNTHETIC'
    backup.mkdir(parents=True, mode=0o700)
    known = json.loads((package / 'baseline-known-release.json').read_text())
    known['files'].update({name: entry['after'] for name, entry in m['files'].items()})
    (package / 'known-release-after.json').write_text(json.dumps(known))
    return mod, root, backup, m, request.param


def test_both_baselines_install_verify_idempotent_and_exact_rollback(install_env):
    mod, root, backup, m, baseline = install_env
    before = mod.file_states(root, m)
    assert mod.check_versions(root, m) == 'ready'
    assert mod.baseline_for(before, m) == baseline
    assert len(m['files']) == 9
    mod.install_files(root, backup, m)
    assert mod.check_versions(root, m) == 'installed'
    receipt = json.loads((backup / 'receipt.json').read_text())
    assert receipt['baseline'] == baseline
    with pytest.raises(RuntimeError, match='已经安装'):
        mod.install_files(root, backup, m)
    assert (root / '.env').read_bytes() == b'SYNTHETIC_CONFIG_DO_NOT_TOUCH'
    assert (root / 'data/SYNTHETIC.sqlite').read_bytes() == b'SYNTHETIC_DATA_DO_NOT_TOUCH'
    for name, digest in m['dependencies'].items():
        assert mod.sha((root / name).read_bytes()) == digest
    mod.rollback_files(root, backup, m)
    assert mod.file_states(root, m) == before
    assert mod.baseline_for(mod.file_states(root, m), m) == baseline
    assert mod.check_versions(root, m) == 'ready'
    assert not (root / 'src/javert/web/templates/_patient_cards.html').exists()


def test_install_write_failure_restores_actual_baseline(install_env, monkeypatch):
    mod, root, backup, m, _ = install_env
    before = mod.file_states(root, m)
    write = mod.atomic_write
    calls = []

    def fail_once(*args):
        calls.append(1)
        if len(calls) == 3:
            raise OSError('SYNTHETIC disk failure')
        return write(*args)

    monkeypatch.setattr(mod, 'atomic_write', fail_once)
    with pytest.raises(OSError, match='SYNTHETIC'):
        mod.install_files(root, backup, m)
    assert mod.file_states(root, m) == before


def test_mixed_or_unknown_versions_cannot_write(install_env):
    mod, root, backup, m, baseline = install_env
    name = 'src/javert/web/static/app.js'
    other = frozen('249_web_performance_v1_code.json.gz')
    (root / name).write_text(other[name], encoding='utf-8')
    before = mod.file_states(root, m)
    with pytest.raises(RuntimeError, match='混合版本'):
        mod.install_files(root, backup, m)
    assert mod.file_states(root, m) == before
    assert not (backup / 'receipt.json').exists()
    (root / name).write_text('SYNTHETIC_UNKNOWN')
    with pytest.raises(RuntimeError, match='其他修改'):
        mod.check_versions(root, m)


def test_rollback_rejects_damage_and_later_edits_before_any_write(install_env):
    mod, root, backup, m, _ = install_env
    mod.install_files(root, backup, m)
    name = 'src/javert/web/static/app.js'
    original = (backup / 'before' / name).read_bytes()
    (backup / 'before' / name).write_bytes(b'SYNTHETIC_CORRUPT')
    with pytest.raises(RuntimeError, match='备份文件校验失败'):
        mod.rollback_files(root, backup, m)
    assert mod.check_versions(root, m) == 'installed'
    (backup / 'before' / name).write_bytes(original)
    (root / name).write_bytes(b'SYNTHETIC_LATER_EDIT')
    before = mod.file_states(root, m)
    with pytest.raises(RuntimeError, match='后续修改'):
        mod.rollback_files(root, backup, m)
    assert mod.file_states(root, m) == before


def test_dependencies_payload_and_symlinks_rejected(install_env, tmp_path):
    mod, root, backup, m, _ = install_env
    dependency = root / 'src/javert/data/hub_source.py'
    original = dependency.read_bytes()
    dependency.write_bytes(b'SYNTHETIC_EDIT')
    with pytest.raises(RuntimeError, match='依赖文件'):
        mod.install_files(root, backup, m)
    dependency.write_bytes(original)
    name = 'src/javert/web/static/app.js'
    external = tmp_path / 'SYNTHETIC_EXTERNAL'
    external.write_bytes(b'DO_NOT_TOUCH')
    (root / name).unlink()
    (root / name).symlink_to(external)
    with pytest.raises(RuntimeError, match='软链接'):
        mod.install_files(root, backup, m)
    assert external.read_bytes() == b'DO_NOT_TOUCH'
    (mod.PACKAGE / 'files' / name).write_bytes(b'SYNTHETIC_TAMPERED')
    with pytest.raises(RuntimeError, match='摘要校验失败'):
        mod.manifest()


def test_busy_lock_blocks_install(install_env):
    import fcntl
    from contextlib import ExitStack
    mod, root, _, _, _ = install_env
    with (root / 'output/.249-batch.lock').open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ExitStack() as stack, pytest.raises(RuntimeError, match='持锁'):
            mod.idle_locks(root, stack)


def test_runtime_preflight_isolated_from_application(install_env):
    mod, root, _, _, _ = install_env
    with pytest.raises(RuntimeError, match='未找到应用'):
        mod.check_runtime(root)
    (root / '.venv').symlink_to(Path(sys.prefix), target_is_directory=True)
    result = subprocess.run([sys.executable, '-B', str(mod.PACKAGE / 'installer.py'), 'check', '--root', str(root)],
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'GZIP_SSE_RUNTIME=PASS' in result.stdout and 'PATCH_STATE=READY' in result.stdout
    assert 'SYNTHETIC_CONFIG' not in result.stdout + result.stderr


def test_payload_does_not_touch_batch_code_fingerprint():
    import ast
    tree = ast.parse((ROOT / 'scripts/run_243_lis_batch.py').read_text())
    batch = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'CODE_FILES' for t in n.targets))
    m = json.loads((ROOT / 'delivery/web_performance_v2/v2-manifest.json').read_text())
    assert not set(batch) & set(m['files'])


def test_busy_watch_lock_blocks_mutation(install_env):
    import fcntl
    from contextlib import ExitStack
    mod, root, _, _, _ = install_env
    state = root / 'output/lis-watch-v1'
    state.mkdir()
    with (state / 'service.lock').open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ExitStack() as stack, pytest.raises(RuntimeError, match='持续监测仍运行'):
            mod.idle_locks(root, stack)


def load_watch(package):
    spec = importlib.util.spec_from_file_location('performance_watch_adapter', package / 'watch/launch.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_watch_adapter_keeps_original_context_and_ledger_untouched(install_env, monkeypatch, tmp_path):
    import sqlite3
    mod, root, backup, m, _ = install_env
    target = json.loads((mod.PACKAGE / 'known-release-after.json').read_text())
    for name, expected in target['files'].items():
        path = root / name
        if not path.exists():
            data = (ROOT / name).read_bytes()
            assert mod.sha(data) == expected
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    # 新增模板在安装前必须不存在。
    (root / 'src/javert/web/templates/_patient_cards.html').unlink()
    mod.install_files(root, backup, m)
    helper = tmp_path / 'SYNTHETIC/start.sh'
    helper.parent.mkdir()
    helper.write_text('SYNTHETIC')
    helper.with_name('effective-config.json').write_text('SYNTHETIC_CONFIG')
    cfg = SimpleNamespace(hub_linkage_mode='shanghai')
    monkeypatch.setitem(sys.modules, 'run_243_lis_batch', SimpleNamespace(runtime_profile=lambda: (helper, cfg, None)))
    monkeypatch.delitem(sys.modules, 'ledger', raising=False)
    monkeypatch.syspath_prepend(str(mod.PACKAGE / 'watch'))
    adapter = load_watch(mod.PACKAGE)
    watcher = adapter.prepare(mod.PACKAGE / 'watch')
    runtime = watcher.Runtime(root)
    fields = ('hub_hospital_code','hub_linkage_mode','hub_database','sql_host','sql_port','sql_database',
              'llm_model','llm_endpoint','llm_temperature','llm_max_tokens','llm_enable_thinking',
              'max_tool_calls','precheck','verdict_gate','drift_guard')
    old = json.loads((mod.PACKAGE / 'baseline-known-release.json').read_text())
    context = {'runtime': old['files'], 'settings': {k: getattr(cfg, k, None) for k in fields}, 'watcher': m['watch_files']}
    expected_context = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    assert runtime.context == expected_context
    ledger = root / 'output/SYNTHETIC-ledger.sqlite'
    with sqlite3.connect(ledger) as connection:
        connection.execute('CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)')
        connection.execute('INSERT INTO meta VALUES (?, ?)', ('context', expected_context))
    before = ledger.read_bytes()
    runtime.guard()
    assert ledger.read_bytes() == before
    assert runtime.release['files'] == old['files']
    # 非性能代码和适配器自身发生变化时仍拒绝运行。
    code = root / 'src/javert/data/hub_source.py'
    original = code.read_bytes()
    code.write_bytes(b'SYNTHETIC_UNAPPROVED')
    with pytest.raises(RuntimeError, match='RUNTIME_CODE_CHANGED'):
        runtime.guard()
    code.write_bytes(original)
    (mod.PACKAGE / 'watch/launch.py').write_text('SYNTHETIC_CHANGED')
    with pytest.raises(RuntimeError, match='WATCH_PERFORMANCE_PACKAGE_CHANGED'):
        runtime.guard()


def test_watch_policy_rejects_non_performance_scope(install_env):
    mod, _, _, _, _ = install_env
    adapter = load_watch(mod.PACKAGE)
    path = mod.PACKAGE / 'known-release-after.json'
    value = json.loads(path.read_text())
    value['files']['src/javert/data/hub_source.py'] = '0' * 64
    path.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match='WATCH_NON_PERFORMANCE_CHANGE'):
        adapter.load_policy(mod.PACKAGE / 'watch')


@pytest.mark.parametrize('with_performance_v1', [False, True])
def test_without_installed_date_patch_is_rejected(install_env, with_performance_v1):
    mod, root, backup, m, _ = install_env
    code = frozen('249_lis_v1_code.json.gz')
    code.update(frozen('249_web_performance_v2_baseline_extra.json.gz'))
    if with_performance_v1:
        code.update(frozen('249_web_performance_v1_code.json.gz'))
    for name in set(m['files']) | set(m['dependencies']):
        path = root / name
        if name in code:
            path.write_text(code[name], encoding='utf-8')
        else:
            path.unlink(missing_ok=True)
    before = mod.file_states(root, m)
    with pytest.raises((RuntimeError, FileNotFoundError)):
        mod.install_files(root, backup, m)
    assert mod.file_states(root, m) == before
    assert not (backup / 'receipt.json').exists()
