#!/usr/bin/env python3
"""生成统一来源、条件候选、覆盖矩阵和精选知识迁移工件。"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from javert.oncology.authoring.conditions import (  # noqa: E402
    build_condition_candidates,
    build_shared_qualifier_candidates,
    coverage_partition,
    source_rule_coverage,
    split_source_rule,
    validate_condition_tree,
)
from javert.oncology.authoring.curated import write_manifest  # noqa: E402
from javert.oncology.authoring.ids import canonical_json_bytes, checksum  # noqa: E402
from javert.oncology.authoring.models import (  # noqa: E402
    CandidateDisposition,
    CriterionOperator,
    CriterionType,
    TargetKind,
)
from javert.oncology.authoring.reports import build_representative_condition_snapshot  # noqa: E402
from javert.oncology.authoring.sources import (  # noqa: E402
    build_authoring_snapshot,
    build_candidate_universe,
    build_drug_crosswalk,
    build_pathology_links,
    normalize_drug_concept_name,
)


def _resolve_combination_targets(nodes, concepts, products) -> None:
    """仅绑定唯一精确 concept；无 authority 的自由组合显式阻断发布。"""
    aliases: dict[str, set[str]] = {}
    for concept in concepts:
        aliases.setdefault(
            normalize_drug_concept_name(concept.canonical_name), set()
        ).add(
            concept.drug_concept_id
        )
        for alias in concept.aliases:
            aliases.setdefault(normalize_drug_concept_name(alias), set()).add(
                concept.drug_concept_id
            )
    for product in products:
        aliases.setdefault(
            normalize_drug_concept_name(product.product_name), set()
        ).add(
            product.drug_concept_id
        )
    for node in nodes:
        if (
            node.criterion_type != CriterionType.COMBINATION_REQUIREMENT
            or not isinstance(node.expected_value, dict)
        ):
            continue
        display_name = str(node.expected_value.get("display_name") or "")
        display_key = normalize_drug_concept_name(display_name)
        matches = (
            set(aliases.get(display_key, set()))
            if node.target_kind == TargetKind.CONCEPT
            else set()
        )
        if node.target_kind == TargetKind.CONCEPT and len(matches) == 1:
            node.target_id = next(iter(matches))
            node.expected_value["target_kind"] = TargetKind.CONCEPT.value
            node.expected_value["target_id"] = node.target_id
            continue
        if node.target_kind == TargetKind.CLASS:
            continue

        proposed_kind = str(getattr(node.target_kind, "value", node.target_kind))
        requirement = str(
            getattr(node.combination_requirement, "value", node.combination_requirement)
            or ""
        )
        node.criterion_type = CriterionType.UNSUPPORTED
        node.operator = CriterionOperator.EXISTS
        node.target_kind = TargetKind.VALUE
        node.target_id = ""
        node.expected_value = {
            "display_name": display_name,
            "proposed_criterion_type": CriterionType.COMBINATION_REQUIREMENT.value,
            "proposed_target_kind": proposed_kind,
            "proposed_requirement": requirement,
            "resolution_status": "AUTHORITY_REQUIRED",
        }
        node.combination_requirement = None
        node.disposition = CandidateDisposition.UNSUPPORTED


def main() -> int:
    out = ROOT / "docs/oncology/authoring"
    out.mkdir(parents=True, exist_ok=True)
    snapshot = build_authoring_snapshot(ROOT)
    fragments = {item.source_fragment_id: item for item in snapshot.source_fragments}
    branches = []
    nodes = []
    tree_errors = []
    for rule in snapshot.source_rules:
        for branch in split_source_rule(rule, fragments[rule.source_fragment_id]):
            branch_nodes = build_condition_candidates(branch)
            branch_nodes.extend(
                build_shared_qualifier_candidates(
                    branch,
                    fragments[rule.source_fragment_id],
                    branch_nodes,
                )
            )
            errors = validate_condition_tree(branch_nodes, set(fragments))
            if errors:
                tree_errors.append({"branch_id": branch.branch_id, "errors": errors})
            branches.append(branch)
            nodes.extend(branch_nodes)
    supplemental_names = {
        str(node.expected_value.get("display_name") or "")
        for node in nodes
        if node.criterion_type == CriterionType.COMBINATION_REQUIREMENT
        and node.target_kind == TargetKind.CONCEPT
        and isinstance(node.expected_value, dict)
    }
    concepts, products = build_drug_crosswalk(
        ROOT, supplemental_generic_names=supplemental_names
    )
    _resolve_combination_targets(nodes, concepts, products)
    universe = build_candidate_universe(ROOT)
    pathology_links = build_pathology_links(ROOT, nodes)
    rule_coverage = source_rule_coverage(branches)
    leaf_nodes = [item for item in nodes if item.node_kind.value == "LEAF"]
    concept_ids = {item.drug_concept_id for item in concepts}
    unresolved_combination_concepts = [
        item
        for item in leaf_nodes
        if item.criterion_type.value == "combination_requirement"
        and item.target_kind.value == "CONCEPT"
        and item.target_id not in concept_ids
    ]
    combination_classes = [
        item
        for item in leaf_nodes
        if item.criterion_type.value == "combination_requirement"
        and item.target_kind.value == "CLASS"
    ]
    biomarker_nodes = [
        item for item in leaf_nodes if item.criterion_type.value == "biomarker"
    ]
    payload = {
        "schema_version": "1.0.0",
        "source_documents": [item.model_dump(mode="json") for item in snapshot.source_documents],
        "source_fragments": [item.model_dump(mode="json") for item in snapshot.source_fragments],
        "source_rules": [item.model_dump(mode="json") for item in snapshot.source_rules],
        "drug_concepts": [item.model_dump(mode="json") for item in concepts],
        "drug_products": [item.model_dump(mode="json") for item in products],
        "branches": [item.model_dump(mode="json") for item in branches],
        "condition_nodes": [item.model_dump(mode="json") for item in nodes],
        "candidate_universe": [item.model_dump(mode="json") for item in universe],
        "pathology_links": pathology_links,
        "qa": {
            "coverage_partition": coverage_partition(branches),
            "source_rule_coverage": rule_coverage,
            "tree_errors": tree_errors,
            "source_rule_count": len(snapshot.source_rules),
            "partition_total": sum(coverage_partition(branches).values()),
            "condition_leaf_count": len(leaf_nodes),
            "condition_aggregate_count": len(nodes) - len(leaf_nodes),
            "criterion_type_counts": dict(
                sorted(Counter(item.criterion_type.value for item in leaf_nodes).items())
            ),
            "release_readiness": {
                "status": "REVIEW_REQUIRED",
                "draft_rule_revisions": sum(
                    item.lifecycle.value == "DRAFT" for item in snapshot.source_rules
                ),
                "in_review_branches": sum(
                    item.disposition.value == "in_review" for item in branches
                ),
                "unsupported_leaves": sum(
                    item.criterion_type.value == "unsupported" for item in leaf_nodes
                ),
                "unresolved_combination_concept_nodes": len(
                    unresolved_combination_concepts
                ),
                "draft_combination_class_nodes": len(combination_classes),
                "draft_combination_class_targets": len(
                    {item.target_id for item in combination_classes}
                ),
                "biomarker_nodes_requiring_expert_context_review": len(
                    biomarker_nodes
                ),
            },
        },
    }
    payload["snapshot_checksum"] = checksum(payload)
    (out / "oncology_authoring_candidates.json").write_bytes(canonical_json_bytes(payload) + b"\n")
    representative = build_representative_condition_snapshot(ROOT)
    (out / "representative_condition_snapshot.json").write_bytes(
        canonical_json_bytes(representative) + b"\n"
    )
    manifest = write_manifest(
        ROOT,
        out / "curated_knowledge_manifest.json",
        out / "curated_knowledge_report.md",
    )
    print(
        f"rules={len(snapshot.source_rules)} branches={len(branches)} nodes={len(nodes)} "
        f"concepts={len(concepts)} products={len(products)} universe={len(universe)} "
        f"pathology_links={len(pathology_links)} atoms={manifest['counts']['atoms']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
