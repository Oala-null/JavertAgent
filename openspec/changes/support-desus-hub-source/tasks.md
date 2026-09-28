## 1. 配置与共享取数

- [x] 1.1 新增并校验 `hub_table_prefix` 配置，证明默认空值与环境覆盖行为
- [x] 1.2 将 `hub_source.py` 的固定基表统一接入安全前缀并保持默认 SQL 兼容
- [x] 1.3 让批量 ETL、2C Hub 兜底和工作台实时 Hub 源显式传递前缀配置

## 2. 回归验证

- [x] 2.1 增加非法前缀、默认表名和 `desus_TB_*` SQL 的离线测试
- [x] 2.2 运行相关配置、Hub 映射、工作台与 2C Hub 测试
- [x] 2.3 运行 OpenSpec 严格校验并同步必要文档与变更记录

## 3. 单病人验收

- [x] 3.1 只读核对 `desus_patient_manifest` 唯一患者与所需表存在性
- [x] 3.2 从 `desus_TB_*` 仅导出该患者六域数据并核对患者集合
- [x] 3.3 以 `batch_tag=desus` 运行单患者审计并验证 workbench 结果存储

## 4. 命中项目回放修复

- [x] 4.1 用存储数据复现空命中，并锁定 desus 收费快照可恢复的规则级命中基线
- [x] 4.2 持久化带验证元数据的命中缓存，并保持 SQLite/SQL Server/延迟同步兼容
- [x] 4.3 workbench 直接复用已验证收费缓存，旧缓存继续重验，并补充回归测试
- [x] 4.4 为 backfill 增加患者与 batch tag 定向范围，使用 desus 快照回填现有 43 条 run
- [x] 4.5 验证命中数量、费用定位、tag、裁决和其他患者零改动，更新文档与严格门禁

## 5. 原文对照隔离源修复

- [x] 5.1 用 62 真实 raw API 复现 desus 的 fees/notes/labs 均为 404
- [x] 5.2 新增安全的 batch tag→Hub profile 配置与 latest tag 查询
- [x] 5.3 原文、概览与主诊回退按患者选择 profile，并证明其他患者仍走默认 Hub
- [x] 5.4 配置 62 desus profile，验证三页签、费用锚点、服务与发布门禁
