# data-access Specification

## Purpose
TBD - created by syncing change fix-fee-refund-netting. Update Purpose after archive.
## Requirements
### Requirement: 退费净额聚合

数据访问层 SHALL 提供按项目的退费净额聚合, 使「该费用是否用过 / 收了几次 / 明细」语义按 `+N/-N` 净额计算。系统 MUST 按 `med_list_codg` (无码退项目名) 分组对 `cnt` 求和, 净额 ≤ 0 的项 MUST 从「用过」判定与明细展示中剔除; 净额 > 0 的项 SHALL 显示净量。原始全量 fee 行 (含退费) MUST 仍可访问 (审计留痕)。

#### Scenario: 完全充退项不计不显示

- **WHEN** 某药 `地佐辛(易可定)注射液` 共 12 行、跨 5 个日期, 但 `sum(cnt) = 0`
- **THEN** net helper 判该项净量 0, `search_fees` 明细**不**列该项、计数**不**含该项, 工作台费用区也不显示

#### Scenario: 部分退显示净量

- **WHEN** 某项收 `+2` 后退 `-1` (净 1)
- **THEN** 该项保留, 显示净量 1, MUST NOT 整条剔除

#### Scenario: 计数等于净不同收费

- **WHEN** `住院诊疗费` 共 40 行、含 7 次退费、净 33、覆盖 33 个不同日期
- **THEN** helper 暴露该项 `net_qty=33` 与 `distinct_billing_dates=33`, 供次数门控使用

#### Scenario: search_fees 计数不再虚高

- **WHEN** 患者某费用项有退费行, 调 `search_fees(keyword=...)`
- **THEN** 返回的「共 N 条」与明细按净额计, 不含被抵消的退费行

#### Scenario: 原始退费历史仍可访问

- **WHEN** 调用方需要查看「曾收曾退」的原始行
- **THEN** 原始全量 fee 访问 (`all_fees`) 仍返回含退费行的完整数据, 净额聚合是独立显式调用

### Requirement: 费用行患者归属精确匹配

费用数据的患者归属匹配 MUST 采用两级精确语义: ① 键与患者号完全相等; ② 复合键 (`{hospital_code}-{patient_id}` 形态) 的末段与患者号完全相等 (容忍首尾空白). 系统 MUST NOT 因子串包含关系把长住院号的费用行归入短住院号患者.

#### Scenario: 短号不吃长号

- **WHEN** 数据中同时存在患者号 `123` 与 `1123`, 查询患者 `123` 的费用
- **THEN** 结果只含键精确为 `123` (或复合键末段为 `123`) 的行, 不含 `1123` 的任何费用行

#### Scenario: 复合键患者照常命中

- **WHEN** 外部医院数据以合成复合键 `0003-211530148` 存储, 查询患者 `211530148`
- **THEN** 该患者全部费用行正常返回

#### Scenario: 现有基线患者行为不变

- **WHEN** 对 62 现网患者集合 (含 J66252 等基线患者) 用新旧匹配逻辑分别取费用
- **THEN** 除子串误归属的修正外, 命中集合一致

### Requirement: 费用行明细字段暴露

`search_fees` 的行输出在源数据存在对应列时 MUST 包含 单价与数量 (形如 `单价×数量`) 以及 开单科室/开单医师; 源数据缺列时对应字段 MUST 整体省略 (不出现空占位符), MUST NOT 报错. 行锚 `⟨行=i⟩` 与既有列的文本 MUST 保持不变 (只追加).

#### Scenario: 量价信号可见

- **WHEN** 患者费用行含 单价 86.00、数量 3, LLM 调用 `search_fees`
- **THEN** 该行输出含 `86.00×3` 形态的量价信息, 合计金额列不变

#### Scenario: 科室医师可见

- **WHEN** hub/内部数据带开单科室与开单医师列
- **THEN** 行输出含科室与医师, 串换科室/分解收费类规则可据此审计

#### Scenario: 缺列优雅省略

- **WHEN** 外部医院 CSV 未提供单价/科室/医师列
- **THEN** 行输出与本 change 之前一致 (无新增字段、无占位符、无报错)

### Requirement: 费用分类优先官方类别标签

`search_fees._classify` MUST 优先读费用行的官方类别标签 `medins_chrgitm_type` (data-hub 路径已将 MXFYLB 2 位国标码回填为同款中文, 故跨院可移植): 标签含「手术」→手术类; 含「西药/中药/中成药」→药品类; 含「材料/耗材」→耗材类; 含「CT/检查/化验/拍片/病理/影像/超声/检验」→检查类. 标签缺失或落在模糊类 (治疗/床位/护理/其他/麻醉…) 时 MUST 回退现有名称关键词启发式. 本院自定义数字码 `med_chrgitm_type` MUST NOT 参与分类 (实测为非国标脏码). 类别列缺失时输出 MUST 与本 change 之前 (纯名称启发式) 一致.

#### Scenario: 药品按标签不再落其他类

- **WHEN** 某药品费用行 `medins_chrgitm_type` = 「西药」
- **THEN** 归入「药品类」, 不再因名称未命中关键词而落「其他类」

#### Scenario: 造影归类不靠 dict 顺序

- **WHEN** 某「造影」项目 `medins_chrgitm_type` = 「检查」(或「拍片」)
- **THEN** 归入「检查类」(按标签), 不再取决于 `_CATEGORY_KEYWORDS` 中手术类/检查类的字典顺序

#### Scenario: 缺类别列名称兜底

- **WHEN** 费用数据无 `medins_chrgitm_type` 列 (老 CSV / 外部院未供)
- **THEN** 分类结果 MUST 与本 change 之前的名称关键词启发式逐字一致

