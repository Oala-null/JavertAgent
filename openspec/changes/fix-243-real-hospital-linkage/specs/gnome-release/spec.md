## ADDED Requirements

### Requirement: Reproducible pushed release
发布工具 MUST 从gnome-243已提交且远端同SHA的HEAD生成完整受控运行包，并包含提交号及文件摘要。

#### Scenario: Dirty or unpushed source
- **WHEN** 受控工作目录有改动或远端HEAD不一致
- **THEN** 拒绝正式打包，不能包含未提交文件

### Requirement: Preserve onsite state
安装和回滚 MUST 保留现场环境、密码、模型、虚拟环境、患者数据、SQLite和审计历史，不执行数据库DDL或在线依赖更新。

#### Scenario: Upgrade and rollback
- **WHEN** 操作者确认服务和批跑已停止并安装已验包版本
- **THEN** 备份受控旧代码，受保护配置内容不变；回滚仅恢复受控代码

#### Scenario: Invalid archive
- **WHEN** 包含越界路径、链接、非白名单文件或摘要不符
- **THEN** 在覆盖代码前拒绝安装

### Requirement: Separate local and onsite verification
交付文档 SHALL 区分本地测试、Git推送、包生成和院内运行事实。

#### Scenario: Hospital not accessible
- **WHEN** 尚未在医院完成真实患者审计和工作台检查
- **THEN** 明确标记待现场验收，不宣称部署成功
