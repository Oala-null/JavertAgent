## ADDED Requirements

### Requirement: Versioned oncology eligibility condition trees
The system SHALL represent every supported oncology drug payment restriction as a machine-evaluable, versioned condition tree. Each tree MUST identify `rule_id`, `drug_concept_id`, `indication_branch_id`, `version`, effective date range, review status, and one or more immutable source references. Internal nodes MUST use explicit `AND` or `OR` operators, and every leaf MUST have a stable `criterion_id`, a supported criterion type, an expected value or operator, and an evidence policy. Free-text restriction text MAY be retained for traceability but MUST NOT be the executable representation.

#### Scenario: Compile a multi-condition urothelial carcinoma restriction
- **WHEN** the active restriction requires urothelial carcinoma, locally advanced or metastatic disease, prior platinum-containing chemotherapy, and HER2 overexpression
- **THEN** the compiled tree contains separate stable leaves for diagnosis, stage, prior treatment, and HER2 status, represents locally advanced versus metastatic as an `OR` node, represents the mandatory conditions as an `AND` node, and retains the exact restriction source and version

#### Scenario: Unsupported condition type is not silently interpreted
- **WHEN** a restriction contains a condition type for which no registered deterministic evaluator exists
- **THEN** compilation or evaluation MUST mark that criterion `UNKNOWN`, identify the unsupported type in the proof tree, and require review rather than asking the LLM to invent executable semantics

### Requirement: Four-state leaf evaluation
Each eligibility leaf SHALL be evaluated to exactly one of `SATISFIED`, `NOT_SATISFIED`, `UNKNOWN`, or `CONFLICT`. `SATISFIED` MUST require qualifying evidence that meets the leaf's context, method, threshold, and temporal policy. `NOT_SATISFIED` MUST require explicit contradictory evidence. Missing, unsearched, method-ambiguous, or insufficient evidence MUST yield `UNKNOWN`, not `NOT_SATISFIED`. Mutually incompatible applicable evidence for which no configured precedence rule resolves the discrepancy MUST yield `CONFLICT`. An LLM MAY extract candidate facts, but only the deterministic evaluator SHALL assign the final leaf state.

#### Scenario: Missing prior-treatment evidence remains unknown
- **WHEN** a prior-platinum leaf is evaluated and the available record contains neither a qualifying platinum regimen nor reliable evidence that no prior platinum treatment occurred
- **THEN** the leaf state is `UNKNOWN` and MUST NOT be converted to `NOT_SATISFIED` merely because the record is incomplete

#### Scenario: Explicit biomarker result fails a threshold
- **WHEN** the applicable biomarker normalizer returns HER2 IHC `1+` and the restriction accepts only IHC `2+` or `3+`
- **THEN** the HER2 leaf state is `NOT_SATISFIED` with the normalized result and threshold comparison preserved

#### Scenario: Applicable evidence conflicts
- **WHEN** two applicable evidence items assert mutually incompatible values for the same criterion and the rule has no source-backed precedence policy
- **THEN** the leaf state is `CONFLICT`, both evidence items remain attached, and neither value is silently discarded

### Requirement: Deterministic AND and OR aggregation
The evaluator SHALL aggregate child states deterministically. For `AND`, `NOT_SATISFIED` MUST be decisive if any child is `NOT_SATISFIED`; otherwise `CONFLICT` MUST be returned if any child is `CONFLICT`; otherwise `UNKNOWN` MUST be returned if any child is `UNKNOWN`; otherwise the result MUST be `SATISFIED`. For `OR`, `SATISFIED` MUST be decisive if any child is `SATISFIED`; otherwise `CONFLICT` MUST be returned if any child is `CONFLICT`; otherwise `UNKNOWN` MUST be returned if any child is `UNKNOWN`; otherwise the result MUST be `NOT_SATISFIED`.

#### Scenario: Failed mandatory criterion determines an AND node
- **WHEN** an `AND` node has child states `SATISFIED`, `UNKNOWN`, and `NOT_SATISFIED`
- **THEN** the node state is `NOT_SATISFIED` and the proof identifies the failed child as decisive while retaining the unknown child

#### Scenario: One valid indication satisfies an OR node
- **WHEN** an `OR` node has child states `NOT_SATISFIED`, `SATISFIED`, and `CONFLICT`
- **THEN** the node state is `SATISFIED`, the satisfying branch is identified as decisive, and the other branches remain visible in the proof tree

