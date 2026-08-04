# 肿瘤药医保资格 v2：知识维护、shadow 与回滚

## 当前状态

- 代码默认仍为 `JAVERT_ONCOLOGY_ELIGIBILITY_V2=off`，用于新环境安全落地和一键回滚；62 已于 2026-07-17 部署运行时 commit `260a4d2`，生产值为 `on`，并已从 systemd 新进程环境实读验证。
- `RD04` 已于 2026-07-17 经 shadow/golden 验收并获明确生产授权后转为 `ready`，进入默认审计集。
- `RD04/R007/RD01/RD02/RD03` 是生产 bulk 入口。2026-07-17 的 62 已部署基线中
  `RD10-RD37` 为 `abandoned`；当前本地 authoring change 已将其后退为
  `drafting/migration-pending`，尚未部署，两个状态都不进入默认执行集。本地实时状态以
  `.venv/bin/javert list` 和 router index 为准。
- `on` 模式下，`RD04` 独占 `oncology=true AND source_type=insurance`；`R007` 只保留非肿瘤限适应症候选。`off/shadow` 不改变 R007 旧候选集合。

## 三类知识资产

| 资产 | 构建器 | 自动裁决门槛 |
|---|---|---|
| `configs/oncology_eligibility_rules.json` | `scripts/build_oncology_eligibility_assets.py` | schema/checksum/生效期均合法，条目 `review_status=approved` |
| `configs/pathology_biomarker_kb.json` | 同上 | marker、方法、癌种、支付策略、阈值与生效期完整匹配且 approved |
| `configs/oncology_regimen_kb.json` | `scripts/build_oncology_regimen_kb.py` | 癌种上下文唯一且方案条目 approved |

更新流程：

1. 更新上游 `configs/oncology_drug_kb.json` 或经药师、病理科、医保办审核的结构化种子。
2. 运行两个构建器；构建器以 canonical JSON 输出，重复运行必须字节一致。
3. 审核 `docs/oncology/*needs_review.json` 与 `regimen_alias_candidates.json`。患者语料频次只能发现候选，不能自动改为 approved。
4. 补来源 ID、版本/生效日期、检索日期和 checksum，经代码评审后才把候选提升为 approved。
5. 运行肿瘤专项测试及 shadow；确认后再发布新 `content_version`。

回滚知识版本时，恢复三个资产及其构建器种子到上一提交。历史 `eligibility_json` 保留其 `rule_version/source_versions`，不得用新知识静默重写旧审计。

## 专家 authoring（142 结构与 DRAFT 已物化）

`add-oncology-kb-authoring` 在现有生产 JSON 之外增加两份专家审核投影：

- `outputs/add-oncology-kb-authoring/肿瘤药指南适应证与医保限定条件树KB.xlsx`
- `outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx`

Excel 不是知识真相源，也不会替换本节上方的生产资产。专家填写规则、安全边界和交回检查见
[`kb_authoring_guide.md`](kb_authoring_guide.md)；各来源覆盖不在本文固化数字，按
[`authoring/source_coverage_matrix.md`](authoring/source_coverage_matrix.md) 的 JSON 查询动态生成。

本地维护入口：

```bash
# 运行时路径由工作区依赖环境显式提供，不写入仓库
.venv/bin/javert oncology-kb export \
  --node "$JAVERT_ARTIFACT_NODE" \
  --node-modules "$JAVERT_ARTIFACT_NODE_MODULES" \
  --output-dir outputs/add-oncology-kb-authoring

.venv/bin/javert oncology-kb validate \
  outputs/add-oncology-kb-authoring/肿瘤药指南适应证与医保限定条件树KB.xlsx \
  --kind eligibility

.venv/bin/javert oncology-kb validate \
  outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.xlsx \
  --kind regimen

# 可选：只读 142 sh_yb_platform；要求明确肿瘤诊断和病历文本，肺癌优先
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  scripts/run_oncology_authoring_preview_batch.py \
  --source-database sh_yb_platform \
  --limit 10

# 可选：显式写入工作台结果库；仍只产生 DRAFT/INCONCLUSIVE，不发布知识
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python \
  scripts/run_oncology_authoring_preview_batch.py \
  --source-database sh_yb_platform \
  --limit 10 \
  --result-database zadig \
  --batch-tag <不超过20字符的唯一标签>
```

