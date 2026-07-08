# pilot-deterministic-precheck

## Why

2026-07 扫描主线二: 当前防线结构是「Router 预筛 → LLM 全程自由探索 → gate 补丁式后筛」, 症状是 verdict_gate 已长出 8 道闸 + 7 个 rule_id allowlist 且开始互相打架 (R205 被存在性闸误清 / R156 挂两个语义相反的闸), conf 闸依赖 LLM 自报置信度——复扫确认其与「模型恒输出 0.90」的偶然性耦合, 换模型即有批量压制真阳性的风险. 根因: 可确定性判定的事实 (费用并存/次数/净额/日期) 被交给 LLM 自由探索, 再事后打补丁.

M1 重复收费的判定 = 「主项 A ∩ 附属 B 并存 + 文书无反证」——前半是一条查询能确定的事实, 只有「文书无反证」真正需要 NLU. 选 M1 (22 条 ready) 试点分层, 验证后再推 M2/M4 (同形态, 合计 72 条).

## What Changes

- **precheck 层**: 新 `audit/precheck.py`——复用 `fee_netting` / `clinical_context` 既有确定性代码, 对 M1 规则从 yaml 声明的 A/B 项目集产出结构化事实: A 命中行 (锚点)、B 命中行 (锚点)、净次数、净额、同日并存日期列表
- **事实短路**: A∩B 不并存 → 直接落 CLEAN (新 `precheck_tag` 标签, 与 gate_tag 同风格可解释), **不进 LLM**——预期砍掉 M1 大部分 LLM 调用且判定 100% 可复核
- **LLM 窄问题化**: 事实成立才进 LLM; `initial_user_message` 注入 precheck 事实块, M1 master_prompt 改为「基于已给定的费用事实, 用文书工具核实是否存在反证 (分次手术/两次医嘱/特殊说明)」——LLM 不再自己搜费用, 只答反证一个窄问题; M1.yaml 重渲 22 条
- **证据可机器复核**: verdict evidence 必含 precheck 给出的费用行锚点 (结构化), 工作台命中项目块直接消费, 不再依赖 LLM 自由文本引用
- **gate 瘦身评估**: ②单次闸对 M1 规则由 precheck 取代 (次数已在事实里, 事实层拦截优于裁决后改判); 评估③conf 闸对 precheck 规则的必要性——事实确定性越高, 对自报置信度的依赖应越低
- **对照验证**: 106 患者历史基线重跑 M1 22 条, 三指标: V/I/C 分布对照 (历史确认 V 0 漏检为硬门槛)、LLM 调用数与 token 降幅、专家抽查新 V 的证据可复核率

## Capabilities

### New Capabilities
- `deterministic-precheck`: 模板级确定性事实提取——事实不成立短路 CLEAN、事实成立注入窄问题、事实自带机器可复核锚点

### Modified Capabilities
- `audit-engine`: M1 规则的执行路径变为 precheck → (短路 | LLM 窄问题) → gate
- `rule-templating`: M1 模板 master_prompt 窄问题化 + yaml 声明 A/B 项目集为 precheck 可读的结构化字段

## Impact

- 代码: 新 `src/javert/audit/precheck.py`; `runner.py` / `audit_patient.py` 入口接线; `configs/templates/M1.yaml` + 22 条规则 yaml 重渲 (A/B 集从 prompt 文本提升为结构化字段); 工作台 hit_resolver 消费 precheck 锚点 (增量, 兼容旧 anchors_json)
- 数据口径: M1 的 C 大量变为「precheck 短路 CLEAN」(带标签), 向专家/院方汇报时说明口径; V 数预期变化以对照为准
- 风险: A/B 项目集关键词从 prompt 挪到结构化字段, 沿用现有字面名匹配——换院鲁棒性由 make-rules-code-portable 解决, 两 change 正交但共享「结构化字段」地基, 建议本 change 先行
- 成功判据: 历史 V 0 漏检 + M1 LLM 调用数 ≥50% 下降 + 新增 V 证据 100% 带机器锚点; 达标后再起 M2/M4 推广 change
