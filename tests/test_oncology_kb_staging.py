from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from javert.cli import main
from javert.oncology.authoring.sqlserver import (
    KnowledgePreflight,
    ServerValidationIssue,
    StagingUploadResult,
)
from javert.oncology.authoring.staging import InMemoryKnowledgeStore, dry_run_plan


ROOT = Path(__file__).resolve().parents[1]
WORKBOOK = ROOT / "outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx"


def test_dry_run_reports_safe_plan_without_mutation() -> None:
    store = InMemoryKnowledgeStore()
    plan = dry_run_plan(WORKBOOK, kind="regimen", database="知识库_work")
    assert plan["mutations_planned"] == 0
    assert plan["contains_phi"] is False
    assert plan["local_validation_errors"] == 0
    assert str(WORKBOOK.parent) not in str(plan)
    assert store.batches == {} and store.authoring == {}


def test_upload_is_idempotent_and_does_not_approve_or_release() -> None:
    store = InMemoryKnowledgeStore()
    first = store.upload(WORKBOOK, kind="regimen", uploaded_by="operator-a")
    second = store.upload(WORKBOOK, kind="regimen", uploaded_by="operator-a")
    assert first is second
    assert len(store.batches) == 1
    assert first.status == "UPLOADED"
    assert not store.approved_revision_ids and not store.release_ids and store.deployed_release_id is None


def test_server_validation_failure_keeps_formal_store_empty() -> None:
    store = InMemoryKnowledgeStore()
    batch = store.upload(WORKBOOK, kind="regimen", uploaded_by="operator-a")

    def reject_first(row, _store):
        return "REFERENCE_MISSING" if row.excel_row_no == 2 else None

    store.server_validate(batch.import_batch_id, [reject_first])
    assert batch.status == "VALIDATION_FAILED"
    assert store.authoring == {}
    with pytest.raises(ValueError):
        store.materialize(batch.import_batch_id)


