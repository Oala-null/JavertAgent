## ADDED Requirements

### Requirement: Source profile 必须显式且真正只读
系统 MUST 只允许 versioned allowlist 中的 `sh_yb_platform-readonly` 与 `tp-data-hub-readonly` profile，默认使用前者。系统 MUST 在读取患者数据前验证目标 database、必需表及每表 SELECT=true、INSERT/UPDATE/DELETE=false；`ApplicationIntent=ReadOnly` MUST NOT 替代对象权限验证。系统 MUST NOT 提供绕过可写权限的参数。

#### Scenario: sh_yb_platform 只读账号通过
- **WHEN** profile 指向 `sh_yb_platform` 且关键表 SELECT=true、全部 DML=false
- **THEN** preflight 通过并允许进入 cohort discovery

#### Scenario: TP_data_hub 可写账号被阻断
- **WHEN** 任一关键源表的 INSERT、UPDATE 或 DELETE 权限为 true
- **THEN** snapshot 在读取患者数据和创建输出目录前失败
- **AND** 不因数据库属于我方开发库而放宽门禁

### Requirement: 目标表 schema 与稳定主键必须冻结
每个 snapshot query version MUST 声明必需源表、selected columns、稳定复合主键和排序。系统 MUST 验证表/列/主键与声明一致，并 MUST 拒绝缺表、缺列、空主键、重复主键或不按主键单调排序的结果。

#### Scenario: 原始表主键合法
- **WHEN** 文书使用 `(YLJGYQDM,WSLSH)`、IH 诊断使用 `(YLJGYQDM,ZYZDLSH)`、病案诊断使用 `(YLJGYQDM,SYXH,ZDXH)`、费用使用 `(YLJGYQDM,SFMXID,STFBZ)`
- **THEN** raw artifact 可按声明主键确定性排序并逐行编号

#### Scenario: schema 漂移
- **WHEN** 上游删除、改名或改变任一必需列/主键
- **THEN** preflight 返回稳定 schema-drift 错误且不产生部分 snapshot

### Requirement: Raw SourceArtifact 与 lineage sidecar 必须可重放
系统 MUST 将每张源表的最小选定列按主键顺序流式写为 canonical JSONL，为每行记录 zero-based ordinal、row fingerprint 和 row checksum。Lineage sidecar MUST 记录 SourceArtifact、database/table、PK columns/private values、source columns、query/transform version；真实 PK values MUST 仅存在于 0700/0600 私有工件。

#### Scenario: 相同 raw 内容重复导出
- **WHEN** 同一 source profile、query version、cohort 与表内容重复导出
- **THEN** raw artifact bytes、row ordinals/fingerprints、lineage bytes 和 content digest 相同

#### Scenario: 一行源数据变化
- **WHEN** 任一 selected source value 或主键发生变化
- **THEN** 对应 row fingerprint、artifact digest 和 composite snapshot ID 变化
- **AND** 旧 snapshot 不被覆盖

### Requirement: Canonical projection 必须来自同一受控读取窗口
系统 MUST 复用现有 `hub_source` 映射生成 `shi_fee.csv`、`case_notes.csv` 和 `shi_zd.csv`，按声明稳定列排序并计算 digest。Raw/canonical 查询起止时间、source profile 和 query version MUST 进入 manifest；canonical 输出列 MUST NOT 因本 capability 改名、删除或扩展。

#### Scenario: legacy loader 读取 snapshot
- **WHEN** snapshot 完整生成
- **THEN** 现有 CsvLoader 可读取 canonical 三文件而无需修改 DataLoader 合同

#### Scenario: raw/canonical coverage 不一致
- **WHEN** canonical patient/row coverage 超出或缺少其 raw source cohort且超过声明容忍度
- **THEN** snapshot 状态为 INVALID，不得交给 A/B plan

### Requirement: Manifest 必须诚实表达非原子快照
Manifest MUST 记录 source profile/database、schema/query/code versions、cohort query checksum、查询开始/结束时间、isolation 能力、`atomic_snapshot=false`、每表/投影/lineage行数与 digest，以及仅由内容 digest 构成的 composite snapshot ID。系统 MUST NOT 把 READ COMMITTED 查询窗口表述为 database snapshot。

