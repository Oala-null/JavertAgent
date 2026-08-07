# -*- coding: utf-8 -*-
"""AuditStore — SQLite 持久化层."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from javert.audit.result import AuditResult, Evidence, ToolCall
from javert.oncology.contracts import EligibilityEvaluation
from javert.promises.models import PromiseTrace

logger = logging.getLogger("javert.store.audit_store")


class AuditStore(ABC):
    """audit_runs 持久化抽象."""

    @abstractmethod
    def init_schema(self) -> None: ...

    @abstractmethod
    def write(self, result: AuditResult) -> None: ...

    @abstractmethod
    def find_by_run_id(self, run_id: str) -> AuditResult | None: ...

    @abstractmethod
    def find_by_rule(self, rule_id: str) -> list[AuditResult]: ...

    @abstractmethod
    def find_by_patient(self, patient_id: str) -> list[AuditResult]: ...

    @abstractmethod
    def find_by_verdict(self, verdict: str, since: datetime | None = None) -> list[AuditResult]: ...

    @abstractmethod
    def find_by_rule_patient_latest(self, rule_id: str, patient_id: str) -> AuditResult | None: ...

    @abstractmethod
    def summary_by_rule(
        self, since: datetime | None = None, rule_id: str | None = None,
    ) -> dict[str, dict]: ...

    # v2: sync 状态管理
    @abstractmethod
    def mark_synced(self, run_id: str) -> None: ...

    @abstractmethod
    def mark_sync_failed(self, run_id: str, error: str) -> None: ...

    @abstractmethod
    def find_unsynced(self, limit: int = 100) -> list[AuditResult]: ...

    @abstractmethod
    def count_sync_state(self) -> dict: ...

    @abstractmethod
    def close(self) -> None: ...


class SqliteStore(AuditStore):
    """SQLite 实现."""

    SCHEMA_VERSION = 7

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        # 并发写互斥锁 — sqlite 文件层 single-writer, 客户端串行写避免 OperationalError
        self._write_lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            # check_same_thread=False 允许跨线程使用同一 conn (由 _write_lock 串行)
            # timeout=30 + WAL + busy_timeout: jv-go (CLI 进程) 与 web SyncWorker 并发写同一库时
            # 不再 5s 就抛 database is locked 丢结果 (跨进程 _write_lock 无效, 只能靠 sqlite 层)
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=30000")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---- schema ----
    def init_schema(self) -> None:
        schema_sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
        with self.conn as c:
            c.executescript(schema_sql)
        # 累积 migration: 老库逐列幂等 ALTER, 新库一切就绪
        self._ensure_v2_columns()
        logger.info("audit_store schema 已初始化 (v%d): %s", self.SCHEMA_VERSION, self.db_path)

    def _ensure_v2_columns(self) -> None:
        """累积 migration: 给 audit_runs 幂等补齐 v2-v7 可空列.

        幂等. 老库 (v1) 缺这三列 → ALTER TABLE 补; 新库 (v2) 已含 → 跳过.
        最后无条件 CREATE INDEX IF NOT EXISTS idx_audit_unsynced.
        """
        cur = self.conn.execute("PRAGMA table_info(audit_runs)")
        existing_cols = {row[1] for row in cur.fetchall()}

        migrations: list[str] = []
        if "synced_at" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN synced_at TEXT")
        if "sync_attempts" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN sync_attempts INTEGER DEFAULT 0")
        if "sync_last_error" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN sync_last_error TEXT")
        # v3 (v0.7): batch_tag
        if "batch_tag" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN batch_tag TEXT")
        # v4 (v0.9): anchors_json — 命中项目/锚点缓存 (CREATE 已含, 老库补)
        if "anchors_json" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN anchors_json TEXT")
        # v5 (add-verdict-gate-layer): gate_tag
        if "gate_tag" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN gate_tag TEXT")
        # v6 (strengthen-oncology-drug-eligibility): 单一可空 JSON 扩展
        if "eligibility_json" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN eligibility_json TEXT")
        # v7 (add-evolving-promise-harness): 可空、去标识的终局 trace
        if "promise_trace_json" not in existing_cols:
            migrations.append("ALTER TABLE audit_runs ADD COLUMN promise_trace_json TEXT")

        with self.conn as c:
            for sql in migrations:
                c.execute(sql)
                logger.info("v2 migration: %s", sql)
            # 索引必须在所有列就绪后建
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_unsynced "
                "ON audit_runs(synced_at, created_at)"
            )
            if migrations:
                c.execute(
                    "INSERT OR REPLACE INTO _meta(key, value) VALUES ('schema_version', ?)",
                    (str(self.SCHEMA_VERSION),),
                )

    # ---- write ----
    def write(self, result: AuditResult, batch_tag: str | None = None) -> None:
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
        # 防止调用方在构造后就地改 verdict/eligibility，写入前再次验证投影.
        AuditResult.model_validate(result.model_dump())
        with self._write_lock, self.conn as c:
            c.execute(
                """
                INSERT INTO audit_runs (
                    run_id, rule_id, patient_id, verdict, confidence,
                    reasoning, evidence_json, tool_calls_json,
                    duration_ms, model, started_at, batch_tag, gate_tag,
                    eligibility_json, promise_trace_json, anchors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.rule_id,
                    result.patient_id,
                    result.verdict,
                    result.confidence,
                    result.reasoning,
                    evidence_json,
                    tool_calls_json,
                    result.duration_ms,
                    result.model,
                    result.started_at.isoformat(),
                    batch_tag,
                    result.gate_tag or "",
                    eligibility_json,
                    promise_trace_json,
                    result.anchors_json,
                ),
            )

    # ---- query ----
    def _row_to_result(self, row: sqlite3.Row) -> AuditResult:
        evidence = [Evidence(**e) for e in json.loads(row["evidence_json"] or "[]")]
        tool_calls = [ToolCall(**tc) for tc in json.loads(row["tool_calls_json"] or "[]")]
        started_at = datetime.fromisoformat(row["started_at"]) if row["started_at"] else datetime.now(timezone.utc)
        cols = row.keys()
        gate_tag = (row["gate_tag"] or "") if "gate_tag" in cols else ""
        eligibility = None
        if "eligibility_json" in cols and row["eligibility_json"]:
            eligibility = EligibilityEvaluation.model_validate_json(row["eligibility_json"])
        promise_trace = None
        if "promise_trace_json" in cols and row["promise_trace_json"]:
            promise_trace = PromiseTrace.model_validate_json(row["promise_trace_json"])
        return AuditResult(
            run_id=row["run_id"],
            rule_id=row["rule_id"],
            patient_id=row["patient_id"],
            verdict=row["verdict"],
            confidence=row["confidence"] or 0.0,
            reasoning=row["reasoning"] or "",
            evidence=evidence,
            tool_calls=tool_calls,
            duration_ms=row["duration_ms"] or 0,
            model=row["model"] or "",
            started_at=started_at,
            gate_tag=gate_tag,
            eligibility_evaluation=eligibility,
            promise_trace=promise_trace,
            anchors_json=(row["anchors_json"] if "anchors_json" in cols else None),
        )

    def find_by_run_id(self, run_id: str) -> AuditResult | None:
        cur = self.conn.execute("SELECT * FROM audit_runs WHERE run_id = ?", (run_id,))
        row = cur.fetchone()
        return self._row_to_result(row) if row else None

    def find_by_rule(self, rule_id: str) -> list[AuditResult]:
        cur = self.conn.execute(
            "SELECT * FROM audit_runs WHERE rule_id = ? ORDER BY created_at DESC",
            (rule_id,),
        )
        return [self._row_to_result(r) for r in cur.fetchall()]

    def find_by_patient(self, patient_id: str) -> list[AuditResult]:
        cur = self.conn.execute(
            "SELECT * FROM audit_runs WHERE patient_id = ? ORDER BY created_at DESC",
            (patient_id,),
        )
        return [self._row_to_result(r) for r in cur.fetchall()]

    def find_by_verdict(self, verdict: str, since: datetime | None = None) -> list[AuditResult]:
        if since is None:
            cur = self.conn.execute(
                "SELECT * FROM audit_runs WHERE verdict = ? ORDER BY created_at DESC",
                (verdict,),
            )
        else:
            cur = self.conn.execute(
                """SELECT * FROM audit_runs WHERE verdict = ? AND created_at >= ?
                   ORDER BY created_at DESC""",
                (verdict, since.isoformat()),
            )
        return [self._row_to_result(r) for r in cur.fetchall()]

    def find_by_rule_patient_latest(self, rule_id: str, patient_id: str) -> AuditResult | None:
        cur = self.conn.execute(
            """SELECT * FROM audit_runs WHERE rule_id = ? AND patient_id = ?
               ORDER BY started_at DESC LIMIT 1""",
            (rule_id, patient_id),
        )
        row = cur.fetchone()
        return self._row_to_result(row) if row else None

    def summary_by_rule(
        self,
        since: datetime | None = None,
        rule_id: str | None = None,
    ) -> dict[str, dict]:
        params: list = []
        where_parts: list[str] = []
        if since is not None:
            where_parts.append("created_at >= ?")
            params.append(since.isoformat())
        if rule_id is not None:
            where_parts.append("rule_id = ?")
            params.append(rule_id)
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        sql = f"""
            SELECT rule_id,
                   SUM(CASE WHEN verdict='VIOLATION' THEN 1 ELSE 0 END) AS V,
                   SUM(CASE WHEN verdict='CLEAN' THEN 1 ELSE 0 END) AS C,
                   SUM(CASE WHEN verdict='INCONCLUSIVE' THEN 1 ELSE 0 END) AS I,
                   COUNT(*) AS total,
                   AVG(confidence) AS mean_confidence,
                   AVG(duration_ms) AS mean_duration_ms
            FROM audit_runs
            {where_clause}
            GROUP BY rule_id
            ORDER BY rule_id ASC
        """
        cur = self.conn.execute(sql, params)
        out: dict[str, dict] = {}
        for row in cur.fetchall():
            out[row["rule_id"]] = {
                "V": int(row["V"] or 0),
                "C": int(row["C"] or 0),
                "I": int(row["I"] or 0),
                "total": int(row["total"] or 0),
                "mean_confidence": float(row["mean_confidence"] or 0.0),
                "mean_duration_ms": float(row["mean_duration_ms"] or 0.0),
            }
        return out

    def latest_verdict_rows(self, batch_tag: str | None = None) -> list[tuple[str, str, str]]:
        """每 (rule_id, patient_id) 取 created_at 最新一条 → (rule_id, patient_id, verdict).

        跨患者统计 (add-cross-patient-stats) 的 latest 去重源; batch_tag 非空则只算该批次.
        ~5000 行 Python dedup 足够, 无需 window func. created_at 是 ISO/CURRENT_TIMESTAMP
        文本, 字典序即时间序.
        """
        params: list = []
        where = ""
        if batch_tag is not None:
            where = "WHERE batch_tag = ?"
            params.append(batch_tag)
        cur = self.conn.execute(
            f"SELECT rule_id, patient_id, verdict, created_at FROM audit_runs {where}",
            params,
        )
        latest: dict[tuple[str, str], tuple[str, str]] = {}  # (rule,patient)->(verdict,created_at)
        for row in cur.fetchall():
            key = (row["rule_id"], row["patient_id"])
            ca = row["created_at"] or ""
            prev = latest.get(key)
            if prev is None or ca >= prev[1]:
                latest[key] = (row["verdict"], ca)
        return [(rid, pid, v) for (rid, pid), (v, _ca) in latest.items()]

    # =========================================================
    # v2: 142 同步状态管理
    # =========================================================
    def mark_synced(self, run_id: str) -> None:
        """标记 run_id 已同步到 142 (synced_at = now, 清零 sync_last_error)."""
        with self._write_lock, self.conn as c:
            c.execute(
                """
                UPDATE audit_runs
                SET synced_at = ?,
                    sync_attempts = COALESCE(sync_attempts, 0) + 1,
                    sync_last_error = NULL
                WHERE run_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), run_id),
            )

    def mark_sync_failed(self, run_id: str, error: str) -> None:
        """记一次同步失败 (sync_attempts++, 写入 last_error). synced_at 保持 NULL."""
        with self._write_lock, self.conn as c:
            c.execute(
                """
                UPDATE audit_runs
                SET sync_attempts = COALESCE(sync_attempts, 0) + 1,
                    sync_last_error = ?
                WHERE run_id = ?
                """,
                (str(error)[:1000], run_id),
            )

    def find_unsynced(self, limit: int = 100) -> list[AuditResult]:
        """返回 synced_at IS NULL 的 audit, 按 created_at 升序 (最早的优先)."""
        cur = self.conn.execute(
            """
            SELECT * FROM audit_runs
            WHERE synced_at IS NULL
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (int(limit),),
        )
        return [self._row_to_result(r) for r in cur.fetchall()]

    def count_sync_state(self) -> dict:
        """返回同步状态摘要.

        Returns:
            {
                "synced": int,
                "unsynced": int,
                "total": int,
                "last_synced_at": iso str | None,
                "last_attempt_run_id": str | None,
                "last_error": str | None,
                "last_error_at_run_id": str | None,
            }
        """
        cur = self.conn.execute(
            """
            SELECT
                SUM(CASE WHEN synced_at IS NOT NULL THEN 1 ELSE 0 END) AS synced,
                SUM(CASE WHEN synced_at IS NULL THEN 1 ELSE 0 END) AS unsynced,
                COUNT(*) AS total
            FROM audit_runs
            """
        )
        row = cur.fetchone()
        synced = int(row["synced"] or 0)
        unsynced = int(row["unsynced"] or 0)
        total = int(row["total"] or 0)

        cur2 = self.conn.execute(
            "SELECT run_id, synced_at FROM audit_runs "
            "WHERE synced_at IS NOT NULL ORDER BY synced_at DESC LIMIT 1"
        )
        last_synced = cur2.fetchone()

        cur3 = self.conn.execute(
            "SELECT run_id, sync_last_error FROM audit_runs "
            "WHERE sync_last_error IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        )
        last_err = cur3.fetchone()

        return {
            "synced": synced,
            "unsynced": unsynced,
            "total": total,
            "last_synced_at": last_synced["synced_at"] if last_synced else None,
            "last_synced_run_id": last_synced["run_id"] if last_synced else None,
            "last_error": last_err["sync_last_error"] if last_err else None,
            "last_error_run_id": last_err["run_id"] if last_err else None,
        }

    # =========================================================
    # 中位数耗时 (v1 已有)
    # =========================================================
    def median_duration(self, rule_id: str | None = None, since: datetime | None = None) -> dict[str, float]:
        """中位数耗时 (sqlite 无 PERCENTILE_CONT, 在 Python 算)."""
        params: list = []
        where_parts: list[str] = []
        if rule_id is not None:
            where_parts.append("rule_id = ?")
            params.append(rule_id)
        if since is not None:
            where_parts.append("created_at >= ?")
            params.append(since.isoformat())
        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cur = self.conn.execute(
            f"SELECT rule_id, duration_ms FROM audit_runs {where_clause}",
            params,
        )
        bucket: dict[str, list[int]] = {}
        for row in cur.fetchall():
            bucket.setdefault(row["rule_id"], []).append(int(row["duration_ms"] or 0))
        out: dict[str, float] = {}
        for rid, vals in bucket.items():
            vals.sort()
            n = len(vals)
            if n == 0:
                out[rid] = 0.0
            elif n % 2 == 1:
                out[rid] = float(vals[n // 2])
            else:
                out[rid] = (vals[n // 2 - 1] + vals[n // 2]) / 2.0
        return out
