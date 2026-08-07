## Why

`TP_data_hub` 新增了与现有 `TB_*` 契约结构一致、以 `desus_` 开头的隔离表，但 Javert 当前把表名写死为无前缀 `TB_*`，无法只读取这批脱敏数据并完成单患者审计。

## What Changes

- 新增 Hub 表名前缀配置，默认空值保持现有 `TB_*` 取数行为不变。
- 让批量 ETL 与工作台实时 Hub 源统一通过受校验的前缀读取 `desus_TB_*`。
- 让混合患者 workbench 按最新 `batch_tag` 选择显式配置的只读 Hub profile，使 `desus` 原文页签不改变其他患者的数据源。
- 为前缀表 SQL、配置覆盖和默认兼容行为增加离线测试。
- 将审计所用收费快照确定性解析出的真实命中项目随结果持久化，使 workbench 回放不依赖另一套实时数据源。
- 仅对 `desus` 清单中的唯一脱敏病人运行审计，并以 `batch_tag=desus` 写入 workbench 结果库。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `data-access`: Hub 数据访问支持配置安全的表名前缀，同时维持既有内部列契约和无前缀默认行为。

## Impact

- 影响 `src/javert/config.py`、`src/javert/data/hub_source.py`、结果持久化、批量 Hub ETL、SQL Server tag 查询和工作台 HubRawSource 的共享取数链路。
- 新增环境变量配置；不改变数据库结构、不写 `TP_data_hub`，结果仍写入既有 SQLite/SQL Server workbench 存储。
- 运行阶段需要只读访问 `TP_data_hub.desus_*`，并将审计结果以 `desus` tag 持久化。
