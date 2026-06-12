## MODIFIED Requirements

### Requirement: 药品命中匹配

`drug_audit_lookup` SHALL 以**国家医保药品码精确 join** 为主路判定患者用药是否命中监管知识库, 仅在 fee 行无国家码时退回通用名 stem 子串兜底。系统 MUST NOT 仅凭通用名子串命中 (该法会让相似药名串味)。

#### Scenario: 国家码精确命中

- **WHEN** 患者某 fee 行 `med_list_codg` 非空且 ∈ 某知识点的 KB code set
- **THEN** 该药判为命中该知识点, 回传原始 fee 名 + `basis` + `检出逻辑` + 该 fee 行的国家码

#### Scenario: 相似药名不串味

- **WHEN** 患者用药为 `奥美拉唑肠溶胶囊` (国家码 `XA02BC...01...`), 而 KB 含 `艾司奥美拉唑` / `艾普拉唑` 等子串相似但**国家码不同**的条目
- **THEN** 该 fee **不**命中那些相似条目 (码不在其 code set), 不产生串味假阳性

#### Scenario: 无国家码兜底

- **WHEN** 患者某 fee 行 `med_list_codg` 为空 (`nan`/空串), 但通用名 stem 子串命中某知识点
- **THEN** 判为命中, 且结果标 `needs_review=true` (提示该命中靠名兜底、需人工复核剂型/复方歧义)

#### Scenario: 子串相似但码不同必须放过

- **WHEN** KB 内 `丁苯那嗪` ⊂ `氘丁苯那嗪` 这类「通用名互为子串、国家码不同」的条目, 患者只用了其中一个
- **THEN** 仅命中码匹配的那个知识点, 另一个**不**命中

## ADDED Requirements

### Requirement: 药品知识库携带国家药品码集合

`drug_audit_kb.json` SHALL 为每个受监管通用名携带其**国家医保药品码集合** (`codes[]`), 由 `build_drug_kb.py` 从 `data/药品类规则/含代码/` 4 表按通用名聚合得到, 与原无码 4 表的 `basis`/`检出逻辑` 按通用名 join。该字段为增量, MUST NOT 破坏既有 `entries`/通用名键的读取。

#### Scenario: KB 重建后受监管药含码

- **WHEN** 运行 `scripts/build_drug_kb.py` 重建 KB
- **THEN** `drugs[<受监管通用名>]` 含非空 `codes[]`, 且每个 code 为 `X` 开头的国家医保药品码格式

#### Scenario: 含代码表与无码表通用名漏配可见

- **WHEN** 含代码表某通用名经 stem 归一后仍无法与无码表 entries join
- **THEN** `build_drug_kb` 输出 `log.warning` 并将该项落入核对清单, MUST NOT 静默丢弃

### Requirement: 前后端共用确定性码匹配

`drug_audit_lookup` 与 `hit_resolver` SHALL 共用同一个纯函数 `code_match`, 确保审计命中与工作台「命中项目」编码展示用**完全一致**的匹配规则 (单点真值)。

#### Scenario: 两处匹配结果一致

- **WHEN** 同一患者同一药, 审计工具判为命中知识点 X (码路)
- **THEN** 工作台 `hit_resolver` 解析该命中项目时, 落到**同一** fee 行与**同一**国家码, 不出现两处分叉
