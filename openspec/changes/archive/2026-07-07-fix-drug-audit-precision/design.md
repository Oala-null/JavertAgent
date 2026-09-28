# fix-drug-audit-precision — design

## Context

三个缺陷的机制 (扫描已核验):

1. `runner.py:36` 对所有工具结果按 2000 字符**尾截断**; `drug_audit_lookup` bulk 把命中药列表放前、ground truth 诊断块放**最后**——命中药一多, 截掉的恰是判"有无适应症"的唯一硬证据, 模型只能按 KB 条文倾向判 V. `search_notes` 的反向语义告警同样 append 在末尾.
2. `verdict_gate.py:319` ③ 闸只拦 `conf ∈ [0.70, 0.85)`; conf=0.5 或缺失归 0 的 V 直通落库.
3. RD10-RD37 与 R007/RD01-03 判定逻辑逐字相同, 同优先级批跑重复计违规.

约束: 不改模型/endpoint; 不动 M8 模板语义; 62 有 v2.1 对照基线可复跑.

## Goals / Non-Goals

**Goals:**
- 三缺陷分别修掉, 每处改动可独立验证
- 用 v2.1 同患者集重跑, 量化药品精准度变化

**Non-Goals:**
- 模板确定性 precheck 分层 (扫描主线二, P2 另立 change)
- 药品 KB 内容修订、M8 prompt 重写
- 其余 gate 闸的重构 (只动③闸语义与 R205 归属)

## Decisions

### D1. 分段截断: 标记行方案

工具输出约定一个固定标记行常量 (如 `====[必留头部结束]====`, 定义在 runner 或 tools 公共处): 标记之前是必留段, 之后是可截明细. `_truncate` 改为: 含标记时保全头部整段、明细截到剩余预算并在尾部附 `…[明细已截断, 原 N 字符]`; 无标记时保持现状尾截断 (其余 8 个工具零改动).

- `drug_audit_lookup`: ground truth 诊断块从末尾挪到头部必留段 (工具自己写着"判定指征请优先用此", 挪前语义更顺).
- `search_notes`: 反向语义告警挪进必留段头部 (告警自身带段落定位, 不依赖出现在末尾).

替代方案: (a) 工具返回结构化元组 (head, detail) — 要改 ToolExecutor 返回类型契约, 侵入面大, 拒; (b) 只挪 ground truth 到头部、不加标记 — 明细被截时 LLM 无感知, 且未来工具接入无契约可循, 拒.

### D2. `_TRUNCATE` 提配置

`llm.yaml` 加 `tool_result_max_chars` (默认 2000 不变), `config.py` 读取, runner 使用. 药品 bulk 明细天然长, 修复后如仍观察到明细截断影响判定, 可单独调大观察——不在本 change 内为单工具做差异化上限 (YAGNI).

### D3. conf 闸语义修正

③ 闸条件 `conf_floor <= conf < ceiling` 改为 `conf < ceiling` (floor 以下更该降, 不是更不该降). `conf_floor` 字段保留读取但不再参与判断 (yaml 注释标注弃用), 不改配置 schema、不动其他闸.

### D4. R205 移出⑥闸

只删 `configs/verdict_gate.yaml:26` 的 `- R205`, 并在该闸注释补一句"计数类规则 (按次超收) 不适用存在性闸". 代码零改动.

### D5. M8 收敛用 status=abandoned

RD10-RD37 28 条 `status: abandoned` + notes 注明"由 bulk R007/RD01-03 独占, 消重复计违规" (abandoned 是既有语义: audit-patient 默认跳过). 之后重跑 `build_rule_mapping.py` 重建 router index.

替代方案: (a) 删 yaml — 丢失已填好的个性化字段与专家复核痕迹, 拒; (b) 降级为"router 触发提示、不进 LLM"新机制 — 需要新状态与新链路, YAGNI, 拒.

## Risks / Trade-offs

- [截断预算不变时命中药明细仍可能被截 → 部分药未被审视] → 截断提示让模型/复核者可感知; `tool_result_max_chars` 可调; 对照批次观察漏检
- [③闸收紧可能把真 V 降 I] → gate 原则本就"只降不升、宁 I 不假 V"; 对照批次核对 V 流失是否集中在低 conf 段
- [M8 收敛后, 工作台历史 RD 裁决仍在库, 与新批次口径不一致] → 新批次打独立 batch_tag, 汇报时说明口径; 历史行不动
- [R205 出闸后其原始假阳性 (若有) 回流] → v2.1 专家标注显示 R205 ×4 全是"1手术1全麻=OK"型, 属②计数闸/LLM 判定问题, 出⑥闸后由对照批次复核

## Migration Plan

1. Mac 改码 → `uv run pytest tests/ -v` 全绿
2. `dry-run` J90508 (药品丰富) 看 trace: ground truth 块在截断后仍完整可见
3. 62 升级 src + configs → 重跑 v2.1 药品对照患者集 (`JAVERT_BATCH_TAG=drug-fix-v2.2`)
4. 对比 `docs/v2_1_gate_drug_analysis.md` 基线, 结论写进 docs (精准度/假阳性分布/V 计数口径)
5. 回滚: 均为小改, git revert + 62 重部即可; RD 28 条 status 改回 ready 即恢复

## Open Questions

- 重跑对照集用 v2.1 原患者集全量还是抽样? (建议全量——批跑成本已知可接受, 结论才可比)
