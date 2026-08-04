## ADDED Requirements

### Requirement: Versioned expert workbooks for both oncology knowledge bases

The system SHALL generate two independent `.xlsx` expert workbooks named for the oncology eligibility tree KB and oncology regimen composition KB. Each workbook MUST contain a template schema version, export batch ID, source snapshot checksum, generation time, row-count summary, instructions, structured domain sheets, a dedicated expert-review sheet, read-only dictionaries, and a QA sheet.

#### Scenario: Eligibility workbook is complete

- **WHEN** the eligibility workbook is generated from the current oncology source snapshot
- **THEN** it contains sheets for batch metadata, drug products, source text, indication branches, condition nodes, expert review, dictionaries, QA, and oncology curated-knowledge preservation
- **AND** every exported source restriction or guideline indication is addressable by a stable row ID

#### Scenario: Regimen workbook is complete

- **WHEN** the regimen workbook is generated from the current regimen asset and mined alias candidates
- **THEN** it contains sheets for regimen master data, aliases, cancer contexts, components, reserved schedule fields, expert review, and QA
- **AND** the workbook contains no patient identifier or source note text

### Requirement: Stable machine columns and explicit expert-editable columns

Each exported entity SHALL carry a stable logical ID, revision ID or candidate ID, source reference, row checksum, and lifecycle status. Machine-controlled columns MUST be protected and visually distinguished from expert-editable columns. Expert decisions MUST be recorded in explicit cells using supported enums; Excel comments or formatting alone MUST NOT constitute an importable review decision.

#### Scenario: Expert edits supported columns

- **WHEN** an expert chooses `APPROVE_WITH_EDIT`, enters a corrected value, comment, evidence reference, reviewer ID, and review time
- **THEN** the importer reads the decision as a structured review event linked to the original stable row ID

#### Scenario: Machine key is modified

- **WHEN** a workbook changes a stable ID, source checksum, row checksum, or other protected machine column
- **THEN** validation fails for that row and the workbook MUST NOT be materialized into authoring tables

### Requirement: Structured eligibility branches and condition nodes

The eligibility workbook SHALL represent each source rule as one or more indication branches and each branch as an adjacency-list condition tree with exactly one root. Node kinds MUST be `ALL`, `ANY`, or `LEAF`; negative meaning MUST be expressed by a leaf operator rather than an untyped free-text negation. First-phase leaf types MUST cover disease, population, biomarker, prior-treatment, treatment-line/status, combination, intervention-suitability, and eligibility time-window conditions, and MAY represent unsupported conditions only as explicit review-blocking candidates.

#### Scenario: Multi-branch source restriction is exported

- **WHEN** a source restriction contains numbered alternative indications
- **THEN** every alternative is exported as a separate branch with its own stable branch ID and source span
- **AND** the source restriction remains linked to the complete branch set

#### Scenario: Unsupported condition is encountered

- **WHEN** a condition cannot be represented by the current criterion/operator schema
- **THEN** it is exported with `criterion_type=unsupported`, an explanation, and `needs_review`
- **AND** it MUST NOT be silently omitted or eligible for an approved release

### Requirement: Typed combination requirements

Combination-treatment leaves SHALL reference a drug concept, drug class, or approved regimen and MUST distinguish `REQUIRED`, `OPTIONAL`, and `WITH_OR_WITHOUT`. The authoring model MUST preserve whether a clause requires an exact drug, any member of a class, or a complete regimen.

#### Scenario: Required and optional combination are separated

- **WHEN** source text states that a drug is combined with A and B, with or without C
- **THEN** A and B are represented as required targets and C as `WITH_OR_WITHOUT`
- **AND** the tree MUST NOT require C for satisfaction

#### Scenario: Drug-class combination is represented

- **WHEN** source text requires a platinum drug rather than a named ingredient
- **THEN** the leaf references the approved platinum drug-class ID
- **AND** it MUST NOT be expanded into an arbitrary single platinum product

### Requirement: Review decisions are append-only and field-addressable

The import workflow SHALL support `APPROVE`, `APPROVE_WITH_EDIT`, `REJECT`, and `UNABLE_TO_DETERMINE`. Every decision MUST identify the reviewed entity and field, reviewer, review time, comment, and optional evidence. A later decision MUST append a new event and MUST NOT erase the earlier opinion.

#### Scenario: Expert requests a correction

