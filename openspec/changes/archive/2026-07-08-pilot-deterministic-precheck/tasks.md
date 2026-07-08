# pilot-deterministic-precheck — tasks

## 1. Rule schema + config 开关

- [x] 1.1 `rule.py` 加 `PrecheckSpec(a_items, b_items)` + `Rule.precheck: PrecheckSpec | None = None` (可选, 向后兼容)
- [x] 1.2 `config.py` 加 `precheck: str = "on"` (env `JAVERT_PRECHECK`, 与 `verdict_gate` 同形态)
- [x] 1.3 `rule_writer._FIELD_ORDER` 纳入 `precheck` (写回不丢字段); 单测: 含/不含 precheck 的 yaml round-trip

## 2. precheck 引擎

- [x] 2.1 新 `src/javert/audit/precheck.py`: `run_precheck(spec, fee_df) → PrecheckResult(outcome ∈ {clean,facts,skip}, precheck_tag, a_hits, b_hits, fact_block, evidence)`; 复用 `fee_netting.fully_refunded_keys` 剔除完全充退项 + 项目名 (去空白) 子串匹配 A/B
- [x] 2.2 fact_block: 列 A/B 命中行 (名/额/日期) + 窄问题指令 (只核实反证、勿再搜费用); evidence: A/B 命中行 → `Evidence(source="search_fees", locator=项目名)`
- [x] 2.3 单测: A 缺失→clean / A∩B 并存→facts + fact_block 非空 / 完全充退 B→clean / 空 df→skip

## 3. runner 接线

- [x] 3.1 `AuditResult` 加 `precheck_tag: str = ""` (in-memory; reason 同时并入 `reasoning` 保证下游可见)
- [x] 3.2 `initial_user_message(rule, patient_id, precheck_facts=None)` 注入事实块
- [x] 3.3 `runner._audit_body`: 带 precheck 字段 + 开关 on 时先跑 precheck — clean→构造 CLEAN 结果 0 LLM 调用; facts→注入事实块进 loop, 判 V 时合并 precheck evidence (去重); skip/无字段/off→原路径
- [x] 3.4 单测 (mock provider): clean 路径断言 provider **未被调用** + verdict=CLEAN + precheck_tag; facts 路径断言事实块注入 + V 时 evidence 含费用行锚点; off 断言走原路径

## 4. M1 迁移

- [x] 4.1 `scripts/init_m1_precheck.py`: 解析 22 条 M1 ready 规则 `prompt_addon` 的 `A 类 (...)` / `B 类 (...)` → 写 `precheck` 块; A 或 B 抽取不全 (或含 STEP/触发器/search_examinations 等 bespoke 标记) 则跳过 + 打印「命中/跳过」清单
- [x] 4.2 单测: R191 式规整 prompt_addon 解析出非空 A/B; R112 式改写 prompt_addon 被跳过 (`tests/test_precheck.py`)
- [x] 4.3 跑迁移写盘: **21/22 获得 precheck, 仅 R112 跳过** (bespoke 跨日期比对). 匹配去空白抹平录入差异 (防假「无A项」漏检)
- [~] 4.4 ~~`M1.yaml` master_prompt 补窄问题行~~ **撤销**: 会破坏 `test_r191_round_trip_byte_equal` 字节等价护栏 (存量未重渲), 且窄问题已由运行时注入事实块驱动, 模板无需改 — 保持不变

## 5. 验证与对照

- [x] 5.1 `uv run pytest tests/ -v` 全绿 (590 passed + 1 skipped, 含 +12 本 change 新测试)
- [x] 5.2 端到端真连 (真 LLM @62 + 真数据): **短路** R191/J66252 = 0 LLM 1.7s CLEAN (对照 off 同判但 5 轮/11 工具/9.1s); **facts** R069/J24278 = LLM 只搜 search_notes 判 V, evidence 含合并 `search_fees` 确定性锚点
- [x] 5.3 **100 患者 (router_test_50 ∪ batch_50_new) ON vs OFF 全跑**: LLM 调用 4 vs 6993 = **降 99.9%** · 短路 2099/2100 · **0 真漏检** (唯一告警 J90508/R228 = OFF LLM 噪声, off 重跑翻回 CLEAN) · facts V 机器锚点 **100%** → `docs/precheck_compare_实测.md` + `output/precheck_cmp/`
- [x] 5.4 **facts 路径全集扫描 (3309 患者)**: A∩B 并存仅 4 例全在 R069; ③conf 闸本轮无压制 (facts V 均 conf0.90). **关键发现**: R069 项目集过粗 (a_items 子串命中耗材名 / b_items「滤器管路」命中呼吸耗材) → K01731 假阳性; 已修 `_build_fact_block` 加语义护栏 (两步: 先确认 B 确为 A 附属再核反证), K01731 V→CLEAN 真并存仍 V. R069 项目集精化交 `make-rules-code-portable`
