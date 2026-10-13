#!/usr/bin/env python3
"""独立249化验持续监测；复用原ETL/CLI，不修改业务代码。"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import csv
from datetime import datetime
import fcntl
import hashlib
import hmac
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
from ledger import Store, read_stats

ROOT = Path('/home/admin2/Javert')
PACKAGE = Path(__file__).resolve().parent
STATE = ROOT / 'output/lis-watch-v1'
FILES = {'fees': 'shi_fee.csv', 'notes': 'case_notes.csv', 'zd': 'shi_zd.csv',
         'ss': 'shi_ss.csv', 'labs': 'lab_results.csv', 'exams': 'examinations.csv'}
ENV_FILES = {'NOTES': 'notes', 'FEES': 'fees', 'ZD': 'zd', 'SS': 'ss', 'LABS': 'labs', 'EXAMINATIONS': 'exams'}
ID = re.compile(r'[A-Za-z0-9._-]{1,64}\Z')


def atomic_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def valid_pid(pid):
    return isinstance(pid, str) and bool(ID.fullmatch(pid)) and pid.upper() not in {'0','NULL','NONE','NAN','-'}


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_error(exc):
    message = str(exc)
    return message if re.fullmatch(r'[A-Z][A-Z0-9_]{0,90}',message) else type(exc).__name__


def snapshot_signature(path, hospital, pid, salt, hs=None):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('SNAPSHOT_SYMLINK')
    pre = path / 'preflight.json'
    if pre.is_symlink():
        raise ValueError('SNAPSHOT_SYMLINK')
    pre_hash = digest_file(pre)
    report = json.loads(pre.read_text(encoding='utf-8'))
    if report.get('patient_scope') != {'hospital': hospital, 'syxh': pid} or report.get('mode') != 'shanghai':
        raise ValueError('SNAPSHOT_SCOPE_MISMATCH')
    fee = report['fee_source']; lab = report['lab_source']
    if (fee.get('table') != 'TB_HIS_ZY_FEE_DETAIL' or fee.get('time_field') != 'STFSJ'
            or fee.get('version') != 1 or fee.get('file') != FILES['fees']
            or lab.get('source') != 'medical-record-v1' or lab.get('version') != 1 or lab.get('file') != FILES['labs']):
        raise ValueError('SNAPSHOT_SOURCE_MISMATCH')
    if hs:
        cfg = SimpleNamespace(fees_path=path/FILES['fees'], labs_path=path/FILES['labs'], hub_hospital_code=hospital)
        hs.validate_settlement_snapshot(cfg)
        hs.validate_lis_snapshot(cfg, pid)
    tables, hashes, counts = {}, {}, {}
    for key, name in FILES.items():
        file = path/name
        if file.is_symlink():
            raise ValueError('SNAPSHOT_SYMLINK')
        before = digest_file(file)
        with file.open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.reader(stream)
            header = next(reader)
            rows = list(reader)
        if not header or len(set(header)) != len(header) or any(len(row) != len(header) for row in rows):
            raise ValueError('SNAPSHOT_CSV_INVALID')
        order = sorted(range(len(header)), key=header.__getitem__)
        tables[key] = {'columns': [header[i] for i in order],
                       'rows': sorted([row[i] for i in order] for row in rows)}
        hashes[name] = before
        counts[key] = len(rows)
        if digest_file(file) != before or report['rows'][key] != len(rows):
            raise ValueError('SNAPSHOT_CHANGED_OR_ROW_MISMATCH')
        if key == 'labs':
            if not {'zyh','rpt_itemname','result'} <= set(header) or any(row[header.index('zyh')] != pid for row in rows):
                raise ValueError('LAB_SCOPE_MISMATCH')
    if fee.get('sha256') != hashes[FILES['fees']] or lab.get('sha256') != hashes[FILES['labs']]:
        raise ValueError('SNAPSHOT_CHECKSUM_MISMATCH')
    if digest_file(pre) != pre_hash:
        raise ValueError('SNAPSHOT_CHANGED')
    hashes['preflight.json'] = pre_hash
    payload = {'version': 1, 'hospital': hospital, 'syxh': pid, 'tables': tables,
               'fee_source': 'DETAIL/STFSJ/v1', 'lab_source': 'medical-record-v1',
               'warnings': report.get('warnings', {}), 'lab_linkage': report.get('lab_linkage', {})}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return {'digest': hmac.new(salt, raw, hashlib.sha256).hexdigest(), 'files': hashes, 'rows': counts}


def verify_bytes(path, signature):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('SNAPSHOT_SYMLINK')
    for name, expected in signature['files'].items():
        if name not in set(FILES.values()) | {'preflight.json'}:
            raise ValueError('SNAPSHOT_FILE_INVALID')
        p = path/name
        if p.is_symlink() or digest_file(p) != expected:
            raise ValueError('SNAPSHOT_CHANGED')


@contextmanager
def audit_locks(root):
    streams = []
    try:
        for name in ('.249-batch.lock', '.243-patient.lock'):
            p = root/'output'/name
            if p.is_symlink():
                raise RuntimeError('LOCK_SYMLINK')
            f = p.open('a'); streams.append(f)
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield tuple(f.fileno() for f in streams)
    finally:
        for f in reversed(streams):
            f.close()


class Runtime:
    def __init__(self, root=ROOT):
        self.root = root
        self.py = root/'.venv/bin/python'
        self.state = root/'output/lis-watch-v1'
        self.snapshot_root = self.state/'snapshots'
        sys.path[:0] = [str(root/'scripts'), str(root/'src')]
        logging.disable(logging.CRITICAL)
        spec = importlib.util.spec_from_file_location('watch_release_check', PACKAGE/'check_249_hotfix.py')
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        self.compare = checker.compare
        self.release = json.loads((PACKAGE/'known-release.json').read_text())
        self.verify_code()
        import run_243_lis_batch as batch
        self.batch = batch
        self.helper, self.cfg, self.hs = batch.runtime_profile()
        self.profile_path = self.helper.with_name('effective-config.json')
        self.profile_bytes = self.profile_path.read_bytes()  # 仅内存，不输出/落库。
        fields = ('hub_hospital_code','hub_linkage_mode','hub_database','sql_host','sql_port','sql_database',
                  'llm_model','llm_endpoint','llm_temperature','llm_max_tokens','llm_enable_thinking',
                  'max_tool_calls','precheck','verdict_gate','drift_guard')
        value = {'runtime': self.release['files'], 'settings': {k:getattr(self.cfg,k,None) for k in fields}}
        value['watcher'] = {n:digest_file(PACKAGE/n) for n in ('watcher.py','ledger.py','check_249_hotfix.py','known-release.json')}
        self.context = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
        self.tool_hashes = value['watcher']

    def verify_code(self):
        if not self.compare(self.root, self.release)['disk_match']:
            raise RuntimeError('RUNTIME_CODE_CHANGED')

    def guard(self):
        self.verify_code()
        if self.profile_path.read_bytes() != self.profile_bytes:
            raise RuntimeError('SAVED_PROFILE_CHANGED')
        if any(digest_file(PACKAGE/n) != h for n,h in self.tool_hashes.items()):
            raise RuntimeError('WATCHER_CODE_CHANGED')

    def candidates(self):
        with closing(self.hs.connect(self.cfg, timeout=15)) as cn:
            cn.timeout = 120
            frame = self.hs.list_lis_homes(cn, self.cfg.hub_hospital_code)
        return [str(v) for v in frame['SYXH'] if valid_pid(str(v))]

    def capture(self, pid):
        self.guard()
        if shutil.disk_usage(self.state).free < 1024**3:
            raise RuntimeError('DISK_SPACE_LOW')
        self.snapshot_root.mkdir(mode=0o700, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='snapshot.', dir=self.snapshot_root))
        try:
            with (stage/'etl.log').open('w') as log:
                r = subprocess.run([str(self.py), str(self.root/'scripts/etl_from_data_hub.py'), '--patients', pid,
                                    '--output', str(stage/'data')],cwd=self.root,stdout=log,stderr=subprocess.STDOUT)
            if r.returncode:
                text = (stage/'etl.log').read_text(errors='replace')
                codes = re.findall(r'预检未通过[：:]\s*([A-Z][A-Z0-9_]+)',text)
                reason = codes[-1] if codes else 'SOURCE_QUERY_FAILED'
                self.remove_snapshot(str(stage/'data'))
                return None, reason
            return stage/'data', ''
        except BaseException:
            self.remove_snapshot(str(stage/'data'))
            raise

    def remove_snapshot(self, value):
        data = Path(value)
        parent = data.parent
        if (data.name != 'data' or not parent.name.startswith('snapshot.')
                or parent.parent.resolve() != self.snapshot_root.resolve() or parent.is_symlink() or data.is_symlink()):
            raise RuntimeError('SNAPSHOT_DELETE_SCOPE_INVALID')
        if parent.exists():
            shutil.rmtree(parent)

    def signature(self, path, pid, salt):
        return snapshot_signature(path,self.cfg.hub_hospital_code,pid,salt,self.hs)

    def healthy(self, lock_fds):
        self.guard()
        with (self.state/'health.log').open('w') as log:
            r = subprocess.run([str(self.py),str(self.root/'scripts/check_243_runtime.py')],cwd=self.root,
                               stdout=log,stderr=subprocess.STDOUT,pass_fds=lock_fds)
        return r.returncode == 0

    def execute(self, job, folder, tag, lock_fds):
        self.guard()
        snapshot = Path(job['snapshot'])
        verify_bytes(snapshot, job['signature'])
        self.signature(snapshot,job['pid'],b'pre-audit-validation-only')
        env = dict(os.environ, JAVERT_DATA_DIR=str(snapshot), JAVERT_BATCH_TAG=tag,
                   JAVERT_PATIENT_RECEIPT=str(folder/'patient-0001.audit.json'), PYTHONDONTWRITEBYTECODE='1')
        for env_name,key in ENV_FILES.items():
            env['JAVERT_'+env_name+'_FILE'] = FILES[key]
        with (folder/'patient-0001.log').open('w') as log:
            r = subprocess.run([str(self.py),'-m','javert.cli','audit-patient',job['pid'],
                                '--priority','all','--use-router','--concurrency','1'],
                               cwd=self.root,env=env,stdout=log,stderr=subprocess.STDOUT,pass_fds=lock_fds)
        atomic_json(folder/'patient-0001.exit.json', {'returncode':r.returncode})
        verify_bytes(snapshot, job['signature'])
        return r.returncode

    def receipt_status(self, job):
        folder = Path(job['folder'])
        if folder.is_symlink() or folder.resolve().parent != (self.root/'output').resolve() or not folder.name.startswith('lis-batch.'):
            raise ValueError('RECEIPT_FOLDER_INVALID')
        verify_bytes(job['snapshot'],job['signature'])
        return self.batch.receipt_result(folder,{'index':1,'patient_id':job['pid']},job['tag'])


def job_summary(runtime, job, status, reason='', finished=False):
    folder = Path(job['folder'])
    if folder.is_symlink() or folder.resolve().parent != (runtime.root/'output').resolve() or not folder.name.startswith('lis-batch.'):
        raise ValueError('SUMMARY_FOLDER_INVALID')
    row = {'index':1,'patient_id':job['pid'],'status':status,'reason':reason,
           'started_at':datetime.fromtimestamp(job.get('started') or time.time()).isoformat(timespec='seconds'),
           'snapshot_directory':str(Path(job['snapshot']).parent),'lab_rows':job['signature']['rows']['labs']}
    if finished:
        row['finished_at'] = datetime.now().isoformat(timespec='seconds')
    phase = 'running' if not finished else 'completed' if status in {'PASSED','ROUTER_SKIPPED'} else 'halted'
    value = {'version':1,'tag':job['tag'],'created_at':datetime.fromtimestamp(job['created']).isoformat(timespec='seconds'),
             'phase':phase,'finished':finished,'lis_watch_v1':True,'patients':[row]}
    atomic_json(folder/'summary.json', value)


def remove_discarded(runtime, paths):
    for path in set(paths):
        runtime.remove_snapshot(path)


def import_history(runtime, store):
    """只在持有两把审计锁时读取旧批次，防止与手动审计交错判定。"""
    scanned = 0
    for folder in (runtime.root/'output').glob('lis-batch.*'):
        if folder.is_symlink() or folder.name.startswith('lis-batch.watch-'):
            continue
        p = folder/'summary.json'
        if not p.is_file() or p.is_symlink():
            continue
        try:
            token = digest_file(p)
        except OSError:
            continue
        key = 'history:' + folder.name
        if store.get_meta(key) == token:
            continue
        try:
            data = json.loads(p.read_text())
            if not isinstance(data,dict) or not isinstance(data.get('patients'),list):
                raise ValueError('HISTORY_STRUCTURE')
        except (OSError,ValueError):
            store.set_meta(key,token)
            store.set_meta('history_unverified',int(store.get_meta('history_unverified') or 0)+1)
            continue
        if data.get('lis_watch_v1'):
            continue
        if (data.get('code_fingerprint') != runtime.batch.fingerprint()
                or data.get('profile_signature') != runtime.batch.profile_signature(runtime.cfg)):
            store.set_meta('history_unverified',int(store.get_meta('history_unverified') or 0)+1)
            store.set_meta(key,token)
            continue
        for row in data.get('patients',[]):
            if not isinstance(row,dict):
                continue
            pid = row.get('patient_id'); status = row.get('status')
            if not valid_pid(pid):
                continue
            if status in {'STARTED','FAILED_AUDIT'}:
                try:
                    completed = runtime.batch.receipt_result(folder,row,data['tag'])
                except Exception:
                    completed = None
                if completed not in {'PASSED','ROUTER_SKIPPED'}:
                    store.block_patient(pid,'HISTORY_AUDIT_UNCERTAIN')
                    continue
            elif status not in {'PASSED','ROUTER_SKIPPED'}:
                continue
            try:
                if runtime.batch.receipt_result(folder,row,data['tag']) not in {'PASSED','ROUTER_SKIPPED'}:
                    raise ValueError('HISTORY_RECEIPT_INVALID')
                base = Path(row['snapshot_directory'])
                if base.is_symlink() or base.resolve().parent != (runtime.root/'output').resolve() or not base.name.startswith('patient.'):
                    raise ValueError('HISTORY_SNAPSHOT_INVALID')
                sig = runtime.signature(base/'data',pid,store.salt)
                stamp = datetime.fromisoformat(row['finished_at']).timestamp()
                remove_discarded(runtime,store.import_success(pid,sig['digest'],stamp) or [])
                scanned += 1
            except Exception:
                # 有成功记录却缺完整证据时不冒充同一输入完成；首次新快照建立监测基线。
                store.set_meta('history_unverified',int(store.get_meta('history_unverified') or 0)+1)
        store.set_meta(key,token)
    if scanned:
        store.set_meta('history_verified_imports',int(store.get_meta('history_verified_imports') or 0)+scanned)


class Watcher:
    def __init__(self, runtime, store, interval=300):
        self.runtime, self.store, self.interval = runtime, store, interval
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.fatal = None

    def stopping(self):
        if (self.runtime.state/'STOP').exists():
            self.stop.set()
        return self.stop.is_set()

    def check_patient(self, pid):
        r, s = self.runtime, self.store
        first, reason = r.capture(pid)
        if first is None:
            remove_discarded(r,s.mark_ineligible(pid,reason))
            return
        try:
            sig = r.signature(first,pid,s.salt)
            if self.stopping():
                return
            if sig['rows']['labs'] < 1:
                remove_discarded(r,s.mark_ineligible(pid,'NO_ASSIGNED_LABS'))
                return
            # 先按持久状态判断是否需二次取数；稳定未变化者只取一次。
            # 只读比较与“仍相同则撤队/跳过”共用账本锁；worker不能在其间claim另一版。
            # 真正需要新增排队的一律走下面的二次采样。
            unchanged_discard = None
            with s.lock:
                if s.matches_effective(pid,sig['digest']):
                    queued, unchanged_discard = s.observe(pid,sig,str(first))
                    if queued:
                        raise RuntimeError('LEDGER_ATOMIC_COMPARE_FAILED')
            if unchanged_discard is not None:
                remove_discarded(r,unchanged_discard)
                return
            second, reason = r.capture(pid)
            if second is None:
                remove_discarded(r,s.mark_ineligible(pid,reason))
                return
            try:
                newer = r.signature(second,pid,s.salt)
                if newer['digest'] != sig['digest']:
                    remove_discarded(r,s.mark_ineligible(pid,'SOURCE_UNSTABLE'))
                    return
                queued, discarded = s.observe(pid,newer,str(second))
                if queued:
                    self.wake.set()
                    second = None
                remove_discarded(r,discarded)
            finally:
                if second is not None and second.parent.exists():
                    r.remove_snapshot(str(second))
        finally:
            if first.parent.exists():
                r.remove_snapshot(str(first))

    def scan_once(self):
        r,s = self.runtime,self.store
        r.guard()
        current = r.candidates()
        names = {p.upper():p for p in s.known_pids()}
        names.update({p.upper():p for p in current})
        s.set_meta('scan_state','SCANNING')
        s.set_meta('scan_total',len(names))
        for number,pid in enumerate(names.values(),1):
            if self.stopping():
                break
            try:
                self.check_patient(pid)
            except RuntimeError:
                raise
            except Exception:
                remove_discarded(r,s.mark_ineligible(pid,'SOURCE_OR_SNAPSHOT_FAILED'))
            s.set_meta('scan_checked',number)
        s.set_meta('last_scan_finished',datetime.now().isoformat(timespec='seconds'))
        s.set_meta('scan_state','WAITING_NEXT_SCAN')
        print(json.dumps(s.stats(),ensure_ascii=False),flush=True)

    def finish_job(self, job, status, reason=''):
        self.store.finish(job['id'],status,reason)
        job_summary(self.runtime,job,status,reason,True)
        self.store.set_meta('last_audit_tag',job['tag'])
        self.store.set_meta('last_audit_status',status)
        print('审计任务结束：TAG='+job['tag']+'；状态='+status,flush=True)

    def recover(self):
        for job in self.store.running_jobs():
            try:
                status = self.runtime.receipt_status(job)
                if status in {'PASSED','ROUTER_SKIPPED'}:
                    self.finish_job(job,status)
                else:
                    folder = Path(job['folder'])
                    exit_file = folder/'patient-0001.exit.json'
                    code = json.loads(exit_file.read_text()).get('returncode') if exit_file.is_file() else None
                    if (type(code) is int and code != 0 and not (folder/'patient-0001.audit.started.json').exists()
                            and not (folder/'patient-0001.audit.json').exists()):
                        remove_discarded(self.runtime,self.store.retry_before_start(job['id'],'RECOVERED_PRE_AUDIT_FAILURE'))
                        job_summary(self.runtime,job,'FAILED_SOURCE','RECOVERED_PRE_AUDIT_FAILURE',True)
                    else:
                        self.finish_job(job,'NEEDS_REVIEW','AUDIT_COMPLETION_UNCERTAIN')
            except Exception:
                self.finish_job(job,'NEEDS_REVIEW','SNAPSHOT_OR_RECEIPT_UNCERTAIN')

    def work_one(self):
        r,s = self.runtime,self.store
        with audit_locks(r.root) as fds:
            self.recover()
            import_history(r,s)
            job = s.next_job()
            if not job or self.stopping():
                return False
            if not r.healthy(fds):
                s.set_meta('audit_state','WAITING_RUNTIME_HEALTH')
                return False
            if self.stopping():
                return False
            folder = Path(tempfile.mkdtemp(prefix='lis-batch.watch-',dir=r.root/'output'))
            tag = 'st-lis-'+datetime.now().strftime('%y%m%d')+'-'+secrets.token_hex(3)
            job = s.claim(job['id'],str(folder),tag)
            if job is None:
                folder.rmdir()
                return False
            atomic_json(folder/'audit-input.json',{'version':1,'signature':job['signature'],'snapshot':job['snapshot'],
                                                'patient_id':job['pid'],'tag':tag,'context':s.context})
            job_summary(r,job,'STARTED')
            s.set_meta('audit_state','RUNNING')
            s.set_meta('current_tag',tag)
            try:
                code = r.execute(job,folder,tag,fds)
                status = r.receipt_status(job)
                if code == 0 and status in {'PASSED','ROUTER_SKIPPED'}:
                    self.finish_job(job,status)
                elif not (folder/'patient-0001.audit.started.json').exists() and not (folder/'patient-0001.audit.json').exists():
                    discarded = s.retry_before_start(job['id'],'PRE_AUDIT_FAILED',delay=300)
                    remove_discarded(r,discarded)
                    job_summary(r,job,'FAILED_SOURCE','PRE_AUDIT_FAILED',True)
                else:
                    self.finish_job(job,'NEEDS_REVIEW','AUDIT_OR_SYNC_PARTIAL')
            except Exception:
                self.finish_job(job,'NEEDS_REVIEW','AUDIT_OR_SNAPSHOT_UNCERTAIN')
                raise
            finally:
                s.set_meta('current_tag','')
            s.set_meta('audit_state','IDLE')
            return True

    def worker(self):
        while not self.stopping():
            try:
                self.runtime.guard()
                if self.work_one():
                    continue
            except BlockingIOError:
                self.store.set_meta('audit_state','WAITING_EXISTING_TASK')
            except RuntimeError as exc:
                self.fatal = safe_error(exc)
                self.store.set_meta('audit_state','STOPPED_FOR_CODE_OR_CONFIG_CHECK')
                print('监测停止：'+self.fatal,flush=True)
                self.stop.set()
                break
            except Exception:
                self.store.set_meta('audit_state','WAITING_AFTER_ERROR')
            self.wake.wait(10)
            self.wake.clear()

    def run(self):
        self.store.set_meta('service_state','RUNNING')
        thread = threading.Thread(target=self.worker,name='serial-audit')
        thread.start()
        try:
            while not self.stopping():
                started = time.monotonic()
                try:
                    self.scan_once()
                except RuntimeError as exc:
                    self.fatal = safe_error(exc)
                    self.store.set_meta('scan_state','STOPPED_FOR_RUNTIME_CHECK')
                    print('监测停止：'+self.fatal,flush=True)
                    break
                except Exception:
                    self.store.set_meta('scan_state','SOURCE_UNAVAILABLE_RETRYING')
                    print('源暂不可用，稍后重新扫描；没有启动重复审计。',flush=True)
                remaining = max(5,self.interval-(time.monotonic()-started))
                end = time.monotonic()+remaining
                while time.monotonic()<end and not self.stopping():
                    self.stop.wait(min(2,end-time.monotonic()))
        finally:
            self.stop.set();self.wake.set()
            thread.join()  # 优雅停止：不杀正在写结果的原审计进程。
            self.store.set_meta('service_state','STOPPED_NEEDS_CHECK' if self.fatal else 'STOPPED')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['run','status','stop','check'])
    parser.add_argument('--interval',type=int,default=300)
    args = parser.parse_args()
    if args.interval < 60:
        raise ValueError('INTERVAL_MIN_60')
    if args.mode == 'status':
        stats = read_stats(STATE/'ledger.sqlite')
        stats['service_lock_held'] = False
        if (STATE/'service.lock').is_file() and not (STATE/'service.lock').is_symlink():
            with (STATE/'service.lock').open('r') as f:
                try:
                    fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError:
                    stats['service_lock_held'] = True
        print(json.dumps(stats,ensure_ascii=False,indent=2))
        return
    if args.mode == 'stop':
        if not STATE.exists():
            print('监测尚未启动。');return
        if STATE.is_symlink() or (STATE/'STOP').is_symlink():
            raise RuntimeError('STOP_PATH_SYMLINK')
        (STATE/'STOP').write_text('stop\n')
        print('已请求停止；当前审计完成后退出，状态保留。');return
    runtime = Runtime()
    if args.mode == 'check':
        runtime.guard()
        print('WATCH_PREFLIGHT=PASS；代码/保存配置通过，候选='+str(len(runtime.candidates()))+'；未审计。')
        return
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    if STATE.is_symlink():
        raise RuntimeError('STATE_SYMLINK')
    if (STATE/'service.lock').is_symlink():
        raise RuntimeError('SERVICE_LOCK_SYMLINK')
    with (STATE/'service.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (STATE/'instance.json').exists() and not (STATE/'ledger.sqlite').exists():
            raise RuntimeError('STATE_DATABASE_MISSING')
        store = Store(STATE/'ledger.sqlite',runtime.context)
        atomic_json(STATE/'instance.json',{'version':1})
        (STATE/'STOP').unlink(missing_ok=True)
        watcher = Watcher(runtime,store,args.interval)
        for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
            signal.signal(sig,lambda *_: watcher.stop.set())
        try:
            watcher.run()
        finally:
            store.close()


if __name__ == '__main__':
    try:
        main()
    except BlockingIOError:
        raise SystemExit('WATCH_ALREADY_RUNNING；未重复启动。') from None
    except Exception as exc:
        # 禁止原始异常携带SQL、病历、患者号或保存配置。
        raise SystemExit('WATCH_STOPPED='+safe_error(exc)+'；查看status，未修改业务代码。') from None
