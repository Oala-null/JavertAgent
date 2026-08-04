from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest
from click.testing import CliRunner

from javert.cli import main
from javert.oncology.authoring.ids import checksum
from javert.oncology.authoring.release import (
    ASSET_FILENAMES,
    load_release_bundle,
    resolve_active_release_assets,
    validate_compiled_release,
)
from javert.oncology.authoring.release_store import (
    _TABLE_SPECS,
    _authority_manifests,
    _canonical_authoring_tables,
    _curated_projection,
    _initial_coverage,
    _load_pathology_bootstrap,
    ReleaseStoreError,
    approve_authoring_revision,
    build_operational_release,
    inspect_operational_authority,
    publish_operational_release,
    rollback_operational_release,
)
from javert.oncology.knowledge import asset_payload_checksum


SOURCE_CHECKSUM = "sha256:" + "1" * 64
PATHOLOGY_CHECKSUM = "sha256:" + "2" * 64
SOURCE_REF = {
    "source_id": "source-doc-1",
    "source_fragment_id": "source-fragment-1",
    "title": "合成受控来源",
    "version": "1.0",
    "publication_date": None,
    "effective_date": None,
    "retrieval_date": "2026-07-21",
    "checksum": SOURCE_CHECKSUM,
}
PATHOLOGY_REF = {
    "source_id": "pathology-source-1",
    "source_fragment_id": None,
    "title": "合成已批准病理来源",
    "version": "1.0",
    "publication_date": None,
    "effective_date": None,
    "retrieval_date": "2026-07-21",
    "checksum": PATHOLOGY_CHECKSUM,
}


def _digest(label: str) -> str:
    return checksum({"label": label})


def _review_event(
    event_id: str,
    entity_type: str,
    entity_id: str,
    *,
    decision: str = "APPROVE",
    reviewer: str = "domain-reviewer",
    reviewed_at: str = "2026-07-21T10:00:00Z",
    previous_event_id: str | None = None,
) -> dict[str, Any]:
    field_name = {
        "branch": "branch",
        "condition_node": "condition",
        "regimen": "revision",
        "alias": "alias",
        "context": "context",
        "component": "component",
        "drug_class": "drug_class",
        "eligibility_revision": "effective_window",
    }[entity_type]
    return {
        "review_event_id": event_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "field_name": field_name,
        "decision": decision,
        "expert_value_json": None,
        "comment": "已审核",
        "evidence_reference": "case:synthetic",
        "reviewer_id": reviewer,
        "reviewed_at": reviewed_at,
        "reviewed_content_checksum": _digest(entity_id),
        "previous_event_id": previous_event_id,
    }


