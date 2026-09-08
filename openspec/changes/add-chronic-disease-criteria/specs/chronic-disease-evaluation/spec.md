## ADDED Requirements

### Requirement: Candidate facts are normalized before deterministic evaluation
The system SHALL normalize candidate clinical facts from diagnosis, note, laboratory, examination, procedure, and other declared evidence domains into typed observations before applying a criteria tree. Each observation MUST retain its raw value or assertion, normalized value, unit, observation time, service time, source domain, patient-scoped evidence anchor, extraction method, and normalizer version. An LLM MUST be limited to proposing candidate facts and MUST NOT assign a final criterion, node, or qualification state.

#### Scenario: A narrative report supplies a candidate fact
- **WHEN** a narrative examination report is parsed into a candidate disease-stage or finding assertion
- **THEN** the deterministic evaluator receives a typed observation with its source anchor and extraction metadata and independently decides whether it meets the leaf policy

#### Scenario: A candidate fact lacks a patient-scoped anchor
- **WHEN** an extracted assertion cannot be traced to the evaluated patient's source record
- **THEN** the assertion MUST NOT satisfy a criterion and the proof records the unusable evidence reason

### Requirement: Every evaluated node uses exactly four clinical evidence states
Every evaluated leaf and aggregate node SHALL have exactly one state from `SATISFIED`, `NOT_SATISFIED`, `UNKNOWN`, or `CONFLICT`. `SATISFIED` MUST require applicable evidence that meets the declared criterion. `NOT_SATISFIED` MUST require applicable affirmative evidence that contradicts or fails the declared criterion. Missing, unsearched, unusable, insufficient, or temporally inapplicable evidence MUST yield `UNKNOWN`, never `NOT_SATISFIED`. Mutually incompatible applicable evidence with no configured source-backed precedence MUST yield `CONFLICT`. Only deterministic evaluator code SHALL assign these states.

#### Scenario: Required evidence is missing
- **WHEN** a criterion requires a qualifying examination and no applicable examination result is available
- **THEN** the leaf state is `UNKNOWN` and MUST NOT be treated as an explicit negative finding

#### Scenario: Evidence explicitly fails a criterion
- **WHEN** a valid applicable measurement is normalized and its value is outside the criterion's qualifying range
- **THEN** the leaf state is `NOT_SATISFIED` with the failed comparison preserved

#### Scenario: Applicable evidence is irreconcilable
- **WHEN** two applicable observations assert mutually incompatible values and no approved precedence policy resolves them
- **THEN** the leaf state is `CONFLICT` and both observations remain attached to the result

### Requirement: AND and OR nodes use deterministic four-state truth tables
The evaluator SHALL aggregate child states without free-form interpretation. An `AND` node MUST be `NOT_SATISFIED` when any child is `NOT_SATISFIED`; otherwise it MUST be `CONFLICT` when any child is `CONFLICT`; otherwise it MUST be `UNKNOWN` when any child is `UNKNOWN`; otherwise it MUST be `SATISFIED`. An `OR` node MUST be `SATISFIED` when any child is `SATISFIED`; otherwise it MUST be `CONFLICT` when any child is `CONFLICT`; otherwise it MUST be `UNKNOWN` when any child is `UNKNOWN`; otherwise it MUST be `NOT_SATISFIED`.

#### Scenario: One mandatory condition fails
- **WHEN** an `AND` node receives `SATISFIED`, `UNKNOWN`, and `NOT_SATISFIED` children
- **THEN** the node state is `NOT_SATISFIED`, the failed child is marked decisive, and the unknown child remains visible

#### Scenario: One alternative qualifies
- **WHEN** an `OR` node receives `NOT_SATISFIED`, `CONFLICT`, and `SATISFIED` children
- **THEN** the node state is `SATISFIED`, the satisfying child is marked decisive, and the other children remain visible

#### Scenario: No child is decisive
- **WHEN** an `AND` node has only `SATISFIED` and `UNKNOWN` children
- **THEN** the node state is `UNKNOWN`

