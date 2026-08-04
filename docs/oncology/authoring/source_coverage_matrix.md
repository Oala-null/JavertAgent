# 肿瘤知识作者来源覆盖矩阵

本页是生成资产的导航和对账口径，不手抄会随来源变化的库存数字。它证明本地候选、工作簿和
QA 工件可复现；截至 2026-07-22，最终 generated DRAFT 已在 142 `知识库_work` 完成物化，
实库对象和批次以 [`142_draft_seed_import_report.md`](142_draft_seed_import_report.md) 为准。
本页不表示专家已批准、release 已发布或知识已进入运行时。

## 唯一计数来源

| 口径 | 机器可读资产 | 实时字段/表达式 |
|---|---|---|
| 来源文件 checksum、现有资产基线 | [`../kb_authoring_baseline.json`](../kb_authoring_baseline.json) | `.sources`、`.counts`、`.rule_yaml_snapshot` |
| 医保/指南规则、分支、节点、药品候选并集 | [`oncology_authoring_candidates.json`](oncology_authoring_candidates.json) | 数组 `length`、`.qa`、`.candidate_universe[].source_membership` |
| RD10–RD37 精选知识原子及迁移门禁 | [`curated_knowledge_manifest.json`](curated_knowledge_manifest.json) | `.counts`、`.atoms[].migration_status`、`.atoms[].target_kind` |
| 方案 revision、别名、上下文、组分 | [`regimen_authoring_payload.json`](regimen_authoring_payload.json) | 各数组 `length`、`.aliases[].review_status` |
| 人工可读迁移明细 | [`curated_knowledge_report.md`](curated_knowledge_report.md) | 逐规则/atom type 对账；计数仍以 JSON 为准 |

不要把命令输出复制回长期文档。需要交付快照时，连同上述 JSON 的
`snapshot_checksum`/`manifest_checksum`/`checksum` 和两份 `.validation.json` 一起归档。

## 来源与覆盖含义

| 来源维度 | 候选成员标记或规则口径 | 未匹配时的正确去向 |
|---|---|---|
| 医院 2026-06 药品总库 | `source_membership.hospital_catalog` | 保留为 hospital-only 或 `needs_review`，不得静默删除 |
| 2025 国家药品目录 | `source_membership.national_catalog` | 保留为 national-only 或 `needs_review` |
| 2025 临床应用指导原则 | `source_membership.guideline`；规则 scope 为 `GUIDELINE_INDICATION` | 保留来源锚；不得称为法定说明书 |
| 医保支付限定 | 规则 scope 为 `INSURANCE_PAYMENT` | 每条进入 approved/in-review/rejected/unsupported 分区之一 |
| 现有肿瘤药资产 | `source_membership.oncology_drug_asset` | 保留资产来源；来源缺口进入 QA |
| 现有结构化条件/病理资产 | `source_membership.structured_asset`；另见 `pathology_links` | 资产-only 仍可见，未知阈值不得生成 approved 断言 |
| 规则 YAML | `source_membership.rule_yaml` | 保留为 rule-only 或迁移待审；不能因 bulk 同药命中就宣称已迁移 |
| 现有方案和聚合别名候选 | `regimen_authoring_payload.json` | 未审核/歧义别名保持 `needs_review` 或 blocked |

`candidate_universe` 的候选总数和各来源成员数不是互斥分区：一个候选可同时来自多个来源。
互斥对账应使用 `disposition`；医保/指南规则和分支则分别使用
`.qa.source_rule_coverage.counts` 与 `.qa.coverage_partition`。

## 实时矩阵命令

以下命令均在仓库根目录运行，只读取已生成 JSON。

来源成员矩阵：

```bash
jq -r '
  .candidate_universe as $rows |
  ["source_membership", "candidate_count"],
  (($rows[0].source_membership | keys[]) as $key |
    [$key, ($rows | map(select(.source_membership[$key] == true)) | length)])
  | @tsv
' docs/oncology/authoring/oncology_authoring_candidates.json
```

候选去向与医保/指南 scope：

```bash
jq -r '
  ["kind", "value", "count"],
  (.candidate_universe | group_by(.disposition)[] |
    ["candidate_disposition", .[0].disposition, length]),
  (.source_rules | group_by(.policy_scope)[] |
    ["policy_scope", .[0].policy_scope, length])
  | @tsv
' docs/oncology/authoring/oncology_authoring_candidates.json
```

来源规则与分支覆盖分区：

```bash
jq -r '
  ["coverage_level", "partition", "count"],
  (.qa.source_rule_coverage.counts | to_entries[] |
    ["source_rule", .key, .value]),
  (.qa.coverage_partition | to_entries[] |
    ["branch", .key, .value])
  | @tsv
' docs/oncology/authoring/oncology_authoring_candidates.json
```

病理、精选知识和方案覆盖：

```bash
jq '{pathology_links:(.pathology_links|length)}' \
  docs/oncology/authoring/oncology_authoring_candidates.json

jq '{counts, missing_verification:(.atoms |
      map(select(.migration_status != "VERIFIED")) | length)}' \
  docs/oncology/authoring/curated_knowledge_manifest.json

jq '{revisions:(.revisions|length), aliases:(.aliases|length),
     contexts:(.contexts|length), components:(.components|length),
     schedules:(.schedules|length),
     alias_review_status:(.aliases | group_by(.review_status) |
       map({status:.[0].review_status,count:length}))}' \
  docs/oncology/authoring/regimen_authoring_payload.json
```

## 必须成立的本地对账

```bash
jq -e '
  (.source_rules | length) == .qa.source_rule_coverage.total and
  (.branches | length) == .qa.partition_total and
  (.qa.tree_errors | length) == 0
' docs/oncology/authoring/oncology_authoring_candidates.json

jq -e '.valid == true and .error_count == 0' \
  outputs/add-oncology-kb-authoring/肿瘤药指南适应证与医保限定条件树KB.validation.json

jq -e '.valid == true and .error_count == 0' \
  outputs/add-oncology-kb-authoring/肿瘤治疗方案组成KB.validation.json
```

三条命令都返回 `true` 只说明本地结构、树和当前工作簿校验通过。以下任一情况仍阻断
release：`unsupported`、`needs_review`、未解决别名歧义、来源/概念缺失、日期冲突，或本次
release 相关的肿瘤精选知识原子尚未全部达到 `VERIFIED`。