def _authoring_tables() -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {
        table: [] for table in _TABLE_SPECS
    }
    tables["source_document"] = [
        {
            "source_document_id": "source-doc-1",
            "source_type": "INSURANCE_PAYMENT",
            "title": "合成受控来源",
            "document_version": "1.0",
            "document_year": 2026,
            "retrieval_date": "2026-07-21",
            "content_checksum": SOURCE_CHECKSUM,
        }
    ]
    tables["source_fragment"] = [
        {
            "source_fragment_id": "source-fragment-1",
            "source_document_id": "source-doc-1",
            "anchor": "clause-1",
            "page_numbers_json": "[1]",
            "original_text": "合成限定条件",
            "content_checksum": _digest("source-fragment-1"),
        }
    ]
    tables["drug_concept"] = [
        {
            "drug_concept_id": "drug-concept-1",
            "canonical_name": "合成肿瘤药",
            "normalized_name": "合成肿瘤药",
            # 药品概念是被批准规则引用的支持实体，不伪造独立审批状态。
            "lifecycle": "DRAFT",
            "content_checksum": _digest("drug-concept-1"),
        }
    ]
    tables["drug_product"] = [
        {
            "drug_product_id": "drug-product-1",
            "drug_concept_id": "drug-concept-1",
            "product_name": "合成肿瘤药注射液",
            "dosage_form": "注射剂",
            "manufacturer": "合成厂商",
            "source_kind": "INSURANCE",
            "content_checksum": _digest("drug-product-1"),
        }
    ]
    tables["drug_code_xref"] = [
        {
            "drug_code_xref_id": "drug-code-1",
            "drug_product_id": "drug-product-1",
            "code_system": "INSURANCE",
            "code_value": "SYN-001",
            "content_checksum": _digest("drug-code-1"),
        }
    ]
    tables["eligibility_rule_revision"] = [
        {
            "rule_revision_id": "eligibility-revision-1",
            "logical_rule_id": "eligibility-logical-1",
            "drug_concept_id": "drug-concept-1",
            "source_fragment_id": "source-fragment-1",
            "policy_scope": "INSURANCE_PAYMENT",
            "lifecycle": "DRAFT",
            "effective_from": "2026-01-01",
            "effective_to": "2027-12-31",
            "effective_date_basis": "CURRENT_FILE_ASSUMPTION",
            "date_override_reason": None,
            "date_review_comment": None,
            "historical_application_policy": "APPLY_CURRENT_RELEASE_WITH_WARNING",
            "supersedes_revision_id": None,
            "content_checksum": _digest("eligibility-revision-1"),
        }
    ]
    tables["eligibility_branch"] = [
        {
            "branch_id": "eligibility-branch-1",
            "rule_revision_id": "eligibility-revision-1",
            "source_fragment_id": "source-fragment-1",
            "ordinal_no": 1,
            "source_text": "仅限合成癌",
            "source_span_start": 0,
            "source_span_end": 6,
            "disposition": "approved",
            "content_checksum": _digest("eligibility-branch-1"),
        }
    ]
    tables["condition_node"] = [
        {
            "node_id": "condition-node-1",
            "branch_id": "eligibility-branch-1",
            "parent_node_id": None,
            "sibling_order": 1,
            "node_kind": "leaf",
            "criterion_type": "diagnosis",
            "operator": "includes",
            "target_kind": "",
            "target_id": "",
            "expected_value_json": json.dumps(
                {"includes": ["合成癌"]}, ensure_ascii=False
            ),
            "combination_requirement": "",
            "source_fragment_id": "source-fragment-1",
            "source_span_start": 0,
            "source_span_end": 6,
            "disposition": "approved",
            "content_checksum": _digest("condition-node-1"),
        }
    ]
    tables["regimen_revision"] = [
        {
            "regimen_revision_id": "regimen-revision-1",
            "logical_regimen_id": "regimen-logical-1",
            "canonical_name": "SYN",
            "lifecycle": "DRAFT",
            "effective_from": "2026-01-01",
            "effective_to": "2027-12-31",
            "effective_date_basis": "CURRENT_FILE_ASSUMPTION",
            "date_override_reason": None,
            "date_review_comment": None,
            "historical_application_policy": "APPLY_CURRENT_RELEASE_WITH_WARNING",
            "source_refs_json": json.dumps([SOURCE_REF], ensure_ascii=False),
            "supersedes_revision_id": None,
            "content_checksum": _digest("regimen-revision-1"),
        }
    ]
    tables["regimen_alias"] = [
        {
            "alias_id": "regimen-alias-1",
            "regimen_revision_id": "regimen-revision-1",
            "original_alias": "SYN",
            "normalized_alias": "SYN",
            "alias_type": "regimen_token",
            "language_code": "en",
            "aggregate_frequency": 2,
            "source_corpus_checksum": SOURCE_CHECKSUM,
            "review_status": "approved",
            "is_ambiguous": False,
            "content_checksum": _digest("regimen-alias-1"),
        }
    ]
    tables["regimen_context"] = [
        {
            "context_id": "regimen-context-1",
            "regimen_revision_id": "regimen-revision-1",
            "cancer_context": "合成癌",
            "histology": "",
            "clinical_setting": "",
            "content_checksum": _digest("regimen-context-1"),
        }
    ]
    tables["regimen_component"] = [
        {
            "component_id": "regimen-component-1",
            "regimen_revision_id": "regimen-revision-1",
            "target_kind": "CONCEPT",
            "target_id": "drug-concept-1",
            "token": "SYN",
            "component_role": "backbone",
            "requirement": "required",
            "sibling_order": 1,
            "source_fragment_id": "source-fragment-1",
            "content_checksum": _digest("regimen-component-1"),
        }
    ]
    tables["curated_knowledge_atom"] = [
        {
            "atom_id": "curated-atom-1",
            "source_rule_id": "RD04",
            "source_field_or_test": "eligibility",
            "atom_type": "SOURCE_RULE",
            "canonical_payload": "{}",
            "source_checksum": SOURCE_CHECKSUM,
            "content_checksum": _digest("curated-atom-1"),
            "migration_status": "VERIFIED",
            "oncology": True,
            "rule_status": "ready",
        }
    ]
    tables["curated_knowledge_mapping"] = [
        {
            "mapping_id": "curated-mapping-1",
            "atom_id": "curated-atom-1",
            "target_kind": "CONDITION",
            "target_id": "eligibility-branch-1",
            "verification_evidence": "case:curated-verified",
            "content_checksum": _digest("curated-mapping-1"),
        }
    ]
    tables["review_event"] = [
        _review_event(
            "review-eligibility-parent",
            "eligibility_revision",
            "eligibility-revision-1",
        ),
        _review_event("review-branch", "branch", "eligibility-branch-1"),
        _review_event("review-node", "condition_node", "condition-node-1"),
        _review_event("review-regimen", "regimen", "regimen-revision-1"),
        _review_event("review-alias", "alias", "regimen-alias-1"),
        _review_event("review-context", "context", "regimen-context-1"),
        _review_event("review-component", "component", "regimen-component-1"),
    ]
    return tables


