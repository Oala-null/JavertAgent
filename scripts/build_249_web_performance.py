#!/usr/bin/env python3
"""从已提交维护线构建新性能补丁目录；不覆盖旧包，不读取配置或患者资料。"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = 'Javert-249-Web-performance-v1'
SUPPORT = ('installer.py', 'install.sh', 'stop_web.py', 'README_zh.md', 'VALIDATION.md')


def build(destination, ref='HEAD'):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args])
    commit = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    def read(name):
        return git('show', f'{commit}:{name}')
    prefix = 'delivery/web_performance/'
    manifest = json.loads(read(prefix + 'v1-manifest.json'))
    manifest['source_commit'] = commit
    assets = {f'files/{name}': read(name) for name in manifest['files']}
    for name, item in manifest['files'].items():
        if hashlib.sha256(assets['files/' + name]).hexdigest() != item['after']:
            raise RuntimeError('此提交的源码已变化，必须另立新补丁版本：' + name)
    for name, digest in manifest['dependencies'].items():
        if hashlib.sha256(read(name)).hexdigest() != digest:
            raise RuntimeError('此提交的依赖与补丁基线不同：' + name)
    assets.update({name: read(prefix + name) for name in SUPPORT})
    assets['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    known = json.loads(read('deploy/249/known-release.json'))
    known['files'].update({name: item['after'] for name, item in manifest['files'].items()})
    known['hotfix_paths'] = sorted(set(known['hotfix_paths']) | set(manifest['files']))
    known['evidence'] = '本地提交构建的性能补丁目标状态；未验证院内磁盘或进程。'
    assets['known-release-after.json'] = (json.dumps(known, ensure_ascii=False, indent=2) + '\n').encode()
    assets['check_249_hotfix.py'] = read('scripts/check_249_hotfix.py')
    package = Path(destination) / PACKAGE_NAME
    package.mkdir(parents=True, exist_ok=False)
    for name, data in assets.items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755 if name == 'install.sh' else 0o644)
    checksums = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                        for name, data in sorted(assets.items()))
    (package / 'SHA256SUMS').write_text(checksums, encoding='utf-8')
    return package


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', nargs='?', type=Path, default=ROOT / 'JiaZhongXin/Releases')
    parser.add_argument('--ref', default='HEAD')
    args = parser.parse_args()
    print(build(args.destination, args.ref))
