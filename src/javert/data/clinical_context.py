# -*- coding: utf-8 -*-
"""PatientClinicalContext — 病案首页级临床事实 (供 verdict_gate 确定性闸判据).

设计动机 (fix-anesthesia-false-positive):
  R203/R205 (麻醉) / R131 (术前心脏彩超) / R153-156 (肿瘤标志物) 三类规则, 即便
  prompt_addon 里写满"专家共识触发器", 35B 模型仍反复把『有手术 → 全麻真实』『肿瘤
  病人查标志物』判成 VIOLATION (专家批注"胡扯"). 靠 prompt 管不住, 必须用确定性闸兜底.

  闸的判据来自最权威的病案首页两张表 (而非易缺失的 case_notes):
    - shi_ss (手术表): oprn_oprt_name 手术名 / anst_way 麻醉方式 (1=全麻气管插管, 9=其他)
                       / anst_dr_name 麻醉医师签名 / oprn_lv_code 手术级别 / main_oprn_flag
    - shi_zd (诊断表): inhosp_diag_name + diag_name 全部诊断

  影像类 (R103/R105) 另接入 sy_patient_examination (检查报告表) 确认服务真实开展 —
  报告在 → 服务真实 → CLEAN; 报告不在 → 待 PACS 线下核查 → INCONCLUSIVE.

本模块只读不判 (判定在 verdict_gate). 进程级缓存 ss/zd 索引 (小表, 各 2-3 MB).

Source: 本项目原创 (fix-anesthesia-false-positive).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

import pandas as pd

logger = logging.getLogger("javert.data.clinical_context")

# 恶性 / 肿瘤诊断关键词 (闸⑧ 肿瘤标志物指征) — 专家共识: 任何肿瘤患者查标志物属正常诊疗.
# 用 "肿瘤/占位/新生物" 而非裸 "瘤", 避免误纳血管瘤/脂肪瘤等明确良性 (但保守起见, 用户
# 已明确接受『消假阳性优先于召回』的取舍).
_MALIGNANCY_KW: tuple[str, ...] = (
    "恶性", "癌", "淋巴瘤", "白血病", "肉瘤", "转移", "继发", "母细胞瘤", "胶质瘤", "黑色素瘤",
)
_TUMOR_KW: tuple[str, ...] = _MALIGNANCY_KW + ("肿瘤", "占位", "新生物")

# anst_way 编码 (本数据集只出现 1 / 9): 1=全身麻醉(气管插管), 9=其他(局麻/无). anst_mtd_name
# 列在本数据集全空, 故用 anst_way + anst_dr_name 作麻醉真实性判据.
ANST_WAY_GENERAL = "1"


@dataclass
class Surgery:
    """shi_ss 一行手术记录的精简载体."""

    name: str
    anst_way: str = ""
    anst_dr: str = ""
    level_code: str = ""
    is_main: bool = False

    @property
    def has_general_anesthesia(self) -> bool:
        return self.anst_way == ANST_WAY_GENERAL

    @property
    def has_anesthesiologist(self) -> bool:
        return bool(self.anst_dr.strip())


@dataclass
class PatientClinicalContext:
    """单患者病案首页级临床事实. 闸层判据载体 (None 表示数据不可用 → 闸 fail-open)."""

    patient_id: str
    surgeries: list[Surgery] = field(default_factory=list)
    diagnoses: list[str] = field(default_factory=list)
    # 检查报告 loader (ExaminationLoader); None = 未注入 → has_imaging_report() 返回 None
    exam_loader: object | None = None

    # ---- 麻醉真实性 (闸⑥) ----
    def has_anesthesia_service(self) -> bool:
        """是否有麻醉服务真实发生: 任一手术行有麻醉医师签名 或 anst_way=全麻.

        病案首页手术表记录了麻醉医师 = 该次手术麻醉真实开展 (麻醉记录单仅 ETL 未数字化).
        """
        return any(s.has_anesthesiologist or s.has_general_anesthesia for s in self.surgeries)

    def has_general_anesthesia(self) -> bool:
        """是否有全身麻醉手术 (anst_way=1)."""
        return any(s.has_general_anesthesia for s in self.surgeries)

    def has_surgery(self) -> bool:
        """是否有任何手术/操作记录."""
        return any(s.name.strip() for s in self.surgeries)

    def anesthesiologists(self) -> list[str]:
        return sorted({s.anst_dr.strip() for s in self.surgeries if s.anst_dr.strip()})

    def general_anesthesia_surgeries(self) -> list[str]:
        return [s.name for s in self.surgeries if s.has_general_anesthesia and s.name.strip()]

    # ---- 肿瘤诊断 (闸⑧) ----
    def has_tumor(self) -> bool:
        """是否有肿瘤/恶性诊断 (含动态未定/占位)."""
        return any(any(k in d for k in _TUMOR_KW) for d in self.diagnoses)

    def has_malignancy(self) -> bool:
        """是否有明确恶性诊断."""
        return any(any(k in d for k in _MALIGNANCY_KW) for d in self.diagnoses)

    def tumor_diagnoses(self) -> list[str]:
        return [d for d in self.diagnoses if any(k in d for k in _TUMOR_KW)]

    # ---- 影像服务确认 (闸① 影像可确认) ----
    def has_imaging_report(self) -> bool | None:
        """检查报告表是否有该患者报告. None = exam_loader 未注入 (无法确认)."""
        if self.exam_loader is None:
            return None
        try:
            rows = self.exam_loader.get_examinations(self.patient_id)
            return len(rows) > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("has_imaging_report 查询失败 patient=%s: %s", self.patient_id, exc)
            return None


# =========================================================
# 索引构建 (进程级缓存, ss/zd 各一份)
# =========================================================
_SS_INDEX: dict[str, dict[str, list[Surgery]]] = {}
_ZD_INDEX: dict[str, dict[str, list[str]]] = {}
_LOCK = Lock()


def _bare_pid(raw: str) -> str:
    """复合键 H31010600042-J61556 → J61556; 纯住院号原样返回."""
    return str(raw).strip().split("-")[-1].strip()


def _read_table(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        logger.warning("clinical_context: 表不存在 %s", path)
        return None
    try:
        if path.suffix.lower() == ".csv":
            return pd.read_csv(path, dtype=str, low_memory=False).fillna("")
        return pd.read_excel(path, dtype=str).fillna("")
    except Exception as exc:  # noqa: BLE001
        logger.warning("clinical_context: 读表失败 %s: %s", path, exc)
        return None


def _build_ss_index(ss_path: Path) -> dict[str, list[Surgery]]:
    key = str(ss_path)
    if key in _SS_INDEX:
        return _SS_INDEX[key]
    with _LOCK:
        if key in _SS_INDEX:
            return _SS_INDEX[key]
        index: dict[str, list[Surgery]] = {}
        df = _read_table(ss_path)
        if df is not None:
            id_col = next((c for c in ("ba_id", "bah", "住院号") if c in df.columns), None)
            if id_col is not None:
                for _, row in df.iterrows():
                    pid = _bare_pid(row[id_col])
                    if not pid or pid.lower() == "nan":
                        continue
                    index.setdefault(pid, []).append(Surgery(
                        name=str(row.get("oprn_oprt_name", "")).strip(),
                        anst_way=str(row.get("anst_way", "")).strip(),
                        anst_dr=str(row.get("anst_dr_name", "")).strip(),
                        level_code=str(row.get("oprn_lv_code", "")).strip(),
                        is_main=str(row.get("main_oprn_flag", "")).strip() == "1",
                    ))
        _SS_INDEX[key] = index
        return index


def _build_zd_index(zd_path: Path) -> dict[str, list[str]]:
    key = str(zd_path)
    if key in _ZD_INDEX:
        return _ZD_INDEX[key]
    with _LOCK:
        if key in _ZD_INDEX:
            return _ZD_INDEX[key]
        index: dict[str, list[str]] = {}
        df = _read_table(zd_path)
        if df is not None:
            id_col = next((c for c in ("ba_id", "bah", "住院号") if c in df.columns), None)
            name_cols = [c for c in ("inhosp_diag_name", "diag_name") if c in df.columns]
            if id_col is not None and name_cols:
                for _, row in df.iterrows():
                    pid = _bare_pid(row[id_col])
                    if not pid or pid.lower() == "nan":
                        continue
                    bucket = index.setdefault(pid, [])
                    for c in name_cols:
                        name = str(row.get(c, "")).strip()
                        if name and name.lower() != "nan" and name not in bucket:
                            bucket.append(name)
        _ZD_INDEX[key] = index
        return index


def build_clinical_context(
    patient_id: str,
    ss_path: Path,
    zd_path: Path,
    exam_loader: object | None = None,
) -> PatientClinicalContext:
    """构建单患者临床上下文. ss/zd 缺表则对应字段为空 (闸 fail-open)."""
    pid = _bare_pid(patient_id)
    surgeries = _build_ss_index(ss_path).get(pid, [])
    diagnoses = _build_zd_index(zd_path).get(pid, [])
    return PatientClinicalContext(
        patient_id=pid,
        surgeries=list(surgeries),
        diagnoses=list(diagnoses),
        exam_loader=exam_loader,
    )


def reset_cache() -> None:
    """清空进程级缓存 (测试用)."""
    _SS_INDEX.clear()
    _ZD_INDEX.clear()
