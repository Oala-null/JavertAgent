## 1. 失败基线与范围核实

- [x] 1.1 用去标识 fixtures 为四个已知漂移建立先失败的回归测试：退费净数量为 1 仍被判疑似、无收费行却出现“定位”命中、同一 H/I 类别重复分组、工作台直接展示内部 rule ID/原始 evidence JSON；保存修复前断言而不写入患者原文。
- [x] 1.2 运行 `.venv/bin/javert list` 和当前 ready 规则解析，逐条核实哪些规则的违规成立条件确为“同一目标收费项目净数量 > 1”，形成 `PR-D001` 的显式 scope；一次即违规、组合项目、串换、虚构、限定支付和 M1 主附项目规则必须进入排除说明或 near-negative。
- [x] 1.3 定位工作区内被 Git 忽略的“两库汇总”静态参考工作簿，程序化读取 H/I 列并固化带来源摘要的版本化最小快照，再与 `configs/behavior_names.yaml` 当前 ready 规则映射对账；记录正常 pair、重复内部类型和显式例外，不在文档中手抄会漂移的库存数量。
- [x] 1.4 在不改生产状态的前提下建立原文跳转故障基线：用本地故障注入复现慢/断 hub，并在获准访问 62 时分别采集回环、`--noproxy`、浏览器路径与无 PHI 阶段时间，明确 502 是应用、SQL 还是代理分支后再选择修复点。

## 2. Promise 资产模型与治理校验

- [x] 2.1 新增 DriftCase、PromiseDefinition、PromiseCase、PromiseMatch 和 PromiseTrace 的严格 schema/类型，限制状态枚举、允许 facts 字段和禁止敏感标识；为合法/非法资产补单元测试。
- [x] 2.2 实现 typed evaluator registry 与 Promise loader，只接受注册 kind 和显式 scope，拒绝任意表达式/代码字段；验证未知 kind、未知参数和未知 rule scope 都 fail closed。
- [x] 2.3 实现版本链、唯一 active head、`supersedes`、source case 状态和 active 内容不可就地修改校验，并用分叉、循环、缺版本和合法替代 fixtures 覆盖。
- [x] 2.4 落地首批去标识 DriftCase、`PR-D001 refund-net-single-clean` 定义、positive 与 near-negative 案例；每个案例只保留最小语义事实和确认来源，不含真实 patient/run/ownership 标识。
- [x] 2.5 实现 `javert promise validate`，一次完成 schema、registry、scope、版本链、正反例、隐私、H/I 映射和显式例外检查；保证人类输出与 `--json` 输出稳定且失败返回非零。

## 3. 离线确定性 Harness

- [x] 3.1 实现 `javert promise run` 的纯函数案例执行器，覆盖全部 active Promise、promoted 历史案例、superseded 边界案例和公开 presenter/API fixtures。
- [x] 3.2 对每个案例至少重复执行两次并规范化比较 verdict、reason code、trace facts、公开解释、hits 和类别；实现 `NON_DETERMINISTIC_OUTPUT` 与 `PROMISE_CONFLICT` 失败码。
- [x] 3.3 增加外部依赖隔离门禁，测试中禁止 Promise harness/evaluator 访问 LLM、网络、SQL Server 或 hub；任何访问尝试必须快速失败且不回显连接信息。
- [x] 3.4 实现安全的人类/JSON 报告，只输出 case/promise ID、状态、reason code、计数和耗时分桶；为成功、unexpected match、历史回归和冲突验证退出码与报告契约。

## 4. 首条裁决 Promise 与终局锁

- [x] 4.1 基于现有 `fee_netting.NetItem` 实现 `refund-net-single-clean` evaluator：仅在显式 scope、唯一目标组、确有负数量退费、数量可解析且 `net_qty <= 1` 时返回 LOCKED CLEAN，其余情况返回 NOT_APPLICABLE。
- [x] 4.2 将 active `decision_pre_llm` Promise 接入 Runner：在任何可提前持久化的普通裁决前求值，命中时零 LLM、`tool_calls=[]` 并生成最小 trace；未命中路径的现有调用顺序和输出保持不变。
- [x] 4.3 实现同一审计多 terminal Promise 的冲突检测；不按加载顺序抢占，返回 INCONCLUSIVE + 内部 `PROMISE_CONFLICT`，日志仅含安全 Promise 标识。
- [x] 4.4 修改 verdict gate：合法 active LOCKED trace 原样绕过所有普通降级，未锁定/伪造/不完整 trace 继续既有 gate；为低置信、计数闸和规则专用闸分别补锁定与非锁定测试。
- [x] 4.5 修改 persist drift guard：历史 VIOLATION 后的新 LOCKED CLEAN 仍保存 CLEAN，仅追加 `historical_conflict=true` 和匿名指标；普通 CLEAN 的既有 V→I 防护保持不变。
- [x] 4.6 用同一批正反例完成 runner→gate→persist 组合测试，证明正例恒为 CLEAN、零 LLM，反例不被 Promise 清掉，且重复运行规范化结果一致。

## 5. SQLite 与 SQL Server 兼容存储

- [x] 5.1 为 SQLite 结果表/模型增加可空 `promise_trace_json` 和幂等迁移，验证新行往返、旧行 null 读取、重复迁移与旧数据库启动。
- [x] 5.2 为 SQL Server schema/模型增加同名可空兼容列，更新 `ensure-mssql-schema` 幂等逻辑；测试不得连接或写入 142 `sh_yb_platform`。
- [x] 5.3 更新结果 persister、SQLite/SQL Server 双写、sync 与序列化接线，保证 trace 语义一致且不改变旧 verdict/reasoning/evidence/eligibility 字段。
- [x] 5.4 增加旧行兼容和双写测试：无 trace 行不回填、不静默重评，带 trace 行可读可同步，异常/日志不输出完整 facts 或患者标识。

