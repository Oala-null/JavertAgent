# add-cross-patient-stats

## Why

医保飞检方法论 = 面上筛查 (跨患者大数据锁定系统性问题) + 点上核查 (病历审查落实证据). Javert 目前只有「点」——单 (rule, patient) 裁决. 2026-07 扫描指出: 163 条里 39 条 P3 标「单病历看不出」, 正是缺「面」; 且现有 106 患者 ~5000 裁决 (529V/238I) 基线已经躺在库里, 「V 率 ≥ 阈值提报系统性违规」的统计一分钱 LLM 不花就能产出——这是向院方交付时说服力最强的一页 (CLAUDE.md 路线图 🔥 候选, 解锁 R003/R280/R281/R286).

## What Changes

- **统计层**: 新 `stats/cross_patient.py`——按 rule_id 聚合 audit_runs (仅 is_latest 语义的最新裁决): 审计患者数 / V 数 / V 率 / I 率 / 涉及金额合计 (从 evidence 锚点回 join fee 行, 缺锚点则金额标「不可计」); sqlite 与 142 双源同一查询语义, 支持 batch_tag 过滤 (按批次/按院区看)
- **系统性违规判定**: `configs/systemic_thresholds.yaml`——V 率阈值 (默认 0.5) + 最小样本数 (默认 10 患者) 双条件, 产出 systemic 标记 + 一句话归因 (「N 患者中 M 例违规, V 率 x%」); 阈值可按规则覆盖
- **工作台面板**: dashboard 加「系统性违规」区块——规则维度列表 (V 率倒序 + systemic 徽标), 点击钻取到该规则全部 V 患者 (复用现有 patient_detail 链接); 只读展示, 不新增写路径
- **导出**: `/export` 加规则维度汇总 sheet (规则 / 患者数 / V 率 / 金额 / systemic)
- **规则解锁评估**: R003/R280/R281/R286 (现 abandoned, 归因「需跨患者比对」) 对照统计层能力逐条评估——能由统计直接判的 (如某收费项目全院异常高频) 转「统计规则」形态 (不进 LLM, 纯 SQL 判定 + 人工复核), 不能的留 abandoned 并更新 notes
- **CLI**: `javert stats [--batch-tag X] [--min-patients N]` 终端出同一份表, 现场演示不依赖工作台

## Capabilities

### New Capabilities
- `cross-patient-stats`: 规则维度跨患者聚合、系统性违规双阈值判定、批次/院区切片、统计规则形态 (非 LLM 裁决路径)

### Modified Capabilities
- （无——统计层只读 audit_runs, 不触碰 runner/router/gate 任何既有行为）

## Impact

- 代码: 新 `src/javert/stats/cross_patient.py` + `configs/systemic_thresholds.yaml` + CLI subcommand; `web/templates/dashboard.html` + `routes_workbench.py` dashboard 区块; `sqlserver_store` / `audit_store` 各加一个聚合查询 (注意 142 侧沿用 ROW_NUMBER 最新裁决口径, 与 `list_patients_with_violations` 同 CTE 语义, 勿再发明第四处 verdict filter)
- 数据前提: 裁决量越大统计越可信——建议在 szx2.0 批次跑完后上线面板, 首页数字即有说服力
- 口径风险: V 率的分母是「被审计患者」非「全院患者」, 面板与导出必须明示, 防止院方误读为发生率
- 不影响: 审计链路零改动, 失败可整体回退 (纯增量)
