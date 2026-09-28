from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import javert.oncology.authoring.release as release_module
from javert.oncology.authoring.ids import checksum
from javert.oncology.authoring.release import (
    ASSET_SCHEMA_VERSION,
    ASSET_FILENAMES,
    AUTHORITY_SCHEMA_VERSION,
    DRUG_ASSET,
    ELIGIBILITY_ASSET,
    PATHOLOGY_ASSET,
    REGIMEN_ASSET,
    CompiledRelease,
    CoverageItem,
    CuratedAtomCoverage,
    ReleaseRevision,
    ReleaseValidationError,
    activate_release,
    build_release_authority_snapshot,
    build_release_candidate,
    compile_release,
    load_release_bundle,
    publish_candidate,
    rollback_release,
    resolve_active_release_assets,
    validate_compiled_release,
    write_release_bundle,
)
from javert.oncology.knowledge import asset_payload_checksum, canonical_json_bytes


SOURCE_CHECKSUM = "sha256:" + "1" * 64
_BASE_SOURCE_ITEM_IDS = (
    "source-rev-drug-1",
    "source-rev-elig-1",
    "source-rev-path-1",
    "source-rev-reg-1",
)
SNAPSHOT_CHECKSUM = checksum(
    {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "source_item_ids": list(_BASE_SOURCE_ITEM_IDS),
        "counts": {"source_items": len(_BASE_SOURCE_ITEM_IDS)},
    }
)
SOURCE_REF = {
    "source_id": "source:synthetic",
    "title": "合成来源",
    "version": "1.0",
    "publication_date": "2026-01-01",
    "effective_date": None,
    "retrieval_date": "2026-07-21",
    "checksum": SOURCE_CHECKSUM,
}


def _revision(
    asset_name: str,
    *,
    revision_id: str,
    logical_id: str,
    entity_id: str,
    payload: dict,
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = date(2027, 12, 31),
    source_checksum: str = SOURCE_CHECKSUM,
) -> ReleaseRevision:
    source_ref = {**SOURCE_REF, "checksum": source_checksum}
    source_item_id = f"source-{revision_id}"
    return ReleaseRevision(
        revision_id=revision_id,
        logical_id=logical_id,
        asset_name=asset_name,
        entity_id=entity_id,
        payload=payload,
        reviewer_id="domain-reviewer",
        effective_from=effective_from,
        effective_to=effective_to,
        source_refs=(source_ref,),
        policy_scope="INSURANCE_PAYMENT" if asset_name == ELIGIBILITY_ASSET else "",
        source_item_ids=(source_item_id,),
    )


def _base_revisions(*, suffix: str = "1") -> list[ReleaseRevision]:
    leaf = {
        "node_id": "node-diagnosis",
        "kind": "leaf",
        "source_text": "合成诊断",
        "children": [],
        "criterion_id": "criterion-diagnosis",
        "criterion_type": "diagnosis",
        "expected": {"includes": ["合成癌"]},
        "evidence_policy": {"anchored": True, "missing_is": "UNKNOWN"},
        "documentation_template": "",
    }
    return [
        _revision(
            DRUG_ASSET,
            revision_id=f"rev-drug-{suffix}",
            logical_id="drug-a",
            entity_id="合成药A",
            payload={
                "canonical_name": "合成药A",
                "canonical_match_name": "合成药A",
                "aliases": ["Drug A"],
                "codes": ["SYN-A"],
                "oncology": {"drug_concept_id": "drug-a"},
                "entity_type": "canonical_only",
                "entries": [],
                "effective": None,
                "insurance_status": "restricted",
                "sources": {},
            },
        ),
        _revision(
            ELIGIBILITY_ASSET,
            revision_id=f"rev-elig-{suffix}",
            logical_id="rule-elig-a",
            entity_id="elig-a",
            payload={
                "rule_id": "elig-a",
                "drug_concept_id": "drug-a",
                "indication_branch_id": "branch-a",
                "version": "1.0.0",
                "raw_restriction": "合成限定",
                "condition_tree": leaf,
            },
        ),
        _revision(
            PATHOLOGY_ASSET,
            revision_id=f"rev-path-{suffix}",
            logical_id="path-a",
            entity_id="path-a",
            payload={
                "entry_id": "path-a",
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
                "raw_condition": "合成标志物阳性",
            },
        ),
        _revision(
            REGIMEN_ASSET,
            revision_id=f"rev-reg-{suffix}",
            logical_id="regimen-a",
            entity_id="regimen-a",
            payload={
                "regimen_id": "regimen-a",
                "canonical_name": "SYN-A",
                "aliases": ["SYN-A"],
                "cancer_contexts": ["合成癌"],
                "components": [{"drug_concept_id": "drug-a", "token": "A"}],
            },
        ),
    ]


