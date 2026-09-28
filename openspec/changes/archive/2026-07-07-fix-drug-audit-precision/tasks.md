# fix-drug-audit-precision — tasks

## 1. 分段截断 (1a)

- [x] 1.1 定义必留标记行常量 + `runner._truncate` 识别标记: 保全头部段、明细段截到剩余预算、尾附截断提示; 无标记维持现状
- [x] 1.2 `llm.yaml` 加 `tool_result_max_chars` (默认 2000) + `config.py` 读取 + runner 使用
- [x] 1.3 `drug_audit_lookup` bulk 输出: ground truth 诊断块挪进头部必留段 (标记之前)
- [x] 1.4 `search_notes`: `[否认段]/[选项框]` 反向语义告警挪进必留段
- [x] 1.5 单测: 超长保全 ground truth / 告警保全 / 无标记回归 / 配置生效与默认值

## 2. verdict gate (1c + R205)

- [x] 2.1 `verdict_gate.py` ③闸条件改 `conf < conf_ceiling`; `conf_floor` 标注弃用 (yaml 注释 + 代码注释)
- [x] 2.2 单测: conf=0.5 → I / confidence 缺失 → I / conf=0.90 → 不改判
- [x] 2.3 `configs/verdict_gate.yaml` ⑥闸移除 R205 + 注释"计数类不适用存在性闸"; 补 R205 场景单测

## 3. M8 精选收敛 (1b)

- [x] 3.1 RD10-RD37 28 条 yaml `status: abandoned` + notes "由 bulk R007/RD01-03 独占, 消重复计违规" (脚本或批量编辑)
- [x] 3.2 重跑 `scripts/build_rule_mapping.py` 重建 router index
- [x] 3.3 验证: `audit-patient --priority all --use-router` dry-run 执行集合无 RD10-37; `--rules RD20` 显式仍可跑

## 4. 端到端验证

- [x] 4.1 `uv run pytest tests/ -v` 全绿 (524 passed, 1 skipped)
- [x] 4.2 `dry-run` J90508 + J66252 看 trace: ground truth 在截断后完整可见; 甲状腺 on-label 闸仍 CLEAN
- [x] 4.3 62 升级 src + configs, 重跑 v2.1 药品对照患者集 (`JAVERT_BATCH_TAG=drug-fix-v2.2`) — 抽样 6 患者 × 4 bulk 规则, 写 142
- [x] 4.4 对比 `docs/v2_1_gate_drug_analysis.md` 基线, 精准度/假阳性分布/V 口径变化写进 docs → `docs/drug_fix_v2_2_sample.md`
