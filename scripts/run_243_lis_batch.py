#!/usr/bin/env python3
"""249全量LIS候选：冻结名单、逐人审计和可核验恢复。日志/名单仅留院内。"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
BASE = '8645a2deb1c4c99921a65a3ab01754275ec2ac4e'
TERMINAL = {'PASSED','ROUTER_SKIPPED','SKIPPED_LINKAGE','SKIPPED_NO_LABS','INVALID_ID','FAILED_AUDIT','FAILED_SOURCE'}
CODE_FILES = ('src/javert/data/hub_source.py','src/javert/commands/audit_patient.py',
              'scripts/etl_from_data_hub.py','scripts/run_243_patient.sh','scripts/run_243_lis_batch.py')


def atomic_json(path, value):
    temporary = path.with_name(path.name+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        os.chmod(temporary,0o600)
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary,path)


def runtime_profile():
    if (ROOT/'DEPLOY_COMMIT').read_text().strip()!=BASE:
        raise RuntimeError('BASE_VERSION_CHANGED')
    helpers=[p for p in Path('/home/admin2/releases').glob('243-install-8645a2d.*/run_saved_config.py')
             if p.with_name('install-receipt.json').is_file()]
    if len(helpers)!=1:
        raise RuntimeError('SAVED_CONFIG_NOT_UNIQUE')
    saved=json.loads(helpers[0].with_name('effective-config.json').read_text())
    for name in list(os.environ):
        if name.startswith('JAVERT_'):
            del os.environ[name]
    for name,value in saved.items():
        if value is not None:
            os.environ['JAVERT_'+name.upper()]=json.dumps(value) if isinstance(value,(bool,list,dict)) else str(value)
    os.environ.update(JAVERT_HUB_LINKAGE_MODE='shanghai',JAVERT_HUB_HOSPITAL_CODE='AYY8BNRF',
                      PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(ROOT/'src'))
    logging.disable(logging.CRITICAL)
    from javert.config import get_config
    from javert.data import hub_source as hs
    cfg=get_config()
    if (cfg.sql_host not in {'127.0.0.1','localhost'} or cfg.sql_port!=1533
            or cfg.sql_database!='sh_yb_platform' or cfg.hub_database!='sh_yb_platform' or not cfg.sql_enabled):
        raise RuntimeError('CONFIG_NOT_CONFIRMED_249_TARGET')
    if hs.LIS_SOURCE_VERSION!='medical-record-v1' or hs.SETTLEMENT_FEE_TABLE!='TB_HIS_ZY_FEE_DETAIL':
        raise RuntimeError('PATCH_NOT_INSTALLED')
    return helpers[0],cfg,hs


def fingerprint():
    digest=hashlib.sha256()
    paths=[ROOT/n for n in CODE_FILES]
    paths+=sorted((ROOT/'configs/rules').glob('*.yaml'))
    paths+=sorted((ROOT/'configs/templates').glob('*.yaml'))
    paths+=sorted((ROOT/'data/router').glob('*.json'))
    for path in paths:
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def profile_signature(cfg):
    fields=('sql_host','sql_port','sql_database','hub_database','hub_hospital_code',
            'hub_linkage_mode','llm_endpoint','llm_model','temperature','audit_db_path')
    # 仅存摘要；不写密码、session secret或完整配置。
    data={name:str(getattr(cfg,name,'')) for name in fields}
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()


def safe_folder(value):
    path=Path(value)
    if path.is_symlink() or path.resolve().parent!=(ROOT/'output').resolve() or not path.name.startswith('lis-batch.'):
        raise RuntimeError('BATCH_PATH_INVALID')
    return path.resolve()


def resume_folder(value):
    if value!='latest':
        return safe_folder(value)
    pending=[]
    for path in (ROOT/'output').glob('lis-batch.*'):
        summary_path=path/'summary.json'
        if summary_path.is_file() and not json.loads(summary_path.read_text()).get('finished',False):
            pending.append(path)
    if len(pending)!=1:
        raise RuntimeError('RESUME_BATCH_NOT_UNIQUE：请先status，并用明确批次目录恢复。')
    return safe_folder(pending[0])


def summary(folder,state):
    counts=Counter(r['status'] for r in state['patients'])
    print(f"批次={state['tag']}；候选={len(state['patients'])}；阶段={state['phase']}；状态={dict(counts)}",flush=True)
    print('进度目录：'+str(folder),flush=True)
    reasons=Counter(r.get('reason') for r in state['patients'] if r.get('reason'))
    if reasons:
        print('原因计数：'+str(dict(reasons)),flush=True)
    for row in state['patients']:
        if row['status']=='STARTED':
            print(f"当前序号={row['index']}；开始时间={row.get('started_at','待核对')}",flush=True)


def freeze_roster(folder,rows):
    records=[]
    seen=set()
    for row in rows.to_dict('records'):
        pid=str(row['SYXH'])
        if pid.upper() in seen:
            continue
        seen.add(pid.upper())
        valid=bool(re.fullmatch(r'[A-Za-z0-9._-]{1,64}',pid)) and pid.upper() not in {'NULL','NONE','NAN','0'}
        records.append({'index':len(records)+1,'patient_id':pid,
                        'candidate_reports':int(row['report_count']),
                        'multiple_home_reports':int(row['multiple_home_reports']),
                        'status':'PENDING' if valid else 'INVALID_ID'})
    atomic_json(folder/'roster.json',records)
    return records


def receipt_result(folder,record,tag):
    stem=f"patient-{record['index']:04d}"
    path=folder/(stem+'.audit.json')
    if not path.is_file():
        return None
    data=json.loads(path.read_text())
    if data.get('version')!=1 or data.get('tag')!=tag or data.get('patient_id')!=record['patient_id']:
        raise RuntimeError('RECEIPT_SCOPE_MISMATCH')
    status=data.get('status')
    if status in {'PASSED','ROUTER_SKIPPED'} and (data.get('lab_source')!='medical-record-v1' or data.get('lab_rows',0)<1):
        raise RuntimeError('RECEIPT_LABS_INVALID')
    if status=='PASSED':
        n=data.get('completed',0)
        sync=data.get('sync',{})
        if not (n>0 and n==data.get('selected') and data.get('failed')==0
                and sync.get('synced')==n and sync.get('pending')==0 and sync.get('skipped')==0):
            raise RuntimeError('RECEIPT_COUNTS_INVALID')
    elif status=='ROUTER_SKIPPED':
        if data.get('completed')!=0 or data.get('selected')!=0:
            raise RuntimeError('RECEIPT_COUNTS_INVALID')
    elif status!='FAILED_AUDIT':
        raise RuntimeError('RECEIPT_STATUS_INVALID')
    return status


def classify_before_audit(folder,record,code):
    stem=f"patient-{record['index']:04d}"
    if (folder/(stem+'.audit.started.json')).exists():
        return 'FAILED_AUDIT','AUDIT_WITHOUT_COMPLETE_RECEIPT'
    text=(folder/(stem+'.log')).read_text(errors='replace')
    if code==4:
        return 'SKIPPED_NO_LABS','NO_ASSIGNED_LABS'
    found=re.findall(r'预检未通过[：:]\s*([A-Z][A-Z0-9_]+)',text)
    if code==2 and found:
        reason=found[-1]
        if reason.startswith('LINK_') or reason in {'SOURCE_FEES_EMPTY','SOURCE_NOTES_EMPTY'}:
            return 'SKIPPED_LINKAGE',reason
    return 'FAILED_SOURCE','SOURCE_OR_RUNTIME_FAILED'


def recover_started(folder,state):
    for record in state['patients']:
        if record['status'] in {'PASSED','ROUTER_SKIPPED'}:
            if receipt_result(folder,record,state['tag'])!=record['status']:
                raise RuntimeError('COMPLETED_RECEIPT_MISSING_OR_MISMATCH')
            continue
        if record['status']!='STARTED':
            continue
        status=receipt_result(folder,record,state['tag'])
        if status:
            record['status']=status
            continue
        exit_path=folder/f"patient-{record['index']:04d}.exit.json"
        if exit_path.is_file():
            record['status'],record['reason']=classify_before_audit(folder,record,json.loads(exit_path.read_text())['returncode'])
            continue
        raise RuntimeError(f"INFLIGHT_UNCERTAIN_INDEX_{record['index']}：无可靠完成凭据，未自动重跑；保留日志核对。")


def process(folder,state,helper,max_audits=0):
    started=time.monotonic()
    launched=0
    state['phase']='running'
    state['finished']=False
    atomic_json(folder/'summary.json',state)
    for record in state['patients']:
        if record['status'] in TERMINAL:
            continue
        if record['status']!='PENDING':
            raise RuntimeError('BATCH_STATE_INVALID')
        if max_audits and launched>=max_audits:
            state['phase']='paused'
            break
        if fingerprint()!=state['code_fingerprint']:
            raise RuntimeError('CODE_OR_RULES_CHANGED')
        index=record['index']
        stem=f'patient-{index:04d}'
        record.update(status='STARTED',log=stem+'.log',started_at=datetime.now().isoformat(timespec='seconds'))
        atomic_json(folder/'summary.json',state)
        print(f'[{index}/{len(state["patients"])}] 开始取数与审计预检',flush=True)
        env=dict(os.environ,JAVERT_PATIENT_RECEIPT=str(folder/(stem+'.audit.json')))
        with (folder/(stem+'.log')).open('w') as log:
            rc=subprocess.run(['/bin/bash',str(ROOT/'scripts/run_243_patient.sh'),record['patient_id'],state['tag'],'--require-labs'],
                              cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT).returncode
        atomic_json(folder/(stem+'.exit.json'),{'returncode':rc})
        if (folder/(stem+'.audit.started.json')).exists():
            launched+=1
        status=receipt_result(folder,record,state['tag'])
        if status:
            record['status']=status if rc==0 or status=='FAILED_AUDIT' else 'FAILED_AUDIT'
            receipt_data=json.loads((folder/(stem+'.audit.json')).read_text())
            record['lab_rows']=receipt_data.get('lab_rows',0)
            record['lab_pending_reports']=receipt_data.get('lab_pending_reports',0)
        else:
            record['status'],record['reason']=classify_before_audit(folder,record,rc)
        record['finished_at']=datetime.now().isoformat(timespec='seconds')
        text=(folder/(stem+'.log')).read_text(errors='replace')
        snapshots=re.findall(r'^本次目录：([^\r\n]+?)；',text,re.M)
        if snapshots:
            record['snapshot_directory']=snapshots[0]
            snap=Path(snapshots[0])
            if snap.resolve().parent==(ROOT/'output').resolve() and snap.name.startswith('patient.'):
                preflight=snap/'data/preflight.json'
                if preflight.is_file():
                    report=json.loads(preflight.read_text())
                    record['lab_rows']=report.get('rows',{}).get('labs',0)
                    record['lab_linkage']=report.get('lab_linkage',{})
        atomic_json(folder/'summary.json',state)
        print(f'[{index}/{len(state["patients"])}] {record["status"]} {record.get("reason","")}',flush=True)
        if record['status'].startswith('FAILED_'):
            state['phase']='halted'
            break
    else:
        state['phase']='completed'
        state['finished']=True
    state['last_run_seconds']=round(time.monotonic()-started,1)
    atomic_json(folder/'summary.json',state)
    summary(folder,state)
    return 1 if state['phase']=='halted' else 0


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['new','resume','status'])
    parser.add_argument('folder',nargs='?')
    parser.add_argument('--max-audits',type=int,default=0,help='本次最多启动的患者审计数，0为全部；同批resume继续')
    args=parser.parse_args()
    if args.max_audits<0:
        raise RuntimeError('LIMIT_INVALID')
    if args.mode=='status':
        paths=[safe_folder(args.folder)] if args.folder else sorted((ROOT/'output').glob('lis-batch.*'),key=lambda p:p.stat().st_mtime,reverse=True)[:5]
        for folder in paths:
            if (folder/'summary.json').is_file():
                summary(folder,json.loads((folder/'summary.json').read_text()))
        return 0
    (ROOT/'output').mkdir(exist_ok=True)
    with (ROOT/'output/.249-batch.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with (ROOT/'output/.243-patient.lock').open('a') as check:
            fcntl.flock(check,fcntl.LOCK_EX|fcntl.LOCK_NB)
        helper,cfg,hs=runtime_profile()
        subprocess.run([sys.executable,str(helper),'check'],cwd=ROOT,check=True)
        if args.mode=='new':
            folder=Path(tempfile.mkdtemp(prefix='lis-batch.',dir=ROOT/'output'))
            with closing(hs.connect(cfg,timeout=15)) as cn:
                cn.timeout=120
                rows=hs.list_lis_homes(cn,cfg.hub_hospital_code)
            records=freeze_roster(folder,rows)
            state={'version':1,'tag':'st-lis-'+datetime.now().strftime('%y%m%d')+'-'+secrets.token_hex(3),
                   'created_at':datetime.now().isoformat(timespec='seconds'),
                   'code_fingerprint':fingerprint(),'roster_sha256':hashlib.sha256((folder/'roster.json').read_bytes()).hexdigest(),
                   'profile_signature':profile_signature(cfg),
                   'phase':'planned','finished':False,'patients':records}
            atomic_json(folder/'summary.json',state)
            if not records:
                state.update(phase='no_candidates',finished=True)
                atomic_json(folder/'summary.json',state)
                summary(folder,state)
                return 2
            print(f'名单已固定：{len(records)} 个首页；TAG={state["tag"]}；全量范围未按小结或费用预筛。',flush=True)
        else:
            if not args.folder:
                raise RuntimeError('RESUME_REQUIRES_BATCH_DIRECTORY')
            folder=resume_folder(args.folder)
            state=json.loads((folder/'summary.json').read_text())
            if state.get('version')!=1 or fingerprint()!=state['code_fingerprint']:
                raise RuntimeError('BATCH_CODE_VERSION_CHANGED')
            if profile_signature(cfg)!=state.get('profile_signature'):
                raise RuntimeError('BATCH_CONFIG_CHANGED')
            if hashlib.sha256((folder/'roster.json').read_bytes()).hexdigest()!=state['roster_sha256']:
                raise RuntimeError('ROSTER_CHANGED')
            original=json.loads((folder/'roster.json').read_text())
            if [(r['index'],r['patient_id']) for r in original]!=[(r['index'],r['patient_id']) for r in state['patients']]:
                raise RuntimeError('SUMMARY_ROSTER_MISMATCH')
            recover_started(folder,state)
            atomic_json(folder/'summary.json',state)
        return process(folder,state,helper,args.max_audits)


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except BlockingIOError:
        raise SystemExit('已有批跑/单例任务持锁，未重复启动。')
    except RuntimeError as exc:
        raise SystemExit('STOP: '+str(exc))
    except Exception as exc:
        raise SystemExit('STOP: '+type(exc).__name__+'；保留进度，未输出患者号或凭据。')
