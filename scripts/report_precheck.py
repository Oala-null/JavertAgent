# -*- coding: utf-8 -*-
"""report_precheck — 汇总 compare_precheck.py 的 results.jsonl → markdown 报告.

可在跑中对部分结果运行 (增量 JSONL). 三指标: V/I/C 分布 / LLM 降幅 / 锚点覆盖 + 漏检清单.

用法: uv run python scripts/report_precheck.py --results output/precheck_cmp/results.jsonl [--out docs/precheck_compare_实测.md]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _load(path: Path) -> list[dict]:
    recs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            recs.append(json.loads(line))
    return recs


def build_report(recs: list[dict]) -> str:
    n_pat = len(recs)
    on_v = Counter()
    off_v = Counter()
    on_llm = off_llm = 0
    sc = fac = skip = 0
    misses: list[tuple[str, str, str]] = []          # (patient, rule, off_verdict)
    flips: list[tuple[str, str, str, str]] = []       # (patient, rule, on, off)
    facts_v = 0
    facts_v_anchored = 0
    errors = 0

    for r in recs:
        on_llm += r.get("on_llm_calls", 0)
        off_llm += r.get("off_llm_calls", 0)
        on, off = r.get("on", {}), r.get("off", {})
        for rid, ov in on.items():
            fv = off.get(rid, {})
            on_verdict = ov.get("verdict")
            off_verdict = fv.get("verdict")
            if on_verdict == "ERROR" or off_verdict == "ERROR":
                errors += 1
                continue
            on_v[on_verdict] += 1
            off_v[off_verdict] += 1
            tag = ov.get("precheck_tag", "")
            if tag in ("无A项", "无B项"):
                sc += 1
            elif tag == "A∩B并存待核反证":
                fac += 1
                if on_verdict == "VIOLATION":
                    facts_v += 1
                    if ov.get("fee_anchor"):
                        facts_v_anchored += 1
            else:
                skip += 1
            # 漏检: OFF 判 V 而 ON 短路成 CLEAN
            if off_verdict == "VIOLATION" and on_verdict == "CLEAN" and tag in ("无A项", "无B项"):
                misses.append((r["patient"], rid, off_verdict))
            if on_verdict != off_verdict:
                flips.append((r["patient"], rid, on_verdict, off_verdict))

    total_rules = sc + fac + skip
    reduction = (1 - on_llm / off_llm) * 100 if off_llm else 0.0
    anchor_cov = (facts_v_anchored / facts_v * 100) if facts_v else 100.0

    L = []
    L.append("# precheck ON vs OFF 对照实测 (pilot-deterministic-precheck 任务 5.3)\n")
    L.append(f"- 患者数: **{n_pat}** · M1(precheck) 规则: 21 · 判定单元: {total_rules}\n")
    L.append("## 1. LLM 调用降幅\n")
    L.append(f"- ON 总 chat 次数: **{on_llm}** · OFF 总 chat 次数: **{off_llm}**")
    L.append(f"- **LLM 调用降幅: {reduction:.1f}%** (目标 ≥50%)")
    L.append(f"- 短路 CLEAN (0 LLM): {sc}/{total_rules} ({sc/total_rules*100:.1f}%) · "
             f"facts→LLM: {fac} · skip(数据缺): {skip}\n")
    L.append("## 2. V/I/C 分布对照 + 0 漏检\n")
    L.append("| verdict | ON | OFF |")
    L.append("|---|---|---|")
    for v in ("VIOLATION", "INCONCLUSIVE", "CLEAN"):
        L.append(f"| {v} | {on_v.get(v,0)} | {off_v.get(v,0)} |")
    L.append("")
    L.append(f"- **短路漏检 (OFF=V 而 ON 短路 CLEAN): {len(misses)}** {'✅ 0 漏检' if not misses else '⚠️'}")
    if misses:
        for p, rid, _ in misses[:30]:
            L.append(f"  - MISS {p} / {rid}")
    L.append(f"- verdict 翻转总数 (含 facts 路径 LLM 噪声): {len(flips)}")
    # 翻转分类
    fc = Counter((a, b) for _, _, a, b in flips)
    for (a, b), c in fc.most_common():
        L.append(f"  - {a[:1]}→{b[:1]}: {c}")
    L.append("")
    L.append("## 3. 新 V 证据机器锚点覆盖\n")
    L.append(f"- ON facts 路径判 V: {facts_v} · 带 fee 锚点: {facts_v_anchored} · "
             f"**覆盖率 {anchor_cov:.1f}%** (目标 100%)")
    if errors:
        L.append(f"\n> ⚠️ ERROR 判定单元: {errors} (未计入分布)")
    L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    recs = _load(Path(args.results))
    report = build_report(recs)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"\n→ 写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
