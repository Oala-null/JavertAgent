"""仅在临时目录演练补丁文件安装/回滚；不停止服务、不连接医院。"""
import ast
import importlib.util
import json
import gzip
from pathlib import Path
import shutil

import pytest

from scripts.build_243_lis_hotfix import build

ROOT=Path(__file__).parents[1]


@pytest.fixture
def install_env(tmp_path, monkeypatch):
    # 当前维护线已有后续性能补丁；旧 v1 必须从冻结代码而非当前树演练。
    frozen = tmp_path / 'v1-source'
    shutil.copytree(ROOT / 'delivery/lis_hotfix', frozen / 'delivery/lis_hotfix')
    code = json.loads(gzip.decompress((ROOT / 'tests/fixtures/249_lis_v1_code.json.gz').read_bytes()))
    for name, text in code.items():
        target = frozen / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
    monkeypatch.setattr('scripts.build_243_lis_hotfix.ROOT', frozen)
    package=build(tmp_path/'delivery')
    spec=importlib.util.spec_from_file_location('test_lis_installer',package/'installer.py')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    root=tmp_path/'hospital'
    root.mkdir()
    m=mod.manifest()
    (root/'DEPLOY_COMMIT').write_text(m['base_commit'])
    baseline=json.loads(gzip.decompress((ROOT/'delivery/lis_hotfix/baseline-code.json.gz').read_bytes()))
    for name in set(m['files'])|set(m['dependencies']):
        if name in baseline:
            target=root/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(baseline[name],encoding='utf-8')
    (root/'.env').write_text('SYNTHETIC_CONFIG_DO_NOT_TOUCH')
    backup=root/'output/lis-backup.SYNTH'
    backup.mkdir(parents=True)
    return mod,root,backup,m


def test_install_then_rollback_preserves_other_patches(install_env):
    mod,root,backup,m=install_env
    assert mod.check_versions(root,m)=='ready'
    mod.install_files(root,backup,m)
    assert mod.check_versions(root,m)=='installed'
    assert (root/'.env').read_text()=='SYNTHETIC_CONFIG_DO_NOT_TOUCH'
    for name,digest in m['dependencies'].items():
        assert mod.sha((root/name).read_bytes())==digest
    mod.rollback_files(root,backup,m)
    assert mod.check_versions(root,m)=='ready'
    for name,item in m['files'].items():
        if item['before'] is None:
            assert not (root/name).exists()


def test_unknown_modification_is_not_overwritten(install_env):
    mod,root,backup,m=install_env
    path=root/'src/javert/data/hub_source.py'
    path.write_text(path.read_text()+'\n# UNRELATED_CHANGE\n')
    original=path.read_bytes()
    with pytest.raises(RuntimeError,match='其他修改'):
        mod.install_files(root,backup,m)
    assert path.read_bytes()==original


def test_partial_install_restores_changed_files(install_env,monkeypatch):
    mod,root,backup,m=install_env
    original=mod.atomic_write
    calls=[]
    def fail_once(*args):
        calls.append(1)
        if len(calls)==3:
            raise RuntimeError('SYNTHETIC_WRITE_FAILURE')
        return original(*args)
    monkeypatch.setattr(mod,'atomic_write',fail_once)
    with pytest.raises(RuntimeError,match='SYNTHETIC_WRITE_FAILURE'):
        mod.install_files(root,backup,m)
    assert mod.check_versions(root,m)=='ready'


def test_rollback_refuses_later_change_and_bad_backup(install_env):
    mod,root,backup,m=install_env
    mod.install_files(root,backup,m)
    path=root/'src/javert/data/hub_source.py'
    good=path.read_bytes()
    path.write_bytes(good+b'\n# LATER_CHANGE')
    with pytest.raises(RuntimeError,match='后续修改'):
        mod.rollback_files(root,backup,m)
    assert path.read_bytes()!=good
    path.write_bytes(good)
    (backup/'before/src/javert/data/hub_source.py').write_text('CORRUPTED')
    with pytest.raises(RuntimeError,match='备份文件校验失败'):
        mod.rollback_files(root,backup,m)


def test_symlink_target_is_rejected(install_env,tmp_path):
    mod,root,backup,m=install_env
    path=root/'src/javert/web/static/app.js'
    external=tmp_path/'external'
    external.write_text('DO_NOT_TOUCH')
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(RuntimeError,match='软链接'):
        mod.install_files(root,backup,m)
    assert external.read_text()=='DO_NOT_TOUCH'


def test_settlement_logic_has_not_changed():
    def functions(text):
        return {n.name:ast.dump(n) for n in ast.parse(text).body if isinstance(n,ast.FunctionDef)}
    baseline=json.loads(gzip.decompress((ROOT/'delivery/lis_hotfix/baseline-code.json.gz').read_bytes()))
    before=functions(baseline['src/javert/data/hub_source.py'])
    after=functions((ROOT/'src/javert/data/hub_source.py').read_text())
    for name in ('fetch_fees','settlement_fee_snapshot','validate_settlement_source','validate_settlement_snapshot'):
        assert after[name]==before[name]
