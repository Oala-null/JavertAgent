## 1. Public contract models

- [x] 1.1 Add an isolated `javert.evidence` package with strict Pydantic v2 enums/value objects for stable IDs, `ConceptRef`, clinical valid time, system time, coverage scope, typed literals, canonical locators, and structured validation issues; do not add dependencies or runtime imports.
- [x] 1.2 Implement the versioned `SourceArtifact`, `EvidenceItem`, `EntityRef`, proposition-only `Fact`, `Assertion`, `ProvenanceActivity`, `OntologyRef`, `Inference`, `Conflict`, and `ReviewAction` models with explicit OBSERVED/INFERRED/KNOWLEDGE assertion origin and TRUE/FALSE/UNKNOWN scoped evaluation state.
- [x] 1.3 Implement `CandidateAssertion` and `EvidenceBundle` boundaries, including strict extra-field policy, unique stable IDs, typed `supports`/`contradicts`/`derived_from`/`supersedes`/`retracts` links, and one canonical Fact per normalized proposition.
- [x] 1.4 Add deterministic canonical JSON serialization and Pydantic JSON Schema export, then test serialize/deserialize semantic equality and byte-stable collection/key ordering without maintaining a second handwritten schema.

## 2. Ontology v0.1

- [x] 2.1 Implement immutable `OntologyPack` models for exact ID/version/schema/checksum, entity types, predicate domain/range and literal constraints, concept definitions, and explicit entity-type/concept `is_a` edges.
- [x] 2.2 Implement local pack validation with stable structured issues for checksum mismatch, missing endpoints, self-edges, cycles, duplicate definitions, unknown types/concepts, and forbidden floating-version fallback.
- [x] 2.3 Implement deterministic subtype, predicate domain/range, and ConceptRef identity validation using standard Python collections/DFS only; cover the valid Patient-has_diagnosis-Disease and invalid Medication-has_diagnosis-Disease cases.
- [x] 2.4 Implement minimal `is_a` inference output that creates an origin=INFERRED Assertion referencing the reusable proposition Fact, exact OntologyRef, input IDs, traversed path, and reasoner version; verify a later ontology version does not rewrite prior inference records.

## 3. Deterministic contract validation

- [x] 3.1 Implement the ordered validation pipeline for shape, duplicate/dangling references, proposition uniqueness, time/scope consistency, typed link endpoints, and No Naked Facts; require evidence/derivation for TRUE/FALSE and coverage plus uncertainty reason for UNKNOWN, and return no validated records when any issue exists.
- [x] 3.2 Enforce UNKNOWN as a scoped evaluation state distinct from FALSE, including fail-closed tests that missing evidence never becomes FALSE and never creates an Unknown entity, concept, or diagnosis Fact.
- [x] 3.3 Validate immutable SourceArtifact version/checksum and structured row-ordinal/deterministic-row-fingerprint/field/value-checksum locators; define the canonical row serialization used by the fingerprint, accept optional upstream HIS locator only as a non-authoritative hop, and accept canonical-only lineage when upstream is unavailable.
- [x] 3.4 Validate document row/fingerprint/field locators, zero-based end-exclusive spans and span checksums against caller-supplied synthetic source material; reject out-of-bounds or mismatched evidence without fuzzy fallback or source I/O.
- [x] 3.5 Validate append-only conflicts, supersession, retraction, and ReviewAction references so prior Assertions/EvidenceItems remain serializable and queryable after a later action.

## 4. Oncology conformance without authority migration

- [x] 4.1 Add an oncology-owned, one-way evidence adapter that emits `OncologyConformanceLinks` from existing EvidenceAnchor/NormalizedFact/criterion/proof/source/review references while leaving `src/javert/oncology/contracts.py` and `AuditResult` models unchanged.
- [x] 4.2 Add synthetic adapter tests that compare the authoritative `eligibility_json` dump and legacy verdict before/after mapping, preserve proof-node/source/evaluator references, and prove UNKNOWN and CONFLICT are not collapsed or reprojected.

## 5. Golden conformance suite

