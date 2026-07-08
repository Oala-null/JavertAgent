# -*- coding: utf-8 -*-
"""boost-llm-efficiency 对照跑 (tasks 1.4 / 5.3) — 不落库, 记 轮数/工具调用/时长/裁决.

复刻 `audit-patient --priority all --use-router` 的规则选择, 每规则独立 Runner
(emit 钩子数 "[LLM " 行 = 该规则 LLM 往返轮数), 共享 ToolExecutor 缓存并发跑.
结果写 <out>/<patient>.json; **不写 sqlite** — baseline (改前批次) 保持干净, 事后
用 sqlite 里各规则 latest verdict 对照 V 级差异.

用法:
  uv run python scripts/compare_llm_efficiency.py --patients J66252,J18906,J90508
  # szx (hub 反向取数患者) 需切数据目录:
  JAVERT_DATA_DIR=data_import_hub JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv \
  JAVERT_LABS_FILE=lab_results.csv JAVERT_EXAMINATIONS_FILE=examinations.csv \
  JAVERT_SQL_ENABLED=false \
  uv run python scripts/compare_llm_efficiency.py --patients 211530148
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from javert.audit.rule_loader import load_all
from javert.audit.runner import Runner
from javert.commands.audit_patient import _apply_router_prefilter, _resolve_selection
from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.tools.registry import build_executor


def run_patient(patient_id: str, concurrency: int, out_dir: Path) -> dict:
    cfg = get_config()
    all_rules = load_all(cfg.rules_path)
    selected, label, _ = _resolve_selection(all_rules, "all", None)
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    selected, label, _decision = _apply_router_prefilter(
        selected, patient_id, loader, "all", label
    )
    print(f"=== {patient_id}: {label}", flush=True)
    if not selected:
        print(f"=== {patient_id}: router 全 prune, 无可跑规则", flush=True)
        return {"patient_id": patient_id, "n_rules": 0, "rows": []}

    executor = build_executor(loader, cfg)
    executor.reset_cache()
    executor.set_patient_context(patient_id)

    def one(rule) -> dict:
        n_llm = {"n": 0}

        def emit(msg: str) -> None:
            if msg.startswith("[LLM "):
                n_llm["n"] += 1

        # 每规则独立 Runner (轮数归属清晰), executor 共享 (跨规则工具缓存)
        runner = Runner(executor=executor, config=cfg, emit=emit, loader=loader)
        t0 = time.perf_counter()
        try:
            res = runner.audit(
                rule, patient_id, reset_cache=False, manage_patient_context=False
            )
            return {
                "rule_id": rule.rule_id,
                "verdict": res.verdict,
                "confidence": res.confidence,
                "gate_tag": res.gate_tag,
                "rounds": n_llm["n"],
                "tool_calls": len(res.tool_calls),
                "cached_tool_calls": sum(1 for t in res.tool_calls if t.cached),
                "duration_ms": res.duration_ms,
            }
        except Exception as exc:  # noqa: BLE001 — 单规则失败不中断
            return {
                "rule_id": rule.rule_id,
                "verdict": "FAILED",
                "error": str(exc)[:200],
                "rounds": n_llm["n"],
                "duration_ms": int((time.perf_counter() - t0) * 1000),
            }

    rows: list[dict] = []
    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(one, r) for r in selected]
        for i, fut in enumerate(as_completed(futs), 1):
            row = fut.result()
            rows.append(row)
            print(
                f"  [{i}/{len(selected)}] {patient_id} {row['rule_id']}: "
                f"{row.get('verdict')} rounds={row.get('rounds')} "
                f"tc={row.get('tool_calls', '-')} {row.get('duration_ms', 0) / 1000:.1f}s",
                flush=True,
            )
    total_s = time.perf_counter() - t_start
    executor.clear_patient_context()

    rows.sort(key=lambda r: r["rule_id"])
    ok = [r for r in rows if r.get("verdict") != "FAILED"]
    summary = {
        "patient_id": patient_id,
        "selection": label,
        "n_rules": len(selected),
        "total_seconds": round(total_s, 1),
        "verdicts": {
            v: sum(1 for r in ok if r["verdict"] == v)
            for v in ("VIOLATION", "INCONCLUSIVE", "CLEAN")
        },
        "failed": [r["rule_id"] for r in rows if r.get("verdict") == "FAILED"],
        "avg_rounds": round(sum(r["rounds"] for r in ok) / len(ok), 2) if ok else 0,
        "avg_tool_calls": round(sum(r["tool_calls"] for r in ok) / len(ok), 2) if ok else 0,
        "rows": rows,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{patient_id}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"=== {patient_id} 完成: {summary['verdicts']} failed={summary['failed']} "
        f"avg_rounds={summary['avg_rounds']} avg_tc={summary['avg_tool_calls']} "
        f"{total_s:.0f}s → {out_path}",
        flush=True,
    )
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description="boost-llm-efficiency 对照跑 (不落库)")
    ap.add_argument("--patients", required=True, help="逗号分隔患者号")
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--out", default="output/boost_llm_eff")
    args = ap.parse_args()
    out_dir = Path(args.out)
    for pid in [p.strip() for p in args.patients.split(",") if p.strip()]:
        run_patient(pid, args.concurrency, out_dir)


if __name__ == "__main__":
    main()