`validate` 是离线、零数据库连接的门禁。截至 2026-07-22，已在 142 精确核验
`DB_NAME()=知识库_work` 并建立 authoring schema 与 6 个中文审核视图；当前账号为
`db_owner` 而非最小权限 principal，备份策略也未得到证明。最终工作簿只完成
preflight/`upload --dry-run` 后，已作为 generated DRAFT 上传并物化；重复上传复用既有
batch。`upload`、`materialize` 和 release 命令仍会对精确目标库、owned 白名单、schema
capability、审核状态与 authority pin fail closed。

`run_oncology_authoring_preview_batch.py` 默认只读取患者快照并输出去标识聚合 JSON；只有同时
显式提供 `--result-database` 和唯一 `--batch-tag` 时，才通过 `persist_one` 把每位患者一条
RD04 结果写入本地 SQLite 和工作台结果库。该写路径不写 `TP_data_hub` 或
`[知识库_work]`，已有标签会 fail closed，不能混入或覆盖旧批。它复用真实叶子求值器，先对
同一药/政策范围的多个 OR 分支择优，再跨药生成患者级结果；所有 DRAFT 分支均重新校验为
`REVIEW_REQUIRED / INCONCLUSIVE`，并带 `DRAFT_RULE_PREVIEW_ONLY` 和
`AUTHORING_REVIEW_REQUIRED`。该结果不能计为任务 10.5 的获授权 paired shadow，也不能用来
批准或发布知识。选人要求同时具备 `TB_BA_SYJBK` 非空主诊锚、明确恶性肿瘤诊断、RD04 净正
肿瘤药收费和可用病历文本；在满足硬门禁的候选中优先肺癌，再优先扩大药物覆盖。聚合报告只
保留队列、药物和写入对账计数，不保存患者号或原始病历。

142 知识写入只能命中 `[知识库_work]`，并要求该精确库名出现在 `JAVERT_OWNED_DBS`；
`TP_data_hub` 的现有授权不自动覆盖该库，`sh_yb_platform` 始终只读。用户重新授权后，最终
DDL 已连续幂等执行两次，23 个触发器和两份 generated DRAFT 已在该精确库完成物化；最小权限
principal、备份策略和恢复演练仍未满足 OpenSpec 9.3/9.10。DRAFT 已入库不等于已专家批准或
已发布。

### DRAFT 入库后的批准与 release 顺序

下面是实现已经提供、但必须在 DBA 和发布授权到位后才能执行的操作顺序。命令中的审核人、
发布人、时间、checksum 和 release ID 都必须来自本次受控变更记录，不得用示例值替代。

1. 对任何新的或专家回传工作簿，知识管理员仍须依次 `preflight`、`upload --dry-run`、正式
   `upload` 和 `materialize`；两类工作簿分别处理，服务端校验和物化对账必须全部通过。DBA
   还须补齐备份、最小权限和恢复演练记录。
   若升级既有 `kb.review_event` 时发现历史行没有 `reviewed_content_checksum`，DDL 会以
   `THROW 51003` 阻断；必须依据当时被审核内容人工补齐并留痕后重跑，禁止从当前内容自动猜测。
   `schema_version` 是完整 capability 的完成标记：所有必需触发器创建成功、挂载到预期表且
   处于启用状态后才登记版本；只有版本号而缺触发器时 `preflight` 必须失败。
2. 专家意见只通过 append-only `review_event` 留存。知识管理员对每个待发布父 revision
   显式投影批准状态：

   ```bash
   .venv/bin/javert oncology-kb approve \
     --entity-type eligibility \
     --revision-id <eligibility-rule-revision-id> \
     --reviewer-id <domain-reviewer-id> \
     --database 知识库_work

   .venv/bin/javert oncology-kb approve \
     --entity-type regimen \
     --revision-id <regimen-revision-id> \
     --reviewer-id <domain-reviewer-id> \
     --database 知识库_work
   ```

   `approve` 不创建、覆盖或补写审核意见；它锁定完整 typed 子图，要求目标所有最新事件均为
   同一审核人的批准结论，并要求每条事件的 `reviewed_content_checksum` 精确等于当前父
   revision、branch/node/regimen/alias/context/component typed 内容 checksum，再核对来源、日期、
   条件树、方案、药品概念和独立药物类别 authority。`APPROVE_WITH_EDIT` 不修改原 revision，
   而是 materialize 完整 content-addressed superseding DRAFT 子图；必须针对新 checksum 追加
   已解决编辑的审核事件后才能批准。精选知识映射修订只落为 `MAPPED`，必须用后续新的
   `APPROVE` 事件核验已 materialize 的同一目标后才能升为 `VERIFIED`。重复批准同一内容只返回
   `reused=true`。
