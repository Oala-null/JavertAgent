## ADDED Requirements

### Requirement: Candidate roster follows the validated report path
系统 SHALL 按首页KH→住院医疗记录KH/JZLSH→LIS报告→三字段指标关联列出全部匹配首页，保持原E项范围并按SYXH去重，不加入小结或费用前置条件。

#### Scenario: Summary visit differs
- **WHEN** 首页经医疗记录可关联到指标，但小结就诊号不同
- **THEN** 该首页仍在候选名单，候选总数不因此变为0。

### Requirement: Admission ownership is explicit
系统 SHALL 将同院同卡、正常医疗记录及匹配住院日期能唯一归属的报告映射到该首页；不同院区、其他次住院或无法唯一归属的报告 MUST NOT 进入其审计化验集。

#### Scenario: Different lab and financial visit numbers
- **WHEN** 医疗记录的就诊号与财务就诊号不同，但报告经卡信息和住院日期唯一归属当前首页
- **THEN** 该报告指标进入当前首页化验集，费用仍按原财务就诊号读取。

#### Scenario: Shared card across admissions
- **WHEN** 同卡存在多次住院，报告只能归属其他首页或日期仍有多个候选
- **THEN** 当前首页只获得明确标注的待核查信息，不将这些指标用于审计。

### Requirement: Audit and Workbench use the same LIS contract
ETL与Workbench SHALL 复用hub_source的LIS映射，保留原化验列和原文API字段，并新增归属计数/待核查展示。空缓存 SHALL 在短TTL后失效。

#### Scenario: Newly synchronized labs
- **WHEN** 原文首次为空后中台同步了可归属指标
- **THEN** TTL后新读取能获得新数据；旧审计快照不被静默改写。

### Requirement: Snapshot proves its source
上海模式审计 SHALL 在规则和LLM前验证新LIS来源版本、文件摘要和计数，待核查数据 MUST NOT 被当作本次住院化验输入。

#### Scenario: Stale or modified snapshot
- **WHEN** 使用旧来源或化验CSV被更改
- **THEN** 审计在调用LLM之前拒绝并要求重新抽取。
