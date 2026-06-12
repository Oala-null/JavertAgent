## Context

`add-rule-template-fitter` 完成 (2026-05-13) 后 M1 模板就绪并通过 R191 byte-equal round-trip 验证. 现有 41 条 yaml 中 violation_type 含「重复收费」的有 15 条:

| 已 ready | prompt-fit 已写 (但 status=drafting) | 空骨架 (本期主战场) |
|----------|--------------------------------------|---------------------|
| R191 | R045 | R047 R069 R077 R112 R116 R118 R119 R185 R208 R226 R228 R260 R300 (13 条) |

`add-patient-centric-audit` 组 A 数据 (J66252 冷启动) 揭示空骨架是耗时主因 — R208 (基础麻醉 + 强化麻醉) 与 R228 (重症监护 + 各类基础护理) 在 Top-3 慢规则中, 跑了 129s 与更慢. R226 同样在 list 中. 这些都是本期目标。

操作者: 一名熟悉 yaml + Javert CLI 的 medical-audit 工程师 (即本对话操作者). 单条 vars.json 编写时间预估 10-15 min (参考 m1_r045_vars.json 19 字段, 约 200 行 JSON).

约束:
- 不动 M1.yaml 模板 (作 D1 的 mitigation: 反推不下来才回头加 field)
- 不动代码 / 测试 / schema
- 数据填充阶段, 没有"代码冷启动 - 跑测试 - 改"的循环, 只是 vars-design → prompt-fit → dry-run → mark 四步循环 14 次

## Goals / Non-Goals

**Goals:**
- 14 条 yaml 从 drafting (含空骨架与 R045) 推到 ready, 全部带 `derived_from_template: M1`
- 每条 yaml 的 prompt_addon 字节量与 R191 量级 (600-900 chars), trigger_keywords 5-10 项, expected_signal 2-3 行
- 至少 1 条规则做完后跑 `javert dry-run Rxxx --patient J66252` 看 trace, 确认 LLM 能消化新 prompt 不丢工具调用
- 全部装完后跑 `javert audit-patient J66252 --share-tool-cache`, 写一次实测段记录在 docs/sample_audit_patient.md, 用于和组 A baseline (41.5 min) 对照

**Non-Goals:**
- ❌ 不补 M1 设计列的 13 条无 yaml 规则 (R018 R054 R105 R115 R117 R205 R227 R292 R295 R296 R303 R304 R305) — 那是 add-pending-rules-init 的事
- ❌ 不动 M2-M6
- ❌ 不写新代码, 不加测试
- ❌ 不改 base.txt (那是 `base-prompt-inconclusive-bias` 的事)
- ❌ 不并发跑 (那是 `add-parallel-audit` 的事)
- ❌ R191 不动 (已 ready, 改它就破 round-trip 验证)
- ❌ 不强制对每条都跑完 5 患者 dry-run — pilot 阶段时间预算有限, 抽 2-3 条代表 dry-run 即可

## Decisions

### D1. vars.json 命名与目录结构

**选项:**
- (a) `docs/m1_rXXX_vars.json` 每条独立文件 (与 m1_r191_vars.json / m1_r045_vars.json 一致)
- (b) 单个 `docs/m1_rollout_vars.json` 多条共用 (顶层 dict 按 rule_id 分桶)
- (c) `docs/m1_vars/Rxxx.json` 一个子目录

**选 a**, 因为:
- 已与现有两个 reference 文件 (m1_r191_vars.json, m1_r045_vars.json) 一致, 不引新命名风格
- 单文件 git diff 易看, 操作者改某条不影响其他条 review
- prompt-fit 命令格式 `--vars docs/m1_Rxxx_vars.json` 与 reference 一致

### D2. 推进顺序: 按 domain 分批 vs 按耗时反向

