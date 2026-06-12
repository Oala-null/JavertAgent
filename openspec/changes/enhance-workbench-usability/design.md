## Context

v0.6 工作台已上线 (`src/javert/web/`, 部署 62:8090, systemd)。当前数据通路:

- 病人列表 `list_patients_with_violations` → `PatientSidebarItem` (只有 V/I/C 计数 + 进度 + `batch_tag`) → `_sidebar.html`。
- 病人详情 `list_runs_for_patient` → `RunWithReviews` (含 `evidence_json` + `tool_calls_json`, **均已持久化**) → `patient_detail.html`; 概览 `build_overview` → `_patient_overview.html`。
- 原始病历 `/api/patient/{id}/raw` → `app.js` modal + `highlightAll()` 高亮跳转引擎 (**已能用, 现仅在 modal 内**)。

已确认的数据事实 (决定设计可行性):

1. `data/shi_fee.csv` 每行带 `med_list_codg` (国家医保编码, 如 `S21020000300010`) + `medins_list_codg` (院内编码) + `medins_list_name` (项目名)。**编码不用猜, join 即得**。
2. `configs/drug_audit_kb.json` 的 `drugs[通用名]` 按 `rule_type` 存 `basis` (医保限定 / 说明书原文)。**限定内容不用猜, join 即得**。
3. `tool_calls_json` 存了 LLM 实搜的 `keyword` / `section` —— `search_notes` 用 `.str.contains(keyword)` 命中, **keyword 必是原文精确子串**, 是天然锚点。
4. `evidence[]` 每条 `{source, locator, text}` —— `source ∈ {note,fee,drug,...}` 决定跳哪个 tab, `locator` 是子阶段/项目名, `text` 是 LLM 摘录 (可能含 `...` / 轻微改写, 不保证精确子串)。

约束: 库里已有 **106 病人 5016 条裁决** + 专家批注; 任何方案都不能要求重跑 LLM (扰动批注 + 数小时 GPU), 也不能要求改 `Javert_audit_runs` schema 后才能看老数据。

## Goals / Non-Goals

**Goals:**

- 5 项纯易用性升级: 列表 facet + 富卡片 / 类别就地展开 / 推理跳原文同步面板 / 命中项目展示 / 评语 hover。
- `#3 跳转`与`#5 命中项目展示`共用**同一个确定性解析器**, 不搭两套。
- 对**全部 106 病人即时生效**, 零 LLM 重跑、零 schema 迁移、专家批注零扰动。
- facet / 类别展开 / 评语 hover 三项**纯前端**, 不加后端路由。

**Non-Goals:**

- 不重跑任何审计 (回放回填只重放确定性工具, 不调 LLM)。
- 不动 LLM 审计管道 / prompt / verdict 逻辑 —— 解析器是**只读后处理**。
- 不做跨病人统计 / 新工具 / 新规则 (那是别的 change)。
- facet 不做服务端分页查询 (病人百级, 全量渲染足够)。

## Decisions

### D1 — 命中项目 / 锚点统一解析器 (`hit_resolver.py`)

`#5`要的「编码+名称+限定内容」和`#3`要的「跳原文锚点」是**同一条命中项目的两个侧面**。引入一个确定性纯函数:

```
resolve_hits(run: RunWithReviews, rule_drug_type: str|None) -> list[HitItem]

HitItem = {
  source:      "fee" | "drug" | "note",
  name:        标准项目名 / 药品通用名,
  code_nat:    med_list_codg   (国家码, 可空),
  code_local:  medins_list_codg (院内码, 可空),
  restriction: basis            (限定内容, 仅 drug 规则非空),
  anchor:      {tab, subsection, query, char_start?, char_end?}
}
```

输入只读 `evidence_json` + `tool_calls_json` + `shi_fee` (按 patient 切片) + `drug_audit_kb.json`。**一个组件喂两处渲染**: `patient_detail` 把 `HitItem[]` 渲成命中项目块 (`#5`), 每条挂 `anchor` 供点击跳转 (`#3`)。

*Alternatives:* 分别给 `#3`/`#5` 写解析 —— 否, 两者数据 90% 重叠, 分开必然漂移。

