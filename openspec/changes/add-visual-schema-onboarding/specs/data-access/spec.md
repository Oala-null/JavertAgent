## ADDED Requirements

### Requirement: Manifest 驱动的 N 表 ETL

`etl_import` SHALL 遍历 `schema_manifest.yaml` 声明的 spoke 进行转换,而非硬编码固定 4 表。现有 4 表(费用/文书/诊断/手术)的转换输出 MUST 与旧实现逐列保持一致(回归基线为 `data/song_fixture/` 与既有 szx fixture)。

#### Scenario: 老 4 表行为不变

- **WHEN** 对既有 szx/song 数据按 manifest 跑 ETL
- **THEN** 产出的 `shi_fee`/`case_notes`/`shi_zd`/`shi_ss` MUST 与旧硬编码实现逐列一致

#### Scenario: manifest 新增表自动转换

- **WHEN** manifest 增加化验/检查 spoke 且 column_mapping 提供其映射
- **THEN** ETL MUST 自动转换并产出对应文件,无需改 ETL 代码

### Requirement: 桥表键归一(ETL 阶段)

ETL SHALL 在转换阶段用桥表(如病案首页 `r_basy`)构 `源键→canonical key` 交叉表,把各表患者键归一到统一 canonical;桥表 MUST NOT 带入审计运行时(下游工具仍只认 `bah`/`ba_id`)。canonical 形态 MUST 匹配目标 getter:文书落裸号(`get_notes` 精确相等)、费用/诊断/手术落复合键(`get_fees` 包含匹配)。归一失败的行 MUST 被计数并告警,MUST NOT 静默并入。

#### Scenario: 跨命名空间归一

- **WHEN** 文书表用 `medcasno`(226xxx)、费用表用 `hsp_account_no`(211xxx)
- **THEN** ETL MUST 经桥表把文书 `medcasno` 归一到与费用一致的 canonical patient,使同一患者两表都取得到

#### Scenario: 归一后两 getter 都解析得出

- **WHEN** 对归一后的 fixture 调 `get_notes(pid)` 与 `get_fees(pid)`
- **THEN** 两者 MUST 都返回该患者非空数据(实证基线:患者取到 38 段文书 + 554 行费用)

#### Scenario: 归一失败不静默

- **WHEN** 某表行的源键在桥表中找不到对应 canonical
- **THEN** ETL MUST 计数并告警该批失败行,MUST NOT 静默并入数据

### Requirement: ETL 逐列日期格式归一

ETL SHALL 复用列剖析的日期探测(逐列、不设全局 dayfirst),把各表/各列的异构日期格式归一为统一 ISO 存储;纯时间无日期列 MUST 标记不参与时间窗口。

#### Scenario: 异构格式统一归一

- **WHEN** 同次接入含 `D/M/YYYY`、`YYYY-MM-DD H:M:S.fff`、纯日期等多种格式列
- **THEN** ETL MUST 逐列正确解析并归一为 ISO,MUST NOT 因全局格式假设而误解析

### Requirement: 化验/检查接入外部数据通道

`column_mapping.yaml` 与 ETL SHALL 支持化验/检查表的列映射与转换,使外部医院数据可被既有 `search_lab_results`/`search_examinations` 工具消费。

#### Scenario: 外部化验进通道

- **WHEN** 外部医院提供化验表并完成映射
- **THEN** ETL MUST 产出可被 `LabLoader`/`search_lab_results` 消费的化验数据

### Requirement: 麻醉/病理视图工具

系统 SHALL 提供 `search_anesthesia`/`search_pathology` 视图工具:麻醉转调 notes 子阶段过滤 + `shi_ss` 字段;病理聚合 lab `specimen=病理` + notes 子阶段。两者 status 为 view,输出 MUST 标注其为视图/弱信号(暂不参与判定)。

#### Scenario: 麻醉视图

- **WHEN** 调 `search_anesthesia(patient_id)`
- **THEN** 系统 MUST 从 notes 麻醉子阶段 + `shi_ss.anst_mtd_name` 聚合返回,并标注视图来源

#### Scenario: 病理视图

- **WHEN** 调 `search_pathology(patient_id)`
- **THEN** 系统 MUST 聚合 lab `specimen=病理` 与 notes 病理子阶段返回,并标注视图来源

### Requirement: Tool registry 按 status 注册

tool registry SHALL 读 manifest,据每个 spoke 的 `status` 注册:`live` 注册真工具、`view` 注册视图工具、`stored` 或无对应 tool 的 spoke 注册兜底 stub。兜底 stub MUST 返回"已接收·暂不参与判定"而非报错。

#### Scenario: 无 tool 的 spoke 不崩

- **WHEN** agent 触及一个 stored spoke(无专用工具)
- **THEN** registry 提供的兜底 stub MUST 返回"已接收·暂不参与判定",MUST NOT 抛错或静默