**选项:**
- (a) 按 domain (骨科 → 血液净化 → 医学影像 → 麻醉 → 重症 → 口腔 → 精神医学)
- (b) 按组 A baseline 耗时反向 (慢的先做, 最大化提速回报)
- (c) 按 rule_id 升序

**选 b**, 因为:
- 慢规则 (R208 R228 R226) 装完 prompt 单次收益最高, 一边做一边可以看是否提速
- 先做难的, 难度递减保操作者节奏
- 顺序: R208 R228 R226 R077 R069 R112 R118 R119 R116 R047 R185 R260 R300 R045-推 ready

R045 由 add-rule-template-fitter 已写盘, 不重跑 prompt-fit, 仅推 ready.

### D3. dry-run sanity 频率

**选项:**
- (a) 每条都 dry-run J66252 (14 次, 时间昂贵)
- (b) 每 domain 选一条 dry-run (~7 次)
- (c) 仅前 3 条 + 最后 1 条 dry-run

**选 c**, 因为:
- prompt-fit 写盘后, base.txt + Runner 流程没变, prompt 文案对错由人审 master_prompt 渲染时已能看出
- 前 3 条 dry-run 确认整体 trace 形态正常, 最后 1 条做"全装完后"的端到端复核
- 节省时间预算 (整 audit-patient 还要再跑一次)

### D4. ready 推进时机

**选项:**
- (a) 每条 prompt-fit 后立即 mark ready
- (b) 全 14 条 prompt-fit 写盘后, 统一 mark ready
- (c) dry-run 通过的 mark ready, 其他保 drafting

**选 b**, 因为:
- 14 条同一模板派生, 质量同源, 一并推 ready
- mark 是单条命令, 批量执行 `for r in R047 R069 ...; do javert mark $r --status ready; done` 简洁
- 万一 audit-patient 实测发现某条问题, 个别 mark drafting --force 回退即可

### D5. 验收门: audit-patient 实测对比

跑 `javert audit-patient J66252 --share-tool-cache`, 把结果写到 docs/sample_audit_patient.md 新增"M1 rollout 后"节. 与组 A baseline (41.5 min, V=1 C=28 I=1, avg 83s/rule) 对比:

- **预期 A**: avg/rule 显著下降 (Top-3 慢规则不再是 M1 类) → 本期成功
- **预期 B**: avg/rule 没变, 但 verdict 分布出现新 V (false negative 修正) → 仍成功
- **回退**: avg 反升 + verdict 形态恶化 → 看 trace, 可能是某条 vars 写错让 LLM 误判, 个别回退 drafting

不强行设 avg/rule 数字目标 — 因为 share-tool-cache 减小了 IO, 暖启动跑出的耗时与组 A 冷启动不可直接比.

### D6. 13 条 vars 字段的写法风格

参考 m1_r045_vars.json (骨科手术) 与 m1_r191_vars.json (检查类), **两套已覆盖 fee_category 的 4 个主要枚举值**:
- 手术类: R045 R047 R185 R260 (骨科 / 骨科 / 肿瘤 / 口腔)
- 检查类: R077 R112 R116 R118 R119 R191 (血液净化 / 医学影像)
- 其他类: R069 R208 R226 R228 R300 (耗材 / 麻醉 / 重症 / 重症 / 精神)

每条 vars 强制覆盖 M1 的 19 fields. operator 参考 docs/templates/模板1_重复收费.md §4 给出的要点和关键词候选.

aux_keywords 取 5-10 项, aux_tools 一律 ["note_diagnosis", "search_fees", "search_notes"] 三项 (drug_indication 与 M1 无关), aux_signal 2 行 (典型违规 + 典型 CLEAN).

### D7. spec delta 形态

`rule-registry` capability 已存在. M1 rollout 不改字段定义, 只把 14 条规则的 status / 字段填满. spec delta 写法:
- 不能用 ADDED Requirements (没新 requirement)
- 应该 MODIFIED 一个已有 requirement 的 scenario, 加 "M1-rollout 之后, 15 条 violation_type=重复收费 规则全部 ready 且带 derived_from_template=M1"

