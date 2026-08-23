## Why

Javert 已能从 `TP_data_hub`/`sh_yb_platform` 实时映射费用、文书和诊断，但两库数据量不同，且都未启用 snapshot isolation、CDC、Change Tracking、temporal 或 rowversion；分别实时运行 A/B 不能证明两臂读取了同一输入。需要把一次受控只读查询物化为不可变、带原始主键 lineage 和 checksum 的 Evidence Snapshot，供 legacy 与 structured 路径共同消费。

## What Changes

- 新增严格 source profile：默认正式源为已实测 SELECT-only 的 `sh_yb_platform`；`TP_data_hub` 仅作为开发镜像，必须使用同样的只读账号才可运行 snapshot。数据库名不得静默回退或互换。
- 在读取任何患者数据前验证目标库 allowlist、必需表、稳定主键和对象权限；关键表任一 `INSERT/UPDATE/DELETE=true` 即 fail closed，`ApplicationIntent=ReadOnly` 不替代权限门禁。
- 复用现有 RD04 候选发现和 `hub_source` canonical 映射，从同一查询窗口物化小规模 cohort 的 raw SourceArtifacts、`shi_fee/case_notes/shi_zd` canonical projection、upstream lineage sidecar 与 snapshot manifest。
- Raw SourceArtifact 按源表主键确定性排序并保存 row ordinal/fingerprint；lineage sidecar 为每个 raw artifact row 记录原始 database/table/primary-key columns、私有 key values、source columns、row checksum 和 transform/query version。
- Manifest 记录 source profile/database、query/schema/code versions、查询起止时间、隔离能力、`atomic_snapshot=false`、每表主键/行数/schema/content checksum、canonical/lineage checksum 和 composite snapshot ID；不把 READ COMMITTED 查询窗口冒充数据库原子快照。
- 快照输出只能写入新建的 Git 外 0700 目录和 0600 文件，不允许覆盖；公共 summary 只含 source profile、行数、digest、稳定错误码和时间窗口，不含患者号、主键值、病历原文、凭据或 salt。
- 增加完全合成门禁与获授权的 `sh_yb_platform` 五候选只读 smoke；smoke 临时 PHI 工作区成功/异常均清理，只保留无 PHI summary。
- 该 change 不运行 A/B verdict、不写 `sh_yb_platform`/`TP_data_hub`/`zadig`、不改 `audit_runs`、Runner、Router、SSE、2C、规则或生产配置；后续 EvaluationPlan 只能引用此 snapshot ID。

## Capabilities

### New Capabilities

- `hub-evidence-snapshot`: SQL Server source profile 权限门禁、稳定主键驱动的不可变 cohort snapshot、raw/canonical/lineage/manifest 工件、隐私清理和同输入 A/B 交接合同。

### Modified Capabilities

无。

## Impact

- 新增独立的 snapshot 模块、显式 CLI、合成 fixtures/tests 和运维说明；复用 `hub_source`、`run_oncology_shadow_batch` 候选查询与现有 Evidence Contract canonical serialization。
- 实测正式 source profile：`sh_yb_platform` 关键表 SELECT=true、DML=false；开发库 `TP_data_hub` 当前账号 DML=true，因此会被严格 preflight 阻断，除非改用只读凭据。
- Source snapshot 是 PHI 工件，只能位于受控 Git 外路径；仓库只保存合成 fixture、schema 和无 PHI 测试报告。
- 不新增第三方依赖、CDC、Kafka、图数据库或 SQL Server DDL；PROMOTION 仍需 DBA database snapshot/backup 或启用 snapshot isolation，超出本 change。
