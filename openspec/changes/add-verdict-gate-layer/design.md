## Context

现状所有「降误报」都是软的: `experience.md` 共识 + base.txt 原则 + yaml `📌 专家共识触发器`, 全在裁决**前**引导 LLM。`runner.py` 解析完 verdict JSON 后**原样落库** (288-301 行), 无任何后置校验, 连「conf<0.85 V→I」都只是 prompt 文字。

实测 (本地 `output/audit.sqlite`, 3724 裁决):
- V 433 / I 202 / C 3089。产 V 最多 14 条规则 ≈ 370 V (~85%), 全是专家批注里被判「过度认定」的: R103/R141/R131/R146/R161/R143/R203/R153/R224/R154/R220/R155/R112/R160。
- ① 文件缺失簇: R103(46,影像)+R203(29,麻醉记录)+R224(19,监测)+R112(9,影像) ≈ 103 V。专家 (wangxin/jiweihui/zhoulihong) 一致判 I「待线下核查」。
- ② 过度检查簇: R141+R131+R146+R143+R153+R154+R155 ≈ 206 V, 大量 count==1。`M2.yaml` `min_count` 默认 1 → 单次即 V。
- ⑤ 病程检索: M2 step4 只读 `note_diagnosis`; 病程内容散在 `病程记录内容`(跨 40 患者 10247 字符)+`病情及处理`(7736 字符) 等 6 个 section, 无干净「日常查房记录」。`hit_resolver._classify_source` (77-87) 对 lab/exam/etl_warning 返回 None → 静默丢, 指征跳不到原文。

## Goals / Non-Goals

**Goals:**
- 引入**确定性后置 gate**, 让 ①② 的降误报从「概率性 prompt」变成「代码保证」。
- ① 文件缺失 V → I + `缺文书` 标签, 工作台默认隐藏 (用户: 展示时不看噪音)。
- ② M2 过度检查净次数 ≤1 → CLEAN, 例外集穿透。
- ⑤ 审计真去搜病程/查房症状 (新工具 + M2 模板), 并让前端能锚到 lab/exam 证据。

**Non-Goals:**
- 不替换软层 (experience.md/triggers 仍是第一道防线, 负责降 V 生成量; gate 只兜底保证)。
- gate **不**新造指征 (⑤ 的「补搜」靠工具+模板, 不靠 gate)。
- 不动 Change A (药品码) / 不在 gate 里做退费净额 (用 Change B 的 `net_fees`)。
- 不重写 ETL 把碎片 section 合并 (本期靠跨 section 关键词扫, 不重建索引)。

## Decisions

- **D1 gate 是裁决后纯函数, 在 runner 落库前调用**。`verdict_gate.apply_gate(verdict_data, rule, net_fee_ctx, gate_cfg) -> GateOutcome{verdict, tag, reason}`。
  - *Why*: 单点、可单测、可解释 (每次降级写 `reason` 进 reasoning/审计)。*Alt*: 在 prompt 里继续加压 (已证不够硬) / 在 ConfidenceFilter 式独立 pass (Javert 无该层, 新建即此 gate)。

- **D2 ① 文件缺失判定 = 规则属文件依赖类 ∧ V 证据「仅缺失」**。文件依赖类用 `configs/verdict_gate.yaml` 列 (影像 R103/R105/R112、麻醉记录 R203、监测 R224、虚构文书 R015、条码 R034…); 「仅缺失」= V 的 evidence 全部 `source∈{etl_warning}` 或 locator 含 `ETL_GAP`, 且无任何正向 note/fee/exam/lab 佐证。命中 → V→I + tag `缺文书`。
  - *Why*: 系统从单病历**分不清**「没数字化」vs「服务没发生」, I「线下核查」是诚实裁决 (= 专家行为)。配置驱动不改 Rule schema。*Alt*: 给 Rule 加 `evidence_class` 字段 (改 rule-registry, 牵连面大) —— 不取。

- **D3 ② 单次判定 = M2 派生 ∧ 该检查净不同收费次数 ≤1 ∧ 不在例外集**。次数用 Change B `net_fees(patient).distinct_billing_dates`, 按规则 `exam_kw_primary_list` 关键词匹配该检查的费用行 (确定性重算, 不信 LLM 自报的 count)。例外集 `single_instance_violation` (女性查 PSA R156 等「单次本身即错」) 在 `verdict_gate.yaml` 列, **穿透**次数闸仍 V。命中 → V→CLEAN。
  - *Why*: 地佐辛式案例证明必须先净退费再数日期 (5 日期但净 0); 例外集保留「单次即违规」的真违规。*Alt*: 信 LLM evidence 里的 count (不稳, 含退费虚高) —— 不取。

