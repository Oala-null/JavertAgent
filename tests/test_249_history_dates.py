import re
from datetime import datetime
from pathlib import Path
from javert.store.models import PatientSidebarItem
from javert.web.templating import build_env


def env():
    return build_env()


def test_same_date_random_tags_render_one_date_chip():
    patients = [PatientSidebarItem(patient_id='CASE_A', batch_tag='st-lis-260922-aabbcc', updated_at=datetime(2026,9,22)),
                PatientSidebarItem(patient_id='CASE_B', batch_tag='st-lis-260922-ddeeff', updated_at=datetime(2026,9,22))]
    html = env().get_template('_sidebar.html').render(patients=patients, active_patient=None, filter='all', sidebar_summary_available=False)
    assert len(re.findall('class="facet-chip"', html)) == 1
    assert 'data-date="2026-09-22"' in html
    assert 'st-lis-260922' not in html

import pytest
from contextlib import contextmanager
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from javert.store.models import RunWithReviews, HistoricalRun, User
from javert.web.batch_dates import batch_date, all_patient_dates
from javert.web.api import routes_workbench as routes
from javert.web import templating


@pytest.mark.parametrize('tag,created,expected', [
    ('st-lis-260922-aabbcc', datetime(2026,9,23), '2026-09-22'),
    ('st-lab-260917-AAAAAA', None, '2026-09-17'),
    ('b260909151642-xB0Pmu', None, '2026-09-09'),
    ('20260922', None, '2026-09-22'),
    ('2026-09-22', None, '2026-09-22'),
    ('st-20260922150000', None, '2026-09-22'),
    ('st-lis-260231-random', '2026-09-21', '2026-09-21'),
    ('20260230', None, '日期未知'),
    (None, datetime(2026,9,22,23,59), '2026-09-22'),
    ('v1.0', '2026-09-22T23:59:00+00:00', '2026-09-22'),
    ('NULL', '2026-09-22', '2026-09-22'),
    ('nonsense', None, '日期未知'),
    ('x126092255', None, '日期未知'),
])
def test_date_normalization(tag,created,expected):
    assert batch_date(tag,created)==expected


class FakeStore:
    def __init__(self):
        self.queries=[]
        self.patients=[PatientSidebarItem(patient_id='CASE_A',batch_tag='st-lis-260922-latest',
                                         updated_at=datetime(2026,9,22),v_count=1,relevant_count=1)]
    def list_patients_with_violations(self,filter_mode):
        return self.patients
    def get_engine(self):
        return self
    @contextmanager
    def connect(self):
        yield self
    def execute(self,sql):
        self.queries.append(str(sql))
        return SimpleNamespace(fetchall=lambda:[('CASE_A','st-lis-260921-old','2026-09-21'),
                                                ('CASE_A','st-lis-260922-new','2026-09-22'),
                                                ('CASE_A','st-lis-260922-second','2026-09-22'),
                                                ('CASE_A',None,'2026-09-20')])


def test_all_history_uses_one_read_only_query_and_includes_null_tags():
    store=FakeStore()
    assert all_patient_dates(store)=={'CASE_A':['2026-09-22','2026-09-21','2026-09-20']}
    assert len(store.queries)==1
    query=store.queries[0].upper()
    assert 'SELECT DISTINCT' in query and 'CONVERT(CHAR(10), CREATED_AT, 23)' in query
    assert 'ROW_NUMBER' not in query and 'WHERE' not in query
    assert not any(word in query.split() for word in ('UPDATE','DELETE','INSERT'))


def test_sidebar_enrichment_does_not_load_hospital_raw_and_can_be_refreshed(monkeypatch):
    store=FakeStore()
    monkeypatch.setattr(routes,'get_config',lambda:SimpleNamespace(hub_linkage_mode='shanghai'))
    monkeypatch.setattr(routes,'_get_loader',lambda:pytest.fail('must not read patient raw'))
    routes._sidebar_cache.clear()
    items=routes._sidebar_patients(store,'all',allow_cached=False)
    assert items[0].batch_dates==['2026-09-22','2026-09-21','2026-09-20']
    assert items[0].batch_tag=='st-lis-260922-latest'
    assert items[0].batch_dates_available
    assert routes._sidebar_patients(store,'all',allow_cached=True) is items
    assert len(store.queries)==1
    routes._sidebar_patients(store,'all',allow_cached=False)
    assert len(store.queries)==2