def test_materialization_rolls_back_on_mid_batch_failure() -> None:
    store = InMemoryKnowledgeStore()
    batch = store.upload(WORKBOOK, kind="regimen", uploaded_by="operator-a")
    store.server_validate(batch.import_batch_id)
    fail_id = batch.rows[len(batch.rows) // 2].stable_row_id
    with pytest.raises(RuntimeError, match="injected"):
        store.materialize(batch.import_batch_id, fail_on_stable_id=fail_id)
    assert store.authoring == {}
    assert batch.status == "FAILED"


def test_successful_materialization_reconciles_every_row_and_keeps_states_independent() -> None:
    store = InMemoryKnowledgeStore()
    batch = store.upload(WORKBOOK, kind="regimen", uploaded_by="operator-a")
    store.server_validate(batch.import_batch_id)
    store.materialize(batch.import_batch_id)
    assert batch.status == "MATERIALIZED"
    assert sum(counts["staged"] for counts in batch.reconciliation.values()) == len(batch.rows)
    assert sum(counts["inserted"] for counts in batch.reconciliation.values()) == len(batch.rows)
    assert not store.approved_revision_ids and not store.release_ids and store.deployed_release_id is None
    store.approve("rev-a")
    assert store.approved_revision_ids == {"rev-a"} and not store.release_ids
    store.register_release("release-a")
    assert store.deployed_release_id is None
    store.deploy("release-a")
    assert store.deployed_release_id == "release-a"


def test_cli_exposes_all_required_oncology_kb_operations() -> None:
    result = CliRunner().invoke(main, ["oncology-kb", "--help"])
    assert result.exit_code == 0
    for command in (
        "export", "validate", "preflight", "upload", "materialize",
        "release-build", "release-publish", "release-rollback",
    ):
        assert command in result.output


def test_upload_database_is_required_and_non_owned_target_fails_before_work() -> None:
    runner = CliRunner()
    missing = runner.invoke(main, ["oncology-kb", "upload", str(WORKBOOK), "--kind", "regimen", "--dry-run"])
    assert missing.exit_code == 2
    non_owned = runner.invoke(
        main,
        ["oncology-kb", "upload", str(WORKBOOK), "--kind", "regimen", "--database", "知识库_work", "--dry-run"],
        env={"JAVERT_OWNED_DBS": "TP_data_hub"},
    )
    assert non_owned.exit_code != 0


def test_upload_dry_run_checks_database_but_never_mutates(monkeypatch) -> None:
    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr("javert.commands.oncology_kb._open_connection", lambda database: connection)
    monkeypatch.setattr(
        "javert.commands.oncology_kb.preflight_connection",
        lambda _connection, database: KnowledgePreflight(
            requested_database=database,
            connected_database=database,
            principal="kb_writer",
            can_select=True,
            can_insert=True,
            can_update=True,
            schema_version=1,
        ),
    )
    result = CliRunner().invoke(
        main,
        ["oncology-kb", "upload", str(WORKBOOK), "--kind", "regimen", "--database", "知识库_work", "--dry-run"],
        env={"JAVERT_OWNED_DBS": "知识库_work"},
    )
    assert result.exit_code == 0, result.output
    plan = __import__("json").loads(result.output)
    assert plan["connected_database"] == "知识库_work"
    assert plan["mutations_planned"] == 0
    assert "principal" not in plan and "kb_writer" not in result.output
    assert connection.closed is True


@pytest.mark.parametrize(
    "existing_status",
    ["VALIDATED", "VALIDATION_FAILED", "MATERIALIZED", "FAILED", "ABORTED"],
)
def test_reused_upload_with_completed_status_never_revalidates_or_regresses(
    monkeypatch,
    existing_status: str,
) -> None:
    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(
        "javert.commands.oncology_kb._connected_plan",
        lambda workbook, kind, database: ({"connected_database": database}, connection),
    )
    monkeypatch.setattr(
        "javert.commands.oncology_kb.upload_staging",
        lambda *args, **kwargs: StagingUploadResult(
            import_batch_id="batch-existing",
            reused=True,
            row_count=16,
            status=existing_status,
        ),
    )

    def unexpected_validation(*args, **kwargs):
        raise AssertionError("reused batch must not be server-validated again")

    monkeypatch.setattr(
        "javert.commands.oncology_kb.mark_server_validation",
        unexpected_validation,
    )
    result = CliRunner().invoke(
        main,
        [
            "oncology-kb", "upload", str(WORKBOOK), "--kind", "regimen",
            "--database", "知识库_work", "--uploaded-by", "operator-a",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = __import__("json").loads(result.output)
    assert payload["reused"] is True and payload["status"] == existing_status
    assert connection.closed is True


def test_reused_uploaded_batch_resumes_server_validation(monkeypatch) -> None:
    class Connection:
        closed = False

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(
        "javert.commands.oncology_kb._connected_plan",
        lambda workbook, kind, database: ({"connected_database": database}, connection),
    )
    monkeypatch.setattr(
        "javert.commands.oncology_kb.upload_staging",
        lambda *args, **kwargs: StagingUploadResult(
            import_batch_id="batch-uploaded-before-interruption",
            reused=True,
            row_count=16,
            status="UPLOADED",
        ),
    )
    validation_calls = []

    def resume_validation(received_connection, batch_id, *, kind):
        validation_calls.append((received_connection, batch_id, kind))
        return (
            "VALIDATION_FAILED",
            [ServerValidationIssue("02_方案主表", 2, "REFERENCE_MISSING")],
        )

    monkeypatch.setattr(
        "javert.commands.oncology_kb.mark_server_validation",
        resume_validation,
    )
    result = CliRunner().invoke(
        main,
        [
            "oncology-kb", "upload", str(WORKBOOK), "--kind", "regimen",
            "--database", "知识库_work", "--uploaded-by", "operator-a",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = __import__("json").loads(result.output)
    assert validation_calls == [
        (connection, "batch-uploaded-before-interruption", "regimen")
    ]
    assert payload == {
        "import_batch_id": "batch-uploaded-before-interruption",
        "reused": True,
        "row_count": 16,
        "server_validation_errors": 1,
        "status": "VALIDATION_FAILED",
    }
    assert connection.closed is True


def test_materialize_requires_explicit_batch_kind_and_database() -> None:
    result = CliRunner().invoke(main, ["oncology-kb", "materialize"])
    assert result.exit_code == 2
    help_result = CliRunner().invoke(main, ["oncology-kb", "materialize", "--help"])
    assert help_result.exit_code == 0
    for option in ("--batch-id", "--kind", "--database"):
        assert option in help_result.output
