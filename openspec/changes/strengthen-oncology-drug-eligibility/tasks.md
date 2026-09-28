## 1. Freeze Contracts and Parallel Worktree Boundaries

- [x] 1.1 Add shared enums and Pydantic contracts for `NormalizedFact`, `CriterionAssessment`, `ProofNode`, `DocumentationSuggestion`, and `EligibilityEvaluation`, including legacy verdict projection validation.
- [x] 1.2 Add JSON schemas and common metadata rules for eligibility, pathology, and regimen knowledge assets (`schema_version`, `content_version`, effective dates, source refs, checksum, `review_status`).
- [x] 1.3 Add `JAVERT_ONCOLOGY_ELIGIBILITY_V2=off|shadow|on` configuration with tests proving the default preserves current production behavior.
- [x] 1.4 Create four implementation branches/worktrees after the shared contracts commit: A eligibility/pathology, B regimen, C result/guidance, and Integration wiring; document exclusive file ownership so only Integration edits `runner.py`, `drug_audit_lookup.py`, RD04/R007, and routing files.
- [x] 1.5 Create minimized, de-identified synthetic golden fixture shells for urothelial HER2-low, Pola cycle-conflict, and Pola transplant-gap scenarios without copying raw patient exports into Git.

## 2. Worktree A — Eligibility Trees and Pathology Normalization

- [x] 2.1 Implement the versioned `oncology_eligibility_rules` loader, schema validation, effective-date selection, approved-status gate, and immutable source-version reporting.
- [x] 2.2 Implement typed `all`/`any`/leaf AST evaluation with the specified four-state truth tables and an isomorphic proof tree retaining decisive and non-decisive children.
- [x] 2.3 Implement a builder that converts current active oncology reimbursement restrictions into condition-tree candidates and emits an explicit `needs_review` report for every unsupported or ambiguous condition.
- [x] 2.4 Seed `pathology_biomarker_kb` with all biomarker/pathology conditions used by current active oncology reimbursement restrictions, including typed aliases, methods, cancer contexts, thresholds, effective versions, and approval state.
- [x] 2.5 Implement pathology extraction/normalization for marker aliases, IHC/ISH/FISH/molecular method separation, score/value domains, specimen metadata, temporal alignment, and unresolved conflicts.
- [x] 2.6 Add HER2 regression cases for `HER2`, `HER-2`, `HER2/neu`, `c-erbB-2`, `CerbB2`, and `ERBB2`, proving IHC 0/1+/2+/3+ behavior and preventing molecular results from being coerced into IHC.
- [x] 2.7 Complete the urothelial HER2-low golden test: `CerbB2(1+) → HER2 IHC 1+ → NOT_SATISFIED`, prior platinum remains `UNKNOWN`, and the mandatory branch is not CLEAN.
- [x] 2.8 Add coverage QA proving every active oncology restriction containing a pathology/biomarker condition is either compiled with approved sources or explicitly blocked as `needs_review`; no entry may silently fall back to LLM interpretation.

## 3. Worktree B — Oncology Regimen Resolution

- [x] 3.1 Implement versioned drug-concept and regimen schemas covering generic/English/trade/token aliases, insurance codes, cancer contexts, components, sources, and approval status.
- [x] 3.2 Mine current notes for regimen alias candidates and produce a review artifact that filters dosage/frequency noise; candidate frequency alone MUST NOT activate an entry.
- [x] 3.3 Seed `oncology_regimen_kb` with approved high-frequency current-corpus regimens and all regimens needed by RD04 golden cases, including distinct `Pola-R-GemOx` and `R-GemOx`.
- [x] 3.4 Implement Unicode/case/space/hyphen normalization, longest approved alias matching, cancer-context disambiguation, and `AMBIGUOUS`/context-conflict handling.
- [x] 3.5 Implement evidence precedence: explicit generic/trade/enumerated drug list first, then approved regimen inference; retain explicit-versus-dictionary conflicts without selecting the favorable side.
- [x] 3.6 Implement treatment-event status (`PLANNED / ADMINISTERED / HISTORICAL / UNKNOWN`), independent `cycle_no` and `line_of_therapy`, date anchors, and temporal-conflict flags.
- [x] 3.7 Add boundary tests proving `Pola-R-GemOx` resolves polatuzumab while `R-GemOx` does not, planned treatment does not count as administered, and fee codes corroborate rather than create regimen semantics.
- [x] 3.8 Complete the Pola cycle-conflict golden test: resolve all four Pola-R-GemOx components, set `cycle_no=4`, leave line/relapse/refractory facts unknown, preserve the year conflict, and produce a review-required legacy verdict.

## 4. Worktree C — Structured Results and Patient-Centered Guidance

