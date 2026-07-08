# code-based-triggering Specification

## Purpose
TBD - created by archiving change make-rules-code-portable. Update Purpose after archive.
## Requirements
### Requirement: 规则触发编码维度 (编码命中 OR 名称命中)

rule yaml MAY 声明 `trigger_codes` (医保目录编码前缀 / 类别 token 列表, 默认空). router 预筛判定单条规则命中 MUST 为「名称命中 (trigger_keywords 弹性匹配) **或** 编码命中」的**并集**: 编码命中 = 该规则任一 `trigger_code` 是患者任一费用行编码 token (`med_list_codg` 国标码 / `medins_list_codg` 本院码 / 类别标签) 的前缀. `trigger_codes` 为空时编码命中 MUST 恒为假, 使命中语义与本 change 之前的纯名称匹配逐字一致 (零漏检回归).

#### Scenario: 编码命中在名称不同名时仍召回

- **WHEN** 某规则 `trigger_codes` 含国标编码前缀 `C03`, 患者一条检查费的 `med_list_codg` 以 `C03` 开头但项目名是本院异名 (trigger_keywords 不命中)
- **THEN** 该规则被 router 保留 (编码路径命中)

#### Scenario: 无编码规则行为不变

- **WHEN** 某规则未声明 `trigger_codes` (空)
- **THEN** 该规则命中与否 MUST 与仅用 trigger_keywords 名称匹配的旧行为逐字相同

#### Scenario: 名称命中仍独立生效

- **WHEN** 某规则 trigger_keywords 名称命中患者费用, 但无 `trigger_codes` 或编码不命中
- **THEN** 该规则仍被保留 (名称路径命中, OR 语义)

### Requirement: trigger_codes 格式校验

`build_rule_mapping.py` 重建 index 时 MUST 校验 `trigger_codes` 每项去空白后 `len ≥ 2` 且非空; 存在过短 (< 2) 或纯空白项 MUST 报错拒绝写 index (防短码前缀泛滥误召回).

#### Scenario: 过短编码拒绝

- **WHEN** 某规则 `trigger_codes` 含单字符项 `"C"`
- **THEN** `build_rule_mapping.py` 报错, index 不写入该错误状态

### Requirement: gate 检查关键词读显式字段

rule yaml MAY 声明 `exam_keywords` (单次/存在性闸匹配 fee 行用的检查名列表). `verdict_gate.extract_exam_keywords` MUST 优先返回非空的 `rule.exam_keywords`; 该字段缺省时 MUST 回退现有行为 (grep prompt_addon 的「检索关键词」行 → trigger_keywords), 使未填规则零回归.

#### Scenario: 显式字段优先

- **WHEN** 规则声明了非空 `exam_keywords`
- **THEN** `extract_exam_keywords` 返回该字段值, 不再 grep prompt_addon

#### Scenario: 未填字段回退旧链

- **WHEN** 规则未声明 `exam_keywords`
- **THEN** `extract_exam_keywords` 返回值 MUST 与本 change 之前 (grep prompt_addon → trigger_keywords) 逐字一致

