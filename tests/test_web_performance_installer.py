"""只用冻结代码和临时目录验证新补丁，不接触医院、配置或患者数据。"""
import gzip
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture
def install_env(tmp_path):
    package = tmp_path / 'package'
    shutil.copytree(ROOT / 'delivery/web_performance', package)
    shutil.copyfile(package / 'v1-manifest.json', package / 'manifest.json')
    m = json.loads((package / 'manifest.json').read_text())
    for name in m['files']:
        target = package / 'files' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    spec = importlib.util.spec_from_file_location('performance_installer', package / 'installer.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    m = mod.manifest()
    root = tmp_path / 'hospital'
    root.mkdir()
    (root / 'DEPLOY_COMMIT').write_text(m['base_commit'])
    code = json.loads(gzip.decompress((ROOT / 'tests/fixtures/249_lis_v1_code.json.gz').read_bytes()))
    for name in set(m['files']) | set(m['dependencies']):
        if name in code:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(code[name], encoding='utf-8')
    (root / '.env').write_text('SYNTHETIC_CONFIG_DO_NOT_TOUCH')
    (root / '.env').chmod(0o600)
    backup = root / 'output/web-performance-backup.SYNTHETIC'
    backup.mkdir(parents=True, mode=0o700)
    return mod, root, backup, m


def test_install_verify_idempotent_and_rollback_preserve_configuration(install_env):
    mod, root, backup, m = install_env
    assert mod.check_versions(root, m) == 'ready'
    mod.install_files(root, backup, m)
    assert mod.check_versions(root, m) == 'installed'
    with pytest.raises(RuntimeError, match='已经安装'):
        mod.install_files(root, backup, m)
    assert (root / '.env').read_text() == 'SYNTHETIC_CONFIG_DO_NOT_TOUCH'
    for name, digest in m['dependencies'].items():
        assert mod.sha((root / name).read_bytes()) == digest
    mod.rollback_files(root, backup, m)
    assert mod.check_versions(root, m) == 'ready'
    assert not (root / 'src/javert/web/templates/_patient_cards.html').exists()


def test_partial_write_failure_restores_every_modified_file(install_env, monkeypatch):
    mod, root, backup, m = install_env
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
    assert mod.check_versions(root, m) == 'ready'


def test_modified_target_and_dependency_are_rejected(install_env):
    mod, root, backup, m = install_env
    path = root / 'src/javert/web/static/app.js'
    original = path.read_bytes()
    path.write_bytes(original + b'\n// SYNTHETIC later change')
    with pytest.raises(RuntimeError, match='其他修改'):
        mod.install_files(root, backup, m)
    assert path.read_bytes() != original
    path.write_bytes(original)
    (root / 'src/javert/data/hub_source.py').write_text('SYNTHETIC mismatch')
    with pytest.raises(RuntimeError, match='依赖文件'):
        mod.install_files(root, backup, m)


def test_rollback_validates_all_backups_before_changing_any_file(install_env):
    mod, root, backup, m = install_env
    mod.install_files(root, backup, m)
    name = 'src/javert/web/static/app.js'
    (backup / 'before' / name).write_text('SYNTHETIC damaged backup')
    with pytest.raises(RuntimeError, match='备份文件校验失败'):
        mod.rollback_files(root, backup, m)
    assert mod.check_versions(root, m) == 'installed'


def test_payload_tampering_and_symlink_targets_are_rejected(install_env, tmp_path):
    mod, root, backup, m = install_env
    name = 'src/javert/web/static/app.js'
    external = tmp_path / 'SYNTHETIC-external'
    external.write_text('DO_NOT_TOUCH')
    path = root / name
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(RuntimeError, match='软链接'):
        mod.install_files(root, backup, m)
    assert external.read_text() == 'DO_NOT_TOUCH'
    (mod.PACKAGE / 'files' / name).write_text('SYNTHETIC payload changed')
    with pytest.raises(RuntimeError, match='摘要校验失败'):
        mod.manifest()


def test_busy_batch_lock_blocks_mutation(install_env):
    import fcntl
    from contextlib import ExitStack
    mod, root, _, _ = install_env
    with (root / 'output/.249-batch.lock').open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with ExitStack() as stack, pytest.raises(RuntimeError, match='持锁'):
            mod.idle_locks(root, stack)


@pytest.mark.parametrize('count', [0, 1, 2])
def test_stop_web_only_signals_unique_same_instance(tmp_path, monkeypatch, count):
    script = ROOT / 'delivery/web_performance/stop_web.py'
    spec = importlib.util.spec_from_file_location('performance_stop_web', script)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    root = tmp_path / 'hospital'; root.mkdir()
    other = tmp_path / 'other'; other.mkdir()
    proc = tmp_path / 'proc'; proc.mkdir()
    for i in range(3):
        entry = proc / str(100 + i); entry.mkdir()
        (entry / 'cwd').symlink_to(root if i < count else other, target_is_directory=True)
        (entry / 'cmdline').write_bytes(b'python\0-m\0javert.cli\0web\0')
        (entry / 'stat').write_text(f'{100+i} (python) S ' + '0 ' * 18 + '42')
    original_path = Path
    # 只替换测试模块里的 /proc 入口，绝不访问或终止真实进程。
    monkeypatch.setattr(mod, 'Path', lambda value: proc if str(value) == '/proc' else original_path(value))
    signals = []
    def signal_process(pid, sig):
        signals.append((pid, sig))
        path = proc / str(pid) / 'stat'
        path.write_text(path.read_text().replace('(python) S', '(python) Z'))
    monkeypatch.setattr(mod.os, 'kill', signal_process)
    if count > 1:
        with pytest.raises(RuntimeError, match='多个 Web'):
            mod.stop(root)
        assert not signals
    else:
        mod.stop(root)
        assert len(signals) == count
        if count:
            assert signals[0][0] == 100
