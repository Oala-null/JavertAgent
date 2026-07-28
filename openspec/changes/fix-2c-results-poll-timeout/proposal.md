## Why

2C 在轮询已完成患者时，Javert 会为每次 GET 重新解析全部审计卡片和费用命中；现网 64 张卡、428 条命中项的响应耗时约 35–47 秒，超过调用方 30 秒读超时并造成连续失败。结果投影是同一 attempt 内可复用的确定性数据，应避免重复计算。

## What Changes

- 为 v2/v3 结果投影增加 attempt 内的增量卡片缓存；服务重启后的 SQLite 历史回放按
  run 快照摘要隔离缓存，同一快照的 `run_id` 只构建一次。
- 为同一患者的并发轮询增加单飞保护，避免多个超时请求同时重复占用 CPU。
- 新 attempt 与旧 attempt 的缓存严格隔离；v1 响应字段和 v2/v3 JSON 契约不变。
- 增加大结果集和重复/并发轮询回归测试，并记录匿名性能日志以便现网验收。

## Capabilities

### New Capabilities

- `two-c-results-poll-performance`: 规定 v2/v3 轮询结果的增量复用、attempt 隔离、并发一致性和响应性能门禁。

### Modified Capabilities


## Impact

- 代码：`src/javert/web/api/routes_audit.py`
- 测试：`tests/test_routes_2c.py`
- 接口：`/api/audit/v2/results/{SYXH}`、`/api/audit/v3/results/{SYXH}`；仅性能和内部执行方式变化，无字段删除、改名或类型变化
- 部署：62 Web 服务需要备份源码、重启和现网计时验收；不涉及数据库 schema 变更
