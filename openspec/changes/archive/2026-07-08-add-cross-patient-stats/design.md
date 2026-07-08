# Design — add-cross-patient-stats

## 决策

**统计层只读, 一个纯函数当唯一裁量源.** `stats/cross_patient.py` 里 `aggregate(rows) → RuleAgg[]` +
`compute_rule_stats(aggs, thresholds) → RuleStat[]` 两个纯函数是「系统性违规」判定的唯一实现;
CLI (sqlite) 和 dashboard/export (142) 都喂同一函数, 不各自算一遍阈值。

**latest 语义复用现有 CTE, 不发明第四处 verdict filter.** 两个 store 各加一个
`latest_verdict_rows(batch_tag) → [(rule_id, patient_id, verdict)]`:
- 142: `ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC)` + `WHERE rn=1`
  —— 与 `list_patients_with_violations` / `dashboard_stats` 完全同口径。
- sqlite: `SELECT rule_id,patient_id,verdict,created_at [WHERE batch_tag=?]` + Python 取每对最新
  (~5000 行, Python dedup 足够, 无需 window func)。

两 store 都只返回「已去重的 latest 三元组」, 聚合/阈值全在纯函数里, 接口对称。

**V 率分母 = 被审计患者数** (该规则有 latest 裁决的不同患者数, 含 CLEAN), 非全院患者。
面板/导出/CLI 都在标题明示这一口径, 防止误读为发生率。

## 数据形状

```
RuleAgg  = {rule_id, n_patients, v, i, c, v_patients: list[str]}
RuleStat = RuleAgg + {v_rate, i_rate, systemic: bool, reason: str, amount: float|None}
```

`systemic = n_patients >= min_patients AND v_rate >= v_rate_threshold`。
排序 v_rate desc → n_patients desc → rule_id。

阈值 `configs/systemic_thresholds.yaml`: 全局 `v_rate: 0.5` + `min_patients: 10` +
`overrides: {rule_id: {v_rate?, min_patients?}}` 按规则覆盖。

## 刻意不做 (ponytail)

- **金额合计**: `amount` 字段保留但 v1 恒为 `None` → 渲染「不可计」。真金额需 evidence 锚点
  逐 run join fee 行 (hit_resolver 级开销, 且常无锚 → 本就多半不可计)。列先在, 值后填。
  升级路径: 复用 `hit_resolver.resolve_hits` 对每条 V run 求锚定 fee 金额求和。
- **「统计规则形态」(非 LLM 裁决路径)**: 不建。本层聚合的是「已有 LLM 裁决」, 不新增
  原始频次/相似度检测器。没有任何一条具体规则能被通用统计框架直接判 (见下), 建它是投机。
- **规则解锁**: R003 (文书相似度) / R286 (超工时频次) 需各自专属检测器 (NLP 相似度 / 原始
  频次计数), 本层的「V 率聚合」不直接产出; R280/R281 是行为审计。四条都**不误解锁**,
  只更新 notes 说明「跨患者报表面已具备, 专属检测器仍是后续 change」。

## 影响面

- 新增: `src/javert/stats/cross_patient.py` + `configs/systemic_thresholds.yaml` +
  `javert stats` subcommand + dashboard 面板区块 + 导出「规则维度」sheet。
- 各 store 加 1 个只读方法 `latest_verdict_rows`。runner/router/gate/审计链路**零改动**,
  可整体回退 (纯增量)。
