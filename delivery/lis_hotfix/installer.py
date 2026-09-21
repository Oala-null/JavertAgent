#!/usr/bin/env python3
"""249 LIS医疗记录路径增量补丁。数据库只读；安装/回滚必须无批跑，仅重启Web。"""
import ast
from contextlib import ExitStack, closing
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path('/home/admin2/Javert')
PACKAGE = Path(__file__).resolve().parent
ALLOW = {
    'src/javert/data/hub_source.py', 'src/javert/commands/audit_patient.py',
    'scripts/etl_from_data_hub.py', 'scripts/run_243_patient.sh',
    'src/javert/web/hub_raw_source.py', 'src/javert/web/api/routes_workbench.py',
    'src/javert/web/static/app.js', 'scripts/run_243_lis_batch.py',
    'scripts/lis_batch.sh', 'scripts/check_243_lis.py',
}
DEPENDENCIES = {'src/javert/web/templates/_sidebar.html', 'scripts/check_243_runtime.py',
                'scripts/find_243_patient.py', 'scripts/run_243_settlement_500.sh'}
ACTIVE_SCRIPTS = {'run_243_patient.sh','etl_from_data_hub.py','find_243_patient.py',
                  'run_243_settlement_500.sh','run_243_lis_batch.py',
                  'run-batch.sh','run-patient-with-labs.sh'}



def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_path(root, relative):
    if relative not in ALLOW | DEPENDENCIES:
        raise RuntimeError('文件不在本补丁白名单')
    p = root
    for part in Path(relative).parts:
        p = p / part
        if p.is_symlink():
            raise RuntimeError('受控文件路径存在软链接')
    return p


def manifest():
    m = json.loads((PACKAGE / 'manifest.json').read_text())
    if (m['patch_id'] != '249-lis-medical-v1' or set(m['files']) != ALLOW
            or set(m.get('dependencies',{})) != DEPENDENCIES):
        raise RuntimeError('补丁清单不匹配')
    for name, entry in m['files'].items():
        p = safe_path(PACKAGE / 'files', name)
        data = p.read_bytes()
        if sha(data) != entry['after']:
            raise RuntimeError('补丁文件摘要校验失败')
        if p.suffix == '.py':
            ast.parse(data.decode())
        elif p.suffix == '.sh':
            subprocess.run(['/bin/bash', '-n', str(p)], check=True, capture_output=True)
    return m


def check_versions(root, m, rollback=False):
    if (root / 'DEPLOY_COMMIT').read_text().strip() != m['base_commit']:
        raise RuntimeError('当前DEPLOY_COMMIT不是本补丁基线')
    for name,digest in m['dependencies'].items():
        if sha(safe_path(root,name).read_bytes()) != digest:
            raise RuntimeError('依赖的结算/侧栏文件版本不同，停止覆盖')
    states = {n: sha(safe_path(root, n).read_bytes()) if safe_path(root, n).exists() else None for n in m['files']}
    if not rollback and all(states[n] == e['after'] for n, e in m['files'].items()):
        return 'installed'
    key = 'after' if rollback else 'before'
    if any(states[n] != e[key] for n, e in m['files'].items()):
        raise RuntimeError('受控文件有其他修改或混合版本，未覆盖任何文件')
    return 'ready'


def runtime_config():
    helpers = [p for p in Path('/home/admin2/releases').glob('243-install-8645a2d.*/run_saved_config.py')
               if p.with_name('install-receipt.json').is_file()]
    if len(helpers) != 1:
        raise RuntimeError('有效安装配置不唯一')
    saved = json.loads(helpers[0].with_name('effective-config.json').read_text())
    for key, value in saved.items():
        name = 'JAVERT_' + key.upper()
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = json.dumps(value) if isinstance(value, (bool, list, dict)) else str(value)
    os.environ.update(PYTHONDONTWRITEBYTECODE='1', JAVERT_HUB_LINKAGE_MODE='shanghai',
                      JAVERT_HUB_HOSPITAL_CODE='AYY8BNRF')
    sys.path.insert(0, str(ROOT / 'src'))
    logging.disable(logging.CRITICAL)
    from javert.config import get_config
    return helpers[0], get_config()