def _write_pathology_bootstrap(path: Path) -> Path:
    raw: dict[str, Any] = {
        "asset_type": "pathology",
        "metadata": {
            "schema_version": "2.0.0",
            "content_version": "legacy-approved-bootstrap-1",
            "effective_from": "2026-01-01",
            "effective_to": "2027-12-31",
            "source_refs": [PATHOLOGY_REF],
            "checksum": "sha256:" + "0" * 64,
            "review_status": "approved",
        },
        "entries": [
            {
                "entry_id": "pathology-entry-1",
                "marker_id": "SYN1",
                "aliases": [{"value": "SYN1", "alias_type": "gene"}],
                "observation_type": "sequence_variant",
                "methods": ["MOLECULAR"],
                "cancer_contexts": ["合成癌"],
                "policy_context": "synthetic",
                "specimen_constraints": [],
                "scoring_system": "",
                "accepted_values": ["positive"],
                "threshold": {"operator": "EQUALS", "value": "positive"},
                "raw_condition": "SYN1 阳性",
                "metadata": {
                    "content_version": "legacy-approved-bootstrap-1",
                    "effective_from": "2026-01-01",
                    "effective_to": "2027-12-31",
                    "source_refs": [PATHOLOGY_REF],
                    "review_status": "approved",
                },
            }
        ],
    }
    raw["metadata"]["checksum"] = asset_payload_checksum(raw)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return path


class _FakeReleaseStore:
    """带事务快照的严格 adapter；commit 前的状态可完整回滚。"""

    def __init__(self, tables: Mapping[str, Sequence[Mapping[str, Any]]]):
        self.tables = copy.deepcopy(dict(tables))
        self.releases: dict[str, dict[str, Any]] = {}
        self.items: dict[str, list[dict[str, Any]]] = {}
        self.pointer: dict[str, Any] | None = None
        self.commits = 0
        self.rollbacks = 0
        self.fail_next_commit = False
        self.fail_lifecycle_update = False
        self.load_locks: list[bool] = []
        self._savepoint()

    def _state(self) -> tuple[Any, Any, Any]:
        return copy.deepcopy((self.tables, self.releases, self.items, self.pointer))

    def _savepoint(self) -> None:
        self._committed = self._state()

    def sync_external_change(self) -> None:
        self._savepoint()

    def load_authoring_tables(self, *, lock: bool):
        self.load_locks.append(lock)
        return copy.deepcopy(self.tables)

    def get_release(self, release_id: str, *, lock: bool):
        del lock
        value = self.releases.get(release_id)
        return None if value is None else copy.deepcopy(value)

    def get_release_items(self, release_id: str, *, lock: bool):
        del lock
        return copy.deepcopy(self.items.get(release_id, []))

    def get_pointer(self, *, lock: bool):
        del lock
        return copy.deepcopy(self.pointer)

    def set_revision_lifecycle(
        self,
        entity_type: str,
        revision_id: str,
        *,
        expected: Sequence[str],
        lifecycle: str,
    ) -> bool:
        if self.fail_lifecycle_update:
            return False
        table, key = {
            "eligibility": ("eligibility_rule_revision", "rule_revision_id"),
            "regimen": ("regimen_revision", "regimen_revision_id"),
        }[entity_type]
        for row in self.tables[table]:
            if row[key] == revision_id and row["lifecycle"] in expected:
                row["lifecycle"] = lifecycle
                return True
        return False

    def insert_release(self, row: Mapping[str, Any]) -> None:
        identity = str(row["release_id"])
        if identity in self.releases:
            raise AssertionError("duplicate release")
        self.releases[identity] = copy.deepcopy(dict(row))

    def insert_release_items(self, rows: Sequence[Mapping[str, Any]]) -> None:
        for raw in rows:
            row = copy.deepcopy(dict(raw))
            self.items.setdefault(str(row["release_id"]), []).append(row)

    def update_release(self, release_id: str, values: Mapping[str, Any]) -> None:
        if release_id not in self.releases:
            raise AssertionError("missing release")
        self.releases[release_id].update(copy.deepcopy(dict(values)))

    def set_pointer(
        self, active_release_id: str, previous_release_id: str | None, actor: str
    ) -> None:
        self.pointer = {
            "pointer_name": "oncology",
            "active_release_id": active_release_id,
            "previous_release_id": previous_release_id,
            "changed_by": actor,
            "changed_at": "2026-07-21T00:00:00Z",
        }

    def restore_pointer(self, row: Mapping[str, Any]) -> None:
        self.pointer = copy.deepcopy(dict(row))

    def commit(self) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("PWD=secret; /private/client/path; 原始正文")
        self.commits += 1
        self._savepoint()

    def rollback(self) -> None:
        self.rollbacks += 1
        self.tables, self.releases, self.items, self.pointer = copy.deepcopy(
            self._committed
        )


def _approve_all(adapter: _FakeReleaseStore) -> None:
    eligibility = approve_authoring_revision(
        adapter,
        entity_type="eligibility",
        revision_id="eligibility-revision-1",
        reviewer_id="domain-reviewer",
    )
    regimen = approve_authoring_revision(
        adapter,
        entity_type="regimen",
        revision_id="regimen-revision-1",
        reviewer_id="domain-reviewer",
    )
    assert eligibility.lifecycle == regimen.lifecycle == "APPROVED"