- **D4 conf 底线硬闸**: `0.70 ≤ conf < 0.85` 的 V → I (把 base.txt 软规则代码化)。conf < 0.70 的 V 本就该被 STEP1 拦, 这里兜底。
  - *Why*: 几乎零成本的一致性收口。

- **D5 `缺文书` 是标签不是第四种 verdict**。verdict 仍是 INCONCLUSIVE (三态不变), `gate_tag` 列额外记 `缺文书`/`单次放过`/`低置信降级`/``。
  - *Why*: 不破坏三态契约与既有统计; 前端按 tag facet 过滤即可。

- **D6 ⑤ 新工具 `scan_progress_indications(patient_id, symptom_kw_list)`** 扫病程/查房 section 族 (配置该族 section 名), 返回 `{子阶段, char_start, 摘录, 否认段?, 选项框?}`。M2 模板在判无指征前**强制**调它 (新 `symptom_kw_list` 字段供每规则填该检查的症状词)。命中具体症状 → CLEAN。
  - *Why*: search_notes keyword 本可扫全文, 但 M2 流程从不调; 做成专用确定性工具 + 模板强制步, 既补搜又把命中症状词写进 evidence (前端 level-2 keyword 锚点即点亮)。**修审计搜索面 = 同时修假 V 与前端跳不到原文**。

- **D7 前端放开 lab/exam 证据**: `_classify_source` 给 lab/exam 返回可锚 kind (至少 surface 为可见命中项目: 项名+日期+数值), 不再 None 丢弃。
  - *Why*: 指征常来自 `search_lab_results`/`search_examinations`; 专家要能看到。

## Risks / Trade-offs

- [① 过度抑制: 真虚构服务 (R015) 也被降为 I] → 这正是专家行为 (「未见…→I, 线下核查」); 真虚构靠跨患者/线下, 单病历本就该 I。例外: 反复多次 + 零任何痕迹仍可留 V (gate 只在「仅缺失且属文件依赖类」时降)。
- [② 次数闸放过真违规] → 例外集 `single_instance_violation` 穿透; 且仅作用 M2 派生过度检查类, 不碰其他模板。
- [② 依赖 Change B 未落地] → 硬依赖, tasks 标明顺序; 若 `net_fees` 缺失, 次数闸 fail-open (不降级, 保持原 verdict) 而非误降。
- [gate 误降级不可见] → 每次降级写 `gate_tag` + `reason`「gate: ①缺文书/②单次」, 工作台可筛出 gate 改判项审计; 专家可在 facet 里翻看被隐藏的缺文书。
- [⑤ 症状词表逐规则填, 工作量] → 复用 experience.md 共识 7 已列的症状词 (胸闷/气短/喘/憋/下肢水肿…); 优先填心评/监测簇高 V 规则 (R131/R141/R143/R146)。
- [病程 section 族跨患者命名不一] → `verdict_gate.yaml`/工具配置列全族 section 名, 全量扫一遍补全 (本期先覆盖实测 6 个)。

## Migration Plan

1. (前置) Change B `net_fees` 已落地。
2. `verdict_gate.py` 纯函数 + `configs/verdict_gate.yaml` (文件依赖集 + 例外集) + 单测 (① 仅 etl_warning 证据降 I; ② 净次数 1 降 C; 例外穿透; conf 0.8 V→I)。
3. `runner` 集成 gate (落库前), 降级写 `gate_tag` + reason。
4. schema 加 `gate_tag` 列 (sqlite + mssql 幂等 ALTER); store 读写 tag。
5. `scan_progress_indications` 工具 + registry 注册 + 单测。
6. M2 模板 `min_count=2` + symptom 步骤 + `symptom_kw_list` 字段; 重渲染高 V M2 规则。
7. `hit_resolver` 放开 lab/exam; 工作台 `缺文书` facet (默认隐藏)。
8. 回归: 14 条高 V 规则重跑 diff; `J13365` R141/R146 对齐 zhoulihong C; `uv run pytest tests/ -v` 全绿。
- **回滚**: gate 集成点 feature flag (`JAVERT_VERDICT_GATE=off` 直通原 verdict); `gate_tag` 列保留不读即可。

## Open Questions

- `缺文书` facet 默认隐藏, 但要不要在 dashboard 单列一个「待线下核查」计数桶 (让缺文书可量化而非纯隐藏)? → 倾向加, 属 review-workbench 小增量。
- 症状词表后续是否由 `refresh_experience.py` (experience.md §5 TBD 的反馈回路) 从专家批注自动产出? → 本期手填, 自动化留作 `add-expert-feedback-loop` 候选 change。
- ② 次数闸的「不同日期」是否要再细到「不同医嘱」粒度 (同日两次合理 vs 拆分)? → 本期按不同日期保守, 同日合理两次不误降。
