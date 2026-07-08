# pilot-deterministic-precheck — design

## Context

扫描已核验的事实:

1. `verdict_gate.py` 已长出 8 道闸 + 若干 rule_id allowlist, 其中 ②单次闸只作用 `derived_from_template == "M2"` (第 298-300 行), **从不触及 M1**; ③conf 底线闸对任意 V 按 `conf < ceiling(0.85)` 降级 (第 315-328 行), 依赖 LLM 自报置信度。
2. M1「重复收费」判定 = 「主项 A ∩ 附属 B 并存 + 文书无反证」。前半 (A∩B 是否并存、净次数、净额) 是一条查询能确定的事实; 只有「文书无反证」真正需要 NLU。当前把整件事交给 LLM 自由探索 (自己调 `search_fees` + `note_diagnosis` + `search_notes`)。
3. `fee_netting.net_fee_items` / `fully_refunded_keys` 已提供退费净额聚合 (完全充退项 `is_full_refund`); `hit_resolver._match_fee_rows` 已提供 fee 名→国家码/院内码 join + 锚点。这两块确定性代码可直接复用。
4. M1 有 22 条 `status: ready`。其中多数 (R191/R045/R018/R300…) 的 `prompt_addon` 是规整的 `A 类 (label): "x" / "y"` 渲染形态; 但 **R112 已被人工改写成带 v1.5/1.6/1.7 跨日期比对 + `search_examinations` 的 bespoke prompt**, 不再是纯 M1 形态。
5. 工作台命中项目块 (`hit_resolver`) 只要 evidence 带 `source` 含 "fee" + `locator=费用项目名`, 就能确定性 join 出码 + 锚点 — 无需 LLM 自由文本引用准确。

约束: 项目协议要求改 prompt 后 5 患者 dry-run 对照; 「历史确认 V 0 漏检」是硬门槛。

## Goals / Non-Goals

**Goals:**
- M1 可确定性判定的事实 (A∩B 并存) 从 LLM 自由探索移到确定性 precheck; 事实不成立 → 短路 CLEAN 不进 LLM (砍 ≥50% M1 LLM 调用)。
- 事实成立 → LLM 只答「文书有无反证」一个窄问题, 不再自己搜费用。
- 新增 V 的 evidence 100% 携带 precheck 给定的费用行锚点 (机器可复核), 不依赖 LLM 引用准确。

**Non-Goals:**
- M2/M4 推广 (同形态, 本 change 达标后另立)。
- 换院字面名鲁棒性 (由 `make-rules-code-portable` 解决; 两 change 共享「结构化字段」地基, 本 change 先行)。
- 改 `verdict_gate.py` 代码 (见 D5)。
- 重渲 22 条 `prompt_addon` / 改 `prompt-fit` 渲染链 (见 D4)。
- 诊断指征 / fee 类别作为**硬短路**判据 (见 D2, 只留 A∩B 费用并存这一条最安全的短路)。

## Decisions

### D1. precheck spec 只声明 A/B 项目集

新字段 `Rule.precheck: PrecheckSpec | None`, `PrecheckSpec = {a_items: list[str], b_items: list[str]}`。匹配纯按项目名子串 (与 LLM 原步骤 1-4 同语义), 不需要 `fee_category` (跨全量 fee 子串匹配即可) 也不需要诊断词 (留在背景 `prompt_addon`)。最小字段面 = 最小换院维护面。

### D2. 短路只认「A∩B 费用并存缺失」(0 漏检安全)

precheck 三种 outcome:
- **clean** — A 命中为空 **或** B 命中为空 → 短路 CLEAN, `precheck_tag ∈ {无A项, 无B项}`, **不进 LLM**。这是最安全的确定性事实 (原规则步骤 2/3), 也是绝大多数患者的分支 (体量来源)。
- **facts** — A 与 B 都有命中 → 事实成立, 产出费用行锚点 + 注入窄问题, 进 LLM。
- **skip** — fee 数据不可用 (loader 取数失败 / 缺列) → 不短路、不注入, 走原 LLM 路径 (fail-open, 绝不因数据缺失误 CLEAN)。

诊断指征不做硬短路: 诊断数据可能不全, 「有 A∩B 费用但诊断没抓到」恰是最该让人看的情形, 不能静默 CLEAN。诊断/类别仍在 `prompt_addon` 里由 LLM 兜底。

匹配前先剔除完全充退项 (`fully_refunded_keys`), 否则退费抵消的行会造成假并存。

### D3. facts 成立 → 窄问题 + 确定性锚点回填