3. 发布管理员用经过批准的病理 bootstrap checksum 做一次**只读** authority 检查：

   ```bash
   .venv/bin/javert oncology-kb release-authority \
     --database 知识库_work \
     --pathology-bootstrap /secure/path/pathology_biomarker_kb.json \
     --pathology-bootstrap-checksum <sha256:pathology>
   ```

   命令只读当前 typed 表全集和病理文件，不写 release 状态，也不输出来源原文或 principal。
   将输出的 `source_authority_checksum`、`curated_authority_checksum` 与已提供并回显的
   `pathology_bootstrap_checksum` 三个 pin 记录到 authoring 库之外的审批单；后续 build 和
   publish 必须复用完全相同的三个值。任一来源全集、精选知识台账或病理 bootstrap 漂移，
   都必须重新检查和审批，不能沿用旧 pin。
4. 与领域审核人分离的 release operator 用三个 pin 构建 candidate；`created_at` 使用带时区
   ISO 8601：

   ```bash
   .venv/bin/javert oncology-kb release-build \
     --database 知识库_work \
     --operator <release-operator-id> \
     --created-at <ISO-8601> \
     --pathology-bootstrap /secure/path/pathology_biomarker_kb.json \
     --pathology-bootstrap-checksum <sha256:pathology> \
     --source-authority-checksum <sha256:source> \
     --curated-authority-checksum <sha256:curated> \
     --releases-dir /secure/releases/oncology
   ```

   `release-build` 从数据库重新读取完整权威集合，执行来源全集分区、精选知识保全、日期不
   重叠和跨资产引用门禁，只在 authoring 库登记确定性 `CANDIDATE` 及 release items；它
   **不会**向本地目录写 candidate bundle，也不会切 active pointer。相同输入重试返回同一
   release ID 和 `reused=true`；内容漂移则拒绝复用。
5. 独立发布授权确认 release ID 和三项 pin 后，由**与 build 相同、且不属于任何领域审核人**
   的 release operator 执行发布：

   ```bash
   .venv/bin/javert oncology-kb release-publish \
     --database 知识库_work \
     --release-id <release-id> \
     --operator <same-release-operator-id> \
     --published-at <ISO-8601> \
     --pathology-bootstrap /secure/path/pathology_biomarker_kb.json \
     --pathology-bootstrap-checksum <sha256:pathology> \
     --source-authority-checksum <sha256:source> \
     --curated-authority-checksum <sha256:curated> \
     --releases-dir /secure/releases/oncology
   ```

   publish 会从锁定数据重建并逐项比对 candidate；只有它能写四份 JSON、两个 manifest 的
   不可变 `PUBLISHED` bundle，更新数据库 release/pointer，并原子切换本地
   `active_release.json`。本地激活或数据库提交失败时会恢复调用前的 active pointer 和数据库
   状态；已经写出的不可变 bundle 不覆盖、不删除，使用原参数重试时会先完整校验再复用。
6. 只允许切回本地仍存在且 checksum 合法的历史 published bundle：

   ```bash
   .venv/bin/javert oncology-kb release-rollback \
     --database 知识库_work \
     --target-release-id <historical-published-release-id> \
     --operator <authorized-release-operator-id> \
     --reason <approved-reason> \
     --occurred-at <ISO-8601> \
     --releases-dir /secure/releases/oncology
   ```

   rollback 保留所有 bundle、revision 和审计历史，并追加本地回滚事件。若本地切换失败，
   会补偿恢复数据库 release 状态和 pointer；同一目标已经 active 时返回 `reused=true`。

职责边界固定为：DBA 只负责库和权限；领域专家负责 append-only 内容审核；知识管理员负责
导入、物化及批准投影；release operator 负责确定性 build/publish/rollback，且不得兼任本次
领域审核人；独立发布授权人决定是否允许发布和部署。运行时只读本地 `PUBLISHED` bundle，
不连接 authoring 库，也不会回退到 candidate、staging 或 Excel。

截至 2026-07-22，142 `知识库_work` 的幂等 DDL、23 个必需触发器和 6 个中文审核视图已实际
落地；两次历史失败 batch 留存并保持回滚。最终两份修正版工作簿已通过
validate/preflight/服务端校验，且均已 MATERIALIZED 为 generated DRAFT；`review_event=0`、
`knowledge_release=0`。专家批准、production
publish/部署、上一 release/备份恢复演练和 paired shadow 仍未执行；以
`authoring/142_draft_seed_import_report.md` 为当前事实记录。

