# 2C 平台 ↔ Javert 审计服务 对接文档 (契约 v0.1)

一句话: 你发「患者名单」, 我后台跑 LLM 审计, 你轮询拉每个患者的违规裁决清单。

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
  "accepted": [ { "SYXH": "J30860" }, { "SYXH": "J30933" } ],
  "rejected": [ { "SYXH": "J99999", "reason": "查无此患者数据" } ]
}
```

rejected 的不跑; accepted 的进后台队列, 去接口 2 轮询。

## 接口 2: 查结果

`GET /api/audit/results/{SYXH}`

### 出参

```json
{
  "SYXH": "J30860",
  "YLZZJGDM": "H31010600042",
  "status": "done",
  "summary": { "total": 14, "violation": 2, "inconclusive": 1, "clean": 11 },
  "results": [
    {
      "run_id": "aud_Ab3xY9kQw2Lm",
      "rule_id": "R191",
      "rule_name": "重复收费-静脉输液",
      "verdict": "VIOLATION",
      "verdict_label": "违规",
      "confidence": 0.85,
      "reasoning": "中文裁决理由…",
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
| status | `unknown`(没提交过) / `running`(审计中, results 为已完成部分) / `done`(全部完成) |
| summary | 三档裁决计数 |
| results[].verdict | **`VIOLATION`(违规) / `INCONCLUSIVE`(待人工复核) / `CLEAN`(合规)** |
| results[].confidence | 0~1 置信度 |
| results[].reasoning | 中文裁决理由 (可直接展示给审核员) |
| results[].evidence | 证据数组: 来源工具 + 定位 + 原文摘录 (给人看的) |
| results[].hits | **命中项目数组 (机器可读, 用来挂你方费用明细)**: 仅 V/I 有值, CLEAN 恒 `[]`。见下表 |
| results[].run_id | 审计运行 ID, 疑议追溯用 |

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

## 连通性验证 (2 条命令)

```bash
# 1. 提交
curl -H 'Content-Type: application/json' \
  -d '[{"SYXH":"J66252","YLZZJGDM":"H31010600042"}]' \
  http://192.168.31.62:8090/api/audit/submit

# 2. 轮询 (每 30s 一次, status=done 即完成)
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
5. **幂等**: 同一 SYXH 在跑中 (`running`) 重复提交不重跑, 照常回 accepted; `done` 后重复提交会重新跑一遍 (结果覆盖为最新一轮)。
6. **本文档状态**: 接口已实现并端到端验证 (J66252 真跑 33 条规则)。任务状态存进程内: Javert 服务重启后历史提交查询回 `unknown` (裁决本体仍在库/工作台可查), 重提交即可。
