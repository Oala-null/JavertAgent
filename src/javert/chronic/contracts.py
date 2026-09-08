# -*- coding: utf-8 -*-
"""门诊慢性病认定知识与结构化结果合同。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from javert.clinical_criteria.contracts import CriterionState, ProofNode


Checksum = str
LegacyVerdict = Literal["CLEAN", "INCONCLUSIVE"]


class ReviewStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class RevisionLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class PolicyRole(StrEnum):
    CURRENT_RECOGNITION = "current_recognition"
    HISTORICAL_REFERENCE = "historical_reference"


class CoverageClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class RevisionExecutionStatus(StrEnum):
    EVALUATABLE = "EVALUATABLE"
    BLOCKED = "BLOCKED"


class CriterionNodeType(StrEnum):
    LOGIC = "LOGIC"
    LEAF = "LEAF"
    BLOCKED_ROOT = "BLOCKED_ROOT"


class NodeCompilationStatus(StrEnum):
    COMPILED = "compiled"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class ExecutionStatus(StrEnum):
    EVALUATED = "EVALUATED"
    BLOCKED = "BLOCKED"


class QualificationDisposition(StrEnum):
    QUALIFIED = "QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceDocument(StrictModel):
    source_document_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_role: PolicyRole
    title: str = Field(min_length=1)
    document_number: str = Field(min_length=1)
    publication_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    validity_text: str = ""
    file_path: str = Field(min_length=1)
    physical_page_count: int = Field(gt=0)
    pdf_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_dates(self) -> "SourceDocument":
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("source effective_to 不能早于 effective_from")
        return self


class SourceFragment(StrictModel):
    source_fragment_id: str = Field(min_length=1)
    source_document_id: str = Field(min_length=1)
    document_title: str = Field(min_length=1)
    document_number: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    physical_page: int = Field(gt=0)
    printed_page_label: str | None = None
    reviewed_excerpt: str = Field(min_length=1)
    excerpt_kind: Literal["verbatim", "authoring_summary"] = "authoring_summary"
    extraction_method: Literal[
        "manual_visual_review",
        "feedback_workbook_transcription",
        "ocr_candidate",
    ]
    review_status: ReviewStatus
    fragment_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ExpertInterpretation(StrictModel):
    """用户确认的书面解释，只消歧所列节点，不代表原文或 revision 审批。"""

    interpretation_id: str = Field(min_length=1)
    rule_id: str = Field(pattern=r"^CD\d{2}$")
    policy_version: Literal["2025"]
    workbook_name: str = Field(min_length=1)
    workbook_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    sheet: str = Field(min_length=1)
    cell: str = Field(pattern=r"^H[1-9]\d*$")
    statement: str = Field(min_length=1)
    source_kind: Literal["user_confirmed_written_interpretation"]
    scope: Literal["combination_clarification_only"]
    original_confirmation_status: Literal["pending"]
    review_status: Literal["needs_review"]
    affected_node_ids: list[str] = Field(min_length=1)
    source_fragment_ids: list[str] = Field(min_length=1)
    interpretation_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SourceManifest(StrictModel):
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    previous_manifest_checksum: Checksum | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    documents: list[SourceDocument] = Field(min_length=2)
    fragments: list[SourceFragment] = Field(min_length=1)
    expert_interpretations: list[ExpertInterpretation] = Field(default_factory=list)
    manifest_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_references(self) -> "SourceManifest":
        documents = {item.source_document_id: item for item in self.documents}
        if len(documents) != len(self.documents):
            raise ValueError("source_document_id 不能重复")
        fragment_ids = [item.source_fragment_id for item in self.fragments]
        if len(set(fragment_ids)) != len(fragment_ids):
            raise ValueError("source_fragment_id 不能重复")
        for fragment in self.fragments:
            document = documents.get(fragment.source_document_id)
            if document is None:
                raise ValueError(
                    f"fragment {fragment.source_fragment_id} 引用未知 document"
                )
            if fragment.policy_version != document.policy_version:
                raise ValueError("fragment 与 document policy_version 不一致")
            if fragment.document_title != document.title:
                raise ValueError("fragment 与 document title 不一致")
            if fragment.document_number != document.document_number:
                raise ValueError("fragment 与 document number 不一致")
            if fragment.physical_page > document.physical_page_count:
                raise ValueError("fragment physical_page 超出 PDF 页数")
        interpretations = [item.interpretation_id for item in self.expert_interpretations]
        if len(set(interpretations)) != len(interpretations):
            raise ValueError("interpretation_id 不能重复")
        fragments = {item.source_fragment_id: item for item in self.fragments}
        for item in self.expert_interpretations:
            for ref in item.source_fragment_ids:
                if ref not in fragments or fragments[ref].policy_version != item.policy_version:
                    raise ValueError("书面解释引用未知或跨版本 fragment")
            if any(not node.startswith(item.rule_id + ".") for node in item.affected_node_ids):
                raise ValueError("书面解释不得跨病种节点")
        return self


class CriterionNode(StrictModel):
    """扁平树节点；draft 可显式标 partial，但不得伪装为可执行。"""

    node_id: str = Field(min_length=1)
    parent_node_id: str | None = None
    node_type: CriterionNodeType
    operator: Literal["AND", "OR", "AT_LEAST_N"] | None = None
    threshold: int | None = None
    criterion_id: str = ""
    fact_type: str = ""
    summary: str = Field(min_length=1)
    expected_condition: dict[str, Any] = Field(default_factory=dict, json_schema_extra={
        "if": {"properties": {"type": {"const": "numeric"}}, "required": ["type"]},
        "then": {
            "required": ["terms", "operator", "value", "unit"],
            "properties": {
                "concept_id": {"type": "string", "minLength": 1},
                "operator": {"enum": ["lt", "lte", "gt", "gte", "eq"]},
                "value": {"anyOf": [{"type": "number"}, {"type": "string", "pattern": r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$"}]},
                "terms": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "unit": {"type": "string", "minLength": 1},
            },
        },
    })
    evidence_domains: list[str] = Field(min_length=1)
    unit_policy: dict[str, Any] = Field(default_factory=dict)
    repetition_policy: dict[str, Any] = Field(default_factory=dict)
    temporal_policy: dict[str, Any] = Field(default_factory=dict)
    source_fragment_ids: list[str] = Field(min_length=1)
    expert_interpretation_ids: list[str] = Field(default_factory=list)
    compilation_status: NodeCompilationStatus
    authoring_status: str = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def _validate_shape(self) -> "CriterionNode":
        if self.node_type == CriterionNodeType.BLOCKED_ROOT:
            if self.parent_node_id is not None or self.operator is not None:
                raise ValueError("BLOCKED_ROOT 不能有 parent 或臆造 operator")
            if self.compilation_status != NodeCompilationStatus.BLOCKED:
                raise ValueError("BLOCKED_ROOT compilation_status 必须为 blocked")
            return self
        if self.node_type == CriterionNodeType.LOGIC:
            if self.operator is None:
                raise ValueError("LOGIC 节点必须声明 operator")
            if self.operator == "AT_LEAST_N":
                if isinstance(self.threshold, bool) or not self.threshold or self.threshold < 1:
                    raise ValueError("AT_LEAST_N threshold 必须为正整数")
            elif self.threshold is not None:
                raise ValueError("只有 AT_LEAST_N 可声明 threshold")
            return self
        if self.operator is not None or self.threshold is not None:
            raise ValueError("LEAF 不能声明 operator/threshold")
        if not self.criterion_id or not self.fact_type or not self.expected_condition:
            raise ValueError("LEAF 必须包含 criterion_id/fact_type/expected_condition")
        if self.expected_condition.get("type") == "numeric":
            condition = self.expected_condition
            value = condition.get("value")
            try:
                valid_number = not isinstance(value, bool) and Decimal(str(value)).is_finite()
            except InvalidOperation:
                valid_number = False
            terms = condition.get("terms")
            if (
                condition.get("operator") not in {"lt", "lte", "gt", "gte", "eq"}
                or not valid_number
                or not isinstance(condition.get("unit"), str) or not condition["unit"].strip()
                or not isinstance(terms, list) or not terms
                or any(not isinstance(term, str) or not term.strip() for term in terms)
            ):
                raise ValueError("numeric 条件必须声明合法比较符、有限数值、单位和 terms")
        return self


class CriteriaBlocker(StrictModel):
    block_reason_code: str = Field(min_length=1)
    unresolved_question: str = Field(min_length=1)
    affected_node_ids: list[str] = Field(min_length=1)
    required_approver_roles: list[str] = Field(min_length=1)
    prohibited_fallbacks: list[Literal["2020_POLICY", "LLM", "LOCAL_DEFAULT"]] = (
        Field(min_length=3)
    )
    resolution_reference: str | None = None


class DiseaseRevision(StrictModel):
    rule_id: str = Field(pattern=r"^CD\d{2}$")
    canonical_disease_id: str = Field(min_length=1)
    disease_name: str = Field(min_length=1)
    coverage_class: CoverageClass
    revision_id: str = Field(min_length=1)
    previous_revision_id: str | None = None
    expert_interpretation_ids: list[str] = Field(default_factory=list)
    policy_version: str = Field(min_length=1)
    lifecycle: RevisionLifecycle
    review_status: ReviewStatus
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_evidence_ref: str | None = None
    execution_status: RevisionExecutionStatus
    effective_from: date | None = None
    effective_to: date | None = None
    root_node_id: str = Field(min_length=1)
    source_fragment_ids: list[str] = Field(min_length=1)
    nodes: list[CriterionNode] = Field(min_length=1)
    blocker: CriteriaBlocker | None = None
    coverage_notes: str = ""

    @model_validator(mode="after")
    def _validate_tree(self) -> "DiseaseRevision":
        if self.review_status == ReviewStatus.APPROVED and not all(
            (self.reviewed_by, self.reviewed_at, self.review_evidence_ref)
        ):
            raise ValueError("approved revision 必须记录 reviewer、时间和审核证据")
        if not self.revision_id.startswith(f"{self.rule_id}-"):
            raise ValueError("revision_id 必须以稳定 rule_id 开头")
        if self.previous_revision_id is not None and (
            not self.previous_revision_id.startswith(f"{self.rule_id}-")
            or self.previous_revision_id == self.revision_id
        ):
            raise ValueError("previous_revision_id 必须是同病种的旧 revision")
        if self.expert_interpretation_ids and self.previous_revision_id is None:
            raise ValueError("书面解释必须创建新 revision 并保留 previous_revision_id")
        nodes = {item.node_id: item for item in self.nodes}
        if len(nodes) != len(self.nodes):
            raise ValueError(f"{self.rule_id} node_id 不能重复")
        root = nodes.get(self.root_node_id)
        if root is None or root.parent_node_id is not None:
            raise ValueError(f"{self.rule_id} 必须有且仅有一个无 parent 的 root")
        if sum(item.parent_node_id is None for item in self.nodes) != 1:
            raise ValueError(f"{self.rule_id} 只能有一个 root")
        for node in self.nodes:
            if node.parent_node_id is not None and node.parent_node_id not in nodes:
                raise ValueError(f"{node.node_id} 引用未知 parent")
            if not node.node_id.startswith(self.rule_id + "."):
                raise ValueError("node_id 不得跨病种")
            if set(node.expert_interpretation_ids) - set(self.expert_interpretation_ids):
                raise ValueError("节点书面解释未登记到 revision")
            children = [item for item in self.nodes if item.parent_node_id == node.node_id]
            if node.node_type == CriterionNodeType.LEAF and children:
                raise ValueError("LEAF 不得携带子节点")
            if node.node_type == CriterionNodeType.LOGIC:
                if node.compilation_status == NodeCompilationStatus.COMPILED and not children:
                    raise ValueError("compiled LOGIC 必须包含显式子节点")
                if children and node.operator == "AT_LEAST_N" and node.threshold > len(children):
                    raise ValueError("AT_LEAST_N threshold 超过子节点数")
        seen: set[str] = set()
        pending = [self.root_node_id]
        while pending:
            current = pending.pop()
            if current in seen:
                raise ValueError(f"{self.rule_id} 条件树存在环")
            seen.add(current)
            pending.extend(
                item.node_id for item in self.nodes if item.parent_node_id == current
            )
        if seen != set(nodes):
            raise ValueError(f"{self.rule_id} 条件树存在孤立节点")
        if self.execution_status == RevisionExecutionStatus.BLOCKED:
            if self.blocker is None or root.node_type != CriterionNodeType.BLOCKED_ROOT:
                raise ValueError("BLOCKED revision 必须有 blocker 与 BLOCKED_ROOT")
            missing = set(self.blocker.affected_node_ids) - set(nodes)
            if missing:
                raise ValueError(f"blocker 引用未知节点: {sorted(missing)}")
        elif self.blocker is not None or root.node_type == CriterionNodeType.BLOCKED_ROOT:
            raise ValueError("非 BLOCKED revision 不得携带 blocker/BLOCKED_ROOT")
        if (
            self.coverage_class == CoverageClass.C
            and self.execution_status != RevisionExecutionStatus.BLOCKED
            and not self.expert_interpretation_ids
        ):
            raise ValueError("C 类解除组合 blocker 必须有书面解释")
        return self


class PolicySet(StrictModel):
    policy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    policy_role: PolicyRole
    release_id: str = Field(min_length=1)
    lifecycle: RevisionLifecycle
    review_status: ReviewStatus
    execution_enabled: bool = False
    source_document_ids: list[str] = Field(min_length=1)
    effective_from: date | None = None
    effective_to: date | None = None
    disease_revisions: list[DiseaseRevision] = Field(default_factory=list)
    notes: str = ""


class ChronicDiseaseCriteriaAsset(StrictModel):
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    asset_type: Literal["chronic_disease_criteria"] = "chronic_disease_criteria"
    release_id: str = Field(min_length=1)
    previous_asset_checksum: Checksum | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    source_manifest_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ordered_disease_revision_ids: list[str] = Field(min_length=20, max_length=20)
    policy_sets: list[PolicySet] = Field(min_length=2)
    asset_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _validate_policy_versions(self) -> "ChronicDiseaseCriteriaAsset":
        keys = [(item.policy_id, item.policy_version) for item in self.policy_sets]
        if len(set(keys)) != len(keys):
            raise ValueError("policy_id/version 不能重复")
        current = [
            item
            for item in self.policy_sets
            if item.policy_role == PolicyRole.CURRENT_RECOGNITION
        ]
        if len(current) != 1 or current[0].policy_version != "2025":
            raise ValueError("新认定唯一主口径必须是独立 2025 policy set")
        historical = [
            item
            for item in self.policy_sets
            if item.policy_role == PolicyRole.HISTORICAL_REFERENCE
        ]
        if not historical or any(item.policy_version == "2025" for item in historical):
            raise ValueError("2020 历史口径必须与 2025 物理隔离")
        revisions = current[0].disease_revisions
        expected_ids = [f"CD{index:02d}" for index in range(1, 21)]
        if [item.rule_id for item in revisions] != expected_ids:
            raise ValueError("2025 policy 必须按序恰好包含 CD01-CD20")
        revision_ids = [item.revision_id for item in revisions]
        if revision_ids != self.ordered_disease_revision_ids:
            raise ValueError("ordered_disease_revision_ids 与 2025 revisions 不一致")
        if len({item.canonical_disease_id for item in revisions}) != len(revisions):
            raise ValueError("canonical_disease_id 不能重复")
        for revision in revisions:
            if revision.policy_version != current[0].policy_version:
                raise ValueError("disease revision 不得跨 policy version")
        return self


class ClinicalCriteriaEvaluation(StrictModel):
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    domain: Literal["chronic_disease"] = "chronic_disease"
    rule_id: str = Field(pattern=r"^CD\d{2,3}$")
    disease_id: str = Field(min_length=1)
    disease_name: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    disease_revision_id: str = Field(min_length=1)
    asset_checksum: Checksum = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_status: ExecutionStatus
    evaluation_mode: Literal["shadow", "on"]
    root_state: CriterionState | None = None
    qualified: bool | None = None
    qualification_disposition: QualificationDisposition
    legacy_verdict: LegacyVerdict
    proof_tree: ProofNode | None = None
    shadow_proof_tree: ProofNode | None = None
    missing_items: list[str] = Field(default_factory=list)
    conflict_items: list[str] = Field(default_factory=list)
    blocking_reasons: list[CriteriaBlocker] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)
    normalizer_version: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    evaluated_at: datetime

    @model_validator(mode="after")
    def _validate_projection(self) -> "ClinicalCriteriaEvaluation":
        if self.execution_status == ExecutionStatus.BLOCKED:
            if any(
                value is not None
                for value in (self.root_state, self.qualified, self.proof_tree)
            ):
                raise ValueError("BLOCKED 不得生成权威 root/proof/qualified")
            if not self.blocking_reasons:
                raise ValueError("BLOCKED 必须保留结构化阻断原因")
            expected = (QualificationDisposition.REVIEW_REQUIRED, "INCONCLUSIVE")
        elif self.root_state == CriterionState.SATISFIED:
            expected = (QualificationDisposition.QUALIFIED, "CLEAN")
            if self.qualified is not True or self.proof_tree is None:
                raise ValueError("SATISFIED 必须有 qualified=true 与 proof_tree")
        elif self.root_state == CriterionState.NOT_SATISFIED:
            expected = (QualificationDisposition.NOT_QUALIFIED, "CLEAN")
            if self.qualified is not False or self.proof_tree is None:
                raise ValueError("NOT_SATISFIED 必须有 qualified=false 与 proof_tree")
        elif self.root_state in {CriterionState.UNKNOWN, CriterionState.CONFLICT}:
            expected = (QualificationDisposition.REVIEW_REQUIRED, "INCONCLUSIVE")
            if self.qualified is not None or self.proof_tree is None:
                raise ValueError("UNKNOWN/CONFLICT 必须 qualified=null 且保留 proof")
        else:
            raise ValueError("EVALUATED 必须有四态 root_state")
        if (self.qualification_disposition, self.legacy_verdict) != expected:
            raise ValueError("资格 disposition 与旧 verdict 投影不一致")
        return self
