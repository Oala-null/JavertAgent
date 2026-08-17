# -*- coding: utf-8 -*-
"""OCR 患者在 Workbench 的来源标签与全净可见性。"""

from __future__ import annotations

from datetime import datetime, timezone

from javert.config import JavertConfig
from javert.store.sqlserver_store import SqlServerStore


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self):
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, _params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "javert_vio_review" in sql:
            return _Rows([])
        if "SUM(CASE WHEN verdict" in sql:
            return _Rows([
                ("J-OCR-SYNTHETIC", 0, 0, 12, "ocr1.0", datetime.now(timezone.utc))
            ])
        return _Rows([("ocr1.0",)])


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    def connect(self):
        return self.connection


def test_all_clean_ocr_patient_stays_visible_in_default_filter(monkeypatch):
    connection = _Connection()
    store = SqlServerStore(JavertConfig(sql_enabled=False))
    monkeypatch.setattr(store, "get_engine", lambda: _Engine(connection))

    patients = store.list_patients_with_violations("v_and_i")

    assert len(patients) == 1
    assert patients[0].batch_tag == "ocr1.0"
    assert patients[0].c_count == 12
    assert any("THEN N'ocr1.0'" in sql for sql in connection.statements)


def test_ocr_profile_wins_over_newer_untagged_run(monkeypatch):
    connection = _Connection()
    store = SqlServerStore(JavertConfig(sql_enabled=False))
    monkeypatch.setattr(store, "get_engine", lambda: _Engine(connection))

    assert store.latest_batch_tag_for_patient("J-OCR-SYNTHETIC") == "ocr1.0"
    assert "WHEN EXISTS" in connection.statements[0]