但实际上这 14 条规则的状态变化不是 capability 行为变化 — 它们只是数据填充. spec delta 文件可只写一个 "MODIFIED Requirements" 节注 "15 条 M1 规则 ready", 不展开 scenario.

## Risks / Trade-offs

**[R1] vars 写错 LLM 误判** → 操作者 vars 关键词错位 (例如把 attached 写进 primary), 跑 audit 时全员 VIOLATION 而错. Mitigation: D3 抽样 dry-run 看 trace, 出现明显 VIOLATION 暴增立即回看 vars

**[R2] M1 字段不够覆盖某条规则** → 例如 R208 (麻醉) 的"基础麻醉 + 强化麻醉"语义跟 M1 设计的"主项 + 附属"对得上, 但具体文案 R208 可能要求时长比对而 M1 没这个 field. Mitigation: 若反推某条不下来, 立即停手, 把 M1.yaml 加 field 算后续 `extend-m1-template` change, 不强塞

**[R3] J66252 不适合做 audit-patient 实测对比** → R047 / R069 等是骨科血液净化规则, J66252 是肿瘤患者, 多数会判 CLEAN. Mitigation: 这无所谓; M1 rollout 的目标是装 prompt 让 LLM 不迷路, 不是让每条都触发 VIOLATION. J66252 跑 N=15 看 trace 是否变干净比看 verdict 重要

**[R4] R191 已 ready, 推 R045 + 13 骨架到 ready 会让 list "ready: 15" 跳出来但跑 audit 还很慢** → 装 prompt 不是并发, 单进程 41 min 串行问题没解决. Mitigation: 不在本期 scope (D 决定); 由 `add-parallel-audit` 接力

**[R5] 操作者疲劳, 13 条 vars 写得越来越草** → 第 8 条之后 trigger_keywords 写 3 项搪塞之类. Mitigation: 按 D2 顺序, 前几条 (R208/R228/R226) 是反复审核的样板; 后期 (R047/R260/R300) 即使略简也不影响主旋律

## Migration Plan

按 D2 顺序, 4 个 commit-window:

1. **W1 (高耗时段)**: R208 → R228 → R226 → dry-run R208 看 trace; commit "phase1: m1 rollout 重症麻醉 3 条"
2. **W2 (血液净化 + 影像)**: R077 → R069 → R112 → R118 → R119 → R116 → dry-run R077; commit "phase2: m1 rollout 净化与影像 6 条"
3. **W3 (其他)**: R047 → R185 → R260 → R300 → R045 推 ready; commit "phase3: m1 rollout 余 4 条 + R045 ready"
4. **W4 (验收)**: mark 14 条 ready (R045 也 ready); 跑 audit-patient; 写 docs/sample_audit_patient.md; commit "phase4: m1 rollout 验收"

(实际项目非 git repo, "commit" 视为 logical checkpoint, 不实际 commit.)

回滚: 单条 vars.json 删除 + 用 `javert mark Rxxx --status drafting --force` 退状态; 极端情况删 vars + rerun init 或人工把 prompt_addon 改回 `''`.

## Open Questions

- **Q1**: 是否在本期把 docs/sample_run_R191.md 也补上 (TODO 项)? 倾向 **不**, 与 m1-rollout 无直接因果, 留给单独 doc-cleanup change
- **Q2**: 14 条全装完后, 是否立刻跑 `audit-patient J18906` (脑干占位, 不同 domain) 验证非肿瘤患者? 倾向 **是, 作为本期附加验证**, 但不强求
- **Q3**: R045 已 prompt-fit 但 status=drafting; 若 vars.json 已可视, 是否复跑 prompt-fit (与 add-rule-template-fitter 跑的结果一致)? 倾向 **不复跑**, 直接 mark ready, 节省时间
