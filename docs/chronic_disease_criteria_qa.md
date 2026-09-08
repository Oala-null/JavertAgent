# 慢病专家反馈与试跑 QA

## 2026-09-08 当前已发布版本

本次专家反馈取自《慢病诊断标准规则覆盖可行性反馈.xlsx》“待线下确认”H4:H8；用户确认该列为专家意见，G列旧“待确认”值原样保留为来源事实。仅记录五条解释及工作簿checksum/单元格，不把候选患者统计或真实病例放入Git。

- release：`chronic-criteria-2025-shadow-r2`。
- 20病种/69节点：14 compiled、54 partial、1 blocked；80来源片段和5条书面解释。
- CD01、CD07、CD09的明确组合阻断已解除；CD03肾病四项OR已落地，拆出独立数值/定性叶。
- CD19记录评分≥6 AND X线III期以上，但严重肢体功能障碍分级仍缺，保留`FUNCTIONAL_IMPAIRMENT_THRESHOLD_MISSING`。
- 所有revision仍draft/needs_review，知识完整审批、单位映射、跨次/时间条件未完成；runtime只产shadow/REVIEW_REQUIRED，不把临床候选当QUALIFIED。
- 资产checksum：`sha256:8813c44379743a9da351620e416c12a6c3676138996edebd2e884e2642ad927e`。
- manifest checksum：`sha256:c3e60659d06d6b923cb79d5c8f98a23e0ef2f3d2d0c8e42f6de6f7cdfc2267b1`。
- r1资产和source快照单独保留，r2记录previous revision/checksum；构建不依赖临时xlsx。

已验证知识构建`--check`、Router重建和OpenSpec strict；规则注册/配置/既有展示定向组合111 passed。完整组合、部署和真实结果对账已完成，见本文末尾的最终验收记录。

内部OCR完成授权文件25页：21临床、3行政、1费用汇总，未发现可完整对账的费用明细页。实际运行19条CD shadow和CD10关闭状态；费用完整性未知，不以空收费跑全量普通规则。病例内容由内部服务处理，模型对话与终端仅接收聚合质量统计。

以下为旧r1历史快照，不代表当前资产。

---

# 门诊慢性病条件资产覆盖与 Needs-Review 报告

> 快照日期：2026-08-30
> 资产状态：仅 authoring / shadow 准备，不是专家批准版本，不可用于自动认定
> 数据边界：本报告只汇总知识资产，不读取或列出任何患者数据

## 1. 结论

- 2025 主口径已建立 `CD01-CD20` 共 20 个稳定病种 ID，转录反馈表中的 58 个主要节点。
- 覆盖分级为 A 类 2 个、B 类 14 个、C 类 4 个。A/B 表示技术覆盖能力，不表示已通过专家审核，也不表示患者符合认定标准。
- 20 个 disease revision 当前全部为 `lifecycle=draft`、`review_status=needs_review`；2025 policy 的 `execution_enabled=false`，因此自动认定数量为 0。
- CD01、CD07、CD09、CD19 为 C 类，根节点均为 `BLOCKED_ROOT`，没有臆造 AND/OR。每个 blocker 都显式禁止使用 2020 条文、LLM 推断或本地默认值补齐 2025 歧义。
- 58 个主要节点中，当前编译状态为 `compiled=7`、`partial=47`、`blocked=4`。这些是主要节点级 authoring 资产，不能替代逐条原文双人复核和完整叶子展开。
- 2025 与 2020 policy set、source document 和 fragment 引用物理隔离。2020 当前只保留历史版本边界，未编译历史可执行树，也不向 2025 提供节点、阈值或默认值。

## 2. 逐病种覆盖