- [x] 4.1 Extend `AuditResult` with optional `eligibility_evaluation` while preserving all legacy required fields and old-result deserialization.
- [x] 4.2 Implement deterministic compatibility mapping and reject or normalize inconsistent combinations of `audit_disposition`, `eligibility_status`, and legacy verdict.
- [x] 4.3 Implement condition-linked documentation templates whose suggestions cannot alter criterion states, create evidence anchors, or claim missing documentation already exists.
- [x] 4.4 Implement the patient-centered transplant template: `患者74岁且已多线治疗；如拟使用该药，建议病程中补充“不适合造血干细胞移植”及简要原因，避免因文书缺项影响医保报销。`
- [x] 4.5 Add an idempotent SQLite migration for nullable `audit_runs.eligibility_json` and round-trip tests for new and historical rows.
- [x] 4.6 Add an idempotent SQL Server migration for nullable `javert_audit_runs.eligibility_json`, update write/read/sync paths, and add mocked dual-store compatibility tests.
- [x] 4.7 Extend API/SSE serializers and workbench rendering to show audit disposition, eligibility status, condition proof, data-quality flags, and documentation suggestions while leaving expert review button semantics unchanged.
- [x] 4.8 Complete the Pola transplant-gap golden test: prior treatment and progression are supported, transplant ineligibility is a documentation gap, result is `NO_VIOLATION_FOUND + DOCUMENTATION_GAP`, legacy verdict is CLEAN, and the focused suggestion is displayed.

## 5. Integration Worktree — Drug Audit Wiring and Rule Ownership

- [x] 5.1 Merge the three completed worktree branches after their independent tests pass and resolve only contract-level differences before editing shared runtime files.
- [x] 5.2 Extend `drug_audit_lookup` to attach structured eligibility and regimen evidence for valid RD04 fee candidates without allowing regimen text to create a candidate whose fee net is zero.
- [x] 5.3 Wire RD04 to the structured oncology evaluator and make R007 exclude `oncology=true AND source_type=insurance` only when v2 is on; retain non-oncology R007 coverage and keep RD10-RD37 abandoned.
- [x] 5.4 Add deterministic ownership/de-duplication tests proving RD04 and R007 cannot emit two decisions for the same patient, drug concept, fee candidate, and restriction version, even when explicitly run together.
- [x] 5.5 Replace the current blanket “复查病理/自费” runner notice with condition-level structured suggestions for v2 results while preserving legacy behavior for non-v2 drug audits.
- [x] 5.6 Preserve the existing refund-netting, self-pay evidence, precheck-first, evidence merge, verdict gate, and non-oncology drug-audit behavior through focused regression tests.
- [x] 5.7 Implement shadow mode that persists or reports the structured comparison separately but does not change the production legacy verdict.
- [x] 5.8 Run end-to-end golden assertions for the urothelial HER2-low, Pola cycle-conflict, and Pola transplant-gap fixtures through the actual RD04/tool/Runner/store/API path.

## 6. Knowledge and Regression Quality Gates

- [x] 6.1 Add schema, checksum, source-version, effective-date, approved-status, and deterministic-byte-output tests for all three knowledge assets and builders.
- [x] 6.2 Add exhaustive AND/OR truth-table tests plus repeated-run tests proving identical inputs produce byte-equivalent criterion states and proof-tree semantics.
- [x] 6.3 Add negative tests for unsupported marker context, untyped `HER2阳性`, post-service pathology, conflicting specimens, unknown regimen context, explicit component conflicts, and cycle-versus-line confusion.
- [x] 6.4 Run all directly affected unit/integration suites and record exact pass/skip counts; fix every new failure before batch evaluation.
- [x] 6.5 Run the full non-slow test suite, separating pre-existing fixture/environment debt from regressions introduced by this change, and add no new failures or errors.
- [x] 6.6 Run shadow evaluation across all current RD04 candidates and generate a de-identified comparison report containing old/new verdict, eligibility status, decisive criteria, documentation gaps, duplicate count, and available expert review agreement.
- [x] 6.7 Verify acceptance gates: all three golden cases pass, duplicate oncology drug decisions equal zero, every active pathology restriction is approved or explicitly blocked, and no fixture or report contains raw PHI.

## 7. Operational Documentation and Activation

- [x] 7.1 Document knowledge-source update/review workflow, candidate promotion, version rollback, and how hospitals can customize documentation-suggestion wording.
- [x] 7.2 Document SQLite/SQL Server migration, feature-flag modes, RD04/R007 ownership, shadow report interpretation, and rollback steps.
- [x] 7.3 Update architecture/operations documentation and the relevant OpenSpec status notes so the abandoned RD10-RD37 and active bulk ownership are no longer contradictory.
- [x] 7.4 Present the shadow/golden results for user acceptance before switching `JAVERT_ONCOLOGY_ELIGIBILITY_V2` to `on`; user accepted the results and separately authorized production activation plus deployment to 62 on 2026-07-17.
