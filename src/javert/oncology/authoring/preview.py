"""全量 authoring 候选的本地、不可裁决 preview 编译与求值。"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from javert.oncology.contracts import AuditDisposition, EligibilityEvaluation
from javert.oncology.eligibility import ConditionNode, EligibilityRule
from javert.oncology.knowledge import KnowledgeEntryMetadata, ReviewStatus, SourceReference
from javert.oncology.regimen import RegimenResolution
from javert.oncology.runtime import _evaluate_runtime_rule

from .sources import normalize_name


@dataclass(frozen=True)
class AuthoringPreviewPack:
    snapshot_checksum: str
    rules: tuple[EligibilityRule, ...]
    concept_names: dict[str, str]
    concept_ids_by_name: dict[str, tuple[str, ...]]
    concept_ids_by_code: dict[str, tuple[str, ...]]

    def concept_ids_for_match(
        self,
        *,
        generic_name: str,
        codes: Iterable[str] = (),
    ) -> tuple[str, ...]:
        by_code = {
            concept_id
            for code in codes
            for concept_id in self.concept_ids_by_code.get(str(code).strip(), ())
            if str(code).strip()
        }
        if by_code:
            return tuple(sorted(by_code))
        normalized = normalize_name(generic_name)
        exact = self.concept_ids_by_name.get(normalized, ())
        if exact:
            return exact
        fuzzy = {
            concept_id
            for name, concept_ids in self.concept_ids_by_name.items()
            if len(name) >= 2 and (name in normalized or normalized in name)
            for concept_id in concept_ids
        }
        return tuple(sorted(fuzzy))

    def rules_for_concept(
        self,
        concept_id: str,
        *,
        policy_scope: str | None = None,
    ) -> tuple[EligibilityRule, ...]:
        return tuple(
            rule
            for rule in self.rules
            if rule.drug_concept_id == concept_id
            and (policy_scope is None or rule.metadata.policy_scope == policy_scope)
        )


def _condition_tree(rows: list[dict[str, Any]], source_text: str) -> ConditionNode:
    by_id = {str(row["node_id"]): row for row in rows}
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    roots: list[dict[str, Any]] = []
    for row in rows:
        parent = str(row.get("parent_node_id") or "")
        if parent:
            children[parent].append(row)
        else:
            roots.append(row)
    if len(roots) != 1:
        raise ValueError("preview condition tree 必须恰有一个根")

    visited: set[str] = set()

    def build(row: dict[str, Any]) -> ConditionNode:
        node_id = str(row["node_id"])
        if node_id in visited:
            raise ValueError("preview condition tree 出现环或重复节点")
        visited.add(node_id)
        kind = str(row["node_kind"]).lower()
        ordered = sorted(
            children[node_id],
            key=lambda item: (int(item.get("sibling_order") or 0), str(item["node_id"])),
        )
        if kind == "leaf":
            expected = row.get("expected_value")
            if not isinstance(expected, dict) or not expected:
                raise ValueError("preview leaf expected_value 必须是非空 JSON object")
            return ConditionNode(
                node_id=node_id,
                kind="leaf",
                source_text=source_text,
                criterion_id=node_id,
                criterion_type=str(row.get("criterion_type") or ""),
                expected=expected,
                evidence_policy={"anchored": True, "missing_is": "UNKNOWN"},
            )
        if kind not in {"all", "any"} or not ordered:
            raise ValueError("preview aggregate node 非法")
        return ConditionNode(
            node_id=node_id,
            kind=kind,
            source_text=source_text,
            children=[build(child) for child in ordered],
        )

    tree = build(roots[0])
    if visited != set(by_id):
        raise ValueError("preview condition tree 存在断开的节点")
    return tree


def load_authoring_preview(path: Path) -> AuthoringPreviewPack:
    raw = json.loads(path.read_text(encoding="utf-8"))
    documents = {
        item["source_document_id"]: item for item in raw["source_documents"]
    }
    fragments = {
        item["source_fragment_id"]: item for item in raw["source_fragments"]
    }
    revisions = {item["revision_id"]: item for item in raw["source_rules"]}
    nodes_by_branch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in raw["condition_nodes"]:
        nodes_by_branch[node["branch_id"]].append(node)

    rules: list[EligibilityRule] = []
    for branch in raw["branches"]:
        revision = revisions[branch["rule_revision_id"]]
        fragment = fragments[branch["source_fragment_id"]]
        document = documents[fragment["source_document_id"]]
        window = revision["effective_window"]
        source = SourceReference(
            source_id=document["source_document_id"],
            source_fragment_id=fragment["source_fragment_id"],
            title=document["title"],
            version=document["document_version"],
            retrieval_date=document["retrieval_date"],
            checksum=fragment["content_checksum"],
        )
        rules.append(
            EligibilityRule(
                rule_id=f"authoring-preview:{branch['branch_id']}",
                drug_concept_id=revision["drug_concept_id"],
                indication_branch_id=branch["branch_id"],
                version=revision["revision_id"],
                raw_restriction=branch["source_text"],
                metadata=KnowledgeEntryMetadata(
                    content_version=revision["revision_id"],
                    effective_from=window["effective_from"],
                    effective_to=window["effective_to"],
                    source_refs=[source],
                    review_status=ReviewStatus.NEEDS_REVIEW,
                    rule_revision_id=revision["revision_id"],
                    policy_scope=revision["policy_scope"],
                ),
                condition_tree=_condition_tree(
                    nodes_by_branch[branch["branch_id"]],
                    branch["source_text"],
                ),
            )
        )

    concept_names = {
        item["drug_concept_id"]: item["canonical_name"]
        for item in raw["drug_concepts"]
    }
    names: dict[str, set[str]] = defaultdict(set)
    codes: dict[str, set[str]] = defaultdict(set)
    for concept_id, canonical_name in concept_names.items():
        names[normalize_name(canonical_name)].add(concept_id)
    for product in raw["drug_products"]:
        concept_id = product["drug_concept_id"]
        for value in (
            product.get("product_name"),
            concept_names.get(concept_id),
        ):
            if value:
                names[normalize_name(str(value))].add(concept_id)
        for value in (product.get("insurance_code"), product.get("hospital_code")):
            if value:
                codes[str(value).strip()].add(concept_id)

    return AuthoringPreviewPack(
        snapshot_checksum=str(raw["snapshot_checksum"]),
        rules=tuple(sorted(rules, key=lambda item: item.rule_id)),
        concept_names=concept_names,
        concept_ids_by_name={key: tuple(sorted(value)) for key, value in names.items()},
        concept_ids_by_code={key: tuple(sorted(value)) for key, value in codes.items()},
    )


def evaluate_authoring_preview_rule(
    rule: EligibilityRule,
    *,
    diagnoses: list[dict[str, Any]],
    records: list[dict[str, Any]],
    regimens: list[RegimenResolution],
    service_date: date,
    pathology_path: Path,
    cancer_context: str,
) -> EligibilityEvaluation:
    """执行真实叶子求值，但强制保持人工复核，不产生自动 CLEAN/VIOLATION。"""
    evaluation_rule = rule.model_copy(
        update={
            "metadata": rule.metadata.model_copy(
                update={"review_status": ReviewStatus.APPROVED}
            )
        }
    )
    evaluated = _evaluate_runtime_rule(
        evaluation_rule,
        diagnoses=diagnoses,
        records=records,
        regimens=regimens,
        service_date=service_date,
        pathology_path=pathology_path,
        cancer_context=cancer_context,
        enforce_effective_date=False,
        published_release=False,
    )
    return EligibilityEvaluation.model_validate(
        {
            **evaluated.model_dump(mode="json"),
            "audit_disposition": AuditDisposition.REVIEW_REQUIRED.value,
            "legacy_verdict": "INCONCLUSIVE",
            "data_quality_flags": sorted(
                set(
                    evaluated.data_quality_flags
                    + ["DRAFT_RULE_PREVIEW_ONLY", "AUTHORING_REVIEW_REQUIRED"]
                )
            ),
        }
    )