### D2 — 渲染时现算为主, 回放回填作可选缓存

解析器在 `routes_workbench.workbench_patient` 组装 `RunWithReviews` 后**现场跑** (per-patient 通常几条到十几条命中, join 是内存 dict 查 + 一次 fee 切片)。好处: **老数据立即有命中项目和跳转, 无迁移**。

`scripts/backfill_anchors.py` 是**可选**加速器: 批量跑 `resolve_hits` 把结果写进派生缓存 (新增 `anchors_json` 列或旁表, 同 run_id), 渲染时优先读缓存、miss 则现算。回填**只重放确定性逻辑、不碰 LLM、不增删 run 行**, 故专家批注 (按 run_id 关联) 纹丝不动。

*Alternatives:* (a) 必须重跑审计才有结构化 locator —— 否, 数小时 GPU + 批注被挤进 history, 代价不成比例; (b) 永远渲染时算不缓存 —— 起步就这样, 实测某病人命中过多卡顿再开缓存。

### D3 — 锚点匹配阶梯 (硬 → 软, 逐级兜底)

`evidence.text` 是 LLM 摘录, 不保证精确子串; 故定位按可靠度阶梯, **永不静默失败**:

```
1. tool_calls 里 source 对应的 keyword  → 100% 原文子串, char offset 精确   [最硬]
2. evidence.locator (子阶段名)           → 先锁定到正确那一段 note/fee 行
3. evidence.text 去首尾 "..." 后最长 n-gram → 段内高亮
4. 都不中 → 面板/同步窗至少开到对的 tab, 标"未能精确定位"            [兜底]
```

`search_fees` / `search_notes` 顺手在结果里吐 `(行定位, char 偏移)` 是**前向增量** (新跑的审计锚点更准), 但阶梯第 1 步用历史 `keyword` 已能对老数据精确定位, 故增强非阻塞。

### D4 — 编码 / 限定内容来源

- **编码**: 用该 patient 实际 fee 行 join (按 `medins_list_name` stem 匹配命中项目名), 取**那一行**的 `med_list_codg` / `medins_list_codg` —— 不做全局猜 (同名不同规格可能多码)。前端主显国家码, hover 显院内码。
- **限定内容**: `rule.drug_rule_type` (M8 规则才有) 决定取 `drug_audit_kb.drugs[通用名]` 下**该 rule_type** 的 `basis`。非药品规则 / 无 `drug_rule_type` → `restriction` 留空, 命中项目块只显编码+名称。

### D5 — facet 纯前端 (data-* + JS)

卡片渲染时把 `data-fees` / `data-dx` / `data-tag` / `data-updated` 写进 `<a class="patient-card">`; 新增 facet 栏 (tag 多选 + 费用区间 + 主诊关键词输入 + 时间预设) 用 vanilla JS 即时 show/hide。与现有服务端"违规/不明"过滤**叠加** (服务端先定哪些 patient 入列, 前端在可见集内再筛)。

*Alternatives:* 服务端查询参数 + SQL 过滤 —— 否, 病人百级、卡片本就全量渲染, 前端 facet 瞬时响应且零路由改动 (设计原则: 简单优先)。

### D6 — 卡片摘要开机 groupby 一次缓存

`fees_sum` / `primary_dx` 每个 patient 都要, 但 `CsvLoader.get_fees(pid)` 是对全表 (3000+ 病人、十万行) 切片; sidebar 百卡逐个切会卡几秒。改为**进程级缓存一次**: 启动/首次访问时对 `shi_fee` 做一次 `groupby(bah).det_item_fee_sumamt.sum()` → `{pid: fees_sum}`; `primary_dx` 复用 `patient_overview._load_zd()` 已有的进程缓存 (O(1) 查)。`updated_at` 走 SQL —— `list_patients_with_violations` 的 CTE 加 `MAX(created_at)`。缓存与 `_ZD_CACHE` / `_SS_CACHE` 同生命周期, 走 `reset_caches()`。

### D7 — 右侧原文同步面板: 对照模式 (非三栏常驻)

