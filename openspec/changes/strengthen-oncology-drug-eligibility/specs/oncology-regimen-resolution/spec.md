## ADDED Requirements

### Requirement: Versioned oncology regimen knowledge base

The system SHALL provide an offline, versioned oncology regimen knowledge base independent from the drug eligibility-rule knowledge base. Each regimen entry MUST have a stable `regimen_id`, canonical name, normalized aliases, applicable cancer contexts, component drugs, source references, and review status. Each component drug MUST identify its generic name; when English generic names, trade names, regimen tokens, or insurance charge codes are known, the entry MUST retain them as typed aliases. Only entries with an approved review status MUST participate in automatic component inference; an unreviewed or ambiguous entry MUST be surfaced for review rather than silently inferred.

#### Scenario: Pola-R-GemOx resolves to its four component drugs

- **WHEN** an approved DLBCL regimen entry identifies `Pola-R-GemOx` and its case/spacing/hyphen variants
- **THEN** the entry resolves to polatuzumab vedotin (`Pola`, `维泊妥珠单抗`, `优罗华`), rituximab (`R`), gemcitabine (`Gem`), and oxaliplatin (`Ox`), with the applicable cancer context and source references preserved

#### Scenario: Charge code and trade name resolve to the generic component

- **WHEN** the same treatment event contains either trade name `优罗华` or charge code `XL01FXW129B001010181735`
- **THEN** the resolver identifies generic drug `注射用维泊妥珠单抗` and records which alias or code produced the match

#### Scenario: Unreviewed regimen is not used for automatic inference

- **WHEN** a candidate regimen alias has `review_status` other than approved
- **THEN** the resolver returns a review-required candidate and MUST NOT assert its component drugs as established treatment facts

### Requirement: Context-aware deterministic alias resolution

The resolver SHALL normalize Unicode, letter case, whitespace, and common hyphen variants before matching regimen aliases. It MUST use the longest approved alias match and retain the matched text and source anchor. Cancer context MUST remain part of the resolution: a supplied compatible context SHALL be recorded as matched, a supplied incompatible context SHALL produce a context conflict, and an absent context SHALL remain `UNKNOWN` rather than being invented. If an alias maps to multiple approved regimens and cancer context does not disambiguate them, the result MUST be `AMBIGUOUS` and MUST NOT infer components.

#### Scenario: Formatting variants resolve identically

- **WHEN** otherwise identical DLBCL notes contain `Pola-R-GemOx`, `pola-R-Gemox`, `POLA R GEMOX`, or equivalent Unicode-hyphen variants
- **THEN** all variants resolve to the same approved regimen and component set while preserving their original matched text

#### Scenario: Missing cancer context does not become a fabricated diagnosis

- **WHEN** a unique approved regimen alias is found but no cancer diagnosis context is available
- **THEN** the regimen and components are returned with `cancer_context_status=UNKNOWN`, and the regimen match MUST NOT itself be used as proof of a cancer diagnosis

#### Scenario: Conflicting cancer context is explicit

- **WHEN** a regimen alias is found but the supplied cancer context is incompatible with every approved context for that regimen
- **THEN** the resolver records a context conflict and MUST NOT present the regimen-derived components as unqualified indication evidence

### Requirement: Explicit medication evidence takes precedence

For a single treatment event, an explicit generic name, trade name, or explicitly enumerated drug list in the clinical text SHALL take precedence over component names inferred only from a regimen alias. Regimen inference SHALL fill components not addressed by the explicit evidence when the approved mapping is unambiguous, but MUST NOT overwrite an explicitly identified drug. When explicit evidence directly contradicts the approved regimen mapping, the resolver MUST retain both sources and return a conflict instead of silently selecting one.

#### Scenario: Explicit Pola-R-GemOx component list confirms the regimen

- **WHEN** a note records `Pola-R-GemOx` and explicitly lists `利妥昔单抗 + 优罗华 + 吉西他滨 + 奥沙利铂`
- **THEN** the four explicit medication matches are returned as the primary component evidence and the regimen mapping is returned as corroborating evidence

#### Scenario: Explicit list and regimen mapping disagree