def test_multiday_patient_matches_all_history_chips():
    p=PatientSidebarItem(patient_id='CASE_MULTI',batch_tag='st-lis-260922-abcdef',updated_at=datetime(2026,9,22),
                         batch_dates=['2026-09-22','2026-09-20'])
    html=env().get_template('_sidebar.html').render(patients=[p],active_patient='CASE_MULTI',filter='all',sidebar_summary_available=False)
    assert html.count('class="facet-chip"')==2
    assert 'data-dates=\'["2026-09-22", "2026-09-20"]\'' in html
    assert 'data-active-patient="CASE_MULTI"' in html
    assert 'st-lis-' not in html


def test_date_lookup_error_has_explicit_partial_history_notice(monkeypatch):
    store=FakeStore()
    store.get_engine=lambda:None
    monkeypatch.setattr(routes,'get_config',lambda:SimpleNamespace(hub_linkage_mode='shanghai'))
    items=routes._sidebar_patients(store,'all',allow_cached=False)
    html=env().get_template('_sidebar.html').render(patients=items,active_patient=None,filter='all',sidebar_summary_available=False)
    assert '历史日期暂不可用' in html
    assert items[0].batch_dates==['2026-09-22']


def test_sidebar_refresh_requires_login_and_preserves_active_filter(monkeypatch):
    app=FastAPI();app.include_router(routes.router)
    store=FakeStore()
    monkeypatch.setattr(routes,'get_sqlserver_store',lambda:store)
    monkeypatch.setattr(routes,'get_config',lambda:SimpleNamespace(hub_linkage_mode='shanghai'))
    monkeypatch.setattr(templating,'_ENV',env())
    monkeypatch.setattr(routes,'current_user',lambda req:None)
    client=TestClient(app)
    denied=client.get('/api/workbench/sidebar?filter=all')
    assert denied.status_code==401 and not store.queries
    monkeypatch.setattr(routes,'current_user',lambda req:SimpleNamespace(id=1))
    response=client.get('/api/workbench/sidebar?filter=all&active_patient=CASE_A')
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'
    assert 'data-active-patient="CASE_A"' in response.text
    assert re.search(r'value="all"\s+selected', response.text)
    assert 'patient-card active' in response.text
    assert response.text.count('class="facet-chip"')==3
    assert 'st-lis-' not in response.text


def test_detail_and_history_show_dates_in_all_three_places():
    current=RunWithReviews(run_id='aud_current',rule_id='R001',patient_id='CASE_A',verdict='VIOLATION',confidence=.8,
                           reasoning='',batch_tag='st-lis-260922-abcdef',created_at=datetime(2026,9,22),
                           history=[HistoricalRun(run_id='aud_old',verdict='CLEAN',confidence=.9,reasoning='',
                                                 batch_tag='st-lab-260921-random',created_at=datetime(2026,9,21))])
    user=User(id=1,username='SYNTHETIC',display_name='SYNTHETIC',created_at=datetime(2026,1,1))
    html=env().get_template('patient_detail.html').render(current_user=user,patients=[],active_patient='CASE_A',
        filter='all',filter_label='全部',runs=[current],overview=None,sidebar_summary_available=False)
    assert '<span class="batch-badge">2026-09-22</span>' in html
    assert '上方最新结果日期为 2026-09-22' in html
    assert '2026-09-21 ·' in html
    assert 'st-lis-260922' not in html and 'st-lab-260921' not in html
    assert '每条规则的最新结果' in html


