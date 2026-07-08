# add-workbench-sql-raw-source

## Why

工作台原文/费用/检验展示 (`/api/patient/{pid}/raw` + 病案概览) 目前只读 62 本机 CSV (base + `data_import/` overlay 全量进内存): 数据靠人肉导出/scp/合并同步, 批次患者不在文件里就 404 (szx2.0 4680 患者实发); 500MB+ CSV 整体加载吃 2-4GB RAM 且新数据必须重启服务才可见。医院部署场景数据持续产生、量无上限, 文件拷贝链路不可持续 — 需要"审计结果在哪个库, 原文就从哪个体系查"的单一数据源。

## What Changes

- 新增 SQL 原文数据源: 按患者号实时查 142 `TP_data_hub` 国标 TB_* 表 (文书/费用/化验/诊断/手术), 逐患者按需、毫秒级索引查询, 不整库加载
- TB_* → 内部列契约映射从 `scripts/etl_from_data_hub.py` 提取为共享模块, 脚本与工作台共用一份映射 (不复制粘贴两处漂移)
- 工作台读数改为**链式回退**: 先查本机 CSV (老演示患者, 零行为变化) → 未命中再查 hub SQL (新批次患者) → 双 miss 才 404
- 配置开关 (env, 默认关闭): 不开启则行为与现状完全一致; 62 开启后 szx2.0 类批次患者原文即查即得, 无需拷文件/重启
- 逐患者小 LRU 缓存 (进程内), 同一患者反复点开不重复查库
- 审计侧 (`audit-patient` 工具链 / DataLoader) **不动**, 仍走 ETL 快照 — 审计需全患者数据高频反复检索, 本地 CSV 合理; SQL 化属后续独立 change

## Capabilities

### New Capabilities

- `workbench-raw-source`: 工作台患者原文/费用/检验/主诊断展示数据的来源与回退契约 — CSV(base+overlay) 与 hub SQL 的链式查找次序、开关行为、未命中语义、缓存与降级 (SQL 不可用时不阻断 CSV 路径)

### Modified Capabilities

(无 — data-access 等审计侧 spec 的需求不变, 本次只动工作台展示读数路径)

## Impact

- **代码**: `src/javert/web/api/routes_workbench.py` (loader 获取处接链式源), 新增 `src/javert/store/hub_raw_source.py` (或同级), 共享映射模块提取自 `scripts/etl_from_data_hub.py` (脚本改为 import 共享模块, 行为回归一致), `src/javert/config.py` (+hub 源开关/连接配置 env)
- **API**: `/api/patient/{pid}/raw` 响应结构不变; 404 语义收窄为"CSV 与 hub 都查不到"
- **部署**: 62 `.env` 加开关 + hub 连接配置; 142 `TP_data_hub` 零 schema 改动 (只读); 建议核对 JZLSH 索引
- **依赖**: 无新增 (pyodbc/SQLAlchemy 已在用)
- **风险**: hub 网络抖动 → 降级为仅 CSV + 日志告警, 不影响老患者; 首版不做连接池调优 (单进程低并发)
