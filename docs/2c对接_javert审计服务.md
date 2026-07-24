# 2C 平台 ↔ Javert 审计服务 对接文档 (契约 v0.3)

一句话: 你发「患者名单」, 我后台跑 LLM 审计, 你轮询拉每个患者的违规裁决清单。

> 本文是原 v1/v0.3 契约，继续保留。需要完整还原 Javert Web 卡片、返回 CLEAN
> 命中、项目发生时间及完整肿瘤限定条件的新接入，请使用
> [2C v2 对接文档](2c对接_javert审计服务_v2.md)。

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
| results[].behavior_name | **行为认定名称** (监管规则框架总表口径, 如"重复收费"/"超范围支付"), 前端展示用这个, 可不显示 rule_id |
| results[].confidence | 0~1 置信度 |
| results[].reasoning | 裁决理由, **全中文自然语言** (无工具名/规则代号/英文判定词, 可直接展示给审核员) |
| results[].diagnostic_code | 规则级诊断码；正常为空，格式异常为 `LLM_OUTPUT_MALFORMED`，长度截断为 `LLM_OUTPUT_TRUNCATED` |
| results[].retryable | 当前规则是否适合重试；模型格式/截断失败为 `true` |
| results[].eligibility_evaluation | 可空。RD04 肿瘤医保资格 v2 的双轴结果、条件证明和文书建议；其他规则及历史旧行是 `null` |
| results[].evidence | 证据数组: 来源工具 + 定位 + 原文摘录 (给人看的) |
| results[].hit_codes | 命中项目编码扁平数组 (国家医保码优先, 缺则院内码; 仅 V/I 非空), 直接挂明细用 |
| results[].hit_names | 命中项目名称扁平数组 (费用明细原始项目名; 与 hit_codes 同源去重) |
| results[].hits | 命中项目明细数组 (含编码/名称/限定/复核提示): 仅 V/I 有值, CLEAN 恒 `[]`。见下表 |
| results[].run_id | 审计运行 ID, 疑议追溯用 |

### eligibility_evaluation（RD04，可空）

RD04 在 62 的 `on` 模式会返回结构化资格结果；旧三态 `verdict` 继续保留，现有客户端可以
不解析本字段。最小示例：

```json
{
  "audit_disposition": "NO_VIOLATION_FOUND",
  "eligibility_status": "DOCUMENTATION_GAP",
  "legacy_verdict": "CLEAN",
  "rule_id": "RD04",
  "rule_version": "2026.1",
  "indication_branch_id": "example-branch",
  "source_versions": ["eligibility:2026.1", "regimen:2026.1"],
  "rule_effective_from": "2026-01-01",
  "rule_effective_to": "2027-12-31",
  "evaluated_service_date": "2025-08-22",
  "effective_date_enforced": false,
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

生效期字段（2026-07-18 新增，**只加不改名**，旧行/旧客户端不解析即可）：

| 字段 | 说明 |
|---|---|
| `rule_effective_from` / `rule_effective_to` | 该医保限定条件树声明的生效期（可空；`to` 空=长期） |
| `evaluated_service_date` | 本次求值采用的就诊/收费日期（可空） |
| `effective_date_enforced` | 是否按生效期过滤。`false` 且就诊日在声明窗口外时，`data_quality_flags` 含「未按生效期过滤·需核查就诊时该医保限定是否已生效」，该结果定性前须人工核查生效期 |

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
