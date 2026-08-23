# Hub Evidence Snapshot v0.1

## 定位

该能力把 SQL Server Hub 的一次受控只读查询物化为不可变输入包，供 legacy A 与 structured B
共同读取。它不运行裁决、不写 Workbench，也不把 READ COMMITTED 查询窗口冒充数据库原子快照。

```text
sh_yb_platform (SELECT-only)
  → raw stable-key SourceArtifacts
  → canonical shi_fee / case_notes / shi_zd
  → private lineage sidecar
  → manifest + composite snapshot ID
```

## Source profile

| Profile | Database | 当前实测权限 | 用途 |
|---|---|---|---|
| `sh_yb_platform-readonly` | `sh_yb_platform` | SELECT=true，DML=false | 默认正式 source |
| `tp-data-hub-readonly` | `TP_data_hub` | 当前账号 DML=true，preflight 会阻断 | 仅在换成只读凭据后用于开发镜像 |

`ApplicationIntent=ReadOnly` 只是连接意图，不是安全边界。命令会逐表检查
`HAS_PERMS_BY_NAME`；任一 INSERT/UPDATE/DELETE 权限为 true 都在患者查询前 fail closed，
没有 override 参数。数据库名只能来自 profile allowlist，不允许静默跨库回退。

## 稳定源键

| Source | Stable primary key |
|---|---|
| IH 诊断 | `YLJGYQDM, ZYZDLSH` |
| 病案主表 | `YLJGYQDM, SYXH` |
| 病案诊断 | `YLJGYQDM, SYXH, ZDXH` |
| 医疗文书 | `YLJGYQDM, WSLSH` |
| 出院小结 | `YLJGYQDM, JZLSH` |
| 医嘱 | `YLJGYQDM, YZID` |
| 费用 FS | `YLJGYQDM, SFMXID, STFBZ` |
| 费用 EXT | `YLJGYQDM, SFMXID` |
| 医院字典 | `YLJGYQDM` |

每张 raw 表按 PK 排序写 canonical JSONL。lineage sidecar 保存 raw PK values、row ordinal、
row fingerprint、selected columns、query/transform version 和 row checksum；它是 PHI-classified
私有文件，不进入 manifest 或 public summary。

## Snapshot 目录

```text
snapshot/
├── manifest.json
├── raw/<table>.jsonl
├── canonical/shi_fee.csv
├── canonical/case_notes.csv
├── canonical/shi_zd.csv
└── lineage/source_rows.jsonl
```

目录必须位于 Git 外、首次创建为 0700，文件 0600；非空目录拒绝覆盖。异常退出删除本次未完成
目录。`snapshot_id` 只由 profile/database/query/schema/code/cohort 与 artifact digests 计算，
不含时间或绝对路径；相同内容可得到相同 ID。

## 当前一致性限制

2026-08-23 实测两个 Hub 库均为：

```text
SNAPSHOT_ISOLATION=OFF
READ_COMMITTED_SNAPSHOT=OFF
CDC=false
Change Tracking=false
temporal=false
rowversion=none
```

因此 manifest 固定：

```text
source_consistency=read_committed_query_window_no_snapshot_isolation
atomic_snapshot=false
```

物化后 A/B 可以公平地读取同一个 snapshot；但跨表状态不保证来自同一数据库时点。
PROMOTION 前仍需 DBA database snapshot/backup restore 或启用 Snapshot Isolation。

## 命令

五候选临时 smoke（退出前清理 PHI）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/hub_evidence_snapshot.py \
  --profile sh_yb_platform-readonly \
  --smoke --limit 5
```

持久 snapshot 必须指定全新 Git 外目录：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python \
  scripts/hub_evidence_snapshot.py \
  --profile sh_yb_platform-readonly \
  --limit 30 \
  --output-dir /secure/new/javert-snapshot-001 \
  --summary /secure/new/javert-snapshot-001.summary.json
```

命令不接受密码、salt 或患者号参数；凭据只从受控环境读取。public summary 只包含 profile、
权限结果、时间窗口、行数、digest、snapshot ID 和稳定错误码。

## 五候选只读 smoke（2026-08-23）

对 `sh_yb_platform` 连续执行两次：

- source permission preflight 通过；
- 两次均为 5 个候选；
- raw lineage 5,248 行；canonical 为费用 2,416、文书 1,171、诊断 44 行；
- 两次所有 artifact digest 相同；
- 最终 transform code digest 收窄后连续两次 snapshot ID 均为 `snapshot-9c6368087d128212`；
- `atomic_snapshot=false`；
- error codes 为空；
- `/private/tmp` 与 `/tmp` 无 `javert-oncology-shadow-*` 残留；
- 未写 `sh_yb_platform`、`TP_data_hub`、`zadig` 或 audit store。

该结果只证明 snapshot mechanics 与重复一致性，不证明 cohort 代表性、临床优效或生产 readiness。
