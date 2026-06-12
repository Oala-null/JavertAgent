## ADDED Requirements

### Requirement: derived_from_template field

The `Rule` model SHALL include an optional `derived_from_template` field with type `str | None` (default `None`). When non-None, its value MUST match a known template_id (e.g. `M1`). The field records which template the rule's current `prompt_addon` was rendered from, so future template revisions can identify which rules need re-fitting.

#### Scenario: Loading a rule yaml with derived_from_template

- **WHEN** the system reads R045.yaml containing `derived_from_template: M1`
- **THEN** the returned `Rule` object has `derived_from_template == "M1"`

#### Scenario: Backwards compat without derived_from_template

- **WHEN** the system reads a rule yaml that lacks the `derived_from_template` key (existing yamls before this change)
- **THEN** the returned `Rule` object has `derived_from_template is None` and no validation error

#### Scenario: Field position in written yaml

- **WHEN** `rule_writer.write_rule(rule)` writes a Rule with `derived_from_template == "M1"`
- **THEN** the yaml output places `derived_from_template` between `notes` and end-of-file (last logical field), preserving the existing field order for all other fields

## REMOVED Requirements

### Requirement: Pilot includes N-marked rules

**Reason**: 医保专家 2026-05-13 通过 `docs/163规则可行性分析表-已标注.xlsx` 给出最终筛查, 14 条规则被标 N (不做): R001 R002 R003 R004 R005 R006 R011 R134 R170 R173 R174 R178 R202 R312. 这些规则的 yaml 文件被物理删除 (不走 abandoned 状态), 后续审计不再涵盖。

**Migration**: `git rm configs/rules/{R001,R002,R003,R004,R005,R006,R011,R134,R170,R173,R174,R178,R202,R312}.yaml`. 历史 `audit_runs` 行保留可查; `javert show <rule_id> --patient <pid>` 对已删 rule_id 会失败 (找不到 yaml), 这是已接受的代价。