| ID | 病种 | 覆盖级别 | 主要节点 | 结构执行状态 | 当前审核状态 | 当前可自动认定 |
|---|---|---:|---:|---|---|---|
| CD01 | 再生障碍性贫血 | C | 6 | BLOCKED | draft / needs_review | 否 |
| CD02 | 慢性肾功能不全（III期以上） | A | 4 | EVALUATABLE | draft / needs_review | 否 |
| CD03 | 糖尿病合并症 | B | 8 | EVALUATABLE | draft / needs_review | 否 |
| CD04 | 心房颤动 | B | 4 | EVALUATABLE | draft / needs_review | 否 |
| CD05 | 冠心病（心功能不全3级以上） | B | 3 | EVALUATABLE | draft / needs_review | 否 |
| CD06 | 风湿性心脏病（心功能不全3级以上） | B | 1 | EVALUATABLE | draft / needs_review | 否 |
| CD07 | 高血压（III期以上） | C | 3 | BLOCKED | draft / needs_review | 否 |
| CD08 | 肝硬化失代偿期 | B | 4 | EVALUATABLE | draft / needs_review | 否 |
| CD09 | 慢性病毒性肝炎 | C | 3 | BLOCKED | draft / needs_review | 否 |
| CD10 | 艾滋病 | A | 2 | EVALUATABLE | draft / needs_review | 否 |
| CD11 | 布鲁氏菌病 | B | 2 | EVALUATABLE | draft / needs_review | 否 |
| CD12 | 帕金森氏病 | B | 2 | EVALUATABLE | draft / needs_review | 否 |
| CD13 | 重症肌无力 | B | 1 | EVALUATABLE | draft / needs_review | 否 |
| CD14 | 癫痫病 | B | 2 | EVALUATABLE | draft / needs_review | 否 |
| CD15 | 脑血管病后遗症（合并肢体功能障碍） | B | 2 | EVALUATABLE | draft / needs_review | 否 |
| CD16 | 慢性阻塞性肺疾病 | B | 2 | EVALUATABLE | draft / needs_review | 否 |
| CD17 | 肺源性心脏病（慢性心力衰竭） | B | 1 | EVALUATABLE | draft / needs_review | 否 |
| CD18 | 支气管哮喘 | B | 2 | EVALUATABLE | draft / needs_review | 否 |
| CD19 | 类风湿性关节炎（有严重肢体功能障碍） | C | 3 | BLOCKED | draft / needs_review | 否 |
| CD20 | 系统性红斑狼疮 | B | 3 | EVALUATABLE | draft / needs_review | 否 |

`EVALUATABLE` 只表示没有 C 类根级政策歧义；它不绕过生命周期、专家审核、来源、checksum、适用期和节点完整性门禁。当前 16 个 A/B revision 同样不能自动认定。

## 3. C 类结构化阻断项

| ID | blocker code | 未决问题 | 需要的书面审批角色 |
|---|---|---|---|
| CD01 | `DIAGNOSTIC_BASIS_COMBINATION_AMBIGUITY` | 诊断依据 1-4 是全部必备、至少若干项还是其他组合 | 医保办、血液科专家 |
| CD07 | `POLICY_ROOT_LOGIC_CONFLICT` | 3 级血压路径与 1-2 级伴靶器官损害路径的根关系是 OR 还是 AND | 医保办、心血管专家 |
| CD09 | `POLICY_ROOT_CLOSURE_MISSING` | 慢性病史是否必备，HBV/HCV 分支如何与病史组合 | 医保办、感染或肝病专家 |
| CD19 | `SCORE_AND_FUNCTION_THRESHOLD_MISSING` | ACR/EULAR 评分阈值、严重功能障碍分级及其与 X 线 III 期的组合 | 医保办、风湿免疫专家 |

解除任一 blocker 时必须创建新 revision，把书面解释作为受控审核证据关联到受影响节点，并重新计算 fragment/source/asset checksum；不得直接修改当前 revision 后沿用旧审核状态。

## 4. 来源与 checksum

| policy | 角色 | PDF 物理页 | PDF SHA-256 |
|---|---|---:|---|
| 2025 | 新认定唯一主口径 | 19 | `sha256:0dfef82da04ecaf8cb5bcc56298f01d812a7aa77d0f6098bb0231cefb12e40f4` |
| 2020 | historical_reference | 32 | `sha256:fcff40d581b1bfee395fe23084b482468a189bf4a7329bcba178fee4b6916d82` |

