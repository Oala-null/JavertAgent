## ADDED Requirements

### Requirement: Versioned public evidence records
The system SHALL define versioned, serializable records named `SourceArtifact`, `EvidenceItem`, `EntityRef`, `Fact`, `Assertion`, `ProvenanceActivity`, `OntologyRef`, `Inference`, `Conflict`, and `ReviewAction`. Every record SHALL carry a stable identifier, and every serialized contract bundle SHALL declare its Evidence Contract version.

#### Scenario: Complete bundle round-trips without semantic loss
- **WHEN** a valid contract bundle containing all referenced public record types is serialized and deserialized
- **THEN** record identifiers, typed references, semantic values, time values, and version references remain unchanged

#### Scenario: Dangling reference is rejected
- **WHEN** a record references a source, evidence item, entity, fact, assertion, activity, ontology, inference, conflict, or review action that is absent from the bundle
- **THEN** deterministic validation rejects the bundle with the failing record identifier and reference field

### Requirement: Fact and Assertion are separate concerns
The system SHALL represent a `Fact` as a normalized subject-predicate-object proposition and SHALL represent each producer's claim about that proposition as a separate `Assertion`. Unvalidated producer output SHALL enter the boundary as a `CandidateAssertion` and SHALL NOT be treated as a committed `Fact` or `Assertion`.

#### Scenario: Multiple producers assert the same fact
- **WHEN** a structured extractor and a document extractor independently claim the same normalized proposition
- **THEN** the bundle contains one reusable Fact and distinct Assertions with their own evidence and provenance

#### Scenario: Candidate cannot bypass validation
- **WHEN** a producer submits a CandidateAssertion with an invalid reference or semantic type
- **THEN** the candidate is rejected before any Fact or Assertion is committed

### Requirement: Assertion and derivation origins remain explicit
Every Assertion SHALL declare exactly one origin class: `OBSERVED`, `INFERRED`, or `KNOWLEDGE`. An OBSERVED Assertion SHALL be grounded in source evidence, an INFERRED Assertion SHALL reference an `Inference` and its inputs, and a KNOWLEDGE Assertion SHALL reference a versioned knowledge source. Origin belongs to the claim or derivation, so Assertions from different origins about the same proposition SHALL reuse one Fact rather than duplicate it.

#### Scenario: Direct clinical observation is preserved
- **WHEN** a diagnosis is normalized directly from a canonical diagnosis row or anchored document span
- **THEN** the resulting Assertion is classified as OBSERVED, retains the EvidenceItem and SourceArtifact path, and references the normalized Fact

#### Scenario: Derived diagnosis remains distinguishable
- **WHEN** an `is_a` relationship derives a broader diagnosis from an observed diagnosis
- **THEN** the Assertion about the broader Fact is classified as INFERRED and references the input Fact, inference rule, and ontology version

#### Scenario: Knowledge statement is not patient observation
- **WHEN** a terminology or reviewed knowledge pack supplies a concept hierarchy statement
- **THEN** the Assertion is classified as KNOWLEDGE and is not presented as a clinician-observed patient assertion

#### Scenario: Same proposition is not duplicated by origin
- **WHEN** an observed Assertion and an inferred Assertion refer to the same normalized subject-predicate-object proposition
- **THEN** both Assertions reference the same Fact identifier while retaining distinct origin and provenance

### Requirement: No Naked Facts
Every committed Fact SHALL be referenced by at least one Assertion. Every Assertion SHALL reference its Fact, asserting actor or producer, ProvenanceActivity, OntologyRef, assertion value, and clinical-time scope. TRUE/FALSE Assertions SHALL have directly anchored EvidenceItems or a valid derived-from chain whose inputs ultimately reach anchored evidence or a versioned knowledge source. UNKNOWN Assertions SHALL instead record the examined source coverage and a non-empty uncertainty or unavailability reason; they SHALL NOT fabricate supporting or contradicting clinical evidence.

#### Scenario: Fully grounded assertion is accepted
- **WHEN** an Assertion references a valid Fact, producer activity, ontology version, and EvidenceItem whose SourceArtifact has a canonical locator
- **THEN** the No Naked Facts invariant passes

#### Scenario: Bare proposition is rejected
- **WHEN** a Fact has no Assertion, a TRUE/FALSE Assertion has neither grounded evidence nor a valid derived-from chain, or an UNKNOWN Assertion lacks coverage and an uncertainty reason
- **THEN** deterministic validation rejects the bundle and identifies the missing grounding edge