## 病历建议措辞

医院可编辑 `configs/oncology_documentation_templates.json`：

- `criterion_id` 是稳定关联键，不应改名；
- 可改 `title/rationale/content_template/safety_note/priority`；
- `required_context` 控制缺少哪些已证实上下文时不生成建议；
- 模板只能生成前瞻性建议，不能写成“患者已经不适合移植”，也不能创建证据或改变条件状态。

修改后运行：

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_oncology_result.py tests/test_oncology_integration.py
```

## feature flag

| 模式 | 行为 |
|---|---|
| `off` | 完全走旧 LLM/bulk 路径；新字段为 `None` |
| `shadow` | 旧 verdict 不变；结构化比较保存在 `tool_calls_json[].structured_output` |
| `on` | RD04 的净正收费候选采用确定性双轴结果，并写入 `eligibility_json`；R007 排除相同肿瘤医保臂 |

### 生效期闸（`JAVERT_ONCOLOGY_ENFORCE_EFFECTIVE_DATE`）

控制是否按知识资产声明的生效期过滤候选。**代码默认 `true`（只审就诊日落在声明窗口内的候选）；62 的受控运行值为 `false`（不分时间全部生效）。**审核状态闸（`review_status=approved`）与本开关无关，始终生效。

- 生效期闸在**两处**：`eligibility.select_effective_rules`（选条件树）与 `pathology._select_threshold`（选免疫组化阈值）。开关一路透传，两处一起放开——只放开前者会让免疫组化 criterion 落 `UNKNOWN`。
- `false` 且就诊日在声明窗口外时，结果照常求值，但追加数据质量提示「未按生效期过滤·需核查就诊时该医保限定是否已生效」，`eligibility_json` 记录 `rule_effective_from/to`、`evaluated_service_date`、`effective_date_enforced`，工作台 fail-loud 展示"核查生效时间"橙框。
- 回滚 = 该 env 改 `true` 重拉，一步回到按生效期过滤。

**⚠ KB 生效期待核对**：`oncology_eligibility_rules.json` 与 `pathology_biomarker_kb.json` 里维迪西妥单抗/HER2 生效期填的是 `2026-01-01~2027-12-31`，但该药早已进国谈目录，2025 就诊多半应可审。生效期是带 checksum 的受审资产，不得私改；需医保办确认真实生效日后走构建脚本补对应生效窗口版本。生效期闸关闭是过渡手段，不替代把生效期填对。

**术语归一**：`cancer_context` 推导已把「移行细胞癌 / 移行上皮癌」（尿路上皮癌 WHO 2004 前旧名，同一诊断）归一到「尿路上皮癌」，并纳入文书原文（与 diagnosis 叶子同口径），否则病案首页只编码旧名时 HER2 阈值匹配不上。

单患者 smoke 示例（仅排障，不构成全候选验收）：

```bash
export JAVERT_ONCOLOGY_ELIGIBILITY_V2=shadow
export JAVERT_BATCH_TAG=oncology-v2-shadow
uv run javert audit-patient <患者号> --rules RD04
PYTHONPATH=src .venv/bin/python scripts/oncology_shadow_report.py \
  --db output/audit.sqlite \
  --output docs/oncology/shadow_comparison.json \
  --salt "$JAVERT_SHADOW_DEID_SALT"
```

`oncology_shadow_report.py` 的 SQLite 路径只读取已经落库的 smoke 行。正式验收不得用这一路径声称覆盖全部候选。

### 142 全候选可复现批处理

先准备工作区外的受控 env 文件（至少含 `JAVERT_SQL_HOST/PORT/USER/PASSWORD/DRIVER`），并通过环境注入去标识 salt；凭据和 salt 不得写进命令参数、仓库或 manifest：

```bash
export JAVERT_SHADOW_DEID_SALT="<从受控密钥存储读取>"

PYTHONPATH=src .venv/bin/python scripts/run_oncology_shadow_batch.py \
  --env-file /secure/path/javert-142.env \
  --hub-database TP_data_hub \
  --history-database zadig \
  --report docs/oncology/shadow_comparison.json \
  --manifest docs/oncology/shadow_run_manifest.json
