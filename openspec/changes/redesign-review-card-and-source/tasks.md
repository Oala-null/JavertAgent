## 1. 后端: 文书分桶 + 费用日期 (routes_workbench / 新工具)

- [x] 1.1 新增 `src/javert/web/doc_order.py`: `阶段关键词 → (桶名, 序)` 映射 + `bucket_of(stage) -> (name, order)`, 关键词带优先级 (病程关键词先于知情书命中), 兜底「其他」桶
- [x] 1.2 对全量 `case_notes.csv` 的 `阶段` distinct 跑一遍 `bucket_of`, 人工审归桶结果, 补漏关键词 (易混: 含「讨论」的同意书 / 评估单 / 核查表)
- [x] 1.3 `routes_workbench._notes_to_list`: 给每条 note 加 `bucket` + `bucket_order` 字段, 输出按 `(bucket_order, 事件时间)` 排序
- [x] 1.4 `routes_workbench._fees_to_list`: 预格式化 `fee_date` 字段 (解析 dd/mm/yyyy → `YYYY/MM/DD`), 复用 `patient_overview` 的多格式 fallback

## 2. 后端: 命中项目模糊跳转解耦 (hit_resolver)

- [x] 2.1 `hit_resolver._resolve_fee_anchor`: 无 fee 行匹配时改为 `query = 命中名 (或 kb_stem)`, `match_level="name"`, 不再 `unresolved=True`; 编码富集逻辑不动
- [x] 2.2 更新 `tests/` 中 hit_resolver 锚点降级断言 (旧 unresolved fee 场景 → 带 name query); 新增「重组人血无码仍可跳」用例

## 3. 后端: 细类分组 + 别名 (rule_meta / routes_workbench)

- [x] 3.1 新增 `violation_type → 短别名` 表 (web 层, 16 个细类, 整句压成短词)
- [x] 3.2 `routes_workbench.workbench_patient`: 把 `runs` 按 `violation_type` 分组、组内 V 前 I 后, 传 `run_groups`=`[{vt, alias, n_v, n_i, runs}]` 给模板

## 4. 前端模板: 概览瘦身 + 费用列序 (_patient_overview.html)

- [x] 4.1 删除主诉 / 现病史 / 既往史三个 subblock (诊断/手术/费用结构保留)

## 5. 前端模板: 违规卡片重构 + tag 导航 (patient_detail.html)

- [x] 5.1 命中项目块只渲 `h.source in {fee, drug}`; note 类不再单列
- [x] 5.2 加「📄 查阅文书原文」按钮 (替代 note 命中项), 点击 `openSourcePanel({tab:'notes'})`
- [x] 5.3 `reasoning` + `evidence_json` 移进折叠 `<details class="evidence-block">` (默认收起)
- [x] 5.4 卡片字幕简化为细类别名 (去掉 `.模板Mx` code 串)
- [x] 5.5 正文按 `run_groups` 渲染可折叠 section + 组标题 (违规数·不明数)
- [x] 5.6 顶部 sticky chip 行 (每组一 chip: `别名 n_v▪n_i`, `data-target` 指向组锚点) + 「只看不明」toggle 控件

## 6. 前端 JS: 文书分组渲染 + 跳转 + chip 导航 (app.js)

- [x] 6.1 `_feesPanelHtml`: 时间列移到第一列, 用后端 `fee_date`
- [x] 6.2 `_notesPanelHtml`: 按 `bucket` 分组渲染可折叠组标题段, 桶序由后端 `bucket_order` 保证
- [x] 6.3 `openSourcePanel`: 支持 `{tab:'notes'}` 无 query 时滚到文书 tab 顶部 (查文书按钮入口)
- [x] 6.4 chip 点击 → `scrollIntoView` 到细类组锚点 (导航, 不 reload)
- [x] 6.5 「只看不明」toggle: 纯 CSS class 隐藏 V 卡片, 与 server filter 正交, 可逆

## 7. 验证 + 部署

- [x] 7.1 `uv run pytest tests/ -v` 全绿 (377 passed, 1 skipped)
- [x] 7.2 模板渲染 + `/raw` 端点单元验证 (分桶顺序 / fee_date 格式 / run_groups 排序) — `tests/test_doc_order.py` + `tests/test_workbench_routes.py` + `test_patient_detail_run_groups_chips_and_ordering`
- [x] 7.3 升级 src 到 62 (tar+scp+systemctl restart 流程) — 已部署, 旧 src 备份 `/tmp/javert-src-prev.tgz`; 服务 active, /api/health 200; 6 项新代码标记均已落盘
- [x] 7.4 62 上重跑 `scripts/backfill_anchors.py --target mssql` 刷新锚点缓存 — 13,881 条 run 全部重算并提交 (10,552 条带新 `name` 级锚点)
- [x] 7.5 端到端: J94935 R007「重组人血」可跳费用 (`unresolved=False, level=name`) + 文书分桶顺序 (入院→病程→手术→知情→出院小结) + 费用 `YYYY/MM/DD`; tag导航/只看不明走绿色单测 + 已部署代码 (无浏览器侧驱动)
