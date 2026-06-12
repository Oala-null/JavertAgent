# -*- coding: utf-8 -*-
"""Tool registry — 读 schema_manifest 按 status 注册 spoke 工具 + 非 spoke 工具.

add-visual-schema-onboarding (设计 D2): registry 据每个 spoke 的 status 注册:
  live   → 真工具 (产裁决)
  view   → 视图工具 (弱信号, 标"暂不参与判定")
  stored / 无对应 tool factory → 兜底 stub (返回"已接收·暂不参与判定", 不报错)
非 spoke 工具 (drug_indication / drug_audit_lookup / scan_progress_indications) 照常注册.
"""

from __future__ import annotations

from typing import Callable

from javert.config import JavertConfig, get_config
from javert.data.examination_loader import ExaminationLoader
from javert.data.lab_loader import LabLoader
from javert.data.loader import DataLoader
from javert.onboarding.manifest_loader import Spoke, load_manifest

from . import (
    drug_audit_lookup,
    drug_indication,
    note_diagnosis,
    scan_progress_indications,
    search_anesthesia,
    search_examinations,
    search_fees,
    search_lab_results,
    search_notes,
    search_pathology,
)
from .tool_executor import ToolExecutor


# 进程级 lazy singleton — LabLoader 392MB 流读 30-60s,避免重复 build.
_lab_loader_singleton: LabLoader | None = None
_exam_loader_singleton: ExaminationLoader | None = None


def _get_lab_loader(cfg: JavertConfig) -> LabLoader:
    global _lab_loader_singleton
    if _lab_loader_singleton is None:
        _lab_loader_singleton = LabLoader(cfg.labs_path)
    return _lab_loader_singleton


def _get_exam_loader(cfg: JavertConfig) -> ExaminationLoader:
    global _exam_loader_singleton
    if _exam_loader_singleton is None:
        _exam_loader_singleton = ExaminationLoader(cfg.examinations_path)
    return _exam_loader_singleton


def get_exam_loader(cfg: JavertConfig) -> ExaminationLoader:
    """公开访问检查报告 loader 单例 (verdict_gate 影像确认闸复用, 避免重复 build)."""
    return _get_exam_loader(cfg)


def _make_stub(spoke: Spoke) -> Callable[..., str]:
    """stored / 无 factory 的 spoke 兜底 stub — 不报错, 诚实标注暂不参与判定 (设计 D2)."""
    def execute(patient_id: str | None = None, **_kwargs) -> str:
        return (
            f"【已接收·暂不参与判定】{spoke.name} 数据已接入但暂无专用审计工具, "
            "不产裁决, 仅病案概览可见原文."
        )
    return execute


def _spoke_tool_wiring(loader: DataLoader, cfg: JavertConfig) -> dict[str, tuple]:
    """tool 名 → (factory, 模块) — 各 spoke 工具的 bespoke 注入装配."""
    return {
        "search_notes": (lambda: search_notes.create_executor(loader), search_notes),
        "search_fees": (lambda: search_fees.create_executor(loader), search_fees),
        "note_diagnosis": (lambda: note_diagnosis.create_executor(loader), note_diagnosis),
        "search_lab_results": (
            lambda: search_lab_results.create_executor(_get_lab_loader(cfg)), search_lab_results),
        "search_examinations": (
            lambda: search_examinations.create_executor(_get_exam_loader(cfg)), search_examinations),
        "search_anesthesia": (
            lambda: search_anesthesia.create_executor(loader, cfg.ss_path), search_anesthesia),
        "search_pathology": (
            lambda: search_pathology.create_executor(loader, _get_lab_loader(cfg)), search_pathology),
    }


def build_executor(loader: DataLoader, config: JavertConfig | None = None) -> ToolExecutor:
    """读 manifest 按 status 注册 spoke 工具 + 非 spoke 工具.

    Args:
        loader: 数据接入层 (CsvLoader / 未来 SqlLoader)
        config: 配置对象 (None 则取全局)
    """
    cfg = config or get_config()
    executor = ToolExecutor()
    manifest = load_manifest()
    wiring = _spoke_tool_wiring(loader, cfg)

    # ── manifest 驱动: 按 status 注册 spoke 工具 (一 tool 多 spoke 去重) ──
    registered: set[str] = set()
    for spoke in manifest.spokes.values():
        tool = spoke.tool
        if not tool or tool in registered:
            continue
        if tool in wiring:
            factory, mod = wiring[tool]
            executor.register(
                tool, factory(),
                description=mod.DESCRIPTION,
                requires_patient_id=getattr(mod, "REQUIRES_PATIENT_ID", False),
            )
        else:
            # stored / 无 factory → 兜底 stub
            executor.register(
                tool, _make_stub(spoke),
                description=f"{spoke.name} (已接收·暂不参与判定)",
                requires_patient_id=True,
            )
        registered.add(tool)

    # ── 非 spoke 工具 (不挂在数据 spoke 上, 照常注册) ──
    drug_map_path = cfg.resolve("configs") / "drug_indication_map.json"
    executor.register(
        "drug_indication",
        drug_indication.create_executor(drug_map_path),
        description=drug_indication.DESCRIPTION,
        requires_patient_id=getattr(drug_indication, "REQUIRES_PATIENT_ID", False),
    )
    drug_kb_path = cfg.resolve("configs") / "drug_audit_kb.json"
    executor.register(
        "drug_audit_lookup",
        drug_audit_lookup.create_executor(loader, drug_kb_path, cfg.zd_path),
        description=drug_audit_lookup.DESCRIPTION,
        requires_patient_id=getattr(drug_audit_lookup, "REQUIRES_PATIENT_ID", False),
    )
    executor.register(
        "scan_progress_indications",
        scan_progress_indications.create_executor(loader),
        description=scan_progress_indications.DESCRIPTION,
        requires_patient_id=getattr(scan_progress_indications, "REQUIRES_PATIENT_ID", False),
    )
    return executor


def get_tool_input_schemas() -> dict[str, dict]:
    """返回每个工具的 JSON-Schema (供 prompt 文档化, 不强制传给 LLM)."""
    return {
        "search_notes": search_notes.INPUT_SCHEMA,
        "search_fees": search_fees.INPUT_SCHEMA,
        "note_diagnosis": note_diagnosis.INPUT_SCHEMA,
        "drug_indication": drug_indication.INPUT_SCHEMA,
        "drug_audit_lookup": drug_audit_lookup.INPUT_SCHEMA,
        "search_examinations": search_examinations.INPUT_SCHEMA,
        "search_lab_results": search_lab_results.INPUT_SCHEMA,
        "search_anesthesia": search_anesthesia.INPUT_SCHEMA,
        "search_pathology": search_pathology.INPUT_SCHEMA,
        "scan_progress_indications": scan_progress_indications.INPUT_SCHEMA,
    }
