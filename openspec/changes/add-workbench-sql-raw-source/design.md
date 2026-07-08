# design — add-workbench-sql-raw-source

## Context

工作台原文展示当前 100% 依赖 62 本机 CSV: `routes_workbench._get_loader()` 起 `CsvLoader(base, overlay_dir=data_import)` 单例, 全量进内存; lab/exam loader 同构。患者不在文件里 → `/api/patient/{pid}/raw` 404。szx2.0 批次 (4680 患者) 的原文只存在于 142 `TP_data_hub`, 修复靠 1GB 级文件 scp+合并+重启 — 医院部署不可持续。

`scripts/etl_from_data_hub.py` 已实现全部 6 类数据的 TB_* → 内部列契约映射 (fee ⋈ EXT / 文书 / 诊断 / 手术 / 检验 ⋈ REPORT / 检查 RIS∪RIS2), 且查询天然支持按 JZLSH 过滤 (`in_clause`) — 逐患者按需查询所需的映射逻辑已经存在, 只是被锁在批量脚本里。

约束: 62 工作台进程同时服务专家审核, 改动不能影响现有患者的展示行为; 142 只读, 零 schema 改动。

## Goals / Non-Goals

**Goals:**
- 工作台原文/费用/检验/主诊断对 hub 内患者即查即得: 不拷文件、不重启、内存 O(患者)
- CSV 与 SQL 共存: 现有患者 (base+overlay) 展示行为逐字节不变
- 映射逻辑单一来源: 脚本与工作台 import 同一模块, 不出现两份映射漂移
- 开关默认关: 未配置的部署 (含测试环境) 行为与现状完全一致

**Non-Goals:**
- 审计侧 (audit-patient / DataLoader / 10 工具) 不切 SQL — 全患者高频反复检索, 本地快照合理, 属后续独立 change
- 不做连接池/并发调优 (单进程低并发工作台, pyodbc 短连接或单连接复用够用)
- 不迁移病案概览的全部聚合 (sidebar 富卡片 fees_sum 等仍走现有 142 runs + CSV 路径); 本次只覆盖"点开患者后"的原文/费用/检验/主诊断取数

## Decisions

**D1 — 链式回退次序: CSV 先, hub SQL 后。**
`raw` 取数先查现有 CsvLoader (内存命中, 零延迟), notes+fees 双空才落到 hub SQL。理由: 现有患者零行为变化 + 零新增延迟; sy 患者在 CSV 和 hub 都存在, CSV 先保证渲染与历史一致。备选"SQL 优先" (数据最新鲜) 被否: 每次点开都付网络往返, 且 demo 患者展示可能出现细微 diff。

**D2 — 共享映射模块 `src/javert/data/hub_source.py`。**
从 `etl_from_data_hub.py` 提取 6 个 `fetch_xxx(cn, pids) -> DataFrame` 纯函数 (含 MXFYLB2CN/YCTS2FLAG/clean_dt/in_clause), 脚本改为薄 CLI 壳 import 之。回归判据: 提取后脚本对同一患者集产出与提取前逐字节一致 (BOM/列序/行序)。备选"工作台内复制一份映射"被否: 两处映射必然漂移 (data-hub 日期三坑的教训)。

**D3 — 工作台侧适配器 `HubRawSource` (web 层, 薄)。**
持 hub 连接 (lazy, 失败可重建), 暴露与现有消费方同形的接口: `get_notes(pid)/get_fees(pid)` 返回与 CsvLoader 同列 DataFrame; labs/主诊断返回 routes 现用结构。内部调 D2 的 fetch_* (pids=[pid])。患者号大写归一后 JZLSH 精确匹配 (hub 键为裸号; 复合键 bah 由映射层合成, 与 overlay 数据同形, hit_resolver/前端零改动)。

**D4 — 逐患者 LRU 缓存 (maxsize≈32, 无 TTL)。**
出院患者数据静态, 无 TTL; `reset_loader()` 一并清空。防的是同一患者反复点开/锚点跳转重复查库。

**D5 — 配置开关与连接复用。**
`config.py` 加 `hub_raw_enabled: bool = False` (env `JAVERT_HUB_RAW_ENABLED`) + `hub_database: str = "TP_data_hub"` (env `JAVERT_HUB_DATABASE`); server/凭据复用现有 `sql_*` 字段 (同一台 142)。连接串同 `sqlserver_store` 加 `Encrypt=no`。脚本里硬编码 CS 改由 config 构造 (行为不变)。

**D6 — 降级语义: SQL 异常 = miss, 不是 500。**
hub 查询任何异常 (超时/网络/权限) → log warning + 返回空 → 走原 404 分支。CSV 路径永不受 hub 可用性影响。

## Risks / Trade-offs

- [hub 单次查询延迟 (患者 8k 费用行 ~200-500ms)] → 首次点开可接受, LRU 使复跳零延迟; JZLSH 需索引 — 部署清单含幂等 `CREATE INDEX` 核对 (142 只加索引不改 schema)
- [跨院区裸号碰撞 (0001 J 前缀 vs 0003 纯数字, 理论可撞)] → 命中多院区时全部返回 (bah 复合键天然区分展示); 实际命名空间不相交, 不做主动去重
- [提取重构破坏脚本行为] → 提取前跑脚本存 oracle 输出, 提取后 diff 逐字节回归 (freeze-source-table-headers 同款手法)
- [62 进程内存: CSV 大 overlay 与 SQL 并存期翻倍] → 上线验证后可删 62 的 szx2 大 overlay 文件回收 2-4GB (回滚备份已在)

## Migration Plan

1. Mac dev: 实现 + 单测 + 本地 `--no-mssql` 冒烟 (开关开, 查 szx2.0 患者)
2. 62 部署 (开关不配 = off), 服务行为不变 → `.env` 加 `JAVERT_HUB_RAW_ENABLED=true` → 重启
3. 验证: 点一个**不在** overlay 的 hub 患者原文正常; 点 J66252 与改前一致
4. 观察一批后 (可选) 删 62 大 overlay 文件回收内存
5. 回滚: env 开关置 false + 重启, 一步回到纯 CSV

## Open Questions

- examinations (RIS) 是否首版就接: szx 无 RIS 数据, sy 有 — 倾向接 (同构 fetch 已有, 增量小), tasks 里作为独立可裁项
- `_get_main_diagnosis` 现读 `data/shi_zd.xls` 硬路径, 顺手接 hub zd 还是单独小修 — 倾向本 change 内接 (同一个 404 体验问题)
