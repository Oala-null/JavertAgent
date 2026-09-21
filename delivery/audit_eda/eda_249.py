#!/usr/bin/env python3
"""249手工下班统计；数据库只读，无病历/患者明细导出。"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path('/home/admin2/Javert')
VERSION = '249-eda-v1'
VERDICTS = ('VIOLATION', 'INCONCLUSIVE', 'CLEAN')
NOTES = [
    '人数按首页SYXH去重；同一自然人多次住院可能计为多个首页。',
    '命中次数=患者×规则的最新VIOLATION结果数，不是收费明细条数。机器判定需人工复核。',
    '工作台全量包含所选批次和既有存量；两个范围不能相加。各类别患者人数也不可相加。',
    '同患者同规则取最新created_at；时间并列时用id倒序稳定选取，工作台原排序未规定并列顺序。',
    '类别优先取审计时rule_yaml_snapshot；缺失才用当前规则并计入元数据回退数量。',
    '违规金额未结构化记录，显示待核定而非0；不从LLM叙述或工具文本猜金额。',
    '命中患者当前DETAIL结算净费用涵盖整次住院，已按共享映射计退费；不是违规额、医保损失或历史审计快照金额。',
    '规则累计耗时是duration_ms之和；并发时不能当墙钟用时。最新结果耗时与含历史重跑的计算开销分别列出。',
    '批次自然跨度含等待/暂停；逐人已完成处理耗时含取数、校验、审计和同步，但不含两次恢复之间的暂停。',
    '进度、SQL结果和费用分别读取，不是跨源事务快照；结果水位之后的新落库数据需下次再统计。',
    '本工具只读结果及取费，不重跑审计，不调用LLM，不修改数据库或运行代码。',
]

# 同一水位，同一SELECT同时取全量最新与所选批次内最新，避免用Python复制数据库排序规则。
RANKED = """
WITH ranked AS (
 SELECT id, run_id, patient_id, rule_id, verdict, batch_tag, duration_ms,
        started_at, created_at, rule_yaml_snapshot,
        ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC, id DESC) AS rn_all,
        ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id, batch_tag ORDER BY created_at DESC, id DESC) AS rn_batch,
        COUNT(*) OVER (PARTITION BY patient_id, rule_id, created_at) AS ties_all,
        COUNT(*) OVER (PARTITION BY patient_id, rule_id, batch_tag, created_at) AS ties_batch
 FROM dbo.javert_audit_runs WHERE id <= ?
)
"""
CHOSEN = "(rn_all=1 OR (batch_tag=? AND rn_batch=1))"
READ_RESULTS = RANKED + 'SELECT * FROM ranked WHERE ' + CHOSEN
READ_REVIEWS = RANKED + """
SELECT DISTINCT v.run_id, v.review_verdict
FROM dbo.javert_vio_review v JOIN ranked r ON r.run_id=v.run_id
WHERE v.is_latest=1 AND """ + CHOSEN
READ_HISTORY = """
SELECT batch_tag, COUNT_BIG(*) AS run_count, COUNT(DISTINCT patient_id) AS patients,
       SUM(CASE WHEN duration_ms >= 0 THEN CONVERT(bigint,duration_ms) ELSE 0 END) AS duration_ms,
       SUM(CASE WHEN duration_ms IS NULL OR duration_ms < 0 THEN CONVERT(bigint,1) ELSE 0 END) AS missing_duration,
       MIN(created_at) AS first_record, MAX(created_at) AS last_record
