# 2C 平台 ↔ Javert 审计服务 v2 对接文档

> v2 继续兼容。新接入需要收费明细数量、单价、开单科室编码/名称和开单医生工号/名称时，
> 请使用 [2C v3 对接文档](2c对接_javert审计服务_v3.md)。

v2 用于尽可能完整还原 Javert Web 的审计卡片。它保留 v1，不要求旧客户端迁移：

- v1：`POST /api/audit/submit`、`GET /api/audit/results/{SYXH}`
- v2：`POST /api/audit/v2/submit`、`GET /api/audit/v2/results/{SYXH}`

v1 和 v2 共用审计任务、队列、结果库及 `attempt_id`。同一患者正在运行时，从任一版本重复
提交都不会重复跑。

## 1. 接入约定

- 服务地址：`http://192.168.31.62:8090`
- Content-Type：`application/json`
- HTTP：固定使用 HTTP/1.1
- 鉴权：两个 v2 接口为内网系统间免登录路径；其他审计接口仍需登录
- 模式：异步提交、轮询取结果
- 建议轮询间隔：30 秒
- 字符编码：UTF-8

## 2. 提交审计

### 2.1 请求

`POST /api/audit/v2/submit`

请求体必须是 JSON 数组，即使只有一个患者也不能直接发送裸对象：

```json
[
  {
    "SYXH": "CASE-EXAMPLE-001",
    "YLZZJGDM": "H31010600042"
  }
]
```

字段标准：

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `SYXH` | string | 是 | 患者住院流水号；去除首尾空格后不得为空 |
| `YLZZJGDM` | string | 否 | 医疗机构代码；当前透传回显，不参与规则路由；缺省为 `""` |

### 2.2 响应

HTTP 202：

```json
{
  "accepted": [
    {
      "SYXH": "CASE-EXAMPLE-001",
      "source": "local",
      "attempt_id": "att_a1b2c3d4e5f6"
    }
  ],
  "rejected": []
}
```

| 字段 | 说明 |
|---|---|
| `accepted[].source` | `local`：本地接入数据；`hub`：从 142 数据中台实时取数 |
| `accepted[].attempt_id` | 本次实际执行标识；running 状态重复提交复用原值 |
| `rejected[]` | 未受理患者及原因；即使部分驳回，HTTP 仍可能是 202 |

`done` 后再次 submit 会生成新 attempt 并重跑；running 时重复 submit 不重跑。

## 3. 查询结果

`GET /api/audit/v2/results/{SYXH}`

### 3.1 顶层结构

```json
{
  "api_version": "2.0",
  "SYXH": "CASE-EXAMPLE-001",
  "YLZZJGDM": "H31010600042",
  "status": "done",
  "outcome": "succeeded",
  "attempt_id": "att_a1b2c3d4e5f6",
  "error_code": "",
  "retryable": false,
  "progress": {
    "total": 14,
    "completed": 14,
    "failed": 0
  },
  "summary": {
    "total": 14,
    "violation": 2,
    "inconclusive": 1,
    "clean": 11,
    "not_applicable": 4
  },
  "cards": []
}
```

状态与终止判定：

| status | outcome | 调用方动作 |
|---|---|---|
| `unknown` | `unknown` | 未提交或未受理，停止轮询 |
| `running` | `running` | 继续轮询；`cards` 是已完成部分 |
| `done` | `succeeded` | 成功结束 |
| `done` | `partial` | 展示已有 cards，并根据 `retryable` 决定是否重提 |
| `done` | `failed` | 展示错误并停止轮询；不得解释为“患者合规” |

v2 结果按当前 `attempt_id + run_id` 增量复用已生成 cards。HTTP 请求自身超时时，调用方应
重试同一个 GET；只有成功收到终态 JSON 后才能依据其中的 `retryable` 决定是否重新 POST。
网络超时本身不等于 `retryable=true`。

