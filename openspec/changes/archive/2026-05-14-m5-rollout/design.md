## Context

m1-m3 rollout 完成后, 50 条规则 ready (覆盖重复收费/过度检查/口腔串换). 三大模板 (M1/M2/M3) 形成稳定 60-70s avg/rule, V/C/I 形态正常. M5 是从 "fee 命中 + 文书无执行证据" 角度补充, 与 m1-m3 的 fee-fee / fee-dx / fee-诊断对比模式不同 — **M5 是 fee-notes 模式**.

设计 doc `docs/templates/模板5_虚构医药服务.md` 在本期 phase 0 写就 (140 行精简版, 包含 master prompt + 8 条 personalization 速查表 + R003/R004 特殊处理).

10 条 H 类规则 violation_type 都是"虚构医药服务项目" (0325 表原文), 分两组:
- **主审 8 条** (R015 R037 R080 R103 R105 R134 R203 R224): 当前 4 工具可审, 都是"未开展但收费"模式
- **特殊 2 条** (R003 R004): R003 需跨患者文书相似度比对, R004 需采购侧数据, **超出当前工具能力**

操作者: 同 m1-m3-rollout. 8 条 vars json 预估 6-10 min/条 (字段比 M3 少 2-3 个).

## Goals / Non-Goals

**Goals:**
- M5.yaml 模板从空骨架装满: master_prompt + 12 fields + jinja2 dept_check / supporting_dx / special_notes / pilot_caveat 条件块
- 10 条 H 类骨架 init (包括特殊 2 条 R003/R004)
- 8 条主审 prompt-fit + ready (priority P0, derived_from_template=M5)
- 2 条特殊 (R003/R004) 保 drafting + priority=P2 + notes 标明原因
- 每条主审 prompt_addon ≥ 600 chars, trigger_keywords ≥ 5 项
- 验收: 跑 P0 子集 (15 M1 + 15 M2 P0 + 8 M5 P0 = 38 条) 看 verdict 分布
- **期望 M5 子集出现 V 触发** (与 M1/M2/M3 全 CLEAN 形成对比) — 因为 J66252 fee 项与文书不完全匹配, M5 模板设计上"fee 有 + notes 空"应该触发 V

**Non-Goals:**
- ❌ 不动 M4/M6
- ❌ 不实装 R003/R004
- ❌ 不强 byte-equal round-trip (R015-R224 都是新 init 的)
- ❌ 不写新代码 / 不加测试 / 不改 base.txt

## Decisions

### D1. M5 模板字段集 (12 个)

| 字段 | 类型 | required | 说明 |
|------|------|---------|------|
| service_desc | str | true | 被虚构的服务描述 (例: 全身麻醉) |
| service_kw_list | list[str] | true | search_fees keyword (3-5 个) |
| catalog_basis | str | true | 适应症 + 文书要求基础 (1-3 句) |
| execution_evidence_kw_list | list[str] | true | 文书中应有的执行证据关键词 (4-5 个) |
| notes_section_hints | list[str] | true | 重点检查的文书 section |
| supporting_dx_kw_list | list[str] | false (default []) | 支持开展的诊断 (可选; 若有则 + 文书无 = VIOLATION) |
| dept_check | str | false (default "") | 要求科室 (麻醉/重症/影像; 空表不限) |
| inconclusive_addendum | str | false (default "") | 额外 INCONCLUSIVE 条款 |
| special_notes | list[str] | false (default []) | 特殊注意 |
| pilot_caveat | str | false (default "") | pilot 适用性说明 |
| aux_keywords | list[str] | true | trigger_keywords |
| aux_tools | list[str] | true | suggested_tools (推荐 [search_fees, search_notes, note_diagnosis]) |
| aux_signal | str | true | expected_signal |

### D2. R003/R004 处理策略

**选 (b) 保 drafting + P2 + notes 说明**, 不强行装 prompt:
- R003 需跨患者文书相似度比对, 工具不支持
- R004 需采购侧数据, 工具不可见
- notes 标 "abandoned 候选, 留待 add-cross-patient-stats / add-procurement-data"

未来 add-cross-patient-stats 解锁后, R003/R004 可以走另一个模板 (M5-cross) 或独立 prompt.

### D3. priority 设置

- 8 条主审: P0 (高命中候选, 当前可审, pilot 立即可跑)
- R003/R004: P2 (备选, 需新工具)

### D4. 验收门: P0 子集组 I

跑 `javert audit-patient J66252 --priority P0 --share-tool-cache --concurrency 5`. P0 子集 = 30 + 8 = 38 条 (15 M1 + 15 M2 + 8 M5).

**预期 A**: 38 条总耗时 8-12 min concurrency 5
**预期 B**: **M5 子集可能出现 V** — 因为 J66252 fee 列有 117 项, 文书 152 notes 但可能不完全对应; 部分 fee (R103 影像 / R134 检验 / R203 麻醉) 文书可能没"执行证据"细节
**回退**: 若 M5 全 CLEAN, 怀疑 prompt 设计太宽容 (LLM 把 fee 与诊断混了); 若 M5 大量 V, 怀疑设计 doc execution_evidence_kw 太严格 (任意搜索词漏命中)

### D5. spec delta

`rule-templating` ADDED: M5 template 装填 + 12 fields
`rule-registry` ADDED: 8 条 M5-class ready + 2 条 H 类 drafting (R003 R004)

## Risks / Trade-offs

**[R1] M5 prompt 设计偏严, 大量假 V** → execution_evidence_kw 过于具体 (例: 要求"诱导药物"完全字面命中). Mitigation: D4 验收时看 V 数量, 若 ≥ 5 个就调 prompt 让 evidence_kw 更宽容

**[R2] notes_section_hints 名称与实际 search_notes section 不匹配** → 例: 设计写"重症监护记录", 实际表是 "ICU 监护记录". Mitigation: 抽 R015 / R134 dry-run, 看 search_notes 调用返回, 必要时改 vars

**[R3] J66252 是择期手术患者, 文书相对完整** → M5 触发能力可能仍 0 V 全 CLEAN, 不能验证设计. Mitigation: 接受, 后续跑 J18906 (非肿瘤) / J13365 (骨科 R037 强相关) 验证

**[R4] R203 麻醉特殊条件 ('用了药但记录不全 → INCONCLUSIVE')** → M5 模板可能装不下这种细节. Mitigation: 用 inconclusive_addendum 字段塞

## Migration Plan

1. **W1 (模板装填 + init)**: 装 M5.yaml + init 10 条骨架 + R003/R004 P2 标记
2. **W2 (8 条 prompt-fit)**: R015 R037 R080 R103 R105 R134 R203 R224 + dry-run R134
3. **W3 (验收)**: mark 8 条 ready; 跑 audit-patient --priority P0; 写组 I; 更新 CLAUDE.md
