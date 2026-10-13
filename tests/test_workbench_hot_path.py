"""院内性能回归，仅合成数据，不改变旧收费/锚点语义。"""

import json
from types import SimpleNamespace
import pandas as pd
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.testclient import TestClient
from javert.web import hit_resolver as hr
from javert.web.api import routes_workbench as rw

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
    monkeypatch.setattr(rw, 'get_config', lambda: SimpleNamespace(hub_raw_enabled=False))
    monkeypatch.setattr(rw, '_get_loader', lambda: SimpleNamespace(get_fees=lambda _: current['fees']))
    calls = []
    original = hr.fully_refunded_keys
    def count_netting(df):calls.append(len(df));return original(df)
    monkeypatch.setattr(hr, 'fully_refunded_keys', count_netting)
    actual = rw._resolve_hits_for_runs('CASE-FIRST', runs, {})
    assert {rid: [h.model_dump() for h in hits] for rid, hits in actual.items()} == expected
    assert len(calls) == 1, '五条规则、两条收费证据应该共用一次费用净额整理'
    current['fees'] = pd.DataFrame([['合成保留项目','SYNTH-KEEP','SYNTH-K',-1,-20]], columns=columns)
    second = rw._resolve_hits_for_runs('CASE-SECOND', runs, {})
    assert all(not hit.matched_fee_name for hits in second.values() for hit in hits)
    assert len(calls) == 2
    note = SimpleNamespace(run_id='aud_NOTE', rule_id='R100', evidence_json='[{"source":"note","locator":"SYNTHETIC"}]', tool_calls_json='[]')
    assert rw._resolve_hits_for_runs('CASE-NOTE', [note], {})['aud_NOTE']
    assert len(calls) == 2, '只有文书证据不需要费用预处理'

def test_new_audit_signature_invalidates_sidebar_cache(monkeypatch):
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
        assert rw.workbench_sig(None)['sig'] == 101
        assert rw._sidebar_cache
        assert rw.workbench_sig(None)['sig'] == 102
        assert not rw._sidebar_cache
    finally:
        rw._sidebar_cache.clear()