- **WHEN** an expert approves a condition only after correcting its operator or expected value
- **THEN** the system creates a new draft revision containing the correction and an append-only review event pointing to the original proposal

#### Scenario: Expert cannot determine a condition

- **WHEN** an expert selects `UNABLE_TO_DETERMINE`
- **THEN** the item remains non-publishable and appears in the next QA/review export with the expert comment intact

### Requirement: Workbook import fails closed with row-level diagnostics

The importer MUST validate the workbook schema version, required sheets and columns, enums, dates, stable IDs, row checksums, cross-sheet references, tree invariants, and duplicate keys before any formal authoring write. Validation SHALL produce a deterministic report containing sheet name, Excel row number, stable row ID, error code, and safe error detail. Any validation error MUST prevent the whole batch from materializing.

#### Scenario: Workbook has one invalid reference

- **WHEN** one condition node references a missing branch or concept while all other rows are valid
- **THEN** the report identifies that sheet and row
- **AND** zero rows from the workbook are written to formal authoring tables

#### Scenario: Workbook round-trip is lossless

- **WHEN** an unmodified generated workbook is validated and imported
- **THEN** all logical IDs, source spans, tree relationships, effective dates, aliases, and components round-trip without semantic change
- **AND** re-export produces the same canonical row payloads apart from permitted batch metadata

### Requirement: Oncology candidate coverage uses the union of all current sources

The eligibility export MUST build its oncology candidate universe from the union of the hospital 2026-06 drug inventory, the 2025 national drug catalog, the 2025 clinical application guideline, current oncology runtime assets, and oncology-related rule YAML knowledge. Every candidate MUST retain source-membership flags and have an explicit included, duplicate, excluded-with-reason, or needs-review disposition. A candidate present in only one source MUST NOT disappear because it lacks a cross-source match.

#### Scenario: Hospital-only oncology candidate is found

- **WHEN** an oncology-related hospital product has no national-catalog or guideline match
- **THEN** it appears in the product/crosswalk or QA sheets with hospital source membership and a review disposition
- **AND** the exporter does not silently discard it

#### Scenario: Existing asset cannot be traced to current files

- **WHEN** a current oncology JSON entry has no deterministic match in the three current source files
- **THEN** it remains visible as an asset-only candidate with provenance-gap QA
- **AND** it cannot enter a new approved release until the gap is resolved

### Requirement: Oncology curated knowledge is visible to expert review

The eligibility workbook SHALL include a structured preservation view for every oncology knowledge atom originating from a curated rule. It MUST show the source rule and field/test reference, atom classification, canonical content, proposed target kind/ID, migration status, and verification evidence. Expert comments or corrections MUST append review events and MUST NOT overwrite the atom's source payload or checksum.

#### Scenario: Curated oncology hint lacks a structured target

- **WHEN** an oncology knowledge atom is discovered but no condition, dictionary, evaluator-policy, review-guidance, or regression target is assigned
- **THEN** the preservation sheet marks it `DISCOVERED` and release-blocking
- **AND** the expert can annotate the intended target without editing the machine source columns

#### Scenario: Curated atom is verified

- **WHEN** the mapped target exists and regression evidence is accepted
- **THEN** the preservation row records `VERIFIED`, target identity, reviewer, and evidence
- **AND** the original rule content remains traceable from the row

### Requirement: Candidate coverage and privacy are explicit

The eligibility export MUST partition every current insurance restriction, guideline indication, oncology source-union candidate, and oncology curated knowledge atom into approved, in-review, rejected, unsupported, excluded-with-reason, or migration-pending coverage as applicable; no source item may disappear silently. Regimen candidates mined from clinical text MUST be exported only as normalized aliases, aggregate frequencies, and non-PHI context categories, without patient IDs, note text, run IDs, or ownership IDs.

#### Scenario: Coverage report is generated

- **WHEN** the current source snapshot is exported
- **THEN** the QA sheet reports source-membership totals and status-partition totals separately for insurance, guideline, hospital/national products, prior assets, and curated oncology atoms
- **AND** every source-specific total reconciles to an explicit disposition

#### Scenario: Mined alias candidate is exported

- **WHEN** a regimen alias is discovered from patient records
- **THEN** the workbook contains only the normalized candidate, aggregate count, review status, and allowed context summary
- **AND** no source patient or raw note can be reconstructed from the workbook
