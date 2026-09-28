# Design: add-fn-regression-library

## Context

5 例专家案例已人工归因 (见 proposal), 其中 4 例裁定为 FN、1 例 (211440399×R063) ground truth 待裁不登记。211318013×R225 本已通过规则细化修复 (dry-run V conf=0.90, 证据点名关节松动+颈椎), 需要 anchor 固化; 其余案例的修复分属后续两个 change, 落地前需要基线证明"确实漏"、落地后证明"确实接住"。

约束: 回归跑真 LLM (Qwen3.6@62), 有漂移与耗时; 案例患者多为 hub-only (不在本地 CSV); 不得污染生产库。

## Goals / Non-Goals

**Goals:**
- 每例专家 FN = 一个 git 可 diff 的可执行案例文件
- catch 率成为可复跑的召回度量; 退化 (full→miss) 能拦截
- 基线报告与人工归因表互证

**Non-Goals:**
- 不进 pytest 套件 (真 LLM 调用不混进单元测试)
- 不做 CI 自动触发 (先手动, 用出节奏再固化)
- 不修任何 FN (修复属后续 change)

## Decisions

### D1. 案例 = yaml 文件, 不进 DB

`tests/fn_cases/<case_id>.yaml`, 与"规则即文件"哲学一致:

```yaml
case_id: FN-005
patient_id: "211318013"
rule_id: R225
expected_verdict: VIOLATION        # 最低期望严重度
expected_evidence_keywords: [关节松动训练, 颈椎]
data_source: hub                   # hub → runner 自动 etl_from_data_hub 到缓存目录
found_by: 专家协查 2026-07-08
attribution: 规则粒度不足 (原 R225 关键词摸不到关节松动训练)
```

FN-001/002 的 rule_id 在规则落地前为占位 (`TBD-溶栓虚构`), 后续 change 落规则时回填 — 案例先于规则存在, 恰好表达"覆盖空白"这一归因。runner 对 TBD 案例直接判 miss 并注明"无规则", 不报错。

### D2. 三档判定, 严重度下限断言

- **full**: verdict 达期望严重度 且 证据关键词全命中
- **partial**: verdict 只到 INCONCLUSIVE (期望 V 时), 或证据缺要素
- **miss**: 判 CLEAN / 规则未跑 / 无规则

召回口径 catch = full + partial (没有被静默放过)。断言用严重度下限 (V ≥ I > C) 而非等值, 容忍 LLM 漂移; `--repeat N` 可选多数投票。退化拦截: 任一案例从基线档位跌档 → 非零 exit。

### D3. dry-run 隔离 + hub 自动取数

runner 走 `javert dry-run` 等价路径 (不落生产库, `JAVERT_SQL_ENABLED=false`); `data_source: hub` 的患者自动 `etl_from_data_hub` 到 `output/fn_cache/<pid>/` 并以 env 覆盖数据目录, 缓存命中则跳过取数。基线档位存 `docs/fn_baseline.md`, runner `--against-baseline` 比对跌档。

## Risks / Trade-offs

- [LLM 漂移使回归自身抖动] → 下限断言 + partial 缓冲 + `--repeat`; 抖动记录进报告 (它就是后续 drift-guard 的证据)
- [5 例样本小, catch 率统计意义弱] → 库是增量的, 专家每发现一例就登记一例; 度量价值在趋势与退化拦截, 不在绝对值
- [真 LLM 回归慢 (~分钟/例)] → 例数少 + 手动触发, 可接受

## Migration Plan

纯增量, 无部署风险: Mac 实现 → 跑基线 → 62 复跑一遍验证 hub 取数路径。回滚 = 删文件。

## Open Questions

- 回归触发时机固化 (每批跑前 / 改规则后 / 手动) — 先手动
