## ADDED Requirements

### Requirement: Ontology packs are immutable and version-addressed
Each Ontology v0.1 pack SHALL have a stable ontology identifier, explicit version, schema version, and content checksum. Every `OntologyRef` SHALL identify an exact pack version and checksum; validation SHALL NOT silently resolve an unversioned name or floating latest version.

#### Scenario: Exact ontology reference resolves
- **WHEN** an Assertion references an available ontology ID, version, schema version, and matching checksum
- **THEN** deterministic validation uses that exact immutable pack

#### Scenario: Checksum mismatch fails closed
- **WHEN** an OntologyRef version exists but its checksum differs from the loaded pack
- **THEN** validation rejects the reference and does not fall back to another version

### Requirement: Entity types are versioned semantic types
An Ontology v0.1 pack SHALL declare stable entity type identifiers and an optional single or multiple parent `is_a` relationship between types. Every EntityRef SHALL name a type defined by its referenced ontology version.

#### Scenario: Declared subtype is accepted
- **WHEN** an EntityRef uses a type declared in the exact ontology pack
- **THEN** entity type validation succeeds and retains the type identifier and ontology version

#### Scenario: Unknown entity type is rejected
- **WHEN** an EntityRef names a type absent from the exact ontology pack
- **THEN** deterministic validation rejects the EntityRef with an unknown-type issue

### Requirement: Predicate domain and range are enforced
Each predicate definition SHALL declare a stable identifier plus allowed subject domain types and object range types or literal constraints. A Fact SHALL be valid only when its subject and object conform to those constraints, including declared subtype compatibility.

#### Scenario: Patient diagnosis relation is valid
- **WHEN** `has_diagnosis` declares Patient as its domain and Disease as its range and a Fact relates a Patient EntityRef to a Disease EntityRef
- **THEN** predicate validation accepts the Fact

#### Scenario: Medication diagnosis relation is invalid
- **WHEN** the same `has_diagnosis` predicate is used with a Medication subject
- **THEN** predicate validation deterministically rejects the Fact with a domain-mismatch issue

#### Scenario: Literal range constraint is enforced
- **WHEN** a predicate expects a coded or scalar literal and a Fact supplies an incompatible entity or literal type
- **THEN** predicate validation rejects the Fact with a range-mismatch issue

### Requirement: ConceptRef is terminology-aware and self-describing
The ontology contract SHALL represent a coded concept with `system`, `code`, `version`, and `display`; system, code, and version SHALL be required for coded assertions, while display SHALL remain a presentation label that cannot override code identity.

#### Scenario: Same display does not merge different codes
- **WHEN** two ConceptRefs have the same display but different systems, codes, or versions
- **THEN** the validator preserves them as distinct concept identities

#### Scenario: Display change preserves concept identity
- **WHEN** a ConceptRef's display is updated without changing system, code, or version
- **THEN** semantic identity remains unchanged and the display change remains serializable

### Requirement: Minimal is_a hierarchy is finite and deterministic
Ontology v0.1 SHALL support explicit `is_a` edges and deterministic transitive subtype checks. The pack validator SHALL reject self-edges, cycles, missing endpoints, and duplicate contradictory definitions; it SHALL NOT require or imply broader OWL reasoning.

#### Scenario: Transitive subtype satisfies predicate range
- **WHEN** PapillaryThyroidCarcinoma `is_a` ThyroidMalignancy and ThyroidMalignancy `is_a` Disease
- **THEN** PapillaryThyroidCarcinoma deterministically satisfies a predicate range of Disease

#### Scenario: Cyclic hierarchy is rejected
- **WHEN** an ontology pack contains an `is_a` path that returns to its starting type or concept
- **THEN** pack validation rejects the cycle before validating evidence bundles

### Requirement: Ontology validation is deterministic and local
For an exact contract bundle and ontology pack, entity, predicate, concept, and hierarchy validation SHALL produce deterministic structured issues without LLM, network, graph database, vector index, message broker, or external reasoner access.

#### Scenario: Offline validator rejects all semantic violations
- **WHEN** an offline bundle contains unknown types, invalid predicate endpoints, and an unresolved ConceptRef
- **THEN** the validator returns stable issue codes and paths for every violation without external calls

### Requirement: Ontology-derived assertions retain inference lineage
Any Assertion produced through `is_a` SHALL have origin INFERRED and SHALL reference an Inference containing the input Fact or Assertion identifiers, inference kind, exact OntologyRef, traversed hierarchy path, and deterministic reasoner version. The referenced Fact SHALL remain the normalized proposition and SHALL NOT be duplicated merely because it was reached through inference.

#### Scenario: Broader diagnosis records hierarchy path
- **WHEN** an observed papillary thyroid carcinoma Fact yields a thyroid malignancy Fact through `is_a`
- **THEN** the inferred Assertion references the broader diagnosis Fact and records its input, every traversed concept edge, ontology checksum, and reasoner version

#### Scenario: Ontology upgrade does not rewrite prior inference
- **WHEN** a later ontology version changes the relevant hierarchy
- **THEN** prior inferred Assertions retain their original OntologyRef and inference path and are not silently recomputed

### Requirement: Ontology conformance cases cover valid and invalid semantics
The implementation SHALL provide synthetic golden cases for exact version resolution, entity subtyping, predicate domain/range, ConceptRef identity, transitive `is_a`, cycle rejection, and inference provenance.

#### Scenario: Ontology golden suite is reproducible
- **WHEN** the ontology conformance suite runs repeatedly with the same pack and fixtures
- **THEN** accepted bundles, rejected bundles, and structured issue outputs are byte-stable after canonical serialization
