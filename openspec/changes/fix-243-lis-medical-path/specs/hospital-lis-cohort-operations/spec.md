## ADDED Requirements

### Requirement: Process the entire frozen cohort
批跑 SHALL 冻结四表路径的全量候选名单，逐人处理并区分审计成功、Router跳过、数据缺失、归属待核查和失败，MUST NOT 只选择最近1500人或把跳过人数视为成功。

#### Scenario: Some candidates lack data
- **WHEN** 名单中一部分患者的住院关联或费用/文书不完整
- **THEN** 记录这些患者的独立状态，继续其他可处理患者，并保持总名单可对账。

### Requirement: Background execution and verifiable resume
后台任务 SHALL 使用既有任务锁、原配置、统一新标签及原子进度；恢复 SHALL 保持名单和标签，跳过已验证完成患者，对不确定的在途患者停止自动重跑。

#### Scenario: SSH closes
- **WHEN** 操作者关闭跳板机SSH窗口
- **THEN** tmux内任务继续运行，状态命令可查看计数。

#### Scenario: Crash after audit start
- **WHEN** 进度中有STARTED但日志无可靠完成凭据
- **THEN** 恢复标记该患者需核对，不重复调用审计或写入结果。

### Requirement: Offline patch is bounded and reversible
补丁 SHALL 验证基线和受控文件摘要，备份后原子替换；仅正常停止已确认Web，拒绝覆盖未知改动或运行中任务。安装及回滚 MUST NOT 修改中台业务表、配置或历史结果。

#### Scenario: Existing patches
- **WHEN** 在已安装DETAIL及侧栏补丁的249上安装
- **THEN** 保留DETAIL/STFSJ和侧栏修复，回滚只撤销本次LIS增量。

### Requirement: Deliver a complete operational folder
交付 SHALL 包括补丁ZIP及校验、已解压目录、启动/状态/恢复/回滚命令、只读诊断与详细中文教程，并明确本地测试不代表院内已安装或全部患者已审计。

#### Scenario: Windows jump host transfer
- **WHEN** 经Windows中转上传ZIP或解压后的文件夹
- **THEN** 教程分别提供249上的完整性校验、安装和验收步骤，无需联网安装依赖。