```

`--env-file` 显式解决 integration worktree 没有 `.env` 时的连接配置；显式数据库参数避免把 hub 源库和历史结果库混为一库。临时目录默认位于系统临时区；如传 `--temp-root`，该路径必须位于 Git 工作区之外。
2026-07-17 正式 shadow 报告使用的是我方维护的 142 `TP_data_hub` 快照；`sh_yb_platform`
是数据工程侧只读源库，不得在复现实验时无说明地互换。若以后改用其他 hub 库，必须把库名、
读取时间窗和一致性说明写进新 manifest。

脚本执行以下固定口径：

1. 在 hub 费用表按国家码精确命中，同时纳入“国家码为空（含源值 `nan/none/null`）且费用名包含完整实体通用名”的候选；不使用共享 stem 扩大肿瘤药候选。
2. 复用 `hub_source` 拉取费用、文书、诊断，再复用 `lookup_patient_drugs` 做退费净额、RD04 ownership 和结构化 `shadow` 求值。
3. 求值前以 `matches` 独立记录确认患者数和预期候选数；报告器不得用实际输出反填分母。
4. 查询 `zadig` 中每患者最新历史 RD04 和最新专家复核，仅作历史方向性对照。
5. 只持久化去标识报告及不含 PHI 的 manifest。含患者号的 CSV/JSON 中间文件位于随机 0700 临时目录、文件权限 0600，成功或异常退出均自动删除。

manifest 记录代码 commit、工作区是否干净、tracked diff 与 untracked 路径/内容的聚合指纹、四个知识资产的 SHA-256/版本、hub 与历史数据库名、候选查询版本、数据读取起止时间和汇总计数；不记录主机凭据、salt、患者号、原文、untracked 明文路径或费用明细。指纹明确排除本次生成的 report/manifest，避免写出文件后指纹立即因自引用失效；排除的两个固定输出相对路径会列在 `excluded_generated_outputs`。当前读取是多个 `READ COMMITTED` 查询组成的时间窗口，manifest 明确写入 `snapshot_isolation=false`，因此起止时间不得称为一致性数据库快照。

严格报告只有同时满足以下条件才会是 `status=complete`，否则写入去标识 `validation_errors`、返回 `status=blocked`，并以退出码 2 结束：

- `source_summary` 提供来自求值前候选集的独立患者/候选分母、查询版本、读取起止时间和一致性说明；
- 每个患者只出现一次，且 `structured.mode=shadow`；
- 每个确认患者至少有一个候选，并具有完整、schema 合法的 patient-level selected evaluation；
- 每个候选均有非空 ownership，且药品概念 ID 或通用名至少一项非空；尚未编译条件树的药允许 concept ID 为空，但必须保留通用名并输出 `REVIEW_REQUIRED/DOCUMENTATION_GAP`。此外 legacy verdict、audit disposition、eligibility status 和完整 selected evaluation 均须存在；
- 实际患者数/候选求值数与独立分母完全一致，结构化错误和重复 ownership 均为零。

报告中的 `candidate_evaluation_count` 是本次结构化求值候选行数；`old_new_comparable_patient_count` 才是同时存在历史旧裁决和当前新裁决的患者数，两者不得混称 comparison count。`comparison_design=historical_unpaired` 表示旧裁决并非在本次同一输入快照上重跑；新旧矩阵和专家一致率只能作为探索性历史对照，不能解释为 paired shadow 性能。

## 存储迁移

SQLite 启动时幂等增加 `audit_runs.eligibility_json TEXT NULL`；SQL Server 运行：

```bash
uv run javert ensure-mssql-schema
```

部署脚本会幂等增加 `javert_audit_runs.eligibility_json NVARCHAR(MAX) NULL`。旧行读为 `None`，不重判。写入前会校验：

```text
NO_VIOLATION_FOUND -> CLEAN
VIOLATION_FOUND    -> VIOLATION
REVIEW_REQUIRED    -> INCONCLUSIVE
```

## 回滚

1. 设置 `JAVERT_ONCOLOGY_ELIGIBILITY_V2=off` 并重启审计进程。
2. 将 RD04 从 `ready` 恢复为 `abandoned`；R007 恢复完整限适应症所有权。
3. 不删除 `eligibility_json` 列、不改历史行；旧消费者会忽略该可空字段。
4. `RD10-RD37` 不得转为 ready，也不用它们“补回”bulk 覆盖；2026-07-17 生产基线为
   `abandoned`，当前本地 authoring change 为 `drafting/migration-pending`。只有全部知识原子
   验证完成并重新经过所有权、router 和发布门禁后，才能另行调整状态。

2026-07-17 已获得将 62 切换为 `on`、把 RD04 转为 `ready` 并部署到 62 的明确授权；
该授权不包含 243 或其他环境。