`summary.total` 是本轮规则总数；Router 无候选时可以是 0。v2 会返回三种 verdict 的卡片，
包括 `CLEAN`。`summary.not_applicable` 是 `CLEAN` 中由确定性初步核查确认规则不适用的
卡片数，不从 `summary.clean` 中扣除。

## 4. cards[] 卡片契约

示例：

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
    "headline": "肿瘤医保限定用药核对未发现违规",
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
    "priority": "P0",
    "handling_level": "可疑（警告）",
    "template": "M8",
    "drug_rule_type": "限适应症"
  },
  "verdict": "CLEAN",
  "verdict_label": "合规",
  "applicability": "APPLICABLE",
  "applicability_label": "适用",
  "confidence": 1.0,
  "headline": "肿瘤医保限定用药核对未发现违规",
  "reasoning": "患者用药及现有证据符合当前医保限定条件。",
  "diagnostic_code": "",
  "retryable": false,
  "matched_items": [
    {
      "code": "XL01EXAMPLE",
      "name": "注射用维泊妥珠单抗",
      "occurrence_time": "2026-07-04 11:15:00",
      "source": "drug",
      "code_nat": "XL01EXAMPLE",
      "code_local": "LOCAL001",
      "matched_fee_name": "注射用维泊妥珠单抗",
      "restriction": "限既往未经治疗的特定成人患者，或不适合造血干细胞移植的复发/难治患者。",
      "review_note": ""
    }
  ],
  "hit_codes": ["XL01EXAMPLE"],
  "hit_names": ["注射用维泊妥珠单抗"],
  "hit_times": ["2026-07-04 11:15:00"],
  "hits": [
    {
      "source": "drug",
      "name": "注射用维泊妥珠单抗",
      "code_nat": "XL01EXAMPLE",
      "code_local": "LOCAL001",
      "matched_fee_name": "注射用维泊妥珠单抗",
      "restriction": "限既往未经治疗的特定成人患者，或不适合造血干细胞移植的复发/难治患者。",
      "review_note": "",
      "occurrence_times": ["2026-07-04 11:15:00"],
      "anchor": {
        "tab": "fees",
        "subsection": "",
        "query": "注射用维泊妥珠单抗",
        "char_start": null,
        "char_end": null,
        "unresolved": false,
        "match_level": "keyword"
      }
    }
  ],
  "evidence": [
    {
      "source": "drug_indication",
      "locator": "注射用维泊妥珠单抗",
      "text": "患者存在该药净正收费记录。"
    }
  ],
  "eligibility_evaluation": {},
  "finished_at": "2026-07-24T08:12:30+08:00"
}
```

`handling_level` 是规则静态处理等级，只允许 `违规（阻断） / 可疑（警告） / 提醒（引导）`；
它不随患者运行时 `verdict` 改变。顶层字段与 `rule.handling_level` 值相同，便于严格 DTO 和规则元数据视图分别消费。

字段说明：

| 字段 | 说明 |
|---|---|
| `title` | 卡片标题，完整行为认定名称，不截断 |
| `category.code/title` | 大类编码和名称；名称以“两库汇总”H/I 口径为主 |
| `description` | Web 卡片蓝色说明区对应的规则问题 |
| `rule` | 规则元数据；2C 展示通常只需 `question`，其余用于追溯 |
| `verdict` | `VIOLATION / INCONCLUSIVE / CLEAN` |
| `verdict_label` | 普通 CLEAN 为“合规”；规则不适用的 CLEAN 为“不适用” |
| `applicability` | `APPLICABLE / NOT_APPLICABLE`；不改变既有三态 verdict |
| `applicability_label` | `适用 / 不适用` |
| `headline` | 15–60 字单行公开短标题，与最终 verdict 一致；首屏优先读取本字段 |
| `reasoning` | 兼容中文摘要；v2 清除“暂未描述”占位短语，新医生界面优先展示 `public_explanation` |
| `matched_items` | 费用/药品命中关联的唯一真相源 |
| `hits` | Web 命中块所需的完整解析结果，含 fee/drug/note/lab/exam 及定位锚点 |
| `evidence` | 模型裁决引用的证据摘要 |
| `eligibility_evaluation` | RD04 完整肿瘤资格结构；非 RD04 或历史空行返回 `null` |
| `public_explanation` | 固定含 8 个字段：`headline/conclusion/narrative/audit_items/charge_facts/basis/clinical_evidence/review_needs`；headline 与顶层一致，`narrative` 保留持久化 reasoning 的完整中文化摘要，其余字段只投影有确定来源的事实 |
| `promise` | 可空公开摘要；仅含 `locked/historical_conflict`，不暴露内部 Promise 标识、原因码或 facts |

`headline`、`public_explanation.headline` 与 `promise` 是 additive 字段。v2 既有字段名、每条规则
一张 card、三态结果和 `matched_items` 对齐语义不变；下游 BFF 应优先展示 headline 并保留完整
reasoning/evidence 展开，但不得删除或改写旧字段，严格 DTO 应把新增字段建模为可选
并允许未知字段。公开 fee/drug hit 必须关联患者实际净正收费行，无实际收费的搜索词不再生成
假命中。

部署必须先由 Javert 增加 nullable headline 列并上线 additive API，再由 2C 消费；回滚 2C 时忽略
headline 即可，服务端保留可空列，不执行破坏性降级。

## 5. code/name/time 一一对应标准

2C 应优先读取 `matched_items[]`，不要自行把来自不同来源的数组重新去重。

兼容数组满足以下强约束：

```text
len(hit_codes) == len(hit_names) == len(hit_times) == len(matched_items)

