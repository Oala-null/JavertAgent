## 1. 数据层: 卡片摘要 + 更新时间 (D6)

- [x] 1.1 `store/models.py` `PatientSidebarItem` 加 `fees_sum: float = 0.0` / `primary_dx: str = ""` / `updated_at: datetime | None = None` 三字段 (默认值不破坏现有构造)
- [x] 1.2 `store/sqlserver_store.py` `list_patients_with_violations`: CTE 加 `MAX(created_at) AS updated_at` per patient, 回填到 `PatientSidebarItem.updated_at`
- [x] 1.3 `web/patient_overview.py` 加 `get_fees_sum_map()`: 对 `shi_fee` 一次 `groupby(bah).det_item_fee_sumamt.sum()` → `{pid: fees_sum}`, 进程级缓存 (同 `_ZD_CACHE` 生命周期, 纳入 `reset_caches()`)
- [x] 1.4 加 `get_primary_dx(pid)`: 复用 `_load_zd()` 进程缓存返回主诊 (maindiag_flag=1, name+code); 无首页则 note 派生兜底, 再无则 `""`
- [x] 1.5 `routes_workbench.workbench_index` / `workbench_patient`: 用 1.3/1.4 给每个 `PatientSidebarItem` 填 `fees_sum`/`primary_dx` (sidebar 渲染前)
- [x] 1.6 `tests/`: 加 `get_fees_sum_map` 命中已知 patient (J66252 ≈ 已知值) + 缓存只算一次 + `primary_dx` 取 maindiag_flag=1 + 缺首页兜底 三场景
- [x] 1.7 `uv run pytest tests/ -v` 全绿

## 2. 命中项目 / 锚点解析器 (evidence-anchoring 核心, D1/D3/D4)

- [x] 2.1 新 `web/hit_resolver.py`: 定义 `HitItem` (pydantic, `{source,name,code_nat,code_local,restriction,anchor}`) + `Anchor` (`{tab,subsection,query,char_start?,char_end?,unresolved:bool}`)
- [x] 2.2 `_enrich_code(name, patient_fee_df)`: 按 `medins_list_name` stem 匹配该 **patient 实际 fee 行** → `med_list_codg`/`medins_list_codg`; 复用 `drug_audit_lookup` 的 stem 匹配逻辑; 无码留空 (D4)
- [x] 2.3 `_enrich_restriction(name, drug_rule_type)`: `drug_rule_type` 非空时 join `configs/drug_audit_kb.json` `drugs[通用名]` 该 rule_type 的 `basis`; 同名异药/剂型标注"按通用名匹配, 需复核"; 否则空
- [x] 2.4 `_resolve_anchor(evidence_item, tool_calls, source_df)`: 实现 D3 匹配阶梯 (keyword→locator→text n-gram→tab-only), 算 char_start/char_end, 不中则 `unresolved=True` 仅留 tab
- [x] 2.5 `resolve_hits(run, rule_drug_type) -> list[HitItem]`: 读 `evidence_json` (source∈{fee,drug,note}) + `tool_calls_json`, 组装 HitItem[]; 纯函数, 无 LLM/网络/写库
- [x] 2.6 新 `tests/test_hit_resolver.py`: drug 违规出码+限定+锚点 (构造 J26355 R007 fixture) / 纯函数二次调用 byte-identical / 仅 etl_warning 返回空 / 同名规格多码取本患者行 / 缺码降级 / 锚点阶梯四级各一例 / 非药品规则 restriction 空
- [x] 2.7 `uv run pytest tests/test_hit_resolver.py -v` 全绿

## 3. 费用类别分组挂明细 (D8)

