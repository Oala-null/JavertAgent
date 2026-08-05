# 2C 平台 ↔ Javert 审计服务 对接文档 (契约 v0.3)

一句话: 你发「患者名单」, 我后台跑 LLM 审计, 你轮询拉每个患者的违规裁决清单。

> 本文是原 v1/v0.3 契约，继续保留。需要完整还原 Javert Web 卡片、返回 CLEAN
> 命中、项目发生时间及完整肿瘤限定条件的新接入，请使用
> [2C v2 对接文档](2c对接_javert审计服务_v2.md)。
>
> 新接入需要收费明细数量、单价、开单科室和开单医生时，请直接使用
> [2C v3 对接文档](2c对接_javert审计服务_v3.md)。

- **服务地址**: `http://192.168.31.62:8090`
- **鉴权**: 无。本文档两个接口免登录直调 (内部系统间对接, 仅限内网访问; Javert 工作台其余路径仍需登录, 互不影响)。
- **模式**: 异步。单患者审计约 **5~10 分钟** (十几条规则 × LLM 逐条裁决), 所以提交立即回执, 结果轮询拉取 (建议间隔 30s)。

---

## 接口 1: 提交审计

`POST /api/audit/submit` (Content-Type: application/json)

### 入参 (你方格式, 照收)

```json
[
  { "SYXH": "J30860", "YLZZJGDM": "H31010600042" },
  { "SYXH": "J30933", "YLZZJGDM": "H31010600042" }
]
```

| 字段 | 说明 |
|------|------|
| SYXH | 首页序号 (患者住院流水号) |
| YLZZJGDM | 医疗机构代码。当前单机构部署: 透传回显, 暂不参与路由 |

### 出参 (HTTP 202)

```json
{
  "accepted": [
    { "SYXH": "J30860", "source": "local", "attempt_id": "att_a1b2c3d4e5f6" },
    { "SYXH": "211449756", "source": "hub", "attempt_id": "att_g7h8i9j0k1l2" }
  ],
  "rejected": [ { "SYXH": "J99999", "reason": "本地与数据中台均查无此患者" } ]
}
```

`attempt_id` 标识本次实际入队的审计。相同 SYXH 在 `running` 时重复提交不会重跑，
会返回同一个 `attempt_id`；上一轮 `done` 后再次提交会生成新的 `attempt_id`。

**⚠ 202 ≠ 全部受理**: 必须检查 `rejected` 数组, 被驳回的患者不要轮询 (results 永远 unknown)。

**数据源自动兜底**: 本地无数据的患者会自动查 142 数据中台 (`source: "hub"`),
有则实时取数入审 — hub 患者首轮结果比本地患者**多等约 1~2 分钟** (取数耗时)。
两边都查无才 rejected。

## 接口 2: 查结果

`GET /api/audit/results/{SYXH}`

### 出参

