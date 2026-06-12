"""Batch runner — gate-v3 (麻醉/术前/肿瘤/缺文书确定性闸) 在全新病人上重跑.

读 data/batch_gate_v3_fresh.txt (从未跑过的手术+费用病人), --priority all --use-router,
输出 output/batch_gate_v3/. batch_tag 走 env JAVERT_BATCH_TAG=gate-v3-fresh.

单 patient 失败不中断 (写 _failed.txt + 继续). 每患者完一行 _progress.jsonl.

跑法 (62 后台, 用全量基础数据而非 with_szx):
  cd ~/javert && set -a && source .env && set +a
  export JAVERT_NOTES_FILE=case_notes.csv JAVERT_FEES_FILE=shi_fee.csv \
         JAVERT_ZD_FILE=shi_zd.xls JAVERT_SS_FILE=shi_ss.xls \
         JAVERT_BATCH_TAG=gate-v3-fresh
  nohup ~/.local/bin/uv run python scripts/run_batch_gate_v3.py \
       > output/batch_gate_v3/_main.log 2>&1 &
  disown
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST_FILE = ROOT / "data" / "batch_gate_v3_fresh.txt"
OUT_DIR = ROOT / "output" / "batch_gate_v3"
PROGRESS_FILE = OUT_DIR / "_progress.jsonl"
FAILED_FILE = OUT_DIR / "_failed.txt"

CONCURRENCY = 6


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pids = [
        line.strip()
        for line in LIST_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"==== batch_gate_v3 start, {len(pids)} pids, concurrency={CONCURRENCY} ====", flush=True)

    # 续跑: 已在 _progress.jsonl 出现的 pid 跳过
    done: set[str] = set()
    if PROGRESS_FILE.exists():
        for line in PROGRESS_FILE.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["patient_id"])
            except Exception:
                pass
    pf = open(PROGRESS_FILE, "a", encoding="utf-8", buffering=1)

    from javert.commands.audit_patient import run_audit_patient

    t_batch = time.perf_counter()
    for i, pid in enumerate(pids, 1):
        if pid in done:
            print(f"==== [{i}/{len(pids)}] skip {pid} (已完成) ====", flush=True)
            continue
        t0 = time.perf_counter()
        print(f"\n==== [{i}/{len(pids)}] start {pid} ====", flush=True)
        record = {"patient_id": pid, "idx": i, "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            code = run_audit_patient(
                patient_id=pid,
                priority="all",
                rules_arg=None,
                share_tool_cache=True,
                concurrency=CONCURRENCY,
                use_router=True,
            )
            record["exit_code"] = code
            record["ok"] = code == 0
        except Exception as exc:  # noqa: BLE001
            record["ok"] = False
            record["error"] = f"{type(exc).__name__}: {exc}"
            print(f"!!!! {pid} FAILED: {exc}\n{traceback.format_exc()}", flush=True)
            with open(FAILED_FILE, "a", encoding="utf-8") as ff:
                ff.write(pid + "\n")
        record["elapsed_s"] = round(time.perf_counter() - t0, 1)
        pf.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"==== [{i}/{len(pids)}] done {pid} in {record['elapsed_s']}s ====", flush=True)

    pf.close()
    print(f"\n==== batch_gate_v3 DONE in {round((time.perf_counter()-t_batch)/60,1)} min ====", flush=True)


if __name__ == "__main__":
    main()
