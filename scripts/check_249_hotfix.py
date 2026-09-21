#!/usr/bin/env python3
"""已知249发行版只读文件核验；仅标准库，不导入应用、不读取配置/患者/日志。"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket


def validate_manifest(manifest):
    if manifest.get('version') != 1 or not re.fullmatch(r'[a-f0-9]{40}', manifest.get('base_commit', '')):
        raise ValueError('MANIFEST_INVALID')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise ValueError('MANIFEST_FILES_INVALID')
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (path.is_absolute() or path.as_posix() != name or '\\' in name
                or any(p in {'.', '..'} or p.startswith('.') for p in path.parts)
                or name in {'configs/llm.yaml', 'configs/hospital_config.yaml'}
                or not (name.startswith(('src/', 'scripts/', 'configs/', 'data/router/')) or name in {'pyproject.toml', 'uv.lock'})
                or not re.fullmatch(r'[a-f0-9]{64}', digest)):
            raise ValueError('MANIFEST_PATH_OR_HASH_INVALID')
    if not set(manifest.get('hotfix_paths', [])) <= files.keys():
        raise ValueError('MANIFEST_HOTFIX_SCOPE_INVALID')


def compare(root, manifest):
    validate_manifest(manifest)
    root = Path(root)
    errors = []
    newest = 0.0
    for name, expected in manifest['files'].items():
        path = root / name
        try:
            if any((root / Path(*PurePosixPath(name).parts[:n])).is_symlink()
                   for n in range(1, len(PurePosixPath(name).parts) + 1)):
                state = 'SYMLINK'
            elif not path.is_file():
                state = 'MISSING'
            else:
                before = path.stat()
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                after = path.stat()
                stable = (before.st_mtime_ns, before.st_size, before.st_ino) == (after.st_mtime_ns, after.st_size, after.st_ino)
                state = 'CHANGED_DURING_CHECK' if not stable else ('MATCH' if digest == expected else 'DIFFERENT')
                newest = max(newest, after.st_mtime)
        except OSError:
            state = 'UNREADABLE'
        if state != 'MATCH':
            errors.append({'path': name, 'status': state})
    return {'disk_match': not errors, 'controlled_files': len(manifest['files']),
            'hotfix_files': len(manifest['hotfix_paths']),
            'hotfix_match': not any(e['path'] in manifest['hotfix_paths'] for e in errors),
            'differences': errors, 'newest_controlled_mtime': newest}


def process_evidence(root, newest):
    """观察同目录javert web进程与文件时间；不冒充内存代码证明。"""
    proc = Path('/proc')
    if not proc.is_dir():
        return {'status': 'UNKNOWN_NOT_LINUX', 'loaded_code_verified': False}
    found = []
    try:
        boot = next(int(line.split()[1]) for line in (proc / 'stat').read_text().splitlines() if line.startswith('btime '))
        ticks = os.sysconf('SC_CLK_TCK')
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                args = (entry / 'cmdline').read_bytes().split(b'\0')
                if b'javert.cli' not in args or b'web' not in args or (entry / 'cwd').resolve() != root.resolve():
                    continue
                started = boot + int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[19]) / ticks
                found.append({'pid': int(entry.name), 'started_utc': datetime.fromtimestamp(started, timezone.utc).isoformat(),
                              'started_after_code_mtime': started >= newest})
            except (OSError, ValueError, IndexError):
                continue
    except (OSError, ValueError, StopIteration):
        return {'status': 'UNKNOWN_PROC_UNREADABLE', 'loaded_code_verified': False}
    return {'status': 'OBSERVED' if found else 'UNKNOWN_NO_MATCHING_WEB',
            'web_processes': found, 'loaded_code_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/home/admin2/Javert'))
    parser.add_argument('--manifest', type=Path, default=Path(__file__).resolve().parents[1] / 'deploy/249/known-release.json')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    result = compare(args.root, manifest)
    stamp = args.root / 'DEPLOY_COMMIT'
    try:
        stamp_ok = not stamp.is_symlink() and stamp.read_text().strip() == manifest['base_commit']
    except OSError:
        stamp_ok = None
    result.update(hostname=socket.gethostname(), root=str(args.root.resolve()),
                  checked_at=datetime.now(timezone.utc).isoformat(), base_commit=manifest['base_commit'],
                  deploy_stamp_match=stamp_ok,
                  process=process_evidence(args.root, result.pop('newest_controlled_mtime')),
                  note='仅证明所列磁盘文件；配置、额外文件、数据库和进程已加载模块不在摘要证明范围。未停止或启动服务。')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print('HOTFIX_FILES=' + ('PASS' if result['hotfix_match'] else 'FAIL'))
    print('KNOWN_RELEASE_FILES=' + ('PASS' if result['disk_match'] else 'FAIL'))
    print('DEPLOY_STAMP=' + ('MATCH' if stamp_ok else 'MISSING' if stamp_ok is None else 'DIFFERENT'))
    print('LOADED_PROCESS_CODE=UNVERIFIED')
    return 0 if result['disk_match'] and stamp_ok is not False else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit('CHECK_FAILED=' + type(exc).__name__ + '；未修改任何文件或服务。') from None
