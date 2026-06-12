## Context

工作台病人详情页 (`/workbench/{pid}`) 现状: 概览块 (`_patient_overview.html`) 把诊断/手术/费用 + 主诉/现病史/既往史一起摊开; 违规卡片 (`patient_detail.html`) 渲染 rule 字幕、命中项目 (含 note 来源)、整段 `reasoning`、`evidence_json` 裸 dump; 原文对照 (modal + 右侧 `openSourcePanel`) 共用 `app.js` 的 `_notesPanelHtml`/`_feesPanelHtml`, 文书按 DataFrame 行序平铺, 费用时间列在末尾。命中项目跳转依赖 `hit_resolver.resolve_hits` 产出的 `anchor`; 当编码 stem 匹配失败时锚点降级为 `tab`-only, 跳转只切 tab 不高亮 (J94935 R007「重组人血」即此路径)。

约束: 全中文开发; 改动只碰 `src/javert/web/`, 不动审计引擎/数据层/SQL schema/规则 yaml; 142 上有 `anchors_json` 缓存 (确定性回填); 333 测试需保持绿。这是纯前端动线重排, 不是新能力。

## Goals / Non-Goals

**Goals:**
- 专家打开病人页第一眼落在"命中了什么 + 哪类违规", 而非 agent 查询过程。
- 命中项目永远能跳到收费明细 (模糊高亮), 不再有跳不动的死项。
- 原文对照按临床文书序分组折叠, 消费表时间列前置统一格式, 提升可读性。
- 顶部按细类 tag 导航 + 只看不明, 在多命中病人页快速定位。

**Non-Goals:**
- 不改审计引擎、不重跑推理、不改 verdict 逻辑。
- 不改 `evidence-anchoring` 的编码富集 / 限定富集 / note 锚点阶梯 (只改 fee 无匹配时的降级行为)。
- 不引入新依赖、新 SQL 列 (anchors_json 已存在, 复用)。
- 不做病案首页/治疗单/体温单等 case_notes 里本就没有的文书类型 (数据缺位, 只排现有的)。

## Decisions

### D1. 命中项目: 跳转锚点与编码富集解耦 (修复模糊跳转)

`hit_resolver` 现在把"能不能跳"绑死在"有没有匹到带码的 fee 行"。两件事其实独立:
- **编码显示** 需要 join 患者 fee 行拿 `med_list_codg` —— 保持严格 (无码不显码)。
- **跳转高亮** 只需在费用 tab 里子串高亮一个 query —— 前端 `runHighlight` 本就是 lowercase `indexOf` 子串匹配 ("模糊")。

改 `_resolve_fee_anchor`: 无 fee 行匹配时, 不再返回 `unresolved=True, query=""`, 而是以**命中名 (或其 stem)** 作 `query`, `match_level="name"`。这样「重组人血」当 query 丢进费用 tab, 任何含这四字的行都高亮。

**备选**: ① 放宽 `_match_fee_rows` 的 stem 算法 (token 重叠 / 前缀) —— 但会污染编码富集 (可能匹错码), 且调匹配算法风险扩散。② 只在前端兜底搜索 —— 后端锚点仍 unresolved, 语义不一致, 且绕过了确定性 `hits_to_json` 缓存。选解耦: 改动最小、语义清晰、码富集不受影响。

### D2. anchors_json 缓存失效处理

D1 改了锚点产物, 142 上回填的 `anchors_json` 变旧。`routes_workbench._resolve_hits_for_runs` 已有 "缓存 miss → 现算" 回退, 所以功能不挂, 但缓存命中会给旧锚点 (旧 unresolved)。处理: 部署后重跑 `scripts/backfill_anchors.py --target mssql` 覆盖。`hits_to_json` 确定性不变, 幂等安全。

### D3. 文书分桶排序放后端 `/raw` 端点

排序逻辑放 `_notes_to_list` (而非前端), 给每条文书加 `bucket` (桶名, 用于分组标题) + `bucket_order` (排序键)。理由: modal 与右侧对照面板共用同一 JSON, 一处改两处生效; 前端只管按 `bucket` 渲染折叠段。

桶映射是一张 `阶段关键词 → (桶名, 序)` 表 (新增 `web/doc_order.py` 之类小工具)。因为 `阶段` 有几百种 (大量是「某某医师首次查房记录」), 用**关键词/正则归桶**而非精确枚举:

| 序 | 桶 | 归桶关键词 (命中即入) |
|----|----|----|
| 1 | 病案首页 | `病案首页` `康复病例首页` |
| 2 | 入院记录 | `入院记录` `入出院记录` `转入记录` |
| 3 | 病程记录 | `查房` `病程` `术前讨论` `术前小结` `术后` `疑难病例` `Tumor Board` `多学科讨论` |
| 4 | 手术记录 | `手术记录` `操作记录` `PICC` |
| 5 | 知情书 | `同意书` `告知书` `知情` `志愿书` `委托书` `证明书` `核查表` `评估单` `评估表` `申请报告` |
| 6 | 出院小结 | `出院小结` `出院记录` `疾病证明` |
| 99 | 其他 | (未命中) |

归桶顺序按表从上到下首次命中 (避免「术前讨论同意书」误入知情书 —— 但实际「讨论」先于「同意书」命中, 需把更具体的关键词放前/做优先级)。组内按 `事件时间` 升序。