`patient_detail` 现为 `sidebar | detail` 两栏。加常驻第三栏会挤。改为**对照模式**: 点击某证据 → detail 区左收推理卡、右侧滑出「原文同步」面板 (Cursor 式), 关闭则复原。高亮引擎 (`highlightAll` / `jumpMatch` / TreeWalker) 从 modal **抽成可复用模块**, 面板与 modal 共用。现有"查看原始病历" modal **保留**作"全量浏览"入口。窄屏 (响应式断点) 退回 modal 形态。

### D8 — 费用类别分组挂明细 (build_overview 重组)

现 `build_overview` 把类别 (`medins_chrgitm_type`) 和明细 (`medins_list_name`) 分别聚合成两张断表。改 `_extract_from_fees_df` 让明细**按类别分组** (`{类别: [明细项...]}`), `_patient_overview.html` 把类别表每行包成 `<details>`, summary 是类别汇总、内容是该类别明细 (编码·名称·次数·金额)。数据同行即有, 不碰工具不碰库。

### D9 — 长评语全文已在页面

`list_runs_for_patient` 返回的 `ReviewRecord.comment` 本就是全文; `patient_detail.html:94` 的 `[:80]` 只是视觉截断。加 `title="{{ rv.comment }}"` (原生 tooltip) 即可, 零接口。

## Risks / Trade-offs

- **evidence.text 改写/省略号 → 段内高亮偏移** → D3 阶梯第 1 步优先用 `tool_calls` 精确 keyword, text 仅作第 3 级兜底; 第 4 级保证至少开对 tab, 不假装定位成功。
- **drug KB stem 匹配同名异药/剂型混入** (如 `布地奈德肠溶胶囊`[限IgA肾病] 命中 fee `吸入用布地奈德混悬液`, M8 已知) → 命中项目块**必带原始 fee 名** + 限定内容标注"按通用名匹配, 复方/同名/剂型需复核", 不冒充确证。
- **同名项目多编码 (规格不同)** → D4 取该 patient 实际 fee 行的码, 非全局首条; 若一名多行多码, 命中项目按行列出。
- **回放回填的行定位需 content-stable** (文件历经 `*_with_szx.csv` 合并, 行序会变) → 锚点 key 用 `(子阶段 + 匹配子串)` 而非 positional 行号; merge 只追加不改内容, 故子串偏移稳定。
- **groupby 缓存 stale** (新批次审计来) → 缓存进程级, 与现有 zd/ss 缓存同策略; `reset_caches()` 或重启刷新; sidebar 金额是"大概", 容忍轻微滞后。
- **同步面板窄屏拥挤** → 响应式断点退回 modal; 不在小屏强推三区。

## Migration Plan

- **部署即生效**: 前端模板/JS/CSS + `hit_resolver` 渲染时算 + `patient_overview`/`store` 改动, 走现有 `tar+scp+systemctl restart` 升级流程 (CLAUDE.md 已记)。无 DB 迁移、无重跑。
- **可选回填**: 跑 `scripts/backfill_anchors.py` 填锚点缓存 (若加 `anchors_json` 列, 先 `ensure-mssql-schema` 幂等加列); 失败/未跑都退回渲染时算, 不阻断。
- **rollback**: 前端回滚到上版模板/app.js/style.css + restart; `hit_resolver` 是新增模块, 旧路径不依赖它; 缓存列可留空不影响。

## Open Questions

- **命中项目"算哪些"**: 默认列 `evidence[]` 里 `source ∈ {fee, drug}` 的项 (那是 LLM 的引用证据)。是否要对 M8 额外把 `drug_audit_lookup` bulk 命中但 LLM 未点名的药也列出 (灰显"未判违规")? 倾向**只列 LLM 引用的**, 避免噪音 —— apply 时按实测调。
- **是否持久化 `anchors_json` 列**: 起步渲染时算 + 进程缓存, 不加列; 实测 sidebar/详情有卡顿再开 D2 缓存列。
- **facet "费用区间"分档**: 用固定档 (`<1万 / 1-5万 / >5万`) 还是滑块? 倾向固定档 (一眼可点), apply 时定档位。
