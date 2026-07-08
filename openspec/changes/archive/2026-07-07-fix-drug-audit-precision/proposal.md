# fix-drug-audit-precision

## Why

v2.1 批次实测药品规则精准度 38.7% (非药品 76.7%/97.7% 达标), 假阳性集中在 RD20/R007. 2026-07 系统扫描 (`docs/系统扫描与优化方案_2026-07.md` 主线一) 定位出三个相互独立、恰好都砸在药品规则上的工程缺陷, 均为小改, 且 62 上已有 v2.1 对照基线可直接验证修复效果——这是"如果只做一件事"级别的第一优先.

## What Changes

- **工具结果分段截断**: `runner.py:36` 的 2000 字符尾截断改为「必留头部 + 可截明细」——`drug_audit_lookup` bulk 输出的『患者病案首页诊断 ground truth』块与 `search_notes` 的 `[否认段]/[选项框]` 反向语义告警划入必留段, 截断只砍明细段并附截断提示; `_TRUNCATE` 提进 `llm.yaml` 可调
- **conf 闸补洞**: `verdict_gate.py:319` ③ 闸从 `conf ∈ [floor, ceiling)` 改为 `conf < ceiling` 的 VIOLATION 一律降 INCONCLUSIVE (含 confidence 缺失归 0 的情况), 消灭 "VIOLATION conf 0.00" 自相矛盾落库行
- **R205 移出⑥存在性闸**: R205 是"1 台手术按次多收全麻"的计数问题, 麻醉真实存在恰是其违规前提, 现闸把真超收 V→CLEAN (gate 制造的假阴性); 只改 `configs/verdict_gate.yaml`
- **M8 精选 28 条收敛**: RD10-RD37 判定逻辑与 R007/RD01-03 bulk 逐字相同, 同患者同药会各计一次 V (违规数结构性放大 + 32 条近同文本并行维护); 精选条目 status 降 abandoned + notes 说明, bulk 4 条独占药品审计, 重建 router index
- **对照验证**: 62 重跑 v2.1 药品批次同患者集, 对比修复前后 V 数与精准度, 结论补进 docs

## Capabilities

### New Capabilities
- `tool-result-integrity`: 工具结果截断的分段保全契约 (必留段不被截、明细段可截、截断可感知、上限可配)
- `verdict-gate`: 裁决后确定性闸的 conf 底线语义 + 计数类规则不适用存在性闸

### Modified Capabilities
- `drug-audit`: 药品审计由 bulk 规则独占, 精选单药规则不再独立产出裁决 (消重复计违规; 以 ADDED requirement 形式补充, 不改既有「用药命中扣除全退药」要求)

## Impact

- 代码: `src/javert/audit/runner.py` (`_truncate`), `src/javert/tools/drug_audit_lookup.py`, `src/javert/tools/search_notes.py`, `src/javert/audit/verdict_gate.py`, `configs/verdict_gate.yaml`, `configs/llm.yaml` + `src/javert/config.py`, `configs/rules/RD10-RD37` 28 个 yaml, `data/router/javert_rules_index.json` (重建)
- 数据口径: 药品类 V 计数将显著下降 (消重复 + 消假阳), 向专家/院方汇报时需说明口径变化
- 验证依赖: 62 上 v2.1 批次对照基线 (`docs/v2_1_gate_drug_analysis.md`)
- 不影响: 模型/endpoint、M8 模板本体、非药品规则语义
