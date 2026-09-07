## ADDED Requirements

### Requirement: Explicit encounter resolution
系统 SHALL 在shanghai模式按指定院区和首页SYXH解析唯一BAH与就诊JZLSH，要求首页、小结、登记的一致身份及唯一性，不按时间最近值猜选。

#### Scenario: Independent identifiers match
- **WHEN** 同卡两次住院各有不同SYXH/BAH/JZLSH且同次入院偏差4秒、出院相同
- **THEN** 每个输入只返回本次费用和文书，内部键统一为对应SYXH

#### Scenario: Invalid or conflicting linkage
- **WHEN** 首页/小结/登记缺失、多行、卡号不一致、时间占位或差异超过60秒
- **THEN** 返回无PHI的错误，不启动该患者审计

### Requirement: Source scoped extraction
系统 MUST 按源表正确的键及院区取数，保持legacy调用兼容，不查询其他院区或同卡历次记录。

#### Scenario: Document key differs
- **WHEN** 文书JZLSH等于BAH而非就诊流水号
- **THEN** 按文书BAH取数并转换为首页内部键，1900时间保留为空、无效正文不遮蔽标准小结

#### Scenario: Operation name and charge integrity
- **WHEN** 源库使用TB_OPERATION_DETAIL，且真实费用按标准MXFYLB编码
- **THEN** 使用正确表名与标准类别；连接导致同一收费复合主键重复时拒绝导出

### Requirement: Shared and bounded execution
ETL和工作台 SHALL 共用同一患者解析；新模式仅在有费用和有效文书时允许审计快照，批次逐患者隔离并保留tag。

#### Scenario: No fees for selected admission
- **WHEN** 七月首页没有本次费用而同卡八月有费用
- **THEN** 七月预检失败，不使用八月记录填补

#### Scenario: Legacy compatibility
- **WHEN** 未启用shanghai模式
- **THEN** 原有legacy测试表取数保持兼容
