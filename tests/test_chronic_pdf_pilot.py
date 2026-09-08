import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

spec = importlib.util.spec_from_file_location("chronic_pdf_pilot", Path(__file__).resolve().parents[1] / "scripts/run_chronic_pdf_pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def bundle():
    return {"case_id": "chronic-" + "a" * 32, "pages": [
        {"page": 1, "kind": "administrative", "text": "合成身份资料，不作为证据"},
        {"page": 2, "kind": "clinical", "text": "合成病历：明确糖尿病病史。"},
        {"page": 3, "kind": "financial", "text": "合成费用汇总，不作为证据"},
    ]}


def test_source_identity_stays_original_and_requires_source_quote(tmp_path):
    data = bundle()
    data['pages'][1]['text'] = '姓名：合成甲；住院号：00123。合成病历。'
    data['source_identity'] = {
        'patient_name': {'value': '合成甲', 'source_page': 2, 'quote': '姓名：合成甲'},
        'visit_id': {'value': '00123', 'source_page': 2, 'quote': '住院号：00123'},
    }
    notes = pilot.prepare_notes(data)
    assert notes[0]['source_patient_name'] == '合成甲'
    assert notes[0]['source_visit_id'] == '00123'
    target = tmp_path / 'case_notes.csv'
    old = '住院号,事件时间,阶段,子阶段,内容,来源文件\nother-case,,入院,,旧合成内容,page-old\n'
    target.write_text(old)
    pilot.append_notes(target, notes, tmp_path / 'backup')
    import csv
    with target.open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]['内容'] == '旧合成内容' and rows[0]['source_patient_name'] == ''
    assert rows[1]['source_visit_id'] == '00123'
    assert pilot.append_notes(target, notes, tmp_path / 'backup') is False
    data['source_identity']['patient_name']['value'] = '未经原文支持'
    with pytest.raises(ValueError, match='SOURCE_IDENTITY'):
        pilot.prepare_notes(data)


def test_page_coverage_and_clinical_isolation():
    data = bundle()
    notes = pilot.prepare_notes(data)
    assert len(notes) == 1 and notes[0]["来源文件"] == "page-2"
    assert "合成病历" in notes[0]["内容"] and "未人工核对" in notes[0]["内容"]
    data["pages"][1]["page"] = 1
    with pytest.raises(ValueError, match="PAGE_COVERAGE"):
        pilot.prepare_notes(data)


def test_append_preserves_original_bytes_and_replay_refuses_conflicting_case(tmp_path):
    target = tmp_path / "overlay" / "case_notes.csv"
    target.parent.mkdir()
    original = "住院号,事件时间,阶段,子阶段,内容,来源文件\r\nsynthetic-old,,入院,,旧合成病历,original\r\n".encode()
    target.write_bytes(original)
    notes = pilot.prepare_notes(bundle())
    backup = tmp_path / "backup"
    assert pilot.append_notes(target, notes, backup)
    assert target.read_bytes().startswith(original)
    assert (backup / "case_notes.before.csv").read_bytes() == original
    first = target.read_bytes()
    assert pilot.append_notes(target, notes, backup) is False
    assert target.read_bytes() == first
    notes[0]["内容"] = "不同的合成病历"
    with pytest.raises(ValueError, match="CASE_OVERLAY_CONFLICT"):
        pilot.append_notes(target, notes, backup)
    assert target.read_bytes() == first
    assert target.stat().st_mode & 0o777 == 0o600


def synthetic_result(number=1):
    from javert.audit.result import AuditResult
    from javert.chronic.runtime import _empty_result

    rule_id = f"CD{number:02d}"
    evaluation = _empty_result(SimpleNamespace(rule_id=rule_id), "shadow", "CHRONIC_DISABLED")
    if number != 10:
        evaluation.data_quality_flags = ["SHADOW_ONLY"]
    evaluation.data_quality_flags += ["OCR_UNVERIFIED", "FEE_COMPLETENESS_NOT_VERIFIED"]
    return AuditResult(run_id=f"aud_SYNTHETIC{number:03d}", rule_id=rule_id,
                       patient_id=bundle()["case_id"], verdict="INCONCLUSIVE",
                       started_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
                       clinical_criteria_evaluation=evaluation)


class MemoryStore:
    def __init__(self):
        self.rows, self.tags, self.synced = {}, {}, set()

    def find_by_patient(self, patient_id):
        return [r for r in self.rows.values() if r.patient_id == patient_id]

    def find_by_run_id(self, run_id):
        return self.rows.get(run_id)

    def publication_metadata(self, run_id):
        return self.tags.get(run_id), None

    def write(self, result, *, batch_tag, replay_key=None):
        assert result.run_id not in self.rows, "duplicate INSERT"
        self.rows[result.run_id] = result.model_copy(deep=True)
        self.tags[result.run_id] = batch_tag

    def mark_synced(self, run_id):
        self.synced.add(run_id)