def _authority_pins(
    adapter: _FakeReleaseStore, pathology: Path
) -> tuple[str, str]:
    tables = _canonical_authoring_tables(adapter.tables)
    pathology_checksum = json.loads(pathology.read_text(encoding="utf-8"))[
        "metadata"
    ]["checksum"]
    bootstrap = _load_pathology_bootstrap(
        pathology, expected_checksum=pathology_checksum
    )
    coverage = _initial_coverage(tables, bootstrap)
    source_manifest, curated_manifest = _authority_manifests(
        tables, bootstrap, coverage, _curated_projection(tables)
    )
    return (
        source_manifest["snapshot_checksum"],
        curated_manifest["manifest_checksum"],
    )


def test_authority_inspection_is_read_only_and_returns_only_pins(
    tmp_path: Path,
) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    pathology = _write_pathology_bootstrap(tmp_path / "private-pathology.json")
    pathology_checksum = json.loads(pathology.read_text(encoding="utf-8"))[
        "metadata"
    ]["checksum"]
    before = adapter._state()

    result = inspect_operational_authority(
        adapter,
        pathology_bootstrap=pathology,
        pathology_bootstrap_checksum=pathology_checksum,
    )

    assert result.pathology_bootstrap_checksum == pathology_checksum
    assert result.source_authority_checksum.startswith("sha256:")
    assert result.curated_authority_checksum.startswith("sha256:")
    assert result.source_item_count > 0 and result.curated_atom_count == 1
    assert adapter.load_locks == [True]
    assert adapter.commits == 0 and adapter._state() == before


def _build(
    adapter: _FakeReleaseStore,
    pathology: Path,
    releases_dir: Path,
    *,
    created_at: str = "2026-07-21T11:00:00Z",
    authority_pins: tuple[str, str] | None = None,
):
    source_pin, curated_pin = authority_pins or _authority_pins(adapter, pathology)
    return build_operational_release(
        adapter,
        operator="release-operator",
        created_at=created_at,
        pathology_bootstrap=pathology,
        pathology_bootstrap_checksum=json.loads(
            pathology.read_text(encoding="utf-8")
        )["metadata"]["checksum"],
        expected_source_authority_checksum=source_pin,
        expected_curated_authority_checksum=curated_pin,
        releases_dir=releases_dir,
    )


def _publish(
    adapter: _FakeReleaseStore,
    release_id: str,
    pathology: Path,
    releases_dir: Path,
    *,
    published_at: str = "2026-07-21T12:00:00Z",
    authority_pins: tuple[str, str] | None = None,
):
    source_pin, curated_pin = authority_pins or _authority_pins(adapter, pathology)
    return publish_operational_release(
        adapter,
        release_id=release_id,
        operator="release-operator",
        published_at=published_at,
        pathology_bootstrap=pathology,
        pathology_bootstrap_checksum=json.loads(
            pathology.read_text(encoding="utf-8")
        )["metadata"]["checksum"],
        expected_source_authority_checksum=source_pin,
        expected_curated_authority_checksum=curated_pin,
        releases_dir=releases_dir,
    )


def test_latest_review_and_reviewer_are_required_before_approval() -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    adapter.tables["review_event"].append(
        _review_event(
            "review-branch-rejected",
            "branch",
            "eligibility-branch-1",
            decision="REJECT",
            reviewed_at="2026-07-21T10:01:00Z",
            previous_event_id="review-branch",
        )
    )
    adapter.sync_external_change()

    with pytest.raises(ReleaseStoreError) as rejected:
        approve_authoring_revision(
            adapter,
            entity_type="eligibility",
            revision_id="eligibility-revision-1",
            reviewer_id="domain-reviewer",
        )
    assert rejected.value.code == "LATEST_REVIEW_NOT_APPROVED"
    assert adapter.tables["eligibility_rule_revision"][0]["lifecycle"] == "DRAFT"

    adapter = _FakeReleaseStore(_authoring_tables())
    with pytest.raises(ReleaseStoreError) as mismatch:
        approve_authoring_revision(
            adapter,
            entity_type="regimen",
            revision_id="regimen-revision-1",
            reviewer_id="different-reviewer",
        )
    assert mismatch.value.code == "APPROVAL_REVIEWER_MISMATCH"
    assert adapter.tables["regimen_revision"][0]["lifecycle"] == "DRAFT"