#### Scenario: Scoped unknown is accepted without fabricated evidence
- **WHEN** an UNKNOWN Assertion identifies the source/time coverage that was evaluated, the responsible ProvenanceActivity, and why evidence was insufficient
- **THEN** the invariant accepts the evaluation without requiring a supports or contradicts link for the proposition

### Requirement: UNKNOWN is distinct from FALSE
An Assertion that evaluates a target Fact SHALL preserve `TRUE`, `FALSE`, and `UNKNOWN` as distinct values and SHALL declare the source and clinical-time coverage scope of that evaluation. Missing, unavailable, unparsed, out-of-scope, or insufficient evidence SHALL produce or preserve UNKNOWN and SHALL NOT be coerced to FALSE; FALSE SHALL require explicit negative evidence or a versioned deterministic rule that establishes negation. UNKNOWN SHALL remain an evaluation state and SHALL NOT create an `Unknown` entity, concept, or diagnosis Fact.

#### Scenario: Missing diagnosis evidence stays unknown
- **WHEN** the available sources do not mention whether a diagnosis is present
- **THEN** the scoped assertion value is UNKNOWN, the evaluated diagnosis Fact remains the target, and no `Patient has_diagnosis Unknown` Fact is created

#### Scenario: Explicit negation may establish false
- **WHEN** an anchored source explicitly negates a diagnosis and the assertion records that evidence
- **THEN** the assertion may carry FALSE while retaining the negating EvidenceItem and provenance

### Requirement: Clinical and system time are separate
The contract SHALL represent clinical valid time separately from system record time. Every Assertion and ProvenanceActivity SHALL record its system time, and SHALL represent clinical valid time as a point, interval, or explicit unknown without deriving it from ingestion or creation time.

#### Scenario: Late-arriving record preserves both times
- **WHEN** a diagnosis valid during an earlier encounter is ingested at a later time
- **THEN** the clinical valid time records the encounter interval and the system time records the later ingestion or assertion event

#### Scenario: Unknown clinical time is not fabricated
- **WHEN** a source has a known ingestion time but no defensible clinical effective time
- **THEN** clinical valid time is explicitly unknown and is not copied from the ingestion timestamp

### Requirement: Evidence and assertion relationships are typed
The contract SHALL support typed `supports`, `contradicts`, `derived_from`, `supersedes`, and `retracts` relations. `supports` and `contradicts` SHALL connect EvidenceItems to Assertions; `derived_from` SHALL connect an Inference to its input Facts or Assertions; `supersedes` and `retracts` SHALL identify the prior Assertion and SHALL preserve the prior record.

#### Scenario: Evidence conflict remains inspectable
- **WHEN** one EvidenceItem supports an Assertion and another EvidenceItem contradicts it
- **THEN** both typed relationships remain present and neither evidence item is overwritten

#### Scenario: Retraction is append-only
- **WHEN** a producer retracts a prior Assertion
- **THEN** a new retraction relationship identifies the prior Assertion while the prior content and provenance remain queryable

### Requirement: Canonical ingestion lineage is mandatory
Every SourceArtifact used to ground patient evidence SHALL identify an immutable artifact version and content checksum. Every EvidenceItem SHALL locate content relative to that version: structured evidence SHALL include canonical row ordinal, a deterministic row fingerprint computed from the declared canonical row serialization, field, and value checksum, while document evidence SHALL include canonical row ordinal, the deterministic row fingerprint, document field, and an optional section plus zero-based end-exclusive character span. The contract SHALL allow optional upstream HIS database, table, original primary key, and column locators, but SHALL make no claim that upstream source data is always reachable.

#### Scenario: Structured evidence stops safely at canonical row
- **WHEN** a normalized diagnosis has an immutable SourceArtifact version, canonical row ordinal, deterministic row fingerprint, field, and matching checksum but the upstream HIS primary key is unavailable
- **THEN** lineage validation succeeds at the canonical boundary and records that upstream lineage is unavailable

#### Scenario: Available upstream locator is retained
- **WHEN** ingestion supplies an approved upstream database, table, primary key, and column locator
- **THEN** the lineage retains it as an additional, non-authoritative hop without replacing the canonical locator

#### Scenario: Document span remains traceable
- **WHEN** an EvidenceItem is extracted from a clinical document
- **THEN** its locator identifies the immutable document artifact version, canonical row ordinal, deterministic row fingerprint and field, content checksum, and may include document ID, section, start character, and end-exclusive end character