hit_codes[i] == matched_items[i].code
hit_names[i] == matched_items[i].name
hit_times[i] == matched_items[i].occurrence_time
```

同一项目在两个日期发生，会返回两个对象，code/name 可以重复，time 不同：

```json
{
  "matched_items": [
    {
      "code": "331501001",
      "name": "心脏彩色多普勒超声",
      "occurrence_time": "2026-07-01 08:30:00"
    },
    {
      "code": "331501001",
      "name": "心脏彩色多普勒超声",
      "occurrence_time": "2026-07-02 09:45:00"
    },
    {
      "code": "S22060001000010",
      "name": "左心功能测定",
      "occurrence_time": "2026-07-02 10:00:00"
    }
  ],
  "hit_codes": [
    "331501001",
    "331501001",
    "S22060001000010"
  ],
  "hit_names": [
    "心脏彩色多普勒超声",
    "心脏彩色多普勒超声",
    "左心功能测定"
  ],
  "hit_times": [
    "2026-07-01 08:30:00",
    "2026-07-02 09:45:00",
    "2026-07-02 10:00:00"
  ]
}
```

只有能关联患者实际费用行的费用/药品项目才进入 `matched_items` 与三个兼容数组。
仅记录“检索了 PTCA 但未命中”之类结论的证据仍保留在 `evidence`/`hits` 中，但不会再
生成 code/time 均为空的 name-only 假命中。

`occurrence_time` 和 `hit_times[]` 固定为 `yyyy-MM-dd HH:mm:ss`。上游 datetime、
ISO 字符串及斜杠日期字符串会在 Javert 侧统一格式；无法解析的非空值不会原样透传。

## 6. CLEAN 与 J70782 类场景

v1 为保持历史契约，仅给 VIOLATION/INCONCLUSIVE 解析命中项目。v2 对三种 verdict 都解析：

- CLEAN RD04 仍返回被审核药品，例如“注射用维泊妥珠单抗”；
- CLEAN 卡片仍返回 reasoning、evidence 和可用的 `eligibility_evaluation`；
- CLEAN 不等于“没有命中项目”，而是“命中候选经过规则核对后未发现违规”。

确定性初步核查确认“A 类主项/术式费用未命中，规则不适用”时，底层仍返回
`verdict=CLEAN` 以兼容既有三态，但 v2 同时返回：

```json
{
  "verdict": "CLEAN",
  "verdict_label": "不适用",
  "applicability": "NOT_APPLICABLE",
  "applicability_label": "不适用"
}
```

C 端应优先用 `verdict_label` 展示，不要把这类卡片标成普通“合规”。

## 7. eligibility_evaluation 肿瘤限定条件

本字段直接返回 Javert 持久化的完整结构化结果，主要包括：

- `audit_disposition`、`eligibility_status`、`legacy_verdict`
- `criterion_assessments[]`：诊断、分期、既往治疗、复发/难治、移植适合性等逐项状态
- `proof_tree`：与条件树同构的结构化证明
- `scope_evaluations[]`：医保支付限定与指南适应证的独立评价
- `documentation_suggestions[]`：缺文书时的前瞻性完善建议
- `release_id`、`rule_revision_id`、`drug_concept_id`
- `source_document_ids`、`source_fragment_ids`、`source_versions`
- `rule_effective_from/to`、`evaluated_service_date`
- `temporal_applicability`、`temporal_warning`

固定状态：

| 字段 | 枚举 |
|---|---|
| `audit_disposition` | `NO_VIOLATION_FOUND / VIOLATION_FOUND / REVIEW_REQUIRED` |
| `eligibility_status` | `SATISFIED / NOT_SATISFIED / DOCUMENTATION_GAP / CONFLICT` |
| `criterion_assessments[].state` | `SATISFIED / NOT_SATISFIED / UNKNOWN / CONFLICT` |

历史 RD04 如果当时没有持久化 `eligibility_json`，本字段返回 `null`。服务不会用当前知识库
重算并覆盖历史结论。

## 8. 行为大类口径

当前主要标准映射：

| code | title |
|---|---|
| T380601 | 超范围支付 |
| T380302 | 超标准收费 |
| T380301 | 重复收费 |
| T380303 | 分解项目收费 |
| T380206 | 提供不必要的医药服务 |
| T380201 | 过度诊疗 |
| T380204 | 超量开药 |
| T380205 | 重复开药 |
| T380202 | 过度检查 |

补充约定：

- 纯虚构医药服务及“虚构医药服务项目或以骗保为目的串换项目”，统一映射为
  `T380206 / 提供不必要的医药服务`。
- 明确的串换规则保持独立 title：
  `串换药品、医用耗材、诊疗项目和服务设施`；正式行为认定编码尚未提供，code 为 `""`。
- title 返回完整名称，不使用 Web 导航 chip 的省略显示。

## 9. 最小联调命令

```bash
curl --http1.1 \
  -H 'Content-Type: application/json' \
  -d '[{"SYXH":"CASE-EXAMPLE-001","YLZZJGDM":"H31010600042"}]' \
  http://192.168.31.62:8090/api/audit/v2/submit
```

```bash
curl --http1.1 \
  http://192.168.31.62:8090/api/audit/v2/results/CASE-EXAMPLE-001
```

## 10. 2C 端实现建议

1. submit 后同时检查 `accepted` 和 `rejected`。
2. 记录 `attempt_id`，按顶层 `status/outcome` 判断是否继续轮询。
3. 使用 `cards[]` 渲染卡片，不要只显示违规；CLEAN 默认可折叠。
4. 按 `category.code + category.title` 分组；串换 code 为空时以 title 为稳定键。
5. 命中项目以 `matched_items[]` 为准；三个兼容数组只用于现有模型快速接入。
6. RD04 展开区直接读取 `eligibility_evaluation`，不要从大篇幅 reasoning 反解析限定条件。
7. 忽略未知字段；若使用严格 DTO，将 `narrative` 建模为可选字段，以便 v2 后续继续只增不删。
