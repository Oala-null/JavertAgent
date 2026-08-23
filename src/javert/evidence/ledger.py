# -*- coding: utf-8 -*-
"""独立、追加式 Diagnosis shadow ledger（不接 audit store）。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from .models import FrozenStrictModel
from .serialization import canonical_json_bytes, sha256_digest

StreamName = Literal["accepted", "rejected", "technical"]


class LedgerCorruptionError(RuntimeError):
    pass


class LedgerEvent(FrozenStrictModel):
    cursor: int = Field(ge=1)
    event_id: str
    stream: StreamName
    idempotency_key: str
    payload: dict[str, Any]
    payload_checksum: str


class AppendResult(FrozenStrictModel):
    status: Literal["accepted", "duplicate"]
    cursor: int = Field(ge=1)


class ShadowLedger(Protocol):
    def append_once(
        self, *, stream: StreamName, event_id: str,
        idempotency_key: str, payload: dict[str, Any],
    ) -> AppendResult: ...

    def iter_events(self, *, stream: StreamName | None = None, cursor: int = 0) -> Iterator[LedgerEvent]: ...

    def stats(self) -> dict[str, int]: ...


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_events (
    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    stream TEXT NOT NULL CHECK(stream IN ('accepted','rejected','technical')),
    idempotency_key TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    payload_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_shadow_stream_cursor ON shadow_events(stream, cursor);
CREATE TRIGGER IF NOT EXISTS tr_shadow_no_update
BEFORE UPDATE ON shadow_events BEGIN SELECT RAISE(ABORT, 'shadow ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS tr_shadow_no_delete
BEFORE DELETE ON shadow_events BEGIN SELECT RAISE(ABORT, 'shadow ledger is append-only'); END;
"""


class SqliteShadowLedger:
    def __init__(self, path: Path, *, timeout: float = 0.25):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        self._conn = sqlite3.connect(path, timeout=timeout, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._conn:
            self._conn.executescript(_SCHEMA)
        os.chmod(path, 0o600)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "SqliteShadowLedger":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def append_once(
        self, *, stream: StreamName, event_id: str,
        idempotency_key: str, payload: dict[str, Any],
    ) -> AppendResult:
        payload_bytes = canonical_json_bytes(payload)
        checksum = sha256_digest(payload_bytes)
        payload_text = payload_bytes.decode("utf-8").rstrip("\n")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "SELECT cursor FROM shadow_events WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if cursor is not None:
                return AppendResult(status="duplicate", cursor=int(cursor[0]))
            self._conn.execute(
                "INSERT INTO shadow_events(event_id,stream,idempotency_key,payload_json,payload_checksum) "
                "VALUES(?,?,?,?,?)",
                (event_id, stream, idempotency_key, payload_text, checksum),
            )
            row = self._conn.execute(
                "SELECT cursor FROM shadow_events WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            return AppendResult(status="accepted", cursor=int(row[0]))

    def iter_events(
        self, *, stream: StreamName | None = None, cursor: int = 0,
    ) -> Iterator[LedgerEvent]:
        sql = (
            "SELECT cursor,event_id,stream,idempotency_key,payload_json,payload_checksum "
            "FROM shadow_events WHERE cursor>?"
        )
        params: list[Any] = [cursor]
        if stream:
            sql += " AND stream=?"
            params.append(stream)
        sql += " ORDER BY cursor"
        for row in self._conn.execute(sql, params).fetchall():
            raw = str(row[4])
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LedgerCorruptionError(f"corrupt ledger row cursor={row[0]}") from exc
            if sha256_digest((raw + "\n").encode("utf-8")) != row[5]:
                raise LedgerCorruptionError(f"checksum mismatch cursor={row[0]}")
            yield LedgerEvent(
                cursor=int(row[0]), event_id=row[1], stream=row[2],
                idempotency_key=row[3], payload=payload, payload_checksum=row[5],
            )

    def stats(self) -> dict[str, int]:
        result = {"accepted": 0, "rejected": 0, "technical": 0}
        for stream, count in self._conn.execute(
            "SELECT stream,COUNT(*) FROM shadow_events GROUP BY stream"
        ).fetchall():
            result[str(stream)] = int(count)
        return result