def _coverage(revisions: list[ReleaseRevision]) -> list[CoverageItem]:
    return [
        CoverageItem(
            source_item_id=revision.source_item_ids[0],
            source_type="SYNTHETIC",
            disposition="approved",
            revision_id=revision.revision_id,
            source_checksum=(
                revision.source_refs[0]["checksum"] if revision.source_refs else ""
            ),
        )
        for revision in revisions
    ]


def _atoms() -> list[CuratedAtomCoverage]:
    return [
        CuratedAtomCoverage(
            atom_id="atom-oncology",
            source_rule_id="RD04",
            migration_status="VERIFIED",
            oncology=True,
            rule_status="ready",
            target_id="rule-elig-a",
            verification_evidence="case:synthetic-oncology",
        ),
        CuratedAtomCoverage(
            atom_id="atom-backlog",
            source_rule_id="RD20",
            migration_status="MAPPED",
            oncology=False,
            rule_status="drafting",
            target_id="review-guidance:rd20",
        ),
    ]


def _asset_extras() -> dict[str, dict]:
    return {
        REGIMEN_ASSET: {
            "drug_concepts": [
                {
                    "concept_id": "drug-a",
                    "generic_name": "合成药A",
                    "aliases": [{"value": "Drug A", "alias_type": "english_generic"}],
                    "insurance_codes": ["SYN-A"],
                }
            ],
            "drug_classes": [
                {
                    "drug_class_id": "platinum-class",
                    "canonical_name": "铂类药物",
                    "match_terms": ["含铂", "铂类"],
                    "lifecycle": "APPROVED",
                    "reviewer_id": "domain-reviewer",
                }
            ],
        },
        DRUG_ASSET: {
            "policy": {"guideline_authority": "临床应用指导原则，不是法定说明书"},
            "sources": {"synthetic": SOURCE_REF},
        },
    }


def _source_snapshot_manifest(revisions: list[ReleaseRevision]) -> dict:
    source_item_ids = sorted(
        source_item_id
        for revision in revisions
        for source_item_id in revision.source_item_ids
    )
    payload = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "source_item_ids": source_item_ids,
        "counts": {"source_items": len(source_item_ids)},
    }
    return {**payload, "snapshot_checksum": checksum(payload)}


def _curated_manifest(atoms: list[CuratedAtomCoverage]) -> dict:
    rows = [
        {
            "atom_id": atom.atom_id,
            "source_rule_id": atom.source_rule_id,
            "migration_status": atom.migration_status,
            "oncology": atom.oncology,
            "rule_status": atom.rule_status,
            "target_id": atom.target_id,
            "verification_evidence": atom.verification_evidence,
        }
        for atom in atoms
    ]
    payload = {
        "schema_version": AUTHORITY_SCHEMA_VERSION,
        "atoms": rows,
        "counts": {"atoms": len(rows)},
    }
    return {**payload, "manifest_checksum": checksum(payload)}


def _authority(
    revisions: list[ReleaseRevision],
    atoms: list[CuratedAtomCoverage] | None = None,
):
    source_manifest = _source_snapshot_manifest(revisions)
    curated_manifest = _curated_manifest(atoms or _atoms())
    return build_release_authority_snapshot(
        source_snapshot_manifest=source_manifest,
        curated_manifest=curated_manifest,
        expected_source_snapshot_checksum=source_manifest["snapshot_checksum"],
        expected_curated_manifest_checksum=curated_manifest["manifest_checksum"],
    )


def _candidate(
    *,
    revisions: list[ReleaseRevision] | None = None,
    asset_extras: dict[str, dict] | None = None,
):
    revisions = revisions or _base_revisions()
    coverage = _coverage(revisions)
    atoms = _atoms()
    authority = _authority(revisions, atoms)
    return build_release_candidate(
        revisions=revisions,
        coverage=coverage,
        curated_atoms=atoms,
        source_snapshot_checksum=authority.source_snapshot_checksum,
        created_by="release-builder",
        release_operator="release-operator",
        created_at="2026-07-21T12:00:00Z",
        expected_source_item_ids=authority.source_item_ids,
        authority_snapshot=authority,
        asset_extras=_asset_extras() if asset_extras is None else asset_extras,
    )


