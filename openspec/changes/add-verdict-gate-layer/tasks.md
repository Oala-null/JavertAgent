## 0. 前置

- [x] 0.1 确认 Change B (`fix-fee-refund-netting`) 已落地, `net_fees(patient).distinct_billing_dates` 可用 (② 单次闸硬依赖)

## 1. gate 配置与纯函数

- [x] 1.1 新建 `configs/verdict_gate.yaml`: `file_dependent_rules` 集 (影像 R103/R105/R112、麻醉 R203、监测 R224、虚构文书 R015、条码 R034…) + `single_instance_violation` 例外集 (女性 PSA R156…) + 病程 section 族 + 症状词默认表
- [x] 1.2 `src/javert/audit/verdict_gate.py` 纯函数 `apply_gate(verdict_data, rule, net_fee_ctx, gate_cfg) -> GateOutcome`
- [x] 1.3 ① 文件缺失闸: 文件依赖类 ∧ V 证据全 `etl_warning`/`ETL_GAP` ∧ 无正向佐证 → V→I + tag `缺文书`
- [x] 1.4 ② 单次闸: M2 派生 ∧ 按 `exam_kw_primary_list` 重算净不同收费次数 ≤1 ∧ 不在例外集 → V→C + tag `单次放过`; net 不可用 → fail-open
- [x] 1.5 conf 底线闸: 0.70 ≤ conf < 0.85 的 V → I + tag `低置信降级`
- [x] 1.6 单测: ① 仅缺失证据降 I / 有正向不降; ② 净次数 1 降 C / 全退 0 降 C / 例外穿透 V / net 缺失 fail-open; conf 0.80 V→I; gate=off 直通

## 2. runner 集成与落库

- [x] 2.1 `runner` 解析 verdict 后、构建 `AuditResult` 前调 `apply_gate`, 降级写 verdict + `gate_tag` + reasoning 追加「gate: <原因>」
- [x] 2.2 `JAVERT_VERDICT_GATE=off` feature flag 直通
- [x] 2.3 schema 加 `gate_tag` 列: `audit_runs` (sqlite) + `Javert_audit_runs` (mssql), 幂等 ALTER (`scripts/sql/create_javert_tables.sql` + `audit_store` migration)
- [x] 2.4 `audit_store` / `sqlserver_store` 读写 `gate_tag` (双写 + sync)

## 3. 病程指征扫描工具 (⑤)

- [x] 3.1 `src/javert/tools/scan_progress_indications.py`: 扫 section 族 (配置) 找 `symptom_kw_list`, 返回 `{子阶段, char_start, 摘录, 否认段?, 选项框?}` (复用 search_notes 标注逻辑)
- [x] 3.2 注册进 `src/javert/tools/registry.py` + base.txt 工具清单说明
- [x] 3.3 单测: 病情及处理命中胸闷 / 否认段标注 / 跨碎片 section 不漏

## 4. M2 模板改造 (②⑤)

- [x] 4.1 `configs/templates/M2.yaml`: `min_count` 默认 1 → 2; 新增 `symptom_kw_list` 字段
- [x] 4.2 master_prompt 增步: 判无指征前 MUST 调 `scan_progress_indications`, 命中症状 → CLEAN
- [x] 4.3 给高 V 心评/监测簇规则 (R131/R141/R143/R146/R155/R160/R161) 填 `symptom_kw_list` (复用 experience.md 共识 7 症状词), 重渲染
- [x] 4.4 `uv run javert template validate M2` + 抽 1 条 `prompt-fit --dry-run` 看渲染

## 5. 前端 (⑤ 锚定 + ① facet)

- [x] 5.1 `hit_resolver._classify_source`: lab/exam 不再返 None, surface 为可见命中项目 (项名+日期+数值)
- [x] 5.2 `patient_detail` 命中项目块展示 lab/exam 来源指征
- [x] 5.3 工作台 `缺文书` facet (默认隐藏 `gate_tag=缺文书`), 与 verdict filter 叠加; 详情页展示 gate_tag + 降级 reason
- [x] 5.4 (可选) dashboard 增「待线下核查」计数桶

## 6. 回归

- [x] 6.1 `uv run pytest tests/ -v` 全绿 (gate + 工具 + 模板单测)
- [x] 6.2 14 条高 V 规则重跑, diff: ① 缺文书项降 I+标签、② 单次项降 C、⑤ 病程症状能命中
- [x] 6.3 `J13365` R141/R146 复跑对齐 zhoulihong 的 C; `J22714` R131 对齐 (四级手术术前评估)
- [x] 6.4 抽查工作台: 缺文书默认隐藏、可翻看; lab/exam 指征可见可跳原文