**备选**: 用 `子阶段` 细分 —— 但 `阶段` 已是文书类型粒度, 够用; `子阶段` 留作组内内容。

### D4. 违规卡片重构 (命中项目 / 查文书按钮 / 折叠证据)

- 命中项目块只渲 `h.source in {fee, drug}` (note 类过滤掉)。
- note 类来源 → 一个「📄 查阅文书原文」按钮, 点 `openSourcePanel({tab:'notes'})` 定位文书 tab 顶部 (复用现有面板, 新增 "无 anchor 时滚到顶" 行为)。
- `reasoning` + `evidence_json` → 移进一个 `<details class="evidence-block">` (默认收起, 保留)。
- 卡片字幕从 `临床检验.过度检查.P0.模板M2` 简化为人话 (细类别名), 因为正文已按细类分组, 字幕不必再背模板 code。

### D5. tag 导航: 细类分组 + chip + 只看不明

数据发现 (explore 阶段): `重复收费` 横跨 M1/M4/M7, 模板不是细类干净父节点; 且 M-code 对专家是黑话。所以**用 `violation_type` (细类) 当分组轴, 抹掉模板层**。

- route 里把 `runs` 按 `violation_type` 分组, 组内 V 排在 I 前, 传 `run_groups` (列表: `{vt, alias, n_v, n_i, runs}`) 给模板。
- 模板渲染: 顶部 sticky chip 行 (每组一 chip: `别名 n_v▪n_i`, 点击 `scrollIntoView` 到组锚点) + 正文每组一个可折叠 section 标题。
- chip 标签用 `violation_type → 短别名` 表 (原值有整句, 如 `虚构医药服务项目或以骗保为目的串换项目` → `虚构/串换`)。
- 「只看不明」是前端 toggle: 勾上后隐藏所有 V 卡片 (纯 CSS class), 与现有 server 端 `v_and_i/i_only` filter 正交并存。

**裁决轴 vs 类型轴正交**: 一条 run 同时有细类 + verdict。不把"不明"做成独立分组桶 (否则 `过度检查` 的不明项会被劈出该组, 破坏"看齐某细类全部"的导航)。改为: 分组按细类、组内 V/I 排序 + chip 子计数 + toggle 过滤。

### D6. 消费表列序 + 日期格式

`fee_ocur_time` 原值为 `dd/mm/yyyy [hh:mm:ss]` (见 `patient_overview.py` 解析注释)。统一: 解析后输出 `YYYY/MM/DD`, 列挪到第一列。落点: 后端 `/raw` 的 `_fees_to_list` 预格式化一个 `fee_date` 字段 (前端不再各自 parse), 前端 `_feesPanelHtml` 把时间列放表头第一列; 概览 `_patient_overview.html` 费用类别/项目表同步 (这些表目前无时间列, 仅明细模糊 —— 概览表保持聚合不强加时间列, 仅原文对照/明细表前置)。

## Risks / Trade-offs

- [文书归桶关键词冲突, 如「术前讨论」含的同意书误归] → 关键词表带优先级 (病程关键词先于知情书命中); 兜底「其他」桶不丢行; 加单测覆盖几个易混 阶段。
- [anchors_json 旧缓存给旧锚点 (跳不动复发)] → 部署后必跑 backfill; 现算回退保证最坏情况也只是慢不是错; backfill 幂等可重跑。
- [模糊 query 高亮误命中过多 (如「钠」匹一堆行)] → query 用完整命中名 (通常 ≥4 字, 特异性够); 多命中由专家在费用 tab 自行翻 (高亮带计数 N/M)。
- [删病史三段被某些审核场景需要] → 原文 pop-up 仍可查全文 (入院记录桶里有主诉/现病史/既往史); 概览只是不再前置, 信息无丢失。
- [hit_resolver 改锚点降级使既有测试断言失败] → 同 PR 更新断言 (unresolved 场景改为带 name query); 这是预期变更, 非回归。

## Migration Plan

1. 改 `src/javert/web/` (模板 + app.js + routes + hit_resolver + 新增 doc_order/alias 小工具)。
2. `uv run pytest tests/ -v` 全绿 (更新 hit_resolver 锚点断言 + 新增分桶/分组测试)。
3. 本地 `javert web --no-mssql` 不可 (工作台需 mssql); 走 62 dev 或对 `/raw` 端点做单元验证 + 模板渲染验证。
4. 升级 src 到 62 (按 CLAUDE.md tar+scp+systemctl restart 流程)。
5. 62 上重跑 `scripts/backfill_anchors.py --target mssql` 刷新锚点缓存。
6. 端到端验证: J94935 R007「重组人血」命中项目可跳; 某多命中病人 tag 导航 + 只看不明; 文书分组顺序; 费用时间列。

**回滚**: 纯前端 src, tar 回旧版 + restart 即回滚; anchors_json 旧锚点对旧代码仍兼容。

## Open Questions

- 文书归桶关键词的优先级细节 (哪些 阶段 易混) 在实现时对全量 阶段 value 跑一遍归桶审一遍, 必要时补关键词。
- `violation_type → 短别名` 表的具体别名措辞, 实现时一次性定 (16 个细类)。
