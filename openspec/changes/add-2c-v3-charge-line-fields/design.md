## Context

v2 将规则级结果投影为 `cards[]`，并将实际费用/药品命中投影为 `matched_items[]`。当前命中项按 `(code, name, occurrence_time)` 去重，只携带编码、名称和发生时间；同一项目同一时刻的多条收费明细会被折叠。费用源表和 `shi_fee` 已稳定提供 `cnt`、`pric`、`acord_dept_codg/name`、`orders_dr_code/name`，但这些字段在不同医院可能为空。

## Goals / Non-Goals

**Goals:**

- 提供独立 v3 路径，完整继承 v2 卡片并追加收费明细行字段。
- 对同 code/name/time 的多条收费行逐行返回，不任取第一条。
- 数量和单价使用 JSON number 或 `null`；编码、名称使用 string，缺值为空字符串。
- 保持 `matched_items` 与 `hit_codes/hit_names/hit_times` 的等长同索引关系。
- 保持 v1/v2 路径和响应不变。

**Non-Goals:**

- 不修改审计裁决、Router、SQLite/SQL Server 持久化结构。
- 不回填医生或科室主数据，不猜测缺失编码/名称。
- 不在 v3 中改变异步提交、轮询、attempt 或三态裁决语义。

## Decisions

1. **新增 `/api/audit/v3/submit` 与 `/api/audit/v3/results/{SYXH}`。** v3 submit 复用 `_submit_2c`，results 复用 v2 卡片副本再做收费行级富集。相比原地扩展 v2，这能让已按 v2 固定结构入库的调用方不受字段和数组基数变化影响。

2. **从生成当前响应的同一 `fee_df` 关联收费行。** 关联条件为患者费用原始名称、国家码优先/院内码兜底、以及通过现有 `_format_v2_occurrence_time` 归一后的发生时间。不得在返回阶段跨到另一个实时数据源按名称补值，以免不同快照串行。

3. **按实际收费行展开。** 每个 v2 matched item 找到 N 条符合条件的正数量收费行时，v3 返回 N 个元素；每个元素继承 v2 字段并追加6个字段。若没有正数量行，则使用符合条件的行；仍无法关联时保留原 matched item，新增数字字段为 `null`、文本字段为空字符串，确保不丢卡和不丢命中。

4. **不按新增字段去重。** 收费源中的两条行即使展示字段完全一致也分别返回；调用方如需业务聚合，应在展示层明确处理。兼容 `hit_*` 数组由展开后的 `matched_items` 同一次遍历生成。

5. **数字类型稳定。** `cnt`、`pric` 经有限数值转换后输出 JSON number；空值、NaN、Infinity 输出 `null`。科室与医生字段只做字符串清理，不做字典补全。

6. **文档明确 running 是增量结果。** v3 文档逐项解释 `progress.total/completed/failed`，并要求调用方只在 `status=done` 后将本轮结果视为完整，避免再次存入中间快照。

## Risks / Trade-offs

- [同一命中项展开后数组长度增加] → v3 独立版本隔离；文档要求按 `matched_items[]` 一对多处理。
- [医生/科室字段在部分医院为空] → 字段定义为可空/空字符串，不猜值；保留源数据事实。
- [费用时间格式存在斜杠歧义] → 复用现有 v2 单值格式化函数，保证 v2/v3 关联口径一致；不在本 change 扩展日期语义。
- [历史卡无法关联当前费用快照] → 保留原命中项并返回空新增字段，不删除历史结果。

## Migration Plan

1. 部署代码、文档和测试，不改数据库 schema。
2. 重启 62 Web 服务，验证 systemd、HTTP、SQL/Hub 健康。
3. 对去标识化夹具和受控生产样例验证 v1/v2 不变、v3 字段与拆行正确。
4. 2C 切换到 v3 后继续按 `status=running` 轮询，只有 `done` 才落完整结果。
5. 回滚时恢复部署前代码并重启；v1/v2 始终可用，2C 可临时切回 v2。

## Open Questions

无。产品已确认同项目同时间存在多条收费明细时按收费行拆分。