- Source fragment：80 个，其中 2025 为 79 个逐物理页 fragment，2020 为 1 个历史版本边界 fragment。
- 2025 fragment 当前均为反馈表转录的 `authoring_summary`，不是已签发的逐字原文；其状态全部为 `needs_review`。
- Source manifest checksum：`sha256:022760c3cbff23a6e91a790816a4f4d545286c3664e910c3bc2a7b72ae40a9b0`。
- Criteria release：`chronic-criteria-2025-draft-r1`。
- Criteria asset checksum：`sha256:c128ff6e99a8ab6d031bd08506f9b19d120c90442654fb8c0eedb925c515de92`。
- 构建器会重新读取两份 PDF、校验物理文件 SHA-256，并重算 fragment、manifest 和 asset checksum；相同输入的 canonical 输出必须字节一致。

## 5. 专家审批前的外部事项

这些事项不阻止代码、事实归一、shadow 和 Workbench 展示并行开发，但会阻止相应 revision 自动认定：

1. 对 2025 年扫描件完成双人逐页复核，把 79 个 `authoring_summary` 替换或补充为可签发的逐字 fragment，并核对物理页与可见印刷页。
2. 取得第 3 节四个 C 类问题的书面消歧结论；未取得前继续保持 `BLOCKED_ROOT`。
3. 由内分泌/肾内专家确认 CD03 糖尿病肾病子树的组合关系及单位换算口径。
4. 由相关临床科室和信息/检验部门签发检验项目、单位、检查术语、分期/分级、阴性与排除证据的医院级映射；未知单位和无锚点文本继续 fail closed。
5. 确认跨就诊稳定关联键和允许的回溯窗口；无法合法、稳定关联的重复测量或病程跨度继续为 `UNKNOWN`。
6. 确认“自印发之日起实施，有效期 2 年”的结束日边界。当前只冻结了明确的 2025-09-02 生效起点，没有自行推算结束日。
7. 每个未来 `approved` revision 必须记录 reviewer、审核时间和受控审核证据引用；语义、阈值、单位、时间策略或来源变化后不得继承旧 approval。
8. CD10 等高敏感病种的真实数据试跑须另行通过权限、用途和访问审计门禁；知识结构和合成测试不等于真实病例试跑授权。

## 6. 构建与测试证据

已执行：

```text
.venv/bin/pytest -q tests/test_chronic_criteria_knowledge.py
9 passed

.venv/bin/pytest -q tests/test_clinical_criteria_evaluator.py tests/test_chronic_criteria_knowledge.py
27 passed

.venv/bin/python scripts/build_chronic_disease_criteria.py --check
20 diseases / 58 nodes / 80 source fragments / checked

.venv/bin/python scripts/build_chronic_disease_criteria.py --import-feedback <反馈工作簿> --check
20 diseases / 58 nodes / 80 source fragments / checked
```

测试覆盖 20 病种完整性、稳定 ID、58 节点计数、单根、C 类 blocker、2020/2025 隔离、PDF/fragment/manifest/asset checksum、伪造 approval 拒绝、篡改拒绝和重复构建字节一致性。

## 7. 隐私检查

- 本报告未查询患者库，也未包含患者号、姓名、病例引用、原始病历、检验原文、salt、run ID 或 batch ownership。
- 报告中的 ID 仅为公开的规则/知识 ID（`CD01-CD20`）、source ID、revision/release ID 和不可逆知识文件 checksum。
- 报告不声称存在 50 个合格病例，也不把诊断名候选、A/B/C 覆盖等级或 shadow 证据称为专家 gold。

## 2026-09-08 测试基线核对

初次全量非slow运行：1358 collected，1294 passed，12 skipped，52 failed，0 errors。
同环境在上线前提交ef0f625运行：1221 collected，1159 passed，12 skipped，50 failed，0 errors。
两者失败节点逐项交集恰为50：49项依赖未随隔离工作树提供的肿瘤authoring Excel，
1项anchors回填测试依赖未提供的真实费用快照。未读取或复制患者费用文件以使其通过。
本次另外2项为新增payload字段与合成fixture适配，修正后再跑门禁，不能称初跑全绿。

### 既有环境债务排除清单

下列节点在新旧版本同环境均失败；排除只用于本次代码门禁，不修改测试或宣称债务消失。

