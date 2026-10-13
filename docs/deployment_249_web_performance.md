# 249 工作台完整性能升级 v2.1（2026-10-13）

当前交付目录为`JiaZhongXin/Releases/Javert-249-Web-performance-v2.1`，只生成目录，不生成ZIP。用户确认院内已安装`Javert-249-history-dates-v1`，且性能v1尚未上传；本包以日期版为唯一安装起点，包含首批和后续性能代码，不要求先装性能v1。由用户拷贝安装，本次不连接院内。

安装与回滚见[包内说明](../delivery/web_performance_v2/README_zh.md)。原性能v1、日期包和LIS包保持不变。未知、无日期或混合版本会STOP；不得手工覆盖文件来绕过检查。

## v2.1现场安装修复

用户截图显示v2预检通过，但stop_web.py读取同账号进程cwd时PermissionError，安装器也中止。用户用`ps`确认该进程为admin2的systemd。Web仍返回200只是原服务在线；本次没有PATCH_INSTALLED成功证据。

v2.1两个入口先筛选命令，再读取应用候选cwd。无关systemd不再阻挡；命令不可读或候选cwd不可读仍STOP，不sudo、不停systemd、不忽略全部PermissionError。README安装/回滚链以`&&`连接，前步失败后不继续。9个payload摘要、11个依赖、PATCH_ID和监测context保持；原v2包不修改，已成功安装v2无需重装。

v2.1从提交`7bd5a735ddd5fbb491db44e9567bd473258231a7`构建，25项包内校验通过。`SHA256SUMS`自身SHA-256为`4f1ef6d4df6c33a38d690b450242adecdbf0bd118675712648a49b8df47bc59d`。与v2相比仅README、VALIDATION、installer、stop_web、manifest的source_commit和校验文件改变，运行文件逐字相同。旧性能v1、v2、日期包校验全部通过且摘要保持。

本轮49 collected / 49 passed / 0 failed / 0 skipped / 0 errors，含14项合成/proc回归。实际新包再次演练：旧v2两个入口均复现受限cwd错误；v2.1停止合成Web、CLI安装、CLI verify、监测策略核验及CLI回滚通过。安装后378文件匹配，回滚后377文件匹配，合成配置和账本未改；systemd的cwd访问次数为0。验收回执保存在本地`output/qa/249-web-performance-v21-20261013.json`。进程信号和/proc权限使用合成替身，未连接医院或代用户重试。

## 实际发布范围

| 文件组 | 改动 |
|---|---|
| `store/sqlserver_store.py` | 当前患者的最新主卡取完整大字段；历史保留批注和院内实际展示的400字说明预览（取401字判断省略号） |
| `web/api/routes_workbench.py`、`web/hit_resolver.py` | 独立50人分页、全历史日期筛选后分页、60秒侧栏缓存/签名失效、费用预处理每请求一次 |
| `web/api/main.py` | 大响应GZip，SSE不压缩；保留上海启动不DDL/不回灌 |
| `web/static/app.js` | 无白屏导航、短期详情缓存、事件/401/焦点失效、旧原文世代隔离，保留日期选择和提示 |
| `web/templates/_sidebar.html`、`_patient_cards.html`、`patient_detail.html`、`workbench.html` | 分页与导航配套元数据，保留日期徽标/历史日期、上海筛选隔离和原文容器 |

上述9个文件必须整包安装。`models.py`、`templating.py`、`batch_dates.py`在维护分支补登已交付日期包的原字节；它们不在性能payload中，现场作为11个未变依赖的一部分校验。因此不会重新添加或覆盖日期功能。

院内没有62的来源profile查询，未为移植来源缓存新增该业务路径。费用命中/退费/缓存锚点语义、原文全量接口与30秒缓存、LIS本次住院/pending、规则/模型/2C合同均保持。无SQL、依赖安装、配置覆盖或审计重跑。

## 监测兼容

日期版watcher会校验全部运行文件。性能v2提供兼容入口，以日期+性能的新完整清单校验磁盘，并严格限制差异为9个性能文件。原watcher、ledger、检查器、known-release四文件逐字保持，原审计context不变。安装与回滚不迁移/修改SQLite账本。

性能v2生效后若需恢复监测，使用本包`watch/watch.sh`；回滚后用原日期包入口。安装不自动恢复监测。监测锁、批跑锁和同目录进程检查防止运行中覆盖；原批跑CODE_FILES未改变。

## 构建与验证

构建只读取已提交源码，目标存在即拒绝覆盖：

```bash
python3 scripts/build_249_web_performance_v2.py /Users/shane/26er/Javert/JiaZhongXin/Releases
```

