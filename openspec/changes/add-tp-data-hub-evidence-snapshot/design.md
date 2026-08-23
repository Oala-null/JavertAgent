## Context

`hub_source.py` 当前直接从 SQL Server 多次读取费用、文书、诊断并投影为 `shi_fee/case_notes/shi_zd`。这适合在线取数，但不能证明两个架构 arm 使用同一输入。2026-08-23 只读实测显示：`sh_yb_platform` 与 `TP_data_hub` 目标表结构和主键一致，但数据量有小幅差异；两库 `SNAPSHOT_ISOLATION=OFF`、RCSI/CDC/Change Tracking/temporal 均关闭且无 rowversion。`sh_yb_platform` 当前账号在关键表上 SELECT=true、DML=false；`TP_data_hub` 当前账号 DML=true。

原始表提供了可靠 lineage：IH 诊断 `(YLJGYQDM,ZYZDLSH)`、病案主表 `(YLJGYQDM,SYXH)`、病案诊断 `(YLJGYQDM,SYXH,ZDXH)`、文书 `(YLJGYQDM,WSLSH)`、出院小结 `(YLJGYQDM,JZLSH)`、费用 FS `(YLJGYQDM,SFMXID,STFBZ)`、费用 EXT `(YLJGYQDM,SFMXID)`。现有 canonical projection 丢失了部分原始键，但无需修改原表或 DataLoader 合同；snapshot 可把 raw stable-key artifacts 和 existing canonical views 同时冻结。

## Goals / Non-Goals

**Goals:**

- 以真正 SELECT-only 的 source profile 从 `sh_yb_platform` 或显式只读开发镜像读取小规模 cohort。
- 生成不可覆盖的 raw/canonical/lineage/manifest snapshot，提供稳定 composite snapshot ID。
- 让 legacy A 与 structured B 后续只读同一 canonical snapshot，并让 B 可追到原始 SQL 表和主键。
- 保留 READ COMMITTED 非原子窗口的真实语义，不把它宣传成数据库 point-in-time snapshot。
- 用合成门禁和五候选只读 smoke 验证权限、文件模式、清理、checksum 和无 PHI summary。

**Non-Goals:**

- 不启用 Snapshot Isolation、CDC、Change Tracking、temporal、Kafka 或数据库 DDL。
- 不运行 verdict A/B、不写 Workbench、不新增 evaluation 表、不部署 62。
- 不修改 `hub_source` canonical 输出列、DataLoader、规则、Router、SSE 或 2C。
- 不把 `TP_data_hub` 与 `sh_yb_platform` 当作同一数据版本，也不跨库拼接一个 snapshot。
- 不在 Git 保存真实 snapshot、患者列表、原始主键或病历正文。

## Decisions

### D1 — Source profile 是安全合同，不只是 database 参数

定义 allowlisted profile：`sh_yb_platform-readonly` 默认指向 `sh_yb_platform`；`tp-data-hub-readonly` 指向 `TP_data_hub`。连接使用现有环境凭据和 `ApplicationIntent=ReadOnly`，但在首次患者查询前对每张必需表执行 `HAS_PERMS_BY_NAME`：SELECT 必须为 true，INSERT/UPDATE/DELETE 必须全 false。任一失败即终止且不创建 snapshot 目录。

不提供 `--allow-writable-source` 逃生参数。当前 TP 开发账号会被阻断；若要用该库，必须换 SELECT-only 登录。数据库名必须来自 profile allowlist，不能接收任意标识符或从缺表 profile 静默回退。

### D2 — Raw 稳定键行本身就是 canonical SourceArtifact

每个源表按已声明主键排序，选择当前 RD04/canonical 映射实际需要的最小列，流式写 canonical JSON Lines。每行 canonical JSON 计算 row fingerprint；artifact digest 对文件字节计算 SHA-256。`lineage.jsonl` 为每行记录 artifact ID、row ordinal/fingerprint、database/table、PK column names/private values、selected source columns、query/transform version。PK values 属 PHI-classified 私有工件，不进入 public summary。

相较“先生成 case_notes 再猜 WSLSH”，从 raw key artifact 出发不会丢 lineage。现有 canonical CSV 是从同一读取窗口生成的兼容 projection，不是 source of truth。

### D3 — v0.1 固定九张最小源表

必需表为：医院映射、费用 FS/EXT、MEDICAL_DOCUMENT、LEAVEHOSPITAL_SUMMARY、DRADVICE_DETAIL、IH_DIAGNOSIS_DETAIL、BA_SYJBK、BA_SYZDK。每表 spec 固定 PK、selected columns、order by 和 query version。缺表、缺 PK/列、PK 空值、重复 PK 或结果未按 PK 单调排序均 fail closed。

