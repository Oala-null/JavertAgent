# -*- coding: utf-8 -*-
"""患者路由 — GET /api/patients/sample, GET /api/patients/{id}/summary."""

from __future__ import annotations

import logging
import random
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.data.snapshot import load_pilot_roster

from .schemas import PatientSampleResponse, PatientSummary

logger = logging.getLogger("javert.web.routes_patients")

router = APIRouter(prefix="/api/patients", tags=["patients"])


# 进程内缓存 (csv 全量加载 ~10-30s, lazy)
_loader_singleton: CsvLoader | None = None
_loader_lock = threading.Lock()
_full_pool_cache: list[str] | None = None


def _get_loader() -> CsvLoader:
    global _loader_singleton
    if _loader_singleton is None:
        with _loader_lock:
            if _loader_singleton is None:
                cfg = get_config()
                _loader_singleton = CsvLoader(cfg.notes_path, cfg.fees_path)
    return _loader_singleton


def _get_full_pool() -> list[str]:
    """从 case_notes.csv 提取全量唯一住院号. 进程级缓存."""
    global _full_pool_cache
    if _full_pool_cache is None:
        with _loader_lock:
            if _full_pool_cache is None:
                loader = _get_loader()
                df = loader.all_notes()
                if "住院号" in df.columns:
                    ids = df["住院号"].astype(str).str.strip().unique().tolist()
                else:
                    ids = []
                _full_pool_cache = sorted([i for i in ids if i and i.lower() != "nan"])
                logger.info("full pool 缓存: %d 患者", len(_full_pool_cache))
    return _full_pool_cache


@router.get("/sample", response_model=PatientSampleResponse)
def sample_patients(
    n: int = Query(1, ge=1, le=20, description="抽样数量"),
    pool: str = Query("pilot", pattern="^(pilot|full)$", description="pilot=50 人池, full=全量"),
) -> PatientSampleResponse:
    """从 pilot 50 池或全量 ~10 万池随机抽 N 个患者 ID."""
    cfg = get_config()
    if pool == "pilot":
        if not cfg.pilot_roster_path.exists():
            raise HTTPException(
                status_code=400,
                detail=f"pilot 名单不存在: {cfg.pilot_roster_path}. 请先 javert init",
            )
        ids = load_pilot_roster(cfg.pilot_roster_path)
    else:
        ids = _get_full_pool()

    if not ids:
        raise HTTPException(status_code=400, detail=f"{pool} 池为空")

    n = min(n, len(ids))
    chosen = random.sample(ids, n)
    return PatientSampleResponse(patient_ids=chosen, pool=pool, pool_size=len(ids))


@router.get("/pools")
def get_pools() -> dict:
    """返回 pilot / full 池大小 (仅 pilot 廉价, full 触发 csv 加载)."""
    cfg = get_config()
    pilot_size = 0
    if cfg.pilot_roster_path.exists():
        try:
            pilot_size = len(load_pilot_roster(cfg.pilot_roster_path))
        except Exception:
            pilot_size = -1
    return {
        "pilot": pilot_size,
        "full": len(_full_pool_cache) if _full_pool_cache is not None else None,
    }


@router.get("/{patient_id}/summary", response_model=PatientSummary)
def get_patient_summary(patient_id: str) -> PatientSummary:
    """单个患者的 notes 数 / fees 数 / 总费用. 用于前端展示概览."""
    loader = _get_loader()
    notes_df = loader.get_notes(patient_id)
    fees_df = loader.get_fees(patient_id)
    fee_sum: Optional[float] = None
    if "det_item_fee_sumamt" in fees_df.columns:
        try:
            fee_sum = float(fees_df["det_item_fee_sumamt"].sum())
        except Exception:
            fee_sum = None
    return PatientSummary(
        patient_id=patient_id,
        notes_count=int(len(notes_df)),
        fees_count=int(len(fees_df)),
        fee_sum=fee_sum,
    )
