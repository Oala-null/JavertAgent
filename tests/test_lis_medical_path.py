"""执行真实SQL的去标识LIS路径回归，不连接真实医院。"""
import json

import pandas as pd
import pytest

from javert.data import hub_source as hs
from tests.test_hospital_linkage_sql import Hospital


class LISHospital(Hospital):
    def __init__(self):
        super().__init__()
        # 父夹具同时包含已部署DETAIL口径及医疗记录字段。

    def row(self,table,**values):
        columns=[r[1] for r in self.db.execute('PRAGMA table_info('+table+')')]
        self.db.execute('INSERT INTO '+table+' VALUES ('+','.join('?' for _ in columns)+')',
                        [values.get(c,'') for c in columns])

    def case(self, suffix='A', start='2026-08-03 08:00:00', end='2026-08-08 08:00:00', hospital='TESTHOSP'):
        self.admission(hospital,suffix,'SYNTHETIC')
        visit='CLINICAL_'+suffix
        self.db.execute('UPDATE TB_BA_SYJBK SET RYRQ=?,CYRQ=? WHERE YLJGYQDM=? AND SYXH=?',
                        (start,end,hospital,'CASE_'+suffix))
        self.db.execute('UPDATE TB_CIS_LEAVEHOSPITAL_SUMMARY SET RYSJ=?,CYSJ=? WHERE YLJGYQDM=? AND BAH=?',
                        (start,end,hospital,'CHART_'+suffix))
        self.db.execute('UPDATE TB_HIS_ZY_ADM_REG SET RYSJ=? WHERE YLJGYQDM=? AND JZLSH=?',
                        (start,hospital,'VISIT_'+suffix))
        self.db.execute("UPDATE TB_LIS_REPORT SET KH='CARD_SHARED',KLX='0',JZLSH=? WHERE YLJGYQDM=? AND BGDH=?",
                        (visit,hospital,'REPORT_'+suffix))
        self.db.execute('INSERT INTO TB_YL_ZY_MEDICAL_RECORD VALUES (?,?,?,?,?,?,?,?)',
                        (hospital,visit,'CHART_'+suffix,'CARD_SHARED','0',start,end,'1'))
        return hs.resolve_patient(self,'CASE_'+suffix,hospital)


@pytest.fixture
def hospital():
    h=LISHospital()
    yield h
    h.db.close()


def test_different_clinical_and_summary_visits_load_labs(hospital):
    p=hospital.case()
    result=hs.fetch_labs(hospital,[p.syxh],patient=p)
    assert len(result)==1
    assert result.iloc[0]['zyh']=='CASE_A'
    assert p.visit=='VISIT_A'
    assert result.attrs['lab_linkage']['assigned_reports']==1


def test_candidate_scope_does_not_require_summary_or_fee(hospital):
    hospital.case()
    hospital.db.execute('DELETE FROM TB_CIS_LEAVEHOSPITAL_SUMMARY')
    hospital.db.execute('DELETE FROM TB_HIS_ZY_FEE_DETAIL_FS')
    rows=hs.list_lis_homes(hospital,'TESTHOSP')
    assert list(rows['SYXH'])==['CASE_A']


def test_other_stay_is_not_an_audit_lab(hospital):
    p=hospital.case()
    hospital.case('B','2026-08-12 08:00:00','2026-08-20 08:00:00')
    labs=hs.fetch_labs(hospital,[p.syxh],patient=p)
    assert len(labs)==1
    assert labs.attrs['lab_linkage']['candidate_reports']==2
    assert labs.attrs['lab_linkage']['pending_reports']==1
    assert labs.attrs['pending_labs'][0]['linkage_reason']=='OTHER_ADMISSION'
    assert len(hs.list_lis_homes(hospital,'TESTHOSP'))==2


