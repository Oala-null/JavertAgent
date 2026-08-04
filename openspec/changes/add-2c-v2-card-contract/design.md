## Context

2C v1 已提供异步提交和轮询，但结果是面向简单列表的扁平结构：CLEAN 不解析命中项目，`hit_codes` 与 `hit_names` 分别去重，无法表达同一项目在多个日期发生，也不足以直接还原 Web 工作台的规则问题、行为大类、命中限制和肿瘤资格面板。J70782 的 RD04 最新结果为 CLEAN，证据中已有“注射用维泊妥珠单抗”，但 v1 仅对 VIOLATION/INCONCLUSIVE 调用命中解析器，因此药品未进入出参。

现有工作树包含其他肿瘤知识库和 Web 改动；本 change 必须采用只增不删的最小改动，避免覆盖已有修改。2C 接口仍为内网免登录系统间调用，v2 必须纳入同等精确路径白名单，不能扩大其他 `/api/audit` 路径的访问范围。

## Goals / Non-Goals

**Goals:**

- 保留 v1 原路径和返回结构，新增 `/api/audit/v2/submit` 与 `/api/audit/v2/results/{SYXH}`。
- 复用同一任务表、worker、attempt_id 和历史回放，不重复执行审计。
- v2 对 CLEAN/VIOLATION/INCONCLUSIVE 都解析并返回结构化卡片。
- 使用命中对象数组作为关联真相源，并从该数组生成严格同索引的 code/name/time 三数组。
- 从患者实际费用行的 `fee_ocur_time` 提取项目发生时间；同项目多日期形成多个对象。
- 直接透传完整 `eligibility_evaluation`，覆盖医保限定条件、scope、proof tree、来源和文书建议。
- 对 v2 展示数据递归清理“暂未描述”占位文本。

**Non-Goals:**

- 不改变 Router、规则选择、裁决、verdict gate 或肿瘤资格求值逻辑。
- 不回填历史 `eligibility_json`；历史记录没有结构化资格数据时仍返回 `null`。
- 不为串换类别臆造监管编码。
- 不新增数据库表或列，不改变 v1 的 CLEAN 命中为空行为。

## Decisions

### 1. 使用独立 v2 URL，共享执行状态

v2 使用 `/api/audit/v2/*`，避免依赖请求头协商，也避免现有客户端误解析新结构。v2 submit 调用与 v1 相同的内部提交函数，运行中的同一患者复用当前 `attempt_id`。

备选方案是给 v1 增加查询参数或直接追加字段；前者容易被代理缓存混淆，后者仍无法清晰表达卡片级结构，因此不采用。

### 2. `matched_items[]` 是命中关联的唯一真相源

每个元素至少包含 `code`、`name`、`occurrence_time`，并附带 `code_nat`、`code_local`、`source`、`matched_fee_name`、`restriction`、`review_note`。`hit_codes`、`hit_names`、`hit_times` 仅为方便 2C 落地的等长投影，必须由 `matched_items` 同一次遍历生成。

费用发生时间不写入现有 `HitItem` 缓存模型，而在 v2 序列化时用已解析命中项精确关联患者费用行，避免改变 v1 和工作台的既有去重行为。相同 code/name 在多个不同 `fee_ocur_time` 出现时按时间拆成多项；费用行无法关联时仍保留命中名称，时间为空字符串。

### 3. CLEAN 也解析命中项目

v1 继续保持 CLEAN 的 `hits=[]`。v2 不按 verdict 限制命中解析；只要 evidence/tool_calls 中存在可解析的费用、药品、检验、检查或文书命中就返回。因此 CLEAN RD04 仍能展示被审核药品和“符合限定”的结构化条件。

### 4. 卡片结构以 Web 所需字段为边界

每张 card 返回行为大类 code/title、规则问题、规则元数据、裁决、推理、命中项目、证据、完整肿瘤资格对象和完成时间。内部工具 trace、模型 prompt 和专家审核写操作不属于 2C 展示契约。

### 5. 行为名称映射

纯虚构类及“虚构或串换”混合规则统一映射为 `T380206 / 提供不必要的医药服务`；明确的 `串换项目` 保留独立名称和空编码。包含“过度诊疗”的混合类型继续沿用 `T380201 / 过度诊疗`，避免改变其现有监管归属。

### 6. 占位文案清理

v2 在最终响应序列化前递归清理字符串中的“暂未描述”；清理后无内容的字段按其类型返回 `""`、`[]` 或 `null`。原始数据库证据不被修改，v1 也不受影响。

### 7. 只把实际费用关联项投影为 matched_items

费用/药品 evidence 可能只是记录“检索了 PTCA 但未命中”。解析器为工作台追溯仍可保留这类
锚点，但 v2 的 `matched_items` 与 `hit_*` 只投影能关联到患者实际费用行的项目，避免把
检索词误报成只有名称、没有编码和时间的命中项。

### 8. 不适用是 CLEAN 的适用性子状态

现有持久化和下游统计只有 `CLEAN/VIOLATION/INCONCLUSIVE` 三态，本 change 不新增数据库
裁决枚举。v2 根据持久化 reasoning 中确定性预检的“规则不适用”结论增加
`applicability`；不适用卡仍保持 `verdict=CLEAN`，但展示标签改为“不适用”。普通 CLEAN
仍显示“合规”。

## Risks / Trade-offs

- [证据只有检索词，无法关联原始行] → 保留 evidence/hits 追溯信息，但不投影为 `matched_items`，避免制造 name-only 假命中。
- [同项目多条重复费用造成数组膨胀] → 按 `(code, name, occurrence_time)` 稳定去重，不按金额/数量重复。
- [历史 RD04 没有 `eligibility_json`] → 明确返回 `null`；仅新审计能提供完整限定条件，不用新知识静默改写旧记录。
- [v2 与 v1 共用任务导致客户端重复提交] → running 状态复用 attempt；done 后沿用 v1 的“重新提交即重跑”语义。
- [串换无正式行为编码] → 保持空编码并在文档标注，不自行创造代码。

## Migration Plan

1. 先部署代码、配置和文档，不停止 v1 调用。
2. 运行 v1 回归与 v2 合同测试，确认原有 `/api/audit/submit`、`/results` 响应不变。
3. 2C 开发使用 v2 URL 联调；以 `matched_items` 为主，不自行 zip 独立来源数组。
4. 生产验证 v2 免鉴权仅覆盖两个精确路径，抽查 CLEAN、多个命中及 RD04。
5. 回滚时移除 v2 路由和白名单即可；v1、数据库和历史结果不需迁移。

## Open Questions

- 串换类别的正式行为认定编码尚未提供；当前按用户确认保持独立名称、编码为空。