def _published(
    *,
    revisions: list[ReleaseRevision] | None = None,
    asset_extras: dict[str, dict] | None = None,
):
    return publish_candidate(
        _candidate(revisions=revisions, asset_extras=asset_extras),
        published_by="release-operator",
        published_at="2026-07-21T13:00:00Z",
    )


def _with_document_checksum(document: dict) -> dict:
    value = copy.deepcopy(document)
    value.pop("checksum", None)
    value["checksum"] = checksum(value)
    return value


def _resign_asset(
    compiled: CompiledRelease,
    asset_name: str,
    mutate,
) -> CompiledRelease:
    assets = dict(compiled.assets)
    raw = json.loads(assets[asset_name])
    mutate(raw)
    raw["metadata"]["checksum"] = asset_payload_checksum(raw)
    data = canonical_json_bytes(raw)
    assets[asset_name] = data
    manifest = copy.deepcopy(compiled.manifest)
    manifest["asset_checksums"][asset_name] = {
        "payload_checksum": raw["metadata"]["checksum"],
        "file_checksum": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    manifest = _with_document_checksum(manifest)
    return CompiledRelease(
        release_id=compiled.release_id,
        assets=assets,
        manifest=manifest,
        coverage_manifest=compiled.coverage_manifest,
    )


def test_release_candidate_requires_approved_immutable_revisions_and_duty_separation() -> None:
    revisions = _base_revisions()
    revisions[0] = replace(revisions[0], lifecycle="DRAFT", immutable=False)
    coverage = _coverage(revisions)
    atoms = _atoms()
    authority = _authority(revisions, atoms)
    with pytest.raises(ReleaseValidationError) as exc_info:
        build_release_candidate(
            revisions=revisions,
            coverage=coverage,
            curated_atoms=atoms,
            source_snapshot_checksum=authority.source_snapshot_checksum,
            created_by="builder",
            release_operator="domain-reviewer",
            created_at="2026-07-21T12:00:00Z",
            expected_source_item_ids=authority.source_item_ids,
            authority_snapshot=authority,
        )
    codes = {item.code for item in exc_info.value.blockers}
    assert {"REVISION_NOT_APPROVED", "REVISION_MUTABLE", "DUTY_SEPARATION_FAILED"} <= codes


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda item: replace(item, effective_to=None), "FUTURE_WINDOW_MISSING"),
        (lambda item: replace(item, source_refs=()), "SOURCE_MISSING"),
        (
            lambda item: replace(
                item,
                payload={**item.payload, "review_status": "needs_review"},
            ),
            "UNREVIEWED_OR_UNSUPPORTED_PAYLOAD",
        ),
        (lambda item: replace(item, blocker_codes=("CONCEPT_MISSING",)), "CONCEPT_MISSING"),
    ],
)
def test_release_blocking_checks_fail_closed(mutation, expected_code: str) -> None:
    revisions = _base_revisions()
    revisions[1] = mutation(revisions[1])
    coverage = _coverage(revisions)
    atoms = _atoms()
    authority = _authority(revisions, atoms)
    with pytest.raises(ReleaseValidationError) as exc_info:
        build_release_candidate(
            revisions=revisions,
            coverage=coverage,
            curated_atoms=atoms,
            source_snapshot_checksum=authority.source_snapshot_checksum,
            created_by="builder",
            release_operator="release-operator",
            created_at="2026-07-21T12:00:00Z",
            expected_source_item_ids=authority.source_item_ids,
            authority_snapshot=authority,
        )
    assert expected_code in {item.code for item in exc_info.value.blockers}


def test_release_rejects_unverified_oncology_atom_and_coverage_gap() -> None:
    revisions = _base_revisions()
    coverage = _coverage(revisions)
    atoms = _atoms()
    atoms[0] = replace(atoms[0], migration_status="MAPPED", verification_evidence="")
    authority = _authority(revisions, atoms)
    with pytest.raises(ReleaseValidationError) as exc_info:
        build_release_candidate(
            revisions=revisions,
            coverage=coverage[:-1],
            curated_atoms=atoms,
            source_snapshot_checksum=authority.source_snapshot_checksum,
            created_by="builder",
            release_operator="release-operator",
            created_at="2026-07-21T12:00:00Z",
            expected_source_item_ids=authority.source_item_ids,
            authority_snapshot=authority,
        )
    codes = {item.code for item in exc_info.value.blockers}
    assert "ONCOLOGY_ATOM_NOT_VERIFIED" in codes
    assert "SOURCE_COVERAGE_GAP" in codes


