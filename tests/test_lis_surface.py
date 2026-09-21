import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from javert.data import hub_source as hs
from javert.web.hub_raw_source import HubRawSource


def test_shanghai_empty_cache_expires(monkeypatch):
    import javert.web.hub_raw_source as module
    clock=[0.0]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(hs,'connect',lambda *a,**k:SimpleNamespace(timeout=0))
    calls=[]
    def fetch(*args):
        calls.append(1)
        return {'labs':pd.DataFrame([] if len(calls)==1 else [{'report_dt':'2026-09-01','result':'SYNTHETIC'}]),
                'lab_linkage':{'candidate_reports':len(calls)},'pending_labs':[]}
    monkeypatch.setattr(hs,'fetch_hospital_bundle',fetch)
    source=HubRawSource(SimpleNamespace(hub_linkage_mode='shanghai',hub_hospital_code='TESTHOSP'))
    assert source.get_labs('CASE_A')==[]
    assert source.get_labs('CASE_A')==[] and len(calls)==1
    clock[0]=31
    assert len(source.get_labs('CASE_A'))==1 and len(calls)==2


def test_raw_payload_keeps_audit_labs_and_pending_separate(monkeypatch):
    import javert.web.api.routes_workbench as routes
    hub=SimpleNamespace(
        get_fees=lambda p:pd.DataFrame([{'medins_list_name':'SYNTHETIC_FEE','fee_ocur_time':'2026-09-01'}]),
        get_notes=lambda p:pd.DataFrame([{'内容':'SYNTHETIC_NOTE','阶段':'病程'}]),
        get_labs=lambda p:[{'rpt_itemname':'ASSIGNED','result':'1','report_dt':'2026-09-01'}],
        get_exams=lambda p:[],
        get_lab_linkage=lambda p:{'source':hs.LIS_SOURCE_VERSION,'assigned_reports':1,'pending_reports':1},
        get_pending_labs=lambda p:[{'rpt_itemname':'PENDING','result':'2','linkage_reason':'OTHER_ADMISSION'}])
    monkeypatch.setattr(routes,'get_config',lambda:SimpleNamespace(hub_linkage_mode='shanghai',hub_raw_enabled=True))
    monkeypatch.setattr(routes,'_get_loader',lambda:hub)
    monkeypatch.setattr(routes,'_get_hub_source',lambda:hub)
    monkeypatch.setattr(routes,'_get_main_diagnosis',lambda p:'SYNTHETIC')
    data=routes._raw_payload('CASE_A')
    assert data['n_labs']==1 and data['n_pending_labs']==1
    assert data['labs'][0]['item']=='ASSIGNED'
    assert data['pending_labs'][0]['linkage_note']=='关联到其他次住院'


def test_etl_snapshot_keeps_pending_out_of_audit_input(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('lis_etl',Path(__file__).parents[1]/'scripts/etl_from_data_hub.py')
    etl=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(etl)
    monkeypatch.setattr(hs,'connect',lambda *a,**k:SimpleNamespace(timeout=0,close=lambda:None))
    linkage={'source':hs.LIS_SOURCE_VERSION,'assigned_reports':1,'candidate_reports':2,'pending_reports':1,'reasons':{'OTHER_ADMISSION':1}}
    lab=pd.DataFrame([{'zyh':'CASE_A','rpt_itemname':'ASSIGNED','result':'1'}])
    bundle={key:pd.DataFrame([{'SYNTHETIC':'value'}]) for key in ('fees','notes','zd','ss','exams')}
    bundle.update(labs=lab,warnings={},lab_linkage=linkage,pending_labs=[{'zyh':'CASE_A','rpt_itemname':'PENDING','result':'2','linkage_reason':'OTHER_ADMISSION'}])
    monkeypatch.setattr(hs,'fetch_hospital_bundle',lambda *a:bundle)
    folder=tmp_path/'snapshot'
    etl.hospital_etl(SimpleNamespace(patients='CASE_A',all=False,check_only=False,output=str(folder)),SimpleNamespace(hub_hospital_code='TESTHOSP'))
    assert pd.read_csv(folder/'lab_results.csv').rpt_itemname.tolist()==['ASSIGNED']
    assert pd.read_csv(folder/'lab_results_pending.csv').rpt_itemname.tolist()==['PENDING']
    report=json.loads((folder/'preflight.json').read_text())
    assert report['lab_linkage']==linkage and report['rows']['labs']==1
    hs.validate_lis_snapshot(SimpleNamespace(labs_path=folder/'lab_results.csv',hub_hospital_code='TESTHOSP'),'CASE_A')
    assert (folder/'lab_results_pending.csv').stat().st_mode & 0o777 == 0o600