### Requirement: AT_LEAST_N nodes use lower and upper satisfaction bounds
For an `AT_LEAST_N` node, the evaluator SHALL count direct child states and MUST return `SATISFIED` when the count of `SATISFIED` children is at least `N`. It MUST return `NOT_SATISFIED` when the sum of `SATISFIED`, `UNKNOWN`, and `CONFLICT` children is less than `N`. When neither decisive condition applies, it MUST return `CONFLICT` if at least one outcome-relevant child is `CONFLICT`, otherwise it MUST return `UNKNOWN`. The evaluator MUST retain every child and the lower bound, upper bound, threshold, and decisive calculation in the proof.

#### Scenario: The threshold is already met
- **WHEN** an `AT_LEAST_N` node with `N=2` has child states `SATISFIED`, `SATISFIED`, and `CONFLICT`
- **THEN** the node state is `SATISFIED` because the lower satisfaction bound meets the threshold

#### Scenario: The threshold cannot be met
- **WHEN** an `AT_LEAST_N` node with `N=2` has child states `SATISFIED`, `NOT_SATISFIED`, and `NOT_SATISFIED`
- **THEN** the node state is `NOT_SATISFIED` because the upper satisfaction bound is below the threshold

#### Scenario: An unresolved child can change the outcome
- **WHEN** an `AT_LEAST_N` node with `N=2` has child states `SATISFIED`, `UNKNOWN`, and `NOT_SATISFIED`
- **THEN** the node state is `UNKNOWN` because the unresolved child determines whether the threshold can be met

#### Scenario: A conflicting child can change the outcome
- **WHEN** an `AT_LEAST_N` node with `N=2` has child states `SATISFIED`, `CONFLICT`, and `NOT_SATISFIED`
- **THEN** the node state is `CONFLICT` because resolving the conflicting child can change the threshold result

### Requirement: Numeric comparisons are unit-safe and boundary-exact
The evaluator SHALL compare numeric evidence only after preserving the raw quantity and converting an explicitly recognized source unit to the criterion's canonical unit through a versioned allowlisted conversion. It MUST support the comparison operators declared by the criteria schema, including inclusive and exclusive bounds, without rounding across a decision boundary. Missing units, unrecognized units, dimensionally incompatible units, non-numeric values, and conversions without an approved rule MUST produce `UNKNOWN`; mutually incompatible applicable measurements with no precedence policy MUST produce `CONFLICT`.

#### Scenario: A compatible value is converted
- **WHEN** a laboratory result uses an allowlisted unit equivalent to the criterion's canonical unit
- **THEN** the evaluator applies the versioned conversion, compares the unrounded canonical value, and records the raw and normalized quantities in the proof

#### Scenario: A threshold boundary is inclusive
- **WHEN** a normalized result equals a criterion expressed with `less_than_or_equal`
- **THEN** the numeric comparison treats the boundary as satisfying and records the exact operator used

#### Scenario: A unit cannot be interpreted safely
- **WHEN** a numeric result has no unit or a unit that is not dimensionally compatible with the criterion
- **THEN** the leaf state is `UNKNOWN` and the evaluator MUST NOT assume a default unit

### Requirement: Repeated measurements and temporal windows are evaluated from distinct dated observations
For a criterion requiring repeated evidence, the evaluator SHALL use distinct patient observations that satisfy the leaf's evidence and value policy. Duplicate rows, repeated imports, or multiple representations of the same specimen or examination MUST count once. The evaluator MUST enforce the declared minimum or maximum observation count, inclusive or exclusive window boundaries, minimum or maximum separation, lookback anchor, and observation-date basis. A missing or unreliable date, an insufficient number of observations, or a temporal span that cannot be established MUST yield `UNKNOWN` unless applicable dated evidence affirmatively proves that the temporal requirement cannot be met.

