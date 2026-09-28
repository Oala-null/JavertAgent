## ADDED Requirements

### Requirement: Contextual pathology biomarker knowledge base
The system SHALL provide a structured pathology biomarker knowledge base whose entries identify a canonical marker concept, typed aliases, cancer type, indication or policy context, specimen constraints where applicable, testing method, scoring system, accepted result values or threshold, staining localization or percentage criteria where applicable, effective dates, review status, and immutable source references. Thresholds MUST be selected by the complete applicable context and MUST NOT be stored or applied as a universal property of a marker name.

#### Scenario: Urothelial HER2 policy is represented contextually
- **WHEN** the knowledge base contains the HER2 condition used by the disitamab vedotin urothelial carcinoma restriction
- **THEN** the entry ties IHC `2+` and `3+` acceptance to that cancer, method, indication, source, and effective version rather than declaring every HER2 `2+` result positive for every cancer and purpose

#### Scenario: Missing contextual rule does not fall back globally
- **WHEN** a HER2 result is observed for a cancer, specimen, method, or policy context for which no approved interpretation entry exists
- **THEN** normalization preserves the observed result but returns an unresolved interpretation requiring review and MUST NOT reuse a threshold from another cancer or indication

### Requirement: Marker aliases normalize without collapsing biological layers
The normalizer SHALL map approved textual aliases including `HER2`, `HER-2`, `HER2/neu`, `c-erbB-2`, and `CerbB2` to the canonical HER2 concept while retaining the exact observed text. Alias matching MUST be Unicode-, case-, whitespace-, and punctuation-tolerant according to versioned normalization rules. The system MUST keep protein expression, gene amplification, and sequence variation as distinct observation types: `ERBB2` mutation or amplification MUST NOT by itself be treated as HER2 IHC overexpression, and an untyped occurrence of `HER2 positive` MUST NOT be assigned an IHC score.

#### Scenario: CerbB2 alias resolves to HER2 protein expression
- **WHEN** an immunohistochemistry report contains `CerbB2(1+)`
- **THEN** the normalizer emits canonical marker `HER2`, method `IHC`, score `1+`, retains `CerbB2(1+)` as the observed expression, and attaches the source anchor

#### Scenario: ERBB2 mutation does not become HER2 IHC positivity
- **WHEN** an NGS report contains an `ERBB2` sequence variant but no HER2 IHC result
- **THEN** the normalizer emits a molecular-variant observation and MUST NOT synthesize an IHC score, protein-overexpression finding, or satisfied HER2-IHC eligibility leaf

#### Scenario: Untyped positive wording remains method-ambiguous
- **WHEN** a note states only `HER2阳性` without a linked pathology report, method, score, amplification result, or validated structured field
- **THEN** the canonical marker may be recognized, but method and score remain unknown and a score-dependent eligibility criterion evaluates `UNKNOWN`

### Requirement: Method-aware result normalization
The system MUST normalize pathology observations using method-specific grammars and value domains. IHC observations SHALL preserve categorical score, staining intensity, localization, percentage, and completeness when present; ISH/FISH observations SHALL preserve amplification ratio, copy number, and assay interpretation when present; molecular observations SHALL preserve variant type and assay context. A value valid for one method MUST NOT be coerced into another method's value domain.

#### Scenario: IHC score is not treated as a FISH ratio
- **WHEN** a report contains HER2 `2+` in an IHC panel
- **THEN** the result is stored as an IHC categorical score and no FISH amplification status is inferred

#### Scenario: FISH amplification is not assigned an IHC score
- **WHEN** a report contains HER2/ERBB2 amplification by FISH without an IHC result
- **THEN** the amplification result is preserved as FISH evidence and the system MUST NOT manufacture IHC `2+` or `3+`

### Requirement: Cancer- and policy-specific threshold evaluation
The normalizer SHALL evaluate a biomarker observation only against an approved threshold whose marker, cancer type, indication or payment policy, method, specimen constraints, scoring system, and effective date match the requested criterion. It MUST emit the normalized observation, matched threshold ID and version, comparison outcome, and any mismatch reason. For the approved disitamab vedotin urothelial carcinoma rule, HER2 IHC `2+` or `3+` SHALL satisfy the overexpression criterion, while IHC `0` or `1+` SHALL not satisfy it; no additional ISH/FISH positivity SHALL be required unless the effective source version explicitly requires it.