- **WHEN** a treatment event names a regimen but explicitly identifies a drug that contradicts the approved component mapped to the same regimen token
- **THEN** the resolver returns both anchored facts with `conflict=true` and MUST NOT replace the explicit drug with the inferred component

### Requirement: Pola-R-GemOx and R-GemOx remain distinct regimens

The resolver MUST treat `Pola-R-GemOx` and `R-GemOx` as distinct aliases and component sets. The presence of `R-GemOx` without a `Pola` token, explicit polatuzumab name or trade name, or matching polatuzumab code MUST NOT imply use of polatuzumab vedotin.

#### Scenario: Pola-R-GemOx includes polatuzumab

- **WHEN** an approved `Pola-R-GemOx` alias is identified in a compatible DLBCL context
- **THEN** polatuzumab vedotin is included among the regimen-derived components

#### Scenario: R-GemOx does not include polatuzumab

- **WHEN** a note contains only `R-GemOx` and no explicit polatuzumab name, trade name, or charge code
- **THEN** the resolver returns rituximab, gemcitabine, and oxaliplatin, but polatuzumab vedotin MUST NOT be inferred

### Requirement: Treatment event status is distinct from regimen identity

Each resolved treatment event SHALL carry an `event_status` of `PLANNED`, `ADMINISTERED`, `HISTORICAL`, or `UNKNOWN`, determined from the anchored clinical wording and document context. A planned regimen MUST NOT count as administered medication or prior-treatment evidence. A historical regimen SHALL be eligible for consideration by a prior-treatment criterion but MUST NOT be presented as current administration. If the text cannot distinguish these statuses, `event_status` MUST remain `UNKNOWN` and the event MUST be routed for review.

#### Scenario: Proposed regimen remains planned

- **WHEN** a note says `拟行Pola-R-GemOx方案` without an administration, completion, or billing anchor proving use
- **THEN** the event is `PLANNED` and MUST NOT satisfy a current-use or prior-treatment eligibility condition

#### Scenario: Completed regimen is administered

- **WHEN** a discharge record states that the patient received or completed a named regimen during the encounter
- **THEN** the event is `ADMINISTERED` and carries the treatment-date and source-document anchors

#### Scenario: Prior regimen remains historical

- **WHEN** a history section says the patient previously received a named regimen before the current encounter
- **THEN** the event is `HISTORICAL`, is available to a prior-treatment criterion, and MUST NOT be labeled as current administration

### Requirement: Cycle number is never inferred as line of therapy

The resolver SHALL output `cycle_no` and `line_of_therapy` as independent fields. When a phrase such as `第4次`, `第四周期`, or `C4` modifies the resolved regimen event, the resolver SHALL set `cycle_no=4`, but MUST NOT establish fourth-line therapy, relapse, refractory disease, maintenance therapy, or any other line-of-therapy fact. `line_of_therapy` MUST remain `UNKNOWN` unless separate anchored evidence explicitly establishes it.

#### Scenario: Pola cycle-conflict fourth administration does not become fourth-line treatment

- **WHEN** the synthetic Pola cycle-conflict golden fixture records `第四次Pola-R-GemOx方案化疗` and no separate text establishes a treatment line
- **THEN** the resolver identifies the Pola-R-GemOx components, sets `cycle_no=4`, leaves `line_of_therapy=UNKNOWN`, and MUST NOT infer relapse or refractory disease from the ordinal alone

### Requirement: Temporal conflicts are preserved

The resolver SHALL preserve all available encounter, note, and treatment dates used to resolve a regimen event. When clinically relevant dates are mutually inconsistent, the result MUST include a `temporal_conflict` with both anchored values and MUST NOT silently choose the date that better supports eligibility.

#### Scenario: Pola cycle-conflict inconsistent treatment year is flagged

- **WHEN** the synthetic Pola cycle-conflict golden fixture has a 2026 encounter or fee anchor while the linked treatment narrative dates the same event to 2025
- **THEN** the resolved event includes both dates and `temporal_conflict=true`, and downstream eligibility evaluation is informed that treatment chronology requires review
