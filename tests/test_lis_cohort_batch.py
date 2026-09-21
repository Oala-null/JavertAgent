"""院内全量批跑离线模拟：不连接SQL、不调用LLM、不使用真实患者。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from scripts import run_243_lis_batch as batch
from javert.commands.audit_patient import _write_receipt


@pytest.fixture
def run_state(tmp_path,monkeypatch):
    monkeypatch.setattr(batch,'ROOT',tmp_path)
    monkeypatch.setattr(batch,'fingerprint',lambda:'CODE')
    folder=tmp_path/'output/lis-batch.SYNTH'
    folder.mkdir(parents=True)
    rows=pd.DataFrame({'SYXH':['CASE_A','CASE_B','CASE_C'],'report_count':[2,3,1],'multiple_home_reports':[0,1,0]})
    records=batch.freeze_roster(folder,rows)
    state={'version':1,'tag':'st-lis-SYNTH','code_fingerprint':'CODE','patients':records,'phase':'planned','finished':False}
    return folder,state


def fake_process(monkeypatch,outcomes):
    calls=[]
    def run(command,**kwargs):
        pid=command[2]
        calls.append(pid)
        status=outcomes[len(calls)-1]
        receipt=Path(kwargs['env']['JAVERT_PATIENT_RECEIPT'])
        cfg=SimpleNamespace(batch_tag=command[3])
        if status=='LINKAGE':
            kwargs['stdout'].write('预检未通过：LINK_HOME_NOT_UNIQUE\n')
            return SimpleNamespace(returncode=2)
        if status=='NO_LABS':
            kwargs['stdout'].write('NO_ASSIGNED_LABS\n')
            return SimpleNamespace(returncode=4)
        _write_receipt(receipt.with_suffix('.started.json'),cfg,pid,'STARTED')
        if status=='ROUTER_SKIPPED':
            _write_receipt(receipt,cfg,pid,status,completed=0,selected=0,lab_source='medical-record-v1',lab_rows=1)
        else:
            _write_receipt(receipt,cfg,pid,status,completed=1,selected=1,failed=0,lab_source='medical-record-v1',lab_rows=1,
                           sync={'synced':1 if status=='PASSED' else 0,'pending':0 if status=='PASSED' else 1,'skipped':0})
        kwargs['stdout'].write('SYNTHETIC_LOG\n')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(batch.subprocess,'run',run)
    return calls


def test_all_candidates_processed_and_data_skips_reported(run_state,monkeypatch,capsys):
    folder,state=run_state
    calls=fake_process(monkeypatch,['LINKAGE','NO_LABS','PASSED'])
    assert batch.process(folder,state,Path('helper'))==0
    assert calls==['CASE_A','CASE_B','CASE_C']
    assert [r['status'] for r in state['patients']]==['SKIPPED_LINKAGE','SKIPPED_NO_LABS','PASSED']
    assert state['finished']
    assert all(pid not in capsys.readouterr().out for pid in calls)


def test_trial_then_resume_keeps_roster_and_skips_success(run_state,monkeypatch):
    folder,state=run_state
    calls=fake_process(monkeypatch,['LINKAGE','PASSED','ROUTER_SKIPPED'])
    batch.process(folder,state,Path('helper'),max_audits=1)
    assert state['phase']=='paused'
    assert state['patients'][2]['status']=='PENDING'
    batch.recover_started(folder,state)
    batch.process(folder,state,Path('helper'))
    assert calls==['CASE_A','CASE_B','CASE_C']
    assert state['finished']
    assert {json.loads(p.read_text())['tag'] for p in folder.glob('*.audit.json')}=={'st-lis-SYNTH'}


def test_sync_failure_is_not_success_and_resume_does_not_repeat_it(run_state,monkeypatch):
    folder,state=run_state
    calls=fake_process(monkeypatch,['FAILED_AUDIT','PASSED','PASSED'])
    assert batch.process(folder,state,Path('helper'))==1
    assert state['phase']=='halted' and not state['finished']
    batch.recover_started(folder,state)
    batch.process(folder,state,Path('helper'))
    assert len(calls)==3 and state['patients'][0]['status']=='FAILED_AUDIT'


def test_resume_recovers_trusted_receipt_after_parent_crash(run_state):
    folder,state=run_state
    row=state['patients'][0]
    row['status']='STARTED'
    _write_receipt(folder/'patient-0001.audit.json',SimpleNamespace(batch_tag=state['tag']),'CASE_A','PASSED',
                   completed=1,selected=1,failed=0,lab_source='medical-record-v1',lab_rows=1,sync={'synced':1,'pending':0,'skipped':0})
    batch.recover_started(folder,state)
    assert row['status']=='PASSED'


def test_log_text_cannot_fake_completed_audit(run_state):
    folder,state=run_state
    state['patients'][0]['status']='STARTED'
    (folder/'patient-0001.log').write_text('(1 completed, 0 pending, 0 failed)\nmssql_sync: 1/1 succeeded (0 pending)\nPATIENT_EXIT=0')
    with pytest.raises(RuntimeError,match='INFLIGHT_UNCERTAIN'):
        batch.recover_started(folder,state)


def test_completed_state_requires_receipt(run_state):
    folder,state=run_state
    state['patients'][0]['status']='PASSED'
    with pytest.raises(RuntimeError,match='COMPLETED_RECEIPT'):
        batch.recover_started(folder,state)


def test_receipt_cannot_belong_to_other_patient_or_batch(run_state):
    folder,state=run_state
    state['patients'][0]['status']='STARTED'
    _write_receipt(folder/'patient-0001.audit.json',SimpleNamespace(batch_tag='st-lis-OTHER'),'CASE_A','ROUTER_SKIPPED',completed=0,selected=0)
    with pytest.raises(RuntimeError,match='RECEIPT_SCOPE'):
        batch.recover_started(folder,state)


def test_code_change_stops_before_start(run_state,monkeypatch):
    folder,state=run_state
    monkeypatch.setattr(batch,'fingerprint',lambda:'DIFFERENT')
    with pytest.raises(RuntimeError,match='CODE_OR_RULES_CHANGED'):
        batch.process(folder,state,Path('helper'))
    assert state['patients'][0]['status']=='PENDING'


def test_config_signature_binds_model_without_storing_password():
    a=SimpleNamespace(llm_model='MODEL_A',sql_password='SYNTHETIC_SECRET_A')
    b=SimpleNamespace(llm_model='MODEL_B',sql_password='SYNTHETIC_SECRET_A')
    c=SimpleNamespace(llm_model='MODEL_A',sql_password='SYNTHETIC_SECRET_B')
    assert batch.profile_signature(a)!=batch.profile_signature(b)
    assert batch.profile_signature(a)==batch.profile_signature(c)


def test_all_roster_rows_retained_and_duplicate_id_not_repeated(tmp_path):
    rows=pd.DataFrame({'SYXH':['CASE_A','case_a','INVALID SPACE'],'report_count':[1,1,2],'multiple_home_reports':[0,0,2]})
    records=batch.freeze_roster(tmp_path,rows)
    assert len(records)==2
    assert records[-1]['status']=='INVALID_ID'
