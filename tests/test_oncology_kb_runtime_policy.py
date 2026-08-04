from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from javert.oncology.authoring.models import SourceType, TemporalApplicability
from javert.oncology.authoring.runtime_policy import (
    FUTURE_RELEASE_REQUIRED_WARNING,
    HISTORICAL_APPLICABILITY_WARNING,
    PolicyScopeKey,
    RuntimeEligibilityPayload,
    ScopedPolicyEvaluation,
    add_runtime_provenance,
    index_scoped_evaluations,
    policy_scope_display_label,
    select_temporal_policy,
    temporal_legacy_verdict,
)
from javert.oncology.contracts import AuditDisposition


WINDOW_FROM = date(2026, 1, 1)
WINDOW_TO = date(2027, 12, 31)


def _evaluation(
    scope: SourceType,
    *,
    disposition: AuditDisposition = AuditDisposition.NO_VIOLATION_FOUND,
    release_id: str = "release-synthetic",
    revision_id: str = "revision-synthetic",
) -> ScopedPolicyEvaluation:
    return ScopedPolicyEvaluation(
        patient_id="synthetic-patient",
        drug_concept_id="drug-synthetic",
        release_id=release_id,
        rule_revision_id=revision_id,
        policy_scope=scope,
        source_type=scope,
        audit_disposition=disposition,
        source_document_ids=("source-document-synthetic",),
        source_fragment_ids=("source-fragment-synthetic",),
        source_versions=("synthetic-v1",),
    )


def test_2025_uses_active_release_with_warning_and_allows_adjudication() -> None:
    selection = select_temporal_policy(
        service_date=date(2025, 8, 1),
        effective_from=WINDOW_FROM,
        effective_to=WINDOW_TO,
    )

    assert selection.temporal_applicability == TemporalApplicability.BEFORE_EFFECTIVE_WINDOW
    assert selection.effective_date_enforced is False
    assert selection.automatic_adjudication_allowed is True
    assert selection.audit_disposition_override is None
    assert selection.warning == HISTORICAL_APPLICABILITY_WARNING
    assert temporal_legacy_verdict(selection) is None


@pytest.mark.parametrize("service_date", [WINDOW_FROM, date(2026, 8, 1), WINDOW_TO])
def test_2026_to_2027_window_is_normally_adjudicated(service_date: date) -> None:
    selection = select_temporal_policy(
        service_date=service_date,
        effective_from=WINDOW_FROM,
        effective_to=WINDOW_TO,
    )

    assert selection.temporal_applicability == TemporalApplicability.IN_WINDOW
    assert selection.effective_date_enforced is True
    assert selection.automatic_adjudication_allowed is True
    assert selection.audit_disposition_override is None
    assert selection.warning == ""


def test_2028_fails_closed_instead_of_reusing_historical_fallback() -> None:
    selection = select_temporal_policy(
        service_date=date(2028, 1, 1),
        effective_from=WINDOW_FROM,
        effective_to=WINDOW_TO,
    )

    assert selection.temporal_applicability == TemporalApplicability.AFTER_EFFECTIVE_WINDOW
    assert selection.effective_date_enforced is True
    assert selection.automatic_adjudication_allowed is False
    assert selection.audit_disposition_override == AuditDisposition.REVIEW_REQUIRED
    assert selection.warning == FUTURE_RELEASE_REQUIRED_WARNING
    assert temporal_legacy_verdict(selection) == "INCONCLUSIVE"


def test_dual_scopes_remain_independent_but_share_concept_and_release() -> None:
    insurance = _evaluation(
        SourceType.INSURANCE_PAYMENT,
        disposition=AuditDisposition.VIOLATION_FOUND,
        revision_id="revision-insurance",
    )
    guideline = _evaluation(
        SourceType.GUIDELINE_INDICATION,
        disposition=AuditDisposition.NO_VIOLATION_FOUND,
        revision_id="revision-guideline",
    )

    indexed = index_scoped_evaluations([insurance, guideline])

    assert len(indexed) == 2
    assert indexed[
        PolicyScopeKey(
            "synthetic-patient", "drug-synthetic", SourceType.INSURANCE_PAYMENT
        )
    ].audit_disposition == AuditDisposition.VIOLATION_FOUND
    assert indexed[
        PolicyScopeKey(
            "synthetic-patient", "drug-synthetic", SourceType.GUIDELINE_INDICATION
        )
    ].audit_disposition == AuditDisposition.NO_VIOLATION_FOUND
    assert insurance.release_id == guideline.release_id


