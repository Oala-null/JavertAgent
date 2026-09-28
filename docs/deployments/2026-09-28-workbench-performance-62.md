# 62 工作台性能发布（2026-09-28）

用户追加授权推送 gnome-243 并部署 62。院内远端已更新为 `7d28b02`；249 安装不在本次操作中。

62 只读检查：`production-62` 实时 HEAD `0010f8f5e8d8cb459cac061442731157384f8479`，受控工作树 clean，systemd active、登录页 200、SQL 健康。29 个实际进程 JAVERT_* 配置与保存配置一致；只观察到 Web 主/子进程，无独立审计批跑进程。

该基线含 main 尚未合入的慢病/原始身份展示功能，因此从已部署基线叠加性能修复，发布分支 `codex/deploy-62-workbench-performance`。运行差异仅 5 个文件：`sqlserver_store.py`、`routes_workbench.py`、`app.js`、`_sidebar.html` 和新增 `_patient_cards.html`，必须成组发布。规则、配置、数据源、模型、2C API/SSE 定义和临床评估代码保持原生产字节。

保留慢病入口、按原始姓名/就诊号搜索、内部查询键、慢病命中徽标、最新主卡临床字段和原文精确定位；详情的身份回退只查当前患者。其它患者的慢病 SSE 仅刷新侧栏，当前患者慢病状态变化仍按既有行为刷新当前页。

原基线先复现：1200 合成患者的详情首屏 1,069,913 字节、1200 侧栏卡、全局查询 1 次；8 次审核传 8 份大工具记录。修复后首屏零全局侧栏查询，每页最多 50 卡，大工具记录仅传最新 1 份。

本地组合测试：242 collected / 242 passed / 0 failed / 0 skipped / 0 errors；JS 公开原文入口 2 passed；JS 语法和 OpenSpec strict 通过。组合覆盖性能、工作台、鉴权、Web API、慢病交付/导入、原始身份、v3、公开解释、SQL 读取、原文、事件与部署工具。所有新增测试使用合成数据，不连接生产 SQL 或 LLM。

发布按 deployment_sync artifact/install，先备份进程配置与 SQLite，再幂等确认结果库 schema、重启并验证实际服务与只读页面。执行结果如下。

## 生产验收

- 运行文件提交 `888c27be233d2c759a1a97fff577bd8bb8acad68`，由 production-62 受控制品安装；首次 artifact SHA-256 `22e6323c12cedd2a3bf6067b0280d623c4dcbf19b2a3c18d5246470f7abdda6b`。
- 备份 `/home/admin2/backup/javert-git-20260928-122316-54278`：目录0700，环境、进程快照及 SQLite 备份0600；SQLite integrity_check=ok。完整旧版回滚制品也已保留在服务器 `/home/admin2/releases/web-performance-20260928/`。
- 结果库幂等 schema 确认退出0、4表就绪；只作用于既有 zadig。没有变更 Hub schema、没有重跑历史/真实患者审计。
- systemd MainPID 969790→54525，active；29 个 JAVERT_* 项逐值一致，.env 字节未改。SQL 健康，Hub SELECT 1 成功，同步 worker running。
- 真实 HTTP：/login、/api/health 均200；v3 空数组 submit=202 且 accepted/rejected 为空；去标识未知号 results=200/unknown；匿名分页接口401；静态 app.js 字节与发布文件一致。
- 客户端禁用代理直连登录页为200，用时0.027秒；服务监听0.0.0.0:8090。应用内浏览器一次导航超时，不用这个结果冒充已完成登录后的浏览器验收。
- 部署后10分钟窗口日志 Traceback=0、ERROR=0，startup complete=1。未回传日志、连接串、患者号或病历原文。
- 服务器独立只读进程调用实际 SQL 和模板（使用合成渲染身份，不修改在线鉴权或签发 cookie）：普通病例72张主卡，冷读14.122秒、533749字节；慢病20张主卡，0.867秒、210462字节。两者首屏全局侧栏查询0；慢病原始身份未退化为内部键。侧栏全名单1169人，每页50卡、55415字节、冷读3.222秒。

这些渲染计时包括独立进程的数据冷读，不是用户浏览器实际加载时间。单个大病例本身仍可能慢；全局轻量摘要查询也仍存在。当前验收锁定了去除每页全量侧栏、保留业务功能和部署可用性，没有声称所有前端性能问题都消失。

首次部署后官方 check 返回 synced=true、受控 dirty=[]。后续只提交本记录与 OpenSpec 完成状态，运行文件树与888c27b逐字相同；用同一受控制品流程同步提交身份，无需再次重启未变更的运行代码。
