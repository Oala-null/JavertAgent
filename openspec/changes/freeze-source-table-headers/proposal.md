## Why

工作台审核时,专家在「原始病历」modal 和右侧「原文对照」面板里翻看费用 / 检验记录 / 文书的长表格,
一往下滚动列名首行(时间 / 项目 / 结果 / 标志 …)就滚出视野,看到一半要回滚确认这一列是什么,逐条核对很费神。
把列名首行在滚动时冻结(sticky),长表格随时能对上列含义,是低成本高频收益的易用性改善。

## What Changes

- 「原始病历」modal (`.modal-body`) 和右侧「原文对照」面板 (`.source-body`) 内,
  **费用 / 检验记录 / 文书** 三个 tab 的数据表 `thead` 列名首行在容器滚动时冻结在顶部。
- 检验记录 tab 两段表(化验 / 影像)各自 thead 接力冻结;文书 tab 每个折叠桶的 thead 在该桶内冻结、桶间接力。
- 段标题(「检验·化验」「检查·影像」)与文书桶标题(summary)**不**冻结,随内容滚走(已与用户确认)。
- 搜索(Ctrl+F)跳转命中行时,加 `scroll-margin-top` 让目标行不被冻结表头遮挡。
- 实现方式:**纯前端、纯 CSS**,只改 `src/javert/web/static/style.css`,不动 JS、不动模板、不动后端。
  - 解除 `table.data-table` 的 `overflow:hidden`(否则表自身成为滚动容器,sticky 失效)。
  - 用 `box-shadow` 补 `border-collapse: collapse` 在 sticky 时丢失的表头下边框分隔线。

## Capabilities

### New Capabilities
<!-- 无新增 capability -->

### Modified Capabilities
- `review-workbench`: 新增「原始病历 / 原文对照」数据表列名首行滚动冻结(sticky thead)的展示行为要求。

## Impact

- **代码**: 仅 `src/javert/web/static/style.css`(新增 ~8 行 CSS 规则,scope 到 `.src-scope .data-table`)。
- **不影响**: `app.js`(表格 builder 不变)、`patient_detail.html` 等模板、后端 `/api/patient/{pid}/raw`、数据层。
- **部署**: 走标准 src 升级 runbook(tar→scp→systemd restart);静态资源 `?v=mtime` 自动 cache-bust,无需手动 bump 版本号。
- **风险**: 极低。纯展示层 CSS,不改数据 / 交互逻辑;失败模式仅为「表头未冻结」(退化到现状),不破坏既有搜索 / 高亮 / tab 切换。