- `initial_user_message` 注入事实块: 列出 A 命中行 (名/额/日期) 与 B 命中行, 明示「费用事实已定, 只用 `search_notes` 核实反证 (分次手术/两次独立医嘱/第三方报告/不同时相或部位), 不要再调 `search_fees`」。runner 的「裁决前 ≥1 工具调用」硬门槛天然逼 LLM 至少调一次 `search_notes` = 正是我们要的核实动作。
- 判 V 时, runner 把 precheck 的 A/B 命中行确定性地并进 `result.evidence` (`source="search_fees"`, `locator=费用项目名`), `hit_resolver` 据此 join 出码 + 锚点 → **新 V 100% 带机器锚点**, 与 LLM 是否引用准确解耦。

### D4. 迁移靠解析既有 prompt_addon, 不重渲

一次性脚本 `scripts/init_m1_precheck.py`: 逐条 M1 ready 规则解析 `prompt_addon` 里规整的 `A 类 (...): "…"` / `B 类 (...): "…"` → 写 `precheck` 块。解析不到 (A 或 B 少于 1 项, 如 R112 这种已改写的) → **跳过 + 记日志**, 该规则无 precheck 块 → runner 自动走原 LLM 路径, 零行为变化。自选安全子集, 天然排除 landmine。

不动 `prompt-fit` 渲染链 / 不重构 22 条 `prompt_addon`: 窄问题由 D3 的注入事实块统一驱动 (对所有带 precheck 的 M1 一致), 无需逐条重渲 (且 20 条 vars json 已不可得)。`M1.yaml` master_prompt **保持不变** — 曾试补一行「以事实块为准」, 但会破坏 `test_r191_round_trip_byte_equal` 字节等价护栏 (存量 prompt_addon 未随之重渲), 且窄问题已由运行时注入完全驱动, 模板文本无需改动。

### D5. 不改 verdict_gate 代码

- ②单次闸本就是 M2-scoped, 从不作用 M1 → 无「被 precheck 取代」的重叠, 无需动。
- ③conf 闸对 baseline 与 precheck 新路径**同等作用** (两条路径都过 ③) → precheck 不引入相对 baseline 的**新**漏检, 「0 漏检」硬门槛不受 ③ 影响。是否为 precheck-backed V 放宽 ③ (「事实确定性越高越不该依赖自报 conf」) = 评估项, 用对照数据决定, 不在本 pilot 代码范围内 (避免投机改闸)。

### D6. 开关回滚

config 加 `precheck: str = "on"` (env `JAVERT_PRECHECK=off` 直通)。off → 所有带 precheck 的 M1 也走原 LLM 路径。与 `verdict_gate` 开关同形态。

## Risks / Trade-offs

- [A/B 子串匹配假并存] → 假并存只让规则进 **LLM** (facts 不是裁决), LLM 仍核实反证, 不会误 V; 且事实块把命中行摊给 LLM 复核。安全侧偏 facts。
- [已改写规则被误解析] → 解析器保守 (A、B 各须 ≥1 项才写 precheck), R112 类跳过走原路。迁移脚本输出「命中/跳过」清单供人核。
- [LLM 少搜费用漏掉某反证角度] → 事实块给全 A/B 行 + 日期, 且不禁止 LLM 调其它 note 工具; 只是不再要它重复搜费用。dry-run 抽查 trace 质量。
- [退费未净 → 假并存] → 复用 `fully_refunded_keys` 先剔除, 单测覆盖。

## Migration Plan

1. Mac 改码 (precheck.py + Rule 字段 + runner 接线 + config 开关) → `uv run pytest tests/ -v` 全绿 (含 precheck 单测 + runner 集成 mock-provider 断言短路 0 LLM 调用)。
2. 跑 `scripts/init_m1_precheck.py` → 记录 22 条里 N 条获得 precheck / 跳过清单 (R112 预期在跳过列)。
3. 5 患者 dry-run 对照 (J66252/J18906/J90508 + 2 szx): 同代码基线 verdict 分布无 V 级意外差异 + facts-hold 路径 trace 质量抽查。
4. 106 患者历史基线重跑 M1 22 条: V/I/C 分布对照 (历史确认 V 0 漏检 = 硬门槛) + M1 LLM 调用数降幅 (目标 ≥50%) + 新 V 证据机器锚点覆盖率 (目标 100%) → 写 `docs/`。
5. 回滚: `JAVERT_PRECHECK=off` 一键回原路径; precheck 块留 yaml 无害。

## Open Questions

- ③conf 闸是否最终对 precheck-backed V 放宽 — 待第 4 步对照数据 (若 ③ 明显压制事实成立的真 V 才做, 另 change)。
- 5 患者对照集是否固定 (建议沿用 boost-llm-efficiency 同一集, 便于跨 change 复用协议)。
