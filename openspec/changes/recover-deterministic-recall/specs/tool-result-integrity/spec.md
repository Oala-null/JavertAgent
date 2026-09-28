# tool-result-integrity Spec Delta

## ADDED Requirements

### Requirement: search_fees 退费净额与注记

`search_fees` 喂给 LLM 的费用行 MUST 按项目名聚合退费后净额: 净额 ≤ 0 的项目 MUST NOT 作为收费证据输出; 发生过退费抵消的项目 MUST 在行尾附加「含 N 次退费已抵消」注记。净额口径 MUST 与 `patient_overview` 费用聚合及 precheck 充退剔除一致 (统一到共享 helper, 不得三处三个口径)。输出 MUST 保持行式形态 (只改聚合行内容, 不改输出结构), 既有规则 prompt 无需适配。

#### Scenario: 211440399 式退费不再被当双收

- **WHEN** 患者某手术项目收费 2 行、退费 1 行 (净 1 次), LLM 调 search_fees 检索该项目
- **THEN** 工具结果呈现净次数 1 + 「含 1 次退费已抵消」注记, 不再呈现"出现 2 次"

#### Scenario: 完全充退项不出现

- **WHEN** 某项目收费与退费完全抵消 (净额 ≤ 0)
- **THEN** search_fees 结果不含该项目行 (与 precheck 剔除口径一致)

### Requirement: 费用数量小数保真

费用行的数量字段在工具输出与工作台呈现中 MUST 保留小数 (如同切口次要手术 75% 计价的 0.75), MUST NOT 取整为 0 或 1。该保真 MUST 覆盖 csv 与 hub ETL 两条数据链。

#### Scenario: 0.75 数量原样可见

- **WHEN** 费用行数量为 0.75 (同切口 75% 计价), LLM 或专家查看该行
- **THEN** 数量呈现 0.75, 不是 0.0 或 1.0