- [x] 3.1 `web/patient_overview.py` `_extract_from_fees_df`: 在按类别聚合的同时, 收集 `{类别: [明细项(编码/名称/次数/金额)...]}` (同行数据, 不新增扫描)
- [x] 3.2 `build_overview` 输出 `fee_categories` 每项挂 `items: [...]` (该类别明细, 按金额倒序, 截断 Top-N 防超长)
- [x] 3.3 `templates/_patient_overview.html`: 类别表每行包成 `<details>`, summary 保留原汇总 (条数/金额/占比/bar), 内容渲该类别明细子表 (编码·名称·次数·金额)
- [x] 3.4 `tests/`: `build_overview` 某类别 items 金额之和 == 该类别汇总金额 (一致性) + items 不为空
- [x] 3.5 `uv run pytest tests/ -v` 全绿

## 4. 病人列表 facet + 富卡片 (D5)

- [x] 4.1 `templates/_sidebar.html`: 卡片渲 `primary_dx` + `¥fees_sum` + 相对更新时间 (复用 `humanize_zh`); 加 `data-fees`/`data-dx`/`data-tag`/`data-updated` 属性
- [x] 4.2 `_sidebar.html`: 在 verdict `<select>` 下加 facet 栏 — tag 多选 (chip) + 费用区间固定档 (`全部/<1万/1-5万/>5万`) + 主诊关键词 `<input>` + 更新时间预设 (`全部/今天/本周/本月`)
- [x] 4.3 `static/app.js`: `applyFacets()` 读各 facet 状态, 对 `.patient-card` 按 `data-*` show/hide; 与服务端 verdict filter 叠加 (只在已渲染集内筛); 输入/选择即时触发 (无 reload)
- [x] 4.4 `static/app.js`: facet 清空恢复全集; facet 状态写 sessionStorage 跨 patient 详情页保持 (可选)
- [x] 4.5 `static/style.css`: facet 栏 + chip + 卡片新增行 (主诊/金额/时间) 样式 (蓝色商务风, 与现有一致)
- [x] 4.6 (本地 dev) `uv run javert web --no-mssql` 起站 (sidebar 走 fixture 或忽略 503), 手验: 输 `甲状腺` 只剩甲状腺患者; 选 `>5万` 按金额筛; tag+verdict 叠加交集正确

## 5. 违规卡命中项目块 + 评语 hover (D7 部分 / D9)

- [x] 5.1 `routes_workbench.workbench_patient`: 对每个 `RunWithReviews` 跑 `resolve_hits` (取 rule 的 `drug_rule_type` from rule yaml/meta), 把 `HitItem[]` 注入模板上下文
- [x] 5.2 `templates/patient_detail.html`: 规则描述 (`meta.question`) 下方加 「命中项目 (N)」块, 渲 `code_nat · name` (院内码 `title` hover); 有 `restriction` 则加 `限定: ...` 段; 每条挂 `data-anchor`(JSON) 供点击
- [x] 5.3 `patient_detail.html:94` 其他专家评语截断处加 `title="{{ rv.comment }}"` (原生 tooltip); 同步删除残留的无效 `[展开]` 意图 (若有)
- [x] 5.4 `static/style.css`: 命中项目块样式 (违规色边 + code 等宽 + 限定段弱化色) + 评语 hover 框 (CSS tooltip 备选)
- [x] 5.5 (本地 dev) 手验: J26355 R007 卡显 4 条命中药 + 编码 + 限定内容; 非药品违规只显码+名; hover 评语显全文

## 6. 右侧原文同步面板 — 跳转 (D7)

