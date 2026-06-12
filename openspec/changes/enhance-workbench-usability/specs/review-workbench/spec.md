## ADDED Requirements

### Requirement: Patient list facet filtering

The patient sidebar SHALL provide four client-side facets — tag (`batch_tag` multi-select), fee range (fixed buckets), primary-diagnosis keyword (text contains), and update-time presets — that show/hide already-rendered patient cards via JavaScript. These facets MUST compose additively on top of the existing server-side verdict filter (`v_and_i / v_only / i_only / all`) and MUST NOT require a new server route.

#### Scenario: Diagnosis keyword facet narrows the visible list

- **WHEN** the user types `甲状腺` into the 主诊 facet box
- **THEN** only cards whose `data-dx` contains `甲状腺` remain visible; the verdict filter selection is unchanged and no page reload occurs

#### Scenario: Fee bucket facet filters by amount

- **WHEN** the user selects the `>5万` fee bucket
- **THEN** only cards whose `data-fees` exceeds 50000 remain visible

#### Scenario: Tag facet composes with verdict filter

- **WHEN** the verdict filter is `v_only` and the user picks tag `szx`
- **THEN** the visible set is the intersection: patients with ≥1 V **and** `data-tag=szx`; clearing the tag facet restores the full `v_only` set

#### Scenario: Facets reset cleanly

- **WHEN** the user clears all facets
- **THEN** every card permitted by the current verdict filter is visible again

### Requirement: Patient card summary fields

Each patient card SHALL display, in addition to V/I/C badges and review progress, an approximate total cost (`¥` from `fees_sum`), the primary diagnosis (`primary_dx`), and a relative update time (from `MAX(created_at)`). These values MUST also be emitted as `data-fees` / `data-dx` / `data-updated` attributes to drive the facets.

#### Scenario: Card shows cost and disease

- **WHEN** the sidebar renders J66252 (fees_sum ≈ 48230, primary_dx 甲状腺恶性肿瘤 C73.x00)
- **THEN** the card shows `甲状腺恶性肿瘤 C73.x00` and `¥48,230` and carries `data-fees="48230"` and `data-dx` containing `甲状腺`

#### Scenario: Missing summary degrades gracefully

- **WHEN** a patient has no `shi_zd` primary diagnosis
- **THEN** the card shows `—` for diagnosis (and falls back to a note-derived value if available) without erroring, and `data-dx` is empty

#### Scenario: Summary is served from a process-level cache

- **WHEN** the sidebar renders 100+ patient cards
- **THEN** `fees_sum` is read from a one-time `groupby` cache (not a per-card full-table scan) so render stays responsive

### Requirement: Fee category inline expansion

The 按医保项目类别拆分 table SHALL render each category row as an expandable `<details>` whose body lists that category's own line items (编码 · 名称 · 次数 · 金额), so a reviewer can drill into a category without opening the raw-record modal.

#### Scenario: Expanding a category reveals its line items

- **WHEN** the user expands the `检查` category row for a patient
- **THEN** the body lists the检查-category fee items with their `medins_list_codg`/`med_list_codg`, name, count, and amount — sourced from the same fee rows, grouped by `medins_chrgitm_type`

#### Scenario: Category totals still match

- **WHEN** a category row is collapsed
- **THEN** its summary line still shows the category 条数/金额/占比 as before; expansion adds detail without changing totals

### Requirement: Violation hit-item block

Each violation card SHALL render a 命中项目 block directly under the rule description, listing the resolved hit items as `项目编码 · 项目名称`. For runs whose rule has a `drug_rule_type`, each drug hit MUST additionally show its 限定内容 (restriction). The national code is shown primarily; the院内 code is available on hover.

#### Scenario: Drug violation shows code, name, and restriction

- **WHEN** the J26355 R007 violation card renders (4 flagged drugs)
- **THEN** the 命中项目 block lists each drug as `<code> · <通用名>` with a `限定: <restriction>` segment, and each row is clickable to jump to source

#### Scenario: Non-drug violation shows code and name only

- **WHEN** a 重复收费 violation card renders
- **THEN** the 命中项目 block shows `编码 · 名称` rows with no 限定 segment

#### Scenario: Hover reveals院内 code

- **WHEN** the user hovers a hit item showing the national code `S21020000300010`
- **THEN** a tooltip reveals the院内 code `210200003`

### Requirement: Click-to-source synchronized panel

Clicking a hit item (or evidence reference) on a violation card SHALL open a right-side synchronized source panel (compare/对照 mode): the detail area collapses the reasoning to the left and slides in a source panel on the right that auto-switches to the correct tab (文书/费用 by `source`), scrolls to the anchor, and highlights the matched span using the shared highlight engine. The existing "查看原始病历" modal MUST remain available for full browsing. Narrow viewports MAY fall back to the modal form.

#### Scenario: Clicking a note evidence jumps and highlights

- **WHEN** the user clicks a hit item whose anchor is `{tab:notes, subsection:出院诊断, query:甲状腺乳头状癌}`
- **THEN** the right panel opens to the 文书 tab, scrolls the 出院诊断 段 into view, and wraps `甲状腺乳头状癌` in a highlighted `<mark>` centered in view

#### Scenario: Clicking a fee hit switches to the fee tab

- **WHEN** the user clicks a hit item with `source=fee` (e.g. 脑功能成像)
- **THEN** the right panel opens to the 费用 tab and highlights the matching fee row

#### Scenario: Closing the panel restores the layout

- **WHEN** the user closes the source panel
- **THEN** the reasoning card returns to full width and no highlight state leaks into the next card

#### Scenario: Unresolvable anchor opens the tab without faking a hit

- **WHEN** a hit item's anchor is `tab`-only (no span resolved)
- **THEN** the panel opens to the correct tab and shows a "未能精确定位" hint instead of highlighting an arbitrary location

### Requirement: Reviewer comment full text on hover

A reviewer comment that is visually truncated SHALL expose its full text on hover (native `title` tooltip or CSS hover box) without a database round-trip, since the full comment is already present in the rendered payload. This supersedes the prior truncation-with-`[展开]`-toggle behavior.

#### Scenario: Long other-reviewer comment shown on hover

- **WHEN** another expert's comment exceeds 80 characters and is displayed truncated with `…`
- **THEN** hovering the comment reveals the complete text, and no new network request is made
