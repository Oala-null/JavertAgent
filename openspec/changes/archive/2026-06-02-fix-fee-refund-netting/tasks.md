## 1. 净额聚合 helper

- [x] 1.1 落 `net_fees(patient_id)` helper (`src/javert/data/loader.py` 或新 `src/javert/data/fee_netting.py`): 按 `med_list_codg` (无码退项目名) 分组 `sum(cnt)`
- [x] 1.2 helper 返回 per-item `{name, code, net_qty, distinct_billing_dates, has_refund}`, 净 ≤ 0 标全退
- [x] 1.3 保留原始 `all_fees` 不动 (净额是独立显式调用, 不静默改写)
- [x] 1.4 单测: 地佐辛 → 净 0 全退; 住院诊疗费 → net_qty=33 / distinct_billing_dates=33; +2/-1 → 净 1 保留

## 2. search_fees 走净额

- [x] 2.1 keyword 模式: 明细不列退费行、「共 N 条」按净计、合计仍正确
- [x] 2.2 类别明细模式: 净 ≤ 0 项不进列表
- [x] 2.3 目录模式: 各类计数/Top3 按净额 (不被退费虚高)
- [x] 2.4 保留跨天统计 (📅) 但日期按净正收费的日期

## 3. drug_audit_lookup 扣全退药

- [x] 3.1 `lookup_patient_drugs` 构建用药集时调 net helper, 净 ≤ 0 的药不入 `distinct_fees`
- [x] 3.2 单测: 某全退受监管药不出现在 matches

## 4. 工作台前端走净额

- [x] 4.1 `patient_overview.get_fees_sum_map` / `fee_categories` items 用净额 (被抵消项不显示)
- [x] 4.2 `hit_resolver._match_fee_rows` 命中 fee 行排除全退行 (避免锚到已抵消收费)
- [x] 4.3 明细净量旁脚注式标注「(含 N 次退费, 已抵消)」(不展开退费行)

## 5. 回归

- [x] 5.1 `uv run pytest tests/ -v` 全绿 (含 net helper 单测)
- [x] 5.2 `J13365` 端到端: 工作台费用区 + `audit-patient` trace 均无地佐辛/酮咯酸等全退项
- [x] 5.3 确认 `distinct_billing_dates` 字段可被 Change C 次数门控读取 (接口对齐)