def test_approval_preserves_subsecond_order_and_rejects_unapplied_edits() -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    adapter.tables["review_event"].extend(
        [
            _review_event(
                "zzz-older-approve",
                "branch",
                "eligibility-branch-1",
                reviewed_at="2026-07-21T10:00:00.100Z",
                previous_event_id="review-branch",
            ),
            _review_event(
                "aaa-newer-reject",
                "branch",
                "eligibility-branch-1",
                decision="REJECT",
                reviewed_at="2026-07-21T10:00:00.900Z",
                previous_event_id="zzz-older-approve",
            ),
        ]
    )
    adapter.sync_external_change()
    with pytest.raises(ReleaseStoreError) as ordered:
        approve_authoring_revision(
            adapter,
            entity_type="eligibility",
            revision_id="eligibility-revision-1",
            reviewer_id="domain-reviewer",
        )
    assert ordered.value.code == "LATEST_REVIEW_NOT_APPROVED"

    adapter = _FakeReleaseStore(_authoring_tables())
    component_event = next(
        row
        for row in adapter.tables["review_event"]
        if row["entity_type"] == "component"
    )
    component_event["decision"] = "APPROVE_WITH_EDIT"
    component_event["expert_value_json"] = json.dumps(
        {"target_id": "different-concept"}
    )
    adapter.sync_external_change()
    with pytest.raises(ReleaseStoreError) as edit:
        approve_authoring_revision(
            adapter,
            entity_type="regimen",
            revision_id="regimen-revision-1",
            reviewer_id="domain-reviewer",
        )
    assert edit.value.code == "APPROVAL_EDIT_NOT_MATERIALIZED"
    assert adapter.tables["regimen_revision"][0]["lifecycle"] == "DRAFT"


def test_approval_does_not_freeze_unready_children() -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    adapter.tables["regimen_alias"][0]["is_ambiguous"] = True
    adapter.sync_external_change()
    with pytest.raises(ReleaseStoreError) as blocked:
        approve_authoring_revision(
            adapter,
            entity_type="regimen",
            revision_id="regimen-revision-1",
            reviewer_id="domain-reviewer",
        )
    assert blocked.value.code == "REGIMEN_REVISION_NOT_READY"
    assert adapter.tables["regimen_revision"][0]["lifecycle"] == "DRAFT"

    adapter = _FakeReleaseStore(_authoring_tables())
    adapter.tables["eligibility_branch"][0]["content_checksum"] = _digest(
        "eligibility-branch-edited-after-review"
    )
    adapter.sync_external_change()
    with pytest.raises(ReleaseStoreError) as stale:
        approve_authoring_revision(
            adapter,
            entity_type="eligibility",
            revision_id="eligibility-revision-1",
            reviewer_id="domain-reviewer",
        )
    assert stale.value.code == "REVIEW_CONTENT_CHECKSUM_MISMATCH"
    assert adapter.tables["eligibility_rule_revision"][0]["lifecycle"] == "DRAFT"


def test_regimen_approval_and_release_preserve_class_and_requirement(
    tmp_path: Path,
) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    component = adapter.tables["regimen_component"][0]
    component.update(
        {
            "target_kind": "CLASS",
            "target_id": "platinum-class",
            "token": "含铂",
            "requirement": "WITH_OR_WITHOUT",
        }
    )
    adapter.tables["drug_class"] = [
        {
            "drug_class_id": "platinum-class",
            "canonical_name": "铂类药物",
            "match_terms_json": json.dumps(["含铂", "铂类"], ensure_ascii=False),
            "lifecycle": "APPROVED",
            "content_checksum": _digest("platinum-class"),
        }
    ]
    adapter.tables["review_event"].append(
        _review_event(
            "review-platinum-class",
            "drug_class",
            "platinum-class",
        )
    )
    adapter.sync_external_change()
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    _approve_all(adapter)

    result = _build(adapter, pathology, tmp_path / "releases")
    _publish(adapter, result.release_id, pathology, tmp_path / "releases")
    compiled = load_release_bundle(tmp_path / "releases", result.release_id)
    raw = json.loads(compiled.assets["oncology_regimen_kb.json"])
    released = raw["entries"][0]["components"][0]
    assert released == {
        "target_kind": "CLASS",
        "target_id": "platinum-class",
        "token": "含铂",
        "requirement": "WITH_OR_WITHOUT",
    }
    assert raw["drug_classes"] == [
        {
            "drug_class_id": "platinum-class",
            "canonical_name": "铂类药物",
            "match_terms": ["含铂", "铂类"],
            "lifecycle": "APPROVED",
            "reviewer_id": "domain-reviewer",
        }
    ]


@pytest.mark.parametrize("class_state", [None, "DRAFT"])
def test_regimen_approval_blocks_missing_or_unapproved_class(
    class_state: str | None,
) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    adapter.tables["regimen_component"][0].update(
        {
            "target_kind": "CLASS",
            "target_id": "blocked-class",
            "token": "BC",
            "requirement": "REQUIRED",
        }
    )
    if class_state is not None:
        adapter.tables["drug_class"] = [
            {
                "drug_class_id": "blocked-class",
                "canonical_name": "未批准类别",
                "match_terms_json": '["未批准类别"]',
                "lifecycle": class_state,
                "content_checksum": _digest("blocked-class"),
            }
        ]
    adapter.sync_external_change()

    with pytest.raises(ReleaseStoreError) as exc_info:
        approve_authoring_revision(
            adapter,
            entity_type="regimen",
            revision_id="regimen-revision-1",
            reviewer_id="domain-reviewer",
        )
    assert exc_info.value.code == (
        "DRUG_CLASS_NOT_FOUND" if class_state is None else "DRUG_CLASS_NOT_APPROVED"
    )


