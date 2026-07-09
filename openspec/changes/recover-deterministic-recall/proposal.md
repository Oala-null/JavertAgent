# Proposal: recover-deterministic-recall (确定性召回回收: 闸口径 + 证据保真 + 漂移网)

## Why

专家 FN 归因中三类漏检的根子全是**确定性机制**, 不需要任何 LLM 改动即可回收:

1. **gate ②单次闸计数口径错**: 211419211×R155, LLM 判 V 被闸以"净次数=1"降 C — 11 项细胞因子单日打包恰好算 1 次, 而单日打包正是该规则的违规形态。全库 **463 条"单次放过" V→C** (占 gate 动作 89%) 沉在工作台默认视图之外, 专家连驳回机会都没有。
2. **证据层失真**: search_fees 不净退费 (overview 净, 两处口径打架, LLM 拿错事实); 数量 0.75 被取整为 0 (同切口 75% 计价审计的关键数字); R063 prompt 仍写过时 50% (现行 75%, LLM 算 expected 永远错)。
3. **重跑漂移黑洞**: R063 三轮 V→I→C 完整漂移; 2026-07-08 晨重跑 211440399, R063/R155 两个老 V 静默翻 C — 历史 V 消失无人知晓。

三者共同特性: 零 LLM、单测可证、天级交付、各自独立回滚 — 与 LLM 行为面改动 (另 change) 风险剖面完全不同, 故单独成 change。

## What Changes

- **单次闸场景化**: 套餐类规则 (R155/R151/R132) ②闸计数从"收费次数"改"同日不同项目数"; 套餐规则闸触发时降 I 不降 C (I 是召回网, C 是黑洞); 非套餐规则行为逐字不变 (闸当初消掉的 78 假阳性不还回去)。
- **463 条存量重筛**: 确定性重扫脚本, 达阈值的翻回 I 进专家队列, 原地 UPDATE + 可逆标签, dry-run 先出翻转量分布再落。
- **gate 击杀可见**: 工作台"被闸降级" facet, 专家可抽查闸的击杀记录。
- **证据层三修**: search_fees 退费净额化 + 注记; 数量小数保真; R063 prompt 50%→75%。
- **drift guard**: persist 层老 V 新 C → 落 I + 「漂移防护(历史曾判V)」标签 (verdict_gate 的镜像: 只升到 I 不升回 V); 专家已驳回的 V 不复活; 存量翻转只出报告不自动改。

## Capabilities

### New Capabilities

- `verdict-drift-guard`: 重跑裁决漂移防护 — 老 V 新 C 的落库合并规则 (I + 历史标签), 不改写任何历史行。

### Modified Capabilities

- `verdict-gate`: ②单次闸对套餐类规则改计数口径 (同日不同项目数); 套餐规则闸降级目标 C→I; 存量重筛可逆; gate 降级行对工作台可见可筛。
- `tool-result-integrity`: search_fees 输出退费净额化 + 退费注记; 费用数量小数保真。

## Impact

- **代码**: `audit/verdict_gate.py` + `configs/verdict_gate.yaml`; `tools/search_fees.py` (+loader 聚合点); `store/result_persister.py`; web sidebar facet; 新 `scripts/rescreen_gated.py` + `scripts/drift_report.py`; `configs/rules/R063.yaml` prompt 修正。
- **存量数据**: 463 条 gate 降级行重筛 (sqlite + 142 双侧, 只翻 C→I + 可逆标签, 不增删行不碰 review)。
- **验收**: 依赖 `add-fn-regression-library` 先行 — FN-003 重筛后翻 I, FN-005 不退化; 漂移报告把 211440399×R063 送专家裁定, 结果回填 FN-004 (可能为 CLEAN 误判修正锚)。
- **风险**: 重筛放量超预期 → dry-run 分布定阈值, >100 条收紧; drift guard 误拦"新代码修对了" → 只升到 I + 标签可裁 + 驳回豁免。
- **不影响**: runner LLM loop、router、三态 review 流、2C SSE 契约、非套餐规则的闸行为。
