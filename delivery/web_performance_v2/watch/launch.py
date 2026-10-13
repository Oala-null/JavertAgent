#!/usr/bin/env python3
"""仅适配已知Web性能文件校验；复用日期版监测器及原账本context。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

PACKAGE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True


def digest(path):
    if path.is_symlink():
        raise RuntimeError('WATCH_PACKAGE_SYMLINK')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_policy(package=PACKAGE):
    release = package.parent
    spec = importlib.util.spec_from_file_location('performance_install_check', release / 'installer.py')
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    manifest = installer.manifest()
    expected_watch = manifest.get('watch_files', {})
    if set(expected_watch) != {'watcher.py', 'ledger.py', 'check_249_hotfix.py', 'known-release.json'}:
        raise RuntimeError('WATCH_PACKAGE_SCOPE_MISMATCH')
    if any(digest(package / name) != expected for name, expected in expected_watch.items()):
        raise RuntimeError('WATCH_BASELINE_MISMATCH')
    old = json.loads((package / 'known-release.json').read_text())
    target = json.loads((release / 'known-release-after.json').read_text())
    if old['base_commit'] != manifest['base_commit'] or target['base_commit'] != old['base_commit']:
        raise RuntimeError('WATCH_BASE_COMMIT_MISMATCH')
    if manifest['baselines']['history-dates-v1'] != {name: old['files'].get(name) for name in installer.ALLOW}:
        raise RuntimeError('WATCH_PERFORMANCE_BASE_MISMATCH')
    expected = {**old['files'], **{name: item['after'] for name, item in manifest['files'].items()}}
    if target['files'] != expected:
        raise RuntimeError('WATCH_NON_PERFORMANCE_CHANGE')
    extra = [package / 'launch.py', release / 'installer.py',
             release / 'manifest.json', release / 'known-release-after.json']
    return target, {path: digest(path) for path in extra}


def prepare(package=PACKAGE):
    target, guards = load_policy(package)
    sys.path.insert(0, str(package))
    spec = importlib.util.spec_from_file_location('performance_watch_legacy', package / 'watcher.py')
    watcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watcher)

    def verify_code(runtime):
        if any(digest(path) != expected for path, expected in guards.items()):
            raise RuntimeError('WATCH_PERFORMANCE_PACKAGE_CHANGED')
        if not runtime.compare(runtime.root, target)['disk_match']:
            raise RuntimeError('RUNTIME_CODE_CHANGED')

    # 审计代码与规则未变；保留原release和四个watch文件，使账本context逐字不变。
    watcher.Runtime.verify_code = verify_code
    return watcher


if __name__ == '__main__':
    try:
        prepare().main()
    except BlockingIOError:
        raise SystemExit('WATCH_ALREADY_RUNNING；未重复启动。') from None
    except Exception as exc:
        reason = str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{0,90}', str(exc)) else type(exc).__name__
        raise SystemExit('WATCH_STOPPED=' + reason + '；状态和账本保留。') from None
