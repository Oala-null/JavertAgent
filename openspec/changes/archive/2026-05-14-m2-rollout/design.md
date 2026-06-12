## Context

m1-rollout 完成 (2026-05-14) 后 M1 模板 + 15 条规则形成 ready 集, 组 E 实测 J66252 暖启动 18.6 min, avg 74.5s/rule, 全部高置信 CLEAN (≥0.95). M1 套填策略验证可行: 模板抽公因子 + vars json 个性化 + prompt-fit 写盘 = 单条规则 prompt 装填成本压到 ~10 min vars-design + 1 行 CLI.

本期切到 M2 (过度检查), 与 M1 的核心差异:

| 维度 | M1 (重复收费) | M2 (过度检查) |
|------|--------------|--------------|
| 触发模式 | A 类 fee + B 类 fee 并存 + 文书无反证 | exam fee 命中 + 诊断无指征 |
| 主工具 | search_fees + search_notes | note_diagnosis + search_fees |
| 可选条件 | 文书第三方说明 | drug_check 联查药品 (R219/R313) |
| INCONCLUSIVE 触发 | 信息不足 | 诊断中间态 (+ R151 特殊: 单次筛查) |
| 字段量 | 18 个 | 14-16 个 (无 a/b class 双列表) |

设计 doc 已就位 (`docs/templates/模板2_过度检查.md`, 381 行), 含:
- §2 master prompt 骨架
- §3 R151 reference yaml 完整版
- §4 B 类 28 条全部 personalization 要点
- §5 字段填充速查表 (exam_kw 数量 / indication_dx 数量 / min_count / 联查药品 / 信号强度)

操作者: 本对话操作者 (同 m1-rollout). 已熟悉 prompt-fit 工作流, 单条 vars.json 编写预估 8-12 min.

约束:
- 模板装填 + 数据填充, 无代码改动
- 不强行处理 10 条 MISSING (R108 R132 R218 R221 R222 R225 R278 R279 R311 R312) — 留给后续 change
- 不动 base.txt / audit_runs schema / 代码

## Goals / Non-Goals

**Goals:**
- M2.yaml 模板从空骨架装满: master_prompt + 14-16 fields + keywords/tools/signal_template
- M2 模板支持 m1-rollout 之外的两个新维度: **drug_check 联查** + **single_count INCONCLUSIVE 兜底**
- 18 条 B 类 yaml 从空骨架推到 ready, 全部带 `derived_from_template: M2`
- 每条 prompt_addon 量级 600-1000 chars, trigger_keywords 5-12 项, expected_signal 2 行
- R151 作为 reference: 生成的 prompt_addon 语义覆盖设计 doc §3 (不强求 byte-equal, 因 §3 是 markdown 手写, 模板渲染会有标点/换行差异)
- 全装完后跑 `audit-patient J66252 --share-tool-cache --concurrency 5`, 记录组 G

**Non-Goals:**
- ❌ 不补 10 条 MISSING 骨架 — 由 `add-pending-rules-b-class` 接力
- ❌ 不为 R225/R311/R312 (零命中候选) 写 prompt — 设计 doc 已建议 abandoned
- ❌ 不强 byte-equal round-trip (R151 yaml 当前是空骨架, 没源 prompt 可比对)
- ❌ 不动 M3-M6 (各自 rollout)
- ❌ 不写新代码 / 不加测试 / 不改 base.txt
- ❌ 不强制每条规则 dry-run (抽 2-3 条代表即可, 整 audit-patient 是端到端验证)

## Decisions

### D1. M2 模板字段集设计

**关键问题**: M2 比 M1 多出 drug_check 与 single_count INCONCLUSIVE 两条变体, 怎么用 jinja2 表达?

**选项:**
- (a) 全字段 required: true, 不存在条件块的字段给空值 (e.g. drug_kw_list: [])
- (b) 用 `{% if drug_check %}` 等条件块, 可选字段 required: false + default
- (c) 拆 M2 为 M2a (无 drug_check) / M2b (有 drug_check) 两子模板

**选 b**, 因为:
- 条件块让 prompt 渲染干净 (drug_check=false 时不出现"特殊条件"段)
- vars 文件简洁: 不需要的字段不写
- 单模板覆盖所有变体, 不引"M2a/M2b 选型"决策
- 已被 jinja2 StrictUndefined 实现支持 (`{% if %}` 不算 undefined)

字段清单 (16 个):

