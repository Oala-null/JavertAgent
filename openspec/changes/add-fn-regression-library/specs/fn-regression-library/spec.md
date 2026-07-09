# fn-regression-library Spec Delta

## ADDED Requirements

### Requirement: FN 案例登记为可执行回归文件

系统 MUST 以 `tests/fn_cases/<case_id>.yaml` 一例一文件登记专家发现的案例, 每例至少含: `case_id` / `patient_id` / `rule_id` (规则未落地时可为 TBD 占位) / `expected_verdict` (专家裁定的期望裁决, 允许 CLEAN — 误判修正锚) / `expected_evidence_keywords` (证据必须点名的要素) / `data_source` (csv|hub) / `attribution` (漏检层归因)。案例 MUST NOT 在专家未裁定 ground truth 时登记期望裁决。首批 MUST 收录 4 例专家案例 (211351896×溶栓 / 211427558×内镜治疗 / 211419211×R155 / 211318013×R225); 211440399×R063 保留 FN-004 编号待专家裁定后回填 (漂移报告将其送裁, 结果可能是 CLEAN 锚 — 专家已指出 1365=1820×75% 收费正确)。

#### Scenario: 211318013 anchor 案例登记

- **WHEN** 查看 `tests/fn_cases/` 目录
- **THEN** 存在 211318013×R225 案例文件, expected_verdict=VIOLATION, expected_evidence_keywords 含「关节松动训练」与「颈椎」, attribution 记录"规则粒度不足"

#### Scenario: 覆盖空白案例先于规则登记

- **WHEN** 某案例的违规形态尚无规则覆盖 (rule_id 为 TBD 占位)
- **THEN** 案例文件合法可登记, runner 将其判为 miss 并注明"无规则", 不报错

### Requirement: 回归 runner 产出三档 catch 率报告

系统 MUST 提供 FN 回归 runner (`scripts/fn_regression.py`), 逐案例以 dry-run 方式跑真实 audit (不落生产库), 按三档判定: **full** (verdict 达到期望严重度且证据关键词全命中) / **partial** (verdict 只达 INCONCLUSIVE, 或证据缺要素) / **miss** (判 CLEAN / 规则未跑 / 无规则)。报告 MUST 分列三档计数与总 catch 率 (catch = full + partial)。期望 V/I 的案例, verdict 断言 MUST 用严重度下限而非严格等值, 以容忍 LLM 漂移; 期望 CLEAN 的案例 (误判修正锚) 断言 MUST 为严格等值 — 实得 V 或 I 判 **miss** (假阳性复发), 不适用下限语义。

#### Scenario: 回归全跑出报告

- **WHEN** 执行 `fn_regression.py` 跑全部登记案例
- **THEN** 输出每例的档位 (full/partial/miss) + 实得 verdict + 缺失的证据关键词, 末尾汇总 catch 率

#### Scenario: hub 患者自动取数

- **WHEN** 某案例 `data_source: hub` 且患者不在本地 CSV
- **THEN** runner 自动经 `etl_from_data_hub` 拉取该患者到缓存目录后再跑, 不要求人工预先准备数据

### Requirement: 基线比对与退化拦截

系统 MUST 支持把某次回归结果存为基线 (`docs/fn_baseline.md`), 且 runner MUST 提供基线比对模式: 任一案例档位低于基线 (如 full→partial 或 partial→miss) 时 MUST 以非零 exit code 结束并列出跌档案例。

#### Scenario: 退化被暴露

- **WHEN** 某次代码/规则改动后, 曾 full 的案例跑出 miss, 以基线比对模式运行 runner
- **THEN** 报告将该案例列为跌档并以非零 exit code 结束 (可被批前检查拦截)
