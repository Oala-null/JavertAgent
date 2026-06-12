## MODIFIED Requirements

### Requirement: Source anchor matching ladder

The `anchor` of each hit item SHALL be resolved by a fixed reliability ladder and MUST NOT fail silently: (1) prefer the exact `keyword` the LLM used in `tool_calls` for the matching source — guaranteed to be a substring of the source text — and compute its character offset; (2) else use `evidence.locator` (subsection / item name) to land on the correct row; (3) else use the longest n-gram of `evidence.text` (after stripping leading/trailing `...`); (4) else mark the anchor as `tab`-only so the panel still opens to the correct tab.

For hits whose `source ∈ {fee, drug}`, the jump anchor SHALL be decoupled from insurance-code enrichment: even when no patient `shi_fee` row matches (so no `code_nat`/`code_local` is found), the anchor SHALL carry the hit name (or its stem) as `query` so the fees tab performs substring highlighting. Such a fee/drug anchor SHALL NOT be marked `tab`-only / `unresolved` solely because code enrichment failed. Code-enrichment matching strictness is unchanged.

#### Scenario: Exact keyword gives precise offset

- **WHEN** a note evidence has a corresponding `search_notes(keyword="脑功能成像")` tool call
- **THEN** the anchor carries `{tab:"notes", subsection, query:"脑功能成像", char_start, char_end}` whose span exactly matches the raw note text

#### Scenario: Paraphrased evidence falls back to subsection

- **WHEN** `evidence.text` is a paraphrase not present verbatim and no tool keyword matches, but `evidence.locator="出院诊断"` exists
- **THEN** the anchor lands on the 出院诊断 subsection (tab=notes) without claiming a character-level span

#### Scenario: Fee hit without code still jumps via fuzzy query

- **WHEN** a drug hit resolves to 重组人血 for a patient whose `shi_fee` has no stem-matching row (no code found)
- **THEN** the anchor carries `{tab:"fees", query:"重组人血"}` and is not marked unresolved
- **AND** opening it highlights any fee row whose text contains 重组人血

#### Scenario: Unresolvable note anchor still opens correct tab

- **WHEN** a note hit's keyword/locator/text all fail to resolve to a substring
- **THEN** the anchor is `{tab:"notes", query:""}` and is marked "未能精确定位" rather than dropped
