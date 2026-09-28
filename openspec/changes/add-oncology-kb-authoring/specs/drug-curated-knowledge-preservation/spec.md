## ADDED Requirements

### Requirement: Curated single-drug knowledge is inventoried as atomic records

The system SHALL extract every knowledge-bearing element from RD10-RD37 and future curated single-drug rules into stable, checksum-addressed knowledge atoms. Extraction MUST cover source restrictions, prompt additions, expected signals, clinical extensions, normalization/alias behavior, evidence policy, documentation guidance, and rule-specific regression cases. Each atom MUST retain its source rule ID and source field or test reference and MUST be classified as `SOURCE_RULE`, `CLINICAL_EXTENSION`, `EVIDENCE_POLICY`, `NORMALIZATION`, `DOCUMENTATION_GUIDANCE`, or `REGRESSION_GOLD`.

#### Scenario: Rule contains a clinical extension beyond the source row

- **WHEN** a curated rule contains a clinically meaningful inference or documentation hint that is not represented by the raw bulk restriction text
- **THEN** that content is emitted as a separate knowledge atom linked to the source rule
- **AND** it MUST NOT be collapsed into a generic “bulk covered” marker

#### Scenario: Rule contains no unique prompt text

- **WHEN** a rule only repeats a source restriction already represented in structured knowledge
- **THEN** it still has a `SOURCE_RULE` atom and a deterministic mapping record
- **AND** the coverage report can distinguish true equivalence from a missing extraction

### Requirement: Every knowledge atom maps to an explicit preservation target

Each atom SHALL have a migration status of `DISCOVERED`, `MAPPED`, or `VERIFIED` and MUST identify one target kind when mapped: `CONDITION`, `DICTIONARY`, `EVALUATOR_POLICY`, `REVIEW_GUIDANCE`, or `REGRESSION_CASE`. The mapping MUST reference a stable target ID, preserve the source checksum, and record verification evidence. A raw bulk row with the same drug and rule type MUST NOT by itself advance an atom to `MAPPED` or `VERIFIED`.

#### Scenario: Alias knowledge is preserved

- **WHEN** a curated rule recognizes a product, generic-name, spelling, or dosage-form variant absent from the current concept dictionary
- **THEN** its normalization atom maps to a dictionary/crosswalk target and remains non-verified until lookup tests pass

#### Scenario: Non-source guidance cannot become a source condition

- **WHEN** a prompt contains useful reviewer guidance but no supporting source fragment for a deterministic condition
- **THEN** the atom maps to `REVIEW_GUIDANCE` or `REGRESSION_CASE`
- **AND** it MUST NOT be published as if it were an insurance or guideline clause

### Requirement: Rule execution retirement is gated by complete verified coverage

A curated rule MAY be assigned execution status `abandoned` only when all of its knowledge atoms are `VERIFIED`, every atom has a live preservation target, and representative old-rule cases pass against the new bulk/structured path with the approved expected semantics. A rule with any `DISCOVERED` or unverified `MAPPED` atom MUST be `drafting`, MUST carry a machine-checkable migration-pending reference in notes, and MUST remain outside the default execution and router executable sets. Moving an existing rule from abandoned back to drafting MUST use the repository's forced status-transition workflow.

#### Scenario: Drug and rule type exist in bulk but one atom is missing

- **WHEN** bulk contains the same drug and restriction type but a curated evidence-policy atom has no verified target
- **THEN** migration validation fails for that rule and its permitted status is drafting rather than abandoned
- **AND** default production ownership remains with bulk, avoiding duplicate adjudication

#### Scenario: All atoms have verified targets

- **WHEN** every atom for a curated rule has a live target and equivalence regression evidence
- **THEN** the rule may remain or become abandoned with notes naming the bulk owner and coverage-manifest ID
- **AND** its YAML and regression corpus MUST NOT be deleted as part of retirement

### Requirement: Oncology release and non-oncology preservation use one coverage ledger

The first phase SHALL fully structure and review oncology knowledge while inventorying all RD10-RD37 knowledge atoms in the same preservation model. Oncology atoms referenced by the release MUST be `VERIFIED` before publication. Non-oncology atoms MAY remain `DISCOVERED` or `MAPPED` during the oncology release only if their source rules are drafting, their gaps are explicit in the global report, and they are not claimed as migrated or retired.

#### Scenario: Oncology release is ready while a non-oncology atom is pending

- **WHEN** all oncology release atoms are verified but a non-oncology curated rule still has a mapped, unverified atom
- **THEN** the oncology release may proceed if all other release gates pass
- **AND** the non-oncology rule remains drafting and is listed in the preservation backlog

#### Scenario: Oncology atom is unverified

- **WHEN** a curated oncology atom contributes to a proposed RD04 eligibility result but has not reached verified status
- **THEN** release creation fails and identifies the atom, source rule, target, and missing verification evidence

### Requirement: Preservation coverage is deterministic and reviewable

The toolchain SHALL generate both a canonical machine-readable coverage manifest and a human-reviewable coverage report. They MUST list every curated rule, atom, source checksum, classification, migration status, target, verification evidence, and allowed execution status; totals MUST reconcile by rule and atom type. Status or keyword changes MUST trigger router-index rebuild and validation against the manifest.

#### Scenario: Coverage report is regenerated

- **WHEN** the same rule snapshot and target release are analyzed twice
- **THEN** atom IDs, mappings, counts, canonical manifest bytes, and checksums are identical

#### Scenario: Router status drifts from migration status

- **WHEN** a rule is abandoned while its manifest contains a non-verified atom, or a drafting/abandoned curated rule appears executable in the router index
- **THEN** validation fails before release or deployment
- **AND** the report names the conflicting YAML status, manifest state, and router entry
