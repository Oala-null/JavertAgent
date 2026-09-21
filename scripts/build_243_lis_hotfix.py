#!/usr/bin/env python3
"""仅重建已签名v1的运行源码；摘要改变时必须另立版本，不覆盖原交付。"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    destination = Path(destination)
    template = ROOT / 'delivery/lis_hotfix'
    manifest = json.loads((template / 'v1-manifest.json').read_text())
    expected = {**{n: v['after'] for n, v in manifest['files'].items()}, **manifest['dependencies']}
    for name, digest in expected.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('v1受控代码已改变；请创建新补丁版本：' + name)
    package = destination / 'Javert-249-LIS-hotfix-v1'
    archive = destination / (package.name + '.zip')
    if package.exists() or archive.exists():
        raise RuntimeError('交付路径已存在，拒绝覆盖')
    package.mkdir(parents=True)
    for name, item in manifest['files'].items():
        target = package / 'files' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        target.chmod(item['mode'])
    for name in ('installer.py', 'install.sh', 'README_zh.md', 'START_HERE.txt', 'VALIDATION.md'):
        shutil.copyfile(template / name, package / name)
    shutil.copyfile(template / 'v1-manifest.json', package / 'manifest.json')
    (package / 'install.sh').chmod(0o755)
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    files = sorted(p for p in package.rglob('*') if p.is_file())
    (package / 'SHA256SUMS').write_text(''.join(digest(p) + '  ' + p.relative_to(package).as_posix() + '\n' for p in files))
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(package.rglob('*')):
            if p.is_file():
                z.write(p, p.relative_to(destination))
    Path(str(archive) + '.sha256').write_text(digest(archive) + '  ' + archive.name + '\n')
    return package


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('用法：python3 scripts/build_243_lis_hotfix.py <新的本地目录>')
    print(build(Path(sys.argv[1]).resolve()))