- `tests/test_backfill_anchors.py::test_route_resolve_hits_cache_hit_charge_recheck_and_fallback`
- `tests/test_oncology_authoring_lifecycle.py::test_date_override_on_approved_revision_is_rejected`
- `tests/test_oncology_authoring_lifecycle.py::test_missing_required_column_fails_closed_with_safe_diagnostic`
- `tests/test_oncology_authoring_workbooks.py::test_canonical_round_trip_is_order_and_style_independent`
- `tests/test_oncology_authoring_workbooks.py::test_curated_authority_fields_round_trip_and_are_checksum_protected`
- `tests/test_oncology_authoring_workbooks.py::test_eligibility_workbook_schema_validation_and_protection`
- `tests/test_oncology_authoring_workbooks.py::test_generated_workbooks_have_stable_ids_checksums_and_dates`
- `tests/test_oncology_authoring_workbooks.py::test_regimen_workbook_schema_validation_and_reserved_schedule`
- `tests/test_oncology_authoring_workbooks.py::test_workbooks_contain_no_sensitive_patterns`
- `tests/test_oncology_kb_authoring_e2e.py::test_complete_offline_authoring_to_rd04_dual_scope_evaluation`
- `tests/test_oncology_kb_materialize.py::test_approve_with_edit_creates_complete_superseding_eligibility_graph`
- `tests/test_oncology_kb_materialize.py::test_checksum_match_reuses_entity_and_does_not_duplicate_revision`
- `tests/test_oncology_kb_materialize.py::test_curated_authority_fields_materialize_to_typed_columns_and_checksum`
- `tests/test_oncology_kb_materialize.py::test_curated_mapping_requires_edit_then_fresh_approval`
- `tests/test_oncology_kb_materialize.py::test_curated_materializer_rejects_missing_authority_fields[oncology--oncology \u5fc5\u987b\u4e3a\u5e03\u5c14\u503c]`
- `tests/test_oncology_kb_materialize.py::test_curated_materializer_rejects_missing_authority_fields[rule_status--rule_status \u975e\u6cd5]`
- `tests/test_oncology_kb_materialize.py::test_draft_can_update_but_approved_revision_is_immutable`
- `tests/test_oncology_kb_materialize.py::test_generated_review_rows_pin_exact_typed_entity_checksums[path0-eligibility-06_\u4e13\u5bb6\u5ba1\u6838-locators0]`
- `tests/test_oncology_kb_materialize.py::test_generated_review_rows_pin_exact_typed_entity_checksums[path1-regimen-07_\u4e13\u5bb6\u5ba1\u6838-locators1]`
- `tests/test_oncology_kb_materialize.py::test_mid_transaction_error_rolls_back_formal_writes_and_preserves_failed_batch`
- `tests/test_oncology_kb_materialize.py::test_only_validated_batch_is_accepted_without_changing_other_states`
- `tests/test_oncology_kb_materialize.py::test_real_workbook_materializes_every_sheet_to_typed_tables[path0-eligibility-expected_tables0]`
- `tests/test_oncology_kb_materialize.py::test_real_workbook_materializes_every_sheet_to_typed_tables[path1-regimen-expected_tables1]`
- `tests/test_oncology_kb_materialize.py::test_review_event_is_appended_only_when_decision_exists_and_does_not_approve`
- `tests/test_oncology_kb_materialize.py::test_review_event_rejects_stale_content_checksum_before_formal_write`
- `tests/test_oncology_kb_materialize.py::test_staging_checksum_mismatch_fails_before_any_formal_write`
- `tests/test_oncology_kb_materialize.py::test_storage_adapter_enforces_duplicate_checksum_and_inclusive_overlap`
- `tests/test_oncology_kb_materialize.py::test_storage_adapter_rejects_child_insert_for_approved_regimen_and_rolls_back`
- `tests/test_oncology_kb_materialize.py::test_storage_adapter_rejects_missing_foreign_key_and_rolls_back`
- `tests/test_oncology_kb_sql_safety.py::test_server_validation_accepts_complete_staged_canonical_contract[workbook0-eligibility]`
- `tests/test_oncology_kb_sql_safety.py::test_server_validation_accepts_complete_staged_canonical_contract[workbook1-regimen]`
- `tests/test_oncology_kb_sql_safety.py::test_server_validation_fails_closed_on_template_version_and_never_regresses_existing_batch`
- `tests/test_oncology_kb_sql_safety.py::test_server_validation_reads_canonical_payload_from_staging_and_reuses_review_contract`
- `tests/test_oncology_kb_sql_safety.py::test_server_validation_rechecks_references_and_draft_lifecycle`
- `tests/test_oncology_kb_sql_safety.py::test_server_validation_rejects_missing_or_tampered_review_checksum[None-REVIEW_SOURCE_CHECKSUM_INVALID]`
- `tests/test_oncology_kb_sql_safety.py::test_server_validation_rejects_missing_or_tampered_review_checksum[sha256:0000000000000000000000000000000000000000000000000000000000000000-REVIEW_CONTENT_CHECKSUM_MISMATCH]`
- `tests/test_oncology_kb_sql_safety.py::test_staging_upload_is_parameterized_idempotent_and_path_safe`
- `tests/test_oncology_kb_sql_safety.py::test_staging_upload_rolls_back_and_redacts_driver_error`
- `tests/test_oncology_kb_staging.py::test_dry_run_reports_safe_plan_without_mutation`
- `tests/test_oncology_kb_staging.py::test_materialization_rolls_back_on_mid_batch_failure`
- `tests/test_oncology_kb_staging.py::test_reused_upload_with_completed_status_never_revalidates_or_regresses[ABORTED]`
- `tests/test_oncology_kb_staging.py::test_reused_upload_with_completed_status_never_revalidates_or_regresses[FAILED]`
- `tests/test_oncology_kb_staging.py::test_reused_upload_with_completed_status_never_revalidates_or_regresses[MATERIALIZED]`
- `tests/test_oncology_kb_staging.py::test_reused_upload_with_completed_status_never_revalidates_or_regresses[VALIDATED]`
- `tests/test_oncology_kb_staging.py::test_reused_upload_with_completed_status_never_revalidates_or_regresses[VALIDATION_FAILED]`
- `tests/test_oncology_kb_staging.py::test_reused_uploaded_batch_resumes_server_validation`
- `tests/test_oncology_kb_staging.py::test_server_validation_failure_keeps_formal_store_empty`
- `tests/test_oncology_kb_staging.py::test_successful_materialization_reconciles_every_row_and_keeps_states_independent`
- `tests/test_oncology_kb_staging.py::test_upload_dry_run_checks_database_but_never_mutates`
- `tests/test_oncology_kb_staging.py::test_upload_is_idempotent_and_does_not_approve_or_release`