#### Scenario: Span or checksum mismatch is rejected
- **WHEN** a locator exceeds the versioned document bounds or its value or span checksum differs from the referenced SourceArtifact content
- **THEN** deterministic validation rejects the EvidenceItem instead of falling back to fuzzy text matching

### Requirement: Conflicts and review actions are first-class records
The contract SHALL represent unresolved or resolved disagreement with a `Conflict` that references the participating Assertions. Human disposition SHALL be appended as a `ReviewAction` with reviewer identity appropriate for audit, decision, system time, rationale, and affected record identifiers; it SHALL NOT delete the conflict or its source assertions.

#### Scenario: Opposing assertions open a conflict
- **WHEN** valid TRUE and FALSE Assertions concern the same Fact and applicable clinical time
- **THEN** the bundle can record an unresolved Conflict containing both assertion identifiers

#### Scenario: Reviewer resolves without rewriting history
- **WHEN** an authorized reviewer resolves a Conflict
- **THEN** a ReviewAction records the decision and rationale while the original Conflict, Assertions, EvidenceItems, and provenance remain intact

### Requirement: Contract validation is deterministic and atomic
Schema, reference, invariant, and ontology validation SHALL be deterministic for identical inputs. A validation failure SHALL return structured issue codes and record paths and SHALL prevent partial acceptance of the submitted bundle.

#### Scenario: Same invalid bundle yields same issues
- **WHEN** the same invalid bundle is validated repeatedly under the same contract and ontology versions
- **THEN** it produces the same ordered validation issues without network, model, or database calls

#### Scenario: One invalid record prevents partial commit
- **WHEN** a submitted bundle contains valid records plus one record that violates No Naked Facts
- **THEN** the entire submission remains uncommitted and validation identifies the invalid record

### Requirement: Existing oncology contract has an explicit conformance adapter
The implementation SHALL provide and test an adapter that associates existing oncology `EvidenceAnchor` and `NormalizedFact` records with public evidence and fact references, and associates existing criterion/proof nodes, source/release versions, and review events with corresponding public provenance references. Existing `eligibility_json` and its proof tree SHALL remain the authoritative oncology payload; the adapter SHALL NOT require whole-payload conversion or round-trip reconstruction and SHALL NOT change oncology runtime types or verdict projection. `UNKNOWN` and `CONFLICT` SHALL remain distinct in adapter references.

#### Scenario: Oncology unknown remains unknown
- **WHEN** an oncology criterion in state UNKNOWN is associated with public evidence references
- **THEN** the authoritative criterion remains UNKNOWN and is not converted to NOT_SATISFIED, FALSE, or CLEAN

#### Scenario: Oncology proof provenance remains traceable
- **WHEN** an approved synthetic oncology evaluation has source versions and a proof tree
- **THEN** the adapter links its evidence anchors, normalized facts, proof nodes, evaluator versions, and source versions to public record identifiers while the original payload and legacy verdict projection remain unchanged

### Requirement: Golden conformance cases are synthetic and de-identified
The contract SHALL ship with deterministic golden cases that cover structured and document evidence, multi-evidence support, explicit contradiction, UNKNOWN handling, ontology inference lineage, illegal relation rejection, retraction, and oncology mapping. Fixtures and expected outputs SHALL use explicitly marked synthetic semantic identifiers and synthetic clinical text, and SHALL NOT contain real patient identifiers, original or real-patient clinical text, credentials, or unsalted ownership identifiers.

#### Scenario: Golden suite covers required invariants
- **WHEN** the contract conformance suite runs offline
- **THEN** every required evidence, time, relation, lineage, conflict, ontology, and oncology mapping behavior has at least one passing golden case

#### Scenario: Fixture privacy scan detects prohibited data
- **WHEN** a fixture includes a value matching the repository's prohibited patient or credential patterns
- **THEN** the conformance gate fails and identifies the fixture path without echoing protected content

### Requirement: Contract definition has no runtime side effects
Loading, constructing, serializing, or validating the public contract SHALL NOT invoke the audit runner, Router, LLM, production database, SQLite/SQL Server result store, external network, or current verdict projection. Adoption by runtime producers and stores SHALL occur in separate changes.

#### Scenario: Contract suite runs in isolation
- **WHEN** the contract and ontology conformance tests run in an environment without network or database access
- **THEN** they complete without changing AuditResult, verdict, Router index, gate behavior, or persisted audit rows
