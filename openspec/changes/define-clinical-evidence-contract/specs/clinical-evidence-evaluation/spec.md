## ADDED Requirements

### Requirement: A/B evaluation plans are immutable and versioned
The system SHALL define strict, versioned `EvaluationPlan`, `EvaluationCase`, `ArmObservation`, `ExpertAdjudication`, `MetricResult`, `AcceptanceGate`, and `EvaluationReport` records. A plan SHALL bind an exact source snapshot and candidate-query version, unit of analysis, A/B arm code/config/model/tool/ontology/knowledge versions, repetitions, blinding strategy, harm matrix, acceptance profile, and content checksum. A completed report SHALL reference the exact digests of its plan, cases, observations, adjudications, and manifest.

#### Scenario: Plan mutation creates a new identity
- **WHEN** an arm configuration, knowledge version, cohort definition, harm matrix, or acceptance threshold changes
- **THEN** the evaluator requires a new plan version and checksum and does not rewrite an existing report

#### Scenario: Floating or incomplete arm version is rejected
- **WHEN** a plan omits the source snapshot checksum or either arm's implementation/configuration version
- **THEN** evaluation validation fails before any paired effect is reported

### Requirement: Clinical effects use same-input paired units
Each primary A/B case SHALL contain observations from both arms over the same immutable source snapshot and the same predeclared analysis unit. Missing arms, candidate-set drift, source-version drift, or duplicated patient outcomes expanded across multiple drugs SHALL invalidate the affected paired analysis. Patient-level legacy outcomes MAY be compared only with patient-level B projections; finer-grained B evidence diagnostics SHALL retain their own denominator and SHALL NOT inflate the clinical sample size.

#### Scenario: Same shadow run produces a valid pair
- **WHEN** one oncology shadow run records the legacy patient verdict and the structured patient-level eligibility projection from the same source snapshot and candidate set
- **THEN** the evaluator creates one patient-level paired outcome and separately records drug/scope evidence diagnostics

#### Scenario: Historical verdict is not paired
- **WHEN** an old verdict came from a different query window, model, rule set, or source snapshot
- **THEN** it is labeled `historical_unpaired` and excluded from paired effect and promotion gates

### Requirement: Repetitions expose stability instead of hiding it
The plan SHALL preserve every repetition for every arm. Any modal outcome used in a primary confusion matrix SHALL be predeclared, SHALL map ties to INCONCLUSIVE, and SHALL NOT replace raw repetitions. The evaluator SHALL report per-arm canonical digest stability, outcome disagreement, technical failure, and resource use.

#### Scenario: Legacy arm changes across repetitions
- **WHEN** A returns different outcomes for the same frozen case across repetitions
- **THEN** every observation remains persisted and A's instability metric increases even if its modal outcome agrees with the reference

#### Scenario: Deterministic B output drifts
- **WHEN** B returns a different canonical result for identical input and versions
- **THEN** the reproducibility gate fails rather than normalizing away the difference

### Requirement: Metrics preserve denominators and non-compensating safety gates
The evaluator SHALL report confusion matrices and numerator/denominator/95% interval for exact agreement, false violation, false clean, unsafe auto-decision, correct automation, appropriate and unnecessary abstention, decisive-evidence grounding, locator resolvability, provenance/proof/version completeness, conflict visibility, source retrieval, and technical failure. A zero denominator SHALL produce `not_estimable`, not zero. Harm-weighted loss SHALL use the plan's versioned cost matrix and paired differences; performance, latency, token, or coverage improvements SHALL NOT compensate for a failed safety or integrity gate. The evaluator SHALL NOT emit a single aggregate score.

#### Scenario: All cases abstain
- **WHEN** an arm returns INCONCLUSIVE for every reference V/C case
- **THEN** unsafe-auto errors may be zero but correct automation and unnecessary-abstention gates expose the arm as not useful

#### Scenario: Faster arm has a new false CLEAN
- **WHEN** B reduces latency but introduces a safety-critical false CLEAN absent from A
- **THEN** the safety regression gate fails regardless of the latency improvement

#### Scenario: Class denominator is absent
- **WHEN** a cohort contains no adjudicated VIOLATION reference cases
- **THEN** violation recall is `not_estimable` and the report cannot claim complete clinical effectiveness

### Requirement: Statistical summaries are deterministic and paired
Proportion intervals SHALL use a declared Wilson 95% method. Paired harm-loss differences SHALL use a plan-fixed deterministic bootstrap seed and replication count to produce a one-sided 95% upper bound, and paired win/loss observations SHALL include an exact sign-test. Identical canonical inputs SHALL yield byte-stable metric values. Confidence calibration SHALL be omitted unless both arms expose probabilities with the same defined semantics.

