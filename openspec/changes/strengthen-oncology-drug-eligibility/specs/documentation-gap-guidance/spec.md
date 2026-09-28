## ADDED Requirements

### Requirement: Dual-axis oncology eligibility result

The system SHALL represent an oncology drug decision on two independent axes:

- `audit_disposition` MUST be exactly one of `NO_VIOLATION_FOUND`, `VIOLATION_FOUND`, or `REVIEW_REQUIRED`.
- `eligibility_status` MUST be exactly one of `SATISFIED`, `NOT_SATISFIED`, `DOCUMENTATION_GAP`, or `CONFLICT`.

The result MUST retain condition-level assessments and anchored evidence so that a documentation gap is distinguishable from contrary clinical evidence. A missing attestation-style document in an otherwise supported eligibility branch, with no contradictory evidence, SHALL produce `NO_VIOLATION_FOUND + DOCUMENTATION_GAP`. Missing underlying treatment history, a material chronology conflict, or competing evidence that prevents a safe determination SHALL produce `REVIEW_REQUIRED` with the applicable eligibility status. Explicit evidence that an indispensable condition is false SHALL produce `VIOLATION_FOUND + NOT_SATISFIED`.

#### Scenario: Complete eligible branch has no documentation gap

- **WHEN** every indispensable condition in one payable eligibility branch is supported by anchored evidence and no condition conflicts
- **THEN** the result is `NO_VIOLATION_FOUND + SATISFIED`

#### Scenario: Formal documentation is the only missing condition

- **WHEN** the disease, treatment history, and other substantive conditions support an eligibility branch, no evidence contradicts it, and only a required clinical attestation is absent from the available record
- **THEN** the result is `NO_VIOLATION_FOUND + DOCUMENTATION_GAP`, not a violation based solely on that document omission

#### Scenario: Material history is unknown or chronologically conflicted

- **WHEN** the underlying treatment history required to choose an eligibility branch is unavailable or contains a material unresolved chronology conflict
- **THEN** the result is `REVIEW_REQUIRED` rather than treating the uncertainty as either proof of eligibility or proof of violation

#### Scenario: Contrary evidence is not downgraded to a documentation gap

- **WHEN** anchored evidence explicitly shows that an indispensable eligibility condition is false and no alternative payable branch is satisfied
- **THEN** the result is `VIOLATION_FOUND + NOT_SATISFIED`, and adding a documentation suggestion MUST NOT convert it to `NO_VIOLATION_FOUND`

### Requirement: Backward-compatible verdict mapping

Every dual-axis result SHALL continue to expose the legacy `verdict` field for existing consumers. The compatibility mapping MUST be deterministic: `NO_VIOLATION_FOUND` maps to `CLEAN`, `VIOLATION_FOUND` maps to `VIOLATION`, and `REVIEW_REQUIRED` maps to `INCONCLUSIVE`. The more detailed `eligibility_status` MUST remain available and MUST NOT be discarded by the compatibility mapping.

#### Scenario: Clean result can still expose a documentation gap

- **WHEN** a result is `NO_VIOLATION_FOUND + DOCUMENTATION_GAP`
- **THEN** its legacy verdict is `CLEAN` while the structured eligibility status remains `DOCUMENTATION_GAP`

#### Scenario: Review-required result remains inconclusive

- **WHEN** a result has `audit_disposition=REVIEW_REQUIRED`
- **THEN** its legacy verdict is `INCONCLUSIVE` regardless of whether the unresolved eligibility status is a documentation gap or conflict

#### Scenario: Existing consumer reads the legacy verdict

- **WHEN** a consumer that understands only `verdict`, `reasoning`, and `evidence` reads a newly generated result
- **THEN** those fields remain present and meaningful without requiring the consumer to parse the new dual-axis fields

### Requirement: Condition-level documentation suggestions

