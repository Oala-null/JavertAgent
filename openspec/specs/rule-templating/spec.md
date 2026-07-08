# rule-templating Specification

## Purpose
TBD - created by archiving change m2-rollout. Update Purpose after archive.
## Requirements
### Requirement: M2 template loadable and complete

After this change, `configs/templates/M2.yaml` SHALL contain a complete template definition for "过度检查" (overuse screening): a non-empty `master_prompt` with Jinja2 conditionals supporting drug-check branch / single-count INCONCLUSIVE branch / special_notes / pilot_caveat, plus `keywords_template`, `tools_template`, `signal_template`, and a `fields` list covering 14-16 field declarations.

`load_template("M2")` MUST return a valid `Template` instance (validation passes), `master_prompt` length MUST be >= 800 characters, and `fields` MUST include at minimum: `exam_concept_desc`, `catalog_basis`, `exam_kw_primary_list`, `indication_dx_list`, `min_count`, `drug_check`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M2 template validation passes

- **WHEN** `javert template validate M2` runs after this change
- **THEN** validation reports `ready` status (not `empty` / `partial` / `error`); all required fields present; jinja2 syntax valid

#### Scenario: M2 template can render with vars

- **WHEN** `javert prompt-fit R151 --template M2 --vars docs/m2_R151_vars.json --dry-run` runs after this change
- **THEN** rendering succeeds without `UndefinedError`; output contains the 6-step audit logic + 8+ indication diagnoses + min_count=2 reference + single-count INCONCLUSIVE clause

#### Scenario: M2 template conditional branches work

- **WHEN** vars include `drug_check: true` + `drug_kw_list: [...]`
- **THEN** rendered prompt includes "特殊条件: 若 search_fees(keyword 任一为 ...)" clause
- **WHEN** vars include `drug_check: false` (or omit)
- **THEN** rendered prompt does NOT include any drug-check clause

### Requirement: M3 template loadable and complete

After this change, `configs/templates/M3.yaml` SHALL contain a complete template definition for "口腔串换" (dental hijack): a non-empty `master_prompt` with Jinja2 conditionals supporting dental_dept_check / self_pay_bonus_list / total_amount_threshold / special_notes / pilot_caveat, plus `keywords_template`, `tools_template`, `signal_template`, and a `fields` list covering 14-16 field declarations.

`load_template("M3")` MUST return a valid `Template` instance (validation passes), `master_prompt` length MUST be >= 800 characters, and `fields` MUST include at minimum: `hijacked_surgery_desc`, `hijacked_surgery_kw_list`, `catalog_basis`, `trivial_dx_kw_list`, `supporting_dx_kw_list`, `dental_dept_check`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M3 template validation passes

- **WHEN** `javert template validate M3` runs after this change
- **THEN** validation reports `ready` status (not `empty` / `partial` / `error`); all required fields present; jinja2 syntax valid

#### Scenario: M3 template can render with vars

- **WHEN** `javert prompt-fit R245 --template M3 --vars docs/m3_R245_vars.json --dry-run` runs after this change
- **THEN** rendering succeeds without `UndefinedError`; output contains the 7-step audit logic + dental_dept_check clause + supporting_dx + trivial_dx + 自费项加分

#### Scenario: M3 template subclass support

- **WHEN** vars include `subclass: "G-a"` (美容修复套医保大手术)
- **THEN** rendered prompt logic works the same way; subclass is metadata-only and does not break rendering
- **WHEN** vars include `subclass: "G-b"` (普通诊治虚增手术)
- **THEN** rendering similarly succeeds

### Requirement: M5 template loadable and complete

After this change, `configs/templates/M5.yaml` SHALL contain a complete template definition for "虚构医药服务" (phantom service): a non-empty `master_prompt` with Jinja2 conditionals supporting `dept_check` / `supporting_dx_kw_list` / `special_notes` / `pilot_caveat` / `inconclusive_addendum`, plus `keywords_template`, `tools_template`, `signal_template`, and a `fields` list covering 12 field declarations.

`load_template("M5")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 700 characters, and `fields` MUST include at minimum: `service_desc`, `service_kw_list`, `catalog_basis`, `execution_evidence_kw_list`, `notes_section_hints`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M5 template validation passes

- **WHEN** `javert template validate M5` runs after this change
- **THEN** validation reports `ready` status; all required fields present; jinja2 syntax valid

#### Scenario: M5 template can render with vars

- **WHEN** `javert prompt-fit R134 --template M5 --vars docs/m5_R134_vars.json --dry-run` runs after this change
- **THEN** rendering succeeds; output contains the 6-step audit logic (fee 命中检测 → notes 执行证据检索 → 缺失则 V)

### Requirement: M6 template loadable and complete

After this change, `configs/templates/M6.yaml` SHALL contain a complete template for "过度诊疗" (overtreatment): `master_prompt` with Jinja2 conditionals supporting `exclusion_dx_list` (hard-evidence VIOLATION) / `notes_evidence_section` / `notes_evidence_kw_list` / `special_notes` / `pilot_caveat`, plus 13 fields.

`load_template("M6")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 600 characters, and `fields` MUST include: `treatment_desc`, `treatment_kw_list`, `catalog_basis`, `indication_dx_list`, `exclusion_dx_list`, `aux_keywords`, `aux_tools`, `aux_signal`.

