## ADDED Requirements

### Requirement: Deterministic hit-item resolution from stored audit data

The system SHALL provide a read-only, deterministic resolver `resolve_hits(run, rule_drug_type)` that turns a single audit run's persisted `evidence_json` + `tool_calls_json` into a list of `HitItem` objects, each carrying `{source, name, code_nat, code_local, restriction, anchor}`. The resolver MUST NOT call the LLM, MUST NOT touch the network, and MUST NOT mutate `Javert_audit_runs` rows. The same `HitItem[]` SHALL power both the violation card's hit-item block and the click-to-source jump.

#### Scenario: Resolve hit items for a drug violation

- **WHEN** `resolve_hits` runs on J26355's R007 run (verdict VIOLATION, evidence cites `注射用福沙匹坦双葡甲胺` with source=drug)
- **THEN** it returns at least one `HitItem` whose `name` is the drug's 通用名, `code_nat`/`code_local` come from that patient's matching `shi_fee` row, `restriction` is the 限定支付原文, and `anchor` points at the fee/notes location

#### Scenario: Resolver is pure and repeatable

- **WHEN** `resolve_hits` is called twice on the identical stored run
- **THEN** both calls return byte-identical `HitItem[]` (no randomness, no time dependence, no write side-effects)

#### Scenario: Non-fee evidence yields no spurious hit item

- **WHEN** a run's evidence contains only `source=etl_warning` entries (no fee/drug/note)
- **THEN** `resolve_hits` returns an empty list rather than fabricating a code or name

### Requirement: Insurance code enrichment from shi_fee

For each resolved hit whose `source ∈ {fee, drug}`, the system SHALL look up the code from **that patient's own** `shi_fee` rows by matching `medins_list_name` (stem match), and populate `code_nat` from `med_list_codg` and `code_local` from `medins_list_codg`. It MUST NOT pick a global/first-row code when multiple rows share a name with different specs.

#### Scenario: Code comes from the patient's matching fee row

- **WHEN** a hit item resolves to `脑功能成像` for a patient whose `shi_fee` row has `med_list_codg=S21020000300010` and `medins_list_codg=210200003`
- **THEN** the hit item carries `code_nat=S21020000300010` and `code_local=210200003`

#### Scenario: Missing code degrades gracefully

- **WHEN** a hit item's name has no matching `shi_fee` row with a code (code column blank)
- **THEN** `code_nat`/`code_local` are left empty and the hit item still renders with its name and (if applicable) restriction

### Requirement: Drug restriction enrichment from drug_audit_kb

For runs whose rule declares a `drug_rule_type` (M8 family), the system SHALL attach `restriction` from `configs/drug_audit_kb.json` `drugs[通用名]` under that exact `rule_type` (`basis` text). Runs without `drug_rule_type` MUST leave `restriction` empty.

#### Scenario: 限定药 restriction surfaced

- **WHEN** resolving J26355's R007 hit `注射用福沙匹坦双葡甲胺` (rule `drug_rule_type=限适应症`)
- **THEN** `restriction` equals the KB `basis` for that drug under `限适应症` (e.g. "限放化疗...")

#### Scenario: Non-drug rule has no restriction

- **WHEN** resolving a hit for a重复收费 rule (no `drug_rule_type`)
- **THEN** the hit item's `restriction` is empty and only code + name are carried

#### Scenario: Same-name / compound drug is flagged for review, not asserted

- **WHEN** a stem match links fee `吸入用布地奈德混悬液` to KB `布地奈德肠溶胶囊` (限IgA肾病)
- **THEN** the hit item carries the original fee name verbatim and the restriction is marked as "按通用名匹配, 剂型/复方需复核" rather than presented as confirmed

### Requirement: Source anchor matching ladder

The `anchor` of each hit item SHALL be resolved by a fixed reliability ladder and MUST NOT fail silently: (1) prefer the exact `keyword` the LLM used in `tool_calls` for the matching source — guaranteed to be a substring of the source text — and compute its character offset; (2) else use `evidence.locator` (subsection / item name) to land on the correct row; (3) else use the longest n-gram of `evidence.text` (after stripping leading/trailing `...`); (4) else mark the anchor as `tab`-only so the panel still opens to the correct tab.

#### Scenario: Exact keyword gives precise offset

- **WHEN** a note evidence has a corresponding `search_notes(keyword="脑功能成像")` tool call
- **THEN** the anchor carries `{tab:"notes", subsection, query:"脑功能成像", char_start, char_end}` whose span exactly matches the raw note text

#### Scenario: Paraphrased evidence falls back to subsection

- **WHEN** `evidence.text` is a paraphrase not present verbatim and no tool keyword matches, but `evidence.locator="出院诊断"` exists
- **THEN** the anchor lands on the 出院诊断 subsection (tab=notes) without claiming a character-level span

#### Scenario: Unresolvable anchor still opens correct tab

- **WHEN** none of keyword/locator/text resolve to a substring
- **THEN** the anchor is `{tab: <from source>, query: ""}` and is marked "未能精确定位" rather than dropped

### Requirement: Resolution operates on existing data without re-run

The resolver SHALL produce hit items and anchors for the already-persisted 106 patients / 5016 runs **without** re-running any audit, without a `Javert_audit_runs` schema migration, and without creating new run rows (so existing expert reviews remain attached unchanged).

#### Scenario: Old run gets hit items at render time

- **WHEN** the patient detail route renders a pre-existing J61556 violation run whose data predates this change
- **THEN** hit items and anchors are computed live from that run's stored `evidence_json`/`tool_calls_json` and rendered, with no new run row and no LLM call

### Requirement: Trace-replay backfill script (optional cache)

The system SHALL provide `scripts/backfill_anchors.py` that batch-runs the resolver and persists results into a derived cache keyed by the existing `run_id`, by **replaying only deterministic tool logic** (no LLM). It MUST be idempotent, MUST NOT alter verdicts/reasoning/reviews, and the render path MUST fall back to live resolution on cache miss.

#### Scenario: Backfill is review-safe and idempotent

- **WHEN** `backfill_anchors.py` runs twice over the same runs
- **THEN** the second run writes byte-identical cache entries, no `javert_vio_review` row is modified, and no run row is added or deleted

#### Scenario: Render falls back when cache absent

- **WHEN** a run has no cached anchors (backfill not run)
- **THEN** the patient detail page still shows hit items and supports jump-to-source via live resolution
