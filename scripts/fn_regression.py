#!/usr/bin/env python3
"""FN 回归案例库 runner — 逐例真 LLM dry-run, 三档 catch 率报告 + 退化拦截.

召回侧度量: catch 率 = (full + partial) / 总数. 单向压假阳性的机制此前召回零度量,
本 runner 把"减少假阴性"变成可复跑数字. 案例登记在 tests/fn_cases/*.yaml (一例一文件).

用法:
    uv run python scripts/fn_regression.py                     # 跑全部案例出报告
    uv run python scripts/fn_regression.py --case FN-005       # 只跑一例
    uv run python scripts/fn_regression.py --repeat 3          # 每例跑 3 次多数投票 (记抖动)
    uv run python scripts/fn_regression.py --save-baseline     # 存 docs/fn_baseline.md
    uv run python scripts/fn_regression.py --against-baseline  # 比对基线, 跌档非零 exit

不落生产库 (只调 runner.audit, 不 persist_one, 且强制 JAVERT_SQL_ENABLED=false);
hub 患者自动 etl_from_data_hub 到 output/fn_cache/<pid>/ 缓存后跑, 命中缓存跳过取数.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

CASES_DIR = ROOT / "tests" / "fn_cases"
CACHE_ROOT = ROOT / "output" / "fn_cache"
BASELINE_PATH = ROOT / "docs" / "fn_baseline.md"

_SEV = {"VIOLATION": 2, "INCONCLUSIVE": 1, "CLEAN": 0}
_GRADE_RANK = {"full": 2, "partial": 1, "miss": 0}  # 退化 = 当前 rank < 基线 rank


class FnCase(BaseModel):
    """FN 回归案例 schema. yaml 一例一文件, git 可 diff."""

    case_id: str = Field(..., pattern=r"^FN-\d{3}$")
    patient_id: str = Field(..., min_length=1)
    rule_id: str = Field(..., min_length=1)  # 规则未落地时为 TBD 占位
    # 专家裁定期望裁决; 允许 CLEAN = 误判修正锚
    expected_verdict: Literal["VIOLATION", "INCONCLUSIVE", "CLEAN"]
    expected_evidence_keywords: list[str] = Field(default_factory=list)
    data_source: Literal["csv", "hub"] = "csv"
    found_by: str = ""
    attribution: str = ""


# `from __future__ import annotations` 使字段注解为字符串; 本模块经 importlib 加载
# (scripts 非包, 不入 sys.modules) 时 pydantic 惰性解析找不到模块 globals, 显式在
# 定义处解析 (此时 Literal/list 已在命名空间).
FnCase.model_rebuild()


def load_cases(case_filter: str | None = None) -> list[FnCase]:
    cases: list[FnCase] = []
    for p in sorted(CASES_DIR.glob("FN-*.yaml")):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        c = FnCase(**data)
        if case_filter and c.case_id != case_filter:
            continue
        cases.append(c)
    return cases


# ─── hub 取数 + config 切换 ──────────────────────────────────────────

def ensure_hub_data(pid: str) -> Path:
    """hub 患者取数到 output/fn_cache/<pid>/, 命中缓存跳过. 返回数据目录."""
    from javert.config import get_config
    from javert.data import hub_source as hs

    out = CACHE_ROOT / pid
    marker = out / "shi_fee.csv"
    if marker.exists() and marker.stat().st_size > 0:
        return out
    out.mkdir(parents=True, exist_ok=True)
    cn = hs.connect(get_config())
    yq2org = hs.fetch_hospital_map(cn)
    hs.fetch_fees(cn, [pid], yq2org).to_csv(out / "shi_fee.csv", index=False, encoding="utf-8-sig")
    hs.fetch_notes(cn, [pid]).to_csv(out / "case_notes.csv", index=False, encoding="utf-8-sig")
    hs.fetch_zd(cn, [pid], yq2org).to_csv(out / "shi_zd.csv", index=False, encoding="utf-8-sig")
    hs.fetch_ss(cn, [pid], yq2org).to_csv(out / "shi_ss.csv", index=False, encoding="utf-8-sig")
    hs.fetch_labs(cn, [pid]).to_csv(out / "lab_results.csv", index=False, encoding="utf-8-sig")
    hs.fetch_exams(cn, [pid]).to_csv(out / "examinations.csv", index=False, encoding="utf-8-sig")
    return out


def _point_config_at(data_dir: Path | None) -> None:
    """把 config 指向 hub 缓存目录 (None → 恢复默认 data/), reset 缓存."""
    from javert.config import reset_config_cache

    # 必须 pin 全部 6 个数据文件名: 部署机 (62) `source .env` 会把
    # JAVERT_NOTES_FILE/FEES_FILE=*_with_szx.csv 导入环境, 只覆盖 DATA_DIR 会让
    # loader 去缓存目录找 *_with_szx.csv (不存在) → 全工具失败. etl_from_data_hub
    # 落盘用的就是这 6 个裸名.
    files = {
        "JAVERT_NOTES_FILE": "case_notes.csv",
        "JAVERT_FEES_FILE": "shi_fee.csv",
        "JAVERT_ZD_FILE": "shi_zd.csv",
        "JAVERT_SS_FILE": "shi_ss.csv",
        "JAVERT_LABS_FILE": "lab_results.csv",
        "JAVERT_EXAMINATIONS_FILE": "examinations.csv",
    }
    if data_dir is None:
        # ponytail: csv-source 在已 source _with_szx 的机器上不还原那些名 (当前无 csv 案例); 有了再补
        for k in ("JAVERT_DATA_DIR", *files):
            os.environ.pop(k, None)
    else:
        os.environ["JAVERT_DATA_DIR"] = str(data_dir)
        os.environ.update(files)
    reset_config_cache()


def run_audit_once(case: FnCase):
    """跑一次 audit, 返回 AuditResult; 无规则 (TBD 占位/文件缺) 返回 None."""
    from javert.audit.rule_loader import load_rule
    from javert.audit.runner import Runner
    from javert.config import get_config
    from javert.data.csv_loader import CsvLoader
    from javert.tools.registry import build_executor

    cfg = get_config()
    rule_path = cfg.rules_path / f"{case.rule_id}.yaml"
    if not rule_path.exists():
        return None
    rule = load_rule(rule_path)
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    executor = build_executor(loader, cfg)
    runner = Runner(executor=executor, config=cfg, emit=lambda _m: None, loader=loader)
    return runner.audit(rule, case.patient_id)


# ─── 三档判定 ────────────────────────────────────────────────────────

def _evidence_blob(result) -> str:
    parts = [result.reasoning]
    for ev in result.evidence:
        parts += [ev.locator, ev.text]
    return "\n".join(parts)


def grade(case: FnCase, result) -> tuple[str, str]:
    """三档判定. 返回 (档位, 说明). 档位 ∈ full/partial/miss.

    期望 V/I: 严重度下限断言 (容忍 LLM 漂移); 期望 CLEAN: 严格等值 (误判修正锚).
    """
    if result is None:
        return "miss", "无规则"
    got, exp = result.verdict, case.expected_verdict

    if exp == "CLEAN":  # 误判修正锚 — 实得 V/I 即假阳性复发
        return ("full", f"实得 {got}") if got == "CLEAN" else ("miss", f"实得 {got} (假阳性复发)")

    missing = [k for k in case.expected_evidence_keywords if k not in _evidence_blob(result)]
    if _SEV[got] >= _SEV[exp]:
        if not missing:
            return "full", f"实得 {got}"
        return "partial", f"实得 {got} 缺证据: {','.join(missing)}"
    if got == "INCONCLUSIVE":  # 只到 I, 期望 V
        return "partial", f"实得 {got} (未达期望严重度)"
    return "miss", f"实得 {got}"


def run_case(case: FnCase, repeat: int):
    """跑一例 (repeat>1 多数投票), 返回 (档位, 说明, 代表 result)."""
    _point_config_at(ensure_hub_data(case.patient_id) if case.data_source == "hub" else None)
    results = [run_audit_once(case) for _ in range(repeat)]
    verdicts = [r.verdict if r is not None else "无规则" for r in results]
    modal = Counter(verdicts).most_common(1)[0][0]
    chosen = next((r for r, v in zip(results, verdicts) if v == modal), results[0])
    g, note = grade(case, chosen)
    if repeat > 1 and len(set(verdicts)) > 1:
        note += f" [抖动 {dict(Counter(verdicts))}]"
    return g, note, chosen


# ─── 基线 ────────────────────────────────────────────────────────────

def save_baseline(rows) -> None:
    lines = [
        "# FN 回归基线 (scripts/fn_regression.py --save-baseline 自动生成; 勿手改)",
        "",
        "| case_id | rule_id | 档位 | 实得 verdict | 说明 |",
        "|---------|---------|------|-------------|------|",
    ]
    for case, g, got, note in rows:
        lines.append(f"| {case.case_id} | {case.rule_id} | {g} | {got} | {note} |")
    BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n基线已存 {BASELINE_PATH.relative_to(ROOT)}")


def load_baseline() -> dict[str, str]:
    if not BASELINE_PATH.exists():
        return {}
    base: dict[str, str] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| FN-"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        base[cells[0]] = cells[2]  # case_id → 档位
    return base


def compare_baseline(rows) -> int:
    base = load_baseline()
    if not base:
        print("\n✗ 无基线可比对 (先跑 --save-baseline)", file=sys.stderr)
        return 2
    regressed = [
        (case.case_id, base[case.case_id], g)
        for case, g, _got, _note in rows
        if case.case_id in base and _GRADE_RANK[g] < _GRADE_RANK.get(base[case.case_id], 0)
    ]
    if regressed:
        print("\n✗ 退化 (基线档位 → 当前):", file=sys.stderr)
        for cid, b, g in regressed:
            print(f"    {cid}: {b} → {g}", file=sys.stderr)
        return 1
    print("\n✓ 无退化 (全部 >= 基线档位)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="FN 回归案例库 runner")
    ap.add_argument("--case", help="只跑一例 (如 FN-005)")
    ap.add_argument("--repeat", type=int, default=1, help="每例跑 N 次多数投票")
    ap.add_argument("--save-baseline", action="store_true", help="存基线到 docs/fn_baseline.md")
    ap.add_argument("--against-baseline", action="store_true", help="比对基线, 跌档非零 exit")
    args = ap.parse_args()

    os.environ["JAVERT_SQL_ENABLED"] = "false"  # 不落生产库

    cases = load_cases(args.case)
    if not cases:
        print("✗ 无案例 (tests/fn_cases/ 为空或 --case 未命中)", file=sys.stderr)
        return 2

    print(f"=== FN 回归 ({len(cases)} 例, repeat={args.repeat}) ===")
    print(f"  {'case':8s}  {'rule':16s}  {'档位':6s}  {'verdict':14s}  说明")
    rows = []
    for case in cases:
        g, note, result = run_case(case, args.repeat)
        got = result.verdict if result is not None else "无规则"
        rows.append((case, g, got, note))
        print(f"  {case.case_id:8s}  {case.rule_id:16s}  {g:6s}  {got:14s}  {note}")

    gc = Counter(g for _c, g, _got, _n in rows)
    n = len(rows)
    catch = gc["full"] + gc["partial"]
    print(f"\ncatch 率: {catch}/{n} = {catch / n * 100:.0f}%  "
          f"(full={gc['full']} partial={gc['partial']} miss={gc['miss']})")

    if args.save_baseline:
        save_baseline(rows)
    if args.against_baseline:
        return compare_baseline(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