When `eligibility_status=DOCUMENTATION_GAP`, the system SHALL generate a `documentation_suggestions` collection tied to the exact unresolved conditions. Each suggestion MUST contain `criterion_id`, `title`, `rationale`, `suggested_content`, `supporting_context`, `priority`, and `safety_note`; it MUST state what documentation is missing, give concise prospective wording suitable for the treating team, and explain how completing the record helps avoid an otherwise supportable claim being affected by a documentation omission. The human-readable reasoning SHALL surface these suggestions separately from the eligibility evidence. The system MUST NOT append the same generic pathology, self-pay, or review reminder to every drug result.

#### Scenario: Suggestion names the exact missing condition

- **WHEN** the only unresolved condition is documentation that the patient is unsuitable for hematopoietic stem-cell transplantation
- **THEN** the suggestion specifically asks the clinical record to state `不适合造血干细胞移植` and a brief reason, rather than generically asking the reviewer to recheck all pathology and payment records

#### Scenario: Fully supported result has no unnecessary suggestion

- **WHEN** the result is `NO_VIOLATION_FOUND + SATISFIED` and no condition-level documentation gap exists
- **THEN** the system does not generate a documentation-gap suggestion

#### Scenario: Multiple gaps remain individually traceable

- **WHEN** two distinct required documentation conditions are unresolved
- **THEN** the result contains two independently identified suggestions, each linked to its own condition assessment and source context

### Requirement: Pola transplant-gap patient-centered documentation guidance

For the synthetic Pola transplant-gap golden fixture, the system SHALL recognize the available evidence of an adult DLBCL patient with prior multi-line treatment and disease progression while keeping the absent transplant-ineligibility statement as a documentation gap. The result MUST be `NO_VIOLATION_FOUND + DOCUMENTATION_GAP`, with legacy verdict `CLEAN`, and MUST present the following patient-centered meaning in its reasoning or structured suggestion:

`患者74岁且已多线治疗；如拟使用该药，建议病程中补充“不适合造血干细胞移植”及简要原因，避免因文书缺项影响医保报销。`

#### Scenario: Pola transplant-gap is clean with a focused documentation suggestion

- **WHEN** the synthetic Pola transplant-gap golden fixture contains DLBCL, prior multi-line treatment, disease progression, age 74, and use or proposed use of polatuzumab vedotin, but no statement that the patient is unsuitable for hematopoietic stem-cell transplantation
- **THEN** the result is `NO_VIOLATION_FOUND + DOCUMENTATION_GAP`, its legacy verdict is `CLEAN`, and the transplant-specific patient-centered suggestion is displayed

#### Scenario: Age supplies context but not fabricated evidence

- **WHEN** the Pola transplant-gap suggestion refers to the patient's age and treatment burden
- **THEN** age 74 and prior multi-line treatment are retained as supporting context, while the transplant-ineligibility condition remains unresolved until an actual clinical statement is present

### Requirement: Documentation guidance cannot alter evidence or eligibility facts

Documentation suggestions SHALL be generated only after condition evaluation and MUST remain separate from evidence. A suggestion MUST NOT change any condition status, create an evidence anchor, claim that a missing statement already exists, or cause the system to report that a clinician has made an assessment that is absent from the source record. Re-running evaluation against unchanged source data MUST yield the same condition statuses whether or not suggestions are enabled.

#### Scenario: Suggestion does not satisfy the missing transplant condition

- **WHEN** the system generates a suggestion to supplement `不适合造血干细胞移植` but the source record still lacks that statement
- **THEN** the transplant-ineligibility condition remains unresolved, no synthetic evidence is added, and `eligibility_status` remains `DOCUMENTATION_GAP`

#### Scenario: Disabling suggestions does not change the decision

- **WHEN** the same patient evidence is evaluated once with documentation suggestions enabled and once with them disabled
- **THEN** condition statuses, `audit_disposition`, `eligibility_status`, legacy verdict, and evidence anchors are identical; only the suggestion output differs

#### Scenario: Suggestion is prospective rather than retrospective

- **WHEN** a documentation gap is reported
- **THEN** the wording asks what to supplement if the drug is to be used and MUST NOT state or imply that the missing assessment has already been documented
