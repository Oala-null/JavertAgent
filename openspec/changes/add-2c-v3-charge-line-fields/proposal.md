## Why

2C 目前只能从 v2 命中项目取得编码、名称和发生时间，无法展示该收费行的数量、单价、开单科室和开单医生。现有源表与内部费用契约已具备这些字段，但 v2 按 `(code, name, occurrence_time)` 去重会合并同一时刻由不同科室或医生开立的收费行，因此需要一个收费明细行级的新契约。

## What Changes

- 新增独立的 2C v3 submit/results 路径，复用 v1/v2 的异步任务、幂等和进度语义。
- v3 `cards[].matched_items[]` 在 v2 全部字段基础上追加数量、单价、开单科室编码/名称、开单医生工号/名称。
- v3 按实际收费明细行投影命中项目；同一 code/name/time 下存在不同收费行时分别返回，避免任取第一条或错误合并。
- 保留 v1/v2 路径和既有字段行为，不修改既有客户端契约。
- 新增 v3 对接文档、自动化契约测试和 62 部署/回滚说明。

## Capabilities

### New Capabilities

- `two-c-card-contract-v3`: 覆盖 v3 独立端点、收费明细行级命中字段、拆行与空值语义，以及 v1/v2 向后兼容要求。

### Modified Capabilities

无。

## Impact

- 代码：`src/javert/web/api/routes_audit.py`、`src/javert/web/middleware.py`。
- 测试：`tests/test_routes_2c.py` 及中间件相关契约测试。
- 文档：新增 `docs/2c对接_javert审计服务_v3.md`，并更新 2C 文档入口、部署手册和变更记录。
- 上游数据：复用 `shi_fee` 的 `cnt`、`pric`、`acord_dept_codg/name`、`orders_dr_code/name`；字段缺失时使用类型稳定的空值。
- 下游：2C 应继续按顶层 `cards[]` 保存卡片，并将 `matched_items[]` 视为一对多收费明细。