| 字段 | 类型 | required | 说明 |
|------|------|---------|------|
| exam_concept_desc | str | true | 检查项目类别描述 (例: 乙肝/丙肝抗体测定) |
| catalog_basis | str | true | 适应症范围 + 依据来源 (1-3 句) |
| exam_kw_primary_list | list[str] | true | search_fees keyword 主搜索词 (2-5 个) |
| indication_dx_list | list[str] | true | 诊断指征列表 (任一命中即 CLEAN, 5-16 项) |
| min_count | int | true | 多次违规阈值 (默认 1, R151=2) |
| inconclusive_addendum | str | false (default "") | 额外 INCONCLUSIVE 条款 (R151 单次筛查) |
| drug_check | bool | false (default false) | 是否联查药品 |
| drug_kw_list | list[str] | false (default []) | 药品关键词 (drug_check=true 必填) |
| drug_concept_desc | str | false (default "") | 药品类别简称 (例: 肌松药) |
| special_notes | list[str] | false (default []) | 特殊注意 0+ 条 (例: 女性绝对违规) |
| pilot_caveat | str | false (default "") | pilot 适用性说明 (甲状腺患者有指征等) |
| aux_keywords | list[str] | true | trigger_keywords |
| aux_tools | list[str] | true | suggested_tools (推荐 [note_diagnosis, search_fees]) |
| aux_signal | str | true | expected_signal 整段 |
| basic_diagnosis_keywords | list[str] | false (default []) | 主诊断关键词 (可用于过滤; M2 多数为空, 仅 R279 等可用) |

### D2. R151 reference 验证策略

m1-rollout 用 R191 做 byte-equal 验证 (因为 R191 yaml 已有手写 prompt). 但 R151 当前是空骨架, 没有 prompt 可比对.

**选项:**
- (a) 强 byte-equal: 用设计 doc §3 prompt_addon 段 hand-paste 进 R151.yaml, 然后 prompt-fit 重写, 跑 diff
- (b) 语义对照: prompt-fit 后人工比对生成的 prompt vs 设计 doc §3, 关键步骤都覆盖即通过
- (c) 不做验证: 直接套, 跟其他 17 条一起跑 audit-patient

**选 b**, 因为:
- 设计 doc §3 是给人看的 markdown, 含 `*多次*` 加粗 / 中英括号差异, byte-equal 不现实
- 语义对照足够: 6 步审计逻辑齐全 + 8 项诊断指征齐全 + min_count=2 + 单次 INCONCLUSIVE 都能覆盖即通过
- R151 跑 audit-patient 时若产出合理 verdict, 进一步证明 prompt 可用

### D3. 18 条 prompt-fit 推进顺序

**选项:**
- (a) 按 domain (临床检验 → 影像 → 麻醉 → 精神)
- (b) 按设计 doc §5 信号强度 (强 → 中, 强信号先做)
- (c) 按 rule_id 升序

**选 a**, 因为:
- 同 domain 规则字段填充模式相似 (B.1 临床检验 11 条都是"标志物 + 适应症"模式), batch 写 vars 更快
- 操作者 mental model 切换成本最低: 11 条临床检验一气呵成 → 4 条影像 → 2 条麻醉 → 1 条精神

顺序: R141 R143 R146 R151 R153 R154 R155 R156 R160 R161 R162 (11) → R109 R129 R130 R131 (4) → R219 R220 (2) → R313 (1) = 18

### D4. dry-run sanity 频率

**选项:**
- (a) 每条都 dry-run J66252 (18 次, 耗时)
- (b) 每 domain 选一条 (~4 次)
- (c) 抽 3 条代表 (R151 文档样板 / R141 强信号 / R219 drug_check 变体)

**选 c**, 因为:
- m1-rollout 经验: dry-run 主要看 prompt 形态, 不是看 verdict — 抽样足够
- R151 验证 single_count INCONCLUSIVE 分支
- R141 验证强信号普通 M2 (无 drug_check)
- R219 验证 drug_check 分支

### D5. ready 推进时机

**选 b** (与 m1-rollout 一致): 全 18 条 prompt-fit 写盘后, 统一 mark ready.

### D6. 验收门: audit-patient 实测对比

跑 `javert audit-patient J66252 --share-tool-cache --concurrency 5`, 写组 G 到 `docs/sample_audit_patient.md`. 与组 E (15 条 M1 ready, 18.6 min, V=0 C=15 I=0, avg 74.5s, hit 35.3%) 对照:

- **预期 A**: 33 条 ready 集总耗时 8-15 min (concurrency 5 + share cache), avg/rule 略升 (M2 多 note_diagnosis 调用) 但接受
- **预期 B**: B 类规则在肿瘤患者上产出几个 V (R151 若 J66252 多次测乙肝/丙肝可能 V; R156 PSA 看性别; R141/R143/R146 看诊断), 与 M1 全 CLEAN 形成对比, 说明 M2 触发条件设计合理
- **回退**: 出现 0 个 V (M2 触发完全失败) 或大量 INCONCLUSIVE → 看 trace 怀疑 vars 写错某个 indication_dx 太宽, 个别回退 drafting