def probe_source(cfg, m):
    from javert.data.hub_source import connect
    if (cfg.sql_host not in {'127.0.0.1','localhost'} or cfg.sql_port != 1533
            or cfg.hub_database != 'sh_yb_platform' or cfg.sql_database != 'sh_yb_platform'):
        raise RuntimeError('不是已确认的249数据源配置，停止；未修改配置')
    tables=m['required_tables']
    if set(tables) != {'TB_BA_SYJBK','TB_YL_ZY_MEDICAL_RECORD','TB_LIS_REPORT','TB_LIS_INDICATORS'}:
        raise RuntimeError('LIS字段清单异常')
    with closing(connect(cfg, cfg.hub_database, timeout=10)) as cn:
        cn.timeout=30
        cur=cn.cursor()
        for table,columns in tables.items():
            if any(not re.fullmatch(r'[A-Z0-9_]+', name) for name in columns):
                raise RuntimeError('字段清单格式异常')
            found={r[0] for r in cur.execute('SELECT name FROM sys.columns WHERE object_id=OBJECT_ID(?)',('dbo.'+table,)).fetchall()}
            missing=sorted(set(columns)-found)
            if missing:
                raise RuntimeError(table+'缺失字段：'+','.join(missing))
            cur.execute('SELECT TOP (0) '+','.join(columns)+' FROM dbo.'+table)
    print('SOURCE_CHECK=PASS；四表字段可读，未修改数据库；不代表所有报告已能归属本次住院。',flush=True)