### 修正后的代码门禁

排除上述50个逐项复现的既有节点后：1326 selected，1314 passed，12 skipped，0 failed，0 errors；50 deselected。新旧测试存在本次新增用例数量差异，不以相减推算回归结论。慢病定向组合150 passed；实际CsvLoader→Runner合成CSV全20规则端到端已通过，off不读患者/不调LLM、19shadow全部REVIEW_REQUIRED。该段为写入前代码门禁快照；真实生产验收见下节。

最终存储/UI/API组合271 passed；发布脚本追加冲突/断点/目录隔离/目标绑定/质量门禁测试34 passed。脚本仅在两端身份、标签与完整结果均一致时标记同步完成，既有run跳过SQLite插入并精确补远端，避免恢复时主键冲突。

## 2026-09-08 最终部署与授权单PDF验收

- 功能提交`baf16bc5468295c7a00e2bbdd33fe408a2a182dc`已推送`codex/chronic-expert-pilot`，标准artifact/install部署到62的production-62。两端HEAD一致，受控tracked clean；原项目工作树未提交修改保留。
- 代码及环境备份`/home/admin2/backup/javert-git-20260908-153139-2536884`；SQLite原生备份/旧配置/旧overlay备份`/home/admin2/backup/javert-chronic-20260908-152702`，目录0700、文件0600。SQLite备份完整性校验通过。
- 先代码/配置/index，再双库幂等迁移，后重启；双库clinical_criteria_json各恰一列且nullable。病例运行前SQL该字段非空行数0，运行后20，无历史回填。新旧进程29个JAVERT环境值及既有解析配置一致，慢病全局开关仍off。
- 每次重启后登录200、SQL health true、Hub SELECT 1成功；v3空数组submit202、合成unknown查询200/unknown。
- 用户明确授权的一份25页PDF只交内部PP-OCR及内部Qwen处理。21临床页进入本次原文，3行政页及1费用汇总页未进临床notes；无可信全量费用明细，因此没有运行全量费用规则。原始患者文件未进入Git或模型对话。
- 19条慢病shadow + CD10关闭状态，共20条REVIEW_REQUIRED。7条存在可定位候选：CD01/CD03/CD04/CD05/CD06/CD07/CD19；它们是宽召回证据，不代表已诊断这些病种或满足认定标准。未形成QUALIFIED，也未生成VIOLATION。
- 抽取质量：资产不可用0、服务抽取失败0、截断/不完整0、发布阻断0；13条规则出现候选引用校验拒绝标记，被拒绝候选没有入证据，结果保留提示。
- 20条在SQLite与SQL Server逐项回读相同，20条synced、0额外行，全部batch_tag=慢病。限定本次run集合，无全历史pending同步；再次发布使用同run检查和精确补写。
- 真实Workbench存储查询可发现该批次，全部20条CD可读；侧栏慢病候选数7，普通V/I/C均0。21页临床原文逐字与本次清单一致，29个证据锚点逐条匹配所属页面；20张慢病模板实际渲染通过。未使用浏览器查看患者正文。
- 线上v3实际病例查询HTTP200、20cards，全部携带结构化慢病字段。此次验证只输出计数和布尔值，不输出病例键、run或病历原文。
- 追加后的overlay以原文件字节为前缀，旧内容未改。临时PDF、页面图片、OCR原文、执行CSV和私有结果清单均已清理；受控生产备份及无患者信息的聚合QA保留。可读取的本次journal中整段原文匹配0；系统提示一份旧journal截断被忽略，因此不将该检查扩大声称为全部历史日志审计。
- 后续文档提交只同步Git HEAD元数据，运行时blob与功能提交一致；不重复跑LLM或写病例。

