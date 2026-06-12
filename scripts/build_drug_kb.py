# -*- coding: utf-8 -*-
"""build_drug_kb — 4 份药品监管 xlsx → 归一化 configs/drug_audit_kb.json + 命中频次表.

输入 (data/药品类规则/):
  第二部分-8.药品限适应症...xlsx   → rule_type=限适应症 (715)
  第二部分-70.超说明书...xlsx       → rule_type=超说明书 (144)
  第二部分-5.药品限二线...xlsx      → rule_type=限二线 (112)
  第二部分-71.药品禁忌症...xlsx     → rule_type=禁忌症 (63)

输入 (data/药品类规则/含代码/) — 每知识点带国家医保药品码 (fix-drug-code-match):
  8.药品限适应症对应知识代码表.xlsx    (对应知识点序号|药品通用名|序号|药品代码)
  70.超说明书...代码表.xlsx
  5.药品限二线...代码表.xlsx
  71.药品禁忌症...代码表.xlsx
  → 按通用名聚合国家药品码集合, 与无码 4 表的 basis/检出逻辑 按通用名 (kb_stem 归一) join

输出:
  configs/drug_audit_kb.json   {通用名:{entries:[{rule_type,detect_logic,basis}], codes:[国家码]}} (确定性: sort_keys)
  output/drug_kb_hits.csv      KB 通用名 ∩ shi_fee 西药/中药/草药 命中频次表 (按命中患者数降序)
  output/drug_kb_code_join_warnings.csv  含代码表通用名无法 join 无码表 entries 的核对清单 (不静默丢)

跑法: uv run python scripts/build_drug_kb.py

注: 4 份 xlsx 表头实际在第 4 行 (前 3 行: 空行 + 标题 + 空行), task 2.1 写的 header=2
落不到真表头. 本脚本改用"扫到含『药品通用名』的行"作表头, 对偏移鲁棒且确定.
"""

from __future__ import annotations

import json
import logging
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from javert.tools.drug_audit_lookup import fee_clean, kb_stem  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_drug_kb")

KB_DIR = ROOT / "data" / "药品类规则"
CODE_DIR = KB_DIR / "含代码"
OUT_KB = ROOT / "configs" / "drug_audit_kb.json"
OUT_HITS = ROOT / "output" / "drug_kb_hits.csv"
OUT_JOIN_WARN = ROOT / "output" / "drug_kb_code_join_warnings.csv"
FEE_CSV = ROOT / "data" / "shi_fee.csv"

KB_VERSION = "2.0"  # fix-drug-code-match: drugs[通用名] 升级为 {entries, codes}
DRUG_CHRGITM_TYPES = {"西药", "中药", "草药"}

# 文件名关键片段 → rule_type (无码表)
FILE_RULE_TYPE = [
    ("第二部分-8.", "限适应症"),
    ("第二部分-70.", "超说明书"),
    ("第二部分-5.", "限二线"),
    ("第二部分-71.", "禁忌症"),
]

# 含代码表文件名关键片段 (与无码表同知识点不同视图; rule_type 仅供日志)
CODE_FILE_RULE_TYPE = [
    ("8.", "限适应症"),
    ("70.", "超说明书"),
    ("5.", "限二线"),
    ("71.", "禁忌症"),
]


def _resolve_file(prefix: str, base: Path = KB_DIR) -> Path:
    for p in sorted(base.glob("*.xlsx")):
        if p.name.startswith(prefix):
            return p
    raise FileNotFoundError(f"未找到 {prefix}*.xlsx in {base}")


def _read_kb_sheet(path: Path) -> list[tuple[str, str, str]]:
    """读单份 xlsx, 返回 [(通用名, 检出逻辑, 逻辑依据), ...]."""
    raw = pd.read_excel(path, header=None, dtype=str)
    # 扫到含 "药品通用名" 的行作表头
    header_row = None
    for i in range(len(raw)):
        cells = [str(x) for x in raw.iloc[i].tolist()]
        if any("药品通用名" in c for c in cells):
            header_row = i
            break
    if header_row is None:
        raise ValueError(f"{path.name} 未找到含『药品通用名』的表头行")
    body = raw.iloc[header_row + 1:]
    body = body.dropna(how="all")
    out: list[tuple[str, str, str]] = []
    for _, row in body.iterrows():
        generic = str(row.iloc[1] or "").strip()       # col 1 = 药品通用名
        detect = str(row.iloc[2] or "").strip()         # col 2 = 检出逻辑
        basis = str(row.iloc[3] or "").strip()          # col 3 = 逻辑依据
        if not generic or generic in ("nan", "药品通用名"):
            continue
        out.append((generic, _clean(detect), _clean(basis)))
    return out


