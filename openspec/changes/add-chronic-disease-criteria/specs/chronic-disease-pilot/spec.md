## ADDED Requirements

### Requirement: 候选召回与资格结论分层

慢病试跑 SHALL 先以诊断、文书、检验、检查、手术等宽召回信号形成 `candidate`，再把候选交给与病种匹配的已审核 2025 条件树求值。诊断名称、ICD、关键词或单个阳性叶子命中 MUST 只表示候选，MUST NOT 被写成 `QUALIFIED`、`SATISFIED` 或专家金标。只有条件树 `execution_status=EVALUATED` 且根节点为 `SATISFIED` 的结果，才可进入本次约 50 例的系统资格通过队列；`UNKNOWN`、`CONFLICT`、`NOT_SATISFIED` 或 `execution_status=BLOCKED` MUST NOT 为凑足样本而改标。系统产出的 `QUALIFIED` 仍是待专家评价的试跑结论，MUST NOT 被称为 gold；gold 标签只能来自独立的专家确认流程。

#### Scenario: 仅有诊断命中仍只是候选

- **WHEN** 某患者诊断名称命中慢病病种，但完整条件树尚未求值或关键叶子为 `UNKNOWN`
- **THEN** 该患者只进入候选池，不得进入 `QUALIFIED` 队列，也不得计入约 50 例通过样本

#### Scenario: 根节点满足后进入系统通过队列

- **WHEN** 候选由匹配病种的已审核 2025 条件树完成求值，且 `execution_status=EVALUATED`、根节点状态为 `SATISFIED`
- **THEN** 该候选的 `qualified=true`、`qualification_disposition=QUALIFIED`，可以进入试跑样本，但仍不得自动标记为专家 gold

#### Scenario: 样本不足不以低质量结果补位

- **WHEN** 完整求值后可用的 `QUALIFIED` 唯一病例少于目标数
- **THEN** 试跑报告 SHALL 如实记录缺口，不得以 `UNKNOWN`、`CONFLICT`、`NOT_SATISFIED`、`BLOCKED` 或重复病例补足 50

### Requirement: 试跑清单与输出去标识

候选筛选和试跑 MUST 只读取受控数据源。执行所必需的原始患者标识只可存在于受限运行时内存或权限为 `0600`、目录权限为 `0700` 的临时执行清单中，成功与异常退出均 MUST 清理；该清单、去标识盐和 ownership 标识 MUST NOT 进入 Git。命令标准输出、标准错误、日志、QA 报告和可提交产物 SHALL 只包含聚合统计或带秘密盐的稳定病例引用，MUST NOT 输出患者号、姓名、原始病历片段、未盐化哈希、run id 或 ownership id。

#### Scenario: 候选筛选完成时不打印患者号

- **WHEN** 候选筛选从受控数据源召回患者并生成试跑清单
- **THEN** 控制台和报告只显示病种级候选数、去标识病例引用及排除原因统计，不出现任何患者号、姓名或原始病历文本

#### Scenario: 运行时临时清单异常后仍被清理

- **WHEN** 试跑在写入第一个结果前因数据库或求值异常退出
- **THEN** 含原始患者标识的临时目录和文件仍被删除，Git 工作树及普通日志中不存在该标识

#### Scenario: 去标识引用不可离线反推

- **WHEN** QA 产物包含某病例的稳定引用
- **THEN** 该引用由受控环境注入的秘密盐生成，产物不包含盐、原始标识或未盐化摘要

### Requirement: 五例烟测后追加至约五十例

试跑 SHALL 使用同一受控 pilot manifest、同一条件资产 revision/checksum 和同一 `batch_tag=Chronic_Disease` 分两阶段运行。第一阶段 MUST 先运行 5 个唯一病例；只有在 5 例均产生可反序列化的 `clinical_criteria_evaluation`、无失败、无隐私泄露、无批次混入且阶段对账通过后，第二阶段才可追加最多约 45 个唯一病例。两阶段合计目标 SHALL 为约 50 个根节点 `SATISFIED` 的唯一病例，并 SHALL 优先覆盖不同病种、OR 分支、计数/阈值与时间窗，而不是只抽取最常见病种；第一阶段 5 例计入总数。

#### Scenario: 五例烟测通过后继续第二阶段

- **WHEN** 第一阶段 5 个唯一病例全部得到 `QUALIFIED`、结构化结果可读、隐私 QA 和双库阶段对账均通过
- **THEN** 第二阶段可在同一 manifest、criteria checksum 和 batch ownership 下追加最多约 45 个唯一病例，第一阶段结果不重跑、不重复计数

#### Scenario: 烟测失败阻止扩批

- **WHEN** 第一阶段出现求值失败、不可解析结构化结果、标识泄露、意外 batch row 或对账不一致中的任一项
- **THEN** 第二阶段 MUST NOT 启动，已写结果保持可审计并在报告中标记阶段未通过

#### Scenario: 覆盖优先而非单病种凑数

- **WHEN** 多个病种和条件分支均有 `QUALIFIED` 候选
- **THEN** 第二阶段抽样 SHALL 优先增加尚未覆盖的病种、逻辑分支、数值阈值、重复测量及时间跨度，并在去标识报告中给出覆盖矩阵

### Requirement: Chronic_Disease 批次防混与幂等续跑