#### Scenario: M6 template validation passes

- **WHEN** `javert template validate M6` runs after this change
- **THEN** validation reports `ready` status

#### Scenario: M6 exclusion_dx hard-evidence branch

- **WHEN** vars include `exclusion_dx_list: ["非全麻"]` 
- **THEN** rendered prompt includes "若有任一排除指征命中 + treatment 命中 → VIOLATION (强证据)" clause
- **WHEN** `exclusion_dx_list: []` (空)
- **THEN** the exclusion clause is still present in template but with empty list (no items)

### Requirement: M4 template loadable and complete

After this change, `configs/templates/M4.yaml` SHALL contain a complete template for "超标准收费" (overcharge): `master_prompt` with quantitative calculation guidance (catalog unit vs actual event count), plus 14 fields including `service_name`, `catalog_unit`, `addon_rule_text`, `violation_pattern`, `actual_event_kw_list`, `expected_calc_hint`.

`load_template("M4")` MUST return a valid `Template` instance, `master_prompt` length MUST be >= 800 characters.

#### Scenario: M4 template validation passes

- **WHEN** `javert template validate M4` runs after this change
- **THEN** validation reports `ready` status

#### Scenario: M4 template can render with vars

- **WHEN** `javert prompt-fit R074 --template M4 --vars docs/m4_R074_vars.json --dry-run` runs
- **THEN** rendering succeeds; output contains catalog 条款 (单位=次, 基础价 399, 加成规则原文) + 6 步审计逻辑

### Requirement: 模板渲染的手改保护

prompt-fit 渲染写盘时 MUST 在 rule yaml 内嵌记录本次渲染产物的 hash (`render_hash` 字段, 对 `prompt_addon` 规范化后取 sha256). 下次 prompt-fit 写盘前 MUST 比对当前 on-disk `prompt_addon` 的 hash 与已记录的 `render_hash`:

- 二者不一致 (说明 `prompt_addon` 自上次渲染后被人工修改) → MUST 拒绝覆盖并提示用 `--force` 显式绕过, 保护专家手改不被静默清除;
- `render_hash` 缺失 (旧规则或非模板来源) → 视为「未知来源」, MUST 仅警告不拦截 (向后兼容), 本次渲染后补齐 hash;
- 一致 → 正常覆盖.

`--force` MUST 无条件放行覆盖并重写 `render_hash`.

#### Scenario: 手改后拒绝覆盖

- **WHEN** 某规则曾由模板渲染 (已存 `render_hash`), 之后 `prompt_addon` 被人工修改, 再次执行 prompt-fit (无 `--force`)
- **THEN** 拒绝写盘并提示手改冲突 + `--force` 绕过方式 (exit code 非 0)

#### Scenario: --force 绕过

- **WHEN** 同上冲突场景但带 `--force`
- **THEN** 覆盖写盘并更新 `render_hash` 为新渲染产物的 hash

#### Scenario: 缺 hash 仅警告

- **WHEN** 规则无 `render_hash` 字段 (旧规则) 且 `prompt_addon` 非空, 执行 prompt-fit
- **THEN** 打印「未知来源」警告, 正常覆盖并首次补齐 `render_hash`

#### Scenario: 一致则静默覆盖

- **WHEN** 规则 `prompt_addon` 的 hash 与 `render_hash` 一致 (自渲染后未被改)
- **THEN** 正常覆盖写盘 (无警告无拦截)

### Requirement: M1 规则声明 precheck 可读的结构化 A/B 项目集

M1 (`derived_from_template: M1`) 规则的 Rule 模型 MUST 支持可选 `precheck` 结构化字段 (`a_items` / `b_items` 两个字符串列表), 供确定性预检读取, 与渲染进 `prompt_addon` 的自由文本解耦。存量 M1 规则的 `precheck` 块 MUST 可由既有 `prompt_addon` 中规整的 `A 类 (...)` / `B 类 (...)` 项目名一次性抽取生成; 抽取不到完整 A、B 项目集的规则 (已被人工改写为非标准形态者) MUST 被跳过而非猜测填充, 保留其原路径。

#### Scenario: 规整 M1 规则抽取出 precheck 块

- **WHEN** 对 `prompt_addon` 含 `A 类 (主项手术): "…" / "…"` 与 `B 类 (附属手术): "…"` 的 M1 规则跑迁移
- **THEN** 该规则 yaml 获得 `precheck.a_items` / `precheck.b_items` 两个非空列表, 内容为引号内项目名

#### Scenario: 已改写 M1 规则被跳过

- **WHEN** 对 A 或 B 类项目集抽取不到完整两组的 M1 规则 (如带跨日期比对逻辑的改写规则) 跑迁移
- **THEN** 该规则 MUST NOT 写入 `precheck` 块, 迁移输出将其列入「跳过」清单, 其审计仍走原 LLM 路径

### Requirement: precheck 字段向后兼容

新增 `precheck` 字段 MUST 为可选 (默认无), 不得破坏既有 rule yaml 的加载/写回 (`rule_loader` / `rule_writer` round-trip) 或非 M1 规则。

#### Scenario: 无 precheck 字段规则正常加载

- **WHEN** 加载一条不含 `precheck` 字段的既有规则 yaml
- **THEN** 加载成功且 `rule.precheck` 为空 (None), 该规则一切行为不变