manifest固定日期基线、9个目标摘要和11个依赖摘要。`known-release-after.json`描述日期版叠加性能后的378文件范围；原`deploy/249/known-release.json`仍保留旧LIS交付身份，不被改写。

只在本机使用合成数据验证。实际浏览器确认日期筛选为2/4、上海无全局费用筛选，A→C→A保留Document及日期选择，返回A复用初始响应；截图在包内`QA-browser.jpg`。性能、LIS、日期、安装/故障恢复/回滚、监测context不变等测试记录见[VALIDATION](../delivery/web_performance_v2/VALIDATION.md)。

初次日期兼容组合227 collected / 226 passed / 1 failed / 0 skipped / 0 errors；失败来自旧模板测试仍要求随机tag。按已安装日期合同改为验证日期chip/data-dates，没有修改生产逻辑来迎合断言。后续还新增了历史说明预览先红回归，避免院内可见内容被减载丢失。

最终相关组合228 collected / 228 passed / 0 failed / 0 skipped / 0 errors；另有30项Node全部通过，JS/Bash语法、diff和OpenSpec strict通过。不是全仓测试声明。

## v2交付证据

构建源码提交`73a71ea69586056ec5c0b2f0c6a1b4be9ba2fcb6`已推送gnome-243。实际目录为`/Users/shane/26er/Javert/JiaZhongXin/Releases/Javert-249-Web-performance-v2`。25项SHA校验全通过；9个payload、11个未变依赖；本地完整安装目标378文件匹配。用实际包执行check、文件安装、CLI verify、监测策略校验、回滚；精确恢复日期版，合成配置和账本字节不变。Linux进程控制没有在医院实机执行。

`SHA256SUMS`自身SHA-256：`1acaf192f7843c7527a4420f0d978912bd9faac38af4f3b1c579263bd52b8a2f`。同名目录重建被拒绝。旧性能v1的13项校验和日期包36项校验仍全部通过。

后续仅提交本交付记录及任务完成状态，不重写已构建目录。用户自行拷贝安装；未连接院内、未启动监测或审计，现场安装/重启/真实页面验收仍由用户按包内README完成。

---

# 历史性能v1交付记录

交付目录：`JiaZhongXin/Releases/Javert-249-Web-performance-v1`，默认不生成 ZIP。

完整安装与回滚步骤见 [补丁说明](../delivery/web_performance/README_zh.md)，测试与已知边界见 [验收记录](../delivery/web_performance/VALIDATION.md)。现场上传整个版本目录即可执行；不要只复制单个 Python/JS 文件。

必须成组合并/交付的运行文件：

| 文件 | 改动 |
|---|---|
| `src/javert/store/sqlserver_store.py` | 历史大字段只返回最新主卡，最新锚点，稳定时间排序 |
| `src/javert/web/api/routes_workbench.py` | 患者首屏不查全局侧栏；登录保护的摘要分页、筛选、排序；慢查询缓存计时 |
| `src/javert/web/static/app.js` | 独立加载侧栏、50 卡分页、防抖、取消旧请求、失败重试、原文请求去重 |
| `src/javert/web/templates/_sidebar.html` | 分页侧栏框架；保留上海主诊/费用隔离 |
| `src/javert/web/templates/_patient_cards.html` | 服务端每页卡片模板，必须随路由一起安装 |

不包含 FP8 分支新增的模型、肿瘤知识库或 2C 契约。原 v1 的 `deploy/249/known-release.json` 不改写，新目录提供 `known-release-after.json` 验证叠加后的完整磁盘身份。

维护者从已提交树构建：

```bash
python3 scripts/build_249_web_performance.py JiaZhongXin/Releases --ref b853b3d578bad9785987a9853c0dd70b501e7a4c
```

目标目录存在即拒绝覆盖。新代码若已改变 v1 清单中的 payload 或依赖，构建拒绝，必须另立版本。`DEPLOY_COMMIT` 保留旧基线；新包以 `manifest.json` 的源码提交和文件摘要标识。医院是否已安装、Web 是否已重启需现场验收，本次本地交付不作这一声明。

## 本次交付证据

构建提交：`b853b3d578bad9785987a9853c0dd70b501e7a4c`。目录内 13 项校验通过，安装→verify→回滚演练通过；本地 377 项受控运行文件与叠加清单匹配。再次构建同名目录被拒绝。

`SHA256SUMS` 自身 SHA-256：`d0465416d7f6a4c0f5338fb7355fe6587a1185555926c5b9d024c5dbb207001e`。原目录已有 108 个修改/未跟踪文件的字节保持不变。未连接院内运行环境。
