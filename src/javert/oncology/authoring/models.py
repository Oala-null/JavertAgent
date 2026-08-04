"""肿瘤知识维护的一期类型化合同。"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DEFAULT_EFFECTIVE_FROM = date(2026, 1, 1)
DEFAULT_EFFECTIVE_TO = date(2027, 12, 31)


class SourceType(StrEnum):
    INSURANCE_PAYMENT = "INSURANCE_PAYMENT"
    GUIDELINE_INDICATION = "GUIDELINE_INDICATION"
    NMPA_LABEL = "NMPA_LABEL"


class CriterionType(StrEnum):
    DIAGNOSIS = "diagnosis"
    HISTOLOGY = "histology"
    STAGE = "stage"
    DISEASE_STATUS = "disease_status"
    RESECTABILITY = "resectability"
    BIOMARKER = "biomarker"
    AGE = "age"
    SEX = "sex"
    MENOPAUSAL_STATUS = "menopausal_status"
    PRIOR_THERAPY = "prior_therapy"
    THERAPY_COUNT = "therapy_count"
    LINE_OF_THERAPY = "line_of_therapy"
    TREATMENT_STATUS = "treatment_status"
    COMBINATION_REQUIREMENT = "combination_requirement"
    SURGERY_STATUS = "surgery_status"
    RADIOTHERAPY_STATUS = "radiotherapy_status"
    TRANSPLANT_ELIGIBILITY = "transplant_eligibility"
    TIME_WINDOW = "time_window"
    CLINICIAN_ASSESSMENT = "clinician_assessment"
    UNSUPPORTED = "unsupported"


class CriterionOperator(StrEnum):
    EQUALS = "EQUALS"
    NOT_EQUALS = "NOT_EQUALS"
    IN = "IN"
    NOT_IN = "NOT_IN"
    CONTAINS = "CONTAINS"
    NOT_CONTAINS = "NOT_CONTAINS"
    EXISTS = "EXISTS"
    NOT_EXISTS = "NOT_EXISTS"
    GTE = "GTE"
    LTE = "LTE"
    WITHIN_DAYS = "WITHIN_DAYS"


class TargetKind(StrEnum):
    CONCEPT = "CONCEPT"
    CLASS = "CLASS"
    REGIMEN = "REGIMEN"
    VALUE = "VALUE"


class CombinationRequirement(StrEnum):
    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"
    WITH_OR_WITHOUT = "WITH_OR_WITHOUT"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    APPROVE_WITH_EDIT = "APPROVE_WITH_EDIT"
    REJECT = "REJECT"
    UNABLE_TO_DETERMINE = "UNABLE_TO_DETERMINE"


class RevisionLifecycle(StrEnum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class EffectiveDateBasis(StrEnum):
    SOURCE_EXPLICIT = "SOURCE_EXPLICIT"
    CURRENT_FILE_ASSUMPTION = "CURRENT_FILE_ASSUMPTION"
    EXPERT_OVERRIDE = "EXPERT_OVERRIDE"


class HistoricalApplicationPolicy(StrEnum):
    APPLY_CURRENT_RELEASE_WITH_WARNING = "APPLY_CURRENT_RELEASE_WITH_WARNING"


class TemporalApplicability(StrEnum):
    BEFORE_EFFECTIVE_WINDOW = "BEFORE_EFFECTIVE_WINDOW"
    IN_WINDOW = "IN_WINDOW"
    AFTER_EFFECTIVE_WINDOW = "AFTER_EFFECTIVE_WINDOW"


class NodeKind(StrEnum):
    ALL = "ALL"
    ANY = "ANY"
    LEAF = "LEAF"


class CandidateDisposition(StrEnum):
    APPROVED = "approved"
    IN_REVIEW = "in_review"
    REJECTED = "rejected"
    UNSUPPORTED = "unsupported"
    INCLUDED = "included"
    DUPLICATE = "duplicate"
    EXCLUDED = "excluded"
    NEEDS_REVIEW = "needs_review"


class AtomType(StrEnum):
    SOURCE_RULE = "SOURCE_RULE"
    CLINICAL_EXTENSION = "CLINICAL_EXTENSION"
    EVIDENCE_POLICY = "EVIDENCE_POLICY"
    NORMALIZATION = "NORMALIZATION"
    DOCUMENTATION_GUIDANCE = "DOCUMENTATION_GUIDANCE"
    REGRESSION_GOLD = "REGRESSION_GOLD"


class MigrationStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    MAPPED = "MAPPED"
    VERIFIED = "VERIFIED"


class PreservationTarget(StrEnum):
    CONDITION = "CONDITION"
    DICTIONARY = "DICTIONARY"
    EVALUATOR_POLICY = "EVALUATOR_POLICY"
    REVIEW_GUIDANCE = "REVIEW_GUIDANCE"
    REGRESSION_CASE = "REGRESSION_CASE"


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document_id: str
    source_type: SourceType
    title: str = Field(min_length=1)
    document_version: str = Field(min_length=1)
    document_year: int | None = None
    retrieval_date: date
    content_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SourceFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_fragment_id: str
    source_document_id: str
    anchor: str = Field(min_length=1)
    original_text: str = Field(min_length=1)
    page_numbers: list[int] = Field(default_factory=list)
    content_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class EffectiveWindow(BaseModel):
    effective_from: date = DEFAULT_EFFECTIVE_FROM
    effective_to: date = DEFAULT_EFFECTIVE_TO
    effective_date_basis: EffectiveDateBasis = EffectiveDateBasis.CURRENT_FILE_ASSUMPTION
    date_override_reason: str = ""
    date_review_comment: str = ""
    historical_application_policy: HistoricalApplicationPolicy = (
        HistoricalApplicationPolicy.APPLY_CURRENT_RELEASE_WITH_WARNING
    )

    @model_validator(mode="after")
    def _validate_window(self) -> "EffectiveWindow":
        if self.effective_from > self.effective_to:
            raise ValueError("effective_from 不能晚于 effective_to")
        if self.effective_date_basis == EffectiveDateBasis.EXPERT_OVERRIDE:
            if not self.date_override_reason.strip() or not self.date_review_comment.strip():
                raise ValueError("EXPERT_OVERRIDE 必须填写覆盖原因和日期审核意见")
        return self


class SourceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical_rule_id: str
    revision_id: str
    source_fragment_id: str
    drug_concept_id: str
    policy_scope: SourceType
    lifecycle: RevisionLifecycle = RevisionLifecycle.DRAFT
    effective_window: EffectiveWindow = Field(default_factory=EffectiveWindow)
    label_source_missing: bool = False

    @model_validator(mode="after")
    def _prevent_reserved_label(self) -> "SourceRule":
        if self.policy_scope == SourceType.NMPA_LABEL and self.label_source_missing:
            raise ValueError("缺失法定说明书时不得生成 NMPA_LABEL")
        return self


class DrugConceptRecord(BaseModel):
    drug_concept_id: str
    canonical_name: str = Field(min_length=1)
    normalized_name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)


class DrugClassRecord(BaseModel):
    """方案与联合条件共享的受控药物类别 authority。"""

    drug_class_id: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    match_terms: list[str] = Field(min_length=1)
    lifecycle: RevisionLifecycle = RevisionLifecycle.DRAFT

    @model_validator(mode="after")
    def _normalize_terms(self) -> "DrugClassRecord":
        terms = sorted({item.strip() for item in self.match_terms if item.strip()})
        if not terms:
            raise ValueError("drug class 至少需要一个匹配词")
        self.match_terms = terms
        return self


class DrugProductRecord(BaseModel):
    drug_product_id: str
    drug_concept_id: str
    product_name: str = Field(min_length=1)
    dosage_form: str = ""
    manufacturer: str = ""
    insurance_code: str = ""
    hospital_code: str = ""
    source_kind: str


class IndicationBranchCandidate(BaseModel):
    branch_id: str
    rule_revision_id: str
    source_fragment_id: str
    ordinal: int = Field(ge=1)
    source_text: str = Field(min_length=1)
    source_span_start: int = Field(ge=0)
    source_span_end: int = Field(ge=0)
    disposition: CandidateDisposition = CandidateDisposition.IN_REVIEW

    @model_validator(mode="after")
    def _validate_span(self) -> "IndicationBranchCandidate":
        if self.source_span_end < self.source_span_start:
            raise ValueError("branch source span 非法")
        return self


class CandidateUniverseRecord(BaseModel):
    candidate_id: str
    canonical_name: str
    source_membership: dict[str, bool]
    normalization_status: str
    disposition: CandidateDisposition
    disposition_reason: str


class ConditionNodeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    branch_id: str
    parent_node_id: str | None = None
    sibling_order: int = Field(ge=0)
    node_kind: NodeKind
    criterion_type: CriterionType | None = None
    operator: CriterionOperator | None = None
    target_kind: TargetKind | None = None
    target_id: str = ""
    expected_value: Any = None
    combination_requirement: CombinationRequirement | None = None
    source_fragment_id: str
    source_span_start: int = Field(ge=0)
    source_span_end: int = Field(ge=0)
    disposition: CandidateDisposition = CandidateDisposition.IN_REVIEW

    @model_validator(mode="after")
    def _validate_node_shape(self) -> "ConditionNodeCandidate":
        if self.source_span_end < self.source_span_start:
            raise ValueError("source span 结束位置不能早于开始位置")
        leaf_fields = (self.criterion_type, self.operator, self.target_kind)
        if self.node_kind == NodeKind.LEAF and any(value is None for value in leaf_fields):
            raise ValueError("LEAF 必须声明 criterion/operator/target_kind")
        if self.node_kind != NodeKind.LEAF and any(value is not None for value in leaf_fields):
            raise ValueError("ALL/ANY 不得声明叶子字段")
        return self


class ReviewEvent(BaseModel):
    event_id: str
    entity_type: str
    entity_id: str
    field_name: str
    decision: ReviewDecision
    reviewer_id: str = Field(min_length=1)
    reviewed_at: datetime
    comment: str = Field(min_length=1)
    evidence_reference: str = ""
    expert_value: Any = None
    previous_event_id: str | None = None


class CuratedKnowledgeAtom(BaseModel):
    atom_id: str
    source_rule_id: str
    oncology: bool
    rule_status: Literal["ready", "drafting", "abandoned"]
    source_field_or_test: str
    atom_type: AtomType
    canonical_payload: dict[str, Any]
    source_checksum: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    migration_status: MigrationStatus = MigrationStatus.DISCOVERED
    target_kind: PreservationTarget | None = None
    target_id: str = ""
    verification_evidence: str = ""

    @model_validator(mode="after")
    def _validate_mapping(self) -> "CuratedKnowledgeAtom":
        if self.migration_status != MigrationStatus.DISCOVERED:
            if self.target_kind is None or not self.target_id:
                raise ValueError("MAPPED/VERIFIED 原子必须有类型化迁移目标")
        if self.migration_status == MigrationStatus.VERIFIED and not self.verification_evidence:
            raise ValueError("VERIFIED 原子必须有验证证据")
        return self


class AuthoringSnapshot(BaseModel):
    source_documents: list[SourceDocument]
    source_fragments: list[SourceFragment]
    source_rules: list[SourceRule]

    @model_validator(mode="after")
    def _current_sources_do_not_invent_labels(self) -> "AuthoringSnapshot":
        if any(item.source_type == SourceType.NMPA_LABEL for item in self.source_documents):
            raise ValueError("当前来源快照不得生成预留 NMPA_LABEL")
        return self