class MemorySql(MemoryStore):
    def __init__(self):
        super().__init__()
        self.fail_write = False
        self.fail_query = False
        self.writes = []
        self.database, self.machine = "zadig", "synthetic-machine"
        self.config = SimpleNamespace(sql_host="synthetic-host", sql_port=1433, sql_database="zadig")

    def get_engine(self):
        engine = MagicMock()
        engine.connect.return_value.__enter__.return_value.execute.side_effect = self.execute
        return engine

    def execute(self, query, params=None):
        if self.fail_query:
            raise RuntimeError("synthetic connection failure")
        if "DB_NAME()" in str(query):
            return SimpleNamespace(one=lambda: (self.database, self.machine))
        assert "patient_id = :pid" in str(query) and "run_id IN" in str(query)
        rows = [dict(run_id=r.run_id, patient_id=r.patient_id, rule_id=r.rule_id,
                     batch_tag=self.tags[r.run_id], replay_key=None)
                for r in self.rows.values()
                if r.patient_id == params["pid"] or r.run_id in params.values()]
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))

    def find_audit_by_run_id(self, run_id):
        return self.find_by_run_id(run_id)

    def write_audit(self, result, rule, **kwargs):
        if self.fail_write:
            return False
        self.writes.append(result.run_id)
        self.write(result, batch_tag=kwargs["batch_tag"])
        return True


@pytest.mark.parametrize("side,change", [
    ("local", "extra"), ("remote", "extra"),
    ("local", "content"), ("remote", "content"),
    ("local", "tag"), ("remote", "tag"), ("remote", "patient"),
])
def test_all_conflicts_precede_overlay_and_any_insert(tmp_path, side, change):
    results = [synthetic_result(i) for i in range(1, 21)]
    local, remote = MemoryStore(), MemorySql()
    existing = results[-1].model_copy(deep=True)
    if change == "extra":
        existing.run_id = "aud_FOREIGN00001"
    elif change == "content":
        existing.reasoning = "different synthetic content"
    elif change == "patient":
        existing.patient_id = "synthetic-other"
    owner = local if side == "local" else remote
    owner.write(existing, batch_tag="wrong" if change == "tag" else pilot.TAG)
    before = set(local.rows), set(remote.rows)
    with pytest.raises(ValueError, match="CONFLICT"):
        pilot.publish_results(local, remote, results, {r.rule_id: None for r in results},
                              pilot.prepare_notes(bundle()), tmp_path / "overlay", tmp_path / "backup")
    assert (set(local.rows), set(remote.rows)) == before
    assert not (tmp_path / "overlay").exists()


def test_resume_syncs_only_manifest_runs_and_is_idempotent(tmp_path):
    local, remote = MemoryStore(), MemorySql()
    result = synthetic_result()
    unrelated = synthetic_result(2).model_copy(update={"patient_id": "synthetic-other"})
    local.write(unrelated, batch_tag="other")
    args = (local, remote, [result], {result.rule_id: None}, pilot.prepare_notes(bundle()),
            tmp_path / "overlay", tmp_path / "backup")
    remote.fail_write = True
    with pytest.raises(ValueError, match="SYNC_FAILED"):
        pilot.publish_results(*args)
    assert result.run_id in local.rows and not local.synced
    remote.fail_write = False
    pilot.publish_results(*args)
    pilot.publish_results(*args)
    assert remote.writes == [result.run_id]
    assert local.synced == {result.run_id}
    assert unrelated.run_id not in remote.rows


def test_remote_query_failure_is_not_an_empty_database(tmp_path):
    local, remote = MemoryStore(), MemorySql()
    remote.fail_query = True
    result = synthetic_result()
    with pytest.raises(RuntimeError):
        pilot.publish_results(local, remote, [result], {result.rule_id: None},
                              pilot.prepare_notes(bundle()), tmp_path / "overlay", tmp_path / "backup")
    assert not local.rows and not (tmp_path / "overlay").exists()


def test_atomic_manifest_keeps_previous_checkpoint_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "results.json"
    pilot.atomic_json(target, {"completed": 1})
    def interrupt(*args):
        raise OSError("synthetic replace failure")
    monkeypatch.setattr(pilot.os, "replace", interrupt)
    with pytest.raises(OSError):
        pilot.atomic_json(target, {"completed": 2})
    assert json.loads(target.read_text()) == {"completed": 1}
    assert list(tmp_path.iterdir()) == [target]
    assert target.stat().st_mode & 0o777 == 0o600


