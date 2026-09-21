#!/usr/bin/env bash
# 结算口径500人批跑：新st-批次，逐人执行，复用保存配置；不续接旧FS批次。
set -euo pipefail
umask 077
N=500
MAX_CANDIDATES=1500
live='/home/admin2/Javert'
expected='8645a2deb1c4c99921a65a3ab01754275ec2ac4e'
test "$(head -1 "$live/DEPLOY_COMMIT")" = "$expected" || { echo '机上版本不同，停止。'; exit 1; }
shopt -s nullglob
helpers=()
for f in /home/admin2/releases/243-install-8645a2d.*/run_saved_config.py; do
    test -f "${f%/*}/install-receipt.json" && helpers+=("$f")
done
test "${#helpers[@]}" -eq 1 || { echo '有效安装配置不是唯一一份，请核对安装目录。'; exit 1; }
cd "$live"
mkdir -p output
exec 8>output/.249-batch.lock
flock -n 8 || { echo '已有同类批跑在运行，停止。'; exit 1; }
flock -n output/.243-patient.lock true || { echo '已有单患者审计在运行，停止。'; exit 1; }
batch_dir=$(mktemp -d "$live/output/batch.XXXXXX")
"$live/.venv/bin/python" -u - "${helpers[0]}" "$N" "$MAX_CANDIDATES" "$batch_dir" <<'PY'
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path


def audit_status(returncode, text):
    if returncode != 0 or '持久化失败' in text:
        return 'FAILED'
    summary = re.search(r'\((\d+) completed, (\d+) pending, (\d+) failed\)', text)
    if summary:
        done, pending, failed = map(int, summary.groups())
        sync = re.search(r'mssql_sync: (\d+)/(\d+) succeeded \((\d+) pending\)', text)
        if done > 0 and pending == failed == 0 and sync:
            synced, total, sync_pending = map(int, sync.groups())
            if synced == total == done and sync_pending == 0:
                return 'PASSED'
        return 'FAILED'
    if 'router 判定无可疑规则, 跳过 LLM 审计' in text:
        return 'ROUTER_SKIPPED'
    return 'FAILED'