#### Scenario: No decisive child preserves uncertainty
- **WHEN** an `AND` node has only `SATISFIED` and `UNKNOWN` children
- **THEN** the node state is `UNKNOWN` rather than `SATISFIED` or `NOT_SATISFIED`

### Requirement: Indication-branch isolation
The system MUST evaluate each drug indication as a separate branch with its own cancer type, stage, biomarker, prior-treatment, and source context. Evidence or thresholds from one cancer or indication branch MUST NOT satisfy a leaf in another branch. A drug-level `OR` MAY combine completed indication branches only after each branch has been evaluated independently.

#### Scenario: Gastric and urothelial criteria do not bleed across branches
- **WHEN** the same drug has one restriction branch for gastric cancer and another for urothelial carcinoma
- **THEN** gastric cancer diagnosis or gastric-specific HER2 criteria MUST NOT satisfy the urothelial branch, and the drug-level result records which branch, if any, was decisive

### Requirement: Traceable eligibility proof tree
Every evaluation SHALL emit a proof tree isomorphic to the executable condition tree. Each node MUST include its stable ID, operator or criterion type, resulting state, decisive-child information where applicable, and evaluator version. Each leaf MUST include the normalized fact, expected condition, comparison result, evidence anchors, evidence observation and service dates when available, normalizer version, source version, and any uncertainty or conflict reason. The proof tree MUST retain non-decisive and unknown children rather than presenting only the final outcome.

#### Scenario: Reviewer can reconstruct a failed result
- **WHEN** an eligibility tree evaluates to `NOT_SATISFIED`
- **THEN** a reviewer can traverse the emitted proof from the root to every decisive failed leaf and from each leaf to its originating note, pathology, fee, or structured-record anchor without relying on free-form reasoning

#### Scenario: Unknown evidence remains visible
- **WHEN** one mandatory leaf is `UNKNOWN` but another mandatory leaf decisively fails
- **THEN** the root may be `NOT_SATISFIED` according to the AND truth table, but the proof tree still records the unknown leaf and why its evidence was insufficient

### Requirement: Source-version governance and reproducibility
Only an effective, schema-valid, expert-approved condition-tree version SHALL be eligible for automatic final evaluation. Every source reference MUST carry a stable source identifier, title, version or publication/effective date, retrieval date, and content checksum or equivalent immutable revision identifier. The audit result MUST persist the exact condition-tree and source versions used so that reevaluation with unchanged inputs reproduces the same proof. Missing, expired without replacement, unapproved, or ambiguous source versions MUST produce a review-required result and MUST NOT silently fall back to a different rule text.

#### Scenario: Historical audit uses the historical effective rule
- **WHEN** a service date falls within version 1 of a payment restriction and version 2 became effective later
- **THEN** the evaluator uses version 1, persists that version in the proof, and does not apply version 2 retroactively

#### Scenario: Unapproved compiled rule cannot auto-decide
- **WHEN** a newly compiled tree has `review_status` other than the configured approved status
- **THEN** automatic eligibility evaluation is blocked, the condition status is reported as unresolved for review, and no CLEAN or VIOLATION conclusion is derived from that tree

### Requirement: Urothelial HER2-low golden eligibility regression
The system MUST include a minimized, de-identified synthetic golden fixture for disitamab vedotin that contains urothelial carcinoma context, a pathology result written as `CerbB2(1+)`, and no reliable evidence of prior platinum-containing chemotherapy. Under the approved urothelial restriction accepting HER2 IHC `2+` or `3+`, the deterministic proof MUST mark the HER2 criterion `NOT_SATISFIED`, the prior-platinum criterion `UNKNOWN`, and the mandatory `AND` root `NOT_SATISFIED`. The evaluator MUST NOT reinterpret a current-treatment adverse-reaction statement as proof of prior platinum treatment.

#### Scenario: Urothelial HER2-low fixture fails HER2 without hallucinating prior platinum
- **WHEN** the synthetic urothelial HER2-low golden fixture is evaluated against the applicable disitamab vedotin urothelial condition tree
- **THEN** the proof shows `CerbB2(1+)` normalized to HER2 IHC `1+`, HER2 `NOT_SATISFIED`, prior platinum `UNKNOWN`, and root `NOT_SATISFIED`, with no invented platinum regimen or unsupported treatment-history fact
