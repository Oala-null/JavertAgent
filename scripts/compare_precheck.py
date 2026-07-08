# -*- coding: utf-8 -*-
"""compare_precheck — precheck ON vs OFF 对照 (pilot-deterministic-precheck 任务 5.3).

同一份当前代码, 只切 `config.precheck` on/off, 对 M1 (带 precheck 的 21 条) × N 患者
各跑一遍, 度量三指标:
  1. V/I/C 分布对照 + 逐 (患者,规则) verdict diff → **0 漏检硬门槛** (OFF=V 而 ON=CLEAN 短路 = MISS)
  2. LLM 调用数降幅 (ON 总 chat 次数 / OFF 总 chat 次数)
  3. ON facts 路径判 V 的 evidence 机器锚点覆盖率 (source 含 fee)

本地 sqlite-only (不写 142). 增量写 JSONL, 便于跑中读早期结果; 结束写 markdown 报告.

用法:
    JAVERT_SQL_ENABLED=false uv run python scripts/compare_precheck.py \
        --roster <file> --out <dir> [--concurrency 5] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from javert.audit.rule_loader import load_all
from javert.audit.runner import Runner
from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.tools.llm_provider import Qwen35Provider
from javert.tools.registry import build_executor


class CountingProvider:
    """包一层, 统计 chat_with_retry 调用次数 (线程安全)."""

    def __init__(self, inner: Qwen35Provider):
        self.inner = inner
        self.model_name = inner.model_name
        self._lock = threading.Lock()
        self.calls = 0

    def reset(self) -> None:
        with self._lock:
            self.calls = 0

    def chat_with_retry(self, messages, **kw):
        with self._lock:
            self.calls += 1
        return self.inner.chat_with_retry(messages, **kw)


def _has_fee_anchor(result) -> bool:
    return any("fee" in (e.source or "").lower() for e in result.evidence)


def _run_condition(runner, provider, rules, pid, concurrency) -> tuple[dict, int]:
    """跑一个 patient 的全部 rules (一个 precheck 条件); 返回 ({rule_id: rowdict}, llm_calls)."""
    provider.reset()
    runner.executor.reset_cache()
    runner.executor.set_patient_context(pid)
    out: dict[str, dict] = {}
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            fut = {
                pool.submit(
                    runner.audit, rule, pid,
                    reset_cache=False, manage_patient_context=False,
                ): rule for rule in rules
            }
            for f in as_completed(fut):
                rule = fut[f]
                try:
                    r = f.result()
                    out[rule.rule_id] = {
                        "verdict": r.verdict,
                        "conf": round(r.confidence, 2),
                        "precheck_tag": r.precheck_tag,
                        "gate_tag": r.gate_tag,
                        "n_tools": len(r.tool_calls),
                        "fee_anchor": _has_fee_anchor(r),
                        "duration_ms": r.duration_ms,
                    }
                except Exception as exc:  # noqa: BLE001
                    out[rule.rule_id] = {"verdict": "ERROR", "error": str(exc)[:120]}
    finally:
        runner.executor.clear_patient_context()
    return out, provider.calls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "results.jsonl"

    patients = [p.strip() for p in Path(args.roster).read_text().splitlines() if p.strip()]
    if args.limit:
        patients = patients[: args.limit]

    cfg = get_config()
    rules = sorted(
        [r for r in load_all(cfg.rules_path).values()
         if getattr(r, "precheck", None) is not None and r.status == "ready"],
        key=lambda r: r.rule_id,
    )
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    executor = build_executor(loader, cfg)
    provider = CountingProvider(Qwen35Provider(cfg))
    runner = Runner(executor=executor, provider=provider, config=cfg, loader=loader)

    print(f"[compare] {len(patients)} 患者 × {len(rules)} M1(precheck) 规则, concurrency={args.concurrency}",
          flush=True)
    print(f"[compare] rules: {[r.rule_id for r in rules]}", flush=True)

    with open(jsonl, "w", encoding="utf-8") as fh:
        t0 = time.perf_counter()
        for i, pid in enumerate(patients, 1):
            runner.config.precheck = "on"
            on_rows, on_calls = _run_condition(runner, provider, rules, pid, args.concurrency)
            runner.config.precheck = "off"
            off_rows, off_calls = _run_condition(runner, provider, rules, pid, args.concurrency)

            rec = {"patient": pid, "on": on_rows, "off": off_rows,
                   "on_llm_calls": on_calls, "off_llm_calls": off_calls}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()

            # 逐患者进度行: 短路数 / facts数 / miss数
            sc = sum(1 for v in on_rows.values() if v.get("precheck_tag") in ("无A项", "无B项"))
            fac = sum(1 for v in on_rows.values() if v.get("precheck_tag") == "A∩B并存待核反证")
            miss = sum(1 for rid in on_rows
                       if off_rows.get(rid, {}).get("verdict") == "VIOLATION"
                       and on_rows[rid].get("verdict") == "CLEAN")
            elapsed = time.perf_counter() - t0
            print(f"[{i}/{len(patients)}] {pid}  短路{sc} facts{fac}  "
                  f"LLM on={on_calls} off={off_calls}  miss={miss}  ({elapsed:.0f}s)",
                  flush=True)

    print(f"[compare] done → {jsonl}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