def main():
    helper = Path(sys.argv[1])
    n, limit = int(sys.argv[2]), int(sys.argv[3])
    folder = Path(sys.argv[4])
    if not 1 <= n <= limit <= 5000:
        raise SystemExit('人数须满足 1 <= N <= MAX_CANDIDATES <= 5000。')
    saved = json.loads(helper.with_name('effective-config.json').read_text())
    for key, value in saved.items():
        name = 'JAVERT_' + key.upper()
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = json.dumps(value) if isinstance(value, (bool, list, dict)) else str(value)
    os.environ['JAVERT_HUB_LINKAGE_MODE'] = 'shanghai'
    os.environ['JAVERT_HUB_HOSPITAL_CODE'] = 'AYY8BNRF'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    sys.path.insert(0, str(Path.cwd() / 'src'))
    from javert.config import get_config
    from javert.data import hub_source as hs
    if getattr(hs, 'SETTLEMENT_FEE_TABLE', None) != 'TB_HIS_ZY_FEE_DETAIL':
        raise SystemExit('结算补丁尚未安装，停止；不使用FS候选。')
    cfg = get_config()
    if not cfg.sql_enabled:
        raise SystemExit('当前配置未启用 SQL 双写，停止：这批结果不会自动出现在 SQL 工作台。')

    tag = 'st-' + datetime.now().strftime('%y%m%d%H%M') + '-' + folder.name.split('.')[-1]
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,20}', tag):
        raise SystemExit('批次 tag 格式不符。')
    started = time.monotonic()
    records = []
    finished = False
    phase = 'screening'

    def save_progress():
        summary = {'tag': tag, 'requested': n, 'finished': finished, 'phase': phase,
                   'fee_source': 'TB_HIS_ZY_FEE_DETAIL', 'fee_time_field': 'STFSJ',
                   'seconds': round(time.monotonic() - started, 1), 'patients': records}
        temporary = folder / 'summary.json.tmp'
        temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        temporary.replace(folder / 'summary.json')
        return summary

    (folder / 'tag.txt').write_text(tag + '\n')
    save_progress()
    print(f'批次 TAG={tag}\n批次目录：{folder}\n最多检查 {limit} 个候选，计划审计 {n} 人。')
    try:
        # 复用现场运行检查：只验证连接、模型别名和既有结果表，不执行 DDL。
        subprocess.run([sys.executable, str(helper), 'check'], check=True)
        selected = []
        seen = set()
        with closing(hs.connect(cfg, timeout=15)) as cn:
            cn.timeout = 60
            candidates = hs.q(cn, '''
                SELECT DISTINCT TOP (?) b.SYXH, b.CYRQ
                FROM dbo.TB_BA_SYJBK b
                JOIN dbo.TB_CIS_LEAVEHOSPITAL_SUMMARY s
                  ON s.YLJGYQDM=b.YLJGYQDM AND s.BAH=b.BAH AND s.KH=b.KH AND s.KLX=b.KLX
                WHERE b.YLJGYQDM=?
                  AND EXISTS (SELECT 1 FROM dbo.TB_HIS_ZY_FEE_DETAIL f
                              WHERE f.YLJGYQDM=s.YLJGYQDM AND f.JZLSH=s.JZLSH AND f.XGBZ='1')
                ORDER BY b.CYRQ DESC, b.SYXH
            ''', (limit, cfg.hub_hospital_code))
            for index, value in enumerate(candidates['SYXH'], 1):
                pid = str(value)
                if pid.upper() in seen:
                    continue
                if not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', pid):
                    raise RuntimeError('候选首页号格式不符')
                seen.add(pid.upper())
                try:
                    bundle = hs.fetch_hospital_bundle(cn, pid, cfg.hub_hospital_code)
                except hs.HospitalLinkageError as exc:
                    print(f'候选 {index} 未通过：{exc}')
                    continue
                selected.append(pid)
                (folder / 'patients.txt').write_text('\n'.join(selected) + '\n')
                print(f'候选 {index} 通过：已选 {len(selected)}/{n}；化验 {len(bundle["labs"])} 行，检查 {len(bundle["exams"])} 行。')
                if len(selected) == n:
                    break
        if len(selected) != n:
            raise SystemExit(f'本次候选中只有 {len(selected)} 人通过，未达到 {n} 人；尚未审计任何人。')
        (folder / 'patients.txt').write_text('\n'.join(selected) + '\n')
        (folder / 'tag.txt').write_text(tag + '\n')
        print('名单已固定。下面开始实际审计；每人几分钟，详细进度写入对应日志。')

        phase = 'auditing'
        for index, pid in enumerate(selected, 1):
            log = folder / f'patient-{index:03d}.log'
            record = {'index': index, 'status': 'STARTED', 'log': log.name}
            records.append(record)
            save_progress()
            begin = time.monotonic()
            print(f'[{index}/{n}] 开始，TAG={tag}；日志：{log}')
            try:
                with log.open('w') as stream:
                    result = subprocess.run([sys.executable, str(helper), 'patient', pid, tag],
                                            stdout=stream, stderr=subprocess.STDOUT)
                text = log.read_text(errors='replace')
                record['status'] = audit_status(result.returncode, text)
                labs = re.findall(r'^\s*labs:\s*(\d+) 行\s*$', text, re.M)
                record['labs'] = int(labs[-1]) if labs else None
            finally:
                record['seconds'] = round(time.monotonic() - begin, 1)
                save_progress()
            print(f'[{index}/{n}] {record["status"]}；化验={record.get("labs")} 行；耗时={record["seconds"]} 秒。')
            if record['status'] == 'FAILED':
                raise SystemExit(f'第 {index} 人未通过日志检查，后续患者尚未执行；先在院内查看该日志。')
            if record['status'] == 'ROUTER_SKIPPED':
                print('该患者 Router 无候选，未生成审计结果行；不计入有结果的患者数。')
        finished = True
        phase = 'completed'
    finally:
        summary = save_progress()
        counts = Counter(r['status'] for r in records)
        print(f'本批统计：日志检查通过 {counts["PASSED"]} 人，Router跳过 {counts["ROUTER_SKIPPED"]} 人，'
              f'失败 {counts["FAILED"]} 人，状态待核对 {counts["STARTED"]} 人；总耗时 {summary["seconds"]} 秒。')
        print(f'批次 TAG={tag}；进度保存在 {folder / "summary.json"}。')
    print('本批执行结束。工作台按该 TAG 找结果，并查看化验非空患者的「检验记录」。')
    print('patients.txt、逐人日志和审计快照仅保留院内；对外只反馈 TAG、状态和计数。')


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit('本批已中断；当前患者可能已写入部分结果，先核对 summary.json 和日志。')
    except Exception as exc:
        raise SystemExit(f'批跑停止：{type(exc).__name__}；未输出连接串或凭据。')
PY
