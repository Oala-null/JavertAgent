from __future__ import annotations

from javert.commands import ensure_schema


class _Rows:
    def fetchall(self):
        return [
            ("Javert_audit_runs",),
            ("javert_users",),
            ("javert_vio_review",),
            ("javert_audit_logs",),
        ]


class _Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement):
        assert "LOWER(name)" in str(statement)
        return _Rows()


class _Engine:
    def connect(self):
        return _Connection()


class _Store:
    def health_check(self):
        return {"sql_server": True}

    def init_schema(self, _ddl_path):
        return True

    def get_engine(self):
        return _Engine()


def test_schema_verification_accepts_historical_table_name_case(monkeypatch):
    monkeypatch.setattr(ensure_schema, "get_sqlserver_store", _Store)
    assert ensure_schema.run_ensure_schema() == 0
