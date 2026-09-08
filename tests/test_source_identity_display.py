"""来源身份只用于显示；全部使用合成数据与内部测试键。"""
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import pytest
from javert.store.models import PatientSidebarItem, User
from javert.web import patient_overview as po
from javert.web.api import routes_workbench as wb
from javert.web.templating import render
from javert.data.csv_loader import CsvLoader

KEY = 'chronic-synthetic-case'
NAME = '合成姓名'
VISIT = 'SYNTHETIC-VISIT-001'


def test_source_visit_id_stays_text_through_base_and_overlay_csv(tmp_path):
    base=tmp_path/'case_notes.csv'
    base.write_text('住院号,内容,source_patient_name,source_visit_id\nSYNTHETIC-OLD,旧合成文书,,\n')
    overlay=tmp_path/'overlay';overlay.mkdir()
    (overlay/'case_notes.csv').write_text('住院号,内容,source_patient_name,source_visit_id\nchronic-synthetic-case,合成正文,合成姓名,000123\n')
    loader=CsvLoader(base,tmp_path/'fees.csv',overlay)
    assert po.source_identity(loader.get_notes(KEY))['source_visit_id']=='000123'
    direct=CsvLoader(overlay/'case_notes.csv',tmp_path/'fees.csv')
    assert po.source_identity(direct.get_notes(KEY))['source_visit_id']=='000123'


def test_sidebar_uses_source_label_but_keeps_internal_link():
    p=PatientSidebarItem(patient_id=KEY,batch_tag='慢病')
    row=SimpleNamespace(**{**p.model_dump(), "display_label": f"{NAME} · {VISIT}"})
    html=render('_sidebar.html',patients=[row],active_patient=KEY,filter='all')
    assert f'{NAME} · {VISIT}' in html
    assert f'/workbench/{KEY}?' in html


def test_header_uses_source_label_not_internal_key():
    html=render('patient_detail.html',patients=[],runs=[],active_patient=KEY,
        filter='all',filter_label='全部',overview=None,display_label=f'{NAME} · {VISIT}',
        current_user=User(id=1,username='synthetic',created_at=datetime.now(timezone.utc)))
    assert f'<h1>{NAME} · {VISIT}</h1>' in html
    assert f'window.JAVERT_PATIENT = "{KEY}"' in html


@pytest.mark.parametrize('rows,label,visit', [
    ([{'source_patient_name':NAME,'source_visit_id':VISIT}]*2, f'{NAME} · {VISIT}', VISIT),
    ([{'source_patient_name':NAME,'source_visit_id':None}], NAME, None),
    ([{'source_visit_id':VISIT}], VISIT, VISIT),
    ([{'source_patient_name':'','source_visit_id':float('nan')}], None, None),
    ([{'source_patient_name':NAME},{'source_patient_name':'另一合成姓名'}], '原文身份信息冲突', None),
    ([{'source_patient_name':NAME,'source_visit_id':VISIT},{'source_visit_id':'SYNTHETIC-OTHER'}], '原文身份信息冲突', None),
])
def test_source_identity_unique_missing_and_conflict(rows,label,visit):
    identity=po.source_identity(pd.DataFrame(rows))
    assert identity['display_label']==label
    assert identity['source_visit_id']==visit
    assert identity['source_identity_present'] is bool(label or visit)


def test_old_notes_have_no_identity_override():
    assert po.source_identity(pd.DataFrame([{'内容':'合成旧文书'}])) == {
        'display_label':None, 'source_visit_id':None, 'source_identity_present':False}


def test_sidebar_reads_notes_with_internal_key_and_only_for_chronic(monkeypatch):
    calls=[]
    def get_notes(key):
        calls.append(key)
        return pd.DataFrame([{'source_patient_name':NAME,'source_visit_id':VISIT}])
    monkeypatch.setattr(wb,'_get_loader',lambda:SimpleNamespace(get_notes=get_notes))
    monkeypatch.setattr(wb,'get_fees_sum_map',lambda _: {})
    monkeypatch.setattr(wb,'get_primary_dx',lambda *_: '')
    rows=wb._enrich_sidebar([PatientSidebarItem(patient_id=KEY,batch_tag='慢病'),PatientSidebarItem(patient_id='SYNTHETIC-OLD')])
    assert calls == [KEY]
    assert rows[0].display_label==f'{NAME} · {VISIT}'
    assert rows[1].display_label is None


def test_overview_uses_source_id_or_explicit_missing_without_rekey(monkeypatch):
    class Loader:
        def get_notes(self,key):
            assert key == KEY
            return pd.DataFrame([{'内容':'合成内容','source_patient_name':NAME}])
        def get_fees(self,key):
            assert key == KEY
            return pd.DataFrame()
    monkeypatch.setattr(po,'_load_zd',lambda: {})
    monkeypatch.setattr(po,'_load_ss',lambda: {})
    monkeypatch.setattr(po,'get_stored_spokes',lambda _: [])
    po.reset_caches()
    overview=po.build_overview(KEY,Loader())
    assert overview['patient_id']==KEY and overview['display_label']==NAME
    html=render('_patient_overview.html',overview=overview)
    assert '<strong>原文未提取</strong>' in html
    assert f'<strong>{KEY}</strong>' not in html
    overview['source_visit_id']=VISIT
    assert f'<strong>{VISIT}</strong>' in render('_patient_overview.html',overview=overview)
    overview['source_identity_present']=False
    assert f'<strong>{KEY}</strong>' in render('_patient_overview.html',overview=overview)
    po.reset_caches()


def test_display_identity_is_escaped_in_html_and_script():
    label='<script>synthetic()</script>'
    p=PatientSidebarItem(patient_id=KEY,display_label=label,batch_tag='慢病')
    sidebar=render('_sidebar.html',patients=[p],active_patient=KEY,filter='all')
    assert label not in sidebar and '&lt;script&gt;' in sidebar
    html=render('patient_detail.html',patients=[],runs=[],active_patient=KEY,
        filter='all',filter_label='全部',overview=None,display_label=label,
        current_user=User(id=1,username='synthetic',created_at=datetime.now(timezone.utc)))
    assert label not in html and r'\u003cscript\u003e' in html
    assert f'window.JAVERT_PATIENT = "{KEY}"' in html
    assert 'window.JAVERT_DISPLAY_LABEL' in html


def test_nonchronic_overview_empty_overlay_identity_columns_keep_original_id(monkeypatch):
    patient_id = 'SYNTHETIC-LEGACY-VISIT'
    class Loader:
        def get_notes(self, key):
            assert key == patient_id
            return pd.DataFrame([
                {'内容': '合成旧文书', 'source_patient_name': '', 'source_visit_id': None},
                {'内容': '合成补充文书', 'source_patient_name': float('nan'), 'source_visit_id': '  '},
            ])
        def get_fees(self, key):
            assert key == patient_id
            return pd.DataFrame()
    monkeypatch.setattr(po, '_load_zd', lambda: {})
    monkeypatch.setattr(po, '_load_ss', lambda: {})
    monkeypatch.setattr(po, 'get_stored_spokes', lambda _: [])
    po.reset_caches()
    try:
        overview = po.build_overview(patient_id, Loader())
        assert overview['source_identity_present'] is False
        html = render('_patient_overview.html', overview=overview)
        assert f'<strong>{patient_id}</strong>' in html
        assert '原文未提取' not in html
    finally:
        po.reset_caches()