### D7. spec delta 形态

`rule-templating` capability 已存在 (M1.yaml 装填时由 add-rule-template-fitter 引入). 本期 spec delta 加 ADDED Requirements:

```
### Requirement: M2 template装填与可用性
After this change, configs/templates/M2.yaml SHALL contain a complete master_prompt with jinja2 conditionals supporting drug_check / single_count / special_notes variants, with fields covering exam description, indication diagnoses, min_count threshold, and optional drug-check branch.
```

`rule-registry` capability spec delta 加 ADDED Requirements:
```
### Requirement: M2-class rules ready set
After this change, configs/rules/ SHALL contain 18 rules whose violation_type is "过度检查" and derived_from_template equals "M2" with status=ready: R109 R129 R130 R131 R141 R143 R146 R151 R153 R154 R155 R156 R160 R161 R162 R219 R220 R313.
```

## Risks / Trade-offs

**[R1] M2 模板设计漏洞** → 某些 B 类规则的特殊条件 (R156 女性绝对违规 / R218 联查麻醉记录) M2 模板 jinja2 表达不下, special_notes 兜不住. Mitigation: D1 special_notes 是 list[str], 可塞任意条款; 若反推某条不下来, 立即停手, 把 M2.yaml 加 field 算后续 `extend-m2-template` change, 不强塞

**[R2] R151 不再 byte-equal 验证, 可能 prompt 形态偏离设计意图** → 渲染结果与设计 doc §3 存在语义 gap. Mitigation: D2 改语义对照 + R151 dry-run 看 trace + audit-patient 实测看 verdict 合不合理

**[R3] B 类规则在肿瘤患者上全 CLEAN** → J66252 是甲状腺癌, R279 (甲功) 是 indication 100%, 但 R141 R143 R146 等检验也可能因为肿瘤随访被判 CLEAN, 验收数据看不出 M2 触发能力. Mitigation: 不在本期 scope; 后续跑非肿瘤患者 (J18906 脑干, J13365 骨科) 看 cross-domain trigger

**[R4] drug_check 工具不存在** → R219/R313 用 drug_check, 但 drug_indication 表 51 种药品可能不覆盖肌松药/抗精神病药. Mitigation: drug_check 在 prompt 里其实是让 LLM 调用 search_fees 搜药品名 (不是 drug_indication), 51 种药品表无关; 真正风险是 LLM 误判, 由 dry-run R219 兜底

**[R5] 操作者疲劳, 18 条 vars 写得越来越草** → 后期 trigger_keywords 写 3 项搪塞. Mitigation: 设计 doc §4 已给每条 4-19 个 exam_kw 候选, 直接抄即可, 不需要再思考

## Migration Plan

按 D3 顺序, 4 个 commit-window:

1. **W1 (模板装填)**: 写完整 M2.yaml + 跑 R151 prompt-fit 看渲染输出 + 比对设计 doc §3; commit "phase1: M2 模板装填 + R151 reference"
2. **W2 (临床检验 11 条)**: R141 R143 R146 R153 R154 R155 R156 R160 R161 R162 (R151 已在 W1) → dry-run R141; commit "phase2: M2 rollout 临床检验 11 条"
3. **W3 (影像 4 + 麻醉 2 + 精神 1)**: R109 R129 R130 R131 R219 R220 R313 → dry-run R219 (drug_check 变体); commit "phase3: M2 rollout 影像麻醉精神 7 条"
4. **W4 (验收)**: mark 18 条 ready; 跑 audit-patient J66252; 写组 G; 更新 CLAUDE.md; commit "phase4: m2 rollout 验收"

(实际项目非 git repo, "commit" 视为 logical checkpoint, 不实际 commit.)

回滚: 单条 vars.json 删除 + `javert mark Rxxx --status drafting --force`. 极端: 删 vars + 把 prompt_addon 改回 `''`. M2.yaml 装填回滚 = 恢复空骨架.

## Open Questions

- **Q1**: R279 (甲功) 对甲状腺 pilot 几乎全 CLEAN, 设计 doc 建议"该规则对甲状腺 pilot 不应该跑". 是否在 R279.yaml 写 pilot_caveat 标注但仍装 prompt + ready? 倾向 **装 + ready**, 标 pilot_caveat 让 LLM 知情, 但留 ready 让其他患者群体 (后续切换非肿瘤) 立即可用
- **Q2**: 18 条全装完后跑 J18906 (脑干占位, 非肿瘤) 看 cross-domain 触发? 倾向 **本期附加**, 不强求
- **Q3**: R151 single_count INCONCLUSIVE 是否升级为 M2 通用机制 (m2.yaml 加 `default_single_count_policy` enum)? 倾向 **不**, 用 inconclusive_addendum 这种 catch-all 字段更灵活, 不预设语义