def test_typed_authoring_full_release_publish_and_rollback_cycle(tmp_path: Path) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    releases_dir = tmp_path / "releases"
    _approve_all(adapter)

    first = _build(adapter, pathology, releases_dir)
    assert first.status == "CANDIDATE" and first.item_count == 4
    assert _build(
        adapter,
        pathology,
        releases_dir,
        created_at="2026-07-21T11:30:00Z",
    ).reused is True
    assert adapter.tables["drug_concept"][0]["lifecycle"] == "DRAFT"

    published = _publish(adapter, first.release_id, pathology, releases_dir)
    assert published.status == "PUBLISHED"
    assert adapter.pointer["active_release_id"] == first.release_id
    assert {
        adapter.tables["eligibility_rule_revision"][0]["lifecycle"],
        adapter.tables["regimen_revision"][0]["lifecycle"],
    } == {"RELEASED"}
    active = resolve_active_release_assets(releases_dir)
    assert set(active) == set(ASSET_FILENAMES)
    validate_compiled_release(load_release_bundle(releases_dir, first.release_id))

    # APPROVED→RELEASED 不改变 authority/release ID，重复 publish 必须幂等。
    repeated = _publish(
        adapter,
        first.release_id,
        pathology,
        releases_dir,
        published_at="2026-07-22T12:00:00Z",
    )
    assert repeated.reused is True and repeated.release_id == first.release_id

    # 增加一个明确 drafting 的非肿瘤 atom，形成第二个不可缩减 authority。
    adapter.tables["curated_knowledge_atom"].append(
        {
            "atom_id": "curated-atom-backlog",
            "source_rule_id": "R191",
            "source_field_or_test": "drafting",
            "atom_type": "SOURCE_RULE",
            "canonical_payload": "{}",
            "source_checksum": SOURCE_CHECKSUM,
            "content_checksum": _digest("curated-atom-backlog"),
            "migration_status": "DISCOVERED",
            "oncology": False,
            "rule_status": "drafting",
        }
    )
    adapter.sync_external_change()
    second = _build(
        adapter,
        pathology,
        releases_dir,
        created_at="2026-07-21T13:00:00Z",
    )
    assert second.release_id != first.release_id
    _publish(
        adapter,
        second.release_id,
        pathology,
        releases_dir,
        published_at="2026-07-21T14:00:00Z",
    )

    rolled_back = rollback_operational_release(
        adapter,
        target_release_id=first.release_id,
        operator="release-operator",
        reason="synthetic regression",
        occurred_at="2026-07-21T15:00:00Z",
        releases_dir=releases_dir,
    )
    assert rolled_back.status == "PUBLISHED"
    assert adapter.pointer["active_release_id"] == first.release_id
    assert adapter.releases[second.release_id]["status"] == "ROLLED_BACK"
    assert adapter.releases[first.release_id]["status"] == "PUBLISHED"
    assert adapter.tables["eligibility_rule_revision"][0]["lifecycle"] == "RELEASED"
    assert (releases_dir / second.release_id).is_dir()
    assert (releases_dir / "release_events.jsonl").is_file()
    repeated_rollback = rollback_operational_release(
        adapter,
        target_release_id=first.release_id,
        operator="release-operator",
        reason="synthetic regression",
        occurred_at="2026-07-21T15:00:00Z",
        releases_dir=releases_dir,
    )
    assert repeated_rollback.reused is True


def test_publish_commit_failure_restores_db_lifecycle_and_local_pointer(
    tmp_path: Path,
) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    releases_dir = tmp_path / "releases"
    _approve_all(adapter)
    candidate = _build(adapter, pathology, releases_dir)
    candidate_row = copy.deepcopy(adapter.releases[candidate.release_id])
    adapter.fail_next_commit = True

    with pytest.raises(ReleaseStoreError) as failed:
        _publish(adapter, candidate.release_id, pathology, releases_dir)
    assert failed.value.code == "RELEASE_PUBLISH_COMMIT_FAILED"
    assert adapter.releases[candidate.release_id] == candidate_row
    assert adapter.pointer is None
    assert not (releases_dir / "active_release.json").exists()
    assert adapter.tables["eligibility_rule_revision"][0]["lifecycle"] == "APPROVED"
    assert adapter.tables["regimen_revision"][0]["lifecycle"] == "APPROVED"
    # bundle 可安全保留为孤儿；后续相同发布参数可幂等复用。
    assert (releases_dir / candidate.release_id).is_dir()
    assert (
        _publish(
            adapter,
            candidate.release_id,
            pathology,
            releases_dir,
            published_at="2026-07-21T12:30:00Z",
        ).status
        == "PUBLISHED"
    )


