# -*- coding: utf-8 -*-
"""prescan_med_rst — 肿瘤药医保限定 (RD04) 跑批前的确定性预扫, 零 LLM.

两阶段:
  1. 粗筛 (向量化): 全量 shi_fee 里 med_list_codg ∈ KB 医保限定肿瘤药码集,
     或无码药品行 fee 名含完整实体通用名 (oncology exact_entity_name 兜底口径)
     → 候选患者.
  2. 精筛: 候选患者逐个走 lookup_patient_drugs(rule_type=限适应症, source_type=insurance)
     —— 与 RD04 规则运行时的工具调用**同一函数同一口径** (含退费净额排除),
     保证"预扫命中 = 规则必触发".

输出: output/med_rst_patients.json (患者→命中药明细) + stdout 摘要.
跑法: uv run python scripts/prescan_med_rst.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from javert.config import load_config  # noqa: E402
from javert.data.csv_loader import CsvLoader  # noqa: E402
from javert.tools.drug_audit_lookup import (  # noqa: E402
    DRUG_CHRGITM_TYPES,
    fee_clean,
    kb_codes,
    kb_entries,
    lookup_patient_drugs,
)

OUT_PATH = ROOT / "output" / "med_rst_patients.json"


def insurance_oncology_drugs(kb_path: Path) -> dict[str, set[str]]:
    """KB 里「限适应症 × source_type=insurance」的药 → 通用名 → 码集."""
    kb = json.loads(kb_path.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for generic, val in kb.get("drugs", {}).items():
        for e in kb_entries(val):
            if e.get("rule_type") == "限适应症" and e.get("source_type") == "insurance":
                out[generic] = set(kb_codes(val))
                break
    return out


def bare_pid(bah: str) -> str:
    """复合键 'H31010600042-J13365 ' → 'J13365'; 无 '-' 原样返回."""
    s = str(bah or "").strip()
    return s.rsplit("-", 1)[-1] if "-" in s else s


def main() -> None:
    cfg = load_config()
    kb_path = cfg.resolve("configs") / "drug_audit_kb.json"
    drugs = insurance_oncology_drugs(kb_path)
    all_codes = set().union(*drugs.values()) if drugs else set()
    print(f"KB 医保限定肿瘤药: {len(drugs)} 药 / {len(all_codes)} 个国家码")

    fee_df = pd.read_csv(cfg.fees_path, dtype=str, low_memory=False)
    print(f"费用表: {len(fee_df)} 行, {fee_df['bah'].nunique()} 个 bah")

    ct_col = "medins_chrgitm_type" if "medins_chrgitm_type" in fee_df.columns else None
    drug_rows = fee_df
    if ct_col:
        ct = fee_df[ct_col].fillna("").str.strip()
        drug_rows = fee_df[ct.isin(DRUG_CHRGITM_TYPES) | (ct == "")]

    # 粗筛 A: 码精确命中
    codes = drug_rows.get("med_list_codg", pd.Series(dtype=str)).fillna("").str.strip()
    hit_by_code = drug_rows[codes.isin(all_codes)]
    # 粗筛 B: 无码药品行, fee 名含完整实体通用名 (exact_entity_name 口径)
    nocode = drug_rows[codes.isin({"", "nan", "none", "null"})]
    names = nocode.get("medins_list_name", pd.Series(dtype=str)).fillna("")
    cleaned = names.map(fee_clean)
    name_mask = pd.Series(False, index=nocode.index)
    for generic in drugs:
        name_mask |= cleaned.str.contains(generic, regex=False)
    hit_by_name = nocode[name_mask]

    candidates = sorted(
        {bare_pid(b) for b in hit_by_code["bah"]} | {bare_pid(b) for b in hit_by_name["bah"]}
    )
    print(f"粗筛候选: 码命中 {len(hit_by_code)} 行 + 名兜底 {len(hit_by_name)} 行 → {len(candidates)} 患者")

    # 精筛: 与 RD04 运行时同一函数
    loader = CsvLoader(cfg.notes_path, cfg.fees_path)
    confirmed: dict[str, dict] = {}
    for pid in candidates:
        r = lookup_patient_drugs(
            pid, loader, kb_path, cfg.zd_path,
            rule_type="限适应症", source_type="insurance",
        )
        if not r["matches"]:
            continue  # 粗筛命中但净额全退等 → 规则不会触发, 不入批
        confirmed[pid] = {
            "drugs": [
                {
                    "generic_name": m["generic_name"],
                    "fee_names": m["fee_names"],
                    "basis": m["basis"][:80],
                    "needs_review": m["needs_review"],
                }
                for m in r["matches"]
            ],
            "main_diag": next((d["name"] for d in r["diagnoses"] if d["is_main"]), ""),
            "has_zd": r["zd_available"],
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps(confirmed, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    print(f"\n精筛确认 {len(confirmed)} 患者 → {OUT_PATH}")
    drug_freq: dict[str, int] = {}
    for v in confirmed.values():
        for d in v["drugs"]:
            drug_freq[d["generic_name"]] = drug_freq.get(d["generic_name"], 0) + 1
    print("\n命中药分布 (患者数):")
    for g, n in sorted(drug_freq.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {g}")
    print("\n患者清单 (前 30):")
    for pid in list(confirmed)[:30]:
        v = confirmed[pid]
        ds = " / ".join(d["generic_name"] for d in v["drugs"])
        print(f"  {pid:16s} 主诊={v['main_diag'][:20]:20s} 药={ds}")


if __name__ == "__main__":
    main()