Canonical projection 继续复用 `fetch_candidate_frames`/`hub_source` 产生 `shi_fee.csv`、`case_notes.csv`、`shi_zd.csv`，并按稳定列排序后写出。由于 SQL Server 当前无 snapshot isolation，raw 与 canonical 查询可能来自同一 READ COMMITTED 窗口而非同一事务时点；manifest 必须记录开始/结束时间和 `atomic_snapshot=false`。

### D4 — Snapshot ID 只由内容决定

Snapshot bundle：

```text
snapshot/
  manifest.json
  raw/<table>.jsonl
  canonical/{shi_fee,case_notes,shi_zd}.csv
  lineage/source_rows.jsonl
```

`snapshot_id` 由 source profile/database、query/schema versions、cohort query checksum、每个 raw/canonical/lineage artifact digest 组成的 canonical payload计算，不包含生成时间或绝对路径。相同内容重复生成必须得到同一 snapshot ID；manifest 自身记录查询时间窗口但不参与内容身份。

输出目录必须不存在或为空，创建为 0700，文件 0600。异常时删除本次新目录；已完成 snapshot 不允许原位覆盖、update 或 append。

### D5 — Cohort 发现复用 RD04 查询，但选择与快照分离

复用 `insurance_oncology_drugs` 和 `discover_coarse_candidate_ids` 的候选口径。合成测试可显式传安全 ID；真实 smoke 从只读 source 内存发现、排序后取前五个候选，只在 0700 临时目录处理。cohort query version/checksum、发现数和选取数进入 manifest/summary，原始 ID 不落公共输出。

五例 smoke 只验证 source/lineage/snapshot mechanics，不代表代表性 cohort、SHADOW 样本或临床效果。正式 30+ case cohort 由后续 versioned EvaluationPlan 另行分层选择。

### D6 — Manifest 与 summary 分离 PHI

私有 manifest 可以引用私有 artifact 相对路径和 digest，但不得记录患者号、PK values、病历正文、凭据或 salt；私有 PK values 只存在 lineage 文件。public summary 进一步只保留 profile/database、query window、atomic flag、表/投影行数和 digest、snapshot ID、permission status、错误码。

运行结束前扫描发现到的原始 patient IDs 和关键 PK values，确保它们没有出现在 manifest/summary。smoke 使用临时 workspace，成功与异常均递归清理；只返回内存中的无 PHI summary。

### D7 — A/B 接口只交付 snapshot，不接裁决

Snapshot manifest 暴露 `snapshot_id`、canonical artifact digests 和 source consistency。后续 `EvaluationPlan.source_snapshot_checksum` 必须引用 composite snapshot digest；A/B 两臂加载相同 canonical 目录。`historical_unpaired` Workbench 行不属于本 change，也不得由 snapshot exporter写入或读取。

PROMOTION 仍要求 DBA database snapshot/backup 或开启 Snapshot Isolation；`atomic_snapshot=false` 的 bundle 可用于 CONFORMANCE/工程 SHADOW，但必须保留跨表非原子风险说明。

## Risks / Trade-offs

- [READ COMMITTED 跨表不原子] → 先物化后两臂共读，保证比较公平；manifest fail-loud 标注，PROMOTION 前换 DBA 原子快照。
- [文书体量和 PHI] → 小 cohort、流式 JSONL、0700/0600、无公共原文、异常清理。
- [canonical 查询与 raw 查询窗口内漂移] → 分别 checksum/计数并记录查询窗口；关键 coverage 不一致则 snapshot INVALID。
- [当前 TP 账号可写] → 无 override 的 DML 权限 preflight 阻断；默认使用已验证只读的 sh profile。
- [五例按排序取样有偏] → 明确仅是工程 smoke；效果评测使用后续分层 EvaluationPlan。

## Migration Plan

1. 实现 source profile、表 spec、权限/schema preflight 与合成连接测试。
2. 实现 raw JSONL/lineage/manifest writer 和 deterministic snapshot ID。
3. 接入现有 candidate discovery/canonical projection，完成合成端到端。
4. 对 `sh_yb_platform` 执行五候选只读临时 smoke，核对 summary 后自动清理。
5. 更新运维/A-B 文档并运行全仓门禁。该 change 无数据库迁移、生产启用或回滚数据；回滚只需删除新增离线代码/命令。

## Open Questions

无阻塞问题。正式 PROMOTION 使用 DBA snapshot、backup restore 还是启用 Snapshot Isolation，由后续运维 change 与 DBA 决定。
