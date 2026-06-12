## Why

PM 实测反馈: 专家审核时违规卡片暴露了 agent 的查询过程 (去哪 grep、搜了什么 keyword、整段 reasoning + evidence_json 裸 dump), 这些信息对裁决无用且最 confusing; 命中项目有时跳不到收费明细 (如 J94935 R007「重组人血」); 病人摘要把主诉/现病史/既往史堆在最前面分散注意力; 原文对照里几百条文书无序平铺, 消费表时间列藏在末尾。本次把工作台病人详情页按"专家审核动线"重排, 让注意力一开始就落在"命中了什么 + 是哪类违规"上。

## What Changes

- **病人摘要瘦身**: 删除概览块里的主诉 / 现病史 / 既往史三段 (诊断/手术/费用结构保留)。原文 parallel pop-up 已能让专家自查, 注意力不应起于此。
- **违规卡片简化**: 卡片正文只保留「命中项目」(仅 `source ∈ {fee,drug}`) + 一个「查阅文书原文」按钮; agent 的 reasoning 文本与 evidence_json 裸 dump 折叠进「证据」`<details>` (保留留痕, 默认收起)。note 类来源不再单列, 收敛成那个按钮 (点开 = 文书 tab 最顶部)。
- **命中项目必可跳转 (模糊)**: 跳转锚点与编码富集解耦 —— 即使该命中名匹不到患者 fee 行 (拿不到国家码), 仍以命中名作为 `query` 让费用 tab 做子串高亮。修复 J94935 R007「重组人血」跳不动。编码显示保持严格 (无码不显码)。
- **原文对照文书排序 + 分组**: `/raw` 端点给每条文书算一个临床文书序桶 (病案首页 → 入院记录 → 病程记录 → 手术记录 → 知情书 → 出院小结 …), 前端按桶分组渲染可折叠标题段; 同桶内按事件时间排。
- **消费表时间列前置 + 统一格式**: 所有费用表 (概览 + 原文对照 + 原始病历 modal) 把 `时间` 列挪到第一列, 统一 `YYYY/MM/DD`。
- **AI 推理版面顶部 tag 导航**: 病人详情正文按 `violation_type` (细类) 分组、可折叠, 组内违规(V)排在不明(I)前; 顶部加 sticky chip 行 (每细类一 chip, 带 `V▪I` 子计数 + 短别名) 点击滚到该组; 加「只看不明」轻量前端 toggle (与现有 server 端 verdict filter 正交)。

## Capabilities

### New Capabilities
<!-- 无新增 capability — 全部落在既有 review-workbench / evidence-anchoring 内 -->

### Modified Capabilities
- `review-workbench`: 病人详情页布局变更 —— 概览删三段病史; 违规卡只留命中项目+查文书按钮+折叠证据; 正文按细类分组折叠 + 顶部 tag 导航 chip + 只看不明 toggle; 文书原文对照按临床文书序分桶分组; 所有费用表时间列前置并统一 `YYYY/MM/DD`。
- `evidence-anchoring`: 费用/药品命中项目的跳转锚点与编码富集解耦 —— 无 fee 行匹配时锚点不再降级为 `tab`-only, 而是以命中名作为子串 `query` 永远可高亮跳转; 编码富集逻辑不变。

## Impact

- **代码 (仅 `src/javert/web/`)**:
  - `templates/_patient_overview.html` — 删主诉/现病史/既往史; 费用表列重排。
  - `templates/patient_detail.html` — 卡片重构 (命中项目过滤 source、查文书按钮、reasoning/evidence 折叠)、细类分组 section + 顶部 tag chip 行。
  - `static/app.js` — `_feesPanelHtml` 列序+日期格式; `_notesPanelHtml` 按桶分组折叠渲染; 查文书按钮 (notes tab 顶); tag chip 滚动导航 + 只看不明 toggle。
  - `api/routes_workbench.py` — `/raw` 端点给文书加桶字段; route 里按细类分组 + V前I后排序传给模板。
  - `web/hit_resolver.py` — `_resolve_fee_anchor` 无匹配时改用命中名作 query (解耦)。
  - 新增 `阶段 → 文书桶` 关键词映射表 + `violation_type → 短别名` 表 (web 层小工具)。
- **缓存**: 改 `hit_resolver` 后 142 上 `anchors_json` 旧锚点过期 —— 需重跑 `scripts/backfill_anchors.py`; 现算回退路径保证功能不挂。
- **测试**: `tests/` 中针对 `hit_resolver` 锚点降级的断言需更新; 新增文书分桶 / tag 分组的测试。
- **零改动**: 审计引擎、数据层、SQL schema、规则 yaml 均不动。