#### Scenario: 当前数据库无 snapshot isolation
- **WHEN** source 的 snapshot isolation、RCSI、CDC、temporal 和 rowversion 均不可用
- **THEN** manifest 明确记录 read-committed window 与 `atomic_snapshot=false`
- **AND** snapshot 只具备物化后同输入可重放语义

#### Scenario: 相同内容不同生成时间
- **WHEN** 两次导出的 artifact bytes 相同但查询时间不同
- **THEN** composite snapshot ID 相同而 manifest 时间窗口不同

### Requirement: Snapshot 输出必须私有、不可覆盖且异常可清理
真实 snapshot MUST 写入 Git 外全新 0700 目录，所有 raw/canonical/lineage/manifest 文件 MUST 为 0600。已存在非空目录 MUST fail closed。异常退出 MUST 删除本次新建的未完成目录；完整 snapshot MUST 不支持原位 update/append/delete 单行。

#### Scenario: 输出目录已存在
- **WHEN** 操作者指定已有文件的目录
- **THEN** 命令在连接患者数据前拒绝覆盖

#### Scenario: 写到一半失败
- **WHEN** raw、canonical、lineage 或 manifest 任一步发生异常
- **THEN** 未完成目录被清理且不存在可误认为完整的 manifest

### Requirement: Public summary 与仓库必须无 PHI
系统 MUST 扫描发现的 patient IDs、raw PK values、凭据和 salt，保证它们不出现在 public summary、日志、Git fixture 或 OpenSpec 验证产物。Public summary SHALL 只包含 profile/database、permission result、时间窗口、atomic flag、行数、digest、snapshot ID 和稳定错误码，不得包含原始路径、患者号、主键值或病历原文。

#### Scenario: 标识泄漏到 summary
- **WHEN** public summary 包含任一 discovered patient ID 或 raw PK value
- **THEN** 隐私门禁失败且不回显泄漏值

#### Scenario: 合成 fixture
- **WHEN** fixture 明确 `deidentified=true` 且仅使用 `SYNTH-*` 标识和人工文本
- **THEN** fixture 可进入 Git 并用于 deterministic conformance

### Requirement: 五候选 smoke 只能证明 snapshot mechanics
系统 MUST 提供获授权的 `sh_yb_platform` 五候选只读 smoke：复用 RD04 候选查询，内存排序后取五个，在自动清理的私密临时 workspace 生成完整 snapshot并返回无 PHI summary。Smoke MUST NOT 写源库、`zadig`、`audit_runs` 或永久患者工件，也 MUST NOT宣称 cohort 代表性或临床效果。

#### Scenario: smoke 成功
- **WHEN** sh profile 权限/schema preflight、候选发现、raw/canonical/lineage/manifest 均通过
- **THEN** summary 记录五候选的安全计数和 snapshot ID
- **AND** 临时 PHI workspace 在返回前已删除

#### Scenario: 候选不足或任一步失败
- **WHEN** 确认候选少于五个或 snapshot 不完整
- **THEN** smoke 返回非零/blocked 状态并清理临时 workspace

### Requirement: EvaluationPlan 只能引用完整 snapshot
只有状态 COMPLETE、artifact/lineage/manifest checksum 全部通过且 source profile匹配的 snapshot 才可供后续 EvaluationPlan 引用。A/B 两臂 MUST 使用相同 composite snapshot ID；`historical_unpaired` Workbench 数据与另一数据库 snapshot MUST NOT 补齐或混入。

#### Scenario: A/B 使用相同 snapshot
- **WHEN** legacy A 与 structured B 均加载同一 canonical artifact digests 和 snapshot ID
- **THEN** EvaluationPlan 可声明该 source snapshot 为 paired input

#### Scenario: 两臂数据库或 digest 不同
- **WHEN** A 使用 TP snapshot 而 B 使用 sh snapshot，或任一 artifact digest 不同
- **THEN** evaluation input validation 为 INVALID

### Requirement: Snapshot capability 不得产生数据库或运行时副作用
运行 snapshot、preflight 或 smoke MUST 只执行 SELECT，并 MUST NOT 修改源库、知识库、`zadig`、SQLite audit store、规则状态、Router index、Runner、SSE、2C 或生产配置。该 capability MUST NOT 自动运行 verdict A/B、发布 release 或部署。

#### Scenario: 完成 snapshot
- **WHEN** snapshot 或 smoke 成功结束
- **THEN** 数据库 DML 计数为零，现有 audit/schema/API/config 文件与行为不变
