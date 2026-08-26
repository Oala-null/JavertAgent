# 2C 平台 ↔ Javert 审计服务 v3 对接文档

v3 在 v2 完整规则卡片基础上，将命中项目细化到实际收费明细行，并增加数量、单价、开单科室和开单医生信息。v1、v2 继续保留：

- v1：`POST /api/audit/submit`、`GET /api/audit/results/{SYXH}`
- v2：`POST /api/audit/v2/submit`、`GET /api/audit/v2/results/{SYXH}`
- v3：`POST /api/audit/v3/submit`、`GET /api/audit/v3/results/{SYXH}`

三个版本共享同一审计任务、队列、结果库和 `attempt_id`。同一患者处于 `running` 时，从任一版本重复提交都不会重复执行。

## 1. 接入约定

- 服务地址：`http://192.168.31.62:8090`
- Content-Type：`application/json`
- HTTP：HTTP/1.1
- 字符编码：UTF-8
- 鉴权：v3 submit/results 是内网系统间免登录路径；其他 Javert 接口仍需登录
- 调用模式：异步提交、轮询结果
- 建议轮询间隔：30秒

> 查询路径中的 `/` 不能省略。正确形式是
> `/api/audit/v3/results/{SYXH}`；若误拼成 `/api/audit/v3/results{SYXH}`，请求不会命中
> v3 免登录路由，而会返回 `401 {"error":"未登录"}`。HTTP 202 只证明 submit 成功，调用方
> 还必须记录并核对实际轮询 URL。

历史结果需要按当前规则静态等级补齐时，可读取 `GET /api/audit/v3/rules`。响应仅包含
`{"api_version":"3.0","rules":{"R191":"违规（阻断）"}}` 形式的
`rule_id → handling_level` 目录，不触发审核，也不改写历史 verdict。

## 2. 提交审计

### 2.1 请求

`POST /api/audit/v3/submit`

请求体必须是 JSON 数组，即使只有一个患者也不能发送裸对象：

