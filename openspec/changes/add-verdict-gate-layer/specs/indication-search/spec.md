## ADDED Requirements

### Requirement: 病程指征扫描工具

系统 SHALL 提供确定性工具 `scan_progress_indications(patient_id, symptom_kw_list)`, 扫描**病程/查房 section 族** (病程记录内容/病情及处理/诊疗经过/目前情况/入院情况/简要病情/首次病程 等, 由配置列出) 内的症状关键词, 返回每个命中的 `子阶段 + char 偏移 + 摘录`。工具 MUST 复用 search_notes 的否认段/选项框标注 (否认前导 / `□` 邻近)。工具是纯确定性的 (无 LLM)。

#### Scenario: 病程里命中症状

- **WHEN** 患者 `病情及处理` 段含「胸闷」, 调 `scan_progress_indications(pid, ["胸闷","气短","喘"])`
- **THEN** 返回该命中, 含 `子阶段=病情及处理`、char 偏移、±80 字摘录

#### Scenario: 命中带否认/选项框标注

- **WHEN** 症状词命中处前导有「否认」或邻近有 `□`
- **THEN** 该命中标注 `[否认段]` / `[选项框]`, 提示非阳性指征

#### Scenario: 跨碎片 section 仍能扫到

- **WHEN** ETL 把文书劈碎、无干净「日常查房记录」section, 症状散在多个病程类子阶段
- **THEN** 工具扫遍 section 族, 不因无统一 section 名而漏 (不依赖 `section=日常查房记录` 存在)

#### Scenario: 命中可被前端锚定

- **WHEN** 审计把工具命中的症状词写入 evidence (source=note, 含子阶段+偏移)
- **THEN** 工作台 openSourcePanel 能按该关键词在原文精确高亮 (evidence-anchoring level-2 keyword)
