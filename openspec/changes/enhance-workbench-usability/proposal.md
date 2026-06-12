## Why

v0.6 工作台 (192.168.31.62:8090) 让专家能逐条审 106 病人 5016 条裁决, 但**审起来不顺手**:
病人列表只有"违规/不明"一个下拉, 卡片不告诉你这人花了多少钱、得的什么病, 想按 tag / 费用 / 主诊 / 更新时间找人得靠肉眼翻;
费用"按医保类别拆分"只给一张汇总表, 想看某类别下到底是哪些项目, 还得另开"查看原始病历" modal 翻明细;
违规卡里 AI 的推理是一大段散文, 专家想核"这条证据原文在哪", 没有从推理跳到原文的入口;
M8 药品违规卡只印一句通用规则描述 (如"超出说明书适应症"), 具体是哪个药、什么编码、限定支付条件是什么, 全埋在 reasoning 散文里;
其他专家的长评语被前端 `[:80]` 截断, 想看全文以为得查库。

这些都是**纯易用性**的坎, 数据其实都已落库或落文件, 缺的是把它们结构化地端到专家眼前。

## What Changes

- **病人列表 facet 筛选 + 信息卡片** (review-workbench): 卡片新增「大概多少钱 + 主诊断 + 相对更新时间」; 列表加 tag / 费用区间 / 主诊关键词 / 更新时间 四个**纯前端** facet, 与现有服务端"违规/不明"过滤叠加。
- **医保类别就地展开明细** (review-workbench): `_patient_overview.html` 的类别表改成手风琴, 点类别行就地展开该类别下的费用明细 (编码·名称·次数·金额), 不必再开 modal。
- **违规推理 → 原文同步面板** (review-workbench + evidence-anchoring): 违规卡里每条证据变可点; 点击 → 右侧常驻「原文同步」面板 (Cursor 式), 自动切到对的 tab (文书/费用)、滚动定位、黄标高亮命中片段。
- **命中项目展示** (review-workbench + evidence-anchoring): 违规卡规则描述下方新增「命中项目」块, 印 `项目编码 · 项目名称`; 若是限定药规则 (M8, `drug_rule_type` 非空), 顺带印该药的「限定内容」(医保限定支付原文)。
- **长评语 hover 看全文** (review-workbench): 其他专家评语截断处加原生 tooltip / CSS 悬浮框, 全文本就已在页面, 零数据库往返。
- **命中项目 / 锚点解析器** (evidence-anchoring, 新): 一个确定性 (非 LLM) 后处理器, 把一条违规的 `evidence` + `tool_calls` 解析成统一的「命中项目[]」结构 —— 每条带 `{项目编码, 项目名称, 限定内容?, 原文锚点}`。`#3 跳转`与`#5 展示`共用此结构。编码 join `shi_fee` (`med_list_codg` / `medins_list_codg` / `medins_list_name`), 限定内容 join `configs/drug_audit_kb.json` (`basis`), 锚点用 `tool_calls` 里 LLM 实搜的精确 keyword (保证是原文子串) 回 grep 文书/费用得 `(tab, 子阶段, 字符偏移)`。
- **解析器落地策略**: 渲染时现算 (对全部 106 病人**即时生效**, 零 schema 迁移) 为主; 提供 `scripts/backfill_anchors.py` **trace 回放**脚本把结构化锚点回写持久化作缓存 (零 GPU, 不重跑 LLM, 不动专家批注); `search_fees` / `search_notes` 顺手吐 locator 偏移, 让歧义命中也能精准 (前向增量)。

## Capabilities

### New Capabilities

- `evidence-anchoring`: 把已落库的 `evidence_json` + `tool_calls_json` + `shi_fee` 编码 + `drug_audit_kb` 限定原文, 确定性解析成统一「命中项目[]」结构 (`{编码, 名称, 限定内容?, 原文锚点}`); 同时支撑违规卡的命中项目展示与点击跳原文。含渲染时解析、trace 回放回填脚本、re-grep 兜底三层。

### Modified Capabilities

- `review-workbench`: 病人列表加 4 个前端 facet + 富信息卡片; 费用类别就地展开明细; 违规卡接命中项目块 + 证据可点 + 右侧原文同步面板; 长评语 hover 全文。

## Impact

- **代码 (前端为主, 不碰 LLM 审计管道)**:
  - 新 `src/javert/web/hit_resolver.py` (命中项目 / 锚点解析器, 确定性)
  - 新 `scripts/backfill_anchors.py` (trace 回放回填, 可选缓存)
  - 改 `patient_overview.py` (费用按类别分组挂明细 + 卡片摘要 `fees_sum`/`primary_dx` 开机 groupby 缓存)
  - 改 `store/sqlserver_store.py` `list_patients_with_violations` (加 `MAX(created_at)` updated_at) + `store/models.py` `PatientSidebarItem` (加 `fees_sum`/`primary_dx`/`updated_at`)
  - 改 `routes_workbench.py` (patient_detail 注入命中项目 + 卡片摘要喂 sidebar)
  - 改模板 `_sidebar.html` (facet UI + 卡片字段) / `_patient_overview.html` (类别手风琴) / `patient_detail.html` (命中项目块 + 证据可点 + 评语 title)
  - 改 `static/app.js` (facet 过滤 + 右侧同步面板 + 点击跳转, 复用现有高亮引擎) / `static/style.css` (facet 栏 + 同步面板 + 命中项目块样式)
  - 可能改 `tools/search_fees.py` / `tools/search_notes.py` (输出 locator 偏移) + `audit/result.py` `Evidence` (可选 `anchor` 字段, 前向)
- **数据 / 配置**: 复用现有 `data/shi_fee.csv` (编码列已在) + `configs/drug_audit_kb.json` (限定原文已在), **无新数据资产**。
- **DB**: 渲染时解析路径**零 `Javert_audit_runs` schema 变更**; 回放回填若启用, 写入既有行的派生字段缓存 (同 run_id, 不增删行, 专家批注不受影响)。
- **跨机**: 无新跨机依赖; 解析器纯本地确定性计算, 不占 GPU。
