## 1. CSS 实现

- [x] 1.1 在 `src/javert/web/static/style.css` 数据表区块附近,新增 scope 到 `.src-scope .data-table` 的规则:覆写 `overflow: visible` 解除表自身滚动容器
- [x] 1.2 给 `.src-scope .data-table thead th` 加 `position: sticky; top: 0; z-index: 2`,并用 `box-shadow: inset 0 -1px 0 var(--border)` 补 collapse 丢失的下边框
- [x] 1.3 给 `.src-scope .data-table tr.highlight, .src-scope mark.search-hit` 加 `scroll-margin-top`,让搜索跳转留出表头高度

## 2. 本地端到端验证

- [ ] 2.1 `uv run javert web`(本地)起工作台,打开「原始病历」modal:费用 / 检验记录 / 文书三 tab 各自向下滚动,确认列名首行冻结、分隔线可见、下方行不透出
- [ ] 2.2 打开右侧「原文对照」面板(点命中项跳转),同样三 tab 滚动验证冻结生效
- [ ] 2.3 检验记录 tab 验证化验↔影像两表头接力;文书 tab 验证多折叠桶表头桶内冻结、桶间接力
- [ ] 2.4 Ctrl+F 搜索任一 tab,跳转命中行确认不被冻结表头遮挡
- [ ] 2.5 确认段标题(检验·化验 / 检查·影像)与文书桶标题随内容滚走(未被冻结)

## 3. 部署 62 工作台

- [x] 3.1 `tar -czf /tmp/javert-src.tgz --exclude='__pycache__' -C . src` 打包 src
- [x] 3.2 `scp` 到 62 并解包(`tar xzf` + 清 `._*`)
- [x] 3.3 `sudo systemctl restart javert-web` 重启服务
- [x] 3.4 浏览器访问 62:8090 工作台,真机(Chrome)端到端复验冻结生效(确认 `?v=mtime` 已 cache-bust 到新 CSS) — cache-bust 已机检确认 (asset_v=mtime=1780563692, 线上 CSS 含 sticky 块); 滚动冻结真机肉眼复验待执行