def _clean(s: str) -> str:
    return "" if s.strip().lower() in ("nan", "none") else s.strip()


def _read_code_sheet(path: Path) -> dict[str, set[str]]:
    """读单份含代码 xlsx, 返回 {通用名: set(国家药品码)}.

    表头在第 4 行 (前 3 行标题/空), 列: 知识点序号|药品通用名|序号|药品代码.
    通用名仅在每知识点首行出现 (后续码行 col1 为空) → forward-fill.
    """
    raw = pd.read_excel(path, header=None, dtype=str)
    header_row = None
    for i in range(len(raw)):
        cells = [str(x) for x in raw.iloc[i].tolist()]
        if any("药品通用名" in c for c in cells):
            header_row = i
            break
    if header_row is None:
        raise ValueError(f"{path.name} 未找到含『药品通用名』的表头行")
    out: dict[str, set[str]] = defaultdict(set)
    cur: str | None = None
    for _, row in raw.iloc[header_row + 1:].iterrows():
        generic = str(row.iloc[1] or "").strip()
        code = str(row.iloc[3] or "").strip()
        if generic and generic not in ("nan", "药品通用名"):
            cur = generic
        if not cur:
            continue
        if code and code.lower() not in ("nan", "none"):
            out[cur].add(code)
    return out


def _build_code_map() -> dict[str, set[str]]:
    """4 份含代码表 → {通用名: set(国家药品码)} (跨表按通用名 union)."""
    code_map: dict[str, set[str]] = defaultdict(set)
    for prefix, rule_type in CODE_FILE_RULE_TYPE:
        path = _resolve_file(prefix, base=CODE_DIR)
        sheet = _read_code_sheet(path)
        for generic, codes in sheet.items():
            code_map[generic] |= codes
        log.info("含代码表 %s: %d 通用名 / %d 码", rule_type,
                 len(sheet), sum(len(c) for c in sheet.values()))
    return code_map


def build_kb() -> tuple[dict, list[str]]:
    """返回 (kb, 含代码表无法 join 无码 entries 的通用名核对清单)."""
    drugs: dict[str, list[dict]] = defaultdict(list)
    per_type_counts: dict[str, int] = {}
    for prefix, rule_type in FILE_RULE_TYPE:
        path = _resolve_file(prefix)
        rows = _read_kb_sheet(path)
        per_type_counts[rule_type] = len(rows)
        for generic, detect, basis in rows:
            # 同 (通用名, rule_type) 去重 (同一份表理论不重复, 防御)
            if any(e["rule_type"] == rule_type for e in drugs[generic]):
                continue
            drugs[generic].append({
                "rule_type": rule_type,
                "detect_logic": detect,
                "basis": basis,
            })

    # 含代码表 → code_map, 按通用名 (kb_stem 归一兜底) join 无码 entries
    code_map = _build_code_map()
    drug_stems: dict[str, str] = {kb_stem(g): g for g in drugs}  # stem → 一个无码通用名
    joined: dict[str, set[str]] = defaultdict(set)
    unjoined: list[str] = []
    for code_generic, codes in code_map.items():
        if code_generic in drugs:
            joined[code_generic] |= codes
        elif kb_stem(code_generic) in drug_stems:
            joined[drug_stems[kb_stem(code_generic)]] |= codes
        else:
            unjoined.append(code_generic)
    for g in sorted(unjoined):
        log.warning("含代码表通用名「%s」无法 join 无码表 entries (落核对清单, 不静默丢)", g)

    # 每药: entries 按 (rule_type, basis) 排序 + codes 排序 → 确定性
    sorted_drugs = {
        g: {
            "entries": sorted(entries, key=lambda e: (e["rule_type"], e["basis"])),
            "codes": sorted(joined.get(g, set())),
        }
        for g, entries in drugs.items()
    }
    kb = {
        "version": KB_VERSION,
        "rule_types": sorted({
            e["rule_type"] for v in sorted_drugs.values() for e in v["entries"]
        }),
        "per_type_counts": per_type_counts,
        "drug_count": len(sorted_drugs),
        "code_count": sum(len(v["codes"]) for v in sorted_drugs.values()),
        "drugs_with_codes": sum(1 for v in sorted_drugs.values() if v["codes"]),
        "drugs": sorted_drugs,
    }
    return kb, sorted(unjoined)