def test_date_sidebar_keeps_fee_and_diagnosis_facets():
    p=PatientSidebarItem(patient_id='CASE_FEES',batch_tag='legacy',updated_at=datetime(2026,9,20),
                         primary_dx='SYNTHETIC_DIAGNOSIS',fees_sum=26871.39)
    html=env().get_template('_sidebar.html').render(patients=[p],active_patient=None,filter='all',sidebar_summary_available=True)
    assert 'data-date="2026-09-20"' in html
    assert 'data-fees="26871"' in html and '¥26,871' in html
    assert 'data-dx="SYNTHETIC_DIAGNOSIS"' in html
    assert 'id="facet-dx"' in html and 'id="facet-fee"' in html


def test_sidebar_refresh_failure_keeps_client_previous_html(monkeypatch):
    app=FastAPI();app.include_router(routes.router)
    store=FakeStore();store.get_engine=lambda:None
    monkeypatch.setattr(routes,'get_sqlserver_store',lambda:store)
    monkeypatch.setattr(routes,'get_config',lambda:SimpleNamespace(hub_linkage_mode='shanghai'))
    monkeypatch.setattr(routes,'current_user',lambda req:SimpleNamespace(id=1))
    assert TestClient(app).get('/api/workbench/sidebar?filter=all').status_code==503



@pytest.fixture(autouse=True)
def isolated_sidebar_cache():
    routes._sidebar_cache.clear()
    yield
    routes._sidebar_cache.clear()


def test_paginated_dates_filter_all_history_before_page(monkeypatch):
    app = FastAPI(); app.include_router(routes.router)
    store = FakeStore()
    store.patients = [PatientSidebarItem(patient_id=f'CASE-{i:04}', batch_tag='st-lis-260922-latest',
        updated_at=datetime(2026,9,22), v_count=1, relevant_count=1) for i in range(120)]
    monkeypatch.setattr(routes, 'get_sqlserver_store', lambda: store)
    monkeypatch.setattr(routes, 'current_user', lambda req: SimpleNamespace(id=1))
    monkeypatch.setattr(routes, 'get_config', lambda: SimpleNamespace(hub_linkage_mode='shanghai'))
    monkeypatch.setattr(routes, '_get_loader', lambda: pytest.fail('no patient raw in sidebar'))
    monkeypatch.setattr(routes, 'all_patient_dates', lambda source: {
        p.patient_id: ['2026-09-22'] + (['2026-09-20'] if i % 2 else []) for i,p in enumerate(store.patients)})
    client = TestClient(app)
    data = client.get('/api/workbench/patients?filter=all&date=2026-09-20&page=2').json()
    assert data['total'] == 60 and data['pages'] == 2
    assert data['html'].count('class="patient-card') == 10
    assert 'CASE-0101' in data['html'] and 'CASE-0001' not in data['html']
    assert data['dates'] == ['2026-09-22','2026-09-20']
    both = client.get('/api/workbench/patients?filter=all&date=2026-09-20&date=2026-09-22').json()
    assert both['total'] == 120 and both['html'].count('class="patient-card') == 50
    sidebar = client.get('/api/workbench/sidebar?filter=all')
    assert sidebar.status_code == 200 and sidebar.text.count('class="patient-card') == 50


def test_paginated_date_failure_can_retry_without_cached_partial_dates(monkeypatch):
    app = FastAPI(); app.include_router(routes.router)
    store = FakeStore(); store.get_engine = lambda: None
    monkeypatch.setattr(routes, 'get_sqlserver_store', lambda: store)
    monkeypatch.setattr(routes, 'current_user', lambda req: SimpleNamespace(id=1))
    monkeypatch.setattr(routes, 'get_config', lambda: SimpleNamespace(hub_linkage_mode='shanghai'))
    client = TestClient(app)
    assert client.get('/api/workbench/patients?filter=all').status_code == 503
    monkeypatch.setattr(routes, 'all_patient_dates', lambda source: {'CASE_A':['2026-09-20']})
    result = client.get('/api/workbench/patients?filter=all')
    assert result.status_code == 200 and result.json()['dates'] == ['2026-09-20']
