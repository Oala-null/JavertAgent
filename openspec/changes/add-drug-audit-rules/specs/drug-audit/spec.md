## ADDED Requirements

### Requirement: Drug audit knowledge base

The system SHALL provide a build script `scripts/build_drug_kb.py` that normalizes the four xlsx files under `data/药品类规则/` into a single `configs/drug_audit_kb.json`. The JSON MUST be keyed by drug `通用名`, where each value is a list of entries `{rule_type, detect_logic, basis}`. The four rule types MUST be exactly `限适应症`, `超说明书`, `限二线`, `禁忌症`, sourced from `第二部分-8`, `第二部分-70`, `第二部分-5`, `第二部分-71` respectively. The script MUST skip the two header rows of each sheet and drop empty rows.

#### Scenario: Build produces a keyed KB with four rule types

- **WHEN** `python scripts/build_drug_kb.py` runs against the four xlsx files
- **THEN** `configs/drug_audit_kb.json` is written, the union of `rule_type` values across all entries is exactly `{限适应症, 超说明书, 限二线, 禁忌症}`, and the total distinct 通用名 count is ≈928

#### Scenario: A drug present in multiple source files merges under one key

- **WHEN** a 通用名 (e.g. `艾普拉唑肠溶片`) appears in both `第二部分-8` (限适应症) and `第二部分-5` (限二线)
- **THEN** the KB has a single key for that 通用名 whose value list contains two entries, one per `rule_type`, each carrying its own `basis` text

#### Scenario: Build is deterministic

- **WHEN** the build script is run twice on unchanged inputs
- **THEN** the two `drug_audit_kb.json` outputs are byte-identical

### Requirement: drug_audit_lookup tool — bulk mode

The system SHALL provide a `drug_audit_lookup` tool whose bulk mode takes `(patient_id, rule_type?)` and returns the patient's 药品 fees (`medins_chrgitm_type` ∈ {西药, 中药, 草药}) that match the KB, each annotated with the original fee name, the matched 通用名, the `rule_type`, and the `basis` text. Matching MUST be a deterministic 通用名-stem substring test: the fee name has its `(基)(集)(国谈)` style prefixes stripped, the KB 通用名 has its dosage-form suffix stripped (`片/胶囊/注射液/注射用/…`), and a stem of length ≥ 2 must be a substring of the fee name. When `rule_type` is supplied, only matches of that type are returned.

#### Scenario: Bulk returns only KB-matched drugs with their basis

- **WHEN** `drug_audit_lookup(patient_id="<a patient who was billed 人血白蛋白>")` runs
- **THEN** the result includes `人血白蛋白` with its original fee name, `rule_type="限适应症"`, and the limited-payment `basis` text, and excludes the patient's drug fees that are not in the KB

#### Scenario: rule_type filter narrows the result

- **WHEN** `drug_audit_lookup(patient_id="X", rule_type="禁忌症")` runs
- **THEN** only KB matches whose `rule_type` is `禁忌症` are returned; 限适应症/超说明书/限二线 matches for the same patient are omitted

#### Scenario: Patient with no KB-matched drugs yields an empty result, not an error

- **WHEN** `drug_audit_lookup` runs for a patient whose drug fees intersect the KB in zero entries
- **THEN** the tool returns an explicit "no KB-matched drugs" result and does not raise

#### Scenario: Stem matching tolerates prefixes and dosage-form differences

- **WHEN** the patient fee name is `(集)(基)阿卡波糖片(拜唐苹)` and the KB has `阿卡波糖片`
- **THEN** the match succeeds and the original fee name `(集)(基)阿卡波糖片(拜唐苹)` is carried in the result for LLM review

### Requirement: drug_audit_lookup tool — single mode

The `drug_audit_lookup` tool single mode takes `(drug_name)` and returns the KB facts for that drug across all its `rule_type` entries, or an explicit not-found result. Following Javert pilot convention, there MUST be no network fallback.