def test_release_rejects_inclusive_revision_overlap() -> None:
    revisions = _base_revisions()
    revisions.append(
        replace(
            revisions[1],
            revision_id="rev-elig-overlap",
            entity_id="elig-a-v2",
            effective_from=date(2027, 12, 31),
            effective_to=date(2028, 12, 31),
            source_item_ids=("source-overlap",),
        )
    )
    coverage = _coverage(revisions)
    atoms = _atoms()
    authority = _authority(revisions, atoms)
    with pytest.raises(ReleaseValidationError) as exc_info:
        build_release_candidate(
            revisions=revisions,
            coverage=coverage,
            curated_atoms=atoms,
            source_snapshot_checksum=authority.source_snapshot_checksum,
            created_by="builder",
            release_operator="release-operator",
            created_at="2026-07-21T12:00:00Z",
            expected_source_item_ids=authority.source_item_ids,
            authority_snapshot=authority,
        )
    assert "EFFECTIVE_WINDOW_OVERLAP" in {item.code for item in exc_info.value.blockers}


def test_legacy_candidate_api_fails_closed_without_authoritative_snapshot() -> None:
    revisions = _base_revisions()
    coverage = _coverage(revisions)
    with pytest.raises(ReleaseValidationError) as exc_info:
        build_release_candidate(
            revisions=revisions,
            coverage=coverage,
            curated_atoms=_atoms(),
            source_snapshot_checksum=SNAPSHOT_CHECKSUM,
            created_by="builder",
            release_operator="release-operator",
            created_at="2026-07-21T12:00:00Z",
            expected_source_item_ids=[item.source_item_id for item in coverage],
            asset_extras=_asset_extras(),
        )
    assert "AUTHORITATIVE_SNAPSHOT_REQUIRED" in {
        item.code for item in exc_info.value.blockers
    }


def test_authority_rejects_rechecksummed_shrunken_source_manifest() -> None:
    revisions = _base_revisions()
    source_manifest = _source_snapshot_manifest(revisions)
    curated_manifest = _curated_manifest(_atoms())
    pinned_source_checksum = source_manifest["snapshot_checksum"]
    source_manifest["source_item_ids"].pop()
    source_manifest["counts"]["source_items"] -= 1
    source_manifest["snapshot_checksum"] = checksum(
        {
            key: value
            for key, value in source_manifest.items()
            if key != "snapshot_checksum"
        }
    )
    with pytest.raises(ValueError, match="固定 checksum"):
        build_release_authority_snapshot(
            source_snapshot_manifest=source_manifest,
            curated_manifest=curated_manifest,
            expected_source_snapshot_checksum=pinned_source_checksum,
            expected_curated_manifest_checksum=curated_manifest["manifest_checksum"],
        )