- [x] 6.1 `static/app.js`: 把现有高亮引擎 (`highlightAll`/`jumpMatch`/`clearHighlights`/TreeWalker) 抽成可复用模块, modal 与新面板共用同一套
- [x] 6.2 `static/app.js`: `openSourcePanel(anchor)` — 详情区进入"对照模式" (推理卡左收 + 右侧滑出面板), 拉 `/api/patient/{pid}/raw`, 切到 `anchor.tab`, 滚到 `anchor.subsection`, 用 `anchor.query` 高亮命中并居中
- [x] 6.3 `static/app.js`: 命中项目/证据条目点击 → 解析 `data-anchor` → `openSourcePanel`; `unresolved` 锚点只切 tab + 显"未能精确定位"提示, 不乱高亮
- [x] 6.4 `static/app.js`: 关闭面板复原布局, 清高亮状态不泄漏到下一卡; 窄屏断点退回 modal 形态
- [x] 6.5 `static/style.css`: 对照模式两栏布局 (推理 | 原文) + 面板滑入动画 + 响应式断点
- [x] 6.6 (本地 dev) 手验: 点 note 证据 → 右栏文书 tab 滚到出院诊断高亮; 点 fee 命中 → 费用 tab 高亮该行; 关闭复原; 不可定位的锚点只开 tab 不假高亮

## 7. 回放回填脚本 — 可选缓存 (D2)

- [x] 7.1 (若走持久化) `scripts/sql/create_javert_tables.sql` 加 `anchors_json NVARCHAR(MAX) NULL` 到 `Javert_audit_runs` (或旁表), `ensure-mssql-schema` 幂等; 不走持久化则跳过本组
- [x] 7.2 新 `scripts/backfill_anchors.py`: 遍历 runs, 跑 `resolve_hits` (只重放确定性逻辑, **不调 LLM**), 写 `anchors_json` 缓存 (同 run_id, 不增删行)
- [x] 7.3 `routes_workbench`: 渲染优先读 `anchors_json` 缓存, miss 则 `resolve_hits` 现算 (回填非阻塞)
- [x] 7.4 `tests/`: 回填幂等 (二次跑 byte-identical) + 不改任何 `javert_vio_review` 行 + 不增删 run 行 + 缓存缺失时渲染仍出命中项目 (fallback)
- [x] 7.5 `uv run pytest tests/ -v` 全绿

## 8. (前向可选) search 工具 locator 增强 (D3)

- [x] 8.1 `tools/search_notes.py` / `tools/search_fees.py`: 在返回文本/结构里附 `(行定位, char 偏移)`, 让新跑审计的锚点精确到字符 (不破坏现有纯文本契约 — 加结构旁路或注释行)
- [x] 8.2 (可选) `audit/result.py` `Evidence` 加 optional `anchor` 字段 (默认 None, 前向; 老数据无此字段走解析器兜底)
- [x] 8.3 `tests/`: 工具增强后 locator 偏移命中原文; 老 evidence (无 anchor) 仍被 `resolve_hits` 正常处理
- [x] 8.4 `uv run pytest tests/ -v` 全绿 (含 search 工具既有测试不回归)

## 9. 端到端验证 + 部署

- [x] 9.1 `uv run pytest tests/ -v` 全量全绿 (含新增 hit_resolver / overview / facet 相关)
- [x] 9.2 升级 src 到 62 (CLAUDE.md `tar+scp` 流程), 若启用缓存列先 `ensure-mssql-schema`, `sudo systemctl restart javert-web`
- [ ] 9.3 (真实请求, 不假设热重载) 浏览器开 62:8090 `/workbench`: facet 四项各筛一次有效; 卡片显钱+病+时间
- [ ] 9.4 真实请求验 `/workbench/J26355`: R007 命中项目块出 4 药+编码+限定; 点证据右栏跳原文高亮; hover 评语显全文
- [ ] 9.5 真实请求验 `/workbench/J66252`: 费用类别就地展开见明细; 命中项目对甲状腺 on-label 药 (若有 C) 不误显违规码
- [x] 9.6 (可选) 跑 `scripts/backfill_anchors.py` 回填后复验渲染走缓存路径一致; 确认专家批注数 (`v_javert_reviews`) 跑前跑后不变
- [x] 9.7 更新 `26er/Javert/CLAUDE.md` 架构图 + 当前阶段标记 (本 change 完成项) + 必要时 `docs/review_workbench_user_guide.md` 加 facet/跳转/命中项目使用说明
