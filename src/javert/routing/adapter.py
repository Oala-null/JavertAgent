"""从 Javert 现有数据源 (CsvLoader + shi_zd.xls) 装载 PatientRecord.

调用方:
  build_patient_record_for_router(patient_id, loader)
    → router-ready PatientRecord (诊断 / 费用 / hospital_level / visit_type 默认值)

shi_zd.xls 可选; 缺失时 PatientRecord.diagnoses 为空, router 不会因此 false-negative
(case B 的 trigger_keywords 同时查 fee + diag, fee 命中仍能通过).
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import pandas as pd

from javert.data.loader import DataLoader

from .types import FeeItem, PatientRecord


# ────────────────────────── shi_zd 缓存 (进程内单例) ──────────────────────────

@lru_cache(maxsize=8)
def _load_zd_index(zd_path_str: str) -> dict[str, list[dict]]:
    """读 shi_zd (.xls/.xlsx/.csv), 按 patient_id 索引诊断行.

    返回: { patient_id: [ {diag_name, diag_code, maindiag_flag, ...}, ... ] }
    """
    path = Path(zd_path_str)
    if not path.exists():
        return {}
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype=str, low_memory=False)
    else:
        df = pd.read_excel(path, dtype=str)
    id_col = next((c for c in ("ba_id", "bah", "住院号", "patient_id") if c in df.columns), None)
    if id_col is None:
        return {}
    idx: dict[str, list[dict]] = {}
    for _, row in df.iterrows():
        raw_id = str(row.get(id_col) or "").strip()
        if not raw_id or raw_id == "nan":
            continue
        pid = raw_id.split("-", 1)[1].strip() if "-" in raw_id else raw_id.strip()
        if not pid:
            continue

        def _col(name: str) -> str:
            if name not in df.columns:
                return ""
            v = row.get(name)
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return ""
            s = str(v).strip()
            return "" if s.lower() in ("nan", "none") else s

        idx.setdefault(pid, []).append({
            "diag_name":        _col("diag_name"),
            "diag_code":        _col("diag_code"),
            "inhosp_diag_name": _col("inhosp_diag_name"),
            "maindiag_flag":    _col("maindiag_flag"),
        })
    return idx


def _safe_float(x) -> float | None:
    try:
        if x is None or x == "" or (isinstance(x, float) and math.isnan(x)):
            return None
        return float(x)
    except (ValueError, TypeError):
        return None


# ────────────────────────── 主 adapter ──────────────────────────

def build_patient_record_for_router(
    patient_id: str,
    loader: DataLoader,
    *,
    shi_zd_path: Path | None = None,
    hospital_level: int = 3,
    visit_type: str = "ipt",
    gender: str | None = None,
    age: int | None = None,
) -> PatientRecord:
    """从 loader.get_fees + 可选 shi_zd 装 PatientRecord.

    - hospital_level / visit_type / gender / age 是当前数据集的人为默认值
      (H31010600042 是上海三级综合; shi_fee 数据全是住院).
    - 升级 SQL Loader 后, gender/age/visit_type 应改为从结算主表 (ImsJszdPre) 取真值.
    """
    fees_df = loader.get_fees(patient_id)
    fee_items: list[FeeItem] = []
    for _, row in fees_df.iterrows():
        name = str(row.get("medins_list_name") or "").strip()
        if not name:
            continue
        fee_items.append(FeeItem(
            item_sn=str(row.get("feedetl_sn") or "").strip(),
            medins_list_name=name,
            medins_list_codg=(str(row.get("medins_list_codg") or "").strip() or None),
            med_list_codg=(str(row.get("med_list_codg") or "").strip() or None),
            chrgitm_type=(str(row.get("medins_chrgitm_type") or "").strip() or None),
            inscp_scp_amt=_safe_float(row.get("inscp_scp_amt")),
            fee_ocur_time=(str(row.get("fee_ocur_time") or "").strip() or None),
            spec=(str(row.get("spec") or "").strip() or None),
        ))

    diagnoses: list[str] = []
    diagnosis_codes: list[str] = []
    if shi_zd_path is not None:
        zd_idx = _load_zd_index(str(shi_zd_path))
        for zd in zd_idx.get(patient_id, []):
            if zd.get("diag_name"):
                diagnoses.append(zd["diag_name"])
            if zd.get("inhosp_diag_name"):
                diagnoses.append(zd["inhosp_diag_name"])
            if zd.get("diag_code"):
                diagnosis_codes.append(zd["diag_code"])
        # 去重保序
        diagnoses = list(dict.fromkeys(diagnoses))
        diagnosis_codes = list(dict.fromkeys(diagnosis_codes))

    return PatientRecord(
        patient_id=patient_id,
        gender=gender,
        age=age,
        hospital_level=hospital_level,
        visit_type=visit_type,
        diagnoses=diagnoses,
        diagnosis_codes=diagnosis_codes,
        fee_items=fee_items,
    )


def default_shi_zd_path() -> Path:
    """config.zd_path (默认 data/shi_zd.xls, 环境变量 JAVERT_ZD_FILE 可覆盖)."""
    from javert.config import get_config
    return get_config().zd_path
