"""Batch runner v2 — 跑 data/batch_50_new.txt 里的 50 病人.

跟 run_batch_50patients.py 同结构, 但读不同的 list, 输出到 output/batch_new/.

跑法 (62 后台):
  cd ~/javert
  set -a && source .env && set +a
  nohup ~/.local/bin/uv run python scripts/run_batch_new.py \
       > output/batch_new/_main.log 2>&1 &
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST_FILE = ROOT / "data" / "batch_50_new.txt"
OUT_DIR = ROOT / "output" / "batch_new"
PROGRESS_FILE = OUT_DIR / "_progress.jsonl"
FAILED_FILE = OUT_DIR / "_failed.txt"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pids = [
        line.strip()
        for line in LIST_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    print(f"==== batch_new start, {len(pids)} pids ====", flush=True)

    PROGRESS_FILE.write_text("", encoding="utf-8")
    if FAILED_FILE.exists():
        FAILED_FILE.unlink()
    pf = open(PROGRESS_FILE, "a", encoding="utf-8", buffering=1)

    from javert.commands.audit_patient import run_audit_patient

    t_batch_start = time.perf_counter()
    summary: list[dict] = []
    for i, pid in enumerate(pids, 1):
        t0 = time.perf_counter()
        print(f"\n==== [{i}/{len(pids)}] start {pid} ====", flush=True)
        record = {
            "patient_id": pid,
            "idx": i,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            code = run_audit_patient(
                patient_id=pid,
                priority="all",
                rules_arg=None,
                share_tool_cache=True,
                concurrency=5,
                use_router=True,
            )
            elapsed = time.perf_counter() - t0
            record["status"] = "ok" if code == 0 else f"exit={code}"
            record["elapsed_s"] = round(elapsed, 1)
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            record["status"] = "error"
            record["error"] = str(exc)
            record["traceback"] = traceback.format_exc()[-1500:]
            record["elapsed_s"] = round(elapsed, 1)
            with open(FAILED_FILE, "a", encoding="utf-8") as f:
                f.write(f"{pid}\t{exc}\n")
            print(f"[ERR] {pid}: {exc}", file=sys.stderr, flush=True)

        record["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        pf.write(json.dumps(record, ensure_ascii=False) + "\n")
        summary.append(record)
        eta_min = (time.perf_counter() - t_batch_start) / i * (len(pids) - i) / 60
        print(
            f"==== [{i}/{len(pids)}] {pid} done ({record['status']}, "
            f"{record['elapsed_s']:.1f}s) | ETA剩余 ~{eta_min:.1f} min ====",
            flush=True,
        )

    pf.close()
    total_min = (time.perf_counter() - t_batch_start) / 60
    n_ok = sum(1 for r in summary if r["status"] == "ok")
    n_err = sum(1 for r in summary if r["status"] != "ok")
    print(
        f"\n==== batch_new done: {n_ok}/{len(pids)} ok, "
        f"{n_err} failed, total {total_min:.1f} min ====",
        flush=True,
    )
    (OUT_DIR / "_done.txt").write_text(
        f"completed at {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"ok={n_ok}, err={n_err}, total_min={total_min:.1f}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