def test_candidate_cannot_shrink_authoritative_source_or_curated_universe() -> None:
    revisions = _base_revisions()
    coverage = _coverage(revisions)
    atoms = _atoms()
    atoms[0] = replace(atoms[0], migration_status="MAPPED", verification_evidence="")
    authority = _authority(revisions, atoms)
    with pytest.raises(ReleaseValidationError) as exc_info:
        build_release_candidate(
            revisions=revisions,
            coverage=coverage[:-1],
            curated_atoms=atoms[1:],
            source_snapshot_checksum=authority.source_snapshot_checksum,
            created_by="builder",
            release_operator="release-operator",
            created_at="2026-07-21T12:00:00Z",
            expected_source_item_ids=[item.source_item_id for item in coverage[:-1]],
            authority_snapshot=authority,
            asset_extras=_asset_extras(),
        )
    codes = {item.code for item in exc_info.value.blockers}
    assert {
        "SOURCE_UNIVERSE_AUTHORITY_MISMATCH",
        "SOURCE_COVERAGE_GAP",
        "CURATED_AUTHORITY_MISMATCH",
        "ONCOLOGY_ATOM_NOT_VERIFIED",
    } <= codes


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda revisions: revisions.__setitem__(
                0,
                replace(
                    revisions[0],
                    payload={
                        key: value
                        for key, value in revisions[0].payload.items()
                        if key != "oncology"
                    },
                ),
            ),
            "ONCOLOGY_DRUG_CONCEPT_ID_MISSING",
        ),
        (
            lambda revisions: revisions.__setitem__(
                1,
                replace(
                    revisions[1],
                    payload={
                        **revisions[1].payload,
                        "condition_tree": {
                            **revisions[1].payload["condition_tree"],
                            "criterion_type": "future_magic_type",
                        },
                    },
                ),
            ),
            "UNKNOWN_CRITERION_TYPE",
        ),
        (
            lambda revisions: revisions.__setitem__(
                1,
                replace(
                    revisions[1],
                    payload={
                        **revisions[1].payload,
                        "condition_tree": {
                            **revisions[1].payload["condition_tree"],
                            "criterion_type": "unsupported",
                        },
                    },
                ),
            ),
            "UNSUPPORTED_CRITERION_TYPE",
        ),
        (
            lambda revisions: revisions.__setitem__(
                3,
                replace(
                    revisions[3],
                    payload={
                        **revisions[3].payload,
                        "components": [
                            {"drug_concept_id": "drug-unknown", "token": "X"}
                        ],
                    },
                ),
            ),
            "DRUG_CONCEPT_NOT_FOUND",
        ),
        (
            lambda revisions: revisions.__setitem__(
                1,
                replace(
                    revisions[1],
                    payload={
                        **revisions[1].payload,
                        "condition_tree": {
                            **revisions[1].payload["condition_tree"],
                            "criterion_type": "biomarker",
                            "expected": {"marker_id": "UNKNOWN"},
                        },
                    },
                ),
            ),
            "PATHOLOGY_MARKER_NOT_FOUND",
        ),
        (
            lambda revisions: revisions.__setitem__(
                2,
                replace(
                    revisions[2],
                    source_refs=(
                        {**SOURCE_REF, "checksum": "sha256:" + "9" * 64},
                    ),
                ),
            ),
            "SOURCE_REFERENCE_CONFLICT",
        ),
    ],
)
def test_build_gate_blocks_cross_asset_bypass_attacks(mutate, expected_code: str) -> None:
    revisions = _base_revisions()
    mutate(revisions)
    coverage = _coverage(revisions)
    atoms = _atoms()
    authority = _authority(revisions, atoms)
    with pytest.raises(ReleaseValidationError) as exc_info:
        build_release_candidate(
            revisions=revisions,
            coverage=coverage,
            curated_atoms=atoms,
            source_snapshot_checksum=authority.source_snapshot_checksum,
            created_by="builder",
            release_operator="release-operator",
            created_at="2026-07-21T12:00:00Z",
            expected_source_item_ids=authority.source_item_ids,
            authority_snapshot=authority,
            asset_extras=_asset_extras(),
        )
    assert expected_code in {item.code for item in exc_info.value.blockers}


def test_four_assets_compile_byte_identically_with_release_provenance_and_checksums() -> None:
    published = _published()
    first = compile_release(published)
    second = compile_release(published)
    assert first.assets == second.assets
    assert first.manifest_bytes == second.manifest_bytes
    assert first.coverage_bytes == second.coverage_bytes
    assert set(first.assets) == set(ASSET_FILENAMES)

    validate_compiled_release(first)
    for asset_name, data in first.assets.items():
        raw = json.loads(data)
        metadata = raw["metadata"]
        assert metadata["release_id"] == published.release_id
        assert metadata["release_status"] == "published"
        assert metadata["source_snapshot_checksum"] == SNAPSHOT_CHECKSUM
        assert metadata["revision_ids"]
        assert metadata["checksum"] == asset_payload_checksum(raw)
        assert first.manifest["asset_checksums"][asset_name]["file_checksum"] == (
            "sha256:" + hashlib.sha256(data).hexdigest()
        )


def test_compiled_regimen_preserves_class_and_component_requirement() -> None:
    revisions = _base_revisions()
    revisions[3] = replace(
        revisions[3],
        payload={
            **revisions[3].payload,
            "components": [
                {
                    "target_kind": "CONCEPT",
                    "target_id": "drug-a",
                    "drug_concept_id": "drug-a",
                    "token": "A",
                    "requirement": "OPTIONAL",
                },
                {
                    "target_kind": "CLASS",
                    "target_id": "platinum-class",
                    "token": "含铂",
                    "requirement": "WITH_OR_WITHOUT",
                },
            ],
        },
    )

    compiled = compile_release(_published(revisions=revisions))
    validate_compiled_release(compiled)
    regimen = json.loads(compiled.assets[REGIMEN_ASSET])
    components = regimen["entries"][0]["components"]
    assert components[0]["target_kind"] == "CONCEPT"
    assert components[0]["target_id"] == "drug-a"
    assert components[0]["requirement"] == "OPTIONAL"
    assert components[1] == {
        "target_kind": "CLASS",
        "target_id": "platinum-class",
        "token": "含铂",
        "requirement": "WITH_OR_WITHOUT",
    }


