#!/usr/bin/env python3
"""发布已获授权的单病例 OCR 慢病试跑；只打印聚合统计，恢复仅限私有清单。"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import fcntl
import io
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile

TAG = "慢病"
DEFAULT_EXPECTED_ROOT = Path("/home/admin2/javert")
NOTE_FIELDS = ["住院号", "事件时间", "阶段", "子阶段", "内容", "来源文件"]


def atomic_json(target: Path, value: dict) -> None:
    """私有检查点：失败保留上一版，不留下半份 JSON。"""
    fd, name = tempfile.mkstemp(prefix=".chronic-json-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def publication_target(args, cfg, sql) -> dict:
    """绑定受控 root 与实际 SQL 身份；返回值只进入私有清单。"""
    root = args.expected_root.resolve()
    overlay = args.overlay.resolve() if args.overlay else None
    database = cfg.audit_db_path.resolve()
    if (not cfg.sql_enabled or not args.backup or overlay is None
            or cfg.resolve("").resolve() != root
            or not database.is_relative_to(root) or not database.is_file()
            or overlay != root / "data_import" or cfg.sql_database != "zadig"):
        raise ValueError("PUBLISH_TARGET_INVALID")
    if any(getattr(sql.config, key) != getattr(cfg, key)
           for key in ("sql_host", "sql_port", "sql_database")):
        raise ValueError("SQL_CONFIG_BINDING_CONFLICT")
    from sqlalchemy import text

    engine = sql.get_engine()
    if engine is None:
        raise ValueError("SQL_UNAVAILABLE")
    with engine.connect() as conn:
        actual_db, machine = conn.execute(text(
            "SELECT DB_NAME(), CONVERT(nvarchar(128), SERVERPROPERTY('MachineName'))"
        )).one()
    if actual_db != cfg.sql_database or not isinstance(machine, str) or not machine.strip():
        raise ValueError("SQL_IDENTITY_CONFLICT")
    # 地址可为 IP/别名，MachineName 不与地址字符串硬比；恢复时两者均须一致。
    return {"host": cfg.sql_host, "port": cfg.sql_port, "db": actual_db,
            "machine": machine, "sqlite": str(database), "overlay": str(overlay)}


def validate_paths(args, database: Path | None = None) -> None:
    """在任何写入之前拒绝受控文件别名及隔离目录交叠。"""
    private = args.private_dir.resolve()
    protected = [private / "results.json", private / "summary.json", private / ".pilot.lock"]
    if args.backup:
        protected.append(args.backup.resolve() / "case_notes.before.csv")
    if args.overlay:
        overlay = args.overlay.resolve()
        protected.append(overlay / "case_notes.csv")
        for directory in (private, args.backup.resolve() if args.backup else private):
            if directory.is_relative_to(overlay) or overlay.is_relative_to(directory):
                raise ValueError("PRIVATE_OVERLAY_PATH_CONFLICT")
    if args.bundle.resolve() in {p.resolve() for p in protected}:
        raise ValueError("BUNDLE_PATH_CONFLICT")
    if database is not None and database.resolve() in {
            args.bundle.resolve(), *(p.resolve() for p in protected)}:
        raise ValueError("DATABASE_PATH_CONFLICT")


def result_content(result) -> dict:
    """比较所有可持久化结果字段；SQL DATETIME2 不保留 UTC 时区标记。"""
    value = result.model_dump(mode="json", exclude={"precheck_tag", "started_at"})
    value["started_at"] = result.started_at.replace(tzinfo=None).isoformat()
    value["anchors_json"] = json.loads(result.anchors_json) if result.anchors_json else None
    return value


def check_existing(store, sql, results) -> tuple[set[str], set[str]]:
    """整批只读检查。SQL 查询错误必须抛出，不能被解释为没有历史记录。"""
    from sqlalchemy import text

    expected = {r.run_id: r for r in results}
    rule_ids = {r.rule_id for r in results}
    case_id = results[0].patient_id
    local_ids = set()
    for row in store.find_by_patient(case_id):
        if row.rule_id in rule_ids and row.run_id not in expected:
            raise ValueError("CASE_RESULT_CONFLICT")
    for run_id, result in expected.items():
        prior = store.find_by_run_id(run_id)
        if prior is not None:
            if (result_content(prior) != result_content(result)
                    or store.publication_metadata(run_id) != (TAG, None)):
                raise ValueError("LOCAL_RESULT_CONFLICT")
            local_ids.add(run_id)
    params = {f"r{i}": run_id for i, run_id in enumerate(expected)}
    slots = ", ".join(f":{key}" for key in params)
    params["pid"] = case_id
    with sql.get_engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT run_id, patient_id, rule_id, batch_tag, replay_key "
            "FROM javert_audit_runs WHERE patient_id = :pid "
            f"OR run_id IN ({slots})"
        ), params).mappings().all()
    remote_ids = set()
    for row in rows:
        run_id = row["run_id"]
        if row["rule_id"] not in rule_ids and run_id not in expected:
            continue
        if (run_id not in expected or row["patient_id"] != case_id
                or row["rule_id"] != expected[run_id].rule_id
                or row["batch_tag"] != TAG or row["replay_key"] is not None):
            raise ValueError("REMOTE_RESULT_CONFLICT")
        prior = sql.find_audit_by_run_id(run_id)
        if prior is None or result_content(prior) != result_content(expected[run_id]):
            raise ValueError("REMOTE_RESULT_CONFLICT")
        remote_ids.add(run_id)
    return local_ids, remote_ids


def quality_counts(results) -> dict:
    """只输出固定类别计数；内部服务失败留存清单，等待显式重试决策。"""
    counts = dict(blocked_rules=0, asset_invalid=0, asset_not_loaded=0,
                  extraction_failed=0, extraction_incomplete=0, candidate_rejected=0)
    for result in results:
        flags = set(result.clinical_criteria_evaluation.data_quality_flags)
        invalid = "CRITERIA_ASSET_INVALID" in flags
        unloaded = "ASSET_NOT_LOADED" in flags and not (
            result.rule_id == "CD10" and "CHRONIC_DISABLED" in flags)
        failed = "EXTRACTION_FAILED" in flags
        incomplete = bool(flags & {"INCOMPLETE", "EXTRACTION_INCOMPLETE", "EXTRACTION_TRUNCATED"})
        counts["asset_invalid"] += invalid
        counts["asset_not_loaded"] += unloaded
        counts["extraction_failed"] += failed
        counts["extraction_incomplete"] += incomplete
        counts["candidate_rejected"] += "CANDIDATE_REJECTED" in flags
        counts["blocked_rules"] += bool(invalid or unloaded or failed or incomplete)
    return counts


def publish_results(store, sql, results, rules, notes, overlay, backup) -> None:
    if quality_counts(results)["blocked_rules"]:
        raise ValueError("PUBLICATION_QUALITY_PAUSED")
    local_ids, remote_ids = check_existing(store, sql, results)
    append_notes(overlay / "case_notes.csv", notes, backup)
    for result in results:
        if result.run_id not in local_ids:
            store.write(result, batch_tag=TAG, replay_key=None)
        if result.run_id not in remote_ids:
            if not sql.write_audit(result, rules[result.rule_id], batch_tag=TAG,
                                   triggered_by="chronic-pdf-pilot", replay_key=None):
                raise ValueError("EXACT_SYNC_FAILED")
    local_ids, remote_ids = check_existing(store, sql, results)
    expected = {r.run_id for r in results}
    if local_ids != expected or remote_ids != expected:
        raise ValueError("RESULT_RECONCILIATION_FAILED")
    for run_id in expected:
        store.mark_synced(run_id)


def prepare_notes(bundle: dict) -> list[dict]:
    """拒绝缺页/重复页和不安全病例键；费用/身份页不得变成临床证据。"""
    case_id = bundle.get("case_id", "")
    if not re.fullmatch(r"chronic-[a-f0-9]{32}", case_id):
        raise ValueError("CASE_KEY_INVALID")
    pages = bundle.get("pages", [])
    if not pages or [p.get("page") for p in pages] != list(range(1, len(pages) + 1)):
        raise ValueError("PAGE_COVERAGE_INVALID")
    if any(p.get("kind") not in {"clinical", "fee", "financial", "administrative", "unreadable"} for p in pages):
        raise ValueError("PAGE_KIND_INVALID")
    notes = []
    for page in pages:
        if page["kind"] != "clinical":
            continue
        if not isinstance(page.get("text"), str) or not page["text"].strip():
            raise ValueError("CLINICAL_PAGE_EMPTY")
        notes.append(dict(zip(NOTE_FIELDS, [case_id, "", "OCR临床材料（未人工核对）",
                     "扫描页", "[OCR未人工核对；日期、数值和否定表述须复核]\n" + page["text"],
                     f"page-{page['page']}"])))
    if not notes:
        raise ValueError("NO_CLINICAL_PAGES")
    return notes


def append_notes(target: Path, notes: list[dict], backup: Path) -> bool:
    """原文件字节保留，整批幂等追加；同病例不同内容拒绝覆盖。"""
    original = target.read_bytes() if target.exists() else b""
    reader = csv.DictReader(io.StringIO(original.decode("utf-8-sig")))
    fields = reader.fieldnames or NOTE_FIELDS
    if not set(NOTE_FIELDS).issubset(fields):
        raise ValueError("OVERLAY_SCHEMA_MISMATCH")
    existing = [r for r in reader if r.get("住院号") == notes[0]["住院号"]]
    if existing:
        if [{k: r.get(k, "") for k in NOTE_FIELDS} for r in existing] != notes:
            raise ValueError("CASE_OVERLAY_CONFLICT")
        return False
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup.chmod(0o700)
    if original and not (backup / "case_notes.before.csv").exists():
        (backup / "case_notes.before.csv").write_bytes(original)
        (backup / "case_notes.before.csv").chmod(0o600)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    if not original:
        writer.writeheader()
    writer.writerows(notes)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".chronic-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(original)
            if original and not original.endswith(b"\n"):
                handle.write(b"\n")
            handle.write(stream.getvalue().encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        # Refuse to replace another writer's concurrent change.
        if (target.read_bytes() if target.exists() else b"") != original:
            raise ValueError("OVERLAY_CHANGED")
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--overlay", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--expected-root", type=Path, default=DEFAULT_EXPECTED_ROOT)
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    from javert.config import get_config
    from javert.store.sqlserver_store import SqlServerStore
    from javert.tools.tool_executor import ToolExecutor

    cfg = get_config()
    validate_paths(args, cfg.audit_db_path)
    sql = SqlServerStore(cfg) if args.publish else None
    target = publication_target(args, cfg, sql) if args.publish else None
    bundle = json.loads(args.bundle.read_text())
    notes = prepare_notes(bundle)
    private = args.private_dir.resolve()
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    private.chmod(0o700)
    # 同一清单的计算、原子检查点及恢复串行，避免两个进程生成不同 run。
    with (private / ".pilot.lock").open("a") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        run_pilot(args, cfg, sql, target, bundle, notes, private)


def run_pilot(args, cfg, sql, target, bundle, notes, private):
    from javert.audit.result import AuditResult
    from javert.audit.rule_loader import load_all
    from javert.audit.runner import Runner
    from javert.chronic.knowledge import load_criteria_asset
    from javert.config import PROJECT_ROOT
    from javert.data.csv_loader import CsvLoader
    from javert.store.audit_store import SqliteStore
    from javert.store.result_persister import _attach_verified_hits
    from javert.tools.tool_executor import ToolExecutor

    case_id = bundle["case_id"]
    rules = {k: r for k, r in load_all(cfg.rules_path).items() if r.rule_kind == "chronic_disease_qualification"}
    if set(rules) != {f"CD{i:02d}" for i in range(1, 21)}:
        raise ValueError("CHRONIC_RULE_SET_INCOMPLETE")
    asset = load_criteria_asset(PROJECT_ROOT / "configs/chronic_disease_criteria.json")
    state_path = private / "results.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        if state["case_id"] != case_id or state["asset_checksum"] != asset.asset_checksum or state["notes"] != notes:
            raise ValueError("PRIVATE_MANIFEST_CONFLICT")
        if target is not None and state.get("target") not in (None, target):
            raise ValueError("PRIVATE_TARGET_CONFLICT")
    else:
        state = {"case_id": case_id, "asset_checksum": asset.asset_checksum, "notes": notes, "results": []}
    results = [AuditResult.model_validate(r) for r in state["results"]]
    if (len({r.rule_id for r in results}) != len(results)
            or len({r.run_id for r in results}) != len(results)
            or any(r.patient_id != case_id or r.rule_id not in rules
                   or r.clinical_criteria_evaluation is None or r.verdict != "INCONCLUSIVE"
                   for r in results)):
        raise ValueError("PRIVATE_RESULTS_INVALID")
    if target is not None:
        state["target"] = target
    atomic_json(state_path, state)
    with tempfile.TemporaryDirectory(prefix=".chronic-input-", dir=private) as temporary:
        note_path, fee_path = Path(temporary) / "case_notes.csv", Path(temporary) / "shi_fee.csv"
        with note_path.open("w", newline="", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            writer = csv.DictWriter(f, fieldnames=NOTE_FIELDS)
            writer.writeheader()
            writer.writerows(notes)
        fee_path.write_text("bah,med_list_codg,medins_list_name,cnt,pric,det_item_fee_sumamt,fee_ocur_time\n")
        loader = CsvLoader(note_path, fee_path)
        for rule_id in sorted(rules):
            if any(r.rule_id == rule_id for r in results):
                continue
            # CD10 默认只产关闭状态，既不读患者资料也不调用 LLM。
            mode = "off" if rule_id == "CD10" else "shadow"
            runner = Runner(ToolExecutor(), config=cfg.model_copy(update={"chronic_disease_criteria": mode}), loader=loader)
            result = runner.audit(rules[rule_id], case_id)
            evaluation = result.clinical_criteria_evaluation
            if evaluation is None or result.verdict != "INCONCLUSIVE":
                raise ValueError("SHADOW_RESULT_INVALID")
            evaluation.data_quality_flags = sorted(set(evaluation.data_quality_flags + ["OCR_UNVERIFIED", "FEE_COMPLETENESS_NOT_VERIFIED"]))
            results.append(result)
            state["results"] = [r.model_dump(mode="json") for r in results]
            atomic_json(state_path, state)
            print(json.dumps({"rule": rule_id, "completed": len(results), "total": len(rules)}), flush=True)
        summary = {"rules": len(results), "evaluated_rules": 19, "disabled_rules": ["CD10"],
                   "clinical_pages": len(notes), "qualification": dict(Counter(r.clinical_criteria_evaluation.qualification_disposition for r in results)),
                   "candidate_rules": [r.rule_id for r in results if "CHRONIC_CANDIDATE_MATCHED" in r.clinical_criteria_evaluation.data_quality_flags],
                   "published": False, "quality_counts": quality_counts(results)}
        if args.publish:
            if summary["quality_counts"]["blocked_rules"]:
                summary["status"] = "paused"
                atomic_json(private / "summary.json", summary)
                print(json.dumps(summary, ensure_ascii=False), flush=True)
                raise ValueError("PUBLICATION_QUALITY_PAUSED")
            for result in results:
                _attach_verified_hits(result, rules[result.rule_id], loader)
            state["results"] = [dict(r.model_dump(mode="json"), anchors_json=r.anchors_json) for r in results]
            atomic_json(state_path, state)
            store = SqliteStore(cfg.audit_db_path)
            try:
                publish_results(store, sql, results, rules, notes, args.overlay.resolve(), args.backup.resolve())
                summary["published"] = True
                summary["reconciled"] = len(results)
            finally:
                store.close()
        atomic_json(private / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never print exception values: provider/DB errors may include patient text.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), flush=True)
        sys.exit(1)
