## Context

Javert 的当前裁决链同时存在四类会改变或重新解释结果的组件：规则/LLM、确定性 precheck 与 verdict gate、持久化前的历史漂移防护、工作台/2C 的展示投影。它们解决的问题不同，却没有共享一份“已经确认后不得再次漂移”的契约。现有 FN 案例库度量真 LLM 召回，适合发现模型退化，但依赖模型和数据源，不能作为每次提交都执行的确定性门禁；现有 drift guard 又会把历史 V 后的新 CLEAN 改成 INCONCLUSIVE，即使新 CLEAN 来自更硬的确定性事实。

本 change 把 Promise 定义为从已确认漂移记录中提炼出的**最小、版本化、可执行边界**。Promise 不是完整规则引擎，也不自动从生产批注学习。它只保护已经有充分依据的窄条件，并通过正例与相邻反例证明没有越界。

首批问题跨越审计核心、SQLite/SQL Server 双写、工作台、2C v1/v2/v3 和 142 hub 原文源。实现必须保持：配置优先级不变、旧三态兼容、SSE/2C 字段只加不删不改名、旧行不回填、142 数据中台只读、Git 测试资产无 PHI。

## Goals / Non-Goals

**Goals:**

- 建立从漂移观察、确认、最小化到 Promise 激活/替代的可审计流程。
- 让 active terminal Promise 的结论不受模型、普通 gate、低置信降级或历史漂移防护改写。
- 提供无 LLM、无网络、无生产数据库的快速 harness，阻止所有 active Promise 和既有案例退化。
- 用同一公开 presenter 生成工作台与 2C 的医生可读结构化解释、真实命中项目和唯一行为类别。
- 将原文跳转拆成可诊断、可超时、可重试的按需取数路径，同时兼容既有全量 raw 接口。
- 以退费后同项目净数量不大于 1 的次数型 CLEAN 作为第一条终局裁决 Promise。

**Non-Goals:**

- 不建设可执行任意表达式的规则 DSL，也不替代现有 Rule YAML、precheck、verdict gate 或肿瘤资格条件树。
- 不根据一次模型输出、单条专家自由文本或在线统计自动激活 Promise。
- 不把“净数量不大于 1”推广到禁忌、限定支付、串换、虚构、套餐多项目等一次即可违规或非同项目计数语义。
- 不静默重算或改写历史审计行、专家 review、肿瘤 `eligibility_json`。
- 不在本 change 改变 2C v3 既有收费行展开语义或删除内部追溯字段。
- 不预判 502 的唯一根因；先用阶段诊断区分应用超时、142 查询、服务进程和客户端/代理路径。

## Decisions

### D1 — 三种资产分离：DriftCase、PromiseDefinition、PromiseCase

新增：

- `tests/promise_cases/DRIFT-*.yaml`：语义化、去标识的漂移记录及可执行事实；状态为 `observed/confirmed/promoted/rejected`。
- `configs/promises/PR-*.vN.yaml`：不可变 Promise 版本；状态为 `draft/active/superseded`，含 `kind`、显式 scope、参数、来源案例、版本和 `supersedes`。
- 每个 active Promise 的案例集合同时含 `positive` 与 `near_negative`；前者证明保证成立，后者证明最相邻但不应命中的边界。

DriftCase 是证据，PromiseDefinition 是运行时合同，PromiseCase 是 harness 输入；不把单个患者例外直接当生产规则。真实患者只在受控线下复现中使用，入 Git 前必须重建为语义事实，不能保存患者号、病历原文、run ID 或所有权标识。

*Alternatives:* 直接扩展 `tests/fn_cases`。否：FN runner 的目标是跨模型召回、会连接 hub/LLM；Promise harness 必须离线、确定、适合每次提交门禁。

### D2 — 受控晋升，不做在线自动学习

Promise 从 `draft` 变为 `active` 前必须满足：

1. 所有 `source_cases` 存在且状态为 confirmed/promoted；
2. 至少一个 positive 和一个 near-negative；
3. 适用谓词只能使用已注册的 typed `kind`；
4. scope 显式列出可适用规则或稳定语义 profile，不按公开行为大类整类猜测；
5. harness 全绿、无 active 冲突；
6. 有不含敏感信息的确认依据与版本说明。

激活后不得就地改内容；修正或扩大边界时创建新版本并 `supersedes` 旧版本，旧文件与案例永久保留。默认允许 active 保证集单调增加；若事实证明旧 Promise 错误，只能经新版本显式替代和迁移说明收窄，不能静默编辑。

*Alternatives:* YAML 写 `when: "..."` 任意表达式。否：难做静态验证、冲突分析和安全审计，且会复制一套新规则引擎。

### D3 — typed evaluator 与分阶段 Promise

代码按 `kind` 注册纯函数 evaluator。首期支持三类 phase：

- `decision_pre_llm`：只读 Rule 与确定性患者事实；命中可直接构造终局结果，零 LLM。
- `public_projection`：约束结构化解释、命中和类别，不修改已持久化医学事实。
- `transport`：约束原文取数响应、超时和错误展示。

