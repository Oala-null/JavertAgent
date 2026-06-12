## Why

本地 3724 条裁决里 **433 条 V, 其中 ~85% 落在 14 条规则上 (R103/R141/R131/R146/R161/R203/R153…), 全部是专家已判「过度认定」的那几条**。但 `runner.py:288-301` 把 LLM 的 verdict **原样落库** —— 连 base.txt 写的「conf<0.85 的 V 改判 I」都只是 prompt 一句话, **代码层没有任何硬闸**。所有「降误报」都是软的 (experience.md / prompt / yaml triggers), 所以拿不到用户要的「百分百」「全部不要标违规」「只有一次就给过」。

三个判断可靠性缺口需要确定性手段:
- **① 文件缺失** (影像/麻醉记录/报告单/条码 缺失) 漏出大量 V (R103=46, R203=29, R112=9, R224=19), 专家一致判 I「待线下核查」(45 条批注最大主题)。
- **② 单次就给过**: `M2.yaml` 模板 `min_count` 默认 = 1 → 单次检查直接 V。专家「检查一次不被认定为过度」。
- **⑤ 搜索范围不够**: M2 模板 step4 **只读 `note_diagnosis`**, 而指征症状散在 `病程记录内容`/`病情及处理` (ETL 把文书劈碎, 无干净「日常查房记录」section)。zhoulihong 重复最多的话:「该规则只查阅了诊断, 建议增加日常查房记录文书类的检索」。前端 `hit_resolver` 还**静默丢弃 lab/exam 证据** → 指征查到也跳不到原文。

## What Changes

- **新增确定性 gate 层** `src/javert/audit/verdict_gate.py` (纯函数), `runner` 在解析完 verdict 后、落库前调用:
  - **① 文件缺失硬闸**: V 的支撑是「某文件缺失」(证据全为 `etl_warning` / `ETL_GAP`、无任何正向 note/fee/exam/lab 佐证) 且规则属文件依赖类 → 降 V→INCONCLUSIVE + 打**独立标签 `缺文书`**。
  - **② 单次硬闸**: M2 派生 (`derived_from_template==M2`) 过度检查规则, 该检查**净不同收费次数 ≤ 1** (用 Change B 的 `net_fees` 真值) 且不在 `single_instance_violation` 例外集 (如女性查 PSA) → 降 V→CLEAN。
  - **conf 底线硬闸**: `0.70 ≤ conf < 0.85` 的 V → INCONCLUSIVE (把 base.txt 的软规则变硬)。
- **新标签 `缺文书`** 落库 (audit_runs sqlite + Javert_audit_runs mssql 增 `gate_tag` 列), 工作台 facet **默认隐藏** (用户决定: 展示时不看这些噪音)。
- **新确定性工具 `scan_progress_indications`** (⑤): 扫病程/查房 section 族 (病程记录内容/病情及处理/诊疗经过/目前情况/入院情况/简要病情/首次病程) 找症状词, 返回带 `子阶段 + char 偏移`的命中 (复用 search_notes 的否认段/选项框标注)。
- **M2 模板改造** (②⑤): `min_count` 默认 2; master_prompt 在判「无指征」前**强制** `scan_progress_indications` 扫该检查的症状词 (新增 `symptom_kw_list` 字段), 命中即 CLEAN。
- **前端放开 lab/exam 证据锚定** (⑤): `hit_resolver._classify_source` 不再静默丢 lab/exam, surface 为可见命中项目 (项名+日期)。
- gate 分类用配置 `configs/verdict_gate.yaml` (文件依赖规则集 + `single_instance_violation` 例外集), 不改 Rule schema。

## Capabilities

### New Capabilities

- `verdict-gate`: 裁决后确定性闸层 —— 文件缺失降级 + 缺文书标签 / 单次降级 + 例外穿透 / conf 底线硬闸; 配置驱动分类; 标签落库。
- `indication-search`: 确定性病程/查房指征扫描工具 (扫 section 族 + 症状词表 → 带锚点命中)。

### Modified Capabilities

- `rule-templating`: M2 过度检查模板 `min_count` 默认 2 + 判无指征前强制症状检索 (新增 `symptom_kw_list` 字段)。
- `evidence-anchoring`: `hit_resolver` 放开 lab/exam 证据, 不再静默丢弃, surface 为可见命中项目。
- `review-workbench`: 工作台新增 `缺文书` facet (默认隐藏被 gate 降级的缺文书项)。

## Impact

- **代码**: 新 `src/javert/audit/verdict_gate.py` + `runner.py` 集成钩子; 新 `src/javert/tools/scan_progress_indications.py` + `registry.py` 注册; `configs/templates/M2.yaml` (min_count + symptom 步骤); `src/javert/web/hit_resolver.py` (lab/exam); `src/javert/web/api/routes_workbench.py` + `_sidebar.html` + `app.js` (缺文书 facet); `src/javert/store/{audit_store,sqlserver_store}.py` (gate_tag 读写)。
- **数据/配置**: 新 `configs/verdict_gate.yaml`; M2 派生规则重渲染 (`prompt-fit` / `init`); 部分规则补 `single_instance_violation` 例外标注。
- **schema**: `audit_runs` (sqlite) + `Javert_audit_runs` (mssql) 增 `gate_tag` 列 (幂等 ALTER)。
- **依赖**: **② 单次门控依赖 Change B (`fix-fee-refund-netting`) 的 `net_fees`** —— 须 Change B 先落地。Change A 正交。
- **验证**: 14 条高 V 规则重跑, 确认 ① 缺文书项降为 I+标签、② 单次项降为 C、⑤ 症状检索能命中病程指征; `J13365` R141/R146 (zhoulihong 判 C) 复跑对齐专家。
