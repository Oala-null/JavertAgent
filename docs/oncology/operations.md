# 肿瘤药医保资格 v2：知识维护、shadow 与回滚

## 当前状态

- 代码默认仍为 `JAVERT_ONCOLOGY_ELIGIBILITY_V2=off`，用于新环境安全落地和一键回滚；62 已于 2026-07-17 部署运行时 commit `260a4d2`，生产值为 `on`，并已从 systemd 新进程环境实读验证。
- `RD04` 已于 2026-07-17 经 shadow/golden 验收并获明确生产授权后转为 `ready`，进入默认审计集。
- `RD04/R007/RD01/RD02/RD03` 是生产 bulk 入口；`RD10-RD37` 保持 `abandoned`，仅可显式单条复核。
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
  --hub-database sh_yb_platform \
  --history-database zadig \
  --report docs/oncology/shadow_comparison.json \
  --manifest docs/oncology/shadow_run_manifest.json
```

`--env-file` 显式解决 integration worktree 没有 `.env` 时的连接配置；显式数据库参数避免把 hub 源库和历史结果库混为一库。临时目录默认位于系统临时区；如传 `--temp-root`，该路径必须位于 Git 工作区之外。

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
4. 保留 `RD10-RD37` abandoned，不用它们“补回”bulk 覆盖。

2026-07-17 已获得将 62 切换为 `on`、把 RD04 转为 `ready` 并部署到 62 的明确授权；
该授权不包含 243 或其他环境。
