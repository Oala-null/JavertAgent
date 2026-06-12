## ADDED Requirements

### Requirement: Patient overview omits history narrative

The patient overview block SHALL NOT render the 主诉 (chief complaint) / 现病史 (present illness) / 既往史 (past history) narrative sections. Diagnoses, surgeries, and fee structure SHALL remain. The full narrative text remains available through the source-text panel.

#### Scenario: Overview hides history narrative

- **WHEN** a patient detail page renders the overview block
- **THEN** the 主诉 / 现病史 / 既往史 subblocks are absent
- **AND** the 主诊断 / 其他诊断 / 手术列表 / 费用结构 blocks are present

#### Scenario: History still reachable in source panel

- **WHEN** the expert opens the source-text panel for that patient
- **THEN** the 入院记录 documents (which contain 主诉/现病史/既往史) are present and readable

### Requirement: Violation card shows only fee/drug hit items

The violation card hit-item block SHALL render only hits whose `source ∈ {fee, drug}`. Hits whose `source = note` SHALL NOT appear as individual list items.

#### Scenario: Note hits excluded from hit-item list

- **WHEN** a run's resolved hits include both a drug hit and a note hit
- **THEN** the card's 命中项目 list shows the drug hit
- **AND** the note hit is not listed as a separate hit item

#### Scenario: Card with only note hits shows no hit-item list

- **WHEN** a run's resolved hits are all `source = note`
- **THEN** the card renders no 命中项目 list block (but still renders the source button per the next requirement)

### Requirement: Note evidence collapses to a source button

Each violation card SHALL provide a single "查阅文书原文" button in place of per-note hit items. Activating it SHALL open the source-text panel on the 文书 (notes) tab positioned at the top.

#### Scenario: Button opens notes tab at top

- **WHEN** the expert clicks 查阅文书原文 on a violation card
- **THEN** the source panel opens with the notes tab active
- **AND** it is scrolled to the first document (top), not an arbitrary row

### Requirement: Agent reasoning and raw evidence are collapsed

The agent `reasoning` text and the raw `evidence_json` dump SHALL be moved into a collapsed `<details>` element (the 证据 block), hidden by default and expandable on demand. They SHALL NOT appear in the always-visible card body.

#### Scenario: Reasoning hidden by default

- **WHEN** a violation card renders
- **THEN** the reasoning text and evidence_json are inside a collapsed details element
- **AND** the card body above it shows only hit items, the source button, and the review form

#### Scenario: Reasoning expandable for audit

- **WHEN** the expert expands the 证据 details
- **THEN** the full reasoning text and evidence_json content are shown

### Requirement: Source documents ordered and grouped by clinical sequence

The `/raw` endpoint SHALL assign each note a clinical-document bucket and order key following the sequence 病案首页 → 入院记录 → 病程记录 → 手术记录 → 知情书 → 出院小结, with unmatched stages placed in a trailing 其他 bucket. The source panel SHALL render documents grouped under collapsible bucket headers in that order; within a bucket, documents SHALL be ordered by 事件时间 ascending.

#### Scenario: Buckets render in clinical order

- **WHEN** a patient has 入院记录, 手术记录, and 出院小结 documents
- **THEN** the source panel shows collapsible group headers in the order 入院记录, 手术记录, 出院小结

#### Scenario: Doctor-named ward-round records fall into 病程记录

- **WHEN** a document's 阶段 is "戴佳奇主治医师首次查房记录"
- **THEN** it is bucketed under 病程记录

#### Scenario: Consent forms fall into 知情书

- **WHEN** a document's 阶段 contains 同意书 / 告知书 / 知情 / 志愿书
- **THEN** it is bucketed under 知情书

#### Scenario: Unknown stage goes to trailing bucket

- **WHEN** a document's 阶段 matches no bucket keyword
- **THEN** it is placed in the 其他 bucket rendered last, and is not dropped

### Requirement: Fee tables put date first in YYYY/MM/DD

In the source-text panel and the raw-data modal, fee tables SHALL render the time column as the first column, formatted `YYYY/MM/DD`.

#### Scenario: Date is the first fee column

- **WHEN** the fees tab renders for a patient
- **THEN** the leftmost column is the fee date
- **AND** a `fee_ocur_time` of `05/03/2024` (dd/mm/yyyy) is displayed as `2024/03/05`

### Requirement: Runs grouped by violation type with V before I

The patient detail page SHALL group violation cards by `violation_type` (细类) under collapsible section headers. Within each group, cards with verdict V (violation) SHALL be ordered before verdict I (inconclusive). Each group header SHALL show the violation count and inconclusive count.

#### Scenario: Cards grouped under violation-type headers

- **WHEN** a patient has runs of violation_type 重复收费 and 过度检查
- **THEN** the detail page shows a collapsible section per violation_type
- **AND** within 过度检查 the V cards precede the I cards

#### Scenario: Group header shows V/I counts

- **WHEN** the 过度检查 group has 2 violation and 1 inconclusive runs
- **THEN** its header shows counts equivalent to 2 违 · 1 不明

### Requirement: Violation-type navigation chips

The detail page SHALL render a sticky chip row at the top, one chip per violation-type group, labeled with a short alias plus its `V▪I` subcounts. Clicking a chip SHALL scroll to its group (navigation, not page reload).

#### Scenario: Chip labeled with alias and subcounts

- **WHEN** a group is violation_type 虚构医药服务项目或以骗保为目的串换项目 with 1 V and 0 I
- **THEN** its chip shows a short alias (not the full sentence) and a subcount of 1 violation

#### Scenario: Chip click scrolls to group

- **WHEN** the expert clicks the 重复收费 chip
- **THEN** the page scrolls to the 重复收费 group section
- **AND** the page is not reloaded

### Requirement: Inconclusive-only toggle

The detail page SHALL provide a 只看不明 toggle that, when enabled, visually filters the cards to show only verdict I (inconclusive) runs, implemented client-side without page reload. It SHALL operate independently of the existing server-side verdict filter.

#### Scenario: Toggle shows only inconclusive

- **WHEN** the expert enables 只看不明
- **THEN** only verdict I cards remain visible
- **AND** verdict V cards are hidden without a page reload

#### Scenario: Toggle is reversible

- **WHEN** the expert disables 只看不明
- **THEN** all cards (subject to the server-side filter) are visible again
