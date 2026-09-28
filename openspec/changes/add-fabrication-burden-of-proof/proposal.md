# Proposal: add-fabrication-burden-of-proof (M5 虚构类举证倒置 + 配套缺失预检)

## Why

专家 FN 归因中两例属**规则覆盖空白**, 且共享同一深层缺陷 — M5 虚构类的举证方向反了:

1. **211351896**: 收「经皮穿刺脑血管腔内溶栓术」¥1100, 手术记录明确是取栓无溶栓操作, 全费用单零溶栓药 — 143 条规则无一含"溶栓", 纯覆盖空白。
2. **211427558**: 收「经胃镜特殊治疗」¥400, 操作记录仅胃镜检查+染色, 抬举征阴性后未做任何治疗 — R132 只管色素过度检查 (且判对了), "治疗费收了但没做"无规则接。

当前 M5 类 prompt 的隐含语义是"文书没提 → 存疑 → CLEAN"; 自查自纠语境下应反过来: **收了治疗/手术费, 文书拿不出执行证据, 本身至少是记录不全** — 默认 INCONCLUSIVE 进专家队列, 正面反证才 CLEAN。I 是召回网, 不动 V 的标准, 不伤 V 的信用。

案例①还有一个确定性信号被浪费: "收溶栓术 + 全费用单零溶栓药"不需要 LLM 判断 — 术式↔必备配套的费用交叉是纯数据比对, precheck 引擎 (M1 那套) 现成可套, 只缺一个 companion 模式。全目录内涵解析 (造影×2/球囊含在取栓否) 确实大、等 `add-catalog-loader`; 但高价术式配套窄表很小, 且案例①就是合作医院的真实分布。

本 change 与 `recover-deterministic-recall` 分开: 这里全是 **LLM 行为面** (模板语义 + 新规则 prompt 迭代), 调优周期不可预期, 不应扣住确定性修复的交付。

## What Changes

- **M5 举证责任倒置**: `configs/templates/M5.yaml` master prompt 加裁决语义 — 治疗/手术费存在 + 文书无执行证据 → 默认 I (非 C), 正面反证才 C; 重渲染 M5 派生规则。
- **companion 预检模式**: precheck 引擎加 `mode: companion` (A=术式, B=必备配套): A 无→clean 零 LLM; A 有 B 无→注入确定性事实块, LLM 只核文书反证; 双有→skip 走原路径不注偏置。缺省 mode=coexist, 既有 M1 规则零行为变化。
- **2 条覆盖空白规则**: 脑血管介入溶栓虚构 (companion precheck: 溶栓术↔溶栓药) + 内镜治疗虚构; 优先细化 0325 清单既有 H 类条目, 无归属才新建。
- **名称不匹配 prompt 缓解**: 模板层加一行警示 (收费名与文书术式名常不一致, 禁止因名称不同断言未收费/未做) — 案例④c 的短期缓解, 映射表明确不做。
- **~20 条高价术式配套小表**调研存档 (素材库, 本 change 只消费溶栓 1 条)。

## Capabilities

### New Capabilities

- `fabrication-burden-of-proof`: M5 虚构类证据不足默认 I 的裁决语义 + 溶栓/内镜治疗 2 条规则。

### Modified Capabilities

- `deterministic-precheck`: 新增 companion 模式 (A 存在 + 配套 B 缺失 → facts 注入), 既有 coexist 语义逐字保留。

## Impact

- **代码**: `audit/precheck.py` + `audit/rule.py` (mode 字段); `configs/templates/M5.yaml` + M5 派生规则重渲染; 新增/细化 2 条规则 yaml; router index 重建; `docs/companion_术式配套表.md`。
- **验收**: 依赖 `add-fn-regression-library` — FN-001/002 从 miss 至少升 partial; FN-005 不退化; M5 既有规则抽查无回归。
- **风险**: 默认 I 语义推高 I 量稀释专家注意力 → 仅"零执行证据"分支生效 + FN 回归监控 I 率; companion facts 引导偏 V → 事实块只陈述费用事实, 判定语义留给规则 prompt。
- **不影响**: runner 引擎、gate、router 决策、既有 coexist precheck 规则。