def test_duplicate_patient_drug_scope_is_rejected() -> None:
    left = _evaluation(SourceType.INSURANCE_PAYMENT, revision_id="revision-a")
    right = _evaluation(
        SourceType.INSURANCE_PAYMENT,
        disposition=AuditDisposition.VIOLATION_FOUND,
        revision_id="revision-b",
    )

    with pytest.raises(ValueError, match="只能有一个资格状态"):
        index_scoped_evaluations([left, right])


def test_dual_scopes_cannot_mix_releases() -> None:
    insurance = _evaluation(SourceType.INSURANCE_PAYMENT, release_id="release-a")
    guideline = _evaluation(SourceType.GUIDELINE_INDICATION, release_id="release-b")

    with pytest.raises(ValueError, match="同一 knowledge release"):
        index_scoped_evaluations([insurance, guideline])


def test_guideline_cannot_impersonate_label_or_other_source() -> None:
    guideline = _evaluation(SourceType.GUIDELINE_INDICATION)
    assert guideline.display_source_label == "指南适应证"
    assert policy_scope_display_label(SourceType.INSURANCE_PAYMENT) == "医保支付限定"

    with pytest.raises(ValueError, match="非运行时肿瘤资格"):
        policy_scope_display_label(SourceType.NMPA_LABEL)
    with pytest.raises(ValidationError, match="来源不得互相冒充"):
        ScopedPolicyEvaluation(
            patient_id="synthetic-patient",
            drug_concept_id="drug-synthetic",
            release_id="release-synthetic",
            rule_revision_id="revision-synthetic",
            policy_scope=SourceType.GUIDELINE_INDICATION,
            source_type=SourceType.INSURANCE_PAYMENT,
            audit_disposition=AuditDisposition.NO_VIOLATION_FOUND,
        )


def test_old_payload_reads_unchanged_and_new_fields_are_additive() -> None:
    old_payload = {
        "audit_disposition": "NO_VIOLATION_FOUND",
        "eligibility_status": "SATISFIED",
        "legacy_verdict": "CLEAN",
        "proof_tree": {"node_id": "synthetic", "operator": "all"},
    }
    parsed_old = RuntimeEligibilityPayload.model_validate(old_payload)
    assert parsed_old.release_id is None
    assert parsed_old.policy_scope is None
    assert parsed_old.model_dump(exclude_none=True, exclude_defaults=True) == old_payload

    temporal = select_temporal_policy(
        service_date=date(2025, 12, 31),
        effective_from=WINDOW_FROM,
        effective_to=WINDOW_TO,
    )
    enriched = add_runtime_provenance(
        old_payload,
        evaluation=_evaluation(SourceType.GUIDELINE_INDICATION),
        temporal_selection=temporal,
    )

    assert old_payload.get("release_id") is None
    assert enriched["audit_disposition"] == old_payload["audit_disposition"]
    assert enriched["release_id"] == "release-synthetic"
    assert enriched["policy_scope"] == "GUIDELINE_INDICATION"
    assert enriched["policy_scope_display_label"] == "指南适应证"
    assert enriched["temporal_applicability"] == "BEFORE_EFFECTIVE_WINDOW"
    assert enriched["effective_date_enforced"] is False
    assert enriched["temporal_warning"] == HISTORICAL_APPLICABILITY_WARNING
    RuntimeEligibilityPayload.model_validate(enriched)


def test_future_payload_forces_review_required_and_legacy_projection() -> None:
    future = select_temporal_policy(
        service_date=date(2028, 6, 1),
        effective_from=WINDOW_FROM,
        effective_to=WINDOW_TO,
    )
    enriched = add_runtime_provenance(
        {"audit_disposition": "VIOLATION_FOUND", "legacy_verdict": "VIOLATION"},
        evaluation=_evaluation(SourceType.INSURANCE_PAYMENT),
        temporal_selection=future,
    )

    assert enriched["audit_disposition"] == "REVIEW_REQUIRED"
    assert enriched["legacy_verdict"] == "INCONCLUSIVE"
    assert enriched["temporal_applicability"] == "AFTER_EFFECTIVE_WINDOW"


def test_invalid_effective_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="effective_from"):
        select_temporal_policy(
            service_date=date(2026, 1, 1),
            effective_from=date(2027, 1, 1),
            effective_to=date(2026, 1, 1),
        )
