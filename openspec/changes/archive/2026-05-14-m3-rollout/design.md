## Context

m1+m2-rollout 完成后, 41 条 yaml 全部装好 (33 ready + 8 drafting P2). M3 是绿区 73 条目标的第 3 大模板, 覆盖 17 条 G 类口腔串换. 设计 doc `docs/templates/模板3_口腔串换.md` (339 行) 完整, 包含 §2 master prompt + §3 R245 reference yaml + §4 17 条 personalization + §5-§8 速查表/挑战/复用建议.

M3 与 M1/M2 的核心差异:

| 维度 | M1 (重复收费) | M2 (过度检查) | M3 (口腔串换) |
|------|--------------|--------------|--------------|
| 核心判别 | 两笔费用并存 + 无反证 | fee 命中 + dx 无指征 | dx 全 trivial + fee 命中大手术 |
| 主工具 | search_fees + search_notes | note_diagnosis + search_fees | note_diagnosis + search_fees + search_notes |
| 子类区分 | 无 (单一模式) | drug_check 联查 (R219/R313) | **G-a (12 条) vs G-b (5 条)** 共享 trivial_dx 不同 |
| 触发触发器 | 同次费用对偶 | 检查项命中 + 全诊断查 | 大手术命中 + **全部**诊断 trivial |
| 字段数 | 18 | 14 | ~15 |
| 信号强度 | 中-强 | 强 | 强 (但 pilot 命中率近 0) |
| 17 条骨架状态 | M1: 15 条已存在 | M2: 18 条已存在 | **M3: 17 条 MISSING** |

设计 doc §7 明确: G 类对甲状腺 pilot 50 患者命中率近 0, 留 v0.3 切换口腔/综合医院数据集后批跑. 本期目标 = **装备完毕等数据集切换**, 不期望甲状腺 pilot 上 V 触发.

操作者: 同 m1/m2-rollout. 17 条 G-a + G-b 子类高度同构, 每条 vars json 预估 5-8 min (大部分字段共享, 只换 hijacked_kw + supporting_extra).

约束:
- 模板装填 + init 17 条骨架 + 数据填充, 无代码改动
- priority 设 P1 (口腔强信号待数据切换), 不是 P0
- 不改 base.txt / audit_runs schema / 代码

## Goals / Non-Goals

**Goals:**
- M3.yaml 模板从空骨架装满: master_prompt + 14-16 fields + keywords/tools/signal_template
- M3 模板支持 m1+m2 之外的两个新维度: **dental_dept_check** + **全诊断 trivial 判断**
- 17 条 G 类 yaml 从 MISSING → ready, 全部带 `derived_from_template: M3` + priority=P1
- 每条 prompt_addon 量级 700-1100 chars, trigger_keywords 6-12 项, expected_signal 2 行
- R245 作为 reference: 生成的 prompt_addon 语义覆盖设计 doc §3 (类似 R151 之于 M2)
- 全装完后跑 `audit-patient J66252 --priority P1 --share-tool-cache --concurrency 5`, 记录组 H. 预期 20 条 (3 M2 P1 + 17 M3 P1) 全 CLEAN

**Non-Goals:**
- ❌ 不在甲状腺 pilot 期望 V 触发 — 设计 doc §7 已声明
- ❌ 不动 M4-M6 (各自 rollout, 设计 doc 待写)
- ❌ 不强 byte-equal round-trip (R245 yaml 是新 init 的, 没源 prompt)
- ❌ 不写新代码 / 不加测试 / 不改 base.txt
- ❌ 不强制每条规则 dry-run (抽 1-2 条代表即可)
- ❌ 不为 R260 改归 M3 — 已正确归 M1 (设计 doc §7 强调)

## Decisions

### D1. M3 模板字段集设计

**关键问题**: M3 比 M1/M2 多两个新维度 — dental_dept_check 与 trivial_dx 全集判定 — 怎么用 jinja2 表达?

**选项:**
- (a) 全字段 required: true
- (b) 用 `{% if %}` 条件块, 可选字段 required: false + default

**选 b**, 与 M2 一致. 字段清单 (15 个):

| 字段 | 类型 | required | 说明 |
|------|------|---------|------|
| hijacked_surgery_desc | str | true | 被冒充的"医保大手术/治疗项目"描述 (例: 口腔颌面软组织清创术) |
| hijacked_surgery_kw_list | list[str] | true | search_fees 关键词 (主词 + 变体, 2-5 个) |
| catalog_basis | str | true | 适应症与冒充背景 (1-3 句) |
| trivial_dx_kw_list | list[str] | true | 实际诊断列表 (G-a 9 项 / G-b 7 项), 用于判定"全 trivial" |
| supporting_dx_kw_list | list[str] | true | 支持大手术的诊断 (任一命中即 CLEAN, 5-10 项, 含子类通用 + 该规则专属) |
| dental_dept_check | bool | false (default true) | 是否要求口腔科 (默认 true) |
| self_pay_bonus_list | list[str] | false (default []) | 自费项目并存加分 (种植牙/烤瓷冠/正畸), 命中加分判 V |
| total_amount_threshold | int | false (default 0) | 同次诊治 fee 总额过高阈值 (>800 加分判 V; 0 表示不启用) |
| inconclusive_addendum | str | false (default "") | 额外 INCONCLUSIVE 条款 |
| special_notes | list[str] | false (default []) | 特殊注意 |
| pilot_caveat | str | false (default "") | pilot 适用性说明 |
| aux_keywords | list[str] | true | trigger_keywords |
| aux_tools | list[str] | true | suggested_tools (推荐 [note_diagnosis, search_fees, search_notes]) |
| aux_signal | str | true | expected_signal 整段 |
| subclass | enum | false (default "G-a") | "G-a" / "G-b" 子类标识 (用于文档/标注, 不参与 prompt 渲染) |