#### Scenario: Two results meet a minimum separation
- **WHEN** two distinct qualifying measurements occur on valid dates at least the configured three months apart
- **THEN** the repeated-measurement criterion is `SATISFIED` and the proof identifies both observations and their computed separation

#### Scenario: A duplicate import is present
- **WHEN** the same specimen result appears in laboratory and imported-note representations
- **THEN** identity resolution counts it as one observation and MUST NOT satisfy a two-observation requirement by duplication

#### Scenario: The second dated result is absent
- **WHEN** a criterion requires two qualifying observations but only one distinct dated qualifying observation is available
- **THEN** the criterion is `UNKNOWN`, not `NOT_SATISFIED`

#### Scenario: Available dated evidence proves an excessive interval
- **WHEN** a criterion requires two events within an inclusive maximum window and all applicable, complete, distinct events fall outside that window
- **THEN** the criterion is `NOT_SATISFIED` with the dates, anchor, boundary policy, and interval calculation recorded

### Requirement: Every evaluation emits a complete proof tree
Every evaluation SHALL emit a proof tree isomorphic to the selected executable criteria tree. Each node MUST include its stable ID, operator or criterion type, four-state result when evaluated, evaluator version, criteria revision, and decisive-child or bound calculation where applicable. Each leaf MUST include the expected condition, normalized facts, comparison result, source anchors, observation and service dates when available, unit conversion, repetition and temporal calculations, normalizer version, and uncertainty or conflict reason. Non-decisive, unknown, conflicting, and unused alternative children MUST remain in the proof.

#### Scenario: A reviewer reconstructs a qualified result
- **WHEN** a disease root evaluates to `SATISFIED`
- **THEN** the reviewer can traverse the proof from the root through every qualifying branch to patient-scoped source anchors and reproduce each deterministic comparison

#### Scenario: A decisive failure coexists with missing evidence
- **WHEN** an `AND` root is `NOT_SATISFIED` because one child failed while another child is `UNKNOWN`
- **THEN** the proof identifies the decisive failed child and still records the unknown child and its missing-evidence reason

### Requirement: Qualified is a nullable clinical-recognition result, not an audit violation
The evaluation result SHALL expose `qualified` as a nullable boolean and a qualification disposition. A completed root state of `SATISFIED` MUST produce `qualified=true` and `QUALIFIED`; `NOT_SATISFIED` MUST produce `qualified=false` and `NOT_QUALIFIED`; and `UNKNOWN` or `CONFLICT` MUST produce `qualified=null` and `REVIEW_REQUIRED`. A criteria entry with execution status `BLOCKED` MUST also produce `qualified=null` and `REVIEW_REQUIRED`, even when shadow leaf evaluation is available. `qualified=true` means only that the evaluated evidence meets the selected chronic-disease recognition standard; it MUST NOT be labeled, stored, counted, or projected as a healthcare-payment violation.

#### Scenario: The root satisfies the standard
- **WHEN** an approved active disease tree evaluates to root state `SATISFIED`
- **THEN** the result records `qualified=true` and `QUALIFIED` without producing a violation conclusion

#### Scenario: The root lacks decisive evidence
- **WHEN** an approved active disease tree evaluates to `UNKNOWN` or `CONFLICT`
- **THEN** the result records `qualified=null`, disposition `REVIEW_REQUIRED`, and the unresolved proof paths

#### Scenario: A Category C root is blocked
- **WHEN** shadow evaluation finds qualifying leaf evidence for a disease whose automatic root is `BLOCKED`
- **THEN** the result records `qualified=null` and `REVIEW_REQUIRED`, preserves the shadow proof, and identifies the blocking criteria revision and reason

#### Scenario: The root affirmatively fails the standard
- **WHEN** an approved active disease tree evaluates to root state `NOT_SATISFIED`
- **THEN** the result records `qualified=false` and `NOT_QUALIFIED` with the decisive failed conditions and MUST NOT interpret absent evidence as the reason for failure
