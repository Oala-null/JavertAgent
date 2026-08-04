## ADDED Requirements

### Requirement: Versioned regimen authoring model

The system SHALL maintain regimen revisions independently from eligibility-rule revisions. Each regimen revision MUST have a stable logical regimen ID, revision ID, canonical name, source references, cancer contexts, aliases, component set, effective dates, lifecycle status, and review metadata.

#### Scenario: Existing approved regimen is exported

- **WHEN** an existing approved regimen such as R-CHOP is included in the expert workbook
- **THEN** its canonical name, aliases, cancer contexts, generic component concepts, source references, and current revision identity are preserved

#### Scenario: New candidate regimen is imported

- **WHEN** an expert reviews a mined candidate and supplies canonical name, context, components, and evidence
- **THEN** a new draft regimen revision is created without modifying an existing released regimen

### Requirement: Regimen aliases remain context-aware and ambiguity-safe

Aliases SHALL retain original text, normalized text, alias type, language, source, aggregate frequency when available, and review status. If one normalized alias maps to multiple approved regimens and cancer context cannot disambiguate it, the alias MUST remain ambiguous and MUST NOT infer components automatically.

#### Scenario: Formatting variants share one regimen

- **WHEN** experts approve hyphen, spacing, Unicode, or case variants of the same regimen
- **THEN** all variants link to one regimen revision while preserving their original strings

#### Scenario: Short alias is ambiguous

- **WHEN** a short alias maps to different regimens in overlapping or unknown cancer contexts
- **THEN** publish validation marks it ambiguous
- **AND** runtime component inference is blocked until context or alias mapping is refined

### Requirement: First phase publishes composition only

Each regimen component SHALL reference an approved drug concept or drug class and record token, component role, required status, sibling order, and source reference. First-phase publication MUST use only canonical regimen identity, aliases, contexts, and components. Dose, unit, dose basis, route, administration days, cycle length, maximum cycles, treatment phase, and sequence fields MUST exist in the schema for future revisions but MUST NOT be required or used for first-phase inference.

#### Scenario: Composition-only regimen is approved

- **WHEN** a regimen has reviewed aliases, context, and generic drug components but no schedule details
- **THEN** it can be approved and published in the first-phase regimen KB

#### Scenario: Reserved schedule fields are filled accidentally

- **WHEN** a first-phase workbook contains values in reserved schedule fields without the future schedule schema being activated
- **THEN** validation reports them as non-publishing informational data or rejects unsupported structured use
- **AND** runtime inference ignores them

### Requirement: Component targets preserve exactness and optionality

A component target MUST distinguish an exact drug concept from a drug class, and required status MUST distinguish `REQUIRED`, `OPTIONAL`, and `WITH_OR_WITHOUT`. The system MUST NOT choose an arbitrary product for a drug-class component or convert an optional component into a required component.

#### Scenario: Regimen contains a platinum-class component

- **WHEN** the reviewed regimen source specifies a platinum class without naming one agent
- **THEN** the component references the platinum class and remains class-level

#### Scenario: Optional regimen component is resolved

- **WHEN** a regimen contains an optional component
- **THEN** absence of that component does not by itself make an otherwise matching regimen conflict

### Requirement: Only approved unambiguous regimen revisions are published

The regimen publisher MUST exclude drafts, changes-requested, rejected, unable-to-determine, unsupported, source-incomplete, concept-incomplete, or unresolved-ambiguous regimen revisions. The publication report SHALL list excluded candidates and reasons instead of silently dropping them.

#### Scenario: Regimen has an unknown component

- **WHEN** an expert-approved name still contains a component token that is not linked to a drug concept or class
- **THEN** the regimen remains blocked from release and appears in the QA report

#### Scenario: Approved regimen is published

- **WHEN** a regimen revision has approved source, context, aliases, and all components with no ambiguity
- **THEN** deterministic compilation includes its revision ID and source metadata in the runtime regimen asset
