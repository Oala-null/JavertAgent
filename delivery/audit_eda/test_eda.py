"""纯合成验收；python test_eda.py 或 python test_eda.py --demo 新目录。"""
import csv
from contextlib import redirect_stdout
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import eda_249 as eda

TAG = 'st-lis-000001-demo01'


def fixture():
    cn = sqlite3.connect(':memory:')
    cn.execute("ATTACH DATABASE ':memory:' AS dbo")
    cn.execute('''CREATE TABLE dbo.javert_audit_runs (
        id INTEGER, run_id TEXT, patient_id TEXT, rule_id TEXT, verdict TEXT, batch_tag TEXT,
        duration_ms INTEGER, started_at TEXT, created_at TEXT, rule_yaml_snapshot TEXT)''')
    cn.execute('CREATE TABLE dbo.javert_vio_review (run_id TEXT, review_verdict TEXT, is_latest INTEGER)')
    # A曾违规但本批CLEAN；B两条规则命中；C是存量违规；D同时间并列按id择CLEAN；水位后的违规排除。
    rows = [
        (1, 'demo-old-a', 'TEST_HOME_A', 'R001', 'VIOLATION', 'demo-old', 1000, '01', '01', '重复收费'),
        (2, 'demo-new-a', 'TEST_HOME_A', 'R001', 'CLEAN', TAG, 2000, '02', '02', '重复收费'),
        (3, 'demo-b-1', 'TEST_HOME_B', 'R001', 'VIOLATION', TAG, 3000, '03', '03', '重复收费'),
        (4, 'demo-b-2', 'TEST_HOME_B', 'R002', 'VIOLATION', TAG, 4000, '04', '04', '超标准收费'),
        (5, 'demo-old-c', 'TEST_HOME_C', 'R002', 'VIOLATION', None, None, '05', '05', '超标准收费'),
        (6, 'demo-d-old', 'TEST_HOME_D', 'R003', 'VIOLATION', TAG, 6000, '06', '06', '重复收费'),
        (7, 'demo-d-new', 'TEST_HOME_D', 'R003', 'CLEAN', TAG, 7000, '06', '06', '重复收费'),
        (8, 'demo-future', 'TEST_HOME_A', 'R001', 'VIOLATION', TAG, 8000, '08', '08', '重复收费'),
    ]
    for row in rows:
        cn.execute('INSERT INTO dbo.javert_audit_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (*row[:-1], 'domain: 合成测试\nviolation_type: ' + row[-1]))
    cn.executemany('INSERT INTO dbo.javert_vio_review VALUES(?,?,?)', [
        ('demo-b-1', 'V', 1), ('demo-b-1', 'V', 1), ('demo-b-1', 'C', 0),
        ('demo-b-2', 'V', 1), ('demo-b-2', 'C', 1)])
    rows = eda.query(cn, eda.READ_RESULTS, (7, TAG))
    reviews = {}
    for r in eda.query(cn, eda.READ_REVIEWS, (7, TAG)):
        reviews.setdefault(r['run_id'], set()).add(r['review_verdict'])
    cn.close()
    eda.add_metadata(rows)
    state = dict(version=1, tag=TAG, created_at='2030-01-01T08:00:00', phase='running', finished=False,
                 patients=[
        dict(index=1, patient_id='TEST_HOME_B', status='PASSED', lab_rows=3, started_at='2030-01-01T08:00:00', finished_at='2030-01-01T08:02:00'),
        dict(index=2, patient_id='TEST_HOME_A', status='SKIPPED_NO_LABS', reason='NO_ASSIGNED_LABS', started_at='2030-01-01T08:03:00', finished_at='2030-01-01T08:03:10'),
        dict(index=3, patient_id='TEST_HOME_D', status='STARTED', started_at='2030-01-01T08:04:00'),
        dict(index=4, patient_id='TEST_HOME_E', status='PENDING')])
    history = [dict(batch_tag=TAG, run_count=5, patients=3, duration_ms=22000, missing_duration=0, first_record='02', last_record='06'),
               dict(batch_tag='demo-old', run_count=1, patients=1, duration_ms=1000, missing_duration=0, first_record='01', last_record='01'),
               dict(batch_tag=None, run_count=1, patients=1, duration_ms=0, missing_duration=1, first_record='05', last_record='05')]
    meta = dict(demo=True, result_max_id=7, started_at='2030-01-01T08:10:00+08:00', finished_at='2030-01-01T08:10:01+08:00',
                progress_observed_at='2030-01-01T08:10:00', fees_started_at='2030-01-01T08:10:00+08:00', fees_finished_at='2030-01-01T08:10:01+08:00')
    return rows, history, reviews, state, {'TEST_HOME_B': Decimal('80.00')}, {'TEST_HOME_C': 'LINK_HOME_NOT_UNIQUE'}, meta


class EdaTest(unittest.TestCase):
    def test_watermark_latest_batch_and_stock(self):
        data = fixture()
        report = eda.build_report(*data)
        batch, all_ = report['scopes']
        self.assertEqual((batch['patients'], batch['results'], batch['violation_hits'], batch['violation_patients']), (3, 4, 2, 1))
        self.assertEqual((all_['patients'], all_['results'], all_['violation_hits'], all_['violation_patients']), (4, 5, 3, 2))
        self.assertEqual(batch['timestamp_tie_results'], 1)
        self.assertEqual(batch['rule_seconds'], 16)
        self.assertEqual(batch['all_attempt_rule_seconds'], 22)
        self.assertEqual(all_['all_attempt_rule_seconds'], 23)

    def test_unknown_money_coverage_and_review_dedup(self):
        report = eda.build_report(*fixture())
        s = report['scopes'][1]
        self.assertIsNone(s['violation_amount_yuan'])
        self.assertEqual(s['violation_patient_current_settlement_yuan'], '80.00')
        self.assertEqual((s['fee_covered_patients'], s['fee_unavailable_patients']), (1, 1))
        self.assertEqual((s['expert_unanimous_v_hits'], s['expert_conflict_hits'], s['machine_v_without_review_hits']), (1, 1, 1))
        self.assertEqual(sum(c['violation_patients'] for c in s['categories']), 3)  # 两类别人数不能相加
        self.assertEqual(s['violation_patients'], 2)
        self.assertEqual(eda.build_report(*fixture(), review_ok=False)['scopes'][1]['expert_unanimous_v_hits'], None)

    def test_batch_time_does_not_claim_inflight_completed(self):
        r = eda.build_report(*fixture())['batch']
        self.assertEqual(r['span_seconds'], 600)
        self.assertEqual(r['completed_patient_processing_seconds'], 130)
        self.assertEqual(r['timed_patients'], 2)
        self.assertEqual(r['passed_with_labs'], 1)
        self.assertFalse(r['finished'])

    def test_fee_refund_and_invalid(self):
        import pandas as pd
        self.assertEqual(eda.net_fees(pd.DataFrame({'det_item_fee_sumamt': ['100.10', '-20.10']})), Decimal('80.00'))
        for vals in ([], ['NaN'], ['bad'], ['Infinity']):
            with self.assertRaises((ValueError, eda.InvalidOperation)):
                eda.net_fees(pd.DataFrame({'det_item_fee_sumamt': vals}))

    def test_no_fees_is_unknown_not_zero(self):
        args = list(fixture())
        args[4] = {}
        args[5] = {'TEST_HOME_B': 'NOT_REQUESTED', 'TEST_HOME_C': 'NOT_REQUESTED'}
        s = eda.build_report(*args)['scopes'][1]
        self.assertIsNone(s['violation_patient_current_settlement_yuan'])
        self.assertEqual(s['fee_unavailable_patients'], 2)
        self.assertEqual(eda.collect_fees({'TEST_HOME_A'}, None, None, False), ({}, {'TEST_HOME_A': 'NOT_REQUESTED'}))

    def test_safe_export_and_csv(self):
        report = eda.build_report(*fixture())
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            eda.write_report(p, report)
            self.assertEqual(p.stat().st_mode & 0o777, 0o700)
            self.assertTrue((p / 'REPORT_COMPLETE').is_file())
            for file in p.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                content = file.read_text(encoding='utf-8-sig')
                self.assertNotIn('TEST_HOME_', content)
                self.assertNotIn('demo-b-1', content)
                self.assertNotIn('patient_id', content)
            with (p / 'overview.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(len(list(csv.reader(f))), 3)
            self.assertIn('不是违规额', (p / 'report.txt').read_text(encoding='utf-8-sig'))
        self.assertEqual(eda.csv_safe(' =1+1'), "' =1+1")

    def test_batch_selection_and_no_batch(self):
        state = fixture()[3]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'output/lis-batch.synthetic'
            folder.mkdir(parents=True)
            (folder / 'summary.json').write_text(json.dumps(state))
            self.assertEqual(eda.load_batch('latest', root)['tag'], TAG)
            self.assertIsNone(eda.load_batch('none', root))
            with self.assertRaises(RuntimeError):
                eda.load_batch(tmp, root)
        args = list(fixture())
        args[3] = None
        report = eda.build_report(*args)
        self.assertEqual(len(report['scopes']), 1)
        self.assertIsNone(report['batch'])

    def test_metadata_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'configs/rules').mkdir(parents=True)
            (root / 'configs/rules/R001.yaml').write_text('violation_type: 当前合成类别\n')
            rows = [dict(rule_id='R001', rule_yaml_snapshot='violation_type: 历史类别'), dict(rule_id='R001', rule_yaml_snapshot='[')]
            eda.add_metadata(rows, root)
            self.assertEqual([r['category'] for r in rows], ['历史类别', '当前合成类别'])
            self.assertEqual([r['metadata_fallback'] for r in rows], [False, True])

    def test_main_end_to_end_without_hospital(self):
        rows, history, reviews, state, _, _, _ = fixture()
        class Connection:
            def execute(self, sql):
                assert sql == 'SET LOCK_TIMEOUT 10000'
            def close(self):
                pass
        def read(cn, sql, params=()):
            if sql == eda.READ_RESULTS:
                self.assertEqual(params, (7, TAG))
                return rows
            if sql == eda.READ_HISTORY:
                return history
            if sql == eda.READ_REVIEWS:
                return [dict(run_id=rid, review_verdict=v) for rid, vals in reviews.items() for v in vals]
            self.assertTrue(sql.startswith('SELECT COALESCE(MAX(id),0)'))
            return [dict(max_id=7, observed='2030-01-01T08:10:00+08:00')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'output').mkdir()
            module = ModuleType('run_243_lis_batch')
            module.runtime_profile = lambda: (None, SimpleNamespace(sql_database='synthetic'), SimpleNamespace(connect=lambda *a, **k: Connection()))
            old_path = sys.path[:]
            old_cwd = Path.cwd()
            stream = io.StringIO()
            try:
                with patch.object(eda, 'ROOT', root), patch.object(eda, 'load_batch', return_value=state), \
                     patch.object(eda, 'query', side_effect=read), patch.dict(sys.modules, {'run_243_lis_batch': module}), \
                     patch.object(sys, 'argv', ['eda_249.py', '--no-fees']), redirect_stdout(stream):
                    eda.main()
            finally:
                import os
                os.chdir(old_cwd)
                sys.path[:] = old_path
            self.assertIn('EDA_REPORT=PASS', stream.getvalue())
            self.assertNotIn('TEST_HOME_', stream.getvalue())
            exported = list((root / 'output').glob('eda-*/report.json'))
            self.assertEqual(len(exported), 1)
            report = json.loads(exported[0].read_text())
            self.assertEqual(report['scopes'][1]['violation_hits'], 3)
            self.assertIsNone(report['scopes'][1]['violation_patient_current_settlement_yuan'])


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--demo':
        os_path = Path(sys.argv[2])
        os_path.mkdir(mode=0o700, parents=True, exist_ok=False)
        eda.write_report(os_path, eda.build_report(*fixture()))
        print('DEMO_ONLY=' + str(os_path))
    else:
        unittest.main()