def idle_locks(root, stack):
    for name in ['.249-batch.lock', '.243-patient.lock']:
        handle = stack.enter_context((root / 'output' / name).open('a'))
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('还有批跑或单患者任务持锁，先等待结束或按原流程暂停；未停止任务')
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if Path(os.readlink(proc / 'cwd')) != root:
                continue
            args = (proc / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            if 'audit-patient' in args or any(Path(arg).name in ACTIVE_SCRIPTS for arg in args):
                raise RuntimeError('仍有审计/取数进程；先等待结束或按原流程暂停，未修改代码')
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue


def stop_web(root, cfg):
    r = subprocess.run(['ss', '-lntp', 'sport = :8090'], capture_output=True, text=True, check=True)
    pids = set(re.findall(r'pid=(\d+)', r.stdout))
    if not pids and 'LISTEN' not in r.stdout:
        return
    if len(pids) != 1:
        raise RuntimeError('不能唯一识别8090监听进程，未停止服务')
    pid = int(pids.pop())
    p = Path('/proc') / str(pid)
    birth = (p / 'stat').read_text().rsplit(')', 1)[1].split()[19]
    args = (p / 'cmdline').read_bytes().decode().split('\0')
    if Path(os.readlink(p / 'cwd')) != root or 'audit-patient' in args or not ('javert.cli' in args and 'web' in args):
        raise RuntimeError('8090进程不是已确认的Javert Web')
    env = dict(v.split('=', 1) for v in (p / 'environ').read_bytes().decode().split('\0') if '=' in v)
    for key in ['sql_host', 'sql_port', 'sql_user', 'sql_password', 'sql_database', 'hub_database', 'session_secret']:
        if env.get('JAVERT_' + key.upper()) != str(getattr(cfg, key)):
            raise RuntimeError('Web实际环境与保存配置不一致，未停止服务；不输出秘密值')
    if (p / 'stat').read_text().rsplit(')', 1)[1].split()[19] != birth:
        raise RuntimeError('Web PID已变化')
    os.kill(pid, signal.SIGTERM)
    for step in range(45):
        try:
            if (p / 'stat').read_text().rsplit(')', 1)[1].split()[19] != birth:
                return
        except FileNotFoundError:
            return
        if step in (9,19,29,39):
            print('仍在等待旧Web正常退出；请关闭Workbench浏览器页面。',flush=True)
        time.sleep(1)
    raise RuntimeError('Web尚未正常退出；未强杀、未安装补丁，稍后先查状态')


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
    receipt = json.loads((backup / 'receipt.json').read_text())
    if receipt['patch_id'] != m['patch_id'] or set(receipt['files']) != ALLOW:
        raise RuntimeError('回滚备份不属于此补丁')
    if receipt.get('base_commit') != m['base_commit'] or (root / 'DEPLOY_COMMIT').read_text().strip() != m['base_commit']:
        raise RuntimeError('回滚基线已变化，停止')
    for name,digest in m['dependencies'].items():
        if sha(safe_path(root,name).read_bytes()) != digest:
            raise RuntimeError('依赖的结算/侧栏文件已有后续改动，停止回滚')
    for name, e in receipt['files'].items():
        if e['before'] != m['files'][name]['before'] or e['after'] != m['files'][name]['after']:
            raise RuntimeError('回滚清单摘要不一致')
        p = safe_path(root, name)
        actual = sha(p.read_bytes()) if p.exists() else None
        if actual not in (e['before'], e['after']):
            raise RuntimeError('目标有后续修改，不自动覆盖')
        if e['before'] is not None and sha((backup / 'before' / name).read_bytes()) != e['before']:
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
    print('ROLLBACK=PASS；仅恢复本次LIS改动，既有结算/侧栏补丁、历史数据及配置保留。', flush=True)


def start_web(root, helper):
    folder = Path(tempfile.mkdtemp(prefix='web-bg.', dir=root / 'output'))
    launcher = folder / 'start.sh'
    launcher.write_text('#!/usr/bin/env bash\nset -euo pipefail\numask 077\nexec >"$2" 2>&1\nexec python3 "$1" web\n')
    subprocess.run(['tmux', 'new-session', '-d', '-s', 'javert-lis-web-' + folder.name.split('.')[-1],
                    '-c', str(root), '/bin/bash', str(launcher), str(helper), str(folder / 'web.log')], check=True)
    import http.client
    for _ in range(20):
        try:
            with closing(http.client.HTTPConnection('127.0.0.1', 8090, timeout=2)) as cn:
                cn.request('GET', '/login')
                if cn.getresponse().status == 200:
                    print('WEB_HTTP_200=PASS；日志：' + str(folder / 'web.log'), flush=True)
                    return
        except OSError:
            pass
        time.sleep(1)
    raise RuntimeError('Web未通过健康检查；在院内查看 ' + str(folder / 'web.log') + '，并按说明回滚')


def main():
    os.umask(0o077)
    if len(sys.argv) < 2 or sys.argv[1] not in ('check', 'install', 'rollback'):
        raise RuntimeError('用法：check | install | rollback <本次备份目录>')
    mode = sys.argv[1]
    m = manifest()
    if mode != 'rollback':
        state = check_versions(ROOT, m)
        if state == 'installed' and mode == 'install':
            print('ALREADY_INSTALLED；未重复覆盖或重启。')
            return
    helper, cfg = runtime_config()
    if mode != 'rollback':
        probe_source(cfg, m)
    if mode == 'check':
        print('PREFLIGHT=PASS；尚未安装或停止服务。')
        return
    if not shutil.which('tmux'):
        raise RuntimeError('缺少tmux，未停止服务')
    (ROOT / 'output').mkdir(exist_ok=True)
    with ExitStack() as stack:
        lock = stack.enter_context((ROOT / 'output/.settlement-install.lock').open('a'))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        idle_locks(ROOT, stack)
        if mode == 'rollback':
            if len(sys.argv) != 3:
                raise RuntimeError('回滚必须指定本次备份目录')
            backup = Path(sys.argv[2]).resolve()
            if backup.parent != ROOT / 'output' or not backup.name.startswith('lis-backup.'):
                raise RuntimeError('备份路径不在本实例范围')
            # 所有回滚文件先校验；真正恢复在Web退出后执行。
            validate_rollback(ROOT, backup, m)
            stop_web(ROOT, cfg)
            rollback_files(ROOT, backup, m)
        else:
            stop_web(ROOT, cfg)
            backup = Path(tempfile.mkdtemp(prefix='lis-backup.', dir=ROOT / 'output'))
            try:
                install_files(ROOT, backup, m)
            except BaseException:
                start_web(ROOT, helper)
                raise
        start_web(ROOT, helper)
        print('仅代码和Web切换完成；请按教程检查LIS名单、单例快照和工作台，再恢复同批全量处理。', flush=True)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        raise SystemExit('STOP: ' + str(exc))
    except Exception as exc:
        raise SystemExit('STOP: ' + type(exc).__name__ + '；未输出凭据、连接串或患者数据。')
