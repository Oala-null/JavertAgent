#!/usr/bin/env python3
"""249 工作台性能增量补丁；纯标准库，不读取配置/患者数据，不连接数据库。"""
import argparse
import ast
from contextlib import ExitStack
import fcntl
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

PACKAGE = Path(__file__).resolve().parent
PATCH_ID = '249-workbench-performance-v1'
ALLOW = {
    'src/javert/store/sqlserver_store.py',
    'src/javert/web/api/routes_workbench.py',
    'src/javert/web/static/app.js',
    'src/javert/web/templates/_sidebar.html',
    'src/javert/web/templates/_patient_cards.html',
}
DEPENDENCIES = {
    'src/javert/store/models.py', 'src/javert/web/api/main.py',
    'src/javert/web/templates/patient_detail.html', 'src/javert/web/templates/base.html',
    'src/javert/web/templating.py', 'src/javert/web/auth.py',
    'src/javert/web/patient_overview.py', 'src/javert/web/hub_raw_source.py',
    'src/javert/data/hub_source.py', 'src/javert/web/static/style.css',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, relative):
    if relative not in ALLOW | DEPENDENCIES:
        raise RuntimeError('文件不在本补丁白名单')
    if root.is_symlink():
        raise RuntimeError('受控根目录存在软链接')
    p = root
    for part in Path(relative).parts:
        p = p / part
        if p.is_symlink():
            raise RuntimeError('受控文件路径存在软链接')
    return p


def check_versions(root, m, rollback=False):
    if (root / 'DEPLOY_COMMIT').is_symlink() or (root / 'DEPLOY_COMMIT').read_text().strip() != m['base_commit']:
        raise RuntimeError('当前DEPLOY_COMMIT不是本补丁基线')
    for name,digest in m['dependencies'].items():
        if sha(safe_path(root,name).read_bytes()) != digest:
            raise RuntimeError('依赖文件版本不同，停止覆盖')
    states = {n: sha(safe_path(root, n).read_bytes()) if safe_path(root, n).exists() else None for n in m['files']}
    if not rollback and all(states[n] == e['after'] for n, e in m['files'].items()):
        return 'installed'
    key = 'after' if rollback else 'before'
    if any(states[n] != e[key] for n, e in m['files'].items()):
        raise RuntimeError('受控文件有其他修改或混合版本，未覆盖任何文件')
    return 'ready'


def atomic_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.lis-hotfix-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install_files(root, backup, m):
    if check_versions(root, m) != 'ready':
        raise RuntimeError('补丁已经安装')
    receipt = {'patch_id': m['patch_id'], 'base_commit': m['base_commit'], 'files': {}}
    for name, e in m['files'].items():
        p = safe_path(root, name)
        mode = stat.S_IMODE(p.stat().st_mode) if p.exists() else e['mode']
        receipt['files'][name] = dict(before=e['before'], after=e['after'], mode=mode)
        if p.exists():
            b = backup / 'before' / name
            b.parent.mkdir(parents=True, exist_ok=True)
            b.write_bytes(p.read_bytes())
    (backup / 'receipt.json').write_text(json.dumps(receipt, indent=2))
    changed = []
    try:
        for name, e in m['files'].items():
            p = safe_path(root, name)
            current = sha(p.read_bytes()) if p.exists() else None
            if current != e['before']:
                raise RuntimeError('安装期间目标文件改变')
            atomic_write(p, safe_path(PACKAGE / 'files', name).read_bytes(), receipt['files'][name]['mode'])
            changed.append(name)
    except BaseException:
        for name in reversed(changed):
            entry = receipt['files'][name]
            if entry['before'] is None:
                safe_path(root, name).unlink()
            else:
                atomic_write(safe_path(root, name), (backup / 'before' / name).read_bytes(), entry['mode'])
        raise
    print('PATCH_INSTALLED=PASS\nBACKUP=' + str(backup), flush=True)


def validate_rollback(root, backup, m):
    if backup.is_symlink() or (backup / 'receipt.json').is_symlink() or (backup / 'before').is_symlink():
        raise RuntimeError('备份路径存在软链接')
    receipt = json.loads((backup / 'receipt.json').read_text())
    if receipt['patch_id'] != m['patch_id'] or set(receipt['files']) != ALLOW:
        raise RuntimeError('回滚备份不属于此补丁')
    if (root / 'DEPLOY_COMMIT').is_symlink() or receipt.get('base_commit') != m['base_commit'] or (root / 'DEPLOY_COMMIT').read_text().strip() != m['base_commit']:
        raise RuntimeError('回滚基线已变化，停止')
    for name,digest in m['dependencies'].items():
        if sha(safe_path(root,name).read_bytes()) != digest:
            raise RuntimeError('依赖文件已有后续改动，停止回滚')
    for name, e in receipt['files'].items():
        if e['before'] != m['files'][name]['before'] or e['after'] != m['files'][name]['after']:
            raise RuntimeError('回滚清单摘要不一致')
        p = safe_path(root, name)
        actual = sha(p.read_bytes()) if p.exists() else None
        if actual not in (e['before'], e['after']):
            raise RuntimeError('目标有后续修改，不自动覆盖')
        if e['before'] is not None and sha(safe_path(backup / 'before', name).read_bytes()) != e['before']:
            raise RuntimeError('备份文件校验失败')
    return receipt