@pytest.fixture
def target_setup(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    database = root / "synthetic.sqlite"
    database.touch()  # 只作路径校验，不打开数据库。
    args = SimpleNamespace(expected_root=root, overlay=root / "data_import",
                           backup=tmp_path / "backup", private_dir=tmp_path / "private",
                           bundle=tmp_path / "synthetic-bundle.json", publish=True)
    cfg = SimpleNamespace(sql_enabled=True, sql_database="zadig", sql_host="synthetic-host",
                          sql_port=1433, audit_db_path=database, resolve=lambda _: root,
                          rules_path=root / "rules")
    return args, cfg, MemorySql()


@pytest.mark.parametrize("change", ["root", "overlay", "sqlite", "db", "actual_db", "binding", "unreachable"])
def test_publish_target_rejects_wrong_or_unavailable_destination(target_setup, change, tmp_path):
    args, cfg, sql = target_setup
    if change == "root":
        args.expected_root = tmp_path / "different"
    elif change == "overlay":
        args.overlay = tmp_path / "different"
    elif change == "sqlite":
        cfg.audit_db_path = tmp_path / "outside.sqlite"
        cfg.audit_db_path.touch()
    elif change == "db":
        cfg.sql_database = "not-owned"
    elif change == "actual_db":
        sql.database = "not-owned"
    elif change == "binding":
        sql.config.sql_host = "different-host"
    else:
        sql.fail_query = True
    with pytest.raises((ValueError, RuntimeError)):
        pilot.publication_target(args, cfg, sql)
    assert not args.overlay.exists() and not args.private_dir.exists()


def test_target_binding_is_private_and_contains_no_credentials(target_setup, capsys):
    args, cfg, sql = target_setup
    cfg.sql_password = "synthetic-secret-never-serialized"
    target = pilot.publication_target(args, cfg, sql)
    assert target == dict(host=cfg.sql_host, port=cfg.sql_port, db="zadig", machine=sql.machine,
                          sqlite=str(cfg.audit_db_path), overlay=str(args.overlay))
    assert cfg.sql_password not in json.dumps(target)
    assert capsys.readouterr().out == ""
    assert pilot.DEFAULT_EXPECTED_ROOT == Path("/home/admin2/javert")


@pytest.mark.parametrize("alias", ["same", "symlink", "backup", "bundle", "database"])
def test_path_alias_rejected_before_writes(target_setup, alias):
    args, cfg, _ = target_setup
    args.overlay.mkdir()
    original = args.overlay / "case_notes.csv"
    original.write_text("synthetic preserved contents")
    if alias == "same":
        args.private_dir = args.overlay
    elif alias == "symlink":
        args.private_dir.symlink_to(args.overlay, target_is_directory=True)
    elif alias == "backup":
        args.backup = args.overlay
    elif alias == "bundle":
        args.bundle = original
    else:
        cfg.audit_db_path = original
    with pytest.raises(ValueError, match="PATH_CONFLICT"):
        pilot.validate_paths(args, cfg.audit_db_path)
    assert original.read_text() == "synthetic preserved contents"


@pytest.fixture
def synthetic_runtime(monkeypatch, target_setup):
    import javert.audit.rule_loader
    import javert.audit.runner
    import javert.chronic.knowledge
    import javert.data.csv_loader
    import javert.store.audit_store
    import javert.store.result_persister

    args, cfg, sql = target_setup
    args.private_dir.mkdir()
    rules = {f"CD{i:02d}": SimpleNamespace(rule_kind="chronic_disease_qualification") for i in range(1, 21)}
    monkeypatch.setattr(javert.audit.rule_loader, "load_all", lambda _: rules)
    monkeypatch.setattr(javert.chronic.knowledge, "load_criteria_asset", lambda _: SimpleNamespace(asset_checksum="synthetic"))
    def forbidden_audit(*args, **kwargs):
        raise AssertionError("cached synthetic results must not call LLM")
    monkeypatch.setattr(javert.audit.runner, "Runner", forbidden_audit)
    monkeypatch.setattr(javert.store.result_persister, "_attach_verified_hits", lambda *args: None)
    state = {"case_id": bundle()["case_id"], "asset_checksum": "synthetic",
             "notes": pilot.prepare_notes(bundle()),
             "results": [synthetic_result(i).model_dump(mode="json") for i in range(1, 21)]}
    pilot.atomic_json(args.private_dir / "results.json", state)
    store = MemoryStore()
    store.close = lambda: None
    monkeypatch.setattr(javert.store.audit_store, "SqliteStore", lambda _: store)
    return args, cfg, sql, store, state


@pytest.mark.parametrize("stage", ["fee_write", "loader"])
def test_temporary_ocr_is_removed_on_setup_failure(synthetic_runtime, monkeypatch, stage):
    import javert.data.csv_loader

    args, cfg, sql, store, state = synthetic_runtime
    if stage == "fee_write":
        write_text = Path.write_text
        def fail_fee(path, *args, **kwargs):
            if path.name == "shi_fee.csv":
                raise OSError("synthetic disk full")
            return write_text(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", fail_fee)
    else:
        def fail_loader(notes, fees):
            assert notes.parent.stat().st_mode & 0o777 == 0o700
            assert notes.stat().st_mode & 0o777 == 0o600
            raise OSError("synthetic loader failure")
        monkeypatch.setattr(javert.data.csv_loader, "CsvLoader", fail_loader)
    with pytest.raises(OSError):
        pilot.run_pilot(args, cfg, sql, None, bundle(), state["notes"], args.private_dir)
    assert list(args.private_dir.iterdir()) == [args.private_dir / "results.json"]
    assert not args.overlay.exists() and not store.rows


def test_main_publishes_and_replays_without_exposing_target_or_notes(synthetic_runtime, monkeypatch, capsys):
    import javert.config
    import javert.store.sqlserver_store

    args, cfg, sql, local, state = synthetic_runtime
    args.bundle.write_text(json.dumps(bundle()))
    monkeypatch.setattr(javert.config, "get_config", lambda: cfg)
    monkeypatch.setattr(javert.store.sqlserver_store, "SqlServerStore", lambda _: sql)
    monkeypatch.setattr(pilot.sys, "argv", ["pilot", "--bundle", str(args.bundle),
        "--private-dir", str(args.private_dir), "--publish", "--overlay", str(args.overlay),
        "--backup", str(args.backup), "--expected-root", str(args.expected_root)])
    # main 的进程级隐私设置不污染 pytest 余下用例。
    monkeypatch.setattr(pilot.os, "umask", lambda _: None)
    monkeypatch.setattr(pilot.logging, "disable", lambda _: None)
    pilot.main()
    pilot.main()
    output = capsys.readouterr().out
    summaries = [json.loads(line) for line in output.splitlines()]
    assert len(summaries) == 2 and all(s["published"] and s["reconciled"] == 20 for s in summaries)
    assert len(sql.writes) == 20 and len(local.synced) == 20
    assert not list(args.private_dir.glob(".chronic-input-*"))
    assert cfg.sql_host not in output and str(args.expected_root) not in output
    assert "合成病历" not in output and bundle()["case_id"] not in output
    checkpoint = json.loads((args.private_dir / "results.json").read_text())
    assert checkpoint["target"]["machine"] == sql.machine
    # 同一配置地址换了实际 SQL 机器时，旧私有清单拒绝恢复。
    sql.machine = "different-synthetic-machine"
    with pytest.raises(ValueError, match="PRIVATE_TARGET_CONFLICT"):
        pilot.main()
    assert len(sql.writes) == 20


@pytest.mark.parametrize("flag", ["CRITERIA_ASSET_INVALID", "EXTRACTION_FAILED",
                                  "EXTRACTION_INCOMPLETE", "INCOMPLETE", "EXTRACTION_TRUNCATED"])
def test_bad_quality_pauses_without_erasing_manifest(synthetic_runtime, flag, capsys):
    args, cfg, sql, local, state = synthetic_runtime
    state["results"][0]["clinical_criteria_evaluation"]["data_quality_flags"].append(flag)
    manifest = args.private_dir / "results.json"
    pilot.atomic_json(manifest, state)
    before = manifest.read_bytes()
    with pytest.raises(ValueError, match="PUBLICATION_QUALITY_PAUSED"):
        pilot.run_pilot(args, cfg, sql, None, bundle(), state["notes"], args.private_dir)
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "paused" and not summary["published"]
    assert summary["quality_counts"]["blocked_rules"] == 1
    assert manifest.read_bytes() == before
    assert not local.rows and not sql.writes and not args.overlay.exists()
    assert not list(args.private_dir.glob(".chronic-input-*"))


def test_rejected_candidate_and_disabled_cd10_allow_review_publication(tmp_path):
    local, sql = MemoryStore(), MemorySql()
    results = [synthetic_result(), synthetic_result(10)]
    results[0].clinical_criteria_evaluation.data_quality_flags.append("CANDIDATE_REJECTED")
    counts = pilot.quality_counts(results)
    assert counts["blocked_rules"] == 0 and counts["candidate_rejected"] == 1
    pilot.publish_results(local, sql, results, {r.rule_id: None for r in results},
                          pilot.prepare_notes(bundle()), tmp_path / "overlay", tmp_path / "backup")
    assert len(sql.writes) == 2