剩余范围：完整知识签发、复杂单位/重复测量/跨就诊时间策略、CD19功能障碍分级及5+45严格QUALIFIED样本。它们未被本次shadow验收替代；OpenSpec change保持开放，不归档。

## 用户反馈后的身份显示纠正（代码验收）

此前没有运行2C脱敏，也未改写OCR正文，但导入生成的chronic关联键被侧栏/标题/概览当作原始身份显示。用户要求不脱敏后，补齐source_patient_name/source_visit_id来源显示列；内部服务逐字核验原文引用，未向模型对话或终端回显身份。显示字段与不可变审计关联分开，不重跑既有慢病结果。

合成回归先复现导入缺失显示列及模板显示内部键，修复后165项组合通过，包含空身份列时旧非慢病住院号保持原样、冲突不拼接身份、HTML/JS转义及原始就诊号前导零。JS语法、OpenSpec strict和diff检查通过。辅助脚本最初因包含真实姓名常量被自动审批拦截；常量已删除，后续只在受控运行时处理两个最小身份字段。

真实显示校验进一步发现CSV reader将纯数字source_visit_id推断为浮点（合成用例000123→123.0）。原始CSV内容未损坏，已对base和overlay两路显式按字符串读取，新增真实CsvLoader合成回归，避免号码前导零和精度丢失。

### 原始身份显示最终验收

修正提交`7a27353`与`04117c2`已推送同分支并部署62。最终175 passed、1 skipped（既有真实费用fixture缺失），JS语法/OpenSpec strict通过。生产通过内部校验确认原始姓名和就诊号在侧栏、标题、概览、原文弹窗一致显示；姓名和号码未进入本报告。对照修正前备份，20条审计稳定字段及完整clinical JSON逐值一致，21页临床正文及所有旧CSV字段逐值一致，未重跑审计。

29个进程环境与既有配置保持一致，登录200、SQL/Hub健康、v3空submit202、unknown200/unknown、真例200/20cards。官方check在功能提交04117c2上synced=true、production-62受控clean。重启脚本曾在新进程启动窗口读取/proc遇到临时权限错误，服务实际重启成功；随后重试读取并完成配置逐值验证，未更改服务权限。

身份显示修正前备份为`/home/admin2/backup/javert-chronic-identity-20260908-154715`；最终代码安装备份`/home/admin2/backup/javert-git-20260908-155817-2545122`。原始身份提取清单及临时脚本目录已清理，生产显示字段按用户要求保留。后续验收文档提交不改变运行时blob，不再重复数据写入或重启。
