# Proposal: add-fn-regression-library (FN 回归案例库)

## Why

专家协查发现 5 例问题案例 (`Javert问题汇总.md` + 211318013 关节松动), 归因分布在 4 个互不重叠的漏检层 (规则覆盖空白 / gate 误杀 / 证据层 bug / 规则粒度); 其中 4 例为专家已裁定的假阴性, 1 例 (211440399×R063) ground truth 待裁 (75% 收费疑似正确, 老 V 疑似证据 bug 所致假阳性)。项目此前所有机制单向压假阳性, 召回侧**零度量** — "减少假阴性、增大召回"当前是一句无法验证的话。进院前的每一项召回改动 (后续 `recover-deterministic-recall` / `add-fabrication-burden-of-proof` 两个 change) 都需要一个先行的、可复跑的验收依据; 已修复的案例 (211318013×R225 细化) 也需要 anchor 防退化。

## What Changes

- 新增 FN 回归案例库: 专家裁定的每例案例登记为一个可执行 yaml (`患者 × 规则 × 期望裁决 × 期望证据要素 × 归因`), 首批 4 例; expected_verdict 允许 CLEAN (误判修正锚)。FN-004 编号保留, 待专家裁定 R063×211440399 后回填。
- 新增回归 runner: 逐例 dry-run 真实 audit, 三档判定 (full/partial/miss), catch 率 = 召回的直接度量; hub 患者自动取数。
- 跑出当前代码的基线报告, 与人工归因表互相印证。

本 change **纯增量**: 不改任何生产代码路径、不动规则、不碰数据库。

## Capabilities

### New Capabilities

- `fn-regression-library`: FN 案例登记格式、回归 runner、三档 catch 率报告与退化拦截。

### Modified Capabilities

(无 — 不改既有能力的行为)

## Impact

- **新增**: `tests/fn_cases/*.yaml` (首批 4 例) + `scripts/fn_regression.py` + `docs/fn_baseline.md`。
- **依赖**: 复用 `javert dry-run` 路径与 `etl_from_data_hub` 取数, 均零改动。
- **下游**: `recover-deterministic-recall` 与 `add-fabrication-burden-of-proof` 以本库案例升档为验收判据。
- **风险**: 回归跑真 LLM 有漂移 → 严重度下限断言 + partial 档缓冲; 抖动记录本身是漂移问题的证据。