def test_ambiguous_dates_without_chart_disambiguation_are_pending(hospital):
    p=hospital.case()
    hospital.case('B')
    hospital.db.execute("UPDATE TB_YL_ZY_MEDICAL_RECORD SET BAH='EXTERNAL_CHART'")
    labs=hs.fetch_labs(hospital,[p.syxh],patient=p)
    assert labs.empty
    assert labs.attrs['lab_linkage']['pending_reports']==2
    assert {r['linkage_reason'] for r in labs.attrs['pending_labs']}=={'ADMISSION_AMBIGUOUS'}


def test_unique_dates_do_not_require_same_chart_number(hospital):
    p=hospital.case()
    hospital.db.execute("UPDATE TB_YL_ZY_MEDICAL_RECORD SET BAH='EXTERNAL_CHART',RYSJ='2026-08-03 10:00:00'")
    assert len(hs.fetch_labs(hospital,[p.syxh],patient=p))==1


def test_missing_dates_do_not_use_single_candidate_as_proof(hospital):
    p=hospital.case()
    hospital.db.execute("UPDATE TB_YL_ZY_MEDICAL_RECORD SET RYSJ='1900-01-01 00:00:00'")
    labs=hs.fetch_labs(hospital,[p.syxh],patient=p)
    assert labs.empty
    assert labs.attrs['lab_linkage']['reasons']=={'MEDICAL_TIME_MISSING':1}


def test_medical_duplicates_do_not_multiply_indicators(hospital):
    p=hospital.case()
    hospital.db.execute('INSERT INTO TB_YL_ZY_MEDICAL_RECORD SELECT * FROM TB_YL_ZY_MEDICAL_RECORD')
    assert len(hs.fetch_labs(hospital,[p.syxh],patient=p))==1


def test_report_key_collision_outside_patient_scope_is_not_accepted(hospital):
    p=hospital.case()
    hospital.row('TB_LIS_REPORT',YLJGYQDM='TESTHOSP',BGDH='REPORT_A',BGRQ='2026-08-04',
                 JZLSH='UNRELATED_VISIT',KH='UNRELATED_CARD',KLX='0',BGSJ='2026-08-04 09:00:00')
    labs=hs.fetch_labs(hospital,[p.syxh],patient=p)
    assert labs.empty
    assert labs.attrs['lab_linkage']['reasons']=={'REPORT_HEADER_AMBIGUOUS':1}


@pytest.mark.parametrize('statement',[
    "UPDATE TB_YL_ZY_MEDICAL_RECORD SET YLJGYQDM='OTHERHOSP'",
    "UPDATE TB_YL_ZY_MEDICAL_RECORD SET KLX='OTHER_TYPE'",
    "UPDATE TB_YL_ZY_MEDICAL_RECORD SET XGBZ='2'",
    "UPDATE TB_LIS_REPORT SET KLX='OTHER_TYPE'",
])
def test_identity_and_cancellation_fail_closed(hospital,statement):
    p=hospital.case()
    hospital.db.execute(statement)
    labs=hs.fetch_labs(hospital,[p.syxh],patient=p)
    assert labs.empty and labs.attrs['lab_linkage']['pending_reports']==1


def test_report_date_join_remains_required(hospital):
    p=hospital.case()
    hospital.db.execute("UPDATE TB_LIS_INDICATORS SET BGRQ='2026-08-05'")
    labs=hs.fetch_labs(hospital,[p.syxh],patient=p)
    assert labs.empty and labs.attrs['lab_linkage']['candidate_reports']==0


def test_snapshot_provenance_detects_changed_file(tmp_path):
    path=tmp_path/'lab_results.csv'
    pd.DataFrame([{'zyh':'CASE_A','result':'SYNTHETIC'}]).to_csv(path,index=False)
    source=hs.lis_snapshot(path)
    (tmp_path/'preflight.json').write_text(json.dumps({'lab_source':source}))
    from types import SimpleNamespace
    cfg=SimpleNamespace(labs_path=path)
    hs.validate_lis_snapshot(cfg)
    path.write_text('different')
    with pytest.raises(hs.HospitalLinkageError,match='LIS_SNAPSHOT'):
        hs.validate_lis_snapshot(cfg)
