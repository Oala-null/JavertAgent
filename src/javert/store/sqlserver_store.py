# -*- coding: utf-8 -*-
"""SqlServerStore — 142 SQL Server 双写归档层.

连接 / Engine 管理模式拷贝自 zadig_agent v2.10.1 src/infra/db.py:
  - SQLAlchemy + pyodbc + ODBC Driver 17
  - Engine 单例 (lazy init), 连接池 + pool_pre_ping
  - before_cursor_execute hook 强制 NVARCHAR (中文兼容)
  - 优雅降级: 依赖未装 / 连接失败 → 返回 None / warn

本地 SQLite (audit_store.SqliteStore) 是 source-of-truth, 142 是归档副本.
任何 142 写入失败仅 warn 不阻塞主流程.

环境变量覆盖前缀: `JAVERT_SQL_*` (host / port / user / password / database / driver).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from javert.audit.result import AuditResult, Evidence, ToolCall
from javert.audit.rule import Rule
from javert.config import PROJECT_ROOT, JavertConfig, get_config
from javert.oncology.contracts import EligibilityEvaluation
from javert.promises.models import PromiseTrace

from .models import (
    AuditLogRecord,
    DashboardStats,
    HistoricalRun,
    PatientSidebarItem,
    ReviewRecord,
    ReviewerDrillRow,
    ReviewerLeaderRow,
    RuleAgreementRow,
    RunWithReviews,
    SinceLastLoginStats,
    User,
)

logger = logging.getLogger("javert.store.sqlserver_store")


class DuplicateUsernameError(Exception):
    """`javert_users.username` UNIQUE 约束冲突的类型化异常."""


_DDL_RELATIVE = "scripts/sql/create_javert_tables.sql"
_GO_BATCH_PATTERN = re.compile(r"^\s*GO\s*$", re.MULTILINE)


class SqlServerStore:
    """142 SQL Server 双写归档.

    用法:
        store = SqlServerStore()
        store.init_schema()                # 首次执行 DDL (幂等)
        store.write_audit(result, rule)    # 单次审计后双写, 失败只 warn
    """

    def __init__(self, config: JavertConfig | None = None):
        self.config = config or get_config()
        self._engine = None
        self._engine_initialized = False

    # =========================================================
    # Engine 管理
    # =========================================================
    def _build_url(self) -> str:
        from urllib.parse import quote_plus

        params = quote_plus(
            f"DRIVER={{{self.config.sql_driver}}};"
            f"SERVER={self.config.sql_host},{self.config.sql_port};"
            f"DATABASE={self.config.sql_database};"
            f"UID={self.config.sql_user};"
            f"PWD={self.config.sql_password};"
            f"TrustServerCertificate=yes;"
            f"Encrypt=no;"  # 142 老 TLS: Driver18 默认强制加密会 Login timeout (data-hub 实测)
            f"Connection Timeout=10;"
        )
        return f"mssql+pyodbc:///?odbc_connect={params}"

    def get_engine(self):
        """惰性创建 Engine 单例. 依赖缺失 / 配置关闭 / 连接失败时返回 None."""
        if self._engine_initialized:
            return self._engine

        if not self.config.sql_enabled:
            logger.info("142 双写已被 sql_enabled=false 关闭")
            self._engine_initialized = True
            return None

        if not self.config.sql_password:
            logger.warning(
                "JAVERT_SQL_PASSWORD 未设置 (凭证已移出源码) — 142 双写降级不可用; "
                "source .env 后重启可恢复"
            )
            self._engine_initialized = True
            return None

        try:
            import pyodbc  # noqa: F401
            from sqlalchemy import create_engine, event

            url = self._build_url()
            self._engine = create_engine(
                url,
                pool_size=self.config.sql_pool_size,
                max_overflow=self.config.sql_max_overflow,
                pool_timeout=self.config.sql_pool_timeout,
                pool_pre_ping=True,
                fast_executemany=True,
            )

            # 中文兼容: 强制把 *字符串* 参数当作 NVARCHAR 发送, 避免变 ? ;
            # datetime / int / float / None 用 None 占位 (driver auto-detect),
            # 之前盲目全部 NVARCHAR 会导致 datetime 被 str-roundtrip 丢精度,
            # 触发 watcher fetch_runs_since 死循环 (created_at > 截位的 last_seen).
            @event.listens_for(self._engine, "before_cursor_execute")
            def _use_nvarchar(conn, cursor, statement, parameters, context, executemany):
                if executemany:
                    return
                if isinstance(parameters, dict):
                    sizes = [
                        (pyodbc.SQL_WVARCHAR, 0, 0) if isinstance(v, str) else None
                        for v in parameters.values()
                    ]
                    if any(s is not None for s in sizes):
                        cursor.setinputsizes(sizes)
                elif isinstance(parameters, (list, tuple)) and parameters:
                    sizes = [
                        (pyodbc.SQL_WVARCHAR, 0, 0) if isinstance(v, str) else None
                        for v in parameters
                    ]
                    if any(s is not None for s in sizes):
                        cursor.setinputsizes(sizes)

            self._engine_initialized = True
            logger.info(
                "SQL Server Engine 已创建: %s:%s/%s",
                self.config.sql_host,
                self.config.sql_port,
                self.config.sql_database,
            )
            return self._engine

        except ImportError as e:
            logger.warning("SQL Server 依赖未安装 (pyodbc/sqlalchemy): %s — 142 双写不可用", e)
            self._engine_initialized = True
            return None
        except Exception as e:
            logger.error("创建 SQL Server Engine 失败: %s", e)
            self._engine_initialized = True
            return None

    def health_check(self) -> dict:
        """返回 142 连通状态. 不抛异常."""
        engine = self.get_engine()
        if engine is None:
            return {"sql_server": False, "error": "Engine 不可用 (依赖缺失/配置关闭/连接失败)"}
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                return {
                    "sql_server": True,
                    "host": self.config.sql_host,
                    "port": self.config.sql_port,
                    "database": self.config.sql_database,
                }
        except Exception as e:
            return {"sql_server": False, "error": str(e)}

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            logger.info("SQL Server Engine 已关闭")
        self._engine = None
        self._engine_initialized = False

    # =========================================================
    # DDL 执行 (幂等)
    # =========================================================
    def init_schema(self, ddl_path: Path | None = None) -> bool:
        """执行 DDL 文件 (按 GO 拆分批次). 返回是否成功."""
        engine = self.get_engine()
        if engine is None:
            logger.warning("init_schema: Engine 不可用, 跳过")
            return False

        if ddl_path is None:
            ddl_path = PROJECT_ROOT / _DDL_RELATIVE
        if not ddl_path.exists():
            logger.error("DDL 文件不存在: %s", ddl_path)
            return False

        sql_text = ddl_path.read_text(encoding="utf-8")
        batches = [b.strip() for b in _GO_BATCH_PATTERN.split(sql_text) if b.strip()]

        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                for i, batch in enumerate(batches):
                    try:
                        conn.execute(text(batch))
                    except Exception as e:
                        # 验证语句 / PRINT 失败不阻塞核心 DDL
                        logger.warning("DDL batch #%d 执行警告: %s", i, e)
                conn.commit()
            logger.info("init_schema: %d 批次 DDL 已执行", len(batches))
            return True
        except Exception as e:
            logger.error("init_schema 失败: %s", e)
            return False

    # =========================================================
    # 写入: AuditResult → javert_audit_runs
    # =========================================================
    def write_audit(
        self,
        result: AuditResult,
        rule: Rule | None = None,
        *,
        rule_yaml_text: str | None = None,
        triggered_by: str = "cli",
        batch_tag: str | None = None,
    ) -> bool:
        """单次 AuditResult 写入 142. 失败只 warn 不抛.

        Args:
            result: 审计结果 (run_id 业务唯一键)
            rule: 规则对象 (用于 status + yaml snapshot 回退)
            rule_yaml_text: 显式 raw yaml 文本 (优先级高于从文件读)
            triggered_by: web / cli-dry-run / cli-run

        Returns:
            是否成功写入 (Engine 不可用 / 已存在同 run_id / 异常 → False)
        """
        engine = self.get_engine()
        if engine is None:
            return False

        evidence_json = json.dumps(
            [e.model_dump() for e in result.evidence],
            ensure_ascii=False,
        )
        tool_calls_json = json.dumps(
            [tc.model_dump() for tc in result.tool_calls],
            ensure_ascii=False,
        )
        eligibility_json = (
            json.dumps(
                result.eligibility_evaluation.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
            if result.eligibility_evaluation is not None
            else None
        )
        promise_trace_json = (
            json.dumps(
                result.promise_trace.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            )
            if result.promise_trace is not None
            else None
        )
        AuditResult.model_validate(result.model_dump())

        # rule yaml snapshot: 优先用显式 text, 其次从 rules_dir 读, 退路 model_dump
        snapshot_text: Optional[str] = rule_yaml_text
        rule_status: Optional[str] = None
        if rule is not None:
            rule_status = rule.status
            if snapshot_text is None:
                yaml_path = self.config.rules_path / f"{rule.rule_id}.yaml"
                if yaml_path.exists():
                    try:
                        snapshot_text = yaml_path.read_text(encoding="utf-8")
                    except Exception:
                        snapshot_text = None
                if snapshot_text is None:
                    try:
                        snapshot_text = json.dumps(
                            rule.model_dump(), ensure_ascii=False, indent=2
                        )
                    except Exception:
                        snapshot_text = None
        elif snapshot_text is None:
            # 心跳回灌等场景 (rule=None): 仍尝试根据 result.rule_id 读 yaml 兜底
            yaml_path = self.config.rules_path / f"{result.rule_id}.yaml"
            if yaml_path.exists():
                try:
                    snapshot_text = yaml_path.read_text(encoding="utf-8")
                except Exception:
                    snapshot_text = None

        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                # 业务唯一键 run_id 去重 (避免重写)
                existing = conn.execute(
                    text("SELECT 1 FROM javert_audit_runs WHERE run_id = :rid"),
                    {"rid": result.run_id},
                ).fetchone()
                if existing:
                    logger.info("142 已有 run_id=%s, 跳过 INSERT", result.run_id)
                    return True

                conn.execute(
                    text(
                        """
                        INSERT INTO javert_audit_runs (
                            run_id, rule_id, patient_id, verdict, confidence,
                            reasoning, evidence_json, tool_calls_json,
                            eligibility_json, promise_trace_json, anchors_json,
                            duration_ms, model, rule_yaml_snapshot, rule_status,
                            triggered_by, started_at, batch_tag, gate_tag
                        ) VALUES (
                            :run_id, :rule_id, :patient_id, :verdict, :confidence,
                            :reasoning, :evidence_json, :tool_calls_json,
                            :eligibility_json, :promise_trace_json, :anchors_json,
                            :duration_ms, :model, :rule_yaml_snapshot, :rule_status,
                            :triggered_by, :started_at, :batch_tag, :gate_tag
                        )
                        """
                    ),
                    {
                        "run_id": result.run_id,
                        "rule_id": result.rule_id,
                        "patient_id": result.patient_id,
                        "verdict": result.verdict,
                        "confidence": float(result.confidence),
                        "reasoning": result.reasoning or "",
                        "evidence_json": evidence_json,
                        "tool_calls_json": tool_calls_json,
                        "eligibility_json": eligibility_json,
                        "promise_trace_json": promise_trace_json,
                        "anchors_json": result.anchors_json,
                        "duration_ms": int(result.duration_ms),
                        "model": result.model or "",
                        "rule_yaml_snapshot": snapshot_text,
                        "rule_status": rule_status,
                        "triggered_by": triggered_by,
                        "started_at": result.started_at,
                        "batch_tag": batch_tag,
                        "gate_tag": getattr(result, "gate_tag", "") or "",
                    },
                )
                conn.commit()
            logger.info(
                "142 已归档: rule=%s verdict=%s promise=%s",
                result.rule_id,
                result.verdict,
                int(result.promise_trace is not None),
            )
            return True
        except Exception as e:
            logger.warning("142 写入失败 error_type=%s", type(e).__name__)
            return False

    # =========================================================
    # 查询 (前端 / 报表用)
    # =========================================================
    def list_recent_runs(
        self,
        rule_id: str | None = None,
        patient_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """按时间倒序拉最近 N 条审计. 142 不可达时返回空列表."""
        engine = self.get_engine()
        if engine is None:
            return []
        try:
            from sqlalchemy import text
            where_parts = []
            params: dict = {"limit": int(limit)}
            if rule_id is not None:
                where_parts.append("rule_id = :rule_id")
                params["rule_id"] = rule_id
            if patient_id is not None:
                where_parts.append("patient_id = :patient_id")
                params["patient_id"] = patient_id
            where_clause = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
            # 同 fetch_runs_since 注释: TOP (N) 内联避 NVARCHAR hook 把 int coerce 成字符串
            limit_int = max(1, min(int(limit), 1000))
            params.pop("limit", None)
            sql = (
                f"SELECT TOP ({limit_int}) "
                "id, run_id, rule_id, patient_id, verdict, confidence, "
                "duration_ms, model, triggered_by, started_at, created_at "
                "FROM javert_audit_runs"
                + where_clause
                + " ORDER BY created_at DESC"
            )
            with engine.connect() as conn:
                cur = conn.execute(text(sql), params)
                rows = cur.fetchall()
                cols = list(cur.keys())
                return [dict(zip(cols, r)) for r in rows]
        except Exception as e:
            logger.warning("142 查询失败: %s", e)
            return []

    def find_audit_by_run_id(self, run_id: str) -> AuditResult | None:
        """读取完整 SQL Server 归档结果；旧行扩展 JSON 为 NULL 时向后兼容."""
        engine = self.get_engine()
        if engine is None:
            return None
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT run_id, rule_id, patient_id, verdict, confidence, "
                        "reasoning, evidence_json, tool_calls_json, duration_ms, model, "
                        "started_at, gate_tag, eligibility_json, promise_trace_json, anchors_json "
                        "FROM javert_audit_runs WHERE run_id = :rid"
                    ),
                    {"rid": run_id},
                ).fetchone()
            if row is None:
                return None
            eligibility = (
                EligibilityEvaluation.model_validate_json(row[12])
                if row[12]
                else None
            )
            promise_trace = (
                PromiseTrace.model_validate_json(row[13])
                if len(row) > 13 and row[13]
                else None
            )
            return AuditResult(
                run_id=row[0],
                rule_id=row[1],
                patient_id=row[2],
                verdict=row[3],
                confidence=float(row[4] or 0.0),
                reasoning=row[5] or "",
                evidence=[
                    Evidence.model_validate(item)
                    for item in json.loads(row[6] or "[]")
                ],
                tool_calls=[
                    ToolCall.model_validate(item)
                    for item in json.loads(row[7] or "[]")
                ],
                duration_ms=int(row[8] or 0),
                model=row[9] or "",
                started_at=row[10],
                gate_tag=row[11] or "",
                eligibility_evaluation=eligibility,
                promise_trace=promise_trace,
                anchors_json=(row[14] if len(row) > 14 else None),
            )
        except Exception as e:
            logger.warning("find_audit_by_run_id 失败 run_id=%s: %s", run_id, e)
            return None


    # =========================================================
    # 工作台: 用户 CRUD
    # =========================================================
    def create_user(
        self,
        username: str,
        pw_hash: str,
        display_name: str | None = None,
    ) -> int:
        """新增 javert_users 行, 返回 id. 重名抛 DuplicateUsernameError."""
        engine = self.get_engine()
        if engine is None:
            raise RuntimeError("SQL Server Engine 不可用")
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        try:
            with engine.begin() as conn:
                row = conn.execute(
                    text(
                        "INSERT INTO javert_users (username, pw_hash, display_name) "
                        "OUTPUT INSERTED.id VALUES (:username, :pw_hash, :display_name)"
                    ),
                    {
                        "username": username,
                        "pw_hash": pw_hash,
                        "display_name": display_name,
                    },
                ).fetchone()
                if row is None or row[0] is None:
                    raise RuntimeError("INSERT javert_users 未返回 id")
                return int(row[0])
        except IntegrityError as e:
            msg = str(e).lower()
            if "unique" in msg or "uq_javert_users_username" in msg or "2627" in msg:
                raise DuplicateUsernameError(f"username 已存在: {username}") from e
            raise

    def get_user_by_username(self, username: str) -> User | None:
        engine = self.get_engine()
        if engine is None:
            return None
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, username, display_name, created_at, last_login "
                        "FROM javert_users WHERE username = :username"
                    ),
                    {"username": username},
                ).fetchone()
                if row is None:
                    return None
                return User(
                    id=int(row[0]),
                    username=row[1],
                    display_name=row[2],
                    created_at=row[3],
                    last_login=row[4],
                )
        except Exception as e:
            logger.warning("get_user_by_username 失败 %s: %s", username, e)
            return None

    def get_user_by_id(self, user_id: int) -> User | None:
        engine = self.get_engine()
        if engine is None:
            return None
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT id, username, display_name, created_at, last_login "
                        "FROM javert_users WHERE id = :id"
                    ),
                    {"id": int(user_id)},
                ).fetchone()
                if row is None:
                    return None
                return User(
                    id=int(row[0]),
                    username=row[1],
                    display_name=row[2],
                    created_at=row[3],
                    last_login=row[4],
                )
        except Exception as e:
            logger.warning("get_user_by_id 失败 %s: %s", user_id, e)
            return None

    def update_pw_hash(self, user_id: int, new_hash: str) -> bool:
        """改密 — 用于 /account/password 修改 + mssql-user reset-password."""
        engine = self.get_engine()
        if engine is None:
            return False
        from sqlalchemy import text
        try:
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE javert_users SET pw_hash = :h WHERE id = :uid"),
                    {"h": new_hash, "uid": int(user_id)},
                )
            return True
        except Exception as e:
            logger.warning("update_pw_hash 失败 uid=%s: %s", user_id, e)
            return False

    def get_pw_hash(self, username: str) -> str | None:
        """单独取 pw_hash (用于 bcrypt verify, 避免 User 模型携带敏感字段)."""
        engine = self.get_engine()
        if engine is None:
            return None
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT pw_hash FROM javert_users WHERE username = :u"),
                    {"u": username},
                ).fetchone()
                return row[0] if row else None
        except Exception as e:
            logger.warning("get_pw_hash 失败 %s: %s", username, e)
            return None

    def update_last_login(self, user_id: int) -> datetime | None:
        """SELECT 老 last_login, UPDATE 为 now, 返回老值 (login route 用来 stash session prev_last_login)."""
        engine = self.get_engine()
        if engine is None:
            return None
        from sqlalchemy import text
        try:
            with engine.begin() as conn:
                old = conn.execute(
                    text("SELECT last_login FROM javert_users WHERE id = :id"),
                    {"id": int(user_id)},
                ).fetchone()
                prev = old[0] if old else None
                conn.execute(
                    text("UPDATE javert_users SET last_login = SYSUTCDATETIME() WHERE id = :id"),
                    {"id": int(user_id)},
                )
                return prev
        except Exception as e:
            logger.warning("update_last_login 失败 %s: %s", user_id, e)
            return None

    # =========================================================
    # 工作台: audit_logs 行为 trail
    # =========================================================
    def log_action(
        self,
        user_id: int | None,
        action: str,
        target_id: str | None = None,
        payload: dict | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        engine = self.get_engine()
        if engine is None:
            return
        from sqlalchemy import text
        payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO javert_audit_logs "
                        "(user_id, action, target_id, payload_json, ip, user_agent) "
                        "VALUES (:user_id, :action, :target_id, :payload_json, :ip, :user_agent)"
                    ),
                    {
                        "user_id": int(user_id) if user_id is not None else None,
                        "action": action,
                        "target_id": target_id,
                        "payload_json": payload_json,
                        "ip": ip,
                        "user_agent": user_agent,
                    },
                )
        except Exception as e:
            logger.warning("log_action 失败 action=%s: %s", action, e)

    # =========================================================
    # 工作台: 三态 review 提交 (insert-only + is_latest)
    # =========================================================
    def submit_review(
        self,
        run_id: str,
        user_id: int,
        verdict: str,
        comment: str | None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> ReviewRecord:
        """事务: UPDATE 老 is_latest=0 → INSERT 新行 is_latest=1 → INSERT audit_log."""
        engine = self.get_engine()
        if engine is None:
            raise RuntimeError("SQL Server Engine 不可用")
        if verdict not in ("V", "I", "C"):
            raise ValueError(f"verdict 非法: {verdict}")
        from sqlalchemy import text
        with engine.begin() as conn:
            # 1. run_id 必须存在 + 顺便取 patient_id + rule_id (denormalize 进 vio_review)
            #    + verdict (AI 裁决, SSE 增量计数判定是否落在当前 filter 命中集)
            run_row = conn.execute(
                text("SELECT patient_id, rule_id, verdict FROM javert_audit_runs WHERE run_id = :rid"),
                {"rid": run_id},
            ).fetchone()
            if not run_row:
                raise LookupError(f"run_id 不存在: {run_id}")
            run_patient_id, run_rule_id, run_audit_verdict = run_row[0], run_row[1], run_row[2]
            # 1c. 本次提交前该 run 是否已有任意专家的 latest review (全局首评判定).
            #     在降级老行之前查 — 含本 user 旧行: 本 user 改判时 prior_cnt>=1 → 非首评.
            #     SSE 全局计数只在首评 +1, 避免改判 / 他人二次审重复计数.
            prior_cnt_row = conn.execute(
                text("SELECT COUNT(*) FROM javert_vio_review WHERE run_id = :rid AND is_latest = 1"),
                {"rid": run_id},
            ).fetchone()
            run_first_review = (int(prior_cnt_row[0]) if prior_cnt_row else 0) == 0
            # 1b. 取 username (denormalize)
            u_row = conn.execute(
                text("SELECT username FROM javert_users WHERE id = :uid"),
                {"uid": int(user_id)},
            ).fetchone()
            uname = u_row[0] if u_row else None
            # 2. 查老 latest (取 verdict 作 previous_verdict 写入 log)
            prev_row = conn.execute(
                text(
                    "SELECT review_verdict FROM javert_vio_review "
                    "WHERE run_id = :rid AND user_id = :uid AND is_latest = 1"
                ),
                {"rid": run_id, "uid": int(user_id)},
            ).fetchone()
            previous_verdict = prev_row[0] if prev_row else None
            # 3. 老行降级
            if previous_verdict is not None:
                conn.execute(
                    text(
                        "UPDATE javert_vio_review SET is_latest = 0 "
                        "WHERE run_id = :rid AND user_id = :uid AND is_latest = 1"
                    ),
                    {"rid": run_id, "uid": int(user_id)},
                )
            # 4. 新行 (含 denormalized patient_id / rule_id / username)
            inserted = conn.execute(
                text(
                    "INSERT INTO javert_vio_review "
                    "(run_id, user_id, patient_id, rule_id, username, "
                    " review_verdict, comment) "
                    "OUTPUT INSERTED.id, INSERTED.created_at "
                    "VALUES (:rid, :uid, :pid, :ruleid, :uname, :verdict, :comment)"
                ),
                {
                    "rid": run_id,
                    "uid": int(user_id),
                    "pid": run_patient_id,
                    "ruleid": run_rule_id,
                    "uname": uname,
                    "verdict": verdict,
                    "comment": comment,
                },
            ).fetchone()
            if inserted is None or inserted[0] is None:
                raise RuntimeError("INSERT javert_vio_review 未返回 id")
            new_id = int(inserted[0])
            created_at = inserted[1]
            # 5. audit_log (在同一事务里)
            action = "review_update" if previous_verdict else "review_submit"
            payload_json = json.dumps(
                {
                    "verdict": verdict,
                    "comment": comment,
                    "previous_verdict": previous_verdict,
                },
                ensure_ascii=False,
            )
            conn.execute(
                text(
                    "INSERT INTO javert_audit_logs "
                    "(user_id, action, target_id, payload_json, ip, user_agent) "
                    "VALUES (:user_id, :action, :target_id, :payload_json, :ip, :user_agent)"
                ),
                {
                    "user_id": int(user_id),
                    "action": action,
                    "target_id": run_id,
                    "payload_json": payload_json,
                    "ip": ip,
                    "user_agent": user_agent,
                },
            )
        return ReviewRecord(
            id=new_id,
            run_id=run_id,
            user_id=int(user_id),
            review_verdict=verdict,  # type: ignore[arg-type]
            comment=comment,
            created_at=created_at,
            is_latest=True,
            patient_id=run_patient_id,
            rule_id=run_rule_id,
            previous_verdict=previous_verdict,
            run_first_review=run_first_review,
            run_audit_verdict=run_audit_verdict,
        )

    # =========================================================
    # 工作台: 读路径
    # =========================================================
    def list_patients_with_violations(
        self,
        filter_mode: str,
    ) -> list[PatientSidebarItem]:
        """sidebar — 50+ 病人, V/I/C 计数 (按 (rule_id, patient_id) 取 latest 去重),
        团队已审条数 (任意专家, 不分人头).

        filter_mode: 'v_and_i' / 'v_only' / 'i_only' / 'all'

        注: audit_runs 保留全部历史 (同 rule 重跑会产生多行), 工作台只看每条规则的
        最新一次 → 用 ROW_NUMBER() PARTITION BY rule_id, patient_id ORDER BY created_at DESC.

        reviewed_count 取全局口径 (任意专家的 latest review 都算): 一条 run 只要被任一专家
        审过就计 1 (COUNT DISTINCT run_id 去重多专家). 之前按当前 user 过滤会让其他账号永远
        看到 0/x — 专家审完别人卡片不更新, 故改全局.
        """
        engine = self.get_engine()
        if engine is None:
            return []
        from sqlalchemy import text
        # 病人 + 各 verdict 计数 (latest 去重) + 该 patient 的最新 batch_tag
        sql_counts = """
            WITH latest AS (
                SELECT patient_id, rule_id, verdict, run_id, batch_tag, created_at,
                       ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC) AS rn
                FROM javert_audit_runs
            ),
            patient_tag AS (
                SELECT patient_id, batch_tag,
                       ROW_NUMBER() OVER (PARTITION BY patient_id ORDER BY created_at DESC) AS rn_tag
                FROM javert_audit_runs
            )
            SELECT latest.patient_id,
                   SUM(CASE WHEN verdict = N'VIOLATION' THEN 1 ELSE 0 END) AS v_count,
                   SUM(CASE WHEN verdict = N'INCONCLUSIVE' THEN 1 ELSE 0 END) AS i_count,
                   SUM(CASE WHEN verdict = N'CLEAN' THEN 1 ELSE 0 END) AS c_count,
                   MAX(CASE WHEN pt.rn_tag = 1 THEN pt.batch_tag ELSE NULL END) AS batch_tag,
                   MAX(latest.created_at) AS updated_at
            FROM latest
            LEFT JOIN patient_tag pt
                   ON pt.patient_id = latest.patient_id AND pt.rn_tag = 1
            WHERE latest.rn = 1
            GROUP BY latest.patient_id
        """
        # 团队已审 latest review count by patient + verdict (任意专家, latest run 去重).
        # COUNT(DISTINCT run_id): 同一 run 被多个专家审只算一次 (全局口径, 不分人头).
        sql_team_reviewed = """
            WITH latest AS (
                SELECT patient_id, rule_id, verdict, run_id,
                       ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC) AS rn
                FROM javert_audit_runs
            )
            SELECT l.patient_id, l.verdict, COUNT(DISTINCT l.run_id) AS n
            FROM latest l
            INNER JOIN javert_vio_review rv
                    ON rv.run_id = l.run_id AND rv.is_latest = 1
            WHERE l.rn = 1
            GROUP BY l.patient_id, l.verdict
        """
        try:
            with engine.connect() as conn:
                counts_rows = conn.execute(text(sql_counts)).fetchall()
                reviewed_rows = conn.execute(text(sql_team_reviewed)).fetchall()
        except Exception as e:
            logger.warning("list_patients_with_violations 失败: %s", e)
            return []

        # reviewed map: patient_id → {verdict: count}
        reviewed: dict[str, dict[str, int]] = {}
        for r in reviewed_rows:
            reviewed.setdefault(r[0], {})[r[1]] = int(r[2] or 0)

        out: list[PatientSidebarItem] = []
        for r in counts_rows:
            pid = r[0]
            v = int(r[1] or 0)
            i = int(r[2] or 0)
            c = int(r[3] or 0)
            batch_tag = r[4] if len(r) > 4 else None
            updated_at = r[5] if len(r) > 5 else None
            # filter 决定 sidebar 是否展示 + relevant_count 分母
            if filter_mode == "v_and_i":
                relevant = v + i
            elif filter_mode == "v_only":
                relevant = v
            elif filter_mode == "i_only":
                relevant = i
            else:  # 'all'
                relevant = v + i + c
            if filter_mode != "all" and relevant == 0:
                continue
            rmap = reviewed.get(pid, {})
            if filter_mode == "v_and_i":
                reviewed_n = rmap.get("VIOLATION", 0) + rmap.get("INCONCLUSIVE", 0)
            elif filter_mode == "v_only":
                reviewed_n = rmap.get("VIOLATION", 0)
            elif filter_mode == "i_only":
                reviewed_n = rmap.get("INCONCLUSIVE", 0)
            else:
                reviewed_n = sum(rmap.values())
            fully = relevant > 0 and reviewed_n >= relevant
            out.append(
                PatientSidebarItem(
                    patient_id=pid,
                    v_count=v,
                    i_count=i,
                    c_count=c,
                    reviewed_count=reviewed_n,
                    relevant_count=relevant,
                    fully_reviewed=fully,
                    batch_tag=batch_tag,
                    updated_at=updated_at,
                )
            )
        # sort: 新 batch (非空 + 排序值大) 置顶, 然后 v desc / i desc / pid asc
        # batch 排序 key: None → "0", "v1.2" → "v1.2", "v1.5" → "v1.5". 字典序逆 desc.
        out.sort(key=lambda x: (
            -(1 if x.batch_tag else 0),  # 有 tag 的优先
            x.batch_tag or "",            # tag 字典序 desc (后面再 reverse)
            -x.v_count,
            -x.i_count,
            x.patient_id,
        ))
        # 上面 batch_tag 是 asc, 把它转成 desc — 直接反向也可, 这里用 stable sort 两次
        out.sort(key=lambda x: (x.batch_tag or ""), reverse=True)
        out.sort(key=lambda x: (-(1 if x.batch_tag else 0), -x.v_count, -x.i_count, x.patient_id))
        return out

    def latest_verdict_rows(self, batch_tag: str | None = None) -> list[tuple[str, str, str]]:
        """每 (rule_id, patient_id) 取最新一条 → (rule_id, patient_id, verdict).

        跨患者统计 (add-cross-patient-stats) 的 latest 去重源. 复用与
        list_patients_with_violations / dashboard_stats 完全一致的 ROW_NUMBER CTE 口径,
        不另立 verdict filter. batch_tag 非空则只算该批次. 142 不可达 → 空列表.
        """
        engine = self.get_engine()
        if engine is None:
            return []
        from sqlalchemy import text
        tag_filter = "WHERE batch_tag = :tag" if batch_tag is not None else ""
        sql = f"""
            WITH latest AS (
                SELECT rule_id, patient_id, verdict,
                       ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC) AS rn
                FROM javert_audit_runs
                {tag_filter}
            )
            SELECT rule_id, patient_id, verdict FROM latest WHERE rn = 1
        """
        params = {"tag": batch_tag} if batch_tag is not None else {}
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(sql), params).fetchall()
        except Exception as e:
            logger.warning("latest_verdict_rows 失败: %s", e)
            return []
        return [(r[0], r[1], r[2]) for r in rows]

    def latest_batch_tag_for_patient(self, patient_id: str) -> str | None:
        """返回患者最新 audit run 的 batch_tag；查询失败/无行/NULL 均返回 None。"""
        engine = self.get_engine()
        if engine is None:
            return None
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT TOP (1) batch_tag FROM javert_audit_runs "
                        "WHERE patient_id = :pid ORDER BY created_at DESC, run_id DESC"
                    ),
                    {"pid": patient_id},
                ).fetchone()
            return str(row[0]) if row is not None and row[0] else None
        except Exception as exc:  # noqa: BLE001 — 原文 profile 是可选回退，不阻断默认源
            logger.warning(
                "latest_batch_tag_for_patient 失败 patient=%s error_type=%s",
                patient_id,
                type(exc).__name__,
            )
            return None

    def list_runs_for_patient(
        self,
        patient_id: str,
        filter_mode: str,
    ) -> list[RunWithReviews]:
        """patient_detail — 当前 patient 的 runs (按 filter), JOIN reviews + history.

        v0.7: 拉所有 audit_runs (含老 v1.0), Python 端按 rule_id 分组,
        每组 latest 作主显示, 其他作 history (v1.0 verdict + 当时的批注).
        filter 仍以 latest verdict 为准.
        """
        engine = self.get_engine()
        if engine is None:
            return []
        from sqlalchemy import text
        # 拉该 patient 所有 audit_runs (含老 v1.0, 含 latest v1.2 — 一次性)
        sql_runs = """
            SELECT run_id, rule_id, patient_id, verdict, confidence,
                   reasoning, evidence_json, tool_calls_json,
                   duration_ms, model, started_at, created_at, triggered_by, batch_tag,
                   gate_tag, eligibility_json, promise_trace_json
            FROM javert_audit_runs
            WHERE patient_id = :pid
            ORDER BY rule_id ASC, created_at DESC
        """
        sql_reviews = """
            SELECT rv.id, rv.run_id, rv.user_id, rv.review_verdict,
                   rv.comment, rv.created_at, rv.is_latest,
                   u.username, u.display_name
            FROM javert_vio_review rv
            INNER JOIN javert_audit_runs r ON r.run_id = rv.run_id
            INNER JOIN javert_users u ON u.id = rv.user_id
            WHERE r.patient_id = :pid AND rv.is_latest = 1
            ORDER BY rv.created_at DESC
        """
        try:
            with engine.connect() as conn:
                run_rows = conn.execute(text(sql_runs), {"pid": patient_id}).fetchall()
                rev_rows = conn.execute(text(sql_reviews), {"pid": patient_id}).fetchall()
        except Exception as e:
            logger.warning("list_runs_for_patient 失败 %s: %s", patient_id, e)
            return []

        # reviews_by_run map — 含老 v1.0 run 上的当时批注 (依然 is_latest=1)
        from collections import defaultdict
        reviews_by_run: dict[str, list[ReviewRecord]] = defaultdict(list)
        for rv in rev_rows:
            reviews_by_run[rv[1]].append(
                ReviewRecord(
                    id=int(rv[0]),
                    run_id=rv[1],
                    user_id=int(rv[2]),
                    review_verdict=rv[3],  # type: ignore[arg-type]
                    comment=rv[4],
                    created_at=rv[5],
                    is_latest=bool(rv[6]),
                    reviewer_username=rv[7],
                    reviewer_display_name=rv[8],
                )
            )

        # 按 rule_id 分组 — 因为 SQL ORDER BY rule_id, created_at DESC, 每组首行就是 latest
        by_rule: dict[str, list] = defaultdict(list)
        for r in run_rows:
            by_rule[r[1]].append(r)

        VERDICT_PASS = {
            "v_and_i": {"VIOLATION", "INCONCLUSIVE"},
            "v_only": {"VIOLATION"},
            "i_only": {"INCONCLUSIVE"},
            "all": {"VIOLATION", "INCONCLUSIVE", "CLEAN"},
        }
        pass_set = VERDICT_PASS.get(filter_mode, VERDICT_PASS["v_and_i"])

        out: list[RunWithReviews] = []
        for rule_id, runs in by_rule.items():
            latest = runs[0]
            if latest[3] not in pass_set:
                continue
            history_runs = []
            for h in runs[1:]:
                history_runs.append(
                    HistoricalRun(
                        run_id=h[0],
                        verdict=h[3],
                        confidence=float(h[4] or 0.0),
                        reasoning=h[5] or "",
                        batch_tag=h[13],
                        created_at=h[11],
                        reviews=reviews_by_run.get(h[0], []),
                        eligibility_evaluation=(
                            EligibilityEvaluation.model_validate_json(h[15])
                            if len(h) > 15 and h[15]
                            else None
                        ),
                        promise_trace=(
                            PromiseTrace.model_validate_json(h[16])
                            if len(h) > 16 and h[16]
                            else None
                        ),
                    )
                )
            out.append(
                RunWithReviews(
                    run_id=latest[0],
                    rule_id=latest[1],
                    patient_id=latest[2],
                    verdict=latest[3],
                    confidence=float(latest[4] or 0.0),
                    reasoning=latest[5] or "",
                    evidence_json=latest[6],
                    tool_calls_json=latest[7],
                    duration_ms=int(latest[8] or 0),
                    model=latest[9],
                    started_at=latest[10],
                    created_at=latest[11],
                    triggered_by=latest[12],
                    batch_tag=latest[13],
                    gate_tag=(latest[14] or "") if len(latest) > 14 else "",
                    eligibility_evaluation=(
                        EligibilityEvaluation.model_validate_json(latest[15])
                        if len(latest) > 15 and latest[15]
                        else None
                    ),
                    promise_trace=(
                        PromiseTrace.model_validate_json(latest[16])
                        if len(latest) > 16 and latest[16]
                        else None
                    ),
                    reviews=reviews_by_run.get(latest[0], []),
                    history=history_runs,
                )
            )
        # 排序: V 顶 -> I -> C
        out.sort(key=lambda x: (
            {"VIOLATION": 0, "INCONCLUSIVE": 1, "CLEAN": 2}.get(x.verdict, 3),
            x.rule_id,
        ))
        return out

    def fetch_anchors_for_patient(self, patient_id: str) -> dict[str, str]:
        """读该 patient 各 run 的 anchors_json 命中项目缓存 → {run_id: anchors_json}.

        anchors_json 列缺失 (未跑 migration) / 任何异常 → 返回 {} (渲染回退现算).
        非阻塞: 缓存是可选加速, 永不因此报错.
        """
        engine = self.get_engine()
        if engine is None:
            return {}
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT run_id, anchors_json FROM javert_audit_runs "
                        "WHERE patient_id = :pid AND anchors_json IS NOT NULL"
                    ),
                    {"pid": patient_id},
                ).fetchall()
                return {r[0]: r[1] for r in rows if r[1]}
        except Exception as e:  # noqa: BLE001 — 列缺失等; 静默回退现算
            logger.debug("fetch_anchors_for_patient miss patient=%s: %s", patient_id, e)
            return {}

    def list_reviews_for_run(self, run_id: str) -> list[ReviewRecord]:
        """单 run 的全部历史 review (含 is_latest=0)."""
        engine = self.get_engine()
        if engine is None:
            return []
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT rv.id, rv.run_id, rv.user_id, rv.review_verdict, "
                        "rv.comment, rv.created_at, rv.is_latest, "
                        "u.username, u.display_name "
                        "FROM javert_vio_review rv "
                        "INNER JOIN javert_users u ON u.id = rv.user_id "
                        "WHERE rv.run_id = :rid "
                        "ORDER BY rv.created_at DESC"
                    ),
                    {"rid": run_id},
                ).fetchall()
                return [
                    ReviewRecord(
                        id=int(r[0]),
                        run_id=r[1],
                        user_id=int(r[2]),
                        review_verdict=r[3],  # type: ignore[arg-type]
                        comment=r[4],
                        created_at=r[5],
                        is_latest=bool(r[6]),
                        reviewer_username=r[7],
                        reviewer_display_name=r[8],
                    )
                    for r in rows
                ]
        except Exception as e:
            logger.warning("list_reviews_for_run %s: %s", run_id, e)
            return []

    def get_since_last_login_stats(
        self,
        user_id: int,
        since: datetime | None,
    ) -> SinceLastLoginStats:
        """欢迎 banner — since=None 时返回全工作区计数."""
        engine = self.get_engine()
        if engine is None:
            return SinceLastLoginStats()
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                if since is None:
                    row = conn.execute(
                        text(
                            "SELECT COUNT(DISTINCT patient_id), "
                            "SUM(CASE WHEN verdict = N'VIOLATION' THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN verdict = N'INCONCLUSIVE' THEN 1 ELSE 0 END) "
                            "FROM javert_audit_runs"
                        )
                    ).fetchone()
                else:
                    row = conn.execute(
                        text(
                            "SELECT COUNT(DISTINCT patient_id), "
                            "SUM(CASE WHEN verdict = N'VIOLATION' THEN 1 ELSE 0 END), "
                            "SUM(CASE WHEN verdict = N'INCONCLUSIVE' THEN 1 ELSE 0 END) "
                            "FROM javert_audit_runs WHERE created_at > :since"
                        ),
                        {"since": since},
                    ).fetchone()
                new_patients = int(row[0] or 0) if row else 0
                new_v = int(row[1] or 0) if row else 0
                new_i = int(row[2] or 0) if row else 0
                last = conn.execute(
                    text(
                        "SELECT MAX(created_at) FROM javert_vio_review "
                        "WHERE user_id = :uid AND is_latest = 1"
                    ),
                    {"uid": int(user_id)},
                ).fetchone()
                last_review_at = last[0] if last and last[0] else None
                return SinceLastLoginStats(
                    new_patients=new_patients,
                    new_violations=new_v,
                    new_inconclusive=new_i,
                    last_review_at=last_review_at,
                )
        except Exception as e:
            logger.warning("get_since_last_login_stats 失败 uid=%s: %s", user_id, e)
            return SinceLastLoginStats()

    def fetch_runs_since(
        self,
        last_seen: datetime,
        limit: int = 100,
    ) -> list[dict]:
        """SSE audit_watcher — 拉 created_at > last_seen 的 rows, 升序.

        TOP (N) 不走 ? 参数: NVARCHAR before_cursor_execute hook 会把整数 coerce
        成 NVARCHAR, SQL Server 报 1060 "行计数参数必须是整数". limit 是 internal
        constant, 没有 SQLi 风险, 内联即可.
        """
        engine = self.get_engine()
        if engine is None:
            return []
        from sqlalchemy import text
        limit_int = max(1, min(int(limit), 1000))
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"SELECT TOP ({limit_int}) run_id, patient_id, rule_id, verdict, "
                        "confidence, created_at, eligibility_json, promise_trace_json "
                        "FROM javert_audit_runs "
                        "WHERE created_at > :last_seen "
                        "ORDER BY created_at ASC"
                    ),
                    {"last_seen": last_seen},
                ).fetchall()
                return [
                    {
                        "run_id": r[0],
                        "patient_id": r[1],
                        "rule_id": r[2],
                        "verdict": r[3],
                        "confidence": float(r[4] or 0.0),
                        "created_at": r[5],
                        "eligibility_evaluation": (
                            json.loads(r[6]) if len(r) > 6 and r[6] else None
                        ),
                        "promise_trace": (
                            json.loads(r[7]) if len(r) > 7 and r[7] else None
                        ),
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning("fetch_runs_since 失败: %s", e)
            return []

    def get_max_created_at(self) -> datetime | None:
        engine = self.get_engine()
        if engine is None:
            return None
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT MAX(created_at) FROM javert_audit_runs")
                ).fetchone()
                return row[0] if row and row[0] else None
        except Exception as e:
            logger.warning("get_max_created_at 失败: %s", e)
            return None

    def get_max_id(self) -> int:
        """audit_watcher 用. id 是 BIGINT IDENTITY, 单调; 整数比较精确."""
        engine = self.get_engine()
        if engine is None:
            return 0
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT ISNULL(MAX(id), 0) FROM javert_audit_runs")
                ).fetchone()
                return int(row[0]) if row and row[0] is not None else 0
        except Exception as e:
            logger.warning("get_max_id 失败: %s", e)
            return 0

    def fetch_runs_since_id(
        self,
        last_id: int,
        limit: int = 100,
    ) -> list[dict]:
        """audit_watcher 主拉取. 按 id 严格 >  排序拉 N 条; 每条带 id 给 watcher
        更新 cursor."""
        engine = self.get_engine()
        if engine is None:
            return []
        from sqlalchemy import text
        limit_int = max(1, min(int(limit), 1000))
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"SELECT TOP ({limit_int}) id, run_id, patient_id, rule_id, "
                        "verdict, confidence, created_at, eligibility_json, promise_trace_json "
                        "FROM javert_audit_runs "
                        "WHERE id > :last_id "
                        "ORDER BY id ASC"
                    ),
                    {"last_id": int(last_id)},
                ).fetchall()
                return [
                    {
                        "id": int(r[0]),
                        "run_id": r[1],
                        "patient_id": r[2],
                        "rule_id": r[3],
                        "verdict": r[4],
                        "confidence": float(r[5] or 0.0),
                        "created_at": r[6],
                        "eligibility_evaluation": (
                            json.loads(r[7]) if len(r) > 7 and r[7] else None
                        ),
                        "promise_trace": (
                            json.loads(r[8]) if len(r) > 8 and r[8] else None
                        ),
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning("fetch_runs_since_id 失败: %s", e)
            return []

    def has_other_runs(self, patient_id: str, exclude_run_id: str) -> bool:
        engine = self.get_engine()
        if engine is None:
            return False
        from sqlalchemy import text
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT TOP 1 1 FROM javert_audit_runs "
                        "WHERE patient_id = :pid AND run_id <> :exc"
                    ),
                    {"pid": patient_id, "exc": exclude_run_id},
                ).fetchone()
                return row is not None
        except Exception as e:
            logger.warning("has_other_runs 失败 %s: %s", patient_id, e)
            return False

    # =========================================================
    # Dashboard
    # =========================================================
    def dashboard_stats(self) -> DashboardStats:
        engine = self.get_engine()
        if engine is None:
            return DashboardStats()
        from sqlalchemy import text
        # latest-per-(rule_id, patient_id) CTE — dashboard 全部基于去重后的 V/I/C 算
        _LATEST_CTE = """
            WITH latest AS (
                SELECT run_id, rule_id, patient_id, verdict, gate_tag,
                       ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC) AS rn
                FROM javert_audit_runs
            )
        """
        try:
            with engine.connect() as conn:
                # 1. 总进度 (基于 latest)
                total_vi = conn.execute(
                    text(
                        _LATEST_CTE +
                        "SELECT COUNT(*) FROM latest "
                        "WHERE rn = 1 AND verdict IN (N'VIOLATION', N'INCONCLUSIVE')"
                    )
                ).scalar() or 0
                reviewed_runs = conn.execute(
                    text(
                        _LATEST_CTE +
                        "SELECT COUNT(DISTINCT rv.run_id) "
                        "FROM javert_vio_review rv "
                        "INNER JOIN latest l ON l.run_id = rv.run_id AND l.rn = 1 "
                        "WHERE rv.is_latest = 1 "
                        "AND l.verdict IN (N'VIOLATION', N'INCONCLUSIVE')"
                    )
                ).scalar() or 0
                # add-verdict-gate-layer: 待线下核查桶 = gate_tag=缺文书 的 latest 裁决数
                pending_offline = conn.execute(
                    text(
                        _LATEST_CTE +
                        "SELECT COUNT(*) FROM latest WHERE rn = 1 AND gate_tag = N'缺文书'"
                    )
                ).scalar() or 0
                # 2. reviewers 排名 (用 vio_review 自身的 latest, 不受 audit 重跑影响)
                rev_rows = conn.execute(
                    text(
                        "SELECT u.id, u.username, u.display_name, COUNT(*) "
                        "FROM javert_vio_review rv "
                        "INNER JOIN javert_users u ON u.id = rv.user_id "
                        "WHERE rv.is_latest = 1 "
                        "GROUP BY u.id, u.username, u.display_name "
                        "ORDER BY COUNT(*) DESC"
                    )
                ).fetchall()
                reviewers = [
                    ReviewerLeaderRow(
                        user_id=int(r[0]),
                        username=r[1],
                        display_name=r[2],
                        latest_review_count=int(r[3] or 0),
                    )
                    for r in rev_rows
                ]
                # 3. 规则维度 — V/I 数 + 专家三分布 (走 latest)
                rule_rows = conn.execute(
                    text(
                        _LATEST_CTE +
                        ", r AS (SELECT * FROM latest WHERE rn = 1) "
                        "SELECT r.rule_id, "
                        "SUM(CASE WHEN r.verdict = N'VIOLATION' THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN r.verdict = N'INCONCLUSIVE' THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN rv.review_verdict IS NOT NULL "
                        "          AND ((r.verdict = N'VIOLATION' AND rv.review_verdict = N'V') "
                        "               OR (r.verdict = N'INCONCLUSIVE' AND rv.review_verdict = N'I')) "
                        "    THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN rv.review_verdict IS NOT NULL "
                        "          AND ((r.verdict = N'VIOLATION' AND rv.review_verdict = N'C') "
                        "               OR (r.verdict = N'INCONCLUSIVE' AND rv.review_verdict = N'V')) "
                        "    THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN rv.id IS NULL THEN 1 ELSE 0 END) "
                        "FROM r "
                        "LEFT JOIN javert_vio_review rv "
                        "       ON rv.run_id = r.run_id AND rv.is_latest = 1 "
                        "WHERE r.verdict IN (N'VIOLATION', N'INCONCLUSIVE') "
                        "GROUP BY r.rule_id "
                        "ORDER BY r.rule_id ASC"
                    )
                ).fetchall()
                rules = [
                    RuleAgreementRow(
                        rule_id=r[0],
                        v_count=int(r[1] or 0),
                        i_count=int(r[2] or 0),
                        expert_agreed=int(r[3] or 0),
                        expert_overturned=int(r[4] or 0),
                        expert_pending=int(r[5] or 0),
                    )
                    for r in rule_rows
                ]
                # 4. 一致率
                v_total = sum(rl.v_count for rl in rules)
                v_agreed = sum(rl.expert_agreed for rl in rules if rl.v_count)
                i_total = sum(rl.i_count for rl in rules)
                i_agreed_partial = 0  # I 一致率不算 - 留空
                progress = (reviewed_runs / total_vi * 100.0) if total_vi else 0.0
                v_agree_pct = (v_agreed / v_total * 100.0) if v_total else 0.0
                return DashboardStats(
                    total_v_or_i=int(total_vi),
                    total_reviewed_runs=int(reviewed_runs),
                    progress_pct=round(progress, 1),
                    pending_offline_check=int(pending_offline),
                    reviewers=reviewers,
                    rules=rules,
                    agreement_v_pct=round(v_agree_pct, 1),
                    agreement_i_pct=0.0,
                )
        except Exception as e:
            logger.warning("dashboard_stats 失败: %s", e)
            return DashboardStats()

    def list_reviews_by_user(self, user_id: int) -> list[ReviewerDrillRow]:
        """dashboard 专家下钻 — 拉取该专家所有 latest review,带 audit_run JOIN.

        按 created_at desc 排. 仅 is_latest=1.
        """
        engine = self.get_engine()
        if engine is None:
            return []
        from sqlalchemy import text
        sql = """
            SELECT rv.id, rv.run_id, rv.patient_id, rv.rule_id,
                   rv.review_verdict, rv.comment, rv.created_at,
                   ar.verdict, ar.confidence
            FROM javert_vio_review rv
            INNER JOIN javert_audit_runs ar ON ar.run_id = rv.run_id
            WHERE rv.user_id = :uid AND rv.is_latest = 1
            ORDER BY rv.created_at DESC
        """
        try:
            with engine.connect() as conn:
                rows = conn.execute(text(sql), {"uid": int(user_id)}).fetchall()
        except Exception as e:
            logger.warning(
                "list_reviews_by_user 失败 user_id=%s: %s", user_id, e,
            )
            return []
        out: list[ReviewerDrillRow] = []
        for r in rows:
            out.append(ReviewerDrillRow(
                review_id=int(r[0]),
                run_id=r[1],
                patient_id=r[2],
                rule_id=r[3],
                expert_verdict=r[4],
                comment=r[5],
                created_at=r[6],
                agent_verdict=r[7],
                agent_confidence=float(r[8]) if r[8] is not None else None,
            ))
        return out

    # =========================================================
    # 导出 — 全量 latest reviews + 可选 history
    # =========================================================
    def fetch_export_rows(
        self,
        scope: str = "v_and_i",
        include_history: bool = False,
    ) -> dict[str, list[dict]]:
        """返回 {sheet_name: rows[]} 给 openpyxl 写盘."""
        engine = self.get_engine()
        if engine is None:
            return {}
        from sqlalchemy import text
        verdict_clause = {
            "v_and_i": "r.verdict IN (N'VIOLATION', N'INCONCLUSIVE')",
            "v_only": "r.verdict = N'VIOLATION'",
            "i_only": "r.verdict = N'INCONCLUSIVE'",
            "all": "1=1",
        }.get(scope, "r.verdict IN (N'VIOLATION', N'INCONCLUSIVE')")
        # latest-per-(rule_id, patient_id) — 导出和工作台一致, 不带重复行
        _LATEST_CTE = (
            "WITH latest AS ("
            "  SELECT *, ROW_NUMBER() OVER ("
            "    PARTITION BY patient_id, rule_id ORDER BY created_at DESC) AS rn "
            "  FROM javert_audit_runs"
            "), r AS (SELECT * FROM latest WHERE rn = 1) "
        )
        out: dict[str, list[dict]] = {}
        try:
            with engine.connect() as conn:
                # Sheet1 审核结果 (走 latest)
                rows = conn.execute(
                    text(
                        _LATEST_CTE +
                        "SELECT r.run_id, r.patient_id, r.rule_id, r.verdict, "
                        "r.confidence, u.username, rv.review_verdict, rv.comment, "
                        "rv.created_at "
                        "FROM r "
                        "LEFT JOIN javert_vio_review rv "
                        "       ON rv.run_id = r.run_id AND rv.is_latest = 1 "
                        "LEFT JOIN javert_users u ON u.id = rv.user_id "
                        f"WHERE {verdict_clause} "
                        "ORDER BY r.patient_id, r.rule_id"
                    )
                ).fetchall()
                out["审核结果"] = [
                    {
                        "run_id": r[0],
                        "patient_id": r[1],
                        "rule_id": r[2],
                        "javert_verdict": r[3],
                        "javert_conf": float(r[4] or 0.0),
                        "reviewer": r[5],
                        "review_verdict": r[6],
                        "comment": r[7],
                        "reviewed_at": r[8],
                    }
                    for r in rows
                ]
                # Sheet2 审核进度
                prog = conn.execute(
                    text(
                        "SELECT u.username, COUNT(*) "
                        "FROM javert_vio_review rv "
                        "INNER JOIN javert_users u ON u.id = rv.user_id "
                        "WHERE rv.is_latest = 1 "
                        "GROUP BY u.username "
                        "ORDER BY COUNT(*) DESC"
                    )
                ).fetchall()
                out["审核进度"] = [
                    {"reviewer": p[0], "latest_review_count": int(p[1] or 0)}
                    for p in prog
                ]
                # Sheet3 规则维度 (latest)
                rule_rows = conn.execute(
                    text(
                        _LATEST_CTE +
                        "SELECT r.rule_id, "
                        "SUM(CASE WHEN r.verdict = N'VIOLATION' THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN r.verdict = N'INCONCLUSIVE' THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN rv.id IS NOT NULL THEN 1 ELSE 0 END) "
                        "FROM r "
                        "LEFT JOIN javert_vio_review rv "
                        "       ON rv.run_id = r.run_id AND rv.is_latest = 1 "
                        f"WHERE {verdict_clause} "
                        "GROUP BY r.rule_id "
                        "ORDER BY r.rule_id"
                    )
                ).fetchall()
                out["规则维度"] = [
                    {
                        "rule_id": r[0],
                        "javert_v": int(r[1] or 0),
                        "javert_i": int(r[2] or 0),
                        "expert_reviewed": int(r[3] or 0),
                    }
                    for r in rule_rows
                ]
                # Sheet4 审核历史
                if include_history:
                    hist = conn.execute(
                        text(
                            "SELECT rv.id, rv.run_id, u.username, rv.review_verdict, "
                            "rv.comment, rv.created_at, rv.is_latest "
                            "FROM javert_vio_review rv "
                            "INNER JOIN javert_users u ON u.id = rv.user_id "
                            "ORDER BY rv.created_at DESC"
                        )
                    ).fetchall()
                    out["审核历史"] = [
                        {
                            "id": int(h[0]),
                            "run_id": h[1],
                            "reviewer": h[2],
                            "review_verdict": h[3],
                            "comment": h[4],
                            "created_at": h[5],
                            "is_latest": bool(h[6]),
                        }
                        for h in hist
                    ]
        except Exception as e:
            logger.warning("fetch_export_rows 失败: %s", e)
        # 系统性违规 sheet (add-cross-patient-stats) — 全量 latest 聚合;
        # V 率需完整分母 (含 CLEAN), 故不受 scope 影响, 独立算.
        try:
            from javert.stats.cross_patient import (
                aggregate,
                compute_rule_stats,
                load_thresholds,
            )
            sys_stats = compute_rule_stats(
                aggregate(self.latest_verdict_rows()), load_thresholds(),
            )
            out["系统性违规"] = [
                {
                    "rule_id": s.rule_id,
                    "被审计患者数": s.n_patients,
                    "V": s.v,
                    "I": s.i,
                    "V率": f"{s.v_rate * 100:.0f}%",
                    "金额": "不可计" if s.amount is None else s.amount,
                    "系统性": s.systemic,
                }
                for s in sys_stats
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning("系统性违规 sheet 生成失败: %s", e)
        return out


# =========================================================
# 进程级单例
# =========================================================
_SHARED: SqlServerStore | None = None


def get_sqlserver_store() -> SqlServerStore:
    """进程内单例. 与 SqliteStore 不同, 它是 lazy 双写副本."""
    global _SHARED
    if _SHARED is None:
        _SHARED = SqlServerStore()
    return _SHARED


def reset_sqlserver_store() -> None:
    """供测试用途重置单例."""
    global _SHARED
    if _SHARED is not None:
        _SHARED.dispose()
    _SHARED = None
