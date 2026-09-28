"""可复现的候选快照与覆盖报告。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .conditions import build_condition_candidates, split_source_rule
from .ids import checksum
from .sources import build_authoring_snapshot, build_drug_crosswalk, normalize_name


REPRESENTATIVE_QUERIES = {
    "pembrolizumab": "帕博利珠",
    "tislelizumab": "替雷利珠",
    "disitamab_vedotin": "维迪西妥",
    "polatuzumab_vedotin": "维泊妥珠",
    "parp_inhibitor": "奥拉帕利",
}


def build_representative_condition_snapshot(root: Path) -> dict[str, Any]:
    snapshot = build_authoring_snapshot(root)
    concepts, _ = build_drug_crosswalk(root)
    names = {item.drug_concept_id: item.canonical_name for item in concepts}
    fragments = {item.source_fragment_id: item for item in snapshot.source_fragments}
    selected: dict[str, Any] = {}
    for label, query in REPRESENTATIVE_QUERIES.items():
        concept_ids = [
            concept_id
            for concept_id, name in names.items()
            if normalize_name(query) in normalize_name(name)
        ]
        entries = []
        for rule in snapshot.source_rules:
            if rule.drug_concept_id not in concept_ids:
                continue
            fragment = fragments[rule.source_fragment_id]
            branches = split_source_rule(rule, fragment)
            entries.append(
                {
                    "canonical_name": names[rule.drug_concept_id],
                    "policy_scope": rule.policy_scope.value,
                    "source_fragment_id": fragment.source_fragment_id,
                    "branches": [
                        {
                            **branch.model_dump(mode="json"),
                            "condition_nodes": [
                                node.model_dump(mode="json")
                                for node in build_condition_candidates(branch)
                            ],
                        }
                        for branch in branches
                    ],
                }
            )
        selected[label] = sorted(entries, key=lambda item: (item["canonical_name"], item["policy_scope"]))
    result: dict[str, Any] = {
        "schema_version": "1.0.0",
        "representatives": selected,
    }
    result["snapshot_checksum"] = checksum(result)
    return result