Evaluator 统一返回 `NOT_APPLICABLE` 或 `PromiseMatch`：`promise_id/version`、`guarantee`、`finality=LOCKED`、机器 reason code、用于落库的最小事实摘要。Evaluator 不读历史 reasoning、LLM 搜索词或自由文本来猜关键条件。

### D4 — 首条裁决 Promise 的最小范围

`PR-D001 refund-net-single-clean` 仅作用显式 scope 内、违规成立条件为“同一个收费项目净数量超过 1”的规则。运行时必须从规则已有机器字段解析出唯一目标费用组，并从 `fee_netting.NetItem` 得到：

- 原始组确有负数量退费 (`has_refund=true`)；
- 目标组唯一且可确定；
- `net_qty <= 1`；
- 原始费用列可用且数量可解析。

四项同时成立才锁定 CLEAN。目标不唯一、数据不可用、仍有目标组净量大于 1、无退费、规则不在显式 scope，均返回 NOT_APPLICABLE，继续原链路。套餐多项目按“同日不同项目数”判定、一次本身即错、M1 主附项目并存等语义必须作为 near-negative，不得被该 Promise 清掉。

这里故意不按 `behavior_code=T380301` 或模板编号整类套用：公开类别相同不代表成立条件相同。

### D5 — 终局锁贯穿 runner、gate 和 persist

`Runner` 在 precheck/LLM 之前构建 Promise 所需的确定性事实。terminal match 时直接生成 `AuditResult(CLEAN)`，`tool_calls=[]`，并携带 `promise_trace`。未命中时行为逐字保持现状。

`promise_trace` 存入新的可空 `promise_trace_json`，最小结构为：

```json
{
  "promise_id": "PR-D001",
  "version": 1,
  "kind": "refund-net-single-clean",
  "finality": "LOCKED",
  "reason_code": "REFUND_NET_QTY_LE_ONE",
  "facts": {"net_qty": 1, "refund_count": 1}
}
```

字段允许业务编码/数量等审计事实，但应用日志与 harness 报告只输出 Promise/case ID、计数、耗时和失败码，不输出 patient_id、项目原文或 trace JSON。

当 `finality=LOCKED`：

- `verdict_gate` 不再评估或改写结论；
- persist drift guard 遇到历史 V 只在 trace 内记 `historical_conflict=true` 并产匿名指标，不能把 CLEAN 改 I；
- 历史行和 review 不改写。

若多个 terminal Promise 对同一次审计给出不同保证，运行时不得按顺序抢占；返回 INCONCLUSIVE + 内部 `PROMISE_CONFLICT` 诊断且不暴露敏感事实。harness 必须在发布前用全部案例阻止可复现冲突。

*Alternatives:* 只用 `gate_tag="退费净1"` 给 drift guard 加例外。否：不可版本化、不可说明来源，也无法扩展到展示/传输 Promise。

### D6 — 单一公开 presenter，内部 trace 与医生界面分离

新增纯函数 presenter，从持久化结果、确定性 hits、rule meta 和可空 promise trace 生成：

```json
{
  "conclusion": {"label": "合规", "summary": "退费抵消后净数量未超过一次。"},
  "audit_items": [],
  "charge_facts": [],
  "basis": [],
  "clinical_evidence": [],
  "review_needs": []
}
```

字段只填有确定来源的事实，不把 LLM 散文拆词后冒充结构化事实。旧 `reasoning/evidence/rule_id` 等 API 字段继续保留；v1/v2/v3 只追加 `public_explanation` 与可选 `promise` 摘要，工作台默认只渲新结构。legacy reasoning 继续经过 `humanize_reasoning` 作为兼容摘要，但不得在公开结构中出现 R/RD 代号、tool 名、gate、run ID、英文 verdict 或原始 evidence JSON。

### D7 — “命中项目”只表示已关联的患者实际事实

公开 fee/drug hit 必须关联患者实际费用组，且该组净数量大于 0；公开名称使用实际收费行名称或经 KB 证明的规范药名，并保留收费行关联。无匹配费用行的 locator、搜索关键词或“未找到”证据不再生成公开 hit；如有审计价值，保留在内部 evidence 或公开 `review_needs`，不能叫“命中项目”。

旧 `anchors_json` 缓存若含无实际关联的 hit，渲染时必须重算或过滤，不能因缓存绕过新合同。CLEAN 结果可以有真实核查对象，但无实际收费关联时 hits 必须为空。

### D8 — 行为类别以公开键分组并做源表门禁

`configs/behavior_names.yaml` 继续作为运行时映射，但 harness 读取仓库内“两库汇总”H/I 列核对所有 ready 规则：正常映射的 `(code,name)` 必须存在于源表；未映射不得回退内部 `violation_type` 进入客户界面。串换当前无正式 I 列编码，保留为带 `exception=true/source_ref` 的显式例外，不能与普通映射混在一起。

工作台组/chip 的业务键改为 `(behavior_code, behavior_name)`；空编码特例用稳定 exception key。同一公开键只出现一个组，组内保留多条规则卡片。2C 单规则卡片仍逐条返回，不把业务不同的规则结果合并成一张卡。