实际 prompt 渲染中, `subclass` 不出现在 master_prompt, 仅作元数据. 这样保持 vars 干净.

### D2. R245 reference 验证策略

与 M2 R151 一致: **语义对照**, 不强 byte-equal. R245 yaml 是 init 后空骨架, 跑 prompt-fit 写盘后人审 vs 设计 doc §3, 6 步审计 + trivial + supporting + 自费项加分 都覆盖即通过.

### D3. 17 条 prompt-fit 推进顺序

**按子类**: 12 条 G-a (R233-R244) → 5 条 G-b (R245-R249). 每子类内部 reuse 共享 trivial_dx_kw_list, 加快编写速度.

### D4. dry-run sanity 频率

**抽 2 条**:
- R245 (G-b reference, 文档样板)
- R236 (G-a 高额: 颌骨重建 ¥12000, 信号强, 文档强调)

不必每条 dry-run, 甲状腺患者预期都 CLEAN (无口腔 fee/diag), trace 形态一致.

### D5. ready 推进时机

**全 17 条 prompt-fit 写盘后, 统一 mark ready** (与 m1/m2 一致).

### D6. 验收门: audit-patient 实测对比

跑 `javert audit-patient J66252 --priority P1 --share-tool-cache --concurrency 5`, 写组 H 到 `docs/sample_audit_patient.md`. 与组 G 对比:

- **预期 A**: P1 子集 20 条 (3 M2 P1 + 17 M3 P1) 总耗时 5-8 min, 全 CLEAN (V=0 C=20 I=0)
- **预期 B**: M3 子集 avg/rule 接近 M2 子集 60s (M3 模板与 M2 复杂度近似), 总耗时 ~12-15 min 若串行
- **回退**: 若 M3 子集 出现非预期 V/I 量, 看 trace 怀疑 vars 写错 (trivial_dx_kw 太严格 / supporting 太宽)

### D7. spec delta 形态

与 m2-rollout 一致. ADDED Requirements:
- `rule-templating`: M3 template 完整与可渲染
- `rule-registry`: 17 条 M3-class 规则 ready, derived_from_template=M3, priority=P1

## Risks / Trade-offs

**[R1] init_pilot_rules 默认 priority=P3** — 17 条骨架 init 出来是 P3, 需要手动改 P1. Mitigation: 在 init 完后立即用 yaml 直读直写改 priority (本期 phase 1 完成)

**[R2] 17 条骨架 init 后 prompt_addon 全空** — 跟 m1-rollout 时 13 条空骨架一样, 不影响 prompt-fit 写盘. Mitigation: prompt-fit 设计上就是 "空骨架 → 装填" 流程

**[R3] vars 写错让 LLM 误判 trivial vs supporting** — G-a 与 G-b 共享 trivial 不同, 若混写会让 G-a 规则把 G-b 的诊断也算 trivial. Mitigation: D3 按子类顺序, G-a 12 条用同一份 trivial, G-b 5 条用另一份

**[R4] 甲状腺患者 J66252 跑 17 条都 CLEAN, 看不出 M3 触发能力** → 这是设计预期, 不是问题. Mitigation: 设计 doc §7 已声明 v0.3 才批跑

**[R5] init_pilot_rules 用 PILOT_RULE_IDS 默认列表** — 直接调用会重创已存在 yaml. Mitigation: 显式传 `pilot_ids=['R233'..'R249']` + force=False 不覆盖

**[R6] 17 条规则 violation_type 不一致** — 0325 xls 中 R233-R244 是 "虚构医药服务项目或以骗保为目的串换项目", R245-R249 是 "虚构医药服务". derived_from_template=M3 是统一锚定, 但 violation_type 仍按 xls 原文保留. Mitigation: 不改 violation_type, 让 derived_from_template 作为统一锚

## Migration Plan

按 D3 顺序, 4 个 commit-window:

1. **W1 (模板装填 + init)**: 装 M3.yaml + init 17 条骨架 + priority→P1; commit "phase1: M3 模板装填 + 17 条 G 类骨架 init"
2. **W2 (G-a 12 条)**: R233-R244 prompt-fit + dry-run R236; commit "phase2: M3 rollout G-a 美容修复套 12 条"
3. **W3 (G-b 5 条)**: R245-R249 prompt-fit + dry-run R245; commit "phase3: M3 rollout G-b 普通诊治虚增 5 条"
4. **W4 (验收)**: mark 17 条 ready; 跑 audit-patient --priority P1; 写组 H; 更新 CLAUDE.md; commit "phase4: m3 rollout 验收"

回滚: 单条 vars.json 删除 + `javert mark Rxxx --status drafting --force`. 极端: 删 vars + 把 prompt_addon 改回 `''` + 删除 init 出的 yaml. M3.yaml 装填回滚 = 恢复空骨架.

## Open Questions

- **Q1**: subclass 字段是否真的需要? 倾向 **保留**, 即使不参与 prompt 渲染, 仍是有用的元数据 (后续报告时按子类汇总)
- **Q2**: total_amount_threshold 字段在甲状腺 pilot 完全用不上 (没口腔费用). 是否在 v0.3 切换后再调? 倾向 **现在装好**, 默认 0 表示不启用, 未来调高 (例如 800) 加分判 V
- **Q3**: 跑组 H 后是否同时跑 J18906 (脑干非肿瘤) / J13365 (骨科) 验证非口腔患者? 倾向 **本期可选附加**, 不强求