#### Scenario: Same observations are evaluated twice
- **WHEN** the same plan, observations, adjudications, seed, and bootstrap count are evaluated twice
- **THEN** metric values, intervals, gate decisions, and canonical report digest are identical

#### Scenario: Arm confidence semantics differ
- **WHEN** A exposes an LLM self-reported confidence while B exposes four-state evidence sufficiency rather than a probability
- **THEN** the report marks probability calibration `not_comparable` instead of comparing the numeric values

### Requirement: Acceptance profiles are explicit and sample-aware
The contract SHALL provide versioned `CONFORMANCE`, `SHADOW`, and `PROMOTION` profiles. All profiles SHALL require 100% pair/input completeness, zero schema/PHI/technical errors, zero newly introduced safety-critical B regression, complete provenance/proof/version references, and 100% B canonical repeat stability. SHADOW SHALL additionally require at least 30 adjudicated cases and at least 5 cases in every reference class present, B unsafe-auto performance no worse than A with paired upper bound at most 5 percentage points, paired harm-loss upper bound at most 0.05, correct-automation lower bound at least -5 percentage points, and at least 95% decisive grounding and locator resolution. PROMOTION SHALL require at least 100 adjudicated cases and 20 per reference class, unsafe-auto Wilson upper 95% at most 5%, paired harm-loss upper bound at most 0.02, correct-automation lower bound at least -5 percentage points, 100% decisive grounding/locator resolution for auto-decisions, at least 98% overall grounding/locator resolution, and blinded explanation median at least 4/5 with source retrieval at least 95%.

#### Scenario: Sample is too small
- **WHEN** all point estimates meet SHADOW thresholds but only eight adjudicated cases are available
- **THEN** the report status is `INSUFFICIENT_EVIDENCE`, not PASS

#### Scenario: Profile passes
- **WHEN** every non-compensating gate and sample threshold for the selected profile passes
- **THEN** the report is eligible for the next human decision but does not grant deployment or production authorization

### Requirement: Expert reference and usability review are blinded and append-only
Clinical reference outcomes SHALL come from at least two independent reviewers who do not see arm identity or each other's decision. Disagreements SHALL be resolved by a separately recorded adjudication. Original reviews and the final reference SHALL remain append-only and versioned. Expert usability observations SHALL record coded evidence sufficiency, source-location success, explanation score, review duration, and correction count; public reports SHALL not contain free-text clinical notes.

#### Scenario: Reviewers disagree
- **WHEN** two blinded reviewers assign different reference outcomes
- **THEN** neither review is overwritten, a separate adjudication is required, and the case is excluded from final-reference metrics until adjudicated

#### Scenario: Arm identity leaks
- **WHEN** a review packet exposes which result is legacy or structured
- **THEN** the usability review is invalidated for blinded A/B acceptance

### Requirement: Evaluation artifacts are canonical, persistent, and privacy-safe
An evaluation package SHALL persist canonical plan, cases, arm observations, expert adjudications, report, and manifest as separately checksummed artifacts. A new run SHALL create a new evaluation identity and SHALL NOT overwrite earlier reports. Repository fixtures SHALL be explicitly synthetic and de-identified. Real-case packages SHALL remain outside Git in approved 0700 directories with 0600 files and salted case/run references; public summaries SHALL contain only aggregate metrics, version references, safe reason codes, and digests.

#### Scenario: Report provenance can be replayed
- **WHEN** an expert or engineer opens a persisted report
- **THEN** every metric and case delta can be traced to the exact plan, arm observations, reference adjudication, code/config/knowledge versions, and source snapshot digest

#### Scenario: Public package contains PHI
- **WHEN** a public evaluation artifact contains a raw patient identifier, clinical source text, credential, salt, or unsalted run/ownership identifier
- **THEN** the privacy gate fails without echoing the protected value

### Requirement: Oncology A/B exposes expert-readable case deltas
The oncology adapter SHALL persist, for each valid same-run pair, A/B outcome, expert reference when available, eligibility status, decisive criteria, UNKNOWN/CONFLICT and documentation gaps, added/removed evidence references, proof/source/knowledge versions, technical status, and safe reason codes. It SHALL distinguish patient-level outcome metrics from drug/scope evidence diagnostics and SHALL preserve `historical_unpaired` reports without relabeling them as paired.

#### Scenario: B changes V to I due to missing evidence
- **WHEN** A returns VIOLATION and B returns INCONCLUSIVE because a decisive criterion is UNKNOWN with no source evidence
- **THEN** the case delta explains the outcome change, missing criterion, evidence coverage, and exact knowledge/ontology versions without exposing raw PHI

#### Scenario: Required pairing metadata is missing
- **WHEN** the same-run record lacks source snapshot digest, candidate denominator, arm manifest, or either patient-level outcome
- **THEN** the paired evaluation is INVALID and cannot satisfy SHADOW or PROMOTION acceptance
