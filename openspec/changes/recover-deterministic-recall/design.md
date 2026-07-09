# Design: recover-deterministic-recall

## Context

verdict_gate 现状: ②单次闸按"净不同收费次数 ≤1"降 C, 463 条存量; gate 只降不升是设计原则 (v2.1 消 78 假阳性), 本 change 不推翻它, 只修计数口径错位 + 给降级行出口 (I/facet)。search_fees 现状: 按行原样输出, 退费行与正收行并列, LLM 需自行心算净额 (211440399 实证算错)。persist 现状: 重跑新行直接落, 老 V 新 C 无任何痕迹浮现。

约束: 存量 5000+ 裁决行上有专家批注, 任何存量操作不得触碰 review 行; 142 是生产工作台, 重筛必须 dry-run 先行 + 可一键还原; 每项改动独立开关回滚。

## Goals / Non-Goals

**Goals:**
- 回收 gate 误杀 (套餐口径) + 击杀透明化
- 证据层三个失真点修正, LLM 与专家拿到同一套事实
- 历史 V 不再静默消失

**Non-Goals:**
- 不动③conf 闸/⑥存在性闸/非套餐规则的②闸行为
- 不做 M5 语义/companion 预检 (LLM 行为面, 另 change)
- 不自动改写存量漂移 (只报告, 人裁)

## Decisions

### D1. 套餐口径 = gate yaml 集中配置, 规则文件不加字段

`configs/verdict_gate.yaml`:

```yaml
overuse_single_pass:
  panel_rules:
    R155: {min_distinct_items: 3}    # 已实证 (211419211 11项/日); R151/R132 等候选须逐条核实"多项目单日打包"形态才入集
  panel_downgrade_to: INCONCLUSIVE   # 仅 panel 规则; 其余维持降 C
```

计数口径: 规则费用关键词族命中的**同日不同项目名数** (净额>0)。11 项细胞因子一天 = 11 项 ≠ 1 次。入集与阈值终值由核对 + 重筛 dry-run 分布定; 误入集的代价是该规则闸降级目标变 I, 放大 I 量, 故不实证不入。
替代方案: 全量②闸改降 I — 否, 78 类真单次假阳性会倒回专家队列。

### D2. 重筛 = 原地 UPDATE + 可逆标签, 不 insert

这些行的 verdict 是**机械闸的决定不是 LLM 的** — 修闸即修行; reasoning 里 LLM 的 V 论证原样在, insert 新行反而伪装成一次不存在的重跑。`gate_tag='单次闸重筛回升(原C)'` 内嵌原值, 反向脚本按标签一行还原。已验证 C 行默认视图不可见 → 无 review 挂靠; 脚本仍断言: 目标行有 review 则跳过并报告。顺序 sqlite (source-of-truth) 先、142 后; `--dry-run` 出按规则分组的翻转量与项目数分布, **>100 条则收紧阈值再落**。

### D3. 证据层修在 loader/工具层, 全规则受益

- search_fees 按项目名聚合净额, 复用 `patient_overview` 已有抵消逻辑 (不三处三口径 — precheck 充退剔除已是第二处, 统一提到共享 helper); 净额≤0 项不输出, 行尾 `[含N次退费已抵消]`。
- 数量小数: 排查 csv/hub 两链格式化点 (疑在 fee 行渲染 int cast), 保留原值。
- R063 50%→75%: 纯规则数据修正, 附现行条款依据; 同时是 211440399×R063 三轮漂移的部分成因 (该案 ground truth 待专家裁定, 见 drift_report)。

### D4. drift guard 在 persist 层, 不在显示层

verdict 消费方不止工作台 (导出/dashboard/跨患者统计), 显示层修只堵一个出口。`result_persister` 写前查 sqlite 同 (rule,patient) 最新历史: 老V + 新C → 落 I + tag; **例外**: 老 V 有专家 latest review 且驳回 → 放行 C (专家已裁定 V 是错的, C 是修正)。`JAVERT_DRIFT_GUARD` 默认 on。存量 (含 2026-07-08 晨 211440399 R063/R155) 由 `drift_report.py` 只读出清单 — 存量翻转可能是新代码修对了, 机器分不清, 人分得清。

### D5. facet 复用既有 gate_tag 列, 零 schema 改动

`Javert_audit_runs.gate_tag` 已在; sidebar/detail 把 gate_tag 非空暴露为 facet 维度 (纯前端 show/hide, 与既有 verdict filter 正交叠加, 沿用 v0.9 facet 引擎模式)。

## Risks / Trade-offs

- [重筛放量, 专家被 I 淹没] → dry-run 分布 + >100 收紧 + 可分批 (先 R155 验证专家接受度)
- [drift guard 拦住"修对了"的 C] → 只升到 I, tag 写明来由, 专家一眼裁; 驳回豁免防借尸还魂
- [panel 计数需按规则关键词族扫费用, gate 从纯内存判断变成要碰费用数据] → 复用 runner 已加载的患者费用 (gate 入参扩一个 fee frame), 失败 fail-open 维持原口径
- [search_fees 聚合改变输出形状, 既有规则 prompt 可能依赖逐行形态] → 保持行式输出只改聚合行内容; FN-005 anchor + 抽查 M1 精选规则 dry-run 防回归

## Migration Plan

1. Mac 实现 + 单测绿 (gate 三分支 / persister 四路径 / search_fees 净额) + FN 回归: FN-005 不退化
2. 62 部署 (tar src+configs+scripts), `rescreen_gated.py --dry-run` 出分布 → 与用户定阈值 → sqlite 落 → 142 落 → FN-003 验证翻 I
3. javert-web 重启 (facet); `drift_report.py` 出存量清单交专家
4. 回滚: gate yaml 删 panel_rules 节 = 回旧口径; `JAVERT_DRIFT_GUARD=off`; 重筛反向脚本还原; search_fees/小数为纯修复不设开关 (git revert)

## Open Questions

- min_distinct_items 终值 — dry-run 分布定
- 重筛是否分规则分批放 — 视第一批专家反馈
