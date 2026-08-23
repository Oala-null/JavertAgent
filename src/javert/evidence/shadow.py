# -*- coding: utf-8 -*-
"""同步 validation/ledger coordinator 与安全 shadow job。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Literal

from pydantic import Field

from .diagnosis import DiagnosisExtraction
from .ledger import ShadowLedger
from .models import CandidateAssertion, FrozenStrictModel, OntologyPack
from .serialization import checksum_value
from .validation import validate_candidate


class SubmitOutcome(FrozenStrictModel):
    status: Literal["accepted", "duplicate", "rejected", "failed"]
    issue_codes: tuple[str, ...] = ()
    cursor: int | None = Field(default=None, ge=1)


class ShadowRunSummary(FrozenStrictModel):
    status: Literal["complete", "partial", "failed"]
    source_status: dict[str, str]
    counts: dict[str, int]
    error_codes: tuple[str, ...] = ()
    contract_version: Literal["0.1.0"] = "0.1.0"


class ValidationLedgerCoordinator:
    def __init__(
        self, ledger: ShadowLedger, ontology_pack: OntologyPack,
        *, source_material=None,
    ):
        self.ledger = ledger
        self.ontology_pack = ontology_pack
        self.source_material = source_material

    def submit(self, candidate: CandidateAssertion) -> SubmitOutcome:
        result = validate_candidate(
            candidate, self.ontology_pack, source_material=self.source_material,
        )
        if result.issues:
            codes = tuple(sorted({item.code for item in result.issues}))
            safe_payload = {
                "candidate_ref": candidate.candidate_id,
                "issue_codes": list(codes),
                "contract_version": candidate.bundle.contract_version,
                "ontology_version": self.ontology_pack.version,
            }
            try:
                appended = self.ledger.append_once(
                    stream="rejected", event_id=f"event:rejected:{candidate.candidate_id}",
                    idempotency_key=checksum_value(safe_payload), payload=safe_payload,
                )
            except Exception:
                return SubmitOutcome(status="failed", issue_codes=("LEDGER_WRITE_FAILED",))
            return SubmitOutcome(status="rejected", issue_codes=codes, cursor=appended.cursor)
        payload = candidate.model_dump(mode="json")
        idempotency_key = checksum_value({
            "contract_version": candidate.bundle.contract_version,
            "ontology_id": self.ontology_pack.ontology_id,
            "ontology_version": self.ontology_pack.version,
            "ontology_checksum": self.ontology_pack.content_checksum,
            "candidate": payload,
        })
        try:
            appended = self.ledger.append_once(
                stream="accepted", event_id=f"event:accepted:{candidate.candidate_id}",
                idempotency_key=idempotency_key, payload=payload,
            )
        except Exception:
            return SubmitOutcome(status="failed", issue_codes=("LEDGER_WRITE_FAILED",))
        return SubmitOutcome(status=appended.status, cursor=appended.cursor)

    def technical(self, *, source_kind: str, error_code: str) -> SubmitOutcome:
        payload = {
            "source_kind": source_kind,
            "error_code": error_code,
            "contract_version": "0.1.0",
            "ontology_version": self.ontology_pack.version,
        }
        try:
            appended = self.ledger.append_once(
                stream="technical",
                event_id=f"event:technical:{source_kind}:{error_code}",
                idempotency_key=checksum_value(payload), payload=payload,
            )
        except Exception:
            return SubmitOutcome(status="failed", issue_codes=("LEDGER_WRITE_FAILED",))
        return SubmitOutcome(status=appended.status, issue_codes=(error_code,), cursor=appended.cursor)


def run_shadow_job(
    *, coordinator: ValidationLedgerCoordinator,
    structured_source: Callable[[], DiagnosisExtraction],
    note_source: Callable[[], DiagnosisExtraction],
) -> ShadowRunSummary:
    counts: Counter[str] = Counter()
    source_status: dict[str, str] = {}
    errors: set[str] = set()
    for source_kind, source_fn in (
        ("structured", structured_source), ("document", note_source),
    ):
        try:
            extraction = source_fn()
        except Exception:
            source_status[source_kind] = "failed"
            errors.add(f"{source_kind.upper()}_SOURCE_FAILED")
            coordinator.technical(
                source_kind=source_kind, error_code=f"{source_kind.upper()}_SOURCE_FAILED",
            )
            counts["failed"] += 1
            continue
        source_status[source_kind] = "complete"
        counts["unmapped"] += len(extraction.issues)
        errors.update(issue.code for issue in extraction.issues)
        for candidate in extraction.candidates:
            outcome = coordinator.submit(candidate)
            counts[outcome.status] += 1
            errors.update(outcome.issue_codes)
    if counts["failed"] and counts["accepted"] + counts["duplicate"]:
        status = "partial"
    elif counts["failed"]:
        status = "failed"
    else:
        status = "complete"
    return ShadowRunSummary(
        status=status, source_status=source_status,
        counts={key: counts[key] for key in ("accepted", "duplicate", "rejected", "failed", "unmapped")},
        error_codes=tuple(sorted(errors)),
    )