- [x] 5.1 Add a minimal versioned synthetic Ontology v0.1 pack and de-identified `SYNTH-*` JSON golden fixtures for structured/document evidence, multiple support, contradiction, UNKNOWN, invalid domain/range, transitive `is_a`, cycle rejection, inference, retraction, and oncology references.
- [x] 5.2 Add canonical expected validation reports and tests proving repeated offline runs produce identical accepted bundles or ordered issue lists without LLM, network, filesystem source lookup, or database access.
- [x] 5.3 Add a fixture privacy gate that requires explicit `deidentified=true`, synthetic identifier conventions, and rejects patient/credential/secret keys while reporting only the offending path, never its value.

## 6. Reusable Evaluation Contract and metrics

- [x] 6.1 Implement strict Pydantic evaluation models for immutable plans, cases, A/B observations, blinded expert reviews/adjudications, metric numerator/denominator/intervals, acceptance gates, reports, and manifests; bind every record to exact input/arm/code/config/model/ontology/knowledge versions and checksums.
- [x] 6.2 Implement deterministic tri-state confusion, unsafe-auto, false V/C, correct automation, abstention, grounding, locator, provenance/proof, conflict, reproducibility and expert-usability metrics; emit `not_estimable` for zero denominators and never emit a scalar aggregate score.
- [x] 6.3 Implement Wilson 95% intervals, plan-seeded paired bootstrap upper bounds, paired win/tie/loss and exact sign-test using the standard library; verify canonical byte stability and mark probability calibration `not_comparable` when arm semantics differ.
- [x] 6.4 Implement versioned CONFORMANCE/SHADOW/PROMOTION profiles and fail-closed gate evaluation with the specified sample, safety, useful-automation, evidence and usability thresholds; insufficient sample must produce `INSUFFICIENT_EVIDENCE`.
- [x] 6.5 Implement canonical persistent evaluation packages (plan/cases/observations/adjudications/report/manifest), digest chaining, append-only run identities and privacy validation; synthetic packages may live in Git, real-case packages require 0700/0600 external storage and salted refs.

## 7. Oncology same-input paired A/B adapter

- [x] 7.1 Add a same-run oncology adapter that treats the legacy patient verdict as A and the structured patient selected eligibility projection as B only when source snapshot, candidate denominator and both arm manifests match; patient-level outcomes must not be duplicated across drug candidates.
- [x] 7.2 Extend the oncology shadow reporting path with a separate canonical paired-evaluation output while preserving existing `historical_unpaired` behavior and labels; missing pairing metadata must yield INVALID rather than fallback to history.
- [x] 7.3 Persist expert-readable de-identified case deltas for outcome, decisive criteria, UNKNOWN/CONFLICT, evidence/proof/version changes and technical status; generate randomized arm-neutral review packets plus append-only adjudication input/output schemas.
- [x] 7.4 Add synthetic paired oncology fixtures covering A/B agreement, safer abstention, false CLEAN regression, abstain-all, repeated legacy drift, deterministic B drift, missing class denominator, insufficient sample, PHI rejection and historical-unpaired exclusion.
- [x] 7.5 Test all three acceptance profiles, numerator/denominator/interval persistence, deterministic report digests and the invariant that PASS never grants deployment authorization.

## 8. Documentation and verification

- [x] 8.1 Review and update `README.md`, `docs/how_javert_works.md`, `docs/oncology/operations.md`, `docs/oncology/qa_report.md`, and `docs/CHANGES.md` with the Evidence/Evaluation Contract v0.1 boundary, A/B profiles, Fact/Assertion distinction, canonical lineage limit, and explicit statement that no verdict/API/production path is activated; explicitly confirm the deployment runbook and 2C contracts need no changes, and do not copy volatile inventory counts.
- [x] 8.2 Run the new contract, ontology, evaluation, golden, privacy, oncology adapter and paired-report tests plus existing `tests/test_oncology_eligibility.py`, `tests/test_oncology_result.py`, and `tests/test_oncology_shadow_report.py`, recording collected/pass/skip/fail/error counts and resolving every new failure.
- [x] 8.3 Run Pydantic schema export/canonical fixture checks, deterministic A/B report generation, `git diff --check`, `openspec status --change define-clinical-evidence-contract`, and `openspec validate define-clinical-evidence-contract --strict`; confirm the change is apply-ready and no runtime configuration, Router index, database schema, 2C/SSE contract, or production state changed.