```json
{
  "SYXH": "J30860",
  "YLZZJGDM": "H31010600042",
  "status": "done",
  "outcome": "succeeded",
  "attempt_id": "att_a1b2c3d4e5f6",
  "error_code": "",
  "retryable": false,
  "progress": { "total": 14, "completed": 14, "failed": 0 },
  "summary": { "total": 14, "violation": 2, "inconclusive": 1, "clean": 11 },
  "results": [
    {
      "run_id": "aud_Ab3xY9kQw2Lm",
      "rule_id": "R191",
      "rule_name": "重复收费-静脉输液",
      "behavior_code": "T380301",
      "behavior_name": "重复收费",
      "verdict": "VIOLATION",
      "verdict_label": "违规",
      "confidence": 0.85,
      "reasoning": "全中文自然语言裁决理由 (无内部术语)…",
      "diagnostic_code": "",
      "retryable": false,
      "eligibility_evaluation": null,
      "hit_codes": ["331501001"],
      "hit_names": ["麻醉后复苏监护(PACU)"],
      "evidence": [ { "source": "search_fees", "locator": "…", "text": "证据原文摘录" } ],
      "hits": [
        {
          "source": "fee",
          "name": "麻醉后复苏监护(PACU)",
          "matched_fee_name": "麻醉后复苏监护(PACU)",
          "code_nat": "331501001",
          "code_local": "F00123",
          "restriction": "",
          "review_note": ""
        }
      ],
      "public_explanation": {
        "conclusion": {"label": "发现需核查行为", "summary": "现有结构化事实支持该项进入医保合规复核。"},
        "audit_items": ["核查同一收费项目是否重复计费。"],
        "charge_facts": [],
        "basis": [],
        "clinical_evidence": [],
        "review_needs": ["当前缺少可公开投影的结构化事实，请查阅原始资料确认。"]
      },
      "promise": null,
      "finished_at": "2026-07-15T08:12:30+08:00"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| status | `unknown`(没提交过/被驳回) / `running`(审计中, results 为已完成部分) / `done`(本轮已终止；是否成功还要看 outcome) |
| outcome | `unknown / running / succeeded / partial / failed`。调用方判断本轮成败的主字段 |
| attempt_id | 本次实际入队标识。服务重启后的历史回放为 `null` |
| error_code | 稳定机器码；正常为空。现有值含 `HUB_FETCH_FAILED / ROUTING_FAILED / AUDIT_FAILED / RULE_FAILURES` |
| retryable | 本轮或任一规则是否适合修复上游问题后重提；规则级诊断为 true 时顶层也为 true |
| progress | 规则进度 `{total, completed, failed}`；患者级前置失败可能均为 0 |
| error | (可选, 仅异常时出现) 脱敏的审计中断简述；不得只看 HTTP 200 或 results 是否为空 |
| summary | 三档裁决计数 |
| results[].verdict | **`VIOLATION`(违规) / `INCONCLUSIVE`(待人工复核) / `CLEAN`(合规)** |
| results[].behavior_code | 行为认定编码；与 `behavior_name` 组成公开类别键。显式例外可为空，不得据内部类型臆造编码 |
| results[].behavior_name | **行为认定名称** (监管规则框架总表口径, 如"重复收费"/"超范围支付"), 前端展示用这个, 可不显示 rule_id |
| results[].confidence | 0~1 置信度 |
| results[].reasoning | 兼容裁决摘要；新建医生界面应优先展示 `public_explanation`，不要从本字段反解析结构化事实 |
| results[].diagnostic_code | 规则级诊断码；正常为空，格式异常为 `LLM_OUTPUT_MALFORMED`，长度截断为 `LLM_OUTPUT_TRUNCATED` |
| results[].retryable | 当前规则是否适合重试；模型格式/截断失败为 `true` |
| results[].eligibility_evaluation | 可空。RD04 肿瘤医保资格 v2 的双轴结果、条件证明和文书建议；其他规则及历史旧行是 `null` |
| results[].evidence | 证据数组: 来源工具 + 定位 + 原文摘录 (给人看的) |
| results[].hit_codes | 命中项目编码扁平数组 (国家医保码优先, 缺则院内码; 仅 V/I 非空), 直接挂明细用 |
| results[].hit_names | 命中项目名称扁平数组 (费用明细原始项目名; 与 hit_codes 同源去重) |
| results[].hits | 命中项目明细数组 (含编码/名称/限定/复核提示): 仅 V/I 有值, CLEAN 恒 `[]`。见下表 |
| results[].public_explanation | 医生可读结构化投影，固定含 `conclusion/audit_items/charge_facts/basis/clinical_evidence/review_needs`；只使用有确定来源的事实 |
| results[].promise | 可空公开摘要；有 trace 时仅含 `locked` 与 `historical_conflict`，不公开 Promise ID、kind、reason code 或 facts |
| results[].run_id | 审计运行 ID, 疑议追溯用 |

### 公开解释的兼容边界

`public_explanation`、`promise` 和 `behavior_code` 均为 additive 字段；既有 `rule_id`、
`reasoning`、`evidence`、`hits` 等字段不删、不改名。新建医生界面应优先展示
`public_explanation`，不要把旧 `reasoning/evidence` 反解析为结构化事实，也不要默认展示
内部规则号、工具名、gate/run 术语、英文 verdict 或原始 evidence JSON。

公开 fee/drug 命中只表示已关联到患者实际净正收费行；检索词、未命中 locator 和患者无对应
收费行的名称不能作为“命中项目”。这会让部分旧卡片的公开 hits 变少，但不改变单规则结果数、
三态 verdict 或内部追溯字段。

### eligibility_evaluation（RD04，可空）

RD04 在 62 的 `on` 模式会返回结构化资格结果；旧三态 `verdict` 继续保留，现有客户端可以
不解析本字段。下例展示将来经授权的 published release 返回形状；当前 legacy 资产下
`release_id/rule_revision_id/policy_scope/source_*` 等追加字段为 `null` 或空数组。最小示例：

```json
{
  "audit_disposition": "NO_VIOLATION_FOUND",
  "eligibility_status": "DOCUMENTATION_GAP",
  "legacy_verdict": "CLEAN",
  "rule_id": "RD04",
  "rule_version": "2026.1",
  "release_id": "release_example",
  "rule_revision_id": "revision_example",
  "drug_concept_id": "drug_example",
  "policy_scope": "INSURANCE_PAYMENT",
  "source_type": "INSURANCE_PAYMENT",
  "policy_scope_display_label": "医保支付限定",
  "indication_branch_id": "example-branch",
  "source_versions": ["eligibility:2026.1", "regimen:2026.1"],
  "source_document_ids": ["source-document-example"],
  "source_fragment_ids": ["source-fragment-example"],
  "rule_effective_from": "2026-01-01",
  "rule_effective_to": "2027-12-31",
  "evaluated_service_date": "2025-08-22",
  "effective_date_enforced": false,
  "temporal_applicability": "BEFORE_EFFECTIVE_WINDOW",
  "temporal_warning": "核查当期指南/医保限定是否适用",
  "criterion_assessments": [
    {
      "criterion_id": "example-criterion",
      "criterion_type": "treatment_status",
      "state": "UNKNOWN",
      "reason": "缺少可核验记录",
      "missing_items": ["移植适合性评估"]
    }
  ],
  "proof_tree": {
    "node_id": "root",
    "operator": "leaf",
    "state": "UNKNOWN",
    "criterion_id": "example-criterion",
    "assessment": {
      "criterion_id": "example-criterion",
      "criterion_type": "treatment_status",
      "state": "UNKNOWN",
      "reason": "缺少可核验记录",
      "missing_items": ["移植适合性评估"]
    }
  },
  "data_quality_flags": [],
  "documentation_suggestions": [
    {
      "criterion_id": "example-criterion",
      "title": "补充移植适合性评估",
      "rationale": "当前记录不足以核验该条件。",
      "suggested_content": "如拟使用该方案，建议记录评估结论及依据。",
      "priority": "high",
      "safety_note": "本建议仅用于完善病历记录，不代表缺失条件已被证实。"
    }
  ]
}
```

固定枚举：

| 轴 | 枚举 | 含义 |
|---|---|---|
| `audit_disposition` | `NO_VIOLATION_FOUND / VIOLATION_FOUND / REVIEW_REQUIRED` | 审核处置 |
| `eligibility_status` | `SATISFIED / NOT_SATISFIED / DOCUMENTATION_GAP / CONFLICT` | 医保资格状态 |
| `criterion_assessments[].state` | `SATISFIED / NOT_SATISFIED / UNKNOWN / CONFLICT` | 单条件四态 |

旧三态投影固定为
`NO_VIOLATION_FOUND→CLEAN`、`VIOLATION_FOUND→VIOLATION`、
`REVIEW_REQUIRED→INCONCLUSIVE`。文书建议不能充当证据，也不会改变条件状态。

生效期字段（2026-07-18 起追加，**只加不改名**，旧行/旧客户端不解析即可）：

| 字段 | 说明 |
|---|---|
| `rule_effective_from` / `rule_effective_to` | 该医保限定条件树声明的生效期（可空；`to` 空=长期） |
| `evaluated_service_date` | 本次求值采用的就诊/收费日期（可空） |
| `effective_date_enforced` | 是否按生效期过滤。`false` 且就诊日在声明窗口外时，`data_quality_flags` 含「未按生效期过滤·需核查就诊时该医保限定是否已生效」，该结果定性前须人工核查生效期 |

published release provenance 与时间字段（2026-07-21 追加，全部按可空/可忽略解析）：

| 字段 | 说明 |
|---|---|
| `release_id` / `rule_revision_id` | 本次裁决使用的 published release 和不可变规则 revision；legacy 资产/旧行为 `null` |
| `drug_concept_id` | 跨产品/别名归一后的药品概念 ID（可空） |
| `policy_scope` / `source_type` | `INSURANCE_PAYMENT` 或 `GUIDELINE_INDICATION`；两字段同时存在时必须一致，两个 scope 的资格状态不合并 |
| `policy_scope_display_label` | 受控展示名「医保支付限定」或「指南适应证」；指南不得显示成法定说明书 |
| `scope_evaluations[]` | published 双 scope 的完整独立快照；每项包含各自处置、资格、proof tree、来源和时间字段。legacy/单 scope 结果为空数组 |
| `source_document_ids` / `source_fragment_ids` / `source_versions` | 可追溯来源 ID 和版本数组；可为空数组 |
| `temporal_applicability` | `BEFORE_EFFECTIVE_WINDOW / IN_WINDOW / AFTER_EFFECTIVE_WINDOW`（可空） |
| `temporal_warning` | 日期适用性人工提示；无提示时为空字符串或旧行 `null` |

published release 的日期策略是：服务日早于声明窗口（例如 2025）时使用当前
release 自动裁决并返回告警；窗口内（例如 2026–2027）正常裁决；超出窗口且
无适用新版（例如 2028）时 fail-closed 为 `REVIEW_REQUIRED/INCONCLUSIVE`。
**该非对称策略只属于校验通过的 `PUBLISHED` release**；现行 legacy `configs/` 资产仍
按既有 `JAVERT_ONCOLOGY_ENFORCE_EFFECTIVE_DATE` 开关执行，不因本次契约追加而改变。
存在 `scope_evaluations` 时，顶层 `audit_disposition/eligibility_status` 只是两个 scope
中最严重结果的确定性兼容投影；消费方展示或复核医保与指南差异时必须读取数组，不能把
顶层单值反推成两个 scope 都得出同一结论。

### SSE `new_audit_run` 兼容性

工作台 SSE 仍保留完整嵌套 `eligibility_evaluation`，同时在事件顶层**只追加**
以下可空摘要，便于不展开 proof tree 的消费方：

```text
audit_disposition, eligibility_status,
release_id, rule_revision_id, drug_concept_id,
policy_scope, source_type, policy_scope_display_label,
source_versions, source_document_ids, source_fragment_ids,
rule_effective_from, rule_effective_to, evaluated_service_date,
effective_date_enforced, temporal_applicability, temporal_warning
```

非 RD04 或空 `eligibility_json` 的上述顶层摘要均为 `null`。已有结构化的 legacy RD04 旧行
仍保留它原有的处置/资格/日期摘要；新增 release/revision/scope/来源 provenance 为
`null` 或空数组。旧的 `run_id/patient_id/rule_id/verdict/confidence/is_new_patient` 及
嵌套字段不删除、不改名；
客户端仍应忽略未知字段。

### hits[] 字段 (违规项 ↔ 费用明细关联键)

| 字段 | 说明 |
|------|------|
| source | `fee`(费用项) / `drug`(药品) / `note`(文书证据) / `lab`(检验) / `exam`(检查) |
| name | 命中项目名 (通用名/项目名) |
| matched_fee_name | **患者费用明细里的原始项目名 (逐字)** — 对明细的名称键 |
| code_nat | **国家医保编码** (对应中台 `MXXMBMYB`) — 对明细的编码键, 优先用这个 join |
| code_local | 院内码 (对应中台 `MXXMBM`) |
| restriction | 药品限定内容 (仅药品规则非空, 如"限二线用药") |
| review_note | 匹配复核提示 (如"按通用名匹配, 剂型/复方需复核"), 非空时名称匹配是兜底、编码可能为空 |

> 同一命中项目在明细里有多规格/多行时会出多条 hits (按编码去重)。`source=note` 的 hit 无编码
> (证据在文书不在费用), 挂明细时只处理 `fee`/`drug` 即可。

`running` 状态下 results 增量可见 (跑完一条规则就多一条), 可用于进度展示。

### 轮询终止判定（必须按此处理）

```text
status=unknown                         → 未受理/被驳回，停止轮询
status=running                         → 继续轮询；results=[] 在刚提交时正常
status=done + outcome=succeeded        → 成功结束；total=0 是 Router 无候选规则
status=done + outcome=partial          → 至少一条规则成功且至少一条失败，展示已有 results 并提示可重试
status=done + outcome=failed           → 患者级失败或所有规则失败，展示 error/error_code 并停止轮询
```

HTTP 200 只表示“成功读取任务状态”，不代表审计成功。特别是
`status=done + results=[] + outcome=failed` 不能解释为患者合规或无疑点。

## 连通性验证 (2 条命令)

```bash
# 1. 提交
curl -H 'Content-Type: application/json' \
  -d '[{"SYXH":"J66252","YLZZJGDM":"H31010600042"}]' \
  http://192.168.31.62:8090/api/audit/submit