@pytest.mark.parametrize("class_state", ["missing", "DRAFT"])
def test_release_blocks_unknown_or_unapproved_regimen_class(class_state: str) -> None:
    revisions = _base_revisions()
    revisions[3] = replace(
        revisions[3],
        payload={
            **revisions[3].payload,
            "components": [
                {
                    "target_kind": "CLASS",
                    "target_id": "unapproved-class",
                    "token": "UC",
                    "requirement": "REQUIRED",
                }
            ],
        },
    )
    extras = _asset_extras()
    extras[REGIMEN_ASSET]["drug_classes"] = (
        []
        if class_state == "missing"
        else [
            {
                "drug_class_id": "unapproved-class",
                "canonical_name": "未批准类别",
                "match_terms": ["未批准类别"],
                "lifecycle": class_state,
                "reviewer_id": "domain-reviewer",
            }
        ]
    )

    with pytest.raises(ReleaseValidationError) as exc_info:
        _candidate(revisions=revisions, asset_extras=extras)
    assert "DRUG_CLASS_NOT_APPROVED" in {
        item.code for item in exc_info.value.blockers
    }


def test_release_blocks_unapproved_class_in_eligibility_combination() -> None:
    revisions = _base_revisions()
    revisions[1] = replace(
        revisions[1],
        payload={
            **revisions[1].payload,
            "condition_tree": {
                **revisions[1].payload["condition_tree"],
                "criterion_type": "combination_requirement",
                "expected": {
                    "target_kind": "CLASS",
                    "target_id": "unknown-class",
                    "requirement": "WITH_OR_WITHOUT",
                },
            },
        },
    )
    with pytest.raises(ReleaseValidationError) as exc_info:
        _candidate(revisions=revisions)
    assert "DRUG_CLASS_NOT_APPROVED" in {
        item.code for item in exc_info.value.blockers
    }


def test_release_id_changes_when_authoritative_curated_manifest_changes() -> None:
    first = _candidate()
    revised_atoms = _atoms()
    revised_atoms[1] = replace(
        revised_atoms[1],
        migration_status="VERIFIED",
        verification_evidence="case:non-oncology-backlog-verified",
    )
    revisions = _base_revisions()
    coverage = _coverage(revisions)
    authority = _authority(revisions, revised_atoms)
    second = build_release_candidate(
        revisions=revisions,
        coverage=coverage,
        curated_atoms=revised_atoms,
        source_snapshot_checksum=authority.source_snapshot_checksum,
        created_by="release-builder",
        release_operator="release-operator",
        created_at="2026-07-21T12:00:00Z",
        expected_source_item_ids=authority.source_item_ids,
        authority_snapshot=authority,
        asset_extras=_asset_extras(),
    )

    assert first.release_id != second.release_id


def test_runtime_validation_rejects_candidate_and_tampered_asset() -> None:
    candidate_compiled = compile_release(_candidate())
    with pytest.raises(ValueError, match="PUBLISHED"):
        validate_compiled_release(candidate_compiled)

    published = compile_release(_published())
    tampered_assets = dict(published.assets)
    raw = json.loads(tampered_assets[ELIGIBILITY_ASSET])
    raw["entries"][0]["raw_restriction"] = "被修改"
    tampered_assets[ELIGIBILITY_ASSET] = canonical_json_bytes(raw)
    tampered = CompiledRelease(
        release_id=published.release_id,
        assets=tampered_assets,
        manifest=published.manifest,
        coverage_manifest=published.coverage_manifest,
    )
    with pytest.raises(ValueError, match="file checksum"):
        validate_compiled_release(tampered)


def test_runtime_validation_requires_exact_supported_schema_versions() -> None:
    published = compile_release(_published())

    manifest = copy.deepcopy(published.manifest)
    manifest["schema_version"] = "9.9.9"
    bad_manifest = CompiledRelease(
        release_id=published.release_id,
        assets=published.assets,
        manifest=_with_document_checksum(manifest),
        coverage_manifest=published.coverage_manifest,
    )
    with pytest.raises(ValueError, match="release manifest schema_version"):
        validate_compiled_release(bad_manifest)

    coverage = copy.deepcopy(published.coverage_manifest)
    coverage["schema_version"] = "9.9.9"
    coverage = _with_document_checksum(coverage)
    manifest = copy.deepcopy(published.manifest)
    manifest["coverage_checksum"] = coverage["checksum"]
    bad_coverage = CompiledRelease(
        release_id=published.release_id,
        assets=published.assets,
        manifest=_with_document_checksum(manifest),
        coverage_manifest=coverage,
    )
    with pytest.raises(ValueError, match="coverage manifest schema_version"):
        validate_compiled_release(bad_coverage)

    bad_asset = _resign_asset(
        published,
        PATHOLOGY_ASSET,
        lambda raw: raw["metadata"].__setitem__("schema_version", "9.9.9"),
    )
    with pytest.raises(ValueError, match="schema_version 不受支持"):
        validate_compiled_release(bad_asset)


