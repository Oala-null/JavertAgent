## Context

`shi_fee.csv` 有 `cnt` 列 (数量), 退费表现为**负 `cnt` + 负 `det_item_fee_sumamt`**, 与正行成对。实测:
- 前 50 万行约 **3.8%** 为负行 (`cnt<0` 与 `amt<0` 行数完全相等, 符号一致)。
- `住院诊疗费 +1/-1` 同日 → 净 0; `地佐辛(易可定)注射液` 12 行 / 5 个不同日期 → **净 0** (完全充退); `酮咯酸氨丁三醇` 10 行 / 4 日期 → 净 0。
- `住院诊疗费` 40 行 → 净 33 次 / 33 个不同日期 (有退费, 行数虚高 7)。

现状: `search_fees` 总额 `sum(_amount)` 已自动净 (正负相抵), 但 **keyword/类别明细逐行列出** (含退费行) 且报「共 N 条」; `drug_audit_lookup` 按药名出现计数, 完全不看符号。

## Goals / Non-Goals

**Goals:**
- 净额 ≤ 0 的项**不进明细、不算「用过」**; 部分退显示净量。
- 计数语义 = 净不同收费次数 (兼顾 fee 行净额 + 不同日期), 暴露 `distinct_billing_dates` 给 Change C。
- 单点 net helper, `search_fees` / `drug_audit_lookup` / `patient_overview` / `hit_resolver` 全链路复用。

**Non-Goals:**
- 不改总额计算 (本就自动净)。
- 不删原始行: `all_fees` 原样保留 (审计留痕 / 看退费历史)。
- 不做 ②单次门控本身 (那是 Change C, 本 change 只供数据)。
- 不处理药品同名串味 (Change A 正交)。

## Decisions

- **D1 按 `med_list_codg` 分组 (无码退项目名) `sum(cnt)`, 净 ≤ 0 → 全退 → 剔出「用过」与明细**。
  - *Why*: 码是稳定键 (与 Change A 同源, 同药不同名也能聚到一起); 名退化兜底。*Alt*: 按项目名分组 (改名重收会漏聚) —— 仅作无码兜底。

- **D2 helper 暴露 per-item `{net_qty, distinct_billing_dates}`** (`distinct_billing_dates` = 净正收费覆盖的不同 `fee_ocur_time` 日期数)。
  - *Why*: 用户明确「走不同 fee 行数、日期也兼顾、确保是不同的两次收费」。地佐辛 5 个日期但净 0 → 证明**必须先净再数日期**, 单看日期数会把全退误判多次。

- **D3 净额在 data-access 层做 (loader / `fee_netting.py` util), 不在每个 tool 各做**。
  - *Why*: 单点真值。`raw all_fees` 保留给需要退费历史的场景, 净额是显式 `net_fees(patient_id)` 调用而非静默改写 `all_fees`。

- **D4 跨日退费仍按 item **全周期净**, 不要求同日配对**。
  - *Why*: 退费时点未必当天 (charge 8/8, refund 9/8 都可能); 用户「+1 -1」指整段净。同药全周期 `sum(cnt)` 即可。

## Risks / Trade-offs

- [部分退 (+2 -1 = 净 1) 被误整条剔除] → 只剔 `净 ≤ 0`; 净 > 0 显示净量, 不剔。
- [退费跨 item 名 (改名重收)] → 按 `med_list_codg` 分组可捕获同药不同名; 无码项按名兜底, 漏配处标 `needs_review`。
- [总额已自动净、计数/明细原本没净 → 三者不一致] → 统一全部走 helper, 消除不一致。
- [审计留痕: 净掉退费后看不到「曾收曾退」] → 明细以脚注式标注「(含 N 次退费, 已抵消)」, 不展开退费行 (见 Open Q)。

## Migration Plan

1. 落 `net_fees` helper + 单测 (地佐辛 → 净 0 剔除; 住院诊疗费 → 净 33 / 33 日期)。
2. 切 `search_fees` keyword/类别明细 + 计数走净额。
3. 切 `drug_audit_lookup` 用药集扣全退药。
4. 切 `patient_overview` (fee_categories/fees_sum) + `hit_resolver` fee 行。
5. 回归: `J13365` 端到端 (工作台费用区 + 审计 trace 均无被抵消项); `uv run pytest tests/ -v` 全绿。
- **回滚**: helper 为新增, 各消费点切换可逐个回退到 raw。

## Open Questions

- 净后明细要不要保留一行「(含 N 次退费, 已抵消)」审计留痕? → 倾向脚注式标注 (净量旁加计数), 不展开退费行。
- `distinct_billing_dates` 的「同日多次真实收费」(非退费, 如同日两台手术) 算几次? → 默认按**不同日期**数 (保守, 避免把同日合理两次拆成误报); Change C 门控可在此基础上再判。
