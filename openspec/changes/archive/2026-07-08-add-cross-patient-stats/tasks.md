## 1. 统计层纯函数 + 阈值配置

- [x] 1.1 新建 `configs/systemic_thresholds.yaml`: 全局 `v_rate: 0.5` + `min_patients: 10` + `overrides: {}`
- [x] 1.2 `src/javert/stats/cross_patient.py`: `RuleAgg` / `RuleStat` / `Thresholds` (pydantic) + `load_thresholds()`
- [x] 1.3 `aggregate(rows) → RuleAgg[]` (latest 三元组计数 + v_patients 列表) + `compute_rule_stats(aggs, th) → RuleStat[]` (双阈值 + 归因 + v_rate desc 排序)
- [x] 1.4 单测 `tests/test_cross_patient_stats.py`: 达标/样本不足边界、override、V 率数学、排序

## 2. store 只读查询

- [x] 2.1 `SqliteStore.latest_verdict_rows(batch_tag=None)` — SELECT + Python 取每 (rule,patient) 最新
- [x] 2.2 `SqlServerStore.latest_verdict_rows(batch_tag=None)` — ROW_NUMBER CTE rn=1 (复用现有口径)
- [x] 2.3 sqlite dedup 单测 (同 rule+patient 两 run, latest 胜)

## 3. CLI stats

- [x] 3.1 `cli.py` 注册 `stats` 子命令 (`--batch-tag` / `--min-patients` / `--min-v-rate`)
- [x] 3.2 `commands/stats.py`: sqlite → latest_verdict_rows → aggregate → compute → tabulate 表 + 系统性汇总 + V 率口径脚注

## 4. 工作台面板 + 导出

- [x] 4.1 `routes_workbench.dashboard` 注入 `systemic_rules` (latest_verdict_rows → compute)
- [x] 4.2 `dashboard.html` 加「系统性违规」区块 (V 率倒序 + systemic 徽标 + 金额「不可计」列 + 口径脚注)
- [x] 4.3 `fetch_export_rows` 加「规则维度」sheet

## 5. 规则解锁评估 (只更新 notes, 不误解锁)

- [x] 5.1 R003/R286 notes 更新: 跨患者报表面已具备, 专属检测器 (文书相似度 / 超工时频次) 仍为后续 change
- [x] 5.2 R280/R281 notes 更新: 行为审计, 本统计层不涉及

## 6. 验证

- [x] 6.1 `uv run pytest tests/ -v` 全绿
- [x] 6.2 `uv run javert stats` 本地 sqlite 出表 (端到端看 trace)
