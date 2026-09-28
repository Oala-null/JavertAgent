from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from javert.oncology.authoring.curated import build_manifest
from javert.oncology.authoring.ids import checksum
from javert.oncology.authoring.release import CuratedAtomCoverage, _curated_blockers


ROOT = Path(__file__).resolve().parents[1]


def test_curated_rule_yaml_router_and_manifest_are_consistent() -> None:
    manifest = build_manifest(ROOT)
    router = json.loads((ROOT / "data/router/javert_rules_index.json").read_text(encoding="utf-8"))
    router_entries = router.get("rules", router)
    if isinstance(router_entries, list):
        router_by_id = {str(item["rule_id"]): item for item in router_entries}
    else:
        router_by_id = router_entries
    for number in range(10, 38):
        rule_id = f"RD{number:02d}"
        raw = yaml.safe_load((ROOT / f"configs/rules/{rule_id}.yaml").read_text(encoding="utf-8"))
        assert raw["status"] == manifest["allowed_rule_status"][rule_id]
        assert "knowledge_migration_pending=" in raw["notes"]
        entry = router_by_id.get(rule_id)
        if entry is not None:
            assert entry.get("status") == "drafting"


def test_curated_rules_are_outside_default_ready_set() -> None:
    for number in range(10, 38):
        raw = yaml.safe_load(
            (ROOT / f"configs/rules/RD{number:02d}.yaml").read_text(encoding="utf-8")
        )
        assert raw["status"] != "ready"


def test_curated_authority_is_derived_from_drug_sources_not_rd_number(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    rules = configs / "rules"
    rules.mkdir(parents=True)
    (configs / "oncology_drug_kb.json").write_text(
        json.dumps({"drugs": {"SyntheticOncologyDrug": {}}}),
        encoding="utf-8",
    )
    for name in ("oncology_eligibility_rules.json", "pathology_biomarker_kb.json"):
        (configs / name).write_text(json.dumps({"entries": []}), encoding="utf-8")
    for number in range(10, 38):
        drug_name = "SyntheticOncologyDrug" if number == 10 else f"NonOncologyDrug{number}"
        (rules / f"RD{number:02d}.yaml").write_text(
            yaml.safe_dump(
                {
                    "rule_id": f"RD{number:02d}",
                    "status": "drafting",
                    "trigger_keywords": [drug_name],
                    "question": f"synthetic {drug_name}",
                    "drug_rule_type": "indication",
                    "notes": "synthetic fixture",
                    "expected_signal": "synthetic signal",
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    manifest = build_manifest(tmp_path)
    oncology_rules = {
        atom["source_rule_id"] for atom in manifest["atoms"] if atom["oncology"]
    }
    assert oncology_rules == {"RD10"}
    assert all(
        atom["rule_status"] == manifest["rule_status"][atom["source_rule_id"]]
        for atom in manifest["atoms"]
    )


def test_real_curated_authority_drives_release_gate_and_manifest_checksum() -> None:
    manifest = build_manifest(ROOT)
    oncology_atoms = [atom for atom in manifest["atoms"] if atom["oncology"]]
    non_oncology_atoms = [atom for atom in manifest["atoms"] if not atom["oncology"]]

    assert {atom["source_rule_id"] for atom in oncology_atoms} == {"RD21"}
    assert non_oncology_atoms
    assert {atom["rule_status"] for atom in non_oncology_atoms} == {"drafting"}

    coverage = [
        CuratedAtomCoverage(
            atom_id=atom["atom_id"],
            source_rule_id=atom["source_rule_id"],
            migration_status=atom["migration_status"],
            oncology=atom["oncology"],
            rule_status=atom["rule_status"],
            target_id=atom["target_id"],
            verification_evidence=atom["verification_evidence"],
        )
        for atom in manifest["atoms"]
    ]
    blockers = _curated_blockers(coverage, ())
    assert {
        blocker.entity_id
        for blocker in blockers
        if blocker.code == "ONCOLOGY_ATOM_NOT_VERIFIED"
    } == {atom["atom_id"] for atom in oncology_atoms}
    assert not any(
        blocker.code == "PENDING_RULE_NOT_DRAFTING" for blocker in blockers
    )

    unsigned = {key: value for key, value in manifest.items() if key != "manifest_checksum"}
    assert manifest["manifest_checksum"] == checksum(unsigned)
    mutated = copy.deepcopy(unsigned)
    mutated["atoms"][0]["oncology"] = not mutated["atoms"][0]["oncology"]
    assert checksum(mutated) != manifest["manifest_checksum"]
