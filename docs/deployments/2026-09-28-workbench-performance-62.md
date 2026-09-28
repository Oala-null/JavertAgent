# 62 工作台性能发布（2026-09-28）

用户追加授权推送 gnome-243 并部署 62。院内远端已更新为 `7d28b02`；249 安装不在本次操作中。

62 只读检查：`production-62` 实时 HEAD `0010f8f5e8d8cb459cac061442731157384f8479`，受控工作树 clean，systemd active、登录页 200、SQL 健康。29 个实际进程 JAVERT_* 配置与保存配置一致；只观察到 Web 主/子进程，无独立审计批跑进程。

该基线含 main 尚未合入的慢病/原始身份展示功能，因此从已部署基线叠加性能修复，发布分支 `codex/deploy-62-workbench-performance`。运行差异仅 5 个文件：`sqlserver_store.py`、`routes_workbench.py`、`app.js`、`_sidebar.html` 和新增 `_patient_cards.html`，必须成组发布。规则、配置、数据源、模型、2C API/SSE 定义和临床评估代码保持原生产字节。

保留慢病入口、按原始姓名/就诊号搜索、内部查询键、慢病命中徽标、最新主卡临床字段和原文精确定位；详情的身份回退只查当前患者。其它患者的慢病 SSE 仅刷新侧栏，当前患者慢病状态变化仍按既有行为刷新当前页。

原基线先复现：1200 合成患者的详情首屏 1,069,913 字节、1200 侧栏卡、全局查询 1 次；8 次审核传 8 份大工具记录。修复后首屏零全局侧栏查询，每页最多 50 卡，大工具记录仅传最新 1 份。

本地组合测试：242 collected / 242 passed / 0 failed / 0 skipped / 0 errors；JS 公开原文入口 2 passed；JS 语法和 OpenSpec strict 通过。组合覆盖性能、工作台、鉴权、Web API、慢病交付/导入、原始身份、v3、公开解释、SQL 读取、原文、事件与部署工具。所有新增测试使用合成数据，不连接生产 SQL 或 LLM。

发布按 deployment_sync artifact/install，先备份进程配置与 SQLite，再幂等确认结果库 schema、重启并验证实际服务与只读页面。生产结果将在执行后记录；本段不预先声明已部署。
