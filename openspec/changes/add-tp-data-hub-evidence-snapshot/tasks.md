## 1. Source profile 与只读门禁

- [x] 1.1 定义 versioned `sh_yb_platform-readonly` / `tp-data-hub-readonly` allowlist profile、必需表和 database 精确匹配；默认 sh profile，禁止任意数据库名和静默回退。
- [x] 1.2 实现连接前输出目录空/不存在检查，以及连接后表级 SELECT=true、INSERT/UPDATE/DELETE=false 权限 preflight；可写源必须在患者查询前阻断且无 override。
- [x] 1.3 实现 schema/PK/selected-column preflight，固定九张最小源表的主键、列、排序和 query version；缺表/列/主键或 schema 漂移返回稳定错误码。
- [x] 1.4 用合成 catalog/permission fixture 覆盖 sh 只读通过、TP 可写阻断、database/profile mismatch 和缺表/列场景；错误输出不含连接串或凭据。

## 2. Raw SourceArtifact 与 lineage

- [x] 2.1 定义严格 snapshot/manifest/table-artifact/lineage Pydantic 模型，区分 PHI 私有工件与 public summary，并固定 `atomic_snapshot=false`/READ COMMITTED 语义。
- [x] 2.2 实现按声明 PK 选择最小列、参数化 cohort 过滤和 deterministic order 的 raw table reader；验证空/重复/非单调 PK，禁止拼接患者值或任意 SQL 标识符。
- [x] 2.3 实现 streaming canonical JSONL writer、row ordinal/fingerprint、artifact/schema/content checksum 和 0600 模式；相同输入字节稳定。
- [x] 2.4 实现 `source_rows.jsonl` lineage writer，记录 artifact、database/table、PK columns/private values、selected columns、query/transform version 与 row checksum；真实 key values 不得进入 summary/manifest。
- [x] 2.5 覆盖一行值/主键变化导致 row/artifact/snapshot digest 变化、不同生成时间不改 content identity、旧 snapshot 不覆盖等测试。

## 3. Canonical projection 与 snapshot manifest

- [x] 3.1 复用 `insurance_oncology_drugs`、`discover_coarse_candidate_ids` 与 `fetch_candidate_frames`，按已发现 cohort 生成现有列契约的 `shi_fee.csv`、`case_notes.csv`、`shi_zd.csv`，不修改 `hub_source`/DataLoader 输出。
- [x] 3.2 对 canonical frame 使用声明稳定排序后写 0600 CSV，记录行数、列/schema checksum 和 file digest；相同 frame 重复写字节一致。
- [x] 3.3 实现 raw/canonical cohort coverage 对账；患者/行覆盖超过声明容忍度或 artifact 缺失时 snapshot INVALID，不产生 COMPLETE manifest。
- [x] 3.4 实现 manifest/composite snapshot ID：绑定 source profile/database、query/schema/code/cohort versions、raw/canonical/lineage digests，查询时间只入 manifest 不入 content identity。
- [x] 3.5 实现 EvaluationPlan handoff validator，只有 COMPLETE 且 digest/profile 匹配的同一 snapshot ID 可供 A/B；跨 sh/TP 或 artifact digest 不同必须 INVALID。

## 4. 私有目录、CLI 与五候选 smoke

- [x] 4.1 实现全新 0700 snapshot directory 与 0600 文件、异常递归清理、完整目录不可原位覆盖/append；输出路径必须在 Git 工作区外。
- [x] 4.2 实现显式 snapshot CLI：从环境读取凭据，只接受 allowlisted profile、cohort selector/limit 和全新输出目录；不接受密码、salt 或患者号命令参数。
- [x] 4.3 实现 public summary 与 PHI scanner，只输出 profile/database、permission、时间窗口、atomic flag、行数、digest、snapshot ID 和错误码；扫描 discovered IDs、PK values、凭据/salt 且不回显命中值。
- [x] 4.4 实现自动清理的 `sh_yb_platform` 五候选 smoke：内存发现、排序取五、生成完整 snapshot、验证 summary 后删除 PHI workspace；不得写任一数据库、audit store 或永久患者文件。
- [x] 4.5 覆盖候选不足、raw/canonical/lineage 写入失败、隐私失败和 cleanup 场景，证明不存在半成品 manifest 或临时目录残留。

## 5. Golden 与兼容回归

- [x] 5.1 新增仅含 `SYNTH-*` 的九表最小 fixture，覆盖稳定 PK、文书空段落标题、费用收退、IH/BA 诊断和 canonical 三文件。
- [x] 5.2 建立离线 snapshot golden harness，重复两次比较 raw/canonical/lineage bytes、snapshot ID 与允许变化的查询时间字段；禁止网络、LLM、SQL Server 和生产配置访问。
- [x] 5.3 运行现有 hub_source、CsvLoader、oncology shadow report、Evidence/Evaluation Contract 和 Diagnosis shadow 回归，确认旧行为与字段不变。
- [x] 5.4 静态/运行时断言 snapshot 模块不导入 audit store/Web/2C，不执行 DML，不修改规则、Router index、SQL schema 或配置默认值。

## 6. 文档、实库 smoke 与严格验收

- [x] 6.1 更新 README、`docs/how_javert_works.md`、`docs/oncology/ab_evaluation_standard.md`、新 snapshot 运维文档和 `docs/CHANGES.md`，说明 sh/TP profile、只读门禁、non-atomic window、lineage 和 PROMOTION 限制。
- [x] 6.2 以只读元数据/聚合证据记录 sh profile 权限与目标表 PK；运行五候选临时 smoke，仅保留无 PHI summary，并明确它不构成临床 A/B。
- [x] 6.3 先跑 snapshot/golden/privacy 定向测试，再跑受影响组合与全仓套件，原样记录 collected/pass/skip/fail/error 和既有 skip。
- [x] 6.4 运行 compile/schema/canonical checks、`git diff --check`、`openspec status` 与 `openspec validate add-tp-data-hub-evidence-snapshot --strict`；确认任务全勾选且无数据库/API/生产副作用。