# 2. 轮询 (每 30s 一次；status=done 后继续检查 outcome)
curl http://192.168.31.62:8090/api/audit/results/J66252
```

## HTTP 状态码字典 (本服务实际会出现的)

| 码 | 含义 | 什么情况 / 怎么办 |
|----|------|------------------|
| 200 | 成功 | `results` 正常返回 (含 unknown/running/done 都是 200, 看 body 里 `status`) |
| 202 | 已受理 | `submit` 成功收单, 看 body 里 accepted/rejected; **驳回的患者也是 202**, 不是错误码 |
| 400 | HTTP 协议层错误 | 请求没进应用就被拒: 客户端走了 HTTP/2 (h2c) 或发了非法报文。**客户端固定 HTTP/1.1** (联调实测踩过) |
| 401 | 未登录 | 打到了免鉴权白名单之外的路径 (如 `/api/audit/run`)。检查 URL 是否为 `/api/audit/submit` 或 `/api/audit/results/{SYXH}` |
| 404 | 路径不存在 | URL 拼错 (如 `/api/audit/result` 少了 s) |
| 405 | 方法不对 | submit 必须 POST, results 必须 GET |
| 422 | 请求体校验失败 | body 不是 JSON 数组 / 字段名不对 / 非法 JSON。**响应体 `detail[].input` 会回显服务端实际收到的内容**, 排障先看这个 (联调实测: 裸对象忘包 `[]` 就是它) |
| 500 | 服务端异常 | Javert 侧 bug, 带 traceId 找 Javert 方查日志 |
| 连接拒绝/超时 | 服务不在线 | 服务重启窗口 (~10s) 或宕机, 先打 `GET /healthz` 探活再重试 |

## 注意事项

1. **测试患者**: J66252 (数据最全)。真实对接前患者数据须已接入 Javert (数据中台取数或 CSV 导入)。
2. **规则集**: Javert 侧按患者自动路由选规则 (Router 预筛, 约 7~15 条/患者), 你方不用传规则。
3. **契约只加不改**: 字段只增不删不改名, 解析请忽略未知字段。
4. **访问边界**: 两接口仅限内网 (192.168.31.x) 调用, 出参含患者诊疗数据, 不得暴露公网。日后如需最低门槛, 加固定 token 请求头 (双方各一行改动), 联调时再定。
5. **幂等**: 同一 SYXH 在跑中 (`running`) 重复提交不重跑并复用 `attempt_id`; `done` 后重复提交会生成新 attempt 并重新跑一遍，查询默认指向最新一轮。
6. **重启不丢结果**: Javert 服务重启后, 已完成患者的查询自动回放库内历史 (每规则最新一条, 按 `done` 返回); 只有重启瞬间**正在跑**的患者需要重新 submit。