两阶段写入的每一条试跑结果 MUST 携带精确的 `batch_tag=Chronic_Disease`，并 MUST 绑定本次受控 manifest、criteria revision/checksum 和不可公开的 batch ownership。首次写入前系统 SHALL 检查 SQLite 与 SQL Server 中该 tag 的既有行；若发现 ownership、manifest 或 criteria checksum 不一致，MUST 拒绝运行，且不得覆盖、删除或吸收既有行。第二阶段或失败重试只可在所有权一致时续跑，并 SHALL 以稳定 replay key/预期 `(case, rule)` 集合跳过已成功项、防止重复写入；任何其他 tag 或无 tag 的历史结果 MUST 保持不变。

#### Scenario: 发现同 tag 的外来结果立即拒绝

- **WHEN** 预检发现数据库中已有 `batch_tag=Chronic_Disease` 的结果，但其 ownership、manifest 或 criteria checksum 与本次试跑不一致
- **THEN** 试跑在读取患者正文和写入结果前失败，不覆盖、不追加，也不把既有行计入本次约 50 例

#### Scenario: 第二阶段安全接续第一阶段

- **WHEN** 第一阶段 5 例已存在且 ownership、manifest 和 criteria checksum 均与本次一致
- **THEN** 第二阶段仅执行预期集合中尚未成功的病例规则对，已有 5 例不重复写入，全部新行继续使用 `Chronic_Disease`

#### Scenario: 重试不触碰其他批次

- **WHEN** 某些本批次结果需要重试或补同步
- **THEN** 操作范围仅包含本次 manifest 和 ownership 下的 replay key/run，其他 batch tag 与 baseline 行的内容、同步状态和时间戳均不改变

### Requirement: 双库对账与 pending 安全

每个阶段结束后系统 MUST 以私有 manifest 中的预期 `(case, rule)` 集合对 SQLite 与 SQL Server 逐项对账，并检查缺失项、额外项、重复项、结构化 JSON 可读性、qualification 分布、`batch_tag`、criteria checksum 和同步状态。可提交报告 SHALL 只输出去标识引用和聚合差异。SQL Server 不可达或任一行未同步时，该阶段 MUST 标记为 `pending`/未完成，不得宣称已上 Workbench；补同步 MUST 只处理本次 ownership 明确列出的 pending 结果，MUST NOT 调用或等价执行无范围的全历史 `sync-to-mssql --pending-only`。

#### Scenario: 两端集合完全一致才完成

- **WHEN** 某阶段的预期集合在 SQLite 与 SQL Server 中各恰有一条、字段可解析且全部为正确 tag/checksum
- **THEN** 阶段对账通过，报告给出预期数、实到数、`QUALIFIED` 数和覆盖统计，但不输出患者号

#### Scenario: SQL Server 不可达时保持 pending

- **WHEN** SQLite 已写入本阶段结果但 SQL Server 写入失败
- **THEN** 阶段状态为 `pending` 且不得报告“已上 Workbench”；恢复后只重试本次 ownership 下明确未同步的行

#### Scenario: 历史 pending 不被顺带同步

- **WHEN** 数据库中同时存在其他批次的历史 pending 行
- **THEN** 本试跑的同步或重试不选择这些历史行，其同步状态和内容保持不变

### Requirement: HIV 等高敏感病种真实试跑需显式授权

HIV/艾滋病及配置为同等级的高敏感病种 SHALL 默认从真实患者候选和 5+45 样本中排除。纳入真实病例 MUST 同时满足显式启用高敏感病种、当前操作者通过权限校验、访问目的与批次被审计留痕；任一条件缺失都 MUST fail closed。未获授权时，相关规则仍可使用不含真实患者信息的合成夹具进行结构和求值测试。

#### Scenario: 默认试跑排除 HIV 真实病例

- **WHEN** 操作者按默认参数生成候选并执行 5+45 试跑
- **THEN** HIV 等高敏感病种真实病例不进入候选 manifest、运行队列或 Workbench 批次，报告只记录“默认排除”的聚合计数

#### Scenario: 仅有开关但无权限仍拒绝

- **WHEN** 操作者显式请求纳入 HIV 真实病例但未通过相应权限校验
- **THEN** 系统拒绝访问和运行，写入不含患者号的拒绝审计事件，且不回退为普通候选处理

#### Scenario: 获权试跑仍遵守去标识与范围限制

- **WHEN** 获授权操作者显式纳入高敏感病种
- **THEN** 仅获批 manifest 中的病例可执行，日志和报告仍不得输出患者号或原始病历，访问目的、操作者和批次被留痕

### Requirement: 显式授权的单PDF shadow试跑独立于资格样本批次

用户显式授权的单PDF试跑 SHALL 仅处理指定文件，经现有内部OCR与内部LLM形成临床候选，以 `batch_tag=慢病` 发布候选及待复核结果。该路径 MUST NOT 将shadow、缺失或BLOCKED结果加入5+45的QUALIFIED样本。公开输出只含聚合统计，运行清单与患者原文存于0700/0600私有目录，验收结束清理。CD10默认以关闭状态返回，零患者读取和零LLM。

#### Scenario: 只提供一份脏PDF并授权直接上工作台

- **WHEN** 用户授权一份PDF跳过2C脱敏进行慢病测试，且原文审批和费用完整性尚未完成
- **THEN** 系统仅以临床页运行shadow，在工作台区分“慢病候选命中”和完整资格结论，保留缺失与OCR待核标记，不运行费用完整性未证实的全量费用规则

#### Scenario: 精确续跑不影响旧结果

- **WHEN** 私有清单中某run需重试发布
- **THEN** 只复用相同case、criteria checksum、原临床页和run集合，拒绝冲突，不扫描其他历史pending，不覆盖其他病例原文