@pytest.mark.parametrize(
    ("asset_name", "mutate", "message"),
    [
        (
            DRUG_ASSET,
            lambda raw: next(iter(raw["drugs"].values())).pop("oncology"),
            "oncology.drug_concept_id",
        ),
        (
            ELIGIBILITY_ASSET,
            lambda raw: raw["entries"][0].__setitem__(
                "drug_concept_id", "drug-unknown"
            ),
            "未知 drug_concept_id",
        ),
        (
            ELIGIBILITY_ASSET,
            lambda raw: raw["entries"][0]["condition_tree"].__setitem__(
                "criterion_type", "future_magic_type"
            ),
            "未知 criterion_type",
        ),
        (
            ELIGIBILITY_ASSET,
            lambda raw: raw["entries"][0]["condition_tree"].__setitem__(
                "criterion_type", "unsupported"
            ),
            "unsupported",
        ),
        (
            ELIGIBILITY_ASSET,
            lambda raw: raw["entries"][0].__setitem__(
                "condition_tree",
                {
                    **raw["entries"][0]["condition_tree"],
                    "criterion_type": "biomarker",
                    "expected": {"marker_id": "UNKNOWN"},
                },
            ),
            "未知 pathology marker",
        ),
        (
            REGIMEN_ASSET,
            lambda raw: raw["entries"][0]["components"][0].__setitem__(
                "drug_concept_id", "drug-unknown"
            ),
            "未知 drug_concept_id",
        ),
        (
            ELIGIBILITY_ASSET,
            lambda raw: raw["entries"][0]["metadata"]["source_refs"][0].__setitem__(
                "source_id", "source:undeclared"
            ),
            "source_refs 不一致",
        ),
    ],
)
def test_runtime_semantic_validation_rejects_resigned_bypass_assets(
    asset_name: str,
    mutate,
    message: str,
) -> None:
    tampered = _resign_asset(compile_release(_published()), asset_name, mutate)
    with pytest.raises(ValueError, match=message):
        validate_compiled_release(tampered)


def test_diff_reports_source_date_changes_and_keeps_pending_rule_as_drafting_backlog() -> None:
    first = compile_release(_published())
    revisions = _base_revisions()
    revisions[1] = replace(
        revisions[1],
        revision_id="rev-elig-2",
        payload={**revisions[1].payload, "raw_restriction": "合成限定（修订）"},
        effective_from=date(2026, 2, 1),
        source_refs=(
            {
                **SOURCE_REF,
                "source_id": "source:synthetic-v2",
                "version": "2.0",
                "checksum": "sha256:" + "3" * 64,
            },
        ),
        source_item_ids=("source-rev-elig-2",),
    )
    second = compile_release(_published(revisions=revisions), previous_manifest=first.manifest)
    diff = second.coverage_manifest["diff"]
    assert [item["logical_id"] for item in diff["modified"]] == ["rule-elig-a"]
    assert [item["logical_id"] for item in diff["source_changes"]] == ["rule-elig-a"]
    assert [item["logical_id"] for item in diff["effective_date_changes"]] == ["rule-elig-a"]

    previous = copy.deepcopy(first.manifest)
    previous["release_items"].append(
        {
            "asset_name": DRUG_ASSET,
            "logical_id": "RD20",
            "entity_id": "legacy-rd20",
            "revision_id": "legacy-rd20-v1",
            "payload_checksum": "sha256:" + "4" * 64,
            "source_checksums": ["sha256:" + "5" * 64],
            "effective_from": "2026-01-01",
            "effective_to": "2027-12-31",
        }
    )
    with_backlog = compile_release(_published(), previous_manifest=previous)
    backlog_diff = with_backlog.coverage_manifest["diff"]
    assert [item["logical_id"] for item in backlog_diff["drafting_backlog"]] == ["RD20"]
    assert "RD20" not in {item["logical_id"] for item in backlog_diff["retired"]}


