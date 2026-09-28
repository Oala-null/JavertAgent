# 工作台加载性能验收（2026-09-28）

适用 FP8 / main 的本地代码；院内维护线另行移植并独立打包。不代表 62 或 249 已部署。

## 根因与修复

- 每次详情响应都包含全部患者侧栏卡片。10 秒缓存仅减少 SQL，不减少渲染和传输；改为详情首屏只查当前患者，侧栏独立请求，每页最多 50 人。
- 历史审核只显示结论、置信度、时间与当时批注，却读取全部工具调用、推理和结构化大字段。SQL 只对每条规则最新主卡投影这些字段，保留全部历史批注。同时间用 id 逆序确定最新，以最新裁决做过滤。
- 历史锚点也曾全部读取，现在只取每条规则最新锚点。当前证据回退链路保持。
- 冷查询超过 TTL 时旧缓存刚填充即过期；计时改为查询完成后开始，15 秒合成慢查询不再立即重查。
- 旧侧栏每次输入都会遍历和重排全部 DOM；改为 250ms 防抖、服务器全名单筛选后分页，取消旧请求并校验响应序号，失败可重试。
- 新审计 SSE 不再盲目累加或无限插入侧栏；轮询仅刷新侧栏，保留详情及未提交批注。原文同患者/同页签并发点击合并请求，失败不缓存。

## 合成基准

真实 FastAPI 路由、Jinja 模板和 TestClient；1200 个合成患者、当前患者 1 条合成主卡。SQL 窗口查询由 SQLite 执行（仅适配 Unicode 字面量前缀），不连接院内数据库。

| 指标 | FP8 原始 7572a00 | 修复后 |
|---|---:|---:|
| 患者首屏 HTML 字节 | 1,022,953 | 8,660 |
| 首屏全局侧栏查询 | 1 | 0 |
| 首屏其它患者卡片 | 1200 | 0 |
| 异步侧栏每页卡片 | — | 50 |
| 异步侧栏响应字节 | — | 45,645 |
| 合成 HTTP 渲染中位数（预热后 20 次） | 12.80 ms | 0.77 ms |
| 同规则 8 次审核传输完整大工具记录 | 8 | 1 |

首屏 HTML 减少约 99.15%；上述毫秒只衡量本机合成路由/模板开销，不是医院真实加载时间。冷侧栏仍需全局轻量摘要，费用/主诊来自 CSV，所以先复用短 TTL 摘要再筛选分页；没有宣称全局 SQL 已变为常数时间。单患者大量主卡/文书本身仍会影响详情和原文大小。

## 验证

回归入口：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/test_workbench_performance.py -q -s`。
JS：`node --test tests/js/workbench_performance.test.cjs`，2 passed；另有 `node --check src/javert/web/static/app.js`。

第一轮 61 passed。扩展原始结果：166 collected、165 passed、1 failed、0 skipped、0 errors。失败为 `test_route_resolve_hits_cache_hit_charge_recheck_and_fallback`，依赖未随工作区提供的 `data/shi_fee.csv`；用原始 7572a00 路由复跑同样失败，属于既有外部夹具债务。排除此项并加入概览、事件与心跳：188 collected、187 passed、1 deselected、0 failures、0 errors。不是全仓测试全绿。追加慢查询缓存回归后的最终组合为 189 collected、188 passed、1 deselected、0 failures、0 errors。

组合范围：workbench performance/routes/templating、web app/API/auth、headline SQL Server、OCR visibility、public presenter、hit resolver、PHI access、hub raw source、backfill anchors、patient overview、event bus、heartbeat。

浏览器使用本地合成服务器，验证 50 卡上限、翻页、跨页住院号搜索、费用排序、患者切换后保持筛选、错误提示和重试入口、清空筛选；浏览器错误日志为空。原文请求并发去重与失败重试由 JS 公开入口测试锁定。分页按钮放在列表上方，避免滚过 50 人才能翻页。

## 部署边界

无 DDL、依赖升级、规则修改或历史审计重跑；既有公开 API/SSE 字段保留。新内部接口 `/api/workbench/patients` 需要登录，响应 `Cache-Control: no-store`。所有患者数据、配置与环境文件不纳入提交或补丁。

main 合并和院内补丁分别记录在本次 OpenSpec tasks 与院内独立交付文档中；现场磁盘和运行进程未经本次连接验证。

## 分支与交付完成

首批 FP8 `2603825` 已合入 main `84fa6dd`；缓存计时补充修复经相同组合回归后同步两条分支。院内独立移植 payload 提交为 `b853b3d`，交付记录为 `7d28b02`，194 项 Python + 2 项 JS 检查通过。新目录 `JiaZhongXin/Releases/Javert-249-Web-performance-v1` 已生成并完成包内摘要、安装/verify/回滚演练，本地叠加版 377 项受控文件一致；未修改已签名旧 LIS 包，未部署院内或 62。