#### Scenario: Single-mode hit returns all rule_type entries

- **WHEN** `drug_audit_lookup(drug_name="艾普拉唑肠溶片")` runs
- **THEN** the result lists every `rule_type` entry for that drug with its `basis` text

#### Scenario: Single-mode miss returns not-found without network access

- **WHEN** `drug_audit_lookup(drug_name="<a drug absent from the KB>")` runs
- **THEN** the result states the drug was not found in the KB and performs no network lookup

### Requirement: drug_audit_lookup tool registration

The `drug_audit_lookup` tool SHALL be registered in `src/javert/tools/registry.py` and be callable by the audit Runner via the `<tool_call>` protocol. The existing 52-drug `drug_indication` tool MUST remain unchanged and separately registered.

#### Scenario: Registry exposes both drug tools

- **WHEN** the tool registry is enumerated
- **THEN** both `drug_audit_lookup` and `drug_indication` are present, and `drug_indication` still returns its 52-drug 适应症 + ICD-candidate output unchanged

### Requirement: M8 drug-audit template

The system SHALL provide `configs/templates/M8.yaml` named「药品适应症/限定审计」with a `drug_rule_type` field whose value is one of `限适应症 / 超说明书 / 限二线 / 禁忌症`. The template MUST render four comparison logics: 限适应症 and 超说明书 flag a violation when the diagnosis is NOT within the basis; 禁忌症 flags a violation when the diagnosis IS within the contraindication basis (inverted); 限二线 flags a violation when the diagnosis is within the indication but the notes show no first-line failure evidence (and therefore additionally drives a `search_notes` step). `javert template validate M8` MUST report `ready`.

#### Scenario: M8 validates ready

- **WHEN** `javert template validate M8` runs
- **THEN** it reports `ready` (master_prompt non-empty, all required fields declared)

#### Scenario: 禁忌症 mode renders inverted logic

- **WHEN** M8 is rendered with `drug_rule_type=禁忌症`
- **THEN** the rendered prompt instructs that a VIOLATION occurs when the patient diagnosis matches the contraindication basis (not when it is absent)

#### Scenario: 限二线 mode renders the first-line-failure check

- **WHEN** M8 is rendered with `drug_rule_type=限二线`
- **THEN** the rendered prompt includes a `search_notes` step seeking evidence of first-line drug failure/intolerance, and judges CLEAN when such evidence is present

### Requirement: On-label false-positive guardrail

The M8 `master_prompt` SHALL explicitly state that a KB match alone is NOT a violation, and that the verdict depends on whether the patient's diagnosis falls within (or, for 禁忌症, matches) the `basis`. Diagnosis MUST be sourced from `shi_zd` (病案首页) when available, falling back to `note_diagnosis`. This guardrail is an acceptance gate: on the thyroid cohort, drugs that are on-label for thyroid/calcium-deficiency patients MUST overwhelmingly verdict CLEAN.

#### Scenario: On-label drug on a matching patient is CLEAN

- **WHEN** an M8 drug rule audits `甲状腺片` (matched as 超说明书) on a thyroid-cancer post-op patient whose diagnosis supports thyroid hormone replacement
- **THEN** the verdict is CLEAN, with the diagnosis-within-indication reasoning cited as evidence

#### Scenario: Off-indication drug on a non-matching patient is VIOLATION

- **WHEN** an M8 drug rule audits `人血白蛋白` (限适应症) on a general-ward patient whose diagnoses contain none of the limited-payment indications
- **THEN** the verdict is VIOLATION, citing the patient's full diagnosis list plus the limited-payment `basis`

#### Scenario: Ambiguous diagnosis yields INCONCLUSIVE

- **WHEN** the only relevant diagnosis is an intermediate state (待查/疑似/排除) or the diagnosis source is missing
- **THEN** the verdict is INCONCLUSIVE rather than VIOLATION
