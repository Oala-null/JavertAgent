"""oncology-kb 受控知识维护命令组。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import click

from javert.config import load_config
from javert.oncology.authoring.sqlserver import (
    KNOWLEDGE_DATABASE,
    connect_knowledge_database,
    mark_server_validation,
    materialize_staging,
    preflight_connection,
    safe_database_error,
    upload_staging,
)
from javert.oncology.authoring.release_store import (
    ReleaseOperationResult,
    ReleaseStoreError,
    SqlServerReleaseStoreAdapter,
    approve_authoring_revision,
    build_operational_release,
    inspect_operational_authority,
    publish_operational_release,
    rollback_operational_release,
)
from javert.oncology.authoring.staging import dry_run_plan
from javert.oncology.authoring.workbooks import validate_workbook, write_validation_report
from javert.store.write_safety import validate_owned_database


ROOT = Path(__file__).resolve().parents[3]


@click.group("oncology-kb")
def oncology_kb_group() -> None:
    """肿瘤知识导出、校验、上传、发布和回滚。"""


@oncology_kb_group.command("export")
@click.option("--output-dir", type=click.Path(path_type=Path), default=ROOT / "outputs/add-oncology-kb-authoring")
@click.option("--node", envvar="JAVERT_ARTIFACT_NODE", required=True, type=click.Path(path_type=Path))
@click.option("--node-modules", envvar="JAVERT_ARTIFACT_NODE_MODULES", required=True, type=click.Path(path_type=Path))
def export_cmd(output_dir: Path, node: Path, node_modules: Path) -> None:
    """用 artifact-tool 确定性生成两份专家工作簿。"""
    output_dir = output_dir.resolve()
    commands = [
        [sys.executable, str(ROOT / "scripts/build_oncology_kb_authoring_baseline.py")],
        [sys.executable, str(ROOT / "scripts/build_oncology_authoring_candidates.py")],
        [sys.executable, str(ROOT / "scripts/build_regimen_authoring_payload.py")],
    ]
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
    with tempfile.TemporaryDirectory(prefix="javert-kb-export-") as temp_name:
        temp = Path(temp_name)
        os.chmod(temp, 0o700)
        (temp / "node_modules").symlink_to(node_modules, target_is_directory=True)
        builder = temp / "builder.mjs"
        builder.symlink_to(ROOT / "scripts/build_oncology_authoring_workbooks.mjs")
        subprocess.run(
            [str(node), "--preserve-symlinks-main", str(builder), "--root", str(ROOT), "--output-dir", str(output_dir)],
            cwd=temp,
            check=True,
        )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/finalize_oncology_authoring_workbooks.py"), "--output-dir", str(output_dir)],
        cwd=ROOT,
        check=True,
    )
    click.echo(f"exported={output_dir}")


@oncology_kb_group.command("validate")
@click.argument("workbook", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--kind", type=click.Choice(["eligibility", "regimen"]), required=True)
@click.option("--report", type=click.Path(path_type=Path))
def validate_cmd(workbook: Path, kind: str, report: Path | None) -> None:
    """离线校验；不读取数据库配置、不建立连接。"""
    issues = validate_workbook(workbook, kind=kind)
    target = report or workbook.with_suffix(".validation.json")
    write_validation_report(target, issues)
    click.echo(f"valid={not issues} errors={len(issues)} report={target.name}")
    if issues:
        raise SystemExit(2)


def _database_plan(workbook: Path, kind: str, database: str) -> dict:
    validate_owned_database(database, required_exact=KNOWLEDGE_DATABASE)
    return dry_run_plan(workbook, kind=kind, database=database)


def _open_connection(database: str):
    """owned guard 在 pyodbc 导入和连接前执行。"""
    def connector(target: str):
        import pyodbc

        from javert.data.hub_source import build_conn_str

        config = load_config()
        return pyodbc.connect(build_conn_str(config, target), timeout=60, autocommit=False)

    return connect_knowledge_database(database, connector)


def _connected_plan(workbook: Path, kind: str, database: str) -> tuple[dict, object]:
    plan = _database_plan(workbook, kind, database)
    if plan["local_validation_errors"]:
        raise click.ClickException(f"本地验证失败: errors={plan['local_validation_errors']}")
    try:
        connection = _open_connection(database)
    except Exception as exc:
        raise click.ClickException(safe_database_error(exc, database=database)) from None
    try:
        preflight = preflight_connection(connection, database)
    except Exception as exc:
        connection.close()
        raise click.ClickException(safe_database_error(exc, database=database)) from None
    plan.update(
        {
            "connected_database": preflight.connected_database,
            "schema_version": preflight.schema_version,
            "can_select": preflight.can_select,
            "can_insert": preflight.can_insert,
            "can_update": preflight.can_update,
        }
    )
    return plan, connection


@oncology_kb_group.command("preflight")
@click.argument("workbook", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--kind", type=click.Choice(["eligibility", "regimen"]), required=True)
@click.option("--database", required=True)
def preflight_cmd(workbook: Path, kind: str, database: str) -> None:
    """只读检查 owned target、本地计数、DB_NAME、权限和 schema version。"""
    plan, connection = _connected_plan(workbook, kind, database)
    connection.close()
    click.echo(json.dumps(plan, ensure_ascii=False, sort_keys=True))


@oncology_kb_group.command("schema-apply")
@click.option("--database", required=True)
@click.option("--sqlcmd", type=click.Path(path_type=Path), default=Path("sqlcmd"))
def schema_apply_cmd(database: str, sqlcmd: Path) -> None:
    """在显式 owned 目标上通过 sqlcmd 幂等应用知识库 schema。"""
    validate_owned_database(database, required_exact=KNOWLEDGE_DATABASE)
    try:
        connection = _open_connection(database)
        cursor = connection.cursor()
        connected = str(cursor.execute("SELECT DB_NAME()").fetchone()[0])
        can_alter = bool(
            cursor.execute("SELECT HAS_PERMS_BY_NAME(DB_NAME(), N'DATABASE', N'ALTER')").fetchone()[0]
        )
        connection.close()
    except Exception as exc:
        raise click.ClickException(safe_database_error(exc, database=database)) from None
    if connected != database or not can_alter:
        raise click.ClickException("schema preflight failed: DB_NAME mismatch or ALTER permission missing")
    config = load_config()
    if not config.sql_password:
        raise click.ClickException("JAVERT_SQL_PASSWORD 未配置")
    environment = os.environ.copy()
    environment["SQLCMDPASSWORD"] = config.sql_password
    command = [
        str(sqlcmd),
        "-S", f"{config.sql_host},{config.sql_port}",
        "-U", config.sql_user,
        "-d", database,
        "-v", f"KB_DATABASE={database}",
        "-b",
        "-i", str(ROOT / "scripts/sql/create_oncology_kb_schema.sql"),
    ]
    try:
        subprocess.run(command, cwd=ROOT, env=environment, check=True, capture_output=True, text=True)
    except Exception as exc:
        raise click.ClickException(safe_database_error(exc, database=database)) from None
    click.echo(f"schema_applied database={database}")


@oncology_kb_group.command("upload")
@click.argument("workbook", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--kind", type=click.Choice(["eligibility", "regimen"]), required=True)
@click.option("--database", required=True)
@click.option("--dry-run", is_flag=True)
@click.option("--uploaded-by", envvar="JAVERT_KB_OPERATOR")
def upload_cmd(workbook: Path, kind: str, database: str, dry_run: bool, uploaded_by: str | None) -> None:
    plan, connection = _connected_plan(workbook, kind, database)
    if dry_run:
        connection.close()
        click.echo(json.dumps(plan, ensure_ascii=False, sort_keys=True))
        return
    operator = (uploaded_by or "").strip()
    if not operator:
        connection.close()
        raise click.ClickException("正式上传必须显式提供 --uploaded-by 或 JAVERT_KB_OPERATOR")
    try:
        result = upload_staging(connection, workbook, kind=kind, uploaded_by=operator)
        if result.reused and result.status != "UPLOADED":
            status, issues = result.status, []
        else:
            status, issues = mark_server_validation(
                connection,
                result.import_batch_id,
                kind=kind,
            )
    except Exception as exc:
        raise click.ClickException(safe_database_error(exc, database=database)) from None
    finally:
        connection.close()
    click.echo(
        json.dumps(
            {
                "import_batch_id": result.import_batch_id,
                "reused": result.reused,
                "row_count": result.row_count,
                "status": status,
                "server_validation_errors": len(issues),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@oncology_kb_group.command("materialize")
@click.option("--batch-id", required=True)
@click.option("--kind", type=click.Choice(["eligibility", "regimen"]), required=True)
@click.option("--database", required=True)
def materialize_cmd(batch_id: str, kind: str, database: str) -> None:
    """把已通过服务端校验的完整 batch 单事务投影到 typed authoring 表。"""
    validate_owned_database(database, required_exact=KNOWLEDGE_DATABASE)
    try:
        connection = _open_connection(database)
        preflight_connection(connection, database)
        result = materialize_staging(connection, batch_id, kind=kind)
    except Exception as exc:
        raise click.ClickException(safe_database_error(exc, database=database)) from None
    finally:
        if "connection" in locals():
            connection.close()
    click.echo(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))


def _release_connection(database: str, *, require_pointer_write: bool = False):
    """在创建 release adapter 前完成 owned-target 与 schema/权限预检。"""

    validate_owned_database(database, required_exact=KNOWLEDGE_DATABASE)
    try:
        connection = _open_connection(database)
        preflight_connection(connection, database)
        if require_pointer_write:
            allowed = connection.cursor().execute(
                "SELECT CASE WHEN "
                "HAS_PERMS_BY_NAME(N'kb_meta', N'SCHEMA', N'INSERT') = 1 AND "
                "HAS_PERMS_BY_NAME(N'kb_meta', N'SCHEMA', N'UPDATE') = 1 "
                "THEN 1 ELSE 0 END"
            ).fetchone()
            if allowed is None or not bool(allowed[0]):
                raise ReleaseStoreError("RELEASE_POINTER_PERMISSION_DENIED")
        return connection
    except ReleaseStoreError as exc:
        if "connection" in locals():
            connection.close()
        raise click.ClickException(
            f"oncology release preflight failed: {exc.code}"
        ) from None
    except Exception as exc:
        if "connection" in locals():
            connection.close()
        raise click.ClickException(
            safe_database_error(exc, database=database)
        ) from None


def _release_failure(exc: Exception, database: str) -> click.ClickException:
    if isinstance(exc, ReleaseStoreError):
        return click.ClickException(f"oncology release operation failed: {exc.code}")
    return click.ClickException(safe_database_error(exc, database=database))


def _release_result_payload(result: ReleaseOperationResult) -> dict[str, object]:
    return {
        "item_count": result.item_count,
        "release_id": result.release_id,
        "reused": result.reused,
        "status": result.status,
    }


@oncology_kb_group.command("release-authority")
@click.option("--database", required=True)
@click.option(
    "--pathology-bootstrap",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option("--pathology-bootstrap-checksum", required=True)
def release_authority_cmd(
    database: str,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
) -> None:
    """只读计算待审批固定的完整 DB/pathology authority checksums。"""

    connection = _release_connection(database)
    try:
        result = inspect_operational_authority(
            SqlServerReleaseStoreAdapter(connection),
            pathology_bootstrap=pathology_bootstrap,
            pathology_bootstrap_checksum=pathology_bootstrap_checksum,
        )
    except Exception as exc:
        raise _release_failure(exc, database) from None
    finally:
        connection.close()
    click.echo(
        json.dumps(
            {
                "curated_atom_count": result.curated_atom_count,
                "curated_authority_checksum": result.curated_authority_checksum,
                "pathology_bootstrap_checksum": result.pathology_bootstrap_checksum,
                "source_authority_checksum": result.source_authority_checksum,
                "source_item_count": result.source_item_count,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@oncology_kb_group.command("approve")
@click.option(
    "--entity-type",
    type=click.Choice(["eligibility", "regimen"], case_sensitive=False),
    required=True,
)
@click.option("--revision-id", required=True)
@click.option("--reviewer-id", envvar="JAVERT_KB_REVIEWER", required=True)
@click.option("--database", required=True)
def approve_cmd(
    entity_type: str,
    revision_id: str,
    reviewer_id: str,
    database: str,
) -> None:
    """依据 append-only 最新审核事件显式批准 typed revision。"""

    connection = _release_connection(database)
    try:
        result = approve_authoring_revision(
            SqlServerReleaseStoreAdapter(connection),
            entity_type=entity_type,
            revision_id=revision_id,
            reviewer_id=reviewer_id,
        )
    except Exception as exc:
        raise _release_failure(exc, database) from None
    finally:
        connection.close()
    click.echo(
        json.dumps(
            {
                "entity_type": result.entity_type,
                "lifecycle": result.lifecycle,
                "reused": result.reused,
                "revision_id": result.revision_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@oncology_kb_group.command("release-build")
@click.option("--database", required=True)
@click.option("--operator", envvar="JAVERT_KB_OPERATOR", required=True)
@click.option("--created-at", required=True)
@click.option(
    "--pathology-bootstrap",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option("--pathology-bootstrap-checksum", required=True)
@click.option("--source-authority-checksum", required=True)
@click.option("--curated-authority-checksum", required=True)
@click.option(
    "--releases-dir",
    envvar="JAVERT_ONCOLOGY_RELEASE_DIR",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
def release_build_cmd(
    database: str,
    operator: str,
    created_at: str,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
    source_authority_checksum: str,
    curated_authority_checksum: str,
    releases_dir: Path,
) -> None:
    """从锁定的 authoring 全集构建并登记不可缩减 candidate。"""

    connection = _release_connection(database)
    try:
        result = build_operational_release(
            SqlServerReleaseStoreAdapter(connection),
            operator=operator,
            created_at=created_at,
            pathology_bootstrap=pathology_bootstrap,
            pathology_bootstrap_checksum=pathology_bootstrap_checksum,
            expected_source_authority_checksum=source_authority_checksum,
            expected_curated_authority_checksum=curated_authority_checksum,
            releases_dir=releases_dir,
        )
    except Exception as exc:
        raise _release_failure(exc, database) from None
    finally:
        connection.close()
    click.echo(
        json.dumps(
            _release_result_payload(result), ensure_ascii=False, sort_keys=True
        )
    )


@oncology_kb_group.command("release-publish")
@click.option("--database", required=True)
@click.option("--release-id", required=True)
@click.option("--operator", envvar="JAVERT_KB_OPERATOR", required=True)
@click.option("--published-at", required=True)
@click.option(
    "--pathology-bootstrap",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option("--pathology-bootstrap-checksum", required=True)
@click.option("--source-authority-checksum", required=True)
@click.option("--curated-authority-checksum", required=True)
@click.option(
    "--releases-dir",
    envvar="JAVERT_ONCOLOGY_RELEASE_DIR",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
)
def release_publish_cmd(
    database: str,
    release_id: str,
    operator: str,
    published_at: str,
    pathology_bootstrap: Path,
    pathology_bootstrap_checksum: str,
    source_authority_checksum: str,
    curated_authority_checksum: str,
    releases_dir: Path,
) -> None:
    """重建并校验 candidate 后写 bundle、DB 状态与 active pointer。"""

    connection = _release_connection(database, require_pointer_write=True)
    try:
        result = publish_operational_release(
            SqlServerReleaseStoreAdapter(connection),
            release_id=release_id,
            operator=operator,
            published_at=published_at,
            pathology_bootstrap=pathology_bootstrap,
            pathology_bootstrap_checksum=pathology_bootstrap_checksum,
            expected_source_authority_checksum=source_authority_checksum,
            expected_curated_authority_checksum=curated_authority_checksum,
            releases_dir=releases_dir,
        )
    except Exception as exc:
        raise _release_failure(exc, database) from None
    finally:
        connection.close()
    click.echo(
        json.dumps(
            _release_result_payload(result), ensure_ascii=False, sort_keys=True
        )
    )


@oncology_kb_group.command("release-rollback")
@click.option("--database", required=True)
@click.option("--target-release-id", required=True)
@click.option("--operator", envvar="JAVERT_KB_OPERATOR", required=True)
@click.option("--reason", required=True)
@click.option("--occurred-at", required=True)
@click.option(
    "--releases-dir",
    envvar="JAVERT_ONCOLOGY_RELEASE_DIR",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    required=True,
)
def release_rollback_cmd(
    database: str,
    target_release_id: str,
    operator: str,
    reason: str,
    occurred_at: str,
    releases_dir: Path,
) -> None:
    """事务切回历史 published release，保留 bundle 与回滚事件。"""

    connection = _release_connection(database, require_pointer_write=True)
    try:
        result = rollback_operational_release(
            SqlServerReleaseStoreAdapter(connection),
            target_release_id=target_release_id,
            operator=operator,
            reason=reason,
            occurred_at=occurred_at,
            releases_dir=releases_dir,
        )
    except Exception as exc:
        raise _release_failure(exc, database) from None
    finally:
        connection.close()
    click.echo(
        json.dumps(
            _release_result_payload(result), ensure_ascii=False, sort_keys=True
        )
    )