def write_kb(kb: dict) -> None:
    OUT_KB.parent.mkdir(parents=True, exist_ok=True)
    OUT_KB.write_text(
        json.dumps(kb, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_hit_table(kb: dict) -> pd.DataFrame:
    """KB 通用名 ∩ shi_fee 西药/中药/草药 fee 名 (stem 子串), 按命中患者数降序."""
    df = pd.read_csv(
        FEE_CSV, dtype=str, low_memory=False,
        usecols=["bah", "medins_list_name", "medins_chrgitm_type"],
    )
    df = df[df["medins_chrgitm_type"].isin(DRUG_CHRGITM_TYPES)]
    df = df[df["medins_list_name"].notna()]

    def _pid(bah: str) -> str:
        b = str(bah).strip()
        return b.split("-", 1)[1].strip() if "-" in b else b

    # distinct fee_name → 命中患者集合 + cleaned 名
    fee_to_pids: dict[str, set[str]] = defaultdict(set)
    for bah, name in zip(df["bah"], df["medins_list_name"]):
        fee_to_pids[str(name).strip()].add(_pid(bah))
    distinct = [(name, fee_clean(name), pids) for name, pids in fee_to_pids.items()]

    rows = []
    for generic, drug_val in kb["drugs"].items():
        entries = drug_val["entries"]
        stem = kb_stem(generic)
        if len(stem) < 2:
            continue
        hit_pids: set[str] = set()
        sample_fees: list[str] = []
        for name, cleaned, pids in distinct:
            if stem in cleaned:
                hit_pids |= pids
                if len(sample_fees) < 3 and name not in sample_fees:
                    sample_fees.append(name)
        if hit_pids:
            rows.append({
                "通用名": generic,
                "stem": stem,
                "rule_types": "|".join(e["rule_type"] for e in entries),
                "命中患者数": len(hit_pids),
                "样例fee名": " / ".join(sample_fees),
            })
    out = pd.DataFrame(rows).sort_values(
        ["命中患者数", "通用名"], ascending=[False, True]
    ).reset_index(drop=True)
    return out


def main() -> None:
    kb, unjoined = build_kb()
    write_kb(kb)
    print(f"[1/3] drug_audit_kb.json → {kb['drug_count']} 通用名")
    print(f"        rule_types: {kb['rule_types']}")
    print(f"        per_type_counts: {kb['per_type_counts']}")
    print(f"        国家码: {kb['code_count']} 码 / {kb['drugs_with_codes']} 通用名带码")

    OUT_JOIN_WARN.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"含代码表通用名_无法join无码entries": unjoined}).to_csv(
        OUT_JOIN_WARN, index=False, encoding="utf-8-sig"
    )
    print(f"[2/3] 含代码 join 核对清单 → {len(unjoined)} 条未 join (详见 {OUT_JOIN_WARN.name})")

    hits = build_hit_table(kb)
    OUT_HITS.parent.mkdir(parents=True, exist_ok=True)
    hits.to_csv(OUT_HITS, index=False, encoding="utf-8-sig")
    print(f"[3/3] drug_kb_hits.csv → {len(hits)} 种 KB 药命中本院数据")
    print(f"        总命中患者数 (并集近似, 头部):")
    for _, r in hits.head(30).iterrows():
        print(f"          {r['命中患者数']:>5}  {r['通用名']:<18} [{r['rule_types']}]")


if __name__ == "__main__":
    main()