def test_release_reconstruction_and_duty_separation_fail_closed(tmp_path: Path) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    _approve_all(adapter)
    source_pin, curated_pin = _authority_pins(adapter, pathology)
    with pytest.raises(ReleaseStoreError) as separated:
        build_operational_release(
            adapter,
            operator="domain-reviewer",
            created_at="2026-07-21T11:00:00Z",
            pathology_bootstrap=pathology,
            pathology_bootstrap_checksum=json.loads(
                pathology.read_text(encoding="utf-8")
            )["metadata"]["checksum"],
            expected_source_authority_checksum=source_pin,
            expected_curated_authority_checksum=curated_pin,
            releases_dir=tmp_path / "releases",
        )
    assert separated.value.code == "RELEASE_DUTY_SEPARATION_FAILED"
    assert adapter.releases == {}

    pins = _authority_pins(adapter, pathology)
    candidate = _build(
        adapter, pathology, tmp_path / "releases", authority_pins=pins
    )
    adapter.tables["source_document"].append(
        {
            "source_document_id": "source-doc-unreported",
            "source_type": "GUIDELINE_INDICATION",
            "title": "新增来源不得由 caller 忽略",
            "document_version": "1.0",
            "document_year": 2026,
            "retrieval_date": "2026-07-21",
            "content_checksum": _digest("source-doc-unreported"),
        }
    )
    adapter.sync_external_change()
    with pytest.raises(ReleaseStoreError) as drifted:
        _publish(
            adapter,
            candidate.release_id,
            pathology,
            tmp_path / "releases",
            authority_pins=pins,
        )
    assert drifted.value.code == "RELEASE_AUTHORITY_PIN_MISMATCH"
    assert adapter.releases[candidate.release_id]["status"] == "CANDIDATE"
    assert adapter.pointer is None


def test_external_authority_pin_detects_deleted_curated_atom(tmp_path: Path) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    _approve_all(adapter)
    adapter.tables["curated_knowledge_atom"].append(
        {
            "atom_id": "curated-atom-rd21",
            "source_rule_id": "RD21",
            "source_field_or_test": "preserved-rule",
            "atom_type": "SOURCE_RULE",
            "canonical_payload": "{}",
            "source_checksum": SOURCE_CHECKSUM,
            "content_checksum": _digest("curated-atom-rd21"),
            "migration_status": "DISCOVERED",
            "oncology": False,
            "rule_status": "drafting",
        }
    )
    pins = _authority_pins(adapter, pathology)
    adapter.tables["curated_knowledge_atom"] = [
        row
        for row in adapter.tables["curated_knowledge_atom"]
        if row["atom_id"] != "curated-atom-rd21"
    ]
    adapter.sync_external_change()

    with pytest.raises(ReleaseStoreError) as shrunk:
        _build(
            adapter,
            pathology,
            tmp_path / "releases",
            authority_pins=pins,
        )
    assert shrunk.value.code == "RELEASE_AUTHORITY_PIN_MISMATCH"
    assert adapter.releases == {}


def test_rollback_local_failure_compensates_database_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    releases_dir = tmp_path / "releases"
    _approve_all(adapter)
    first = _build(adapter, pathology, releases_dir)
    _publish(adapter, first.release_id, pathology, releases_dir)
    adapter.tables["curated_knowledge_atom"].append(
        {
            "atom_id": "curated-atom-backlog",
            "source_rule_id": "R191",
            "source_field_or_test": "drafting",
            "atom_type": "SOURCE_RULE",
            "canonical_payload": "{}",
            "source_checksum": SOURCE_CHECKSUM,
            "content_checksum": _digest("curated-atom-backlog"),
            "migration_status": "DISCOVERED",
            "oncology": False,
            "rule_status": "drafting",
        }
    )
    adapter.sync_external_change()
    second = _build(
        adapter,
        pathology,
        releases_dir,
        created_at="2026-07-21T13:00:00Z",
    )
    _publish(
        adapter,
        second.release_id,
        pathology,
        releases_dir,
        published_at="2026-07-21T14:00:00Z",
    )
    before_releases = copy.deepcopy(adapter.releases)
    before_pointer = copy.deepcopy(adapter.pointer)

    def fail_local_rollback(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("PWD=secret; /private/path; 原始正文")

    monkeypatch.setattr(
        "javert.oncology.authoring.release_store.rollback_release",
        fail_local_rollback,
    )
    with pytest.raises(ReleaseStoreError) as failed:
        rollback_operational_release(
            adapter,
            target_release_id=first.release_id,
            operator="release-operator",
            reason="synthetic regression",
            occurred_at="2026-07-21T15:00:00Z",
            releases_dir=releases_dir,
        )
    assert failed.value.code == "RELEASE_LOCAL_ROLLBACK_FAILED"
    assert adapter.releases == before_releases
    assert adapter.pointer == before_pointer
    assert resolve_active_release_assets(releases_dir)[ASSET_FILENAMES[0]].parent.name == second.release_id


def test_build_fails_closed_for_unapproved_or_missing_bootstrap(tmp_path: Path) -> None:
    adapter = _FakeReleaseStore(_authoring_tables())
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    with pytest.raises(ReleaseStoreError) as unapproved:
        _build(adapter, pathology, tmp_path / "releases")
    assert unapproved.value.code == "APPROVED_DRUG_CONCEPT_EMPTY"
    assert adapter.releases == {}

    _approve_all(adapter)
    raw = json.loads(pathology.read_text(encoding="utf-8"))
    raw["metadata"]["review_status"] = "needs_review"
    raw["metadata"]["checksum"] = asset_payload_checksum(raw)
    pathology.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ReleaseStoreError) as bootstrap:
        _build(adapter, pathology, tmp_path / "releases")
    assert bootstrap.value.code == "PATHOLOGY_BOOTSTRAP_NOT_APPROVED"
    assert adapter.releases == {}