#### Scenario: Urothelial HER2 IHC 1+ fails overexpression
- **WHEN** a urothelial carcinoma observation is HER2 IHC `1+` and the applicable threshold accepts only `2+` or `3+`
- **THEN** the threshold comparison is `NOT_SATISFIED`, with observed value `1+` and the matched threshold version recorded

#### Scenario: Urothelial HER2 IHC 2+ satisfies without invented FISH requirement
- **WHEN** a urothelial carcinoma observation is HER2 IHC `2+` and the effective payment source accepts `2+` or `3+` without requiring ISH/FISH confirmation
- **THEN** the threshold comparison is `SATISFIED` and the evaluator MUST NOT downgrade it because FISH was absent

#### Scenario: Same score in another cancer requires its own threshold
- **WHEN** HER2 IHC `2+` is observed in a cancer context governed by a different scoring or confirmatory-testing policy
- **THEN** the urothelial threshold MUST NOT be reused, and the result is evaluated only by the approved context-specific policy or remains unresolved

### Requirement: Temporal alignment and conflicting pathology evidence
Every normalized pathology observation SHALL retain specimen collection date, report date, evidence source, and applicable service or administration date when available. Evidence used to prove eligibility MUST satisfy the configured temporal relation to the audited service. A result obtained only after the audited administration MUST NOT retroactively prove eligibility. Multiple temporally applicable results MUST all be retained; if they yield incompatible criterion outcomes and no approved specimen or chronology precedence policy resolves them, the biomarker criterion MUST be `CONFLICT` rather than selecting the latest result silently.

#### Scenario: Post-administration result cannot retroactively satisfy eligibility
- **WHEN** the only qualifying HER2 result was collected after the audited drug administration
- **THEN** it is labeled post-service evidence, the at-service HER2 criterion remains `UNKNOWN`, and the later result is not used to convert the criterion to `SATISFIED`

#### Scenario: Conflicting pre-service IHC results remain conflict
- **WHEN** two temporally applicable pre-service specimens yield HER2 IHC `1+` and `3+` and no approved policy selects one specimen over the other
- **THEN** both results are retained, the marker criterion is `CONFLICT`, and the system MUST NOT choose `3+` or the latest report merely to obtain a decisive result

#### Scenario: Impossible pathology chronology is surfaced
- **WHEN** recorded report or specimen dates are internally inconsistent with the audited service and the inconsistency cannot be resolved from source metadata
- **THEN** the normalizer records a temporal-conflict reason and routes the criterion for review rather than treating either date as authoritative

### Requirement: Knowledge-source versioning and review control
Each biomarker concept, alias set, interpretation threshold, and precedence policy SHALL carry a schema version, content version, review status, and immutable source metadata including source identifier, title, publication or effective date, retrieval date, and checksum or equivalent revision identifier. Only schema-valid, effective, approved entries MAY drive an automatic eligibility state. Unapproved candidate aliases or thresholds discovered from patient notes MUST remain review candidates and MUST NOT become authoritative through frequency alone.

#### Scenario: Candidate alias requires approval
- **WHEN** corpus mining discovers a previously unseen spelling that frequently co-occurs with HER2
- **THEN** the spelling is stored as an unapproved alias candidate, automatic normalization does not rely on it, and promotion requires recorded expert review

#### Scenario: Historical threshold version is reproducible
- **WHEN** an audit is rerun for a service date governed by an older approved biomarker threshold
- **THEN** the normalizer selects the version effective on that service date and the proof records the exact source and knowledge-base versions used

### Requirement: Urothelial HER2-low golden pathology regression
The system MUST include a minimized, de-identified synthetic pathology fixture containing urothelial carcinoma context and the observed text `CerbB2(1+)`. The fixture MUST verify alias normalization, IHC method and score extraction, context-specific threshold selection, evidence anchoring, and the resulting `NOT_SATISFIED` HER2-overexpression state. The regression MUST fail if the alias is missed, the result is treated as method-unknown, or `1+` is accepted under the `2+`/`3+` urothelial threshold.

#### Scenario: Urothelial HER2-low CerbB2 1+ is deterministically non-qualifying
- **WHEN** the synthetic urothelial HER2-low golden pathology fixture is normalized and compared with the approved disitamab vedotin urothelial HER2 threshold
- **THEN** the output records canonical marker `HER2`, observed alias `CerbB2`, method `IHC`, score `1+`, threshold `{2+, 3+}`, state `NOT_SATISFIED`, source versions, and the originating evidence anchor
