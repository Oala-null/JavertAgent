# -*- coding: utf-8 -*-
"""ResultPersister — 写 SQLite + 立即试推 142 + mark 同步状态.

设计 (跟 zadig_agent ResultPersister 模式一致):
  1. 本地 SQLite 是 source-of-truth, write 必须成功 (失败抛异常)
  2. 立即试推 142, 成功 → mark_synced; 失败 → mark_sync_failed (留给后台 worker retry)
  3. 142 写入失败永不阻塞主流程, 只 warn

调用约定:
  - 单次调用 (web / dry-run): persist_one(result, rule, ...) — 内部管理 SqliteStore
  - 批量调用 (run --pilot): 传入已打开的 sqlite_store 复用连接
"""

from __future__ import annotations

import logging

from javert.audit.result import AuditResult
from javert.audit.rule import Rule
from javert.config import get_config

from .audit_store import SqliteStore
from .sqlserver_store import get_sqlserver_store

logger = logging.getLogger("javert.store.result_persister")

DRIFT_TAG = "漂移防护(历史曾判V)"


def _expert_rejected_violation(run_id: str, sql_enabled: bool) -> bool:
    """老 V 是否被专家 latest review 驳回 (review_verdict=='C', 即专家已裁定非违规).

    驳回 → 新 C 是修正而非漂移, 放行 (不拦截). SQL 不可用/异常 → False (无豁免, 照常拦).
    只认 'C' (专家明确判净) 为驳回; 'I' (存疑) 不算 — 落 I 与专家立场一致, 无害且更保守.
    """
    if not sql_enabled:
        return False
    try:
        reviews = get_sqlserver_store().list_reviews_for_run(run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("drift guard 驳回豁免查询失败 run_id=%s: %s (照常拦)", run_id, exc)
        return False
    return any(r.is_latest and r.review_verdict == "C" for r in reviews)


def _apply_drift_guard(result: AuditResult, sqlite_store: SqliteStore, sql_enabled: bool) -> None:
    """重跑漂移防护 (recover-deterministic-recall D4): 老 V 新 C → 就地改判 I + 标签.

    只升到 INCONCLUSIVE (绝不恢复 V); 历史行不改写; 专家已驳回的老 V 放行 C.
    """
    # 结构化结果的旧 verdict 必须与双轴投影一致；漂移比较交由 shadow 报告，
    # 不得在写库前把 CLEAN 就地改 I，制造自相矛盾的 eligibility_json。
    if result.eligibility_evaluation is not None or result.verdict != "CLEAN":
        return
    prior = sqlite_store.find_by_rule_patient_latest(result.rule_id, result.patient_id)
    if prior is None or prior.verdict != "VIOLATION":
        return
    if _expert_rejected_violation(prior.run_id, sql_enabled):
        return
    result.verdict = "INCONCLUSIVE"
    result.gate_tag = result.gate_tag or DRIFT_TAG
    result.reasoning = (
        f"{result.reasoning}\n[{DRIFT_TAG}: 历史最新 (run={prior.run_id}) 判 VIOLATION, "
        f"本次重跑判 CLEAN, 落 INCONCLUSIVE 待专家裁定 (只升 I 不复活 V)]"
    ).strip()


def persist_one(
    result: AuditResult,
    rule: Rule | None = None,
    *,
    triggered_by: str = "cli",
    sqlite_store: SqliteStore | None = None,
    batch_tag: str | None = None,
) -> dict:
    """单条 audit 持久化.

    Args:
        result: AuditResult
        rule: 可选, 用于 142 双写的 yaml snapshot
        triggered_by: "cli-dry-run" / "cli-run" / "web" / "heartbeat"
        sqlite_store: 复用已开的连接 (批量场景); None = 内部新开

    Returns:
        {
            "sqlite": True,                 # 本地写入永远成功 (失败会抛)
            "sqlserver_142": bool,          # 立即推 142 是否成功
            "sync_state": "synced" | "pending" | "skipped",
                # synced: 已立即推到 142 + mark
                # pending: 142 不可达, 留给后台 worker retry
                # skipped: sql_enabled=false, 不会推
        }
    """
    cfg = get_config()
    # batch_tag 优先级: 显式参数 > config (env JAVERT_BATCH_TAG)
    tag = batch_tag if batch_tag is not None else cfg.batch_tag
    own_store = sqlite_store is None
    if own_store:
        sqlite_store = SqliteStore(cfg.audit_db_path)
        sqlite_store.init_schema()

    try:
        # 0. 重跑漂移防护 (recover-deterministic-recall): 老 V 新 C → 就地改判 I (写前).
        if str(cfg.drift_guard).lower() != "off":
            _apply_drift_guard(result, sqlite_store, cfg.sql_enabled)

        # 1. 本地写 (source-of-truth)
        sqlite_store.write(result, batch_tag=tag)

        # 2. 立即试推 142
        sql142 = get_sqlserver_store()
        if not cfg.sql_enabled:
            return {"sqlite": True, "sqlserver_142": False, "sync_state": "skipped"}

        ok = sql142.write_audit(result, rule, triggered_by=triggered_by, batch_tag=tag)
        if ok:
            sqlite_store.mark_synced(result.run_id)
            return {"sqlite": True, "sqlserver_142": True, "sync_state": "synced"}
        else:
            sqlite_store.mark_sync_failed(
                result.run_id,
                "142 立即推失败: Engine/连接/写入异常 (后台 worker 将 retry)",
            )
            logger.warning(
                "142 立即推失败 run_id=%s, 已留待后台 retry", result.run_id,
            )
            return {"sqlite": True, "sqlserver_142": False, "sync_state": "pending"}

    finally:
        if own_store:
            sqlite_store.close()