def test_rollback_switches_only_active_pointer_and_preserves_bundles_and_history(
    tmp_path: Path,
) -> None:
    first = compile_release(_published())
    second_revisions = _base_revisions()
    second_revisions[1] = replace(
        second_revisions[1],
        revision_id="rev-elig-2",
        payload={**second_revisions[1].payload, "raw_restriction": "合成限定（修订）"},
        source_item_ids=("source-rev-elig-2",),
    )
    second = compile_release(_published(revisions=second_revisions))
    write_release_bundle(first, tmp_path)
    write_release_bundle(second, tmp_path)
    activate_release(tmp_path, second.release_id)

    historical_audit = tmp_path / "historical_audit.json"
    historical_payload = {"audit_id": "audit-old", "release_id": first.release_id}
    historical_audit.write_text(json.dumps(historical_payload), encoding="utf-8")
    event = rollback_release(
        tmp_path,
        target_release_id=first.release_id,
        actor="operator-rollback",
        reason="合成回归演练",
        occurred_at="2026-07-21T14:00:00Z",
    )

    active = json.loads((tmp_path / "active_release.json").read_text(encoding="utf-8"))
    assert active["release_id"] == first.release_id
    resolved = resolve_active_release_assets(tmp_path)
    assert set(resolved) == set(ASSET_FILENAMES)
    assert all(path.parent.name == first.release_id for path in resolved.values())
    assert event["from_release_id"] == second.release_id
    assert event["to_release_id"] == first.release_id
    assert (tmp_path / first.release_id).is_dir()
    assert (tmp_path / second.release_id).is_dir()
    assert json.loads(historical_audit.read_text(encoding="utf-8")) == historical_payload
    validate_compiled_release(load_release_bundle(tmp_path, first.release_id))
    assert len((tmp_path / "release_events.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_rollback_event_write_failure_leaves_active_pointer_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = compile_release(_published())
    revisions = _base_revisions(suffix="2")
    second = compile_release(_published(revisions=revisions))
    write_release_bundle(first, tmp_path)
    write_release_bundle(second, tmp_path)
    activate_release(tmp_path, second.release_id)
    pointer_path = tmp_path / "active_release.json"
    pointer_before = pointer_path.read_bytes()
    original_atomic_write = release_module._atomic_write

    def fail_event(path: Path, data: bytes) -> None:
        if path.name == "release_events.jsonl":
            raise OSError("synthetic event failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(release_module, "_atomic_write", fail_event)
    with pytest.raises(OSError, match="event failure"):
        rollback_release(
            tmp_path,
            target_release_id=first.release_id,
            actor="operator-rollback",
            reason="合成事件写失败",
            occurred_at="2026-07-21T14:00:00Z",
        )
    assert pointer_path.read_bytes() == pointer_before
    assert not (tmp_path / "release_events.jsonl").exists()


def test_rollback_pointer_failure_restores_event_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = compile_release(_published())
    revisions = _base_revisions(suffix="2")
    second = compile_release(_published(revisions=revisions))
    write_release_bundle(first, tmp_path)
    write_release_bundle(second, tmp_path)
    activate_release(tmp_path, second.release_id)
    pointer_path = tmp_path / "active_release.json"
    pointer_before = pointer_path.read_bytes()
    event_path = tmp_path / "release_events.jsonl"
    event_before = b'{"event_id":"existing"}\n'
    event_path.write_bytes(event_before)
    original_atomic_write = release_module._atomic_write

    def fail_pointer(path: Path, data: bytes) -> None:
        if path.name == "active_release.json":
            raise OSError("synthetic pointer failure")
        original_atomic_write(path, data)

    monkeypatch.setattr(release_module, "_atomic_write", fail_pointer)
    with pytest.raises(OSError, match="pointer failure"):
        rollback_release(
            tmp_path,
            target_release_id=first.release_id,
            actor="operator-rollback",
            reason="合成指针写失败",
            occurred_at="2026-07-21T14:00:00Z",
        )
    assert pointer_path.read_bytes() == pointer_before
    assert event_path.read_bytes() == event_before


def test_existing_bundle_is_immutable(tmp_path: Path) -> None:
    compiled = compile_release(_published())
    bundle = write_release_bundle(compiled, tmp_path)
    (bundle / DRUG_ASSET).write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="内容漂移"):
        write_release_bundle(compiled, tmp_path)


def test_active_pointer_checksum_mismatch_fails_closed(tmp_path: Path) -> None:
    compiled = compile_release(_published())
    write_release_bundle(compiled, tmp_path)
    activate_release(tmp_path, compiled.release_id)
    pointer = json.loads((tmp_path / "active_release.json").read_text(encoding="utf-8"))
    pointer["manifest_checksum"] = "sha256:" + "0" * 64
    (tmp_path / "active_release.json").write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(ValueError, match="pointer checksum"):
        resolve_active_release_assets(tmp_path)
