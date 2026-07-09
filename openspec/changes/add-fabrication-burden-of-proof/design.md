# Design: add-fabrication-burden-of-proof

## Context

M5 模板 (虚构医药服务) 现有 8 条 H 类规则, master prompt 未显式规定"收费在、执行证据不在"时的默认裁决 — LLM 各凭语感, 实测倾向 CLEAN (211427558 实证)。precheck 引擎 (pilot-deterministic-precheck) 只有 coexist 语义 (M1 A∩B 并存), spec 明文"只认 A∩B 并存缺失这一条", companion 需要 spec 级修订而非偷偷扩展。

约束: 举证倒置只许动 M5 家族, 全局动会稀释 I 队列; 不动 runner 引擎; 既有 coexist 规则零行为变化 (100 患者对照已证 99.9% 短路收益, 不能回归)。

## Goals / Non-Goals

**Goals:**
- "收费在、证据不在 → 默认 I" 成为 M5 家族的显式裁决语义
- 溶栓/内镜治疗两个覆盖空白有规则接住 (FN-001/002 至少 partial)
- companion 预检把"术式↔配套缺失"确定性信号前移, 少烧 LLM 且给机器锚点

**Non-Goals:**
- 不做全目录内涵判定 (造影重复/球囊含取栓 → `add-catalog-loader`)
- 不做文书↔收费名称映射表 (只 prompt 缓解)
- 不把举证倒置推广到 M5 之外的模板

## Decisions

### D1. 举证倒置落在 M5 模板层, 不动引擎

`configs/templates/M5.yaml` master prompt 加裁决语义段: "治疗/手术类收费存在, 且文书找不到该操作的执行证据 → INCONCLUSIVE (证据缺失待人工); 仅当文书**正面反证**操作确实执行 (操作记录/治疗单/执行记载) → CLEAN"。重渲染派生规则。
替代方案: runner 层全局默认 verdict 改 — 否, 影响面不可控且违背"规则即文件"。

### D2. companion = precheck mode 字段, 配套表内嵌规则

`Rule.precheck` 加可选 `mode` (缺省 `coexist` 零行为变化):

| companion 三态 | 语义 |
|----------------|------|
| A (术式) 无净正命中 | `clean` 短路, 零 LLM |
| A 命中 + B (配套) 无 | `facts` — "收了术式 X 但全费用单无任何配套 Y" 事实块, LLM 只核操作文书反证 |
| A、B 均命中 | `skip` 走原路径, **不注偏置** (配套在场只是虚构信号消失, 不构成 CLEAN 证明) |

facts→evidence 机器锚点复用既有机制 (hit_resolver 可 join 编码+锚点)。配套清单直接写进规则的 `precheck.b_items`, 不建 configs 共享文件 — 单一消费方时内嵌最简; ~20 术式小表进 `docs/` 作后续规则素材库。

### D3. 规则归属: 先查清单, 细化优先于新建

R225 模式已证: 细化既有条目一条规则一张卡片, 专家聚焦。实施第一步查 0325 H 类 8 条归属; 溶栓虚构大概率可挂"未开展相关诊疗项目但收取对应诊疗费用"族 (R080 同 question), 内镜治疗同理。都无归属才新建 R3xx。

### D4. 溶栓规则的证据三角

companion facts (收溶栓术+零溶栓药) + 手术记录反证 (取栓非溶栓) + M5 默认 I 兜底: 三层递进 — facts 成立且文书正面显示未溶栓 → V; facts 成立文书含糊 → I (举证倒置兜住); 配套在场 → skip 原路径。名称警示行 (D5) 防"术式名不同"误判。

### D5. 名称不匹配 prompt 缓解

M5 (及顺带 M4) 模板加一行: "收费项目名与文书术式名常不一致 (如肘关节截骨术↔尺骨截骨术), MUST NOT 仅因名称不同断言未收费/未执行; 用解剖部位与操作类别交叉核对"。一行成本, 映射表 (shi_ss 医保双码) 明确留中期。

## Risks / Trade-offs

- [默认 I 推高 I 量] → 仅 M5 家族 + 仅"零执行证据"分支; FN 回归同时盯 I 率与 5 案例档位; 超预期则把语义收窄到"高价治疗/手术费"
- [companion facts 引导 LLM 偏 V] → 事实块只陈述费用事实 (与 M1 precheck 同风格), 判定语义留给规则 prompt; 双有 skip 不注偏置
- [prompt 迭代周期不可预期] → 与确定性 change 解耦 (本 change 独立交付); 每条新规则 5 患者 dry-run 看 trace 的既有纪律
- [M5 重渲染波及 8 条既有规则] → 抽 2 条 dry-run 对照 + FN-005 anchor 防串味

## Migration Plan

1. Mac: precheck mode 实现 + 单测 (三态 + 缺省逐字不变) → M5 模板改 + 重渲染 → 2 条规则落 + router index 重建
2. dry-run: 211351896 / 211427558 各 5 轮看 trace 调 prompt; FN 回归 FN-001/002 升档、FN-005 不退化、M5 抽查无回归
3. 62 部署 (tar src+configs), 正式重跑两患者上工作台
4. 回滚: 规则 status 改 draft / M5 模板 git revert + 重渲染 / mode 字段缺省即旧行为, 无 env 开关必要

## Open Questions

- 溶栓/内镜治疗在 0325 清单 H 类的确切条目归属 — 实施第一步查
- 默认 I 是否需要金额下限 (小额治疗费不进队列) — 先无下限, 视专家队列压力