class _CliConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _PermissionCursor:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.sql = ""

    def execute(self, sql: str):
        self.sql = sql
        return self

    def fetchone(self):
        return (1 if self.allowed else 0,)


class _PermissionConnection(_CliConnection):
    def __init__(self, allowed: bool) -> None:
        super().__init__()
        self.cursor_value = _PermissionCursor(allowed)

    def cursor(self) -> _PermissionCursor:
        return self.cursor_value


def test_release_cli_rejects_unknown_database_before_connect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    monkeypatch.setattr(
        "javert.commands.oncology_kb._open_connection",
        lambda database: calls.append(database),
    )
    result = CliRunner().invoke(
        main,
        [
            "oncology-kb",
            "release-build",
            "--database",
            "TP_data_hub",
            "--operator",
            "release-operator",
            "--created-at",
            "2026-07-21T11:00:00Z",
            "--pathology-bootstrap",
            str(pathology),
            "--pathology-bootstrap-checksum",
            json.loads(pathology.read_text(encoding="utf-8"))["metadata"][
                "checksum"
            ],
            "--source-authority-checksum",
            "sha256:" + "3" * 64,
            "--curated-authority-checksum",
            "sha256:" + "4" * 64,
            "--releases-dir",
            str(tmp_path / "releases"),
        ],
        env={"JAVERT_OWNED_DBS": "TP_data_hub,知识库_work"},
    )
    assert result.exit_code != 0
    assert calls == []


def test_release_cli_returns_only_stable_store_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = _CliConnection()
    pathology = _write_pathology_bootstrap(tmp_path / "private-pathology.json")
    monkeypatch.setattr(
        "javert.commands.oncology_kb._open_connection", lambda database: connection
    )
    monkeypatch.setattr(
        "javert.commands.oncology_kb.preflight_connection",
        lambda connection, database: object(),
    )

    def fail_build(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise ReleaseStoreError("RELEASE_BUILD_FAILED")

    monkeypatch.setattr(
        "javert.commands.oncology_kb.build_operational_release", fail_build
    )
    result = CliRunner().invoke(
        main,
        [
            "oncology-kb",
            "release-build",
            "--database",
            "知识库_work",
            "--operator",
            "principal-secret",
            "--created-at",
            "2026-07-21T11:00:00Z",
            "--pathology-bootstrap",
            str(pathology),
            "--pathology-bootstrap-checksum",
            json.loads(pathology.read_text(encoding="utf-8"))["metadata"][
                "checksum"
            ],
            "--source-authority-checksum",
            "sha256:" + "3" * 64,
            "--curated-authority-checksum",
            "sha256:" + "4" * 64,
            "--releases-dir",
            str(tmp_path / "private-releases"),
        ],
        env={"JAVERT_OWNED_DBS": "知识库_work", "PASSWORD": "password-secret"},
    )
    assert result.exit_code != 0
    assert "RELEASE_BUILD_FAILED" in result.output
    assert "principal-secret" not in result.output
    assert "password-secret" not in result.output
    assert str(tmp_path) not in result.output
    assert "合成限定条件" not in result.output
    assert connection.closed is True


def test_publish_cli_requires_pointer_insert_update_before_operation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    connection = _PermissionConnection(False)
    pathology = _write_pathology_bootstrap(tmp_path / "pathology.json")
    calls: list[str] = []
    monkeypatch.setattr(
        "javert.commands.oncology_kb._open_connection", lambda database: connection
    )
    monkeypatch.setattr(
        "javert.commands.oncology_kb.preflight_connection",
        lambda connection, database: object(),
    )
    monkeypatch.setattr(
        "javert.commands.oncology_kb.publish_operational_release",
        lambda *args, **kwargs: calls.append("published"),
    )
    pathology_checksum = json.loads(pathology.read_text(encoding="utf-8"))[
        "metadata"
    ]["checksum"]
    result = CliRunner().invoke(
        main,
        [
            "oncology-kb",
            "release-publish",
            "--database",
            "知识库_work",
            "--release-id",
            "release-synthetic",
            "--operator",
            "release-operator",
            "--published-at",
            "2026-07-21T12:00:00Z",
            "--pathology-bootstrap",
            str(pathology),
            "--pathology-bootstrap-checksum",
            pathology_checksum,
            "--source-authority-checksum",
            "sha256:" + "3" * 64,
            "--curated-authority-checksum",
            "sha256:" + "4" * 64,
            "--releases-dir",
            str(tmp_path / "releases"),
        ],
        env={"JAVERT_OWNED_DBS": "知识库_work"},
    )
    assert result.exit_code != 0 and calls == []
    assert "INSERT" in connection.cursor_value.sql
    assert "UPDATE" in connection.cursor_value.sql
    assert "DELETE" not in connection.cursor_value.sql
    assert connection.closed is True