def rollback_files(root, backup, m):
    receipt = validate_rollback(root, backup, m)
    for name, e in receipt['files'].items():
        p = safe_path(root, name)
        if e['before'] is None:
            p.unlink(missing_ok=True)
        else:
            atomic_write(p, (backup / 'before' / name).read_bytes(), e['mode'])
    print('ROLLBACK=PASS；仅恢复本次性能补丁，既有LIS/结算补丁、历史数据及配置保留。', flush=True)


def manifest():
    m = json.loads((PACKAGE / 'manifest.json').read_text())
    if (m.get('patch_id') != PATCH_ID or set(m.get('files', {})) != ALLOW
            or set(m.get('dependencies', {})) != DEPENDENCIES
            or not re.fullmatch('[a-f0-9]{40}', m.get('base_commit', ''))):
        raise RuntimeError('补丁清单不匹配')
    for name, entry in m['files'].items():
        if (entry['mode'] != 0o644 or not re.fullmatch('[a-f0-9]{64}', entry['after'])
                or (entry['before'] is not None and not re.fullmatch('[a-f0-9]{64}', entry['before']))):
            raise RuntimeError('补丁摘要或权限格式错误')
        data = safe_path(PACKAGE / 'files', name).read_bytes()
        if sha(data) != entry['after']:
            raise RuntimeError('补丁文件摘要校验失败')
        if name.endswith('.py'):
            ast.parse(data.decode())
    return m


def idle_locks(root, stack):
    """同院内批跑锁；不 kill，不更换运行中批次指纹。"""
    output = root / 'output'
    if output.is_symlink():
        raise RuntimeError('output 不能是软链接')
    output.mkdir(mode=0o700, exist_ok=True)
    for name in ('.settlement-install.lock', '.249-batch.lock', '.243-patient.lock'):
        path = output / name
        if path.is_symlink():
            raise RuntimeError('锁文件不能是软链接')
        handle = stack.enter_context(path.open('a'))
        os.chmod(path, 0o600)
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('已有安装/批跑/单患者任务持锁，未覆盖代码') from None
    proc = Path('/proc')
    if not proc.is_dir():
        raise RuntimeError('安装只能在院内 Linux 主机执行；非 Linux 只能 check/verify')
    active = {'run_243_lis_batch.py', 'run_243_patient.sh', 'run_243_settlement_500.sh',
              'lis_batch.sh', 'etl_from_data_hub.py', 'run-batch.sh', 'run-patient-with-labs.sh'}
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue  # 本实例使用应用所属账号；其它账号不在本机进程证明范围。
            if (entry / 'cwd').resolve() != root:
                continue
            args = (entry / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            if ('javert.cli' in args or any(Path(arg).name in active | {'javert', 'uvicorn', 'gunicorn'} for arg in args)):
                raise RuntimeError('应用目录仍有 Web/审计/取数进程，先按原运维流程停止；未覆盖代码')
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            raise RuntimeError('无法验证应用进程状态，请使用应用所属账号运行') from None


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'install', 'verify', 'rollback'))
    parser.add_argument('--root', type=Path, default=Path('/home/admin2/Javert'))
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--services-stopped', action='store_true', help='已停止本实例 Web 和审计/取数任务')
    args = parser.parse_args()
    if args.root.is_symlink() or not args.root.is_dir():
        raise RuntimeError('应用目录不存在或是软链接')
    root = args.root.resolve()
    if args.action in ('install', 'rollback') and root.stat().st_uid != os.getuid():
        raise RuntimeError('请使用应用所属账号运行，不要 sudo 切换文件所有权')
    m = manifest()
    if args.action in ('check', 'verify'):
        state = check_versions(root, m)
        if args.action == 'verify' and state != 'installed':
            raise RuntimeError('尚未安装完整性能补丁')
        print('PATCH_STATE=' + state.upper() + '\nLOADED_PROCESS_CODE=UNVERIFIED')
        return
    if not args.services_stopped:
        raise RuntimeError('先停止本实例 Web/审计/取数，再使用 --services-stopped；不会自动停止任何进程')
    with ExitStack() as stack:
        idle_locks(root, stack)
        if args.action == 'install':
            if check_versions(root, m) == 'installed':
                print('ALREADY_INSTALLED；未重复覆盖')
                return
            backup = Path(tempfile.mkdtemp(prefix='web-performance-backup.', dir=root / 'output'))
            install_files(root, backup, m)
            if check_versions(root, m) != 'installed':
                raise RuntimeError('安装后校验失败，请按备份回滚')
        else:
            if args.backup is None or args.backup.is_symlink():
                raise RuntimeError('回滚须指定本次备份目录')
            backup = args.backup.resolve()
            if backup.parent != root / 'output' or not backup.name.startswith('web-performance-backup.'):
                raise RuntimeError('备份不属于本应用实例')
            rollback_files(root, backup, m)
        print('请用既有保存配置启动 Web，再验收页面。配置、数据库和批次账本未修改。')


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit('STOP: ' + str(exc))
    except Exception as exc:
        raise SystemExit('STOP: ' + type(exc).__name__ + '；未输出配置或患者资料')
