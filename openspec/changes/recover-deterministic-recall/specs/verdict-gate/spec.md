# verdict-gate Spec Delta

## ADDED Requirements

### Requirement: 套餐类规则单次闸改计数口径

对配置为套餐形态的规则 (`configs/verdict_gate.yaml` 的 `panel_rules` 节; R155 已实证入集, 其余候选逐条核实违规形态确为"多项目单日打包"才入集), ②单次闸的计数 MUST 按「同日不同项目名数」(该规则费用关键词族命中的净正收费项目) 而非收费发生次数; 同日不同项目数达到该规则 `min_distinct_items` 阈值时 ②闸 MUST NOT 触发 (VIOLATION 保留)。非套餐规则的②闸行为 MUST 保持不变。费用数据不可得时 MUST fail-open 维持原口径评估 (不因数据缺失放大降级)。

#### Scenario: 11 项细胞因子单日打包不再被"单次"放过

- **WHEN** R155 判 VIOLATION, 患者同日收取 11 个不同细胞因子测定项目 (净额均为正), 收费发生仅 1 次
- **THEN** ②闸按同日项目数=11 (≥ min_distinct_items) 判定不触发, VIOLATION 保留落库

#### Scenario: 非套餐规则口径不变

- **WHEN** 不在 panel_rules 内的规则判 V 且净收费次数=1
- **THEN** ②闸按既有"次数"口径照常评估 (触发则降 CLEAN, 行为与本 change 之前一致)

### Requirement: 套餐规则闸降级目标为 INCONCLUSIVE

panel_rules 内的规则被②闸降级时, 降级目标 MUST 为 INCONCLUSIVE (进专家队列) 而非 CLEAN; 非套餐规则维持降 CLEAN 不变。

#### Scenario: 套餐规则项目数不足仍进专家队列

- **WHEN** R155 判 V, 同日不同项目数=2 (< min_distinct_items=3), ②闸触发
- **THEN** 降级为 INCONCLUSIVE + 单次闸标签, 工作台默认视图可见

### Requirement: 存量单次放过行可重筛且可逆

系统 MUST 提供存量重筛脚本: 选 `gate_tag` 为「单次放过」的行, 仅对 panel_rules 内规则按新口径从费用数据确定性重算, 达阈值的行原地 UPDATE verdict CLEAN→INCONCLUSIVE 并把 gate_tag 更新为内嵌原值的可逆标签。脚本 MUST 支持 `--dry-run` 输出翻转量分布; MUST 跳过已有任何专家 review 的行并报告; MUST NOT 增删行、MUST NOT 触碰 review 表。

#### Scenario: dry-run 先出翻转量

- **WHEN** 以 `--dry-run` 运行重筛脚本
- **THEN** 输出按规则分组的将翻转行数与同日项目数分布, 不写任何库

#### Scenario: 211419211×R155 被回收

- **WHEN** 重筛实跑后查看患者 211419211 的 R155 行
- **THEN** verdict 为 INCONCLUSIVE, gate_tag 为可逆标签, LLM 原始 V 推理原样保留, 工作台默认视图可见

#### Scenario: 重筛可一键还原

- **WHEN** 重筛已落库后运行反向脚本
- **THEN** 凭可逆标签把翻转行恢复为原 verdict 与原 gate_tag

### Requirement: gate 降级行对专家可见可筛

工作台 MUST 提供按 gate 降级筛选的入口 (facet 或等价过滤), 使专家能列出并抽查被闸降级的裁决行 (含降级标签与 LLM 原始推理)。

#### Scenario: 专家抽查闸的击杀记录

- **WHEN** 专家在工作台启用"被闸降级"筛选
- **THEN** 列表只显 gate_tag 非空的行, 卡片可见降级标签与原始 V 推理