## 6. 医生可读公开输出

- [x] 6.1 实现单一纯函数 public presenter，输出 `conclusion/audit_items/charge_facts/basis/clinical_evidence/review_needs`，只投影有确定来源的事实；缺失信息留空或进入 `review_needs`，不得从 LLM 散文猜测。
- [x] 6.2 收紧 reasoning 公共化：工作台默认解释和 `public_explanation` 去除 R/RD 编号、tool/gate/run 词、英文 verdict 和内部 reason code；保留既有 API 字段但不在医生默认视图渲染原始 evidence JSON。
- [x] 6.3 收紧 hit resolver：fee/drug hit 必须关联患者实际费用组且净数量 > 0；删除无费用行时的“定位”/搜索词 fallback，并在渲染旧 `anchors_json` 时重算或过滤抽象命中。
- [x] 6.4 将工作台类别分组键改为 `(behavior_code, behavior_name)`，使用 H/I 源表门禁并对串换保留可见显式 exception key；验证同一公开键只有一个组、组内多规则卡不丢失。
- [x] 6.5 将 public presenter 接入工作台详情、SSE 和相关模板/JS，添加结构化区块、空态和复核提示；保证内部调试数据不进入医院默认页面。
- [x] 6.6 以 additive 方式把 `public_explanation` 和可选 Promise 摘要接入 2C v1/v2/v3，保持既有字段名、单规则卡片数量与 v3 收费行展开语义；同步 BFF contract tests。
- [x] 6.7 增加 presenter/hit/category/workbench/2C 的单元、路由、模板和序列化测试，覆盖旧行、CLEAN 空 hits、真实收费 hit、类别合组、内部术语清洗与字段只加不删不改名。
- [x] 6.8 根据 62 医生反馈修复公开解释过度压缩：新增默认可见的中文化 `narrative`，完整保留已持久化 reasoning 中的收费、诊断、证据缺口和降级理由；结构化数组仍不得从散文猜测，原始 evidence JSON 与内部术语仍不展示。

## 7. 原文跳转可靠性与诊断

- [x] 7.1 为 HubRawSource 增加无 PHI 阶段诊断与可注入 deadline，记录 source/tab/outcome/duration_bucket/cache_hit/error_code；用慢查询、断连接和代理路径 fixtures 先证明现有失败模式。
- [x] 7.2 将 raw source 成功缓存细化为 per-patient/per-tab，并实现 notes/fees/labs 的最小查询集合；失败、超时和部分响应不缓存，恢复后重试可成功。
- [x] 7.3 扩展 `GET /api/patient/{pid}/raw?tab=...`：tab 请求在早于代理预算的 deadline 内成功或返回结构化 HTTP 503 可重试错误，真实双 miss 仍为 404；无参数全量接口字段和既有异常降级兼容。
- [x] 7.4 修改工作台原文前端：按 anchor 类型先懒加载目标 tab，成功后定位/高亮，切页再请求；将源暂不可用、定位失效和真实无数据分别显示为中文提示并提供安全重试。
- [x] 7.5 增加 raw source/API/模板/JS 测试，证明 fees 跳转不等待 notes/labs、失败不污染缓存、旧全量请求兼容、503/404 可区分且页面不只显示 `HTTP 502`。

## 8. 文档、组合门禁与受控发布

- [x] 8.1 更新 README、`docs/how_javert_works.md`、规则/模板维护指南和专家工作台用户指南，说明 DriftCase→Promise→Harness 流程、最小边界原则、公开解释与原文重试；不手抄易漂移库存。
- [x] 8.2 更新 `docs/2c对接_javert审计服务.md` 的 additive 字段与兼容说明，并更新部署 runbook、CHANGES 和必要的隐私/诊断约束；核对下游 BFF 契约。
- [x] 8.3 先运行 Promise 定向测试与 `javert promise validate/run`，再运行 audit/gate/store/workbench/2C/raw-source 受影响模块组合测试和一个去标识端到端命令；原样记录 collected/pass/skip/fail/error 及既有债务排除项。
- [x] 8.4 若实现中改变规则状态、关键词、模板渲染或 M8，运行 `scripts/build_rule_mapping.py` 并验证 YAML 与 router index 一致；未改变时在验证记录中明确标为不适用。
- [x] 8.5 运行 `openspec validate add-evolving-promise-harness --strict`，逐条核对 tasks、spec 场景与真实测试结果，未完成的生产诊断或发布项不得勾选完成。
- [x] 8.6 在获得 62 上线授权后按 `production-62` 受控 HEAD artifact/install→schema→restart 流程发布，并验证两端 HEAD、工作树 clean、systemd、登录页、关键环境实值、SQL/Hub、2C v3、Promise 锁与原文三段路径；未获授权时保持此任务 pending，不用本地结果冒充上线完成。
- [x] 8.7 为 `narrative` 补 presenter、模板、2C additive 契约与去标识回归测试，更新工作台/2C 文档和验证记录，运行相关 Web 组合测试、Promise 门禁与严格 OpenSpec 校验。
- [ ] 8.8 提交纠偏代码后按受控 artifact/install 发布 62，并以去标识页面断言确认长审核说明默认可见、内部术语/原始 JSON 不可见，同时复验服务、HEAD 与工作树 clean。
