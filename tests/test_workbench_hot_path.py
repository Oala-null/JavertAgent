"""工作台传输/SQL往返/费用准备回归，全部合成数据。"""
import json
from types import SimpleNamespace

import pandas as pd
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.testclient import TestClient

from javert.web import hit_resolver as hr
from javert.web.api import routes_workbench as rw
from tests.test_workbench_performance import synthetic_workbench

PROFILE_SELECTOR = rw._get_profile_hub_source_for_patient
RESOLVE_RUNS = rw._resolve_hits_for_runs


def test_large_html_is_compressed_but_sse_is_not():
    from javert.web.api.main import create_app
    app = create_app(with_mssql=False)
    @app.get('/__synthetic_html')
    def html():return HTMLResponse('<p>SYNTHETIC</p>' * 10000)
    @app.get('/__synthetic_events')
    def events():return StreamingResponse(iter([b'data: SYNTHETIC\n\n']), media_type='text/event-stream')
    client = TestClient(app)
    html = client.get('/__synthetic_html', headers={'Accept-Encoding': 'gzip'})
    assert html.headers.get('content-encoding') == 'gzip'
    assert len(html.content) == 160000
    events = client.get('/__synthetic_events', headers={'Accept-Encoding': 'gzip'})
    assert 'content-encoding' not in events.headers
    assert events.content == b'data: SYNTHETIC\n\n'


def test_patient_switch_reuses_source_lookup(synthetic_workbench, monkeypatch):
    client, _, _ = synthetic_workbench
    store = rw.get_sqlserver_store()
    calls = []
    def tag(patient_id):calls.append(patient_id);return "SYNTHETIC-BATCH"
    monkeypatch.setattr(store, 'latest_batch_tag_for_patient', tag)
    monkeypatch.setattr(rw, '_get_profile_hub_source_for_patient', PROFILE_SELECTOR)
    monkeypatch.setattr(rw, '_resolve_hits_for_runs', RESOLVE_RUNS)
    monkeypatch.setattr(rw, 'get_config', lambda: SimpleNamespace(hub_raw_enabled=False, hub_raw_profiles={'SYNTHETIC': object()}))
    cache = getattr(rw, '_batch_tag_cache', {})
    cache.clear()
    try:
        for patient in ('CASE-SYNTHETIC-A', 'CASE-SYNTHETIC-B', 'CASE-SYNTHETIC-A'):
            assert client.get('/workbench/' + patient + '?filter=all').status_code == 200
        assert calls == ['CASE-SYNTHETIC-A', 'CASE-SYNTHETIC-B']
    finally:
        cache.clear()


def test_fee_preparation_once_per_request_preserves_netting_and_isolation(monkeypatch):
    columns = ['medins_list_name','med_list_codg','medins_list_codg','cnt','det_item_fee_sumamt']
    fees = pd.DataFrame([
        ['合成保留项目', 'SYNTH-KEEP', 'SYNTH-K', 1, 20],
        ['合成退款项目', 'SYNTH-REFUND', 'SYNTH-R', 1, 10],
        ['合成退款项目', 'SYNTH-REFUND', 'SYNTH-R', -1, -10],
    ], columns=columns)
    evidence = json.dumps([{'source': 'fee', 'locator': name, 'text': 'SYNTHETIC'} for name in ('合成保留项目','合成退款项目')], ensure_ascii=False)
    runs = [SimpleNamespace(run_id=f'aud_SYNTH_{i}', rule_id=f'R{i:03}', evidence_json=evidence, tool_calls_json='[]') for i in range(5)]
    expected = {r.run_id: [h.model_dump() for h in hr.resolve_hits(r, None, patient_fee_df=fees)] for r in runs}
    current = {'fees': fees}
    monkeypatch.setattr(rw, '_get_profile_hub_source_for_patient', lambda _: None)
    monkeypatch.setattr(rw, '_get_loader', lambda: SimpleNamespace(get_fees=lambda _: current['fees']))
    calls = []
    original = hr.fully_refunded_keys
    def count_netting(df):calls.append(len(df));return original(df)
    monkeypatch.setattr(hr, 'fully_refunded_keys', count_netting)
    actual = rw._resolve_hits_for_runs('CASE-FIRST', runs, {})
    assert {rid: [h.model_dump() for h in hits] for rid, hits in actual.items()} == expected
    assert all([h.name for h in hits] == ['合成保留项目'] for hits in actual.values())
    assert len(calls) == 1, '五条规则、两条收费证据应该共用一次费用净额整理'
    current['fees'] = pd.DataFrame([['合成保留项目','SYNTH-KEEP','SYNTH-K',-1,-20]], columns=columns)
    assert not any(rw._resolve_hits_for_runs('CASE-SECOND', runs, {}).values())
    assert len(calls) == 2
    note = SimpleNamespace(run_id='aud_NOTE', rule_id='R100', evidence_json='[{"source":"note","locator":"SYNTHETIC"}]', tool_calls_json='[]')
    assert rw._resolve_hits_for_runs('CASE-NOTE', [note], {})['aud_NOTE']
    assert len(calls) == 2, '只有文书证据不需要费用预处理'


def test_source_cache_expires_and_does_not_cache_unknown(monkeypatch):
    clock = [0.0]
    values = iter([None, 'SYNTHETIC-FIRST', 'SYNTHETIC-SECOND'])
    monkeypatch.setattr(rw.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(rw, 'get_sqlserver_store', lambda: SimpleNamespace(latest_batch_tag_for_patient=lambda _: next(values)))
    rw._batch_tag_cache.clear()
    try:
        assert rw._latest_batch_tag_cached('CASE-SYNTHETIC') is None
        assert rw._latest_batch_tag_cached('CASE-SYNTHETIC') == 'SYNTHETIC-FIRST'
        clock[0] = 20
        assert rw._latest_batch_tag_cached('CASE-SYNTHETIC') == 'SYNTHETIC-FIRST'
        clock[0] = 31
        assert rw._latest_batch_tag_cached('CASE-SYNTHETIC') == 'SYNTHETIC-SECOND'
    finally:
        rw._batch_tag_cache.clear()


def test_new_audit_signature_invalidates_sidebar_and_source_caches(monkeypatch):
    signatures = iter([101, 101, 102])
    class Connection:
        def __enter__(self):return self
        def __exit__(self, *_):return None
        def execute(self, _):return SimpleNamespace(scalar=lambda: next(signatures))
    monkeypatch.setattr(rw, 'get_sqlserver_store', lambda: SimpleNamespace(get_engine=lambda: SimpleNamespace(connect=Connection)))
    monkeypatch.setattr(rw, 'current_user', lambda _: object())
    monkeypatch.setattr(rw, '_sidebar_audit_signature', None, raising=False)
    try:
        assert rw.workbench_sig(None)['sig'] == 101
        rw._sidebar_cache['all'] = (0, [])
        rw._batch_tag_cache['CASE-SYNTHETIC'] = (0, 'SYNTHETIC')
        assert rw.workbench_sig(None)['sig'] == 101
        assert rw._sidebar_cache and rw._batch_tag_cache
        assert rw.workbench_sig(None)['sig'] == 102
        assert not rw._sidebar_cache and not rw._batch_tag_cache
    finally:
        rw._sidebar_cache.clear();rw._batch_tag_cache.clear()
