## Why

费用里有**退费行** (负 `cnt` + 负金额), 实测前 50 万行约 **3.8%** 是负行, 且与正行成对 (`住院诊疗费 +1/-1` 同日净 0; `地佐辛注射液` 12 行 → 净 0 = 完全充退从没真用)。但工具**不看符号**:
- `search_fees` keyword/类别明细**逐行列出含退费行**并报「共 N 条」→ 数量虚高;
- `drug_audit_lookup` 收集药名时**完全无符号意识** → 充退过的药仍算「用过」, 产生假阳性。

退费噪音真实污染了被评审案例 (退费样本最多的患者 `J13365` 正是 zhoulihong 批注的那位)。用户要求「退费 +1/-1 在明细里去掉」。**且 `②单次就给过` 门控 (Change C) 依赖先净退费** —— 地佐辛有 12 行、5 个不同日期, 不净掉会被误判「多次收费」, 净掉才知是 0 次。

## What Changes

- `data-access` (DataLoader / 共享 util) 新增**退费净额聚合** helper: 按项目 (优先 `med_list_codg`, 无码退项目名) `sum(cnt)`, **净额 ≤ 0 的项整条从「用过/明细」剔除**, 部分退显示净量。
- helper 同时暴露 per-item `{net_qty, distinct_billing_dates}` (净正收费的不同日期数), 供 Change C 次数门控「确保是不同的两次收费」。
- `search_fees` keyword/类别/明细模式改用净额: 不再列退费行、计数 = 净收费次数 (总额本就 `sum` 自动净, 现让**计数与明细**也一致)。
- `drug_audit_lookup` 用药命中**扣除全退药** (净 ≤ 0 的药不算患者用过)。
- `patient_overview` (`fee_categories` items / `fees_sum_map`) 与 `hit_resolver` fee 行展示走净额, 工作台不再显示被抵消的收费。
- 原始 `all_fees` 保留 (审计留痕 / 需看退费历史的场景), 净额是显式聚合而非静默改写。

## Capabilities

### New Capabilities

<!-- 无新 capability -->

### Modified Capabilities

- `data-access`: 新增退费净额聚合契约 —— 费用「用过/计数/明细」语义按 `+N/-N` 净额计, 暴露净正收费的不同日期数。
- `drug-audit`: 用药命中须扣除全退药 (完全充退的药不计入患者用药集)。

## Impact

- **代码**: `src/javert/data/loader.py` (或新 `src/javert/data/fee_netting.py` 共享 util) 落 net helper; `src/javert/tools/search_fees.py` (明细/keyword/类别计数净额)、`src/javert/tools/drug_audit_lookup.py` (用药集扣全退药)、`src/javert/web/patient_overview.py` (fee_categories/fees_sum)、`src/javert/web/hit_resolver.py` (fee 行净额) 全部改走 helper。
- **数据/配置**: 零变化 (只在读取时聚合, 不改 csv)。
- **依赖关系**: **Change C (`add-verdict-gate-layer`) 的 ②单次门控依赖本 change** —— net helper 的 `distinct_billing_dates` 是「净不同收费次数」的真值来源, 本 change 须先落地。
- **验证**: `J13365` 回归 (地佐辛净 0 不再算用过 + 不再进明细; 住院诊疗费 40 行 → 净 33)。
