## MODIFIED Requirements

### Requirement: bulk 规则独占默认药品裁决且按语义分工

药品适应症/限定的默认裁决 MUST 仅由 bulk 规则产出，但 bulk MUST 按语义分工而不是合并为一个超级规则：RD04 独占肿瘤资格的 `INSURANCE_PAYMENT` 与 `GUIDELINE_INDICATION`，R007/RD01/RD02 MUST 排除已经迁入 RD04 的肿瘤候选，RD03 MUST 继续独立处理禁忌/安全语义。RD10-RD37 精选单药规则无论处于 `drafting` 还是 `abandoned` 都 MUST NOT 进入 `audit-patient` 默认执行集与 router index 的可执行集合；其是否可以处于 `abandoned` 必须由知识迁移门禁决定，不能按规则编号或仅凭同药同类型 bulk 行统一判定。肿瘤药知识 MUST 按 `policy_scope` 区分医保支付与指南适应证，同一患者、同一药品、同一 policy scope MUST NOT 因规则重叠产生多条 VIOLATION；不同 scope 的状态必须可关联到同一药品概念和 knowledge release，而不能互相冒充。

#### Scenario: 精选规则不再运行

- **WHEN** 患者使用了艾普拉唑钠，执行 `javert audit-patient <pid> --priority all --use-router`
- **THEN** 该药仅由对应 bulk 规则审计一次，RD20 不出现在执行集合中，无论 RD20 因迁移进度处于 drafting 还是 abandoned

#### Scenario: 肿瘤资格由 RD04 独占

- **WHEN** 一个肿瘤药同时有医保限定和指南适应证并进入默认审计
- **THEN** RD04 分别产出两个 policy scope 的资格状态，R007/RD01/RD02 不再对该肿瘤候选产出重复资格结果
- **AND** RD03 只在存在独立安全知识和候选时处理禁忌/安全语义

#### Scenario: 精选规则尚未完成知识迁移

- **WHEN** 一条 RD10-RD37 规则仍有未验证的临床扩展、证据策略或回归金标知识原子
- **THEN** 该规则保持或后退为 drafting 并标注迁移缺口，不进入默认执行集
- **AND** 系统 MUST NOT 因 bulk 已有同药同类型候选而把该规则标记为已完整取代

#### Scenario: 精选规则完成知识迁移

- **WHEN** 一条精选规则的所有知识原子均已映射并通过等价回归验证
- **THEN** 该规则 MAY 标记为 abandoned 且 notes 指向 bulk 所有者与迁移清单
- **AND** 原 YAML、知识原子和回归用例继续保留

#### Scenario: bulk 覆盖不缩水

- **WHEN** 精选规则退出默认执行集后，对同一患者重跑药品审计
- **THEN** 精选规则原可命中的药品仍落在对应 bulk 所有者的候选集合内
- **AND** 迁移待审状态不得造成生产候选覆盖静默减少

#### Scenario: 恢复路径存在

- **WHEN** 需要临时单独复核某一精选药品规则
- **THEN** 该 RD yaml 仍在 `configs/rules/` 且可由 `--rules RDxx` 显式指定作为迁移对照
- **AND** 该显式复核结果不得与默认 bulk 结果同时写成两个生产裁决

#### Scenario: 医保和指南来源不混称

- **WHEN** 同一肿瘤药同时存在医保支付树和指南适应证树
- **THEN** 两个状态引用同一药品概念但保留不同 policy scope 和来源
- **AND** 指南状态 MUST NOT 被展示为法定说明书结论

## ADDED Requirements

### Requirement: Oncology audit loads only a published knowledge release

肿瘤药审计 SHALL 从已发布 release 编译且 schema/checksum 合法的本地 JSON 资产加载规则。未批准 revision、staging 批次、draft 数据、`unsupported` 条件、来源不完整条目或 checksum 不一致资产 MUST NOT 参与自动裁决。运行时 MUST NOT 在患者审计请求中依赖 142 `[知识库_work]` 在线可用。

#### Scenario: Knowledge database is unavailable during audit

- **WHEN** 已部署的发布 JSON 合法但 142 `[知识库_work]` 不可连接
- **THEN** 患者审计继续使用本地发布资产，不因 authoring 数据库离线而失败

#### Scenario: Asset checksum is invalid

- **WHEN** 本地肿瘤知识资产内容与 release manifest checksum 不一致
- **THEN** 运行时拒绝将该资产用于自动裁决并显式报配置错误

### Requirement: Pre-2026 cases use current release with a temporal warning

对于服务日期早于 active release 最早 `effective_from` 的病例，系统 SHALL 按 `APPLY_CURRENT_RELEASE_WITH_WARNING` 使用执行时 active release 正常完成结构化求值和自动裁决。结果 MUST 记录 `temporal_applicability=BEFORE_EFFECTIVE_WINDOW`、`effective_date_enforced=false`、实际服务日期、声明有效期、release/revision/source 版本，并显示“核查当期指南/医保限定是否适用”警告。

#### Scenario: 2025 case is audited with the initial release

- **WHEN** 服务日期为 2025 年且 active release 声明有效期为 2026-01-01 至 2027-12-31
- **THEN** 条件树照常求值并可产生 CLEAN、VIOLATION 或 INCONCLUSIVE
- **AND** 结果携带越界状态、当前 release 身份和时间核查警告

#### Scenario: Historical result is read after a newer release

- **WHEN** 已有 2025 审计结果引用旧 release，之后发布了新 release
- **THEN** 旧结果保持原 release 引用和原裁决，不被静默回填

### Requirement: In-window and future-expired dates remain distinct

服务日期位于规则声明有效期内时，系统 SHALL 正常执行生效期选择且不显示历史越界警告。服务日期晚于 active release 的最后 `effective_to` 时，系统 MUST NOT 因历史 fallback 策略而静默沿用过期知识；缺少覆盖未来日期的新 release 时 SHALL 返回 `REVIEW_REQUIRED` 或等价 fail-closed 结果并提示更新知识版本。

#### Scenario: 2026 case is in window

- **WHEN** 服务日期位于 2026-01-01 至 2027-12-31 且存在唯一 approved revision
- **THEN** 系统按该 revision 正常裁决且 `temporal_applicability=IN_WINDOW`

#### Scenario: Case is after the last released window

- **WHEN** 服务日期晚于 2027-12-31 且没有后续发布版本
- **THEN** 系统不使用 `APPLY_CURRENT_RELEASE_WITH_WARNING` 作为未来日期兜底
- **AND** 结果要求人工复核并提示发布适用版本

### Requirement: Audit results preserve release and dual-source provenance

每个使用结构化肿瘤知识的审计结果 MUST 保存 knowledge release ID、rule revision ID、policy scope、source document/fragment references、声明有效期、实际服务日期、时间适用性和编译资产版本。医保和指南状态均存在时，结果 MUST 能按 drug concept 关联，同时保持各自证据和状态独立。

#### Scenario: Expert traces a violation

- **WHEN** 专家在工作台查看一条肿瘤药违规结果
- **THEN** 可以定位到发布 release、规则 revision、来源类型、来源原文锚点、专家审核版本和时间策略

#### Scenario: Guideline and insurance disagree

- **WHEN** 指南适应证状态与医保支付状态不同
- **THEN** 系统分别显示两个状态和各自证据
- **AND** 不用其中一个状态覆盖或改名为另一个状态

## RENAMED Requirements

- FROM: `### Requirement: bulk 规则独占药品审计`
- TO: `### Requirement: bulk 规则独占默认药品裁决且按语义分工`