### D9 — 原文按 tab 懒加载，兼容旧全量接口

保留 `GET /api/patient/{pid}/raw` 无参数时的全量响应。新增 `tab=notes|fees|labs`：只加载当前页签必需数据与最小元信息；前端按锚点先请求目标 tab，切换 tab 时再取其余数据。Hub 缓存从“首次访问串行抓齐所有表”细化为 per-patient/per-tab 成功缓存；失败不缓存，可重试。

每个阶段有受控 deadline，必须早于外层代理超时预算。tab 请求若已确认患者存在但该源暂不可用，返回受控、可重试的业务状态和中文提示，前端不得只显示 `HTTP 502`；真实双 miss 仍按既有 404 语义。旧全量接口保持既有字段和 SQL 异常降级兼容。

无 PHI 阶段日志记录 `source/tab/outcome/duration_bucket/cache_hit/error_code`，不记录 patient_id、SQL 参数、文书或费用内容。部署验收同时比较 62 回环直连、客户端 `--noproxy` 和浏览器路径：直连成功而浏览器 502 归代理路径，直连也失败才进入应用/SQL 分支。

### D10 — Harness 是提交门禁，真实 LLM 回归是补充

新增：

- `javert promise validate`：schema、引用、状态、版本链、active head、source case、正反例、类别源表与隐私静态检查。
- `javert promise run [--json]`：运行全部 active Promise 的纯函数案例和公开 presenter/API fixtures；任一历史案例降级、unexpected match、冲突或非确定输出即非零退出。

同一案例至少重复执行两次并比较去除时间/run id 后的规范化输出。报告只含 case/promise ID、pass/fail、reason code 和汇总。真 LLM `fn_regression.py` 继续作为较慢的召回/抖动补充，不纳入 Promise 的确定性定义。

## Risks / Trade-offs

- [Promise scope 过宽会制造确定性假阴性] → active scope 显式列规则/稳定 profile，强制 near-negative；模糊目标直接 NOT_APPLICABLE，禁止按行为大类推断。
- [Promise 数量增长后互相冲突] → typed kind、版本链与全案例冲突门禁；运行时冲突 fail-closed 为 INCONCLUSIVE，不按加载顺序裁决。
- [“不可变”文件仍可被 Git 修改] → 版本文件名 + supersedes 链 + 落库内容版本/摘要；代码评审和 harness 禁止 active 内容无版本升级变化，历史文件永不删除。
- [公开结构化解释缺字段] → 缺事实就留空并进入 `review_needs`，绝不让 LLM 散文伪造结构；legacy reasoning 兼容保留。
- [收紧 hit 使历史卡片看起来少了项目] → 这是消除假命中的预期变化；内部 evidence 仍可追溯，真实收费行命中不丢。
- [行为源工作簿未来更新] → 映射与工作簿变更必须同一 change 更新并过 harness；不在代码里手抄另一套库存。
- [tab 懒加载增加前端状态复杂度] → 复用同一 fetch/cache 模块，旧全量入口保留；错误状态显式建模并做模板/JS 测试。
- [502 来自客户端代理而非应用] → 直连/`--noproxy`/浏览器三段冒烟先定位；不以加大 SQL/代理 timeout 作为默认修复。
- [新增 SQL 字段影响双写] → 单一可空 JSON 字段、幂等迁移、旧行 null、SQLite/SQL Server 兼容测试；部署先 schema 后服务。

## Migration Plan

1. 落地 schemas、typed registry、去标识案例和离线 harness；先让现有已知问题成为失败基线。
2. 实现 `PR-D001` evaluator、Promise trace 与终局锁；接 runner、verdict gate、persist drift guard，验证零 LLM和历史 V 不改写锁定 CLEAN。
3. 幂等增加 SQLite/SQL Server `promise_trace_json`，跑旧行反序列化、双写和 sync 兼容测试。
4. 实现公开 presenter、严格 hit、类别源表校验与工作台/2C additive 接线；更新 API 契约。
5. 加 raw tab 懒加载和阶段诊断；本地用慢/断 hub 故障注入验证无无解释 5xx，再做 62 直连/代理/浏览器冒烟。
6. 部署顺序遵守 62 runbook：提交 HEAD artifact/install → schema → 重启 → systemd/登录页/环境实值/SQL-Hub/v3/原文验证；两端 HEAD 相等且工作树 clean 才完成。
7. 回滚应用代码和 Promise active head 到上一提交；新增可空字段保留不删。关闭新 Promise 只允许将其 active version 显式 supersede/停用并发布新版本，不删除历史 trace。

## Open Questions

- `PR-D001` 首批具体 rule scope 必须在实现阶段用当前 ready 规则逐条核对其机器可读目标与违规成立条件后确定；本设计不预填未经核实的 rule_id。
- 串换正式行为认定编码仍未提供；在获得 2C/业务方正式 H/I 口径前继续作为显式例外，harness 必须让该例外可见而非静默当正常映射。
- 原文 cold/warm SLO 数值需先在 62 采集无 PHI基线并确认外层代理超时预算；规范先要求受控 deadline 早于代理预算，不臆造固定秒数。