FROM dbo.javert_audit_runs WHERE id <= ? GROUP BY batch_tag
"""


def query(cn, sql, params=()):
    with closing(cn.cursor()) as cur:
        cur.execute(sql, params)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def load_batch(value, root=ROOT):
    parent = root / 'output'
    if value == 'none':
        return None
    paths = list(parent.glob('lis-batch.*/summary.json')) if value == 'latest' else [Path(value) / 'summary.json']
    found = []
    for p in paths:
        if p.is_symlink() or p.parent.is_symlink() or p.parent.resolve().parent != parent.resolve():
            raise RuntimeError('BATCH_PATH_INVALID')
        if not p.parent.name.startswith('lis-batch.'):
            raise RuntimeError('BATCH_PATH_INVALID')
        state = json.loads(p.read_text(encoding='utf-8'))
        if state.get('version') != 1 or not re.fullmatch(r'st-lis-[A-Za-z0-9-]{1,13}', state.get('tag', '')):
            raise RuntimeError('BATCH_SUMMARY_INVALID')
        found.append(state)
    if not found:
        raise RuntimeError('NO_LIS_BATCH_USE_--batch_none_FOR_STOCK_ONLY')
    # 报表latest指创建时间最新一批，含已完成批次；与批跑resume latest的未完成批次选择不同。
    return max(found, key=lambda s: (s['created_at'], s['tag']))


def iso(value):
    return value.isoformat(sep=' ', timespec='seconds') if isinstance(value, datetime) else value


def seconds(start, end):
    try:
        a, b = datetime.fromisoformat(start), datetime.fromisoformat(end)
        return round(max(0, (b - a).total_seconds()), 3)
    except (ValueError, TypeError):
        return None


def batch_progress(state, observed):
    if state is None:
        return None
    rows = state['patients']
    statuses = Counter(r['status'] for r in rows)
    reasons = Counter(r['reason'] for r in rows if r.get('reason'))
    durations = [seconds(r.get('started_at'), r.get('finished_at')) for r in rows if r.get('finished_at')]
    durations = [d for d in durations if d is not None]
    passed_times = [seconds(r.get('started_at'), r.get('finished_at')) for r in rows if r['status'] == 'PASSED']
    passed_times = [d for d in passed_times if d is not None]
    ends = [r['finished_at'] for r in rows if r.get('finished_at')]
    end = observed if state.get('phase') == 'running' else max(ends, default=state['created_at'])
    return dict(tag=state['tag'], phase=state['phase'], finished=state['finished'],
                candidates=len(rows), status=dict(statuses), reasons=dict(reasons),
                observed_at=observed, created_at=state['created_at'], span_seconds=seconds(state['created_at'], end),
                completed_patient_processing_seconds=round(sum(durations), 3),
                timed_patients=len(durations),
                passed_patient_average_seconds=round(sum(passed_times) / len(passed_times), 3) if passed_times else None,
                passed_with_labs=sum(r['status'] == 'PASSED' and r.get('lab_rows', 0) > 0 for r in rows))


@lru_cache(maxsize=256)
def parse_rule(snapshot):
    import yaml
    try:
        data = yaml.safe_load(snapshot or '')
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def add_metadata(rows, root=ROOT):
    for row in rows:
        data = parse_rule(row.get('rule_yaml_snapshot'))
        fallback = False
        if not isinstance(data.get('violation_type'), str) or not data['violation_type'].strip():
            fallback = True
            rid = row['rule_id']
            path = root / 'configs/rules' / (rid + '.yaml') if re.fullmatch(r'R(?:D)?\d{2,3}', rid) else None
            data = parse_rule(path.read_text(encoding='utf-8')) if path and path.is_file() else {}
        row['category'] = str(data.get('violation_type') or '未分类')[:100]
        row['domain'] = str(data.get('domain') or '未分类')[:100]
        row['metadata_fallback'] = fallback
        row.pop('rule_yaml_snapshot', None)


def net_fees(frame):
    """符号与重复行校验由hub_source完成；这里只求和，遇到非法数绝不当0。"""
    if frame.empty or 'det_item_fee_sumamt' not in frame:
        raise ValueError('FEE_EMPTY')
    total = Decimal('0')
    for raw in frame['det_item_fee_sumamt']:
        n = Decimal(str(raw))
        if not n.is_finite():
            raise ValueError('FEE_NUMERIC_INVALID')
        total += n
    return total.quantize(Decimal('.01'))


def collect_fees(pids, cfg, hs, enabled):
    fees, errors = {}, {}
    if not enabled:
        return fees, {pid: 'NOT_REQUESTED' for pid in pids}
    with closing(hs.connect(cfg, timeout=15)) as cn:
        cn.autocommit = True
        cn.timeout = 60
        cn.execute('SET LOCK_TIMEOUT 10000')
        for index, pid in enumerate(sorted(pids), 1):
            try:
                patient = hs.resolve_patient(cn, pid, cfg.hub_hospital_code)
                frame = hs.fetch_fees(cn, None, {}, patient=patient)
                fees[pid] = net_fees(frame)
            except hs.HospitalLinkageError as exc:
                code = str(exc)
                errors[pid] = code if re.fullmatch(r'[A-Z_0-9]+', code) else 'FEE_LINKAGE_FAILED'
            except (ValueError, InvalidOperation):
                errors[pid] = 'FEE_EMPTY_OR_NUMERIC_INVALID'
            except Exception:
                # 连接/驱动失败不继续逐人重试，避免一次故障重复等待。
                for unprocessed in pids - fees.keys() - errors.keys():
                    errors[unprocessed] = 'FEE_QUERY_FAILED'
                break
            if index == 1 or index % 25 == 0 or index == len(pids):
                print(f'费用只读核对：{index}/{len(pids)}；成功={len(fees)}；未取到={len(errors)}', flush=True)
    return fees, errors


def measure(rows, fees, fee_errors, reviews, reviews_available=True):
    patients = {r['patient_id'] for r in rows}
    vrows = [r for r in rows if r['verdict'] == 'VIOLATION']
    vpids = {r['patient_id'] for r in vrows}
    covered = vpids & fees.keys()
    valid_times = [r['duration_ms'] for r in rows if r.get('duration_ms') is not None and r['duration_ms'] >= 0]
    consensus = [r for r in vrows if reviews.get(r['run_id']) == {'V'}]
    return {
        'patients': len(patients), 'results': len(rows),
        'violation_hits': len(vrows), 'violation_patients': len(vpids),
        'violation_patient_rate_percent': round(len(vpids) * 100 / len(patients), 2) if patients else None,
        'inconclusive_hits': sum(r['verdict'] == 'INCONCLUSIVE' for r in rows),
        'clean_hits': sum(r['verdict'] == 'CLEAN' for r in rows),
        'unknown_verdict_hits': sum(r['verdict'] not in VERDICTS for r in rows),
        'rule_seconds': round(sum(valid_times) / 1000, 3),
        'missing_duration_results': len(rows) - len(valid_times),
        'metadata_fallback_results': sum(bool(r.get('metadata_fallback')) for r in rows),
        'violation_amount_yuan': None,
        'violation_patient_current_settlement_yuan': str(sum((fees[p] for p in covered), Decimal('0')).quantize(Decimal('.01'))) if covered else None,
        'fee_covered_patients': len(covered), 'fee_unavailable_patients': len(vpids - covered),
        'fee_unavailable_reasons': dict(Counter(fee_errors.get(p, 'UNKNOWN') for p in vpids - covered)),
        'review_available': reviews_available,
        'expert_unanimous_v_hits': len(consensus) if reviews_available else None,
        'expert_unanimous_v_patients': len({r['patient_id'] for r in consensus}) if reviews_available else None,
        'expert_conflict_hits': sum(len(reviews.get(r['run_id'], set())) > 1 for r in vrows) if reviews_available else None,
        'machine_v_without_review_hits': sum(not reviews.get(r['run_id']) for r in vrows) if reviews_available else None,
    }


def build_scope(name, rows, fees, errors, reviews, review_ok):
    out = dict(scope=name, **measure(rows, fees, errors, reviews, review_ok))
    out['timestamp_tie_results'] = sum(r.get('ties_all' if name == '工作台全量（含存量和本批）' else 'ties_batch', 1) > 1 for r in rows)
    categories, rules = defaultdict(list), defaultdict(list)
    for r in rows:
        categories[r['category']].append(r)
        rules[(r['rule_id'], r['domain'], r['category'])].append(r)
    out['categories'] = [dict(category=k, **measure(v, {}, {}, reviews, review_ok)) for k, v in sorted(categories.items())]
    out['rules'] = [dict(rule_id=k[0], domain=k[1], category=k[2], **measure(v, {}, {}, reviews, review_ok)) for k, v in sorted(rules.items())]
    for group in out['categories'] + out['rules']:
        # 逐类别整次住院费会重叠，费用只在总览按唯一患者给出，避免被误相加。
        for key in list(group):
            if key.startswith('fee_') or key == 'violation_patient_current_settlement_yuan':
                group.pop(key)
    return out


def build_report(rows, history, reviews, state, fees, fee_errors, metadata, review_ok=True):
    all_rows = [r for r in rows if r['rn_all'] == 1]
    scopes = [build_scope('工作台全量（含存量和本批）', all_rows, fees, fee_errors, reviews, review_ok)]
    if state:
        chosen = [r for r in rows if r.get('batch_tag') == state['tag'] and r['rn_batch'] == 1]
        scopes.insert(0, build_scope('所选LIS批次', chosen, fees, fee_errors, reviews, review_ok))
    for scope in scopes:
        hist = history if scope['scope'].startswith('工作台') else [h for h in history if h.get('batch_tag') == state['tag']]
        scope['all_attempts'] = sum(h['run_count'] for h in hist)
        scope['all_attempt_rule_seconds'] = round(sum(h['duration_ms'] or 0 for h in hist) / 1000, 3)
        scope['all_attempt_missing_duration'] = sum(h['missing_duration'] for h in hist)
    return dict(version=VERSION, metadata=metadata, notes=NOTES,
                batch=batch_progress(state, metadata['progress_observed_at']), scopes=scopes,
                historical_batches=[{k: iso(v) for k, v in h.items()} for h in history])


def human_time(value):
    if value is None:
        return '未知'
    return f'{value / 3600:.2f} 小时（{value:.1f} 秒）'


def report_text(report):
    meta = report['metadata']
    lines = ['Javert 审计统计' + ('【合成演示，不是院内结果】' if meta.get('demo') else ''),
             f"统计开始：{meta['started_at']}；生成完成：{meta['finished_at']}",
             f"结果ID水位：{meta['result_max_id']}；SQL服务器读取时间：{meta.get('sql_observed_at', '演示')}",
             f"费用读取区间：{meta.get('fees_started_at')} 至 {meta.get('fees_finished_at')}", '']
    if report['batch']:
        b = report['batch']
        lines += [f"批次：{b['tag']}；阶段：{b['phase']}；名单处理结束：{b['finished']}",
                  f"候选首页：{b['candidates']}；状态：{json.dumps(b['status'], ensure_ascii=False)}",
                  f"跳过/失败原因：{json.dumps(b['reasons'], ensure_ascii=False)}",
                  f"成功完成且快照有化验：{b['passed_with_labs']} 人",
                  '批次自然跨度（含暂停）：' + human_time(b['span_seconds']),
                  f"逐人已完成处理累计（{b['timed_patients']} 人，含跳过）：" + human_time(b['completed_patient_processing_seconds']),
                  '成功审计患者平均处理耗时：' + human_time(b['passed_patient_average_seconds']), '']
    for s in report['scopes']:
        money = s['violation_patient_current_settlement_yuan']
        rate = '不可计' if s['violation_patient_rate_percent'] is None else str(s['violation_patient_rate_percent']) + '%'
        review_line = (f"机器违规中，专家一致标V：{s['expert_unanimous_v_hits']} 次；专家分歧：{s['expert_conflict_hits']} 次；未复核：{s['machine_v_without_review_hits']} 次"
                       if s['review_available'] else '专家复核：未知（复核表本次不可读）')
        lines += [s['scope'],
                  f"有落库结果患者：{s['patients']}；最新患者×规则结果：{s['results']}",
                  f"机器违规命中：{s['violation_hits']} 次；涉及患者：{s['violation_patients']}；患者命中率：{rate}",
                  f"待核查：{s['inconclusive_hits']} 次；未发现违规：{s['clean_hits']} 次；其他状态：{s['unknown_verdict_hits']}",
                  review_line,
                  '违规金额：待核定（没有结构化金额，不能从整次住院费推算）',
                  f"命中患者当前DETAIL结算净费用参考：{money if money is not None else '不可计'} 元；覆盖 {s['fee_covered_patients']}/{s['violation_patients']} 人",
                  f"费用未取到原因：{json.dumps(s['fee_unavailable_reasons'], ensure_ascii=False)}",
                  '当前保留结果的规则耗时合计：' + human_time(s['rule_seconds']),
                  f"所有历史执行（含重跑）{s['all_attempts']} 条，规则耗时累计：" + human_time(s['all_attempt_rule_seconds']),
                  f"当前/历史缺耗时记录：{s['missing_duration_results']}/{s['all_attempt_missing_duration']}；类别元数据回退：{s['metadata_fallback_results']}；最新时间并列：{s['timestamp_tie_results']}",
                  '违规类别（类别｜命中次数｜涉及患者）：']
        cats = sorted((c for c in s['categories'] if c['violation_hits']), key=lambda c: (-c['violation_hits'], c['category']))
        lines += [f"  {c['category']}｜{c['violation_hits']}｜{c['violation_patients']}" for c in cats] or ['  当前无VIOLATION']
        lines.append('')
    lines += ['统计口径：'] + [f'{i}. {n}' for i, n in enumerate(report['notes'], 1)]
    return '\n'.join(lines) + '\n'


def csv_safe(value):
    if value is None:
        return '待核定'
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def write_csv(path, columns, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow([label for key, label in columns])
        writer.writerows([[csv_safe(row.get(key)) for key, label in columns] for row in rows])
    path.chmod(0o600)


def write_report(folder, report):
    folder.chmod(0o700)
    (folder / 'report.txt').write_text(report_text(report), encoding='utf-8-sig')
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    common = [('patients', '有结果患者数'), ('results', '最新结果数'), ('violation_hits', '违规命中次数'),
              ('violation_patients', '涉及患者数'), ('inconclusive_hits', '待核查次数'), ('clean_hits', '未发现违规次数'),
              ('rule_seconds', '当前结果规则累计秒'), ('violation_amount_yuan', '违规金额_待核定'),
              ('expert_unanimous_v_hits', '机器违规中专家一致V次数')]
    write_csv(folder / 'overview.csv', [('scope', '统计范围')] + common + [
        ('violation_patient_current_settlement_yuan', '命中患者整次住院结算净费用_非违规额'),
        ('fee_covered_patients', '费用取数成功人数'), ('fee_unavailable_patients', '费用不可计人数'),
        ('all_attempt_rule_seconds', '含历史重跑规则累计秒')], report['scopes'])
    for key, fields in [('categories', [('category', '违规类别')]), ('rules', [('rule_id', '规则'), ('domain', '领域'), ('category', '违规类别')])]:
        data = [dict(scope=s['scope'], **row) for s in report['scopes'] for row in s[key]]
        write_csv(folder / (key + '.csv'), [('scope', '统计范围')] + fields + common, data)
    write_csv(folder / 'historical_batches.csv', [('batch_tag', '历史批次_空值为无标签'), ('run_count', '原始执行数_含重跑'),
              ('patients', '曾执行患者数'), ('duration_ms', '规则累计毫秒'), ('missing_duration', '耗时缺失数'),
              ('first_record', '最早落库时间'), ('last_record', '最晚落库时间')],
              [dict(h, batch_tag=h.get('batch_tag') or '无标签') for h in report['historical_batches']])
    for p in folder.iterdir():
        p.chmod(0o600)
    (folder / 'REPORT_COMPLETE').write_text(VERSION + '\n', encoding='ascii')
    (folder / 'REPORT_COMPLETE').chmod(0o600)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batch', default='latest', help='创建时间最新LIS批次，或明确批次目录；none仅统计工作台全量')
    parser.add_argument('--no-fees', action='store_true', help='快速统计，不读取DETAIL费用')
    args = parser.parse_args()
    started = datetime.now().astimezone().isoformat(timespec='seconds')
    if not ROOT.is_dir():
        raise RuntimeError('RUN_ON_249_REQUIRED')
    os.chdir(ROOT)
    sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]
    logging.disable(logging.CRITICAL)
    from run_243_lis_batch import runtime_profile
    _, cfg, hs = runtime_profile()
    state = load_batch(args.batch)
    progress_at = datetime.now().isoformat(timespec='seconds')
    tag = state['tag'] if state else '__no_batch__'
    print('只读统计开始；本批=' + (tag if state else '未选择') + '；同时包含工作台全量存量。', flush=True)
    reviews, review_ok = defaultdict(set), True
    with closing(hs.connect(cfg, database=cfg.sql_database, timeout=15)) as cn:
        cn.autocommit = True
        cn.timeout = 120
        cn.execute('SET LOCK_TIMEOUT 10000')
        boundary = query(cn, 'SELECT COALESCE(MAX(id),0) AS max_id, CONVERT(varchar(40),SYSDATETIMEOFFSET(),127) AS observed FROM dbo.javert_audit_runs')[0]
        hi = boundary['max_id']
        rows = query(cn, READ_RESULTS, (hi, tag))
        history = query(cn, READ_HISTORY, (hi,))
        try:
            for row in query(cn, READ_REVIEWS, (hi, tag)):
                reviews[row['run_id']].add(row['review_verdict'])
        except Exception:
            review_ok = False
    add_metadata(rows)
    print(f'结果读取完成；当前保留结果集合={len(rows)} 条；正在核对命中患者费用。', flush=True)
    fee_start = datetime.now().astimezone().isoformat(timespec='seconds')
    pids = {r['patient_id'] for r in rows if r['verdict'] == 'VIOLATION'}
    try:
        fees, errors = collect_fees(pids, cfg, hs, not args.no_fees)
    except Exception:
        fees, errors = {}, {p: 'FEE_CONNECTION_FAILED' for p in pids}
    finished = datetime.now().astimezone().isoformat(timespec='seconds')
    metadata = dict(started_at=started, finished_at=finished, progress_observed_at=progress_at,
                    result_max_id=hi, sql_observed_at=str(boundary['observed']),
                    fees_started_at=fee_start, fees_finished_at=finished, fees_requested=not args.no_fees,
                    review_available=review_ok)
    report = build_report(rows, history, reviews, state, fees, errors, metadata, review_ok)
    folder = Path(tempfile.mkdtemp(prefix='eda-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.', dir=ROOT / 'output'))
    try:
        write_report(folder, report)
    except BaseException:
        shutil.rmtree(folder)
        raise
    print(report_text(report), flush=True)
    print('EDA_REPORT=PASS\n统计目录：' + str(folder), flush=True)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('已取消统计，未停止审计任务。') from None
    except Exception as exc:
        code = str(exc) if isinstance(exc, RuntimeError) and re.fullmatch(r'[A-Z0-9_ -]+', str(exc)) else type(exc).__name__
        raise SystemExit('EDA_REPORT=FAILED；' + code + '；未修改数据库。') from None
