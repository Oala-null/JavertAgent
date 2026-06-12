## Context

工作台两处展示原始病历数据表,共用 `app.js` 的三个 builder 渲染:

- `showRawData()` →「原始病历」modal,滚动容器 `.modal-body.src-scope`(`overflow:auto`)。
- `openSourcePanel()` → 右侧「原文对照」滑出面板,滚动容器 `.source-body.src-scope`(`overflow:auto`)。

两处都含三个 tab(共用 builder),表结构各异:

- **费用** (`_feesPanelHtml`):单 `table.data-table > thead`。
- **检验记录** (`_labsPanelHtml`):两段表(化验 + 影像),各一 `thead`,中间夹 `.lab-section-head` 段标题。
- **文书** (`_notesPanelHtml`):每个折叠桶 `details.note-bucket` 各含一 `table.data-table > thead`,桶头是 `summary.note-bucket-head`。

`.src-scope` 类同时挂在 `.modal-body` 与 `.source-body` 上 — 是覆盖两处的天然 scope 锚点。
`table.data-table` 现有两条会干扰 sticky 的规则:`overflow:hidden`(为 border-radius 裁切)与 `border-collapse:collapse`。

## Goals / Non-Goals

**Goals:**
- 费用 / 检验记录 / 文书三个 tab 的列名首行(thead)在滚动容器内冻结于顶部,modal 与 source-panel 两处都生效。
- 纯 CSS 实现,单文件改动(`style.css`),不动 JS / 模板 / 后端。
- 搜索跳转命中行不被冻结表头遮挡。

**Non-Goals:**
- 不冻结段标题(`.lab-section-head`)与文书桶标题(`summary.note-bucket-head`)— 随内容滚走(已与用户确认)。
- 不做横向冻结列(只冻结首行,不冻结首列)。
- 不改 tab 切换 / 搜索 / 高亮 / 命中跳转的既有逻辑。
- 不引入分层堆叠(stacked sticky)的 top 偏移计算。

## Decisions

**决策 1:纯 CSS,scope 到 `.src-scope .data-table`,不碰 JS。**
thead 已存在于 builder 生成的 markup,sticky 是纯展示行为。一条 scope 即同时覆盖 modal 与 source-panel 两处渲染。
- 备选:在 builder 里加 wrapper class / JS 计算位置 → 否决,无谓增加 JS 复杂度,且违背「最小改动」。

**决策 2:解除 `table.data-table` 的 `overflow:hidden`(对 `.src-scope` 内的表覆写为 `visible`)。**
`overflow:hidden` 使表自身成为 sticky 的最近滚动容器,thead 只会相对表盒冻结而表盒整体随 `.modal-body` 滚走 → sticky 形同失效。这是「加了 `position:sticky` 却没反应」的根因。
- 代价:失去 border-radius 对行的裁切(纯外观,行无圆角影响微乎其微)。

**决策 3:用 `box-shadow: inset 0 -1px 0 var(--border)` 给 sticky thead 补下边框。**
`border-collapse:collapse` 下 th 的 `border-bottom` 与下方单元格共享,冻结时分隔线会消失。box-shadow 不参与 collapse,稳定绘出分隔线。th 背景已是不透明 `var(--bg-light)`,tbody 行不会透出。
- 备选:改 `border-collapse:separate` → 否决,会牵动全表边框渲染(可能双线),改动面大。

**决策 4:`z-index:2` + `scroll-margin-top` 收尾。**
thead 置于 tbody 行之上;给 `tr.highlight` / `mark.search-hit` 加 `scroll-margin-top` 让命中跳转留出表头高度。

## Risks / Trade-offs

- [文书 / 检验多 thead 接力时视觉是否突兀] → 每个 thead 在自己表盒垂直区间内冻结、出区间即自然让位,是浏览器原生 sticky 行为,符合预期;实测验证。
- [失去表行 border-radius 裁切] → 纯外观退化,可接受;若在意可仅对最外层保留圆角(非本次目标)。
- [跨浏览器 sticky + collapse 表现差异] → box-shadow 补线 + 不透明背景是社区公认稳妥组合;部署后在工作台真机(Chrome)端到端验证滚动 + 搜索跳转。
- [失败模式] → 最坏退化为「表头不冻结」(即现状),不破坏搜索 / 高亮 / tab 切换。

## Migration Plan

1. 改 `src/javert/web/static/style.css`(新增 scope 规则块)。
2. 本地 `uv run javert web` 起工作台,打开 modal + source-panel,三 tab 各自滚动验证 + Ctrl+F 跳转验证。
3. 标准 src 升级 runbook 部署 62:`tar src → scp → systemctl restart javert-web`。
4. `?v=mtime` 自动 cache-bust,无需手动 bump 版本。
5. 回滚:还原 style.css 那段 CSS 即可(无数据 / schema 变更)。
