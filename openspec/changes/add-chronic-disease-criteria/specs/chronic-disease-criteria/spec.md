## ADDED Requirements

### Requirement: The 2025 standard is the sole current recognition authority
The system SHALL treat the reviewed 2025 standard forwarded under `黑医保规〔2025〕12号` and `黑市医保发〔2025〕50号` as the sole executable policy for new recognition decisions. The `黑市医保发〔2020〕37号` Heihe standard MUST be stored in a separate historical policy version and MUST NOT contribute leaves, thresholds, operators, definitions, or defaults to a 2025 condition tree. Every tree and evaluation request MUST identify its policy version explicitly.

#### Scenario: A new recognition is evaluated
- **WHEN** a current outpatient chronic-disease recognition request is created
- **THEN** the system selects the reviewed 2025 policy version and MUST NOT evaluate the request against the 2020 historical version

#### Scenario: A 2025 clause is incomplete
- **WHEN** a 2025 disease clause omits or ambiguously connects a required clinical condition that appears more explicitly in the 2020 document
- **THEN** the system preserves the 2025 ambiguity and MUST NOT copy the 2020 wording into the 2025 executable tree

#### Scenario: A reviewer inspects historical differences
- **WHEN** a reviewer requests the 2020-to-2025 version comparison for a disease
- **THEN** the system presents the two independently sourced versions and their differences without merging either version into the other

#### Scenario: An explicit historical reconstruction is requested
- **WHEN** an authorized historical reconstruction explicitly selects the 2020 policy version
- **THEN** the system uses only the applicable 2020 tree, labels the result historical, and MUST NOT combine any 2025 node or threshold into that evaluation

### Requirement: The 2025 catalog contains exactly twenty independently versioned diseases
The 2025 criteria asset SHALL contain exactly twenty disease entries with stable rule identifiers `CD01` through `CD20`, one canonical disease identity per identifier, and exactly one root condition tree per entry. Disease identifiers MUST remain stable across revisions; a wording or logic change MUST create a new entry revision rather than renumbering another disease. Asset validation MUST reject missing, duplicate, out-of-range, or multiply rooted entries.

#### Scenario: A complete catalog is validated
- **WHEN** the asset contains one unique disease entry and one root tree for every identifier from `CD01` through `CD20`
- **THEN** the catalog completeness gate passes and reports all twenty disease entries as accounted for

#### Scenario: A disease entry is missing or duplicated
- **WHEN** an asset omits `CD12`, contains two `CD12` entries, or maps one identifier to multiple canonical diseases
- **THEN** asset validation fails before release and identifies the missing or conflicting identifier

### Requirement: Chronic-disease standards compile to explicit condition trees
Each disease entry SHALL encode its recognition standard as a machine-evaluable condition tree rather than executable free text. Every tree MUST have a stable root ID; internal nodes MUST declare `AND`, `OR`, or `AT_LEAST_N`; `AT_LEAST_N` nodes MUST declare a valid integer threshold; and every leaf MUST have a stable criterion ID, fact type, comparison or categorical expectation, evidence domain, and applicable unit, repetition, and temporal policies. The original clause text MUST remain attached only as traceability evidence and MUST NOT supply runtime semantics that are absent from the compiled fields.

#### Scenario: A repeated renal-function criterion is compiled
- **WHEN** a source clause requires at least two qualifying renal-function measurements separated by at least three months
- **THEN** the tree contains an explicit measurement leaf with its threshold, required observation count, minimum separation, accepted units, and date basis instead of leaving those semantics in free text

#### Scenario: A threshold requires two of three blood-count findings
- **WHEN** a source clause requires any two of three enumerated blood-count abnormalities
- **THEN** the tree represents the clause as an `AT_LEAST_N` node with `N=2` and three separately identifiable child leaves

#### Scenario: A node type is unsupported
- **WHEN** a compiled entry contains an operator or leaf policy that is not declared by the criteria schema
- **THEN** schema validation fails and the entry MUST NOT become eligible for automatic recognition

### Requirement: Every executable criterion is anchored to immutable source evidence
Every disease entry, internal node, and leaf SHALL resolve to at least one immutable source fragment. Each source fragment MUST record a stable source ID, document title and document number, policy version, publication or effective date when present, PDF physical page number, exact reviewed excerpt, extraction method, and fragment checksum; the source document MUST also record its file checksum. Page references MUST use the physical PDF page index and MUST remain distinct from any printed page number captured from the page image.