```json
[
  {
    "SYXH": "CASE-EXAMPLE-001",
    "YLZZJGDM": "H31010600042"
  }
]
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `SYXH` | string | 是 | 患者住院流水号；去除首尾空格后不得为空 |
| `YLZZJGDM` | string | 否 | 医疗机构代码；当前透传回显，缺省为 `""` |

### 2.2 响应

HTTP 202：

```json
{
  "accepted": [
    {
      "SYXH": "CASE-EXAMPLE-001",
      "source": "hub",
      "attempt_id": "att_a1b2c3d4e5f6"
    }
  ],
  "rejected": []
}
```

| 字段 | 说明 |
|---|---|
| `accepted[].source` | `local` 表示本地接入数据；`hub` 表示从142数据中台取数 |
| `accepted[].attempt_id` | 本轮实际执行标识；running 状态重复提交复用原值 |
| `rejected[]` | 未受理患者及原因；HTTP 202 不代表全部患者都已受理 |

## 3. 查询结果与轮询终止条件

`GET /api/audit/v3/results/{SYXH}`

### 3.1 running 响应示例

```json
{
  "api_version": "3.0",
  "SYXH": "CASE-EXAMPLE-001",
  "YLZZJGDM": "H31010600042",
  "status": "running",
  "outcome": "running",
  "attempt_id": "att_a1b2c3d4e5f6",
  "error_code": "",
  "retryable": false,
  "progress": {
    "total": 39,
    "completed": 14,
    "failed": 0
  },
  "summary": {
    "total": 39,
    "violation": 0,
    "inconclusive": 0,
    "clean": 14,
    "not_applicable": 3
  },
  "cards": [
    "此时仅包含已经完成的14张卡片"
  ]
}
```

`status=running` 时 `cards` 只是中间快照，不能作为本轮完整结果入库，必须继续轮询。

### 3.2 progress 子字段

| 读取路径 | 类型 | 说明 |
|---|---|---|
| `status` | string | `unknown / running / done` |
| `outcome` | string | `unknown / running / succeeded / partial / failed` |
| `progress.total` | integer | 本轮 Router 选中的规则总数 |
| `progress.completed` | integer | 已成功完成并产生卡片的规则数 |
| `progress.failed` | integer | 执行失败的规则数 |
| `cards.length` | integer | 当前响应实际携带的卡片数；running 时可能小于 total |

注意：不是读取顶层 `total/completed`，而是读取 `progress.total` 和 `progress.completed`。

### 3.3 状态处理

| status | outcome | 调用方动作 |
|---|---|---|
| `unknown` | `unknown` | 未提交或未受理，停止轮询 |
| `running` | `running` | 等待约30秒后继续轮询；不得把当前 cards 当全量 |
| `done` | `succeeded` | 成功结束，可以保存本轮完整 cards |
| `done` | `partial` | 保存已有 cards，同时按 `retryable` 决定是否重提 |
| `done` | `failed` | 展示错误并停止轮询；不得解释为患者合规 |

推荐伪代码：

```javascript
while (true) {
  const response = await getV3Results(patientId);

  if (response.status === "running") {
    await wait(30000);
    continue;
  }

  if (response.status === "done" && response.outcome === "succeeded") {
    if (response.cards.length !== response.progress.completed) {
      throw new Error("Javert卡片数量与完成数不一致");
    }
    await saveCards(response.attempt_id, response.cards);
    break;
  }

  handleTerminalError(response);
  break;
}
```

### 3.4 查询性能、超时与重试

v2/v3 在同一 `attempt_id` 内按 `run_id` 增量缓存 cards：running 进度增加时只构建新增
卡片，已经生成的卡片不会在每次 GET 时重新解析；终态重复查询直接复用当前进程缓存。
缓存只改变响应耗时，不改变 JSON 字段、排序或结果内容，服务重启后可从持久化 run 重新构建。

调用方应区分两类重试：

- 已收到 JSON 且 `status=running`：等待约30秒后继续 GET。
- HTTP `request timed out`：本次没有拿到业务响应，应重试同一个 GET；此时不能推断
  `retryable=true`，也不要据此重新 POST。

只有成功收到终态 JSON 后，才根据顶层 `status/outcome/retryable` 判断是否重新提交患者。
建议保留至少30秒的读取超时；若链路还经过网关，可适当增加网关超时作为网络余量，但不应
通过持续重提患者来规避查询超时。

## 4. cards[] 卡片结构

v3 card 保留 v2 的大类、规则、裁决、推理、证据、适用性和肿瘤资格字段：

```json
{
  "card_id": "aud_Ab3xY9kQw2Lm",
  "run_id": "aud_Ab3xY9kQw2Lm",
  "rule_id": "RD04",
  "handling_level": "可疑（警告）",
  "title": "超范围支付",
  "description": "申请医保支付的肿瘤药，超出医保药品目录限定支付范围。",
  "category": {
    "code": "T380601",
    "title": "超范围支付"
  },
  "public_explanation": {
    "conclusion": {"label": "未发现违规", "summary": "现有结构化事实未支持违规结论。"},
    "narrative": "经核对收费事实、诊断与现有文书，保留完整的中文审核说明。",
    "audit_items": ["申请医保支付的肿瘤药，超出医保药品目录限定支付范围。"],
    "charge_facts": [],
    "basis": [],
    "clinical_evidence": [],
    "review_needs": []
  },
  "promise": null,
  "rule": {
    "id": "RD04",
    "name": "超医保限定支付适应症用药",
    "question": "申请医保支付的肿瘤药，超出医保药品目录限定支付范围。",
    "domain": "药品",
    "priority": "P1",
    "handling_level": "可疑（警告）",
    "template": "M8",
    "drug_rule_type": "限适应症"
  },
  "verdict": "CLEAN",
  "verdict_label": "合规",
  "applicability": "APPLICABLE",
  "applicability_label": "适用",
  "confidence": 1.0,
  "reasoning": "患者用药及现有证据符合当前医保限定条件。",
  "diagnostic_code": "",
  "retryable": false,
  "matched_items": [],
  "hit_codes": [],
  "hit_names": [],
  "hit_times": [],
  "hits": [],
  "evidence": [],
  "eligibility_evaluation": null,
  "finished_at": "2026-07-27T08:12:30+08:00"
}
```

`handling_level` 是规则静态处理等级，只允许 `违规（阻断） / 可疑（警告） / 提醒（引导）`；
它不随患者运行时 `verdict` 改变。顶层字段与 `rule.handling_level` 值相同。

规则没有实际费用命中时，card 仍然返回，`matched_items` 和三个 `hit_*` 数组为空。不得因为 `matched_items=[]` 丢弃 CLEAN、不适用或其他卡片。

`public_explanation` 和 `promise` 为 v1/v2/v3 共用的 additive 字段：前者固定包含
`conclusion/narrative/audit_items/charge_facts/basis/clinical_evidence/review_needs`，后者为空或
仅含 `locked/historical_conflict`。`narrative` 保留持久化 reasoning 的完整中文化摘要，其余
结构化字段不从散文反解析。既有 `rule_id/reasoning/evidence` 等兼容字段继续保留；医生默认
界面应展示公开解释。`narrative` 相对最初六字段公开解释也属于只加字段，严格 DTO 应将其建模
为可选并允许未知字段。该增量不改变 v3 按实际收费源行展开 `matched_items[]`、兼容数组等长
对齐或每条规则一张 card 的语义。

## 5. matched_items[] 收费明细行

### 5.1 完整示例

```json
{
  "code": "XL01EXAMPLE",
  "name": "注射用示例药品",
  "occurrence_time": "2026-07-04 11:15:00",
  "source": "drug",
  "code_nat": "XL01EXAMPLE",
  "code_local": "LOCAL001",
  "matched_fee_name": "（国谈）注射用示例药品",
  "restriction": "医保限定条件摘要。",
  "review_note": "",
  "quantity": 2,
  "unit_price": 18800.5,
  "ordering_department_code": "D003",
  "ordering_department_name": "肿瘤科",
  "ordering_doctor_id": "DR003",
  "ordering_doctor_name": "示例医生"
}
```

### 5.2 字段定义

| 字段 | 类型 | 来源/说明 |
|---|---|---|
| `code` | string | 国家医保码优先，缺失时使用院内码 |
| `name` | string | 规则识别的规范命中名称 |
| `occurrence_time` | string | 收费发生时间，固定 `yyyy-MM-dd HH:mm:ss` |
| `source` | string | `fee` 或 `drug` |
| `code_nat` | string | 国家医保码，可为空字符串 |
| `code_local` | string | 院内项目编码，可为空字符串 |
| `matched_fee_name` | string | 患者费用明细原始名称 |
| `restriction` | string | 药品医保限定依据；非药品通常为空 |
| `review_note` | string | 需要复核的匹配提示 |
| `quantity` | number/null | 当前收费明细行数量；无法解析时为 `null` |
| `unit_price` | number/null | 当前收费明细行单价；无法解析时为 `null` |
| `ordering_department_code` | string | 开单科室编码；源数据缺失时为 `""` |
| `ordering_department_name` | string | 开单科室名称；源数据缺失时为 `""` |
| `ordering_doctor_id` | string | 开单医生工号；源数据缺失时为 `""` |
| `ordering_doctor_name` | string | 开单医生名称；源数据缺失时为 `""` |

六个新增字段全部来自同一条费用明细，服务不会跨日期、跨编码或跨项目拼接。

### 5.3 同项目同时间多收费行

如果同一项目在相同时间存在两条费用行，例如开单医生不同，v3 返回两个对象：

```json
{
  "matched_items": [
    {
      "code": "331501001",
      "name": "示例诊疗项目",
      "occurrence_time": "2026-07-27 08:00:00",
      "quantity": 1,
      "unit_price": 300,
      "ordering_department_code": "D001",
      "ordering_department_name": "科室甲",
      "ordering_doctor_id": "DR001",
      "ordering_doctor_name": "医生甲"
    },
    {
      "code": "331501001",
      "name": "示例诊疗项目",
      "occurrence_time": "2026-07-27 08:00:00",
      "quantity": 2,
      "unit_price": 300,
      "ordering_department_code": "D002",
      "ordering_department_name": "科室乙",
      "ordering_doctor_id": "DR002",
      "ordering_doctor_name": "医生乙"
    }
  ]
}
```

2C 必须按数组逐条保存，不得用 `(code,name,time)` 作为唯一键覆盖前一条。

### 5.4 兼容数组

以下关系始终成立：

```text
len(hit_codes) == len(hit_names) == len(hit_times) == len(matched_items)

hit_codes[i] == matched_items[i].code
hit_names[i] == matched_items[i].name
hit_times[i] == matched_items[i].occurrence_time
```

新增业务应优先读取 `matched_items[]`，不要自行拼接三个扁平数组。

## 6. 裁决和适用性

| 字段 | 值 | 说明 |
|---|---|---|
| `verdict` | `VIOLATION` | 违规 |
| `verdict` | `INCONCLUSIVE` | 待人工复核 |
| `verdict` | `CLEAN` | 未发现违规 |
| `applicability` | `APPLICABLE` | 规则适用并已完成核查 |
| `applicability` | `NOT_APPLICABLE` | 确定性初步核查确认规则不适用；底层 verdict 仍为 CLEAN |

`summary.not_applicable` 是 CLEAN 中的规则不适用数量，不从 `summary.clean` 扣除。

## 7. 版本选择

- 新接入统一使用 v3。
- 已使用 v2 且不需要数量、单价、开单科室/医生时可以继续使用 v2。
- v1 仅用于历史兼容，不建议新系统接入。

v3 只增加独立端点和字段，不要求数据库迁移，也不改变 v1/v2 的响应。