#### Scenario: A reviewer opens a leaf source
- **WHEN** a reviewer inspects any executable leaf
- **THEN** the system can identify the exact source document, physical PDF page, reviewed excerpt, document checksum, and fragment checksum from which that leaf was compiled

#### Scenario: Source bytes change after review
- **WHEN** the current PDF or reviewed source fragment no longer matches its recorded checksum
- **THEN** source validation fails, the affected entry is removed from automatic eligibility, and the mismatch is reported for re-extraction and review

#### Scenario: A page reference is absent
- **WHEN** an executable node cannot be traced to a physical PDF page and reviewed source excerpt
- **THEN** the source-completeness gate fails for that disease entry

### Requirement: Schema, source, and expert review gates control automatic use
Only a criteria revision that is schema-valid, source-complete, checksum-valid, within its declared applicability, and explicitly marked with the configured expert-approved review status SHALL be eligible for automatic qualification. Generated, draft, in-review, changes-requested, rejected, expired, or superseded revisions MUST NOT auto-decide a patient's qualification. Approval MUST identify the reviewed revision and reviewer, and any subsequent semantic or source change MUST create a new revision requiring a new approval.

#### Scenario: A generated tree has not been approved
- **WHEN** a generated disease tree passes schema validation but its review status is not expert-approved
- **THEN** the system excludes it from automatic qualification and reports that expert review is required

#### Scenario: An approved tree is edited
- **WHEN** an operator changes an operator, threshold, unit policy, time policy, source excerpt, or source checksum of an approved revision
- **THEN** the system requires a new revision and MUST NOT preserve the prior approval on the changed content

#### Scenario: All release gates pass
- **WHEN** a disease revision is schema-valid, source-complete, checksum-valid, applicable, and expert-approved with no execution block
- **THEN** the revision becomes eligible for deterministic evaluation under its exact recorded version

### Requirement: Category C ambiguities remain explicitly blocked
The 2025 entries for aplastic anemia, hypertension, rheumatoid arthritis, and chronic viral hepatitis SHALL retain all source-backed leaves and shadow evidence extraction, but their automatic qualification root MUST have execution status `BLOCKED` until the identified ambiguity has a written expert resolution incorporated into a newly reviewed revision. Each blocked entry MUST record a structured block reason, the unresolved question, the affected nodes, and the required approver. A blocked 2025 entry MUST NOT use the 2020 standard, an LLM interpretation, or a local default to close the ambiguity.

#### Scenario: Shadow evidence exists for a blocked disease
- **WHEN** patient evidence satisfies every currently compiled leaf for a Category C disease but the root remains `BLOCKED`
- **THEN** the system retains the leaf-level shadow proof but MUST NOT issue an automatic qualified or not-qualified decision

#### Scenario: A blocked ambiguity is resolved in writing
- **WHEN** the designated expert supplies a written interpretation for a Category C ambiguity
- **THEN** the system creates or updates a new criteria revision, preserves the resolution as source-linked review evidence, and keeps the root blocked until that revision passes all approval gates

#### Scenario: A fallback attempts to use the 2020 wording
- **WHEN** evaluation of a blocked 2025 disease would become decidable only by importing a 2020 condition or threshold
- **THEN** the fallback is rejected and the 2025 entry remains `BLOCKED`

### Requirement: Criteria versions are reproducible and non-destructive
Every compiled criteria asset SHALL record a schema version, policy version, release or revision ID, ordered disease revision IDs, source-manifest checksum, and asset checksum. Recompiling the same approved inputs MUST produce byte-identical canonical output. Superseding or retiring a revision MUST preserve prior assets and references so that a historical result can be reconstructed without applying current knowledge retroactively.

#### Scenario: The same approved inputs are compiled twice
- **WHEN** the identical reviewed source manifest and criteria revisions are compiled in clean environments
- **THEN** the canonical assets and their checksums are byte-identical

#### Scenario: A criterion is revised after prior evaluations
- **WHEN** a newly approved revision supersedes an earlier disease revision
- **THEN** prior evaluations remain linked to the earlier immutable asset and MUST NOT be silently re-evaluated with the new revision
