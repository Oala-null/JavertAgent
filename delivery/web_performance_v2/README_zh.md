# 243/249 完整工作台性能补丁 v2.1

把整个`Javert-249-Web-performance-v2.1`目录拷贝到医院的`/home/admin2/releases/`，用应用所属账号`admin2`操作。243是历史称呼，院内维护线为gnome-243，应用路径是大写`/home/admin2/Javert`。

**本包只用于已经安装`Javert-249-history-dates-v1`的机器。不用先安装性能v1。**
用户已确认院内安装了日期补丁。本包保留其日期合并、历史多日期筛选和LIS行为，只叠加性能优化，共更新9个源码/模板文件。

| 检查输出 | 含义 | 操作 |
|---|---|---|
| `READY`且`BASELINE=history-dates-v1` | 已安装已知日期补丁，可升级 | 直接安装本包 |
| `INSTALLED` | 已完整安装本包 | 不重复安装 |
| `STOP` | 未装日期补丁、未知/混合版本或环境不匹配 | 停止并保留输出，不手工覆盖 |

本包不读写`.env`、`243_config.env`、`effective-config.json`、数据库、患者文件、规则、模型、批次账本；不联网下载依赖，不运行SQL或重算历史。上海取数、LIS本次住院/pending分区和原文接口保持。

## 安装工具修订说明

v2.1修复v2扫描同账号systemd进程时读取受限cwd导致的误报。先读取命令判断是否为应用候选，再核验候选cwd；应用候选cwd或进程命令不可读时仍STOP。不要停止systemd，不要sudo，不要跳过进程检查。

9个性能源码文件、11个依赖和安装PATCH_ID与v2完全一致。目录改名用于区分安装工具修订，原v2目录及SHA256SUMS保持原样。v2备份仍兼容。已成功安装v2的机器无需重复覆盖源码。后续需要安装/回滚时使用本包。

## 1. 在线只读检查

```bash
cd /home/admin2/releases/Javert-249-Web-performance-v2.1 &&
sha256sum -c SHA256SUMS &&
bash install.sh check
```

须全部通过，且输出`GZIP_SSE_RUNTIME=PASS`、`PATCH_STATE=READY`及上述已知BASELINE才继续。运行环境检查只调用应用`.venv/bin/python`验证合成HTTP压缩/SSE，不导入应用、不连接数据库。失败时不要升级依赖来强行安装，先保留检查输出。

版本戳仍应是`8645a2d`对应完整提交；仅版本戳一致不够，安装器还会核对全部9文件和11个依赖文件。部分手动复制过的版本会被拒绝。

## 2. 维护窗口安装

先请求日期版持续监测停止，再查看状态。`stop`会等待当前患者结束，不强杀任务。

```bash
bash /home/admin2/releases/Javert-249-history-dates-v1/watch/watch.sh stop
bash /home/admin2/releases/Javert-249-history-dates-v1/watch/watch.sh status
```

须确认`service_lock_held`为false且当前患者任务已结束。安装器还会持有监测锁，防止安装期间启动新监测。

再确认批跑、单患者和取数任务状态。等待当前任务结束，或按既有运维流程暂停支持优雅停止的watch。单次lis_batch没有优雅stop，不能直接kill审计进程。

```bash
bash /home/admin2/Javert/scripts/lis_batch.sh status
```

确认任务都已停止后，执行：

```bash
cd /home/admin2/releases/Javert-249-Web-performance-v2.1 &&
sha256sum -c SHA256SUMS &&
bash install.sh check &&
python3 stop_web.py &&
bash install.sh install --services-stopped &&
bash install.sh verify &&
bash /home/admin2/start_javert_web_bg.sh &&
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/login
```

命令用`&&`连接；前一步失败，后续安装、验证和启动均不会执行。出现`STOP`时保留输出，不继续粘贴剩余步骤。登录页200不能证明已安装。

保存安装输出的`BACKUP=/home/admin2/Javert/output/web-performance-backup.<实际后缀>`。备份只含本次9个代码文件及回执；新文件原来不存在时，回滚会移除它。

`stop_web.py`只停止同账号、同应用目录唯一的Web进程；无匹配不猜PID，多匹配拒绝。安装器还会检查批跑锁及同目录活动进程。若systemd自动拉起Web，须按现场原运维方式停止对应服务，再重试。失败后先执行`check`确认磁盘状态，再启动Web；若仍STOP，按本次准确备份回滚，不要复制单文件覆盖。

静态JS也需要重启Web，因为模板资源版本在进程内固定。启动助手沿用现场保存配置；不要复制62配置或重新生成session secret。

## 3. 页面验收

登录页应返回200。已有浏览器页面普通刷新一次，然后用原账号检查：

1. 详情先出现，侧栏每页最多50人；住院号搜索和翻页覆盖全名单。上海侧栏仍隐藏全局主诊/费用筛选。
2. A→B→A切换期间保留当前画面；同一标签页60秒内返回已读详情可复用缓存。缓存最多5个入口，过大页面、过期、审核/新结果、断线、切回窗口或无显式filter历史链接会重新加载。
3. 试填未提交批注再切换，应提示确认。取消时留在当前患者；确认放弃后，缓存不得恢复草稿。
4. 多次审核的规则只显示最新主卡，历史结论、时间、置信度和当时批注仍可展开；原文及LIS本次/pending区块正常。
5. 审核提交后批注和进度正确。原文30秒缓存及重复请求合并保持；切换患者后不得出现上一患者的迟到原文。

可选完整磁盘核对：

```bash
python3 check_249_hotfix.py --root /home/admin2/Javert --manifest known-release-after.json
```

额外管理工具和配置不在源码摘要范围。`PATCH_STATE=INSTALLED`只证明磁盘；`LOADED_PROCESS_CODE=UNVERIFIED`不代表失败，也不证明进程已加载。重启与页面验收必须单独完成。现场SQL/网络和冷加载耗时没有在本地代测。

安装后不会自动恢复监测。若需要恢复，使用本包兼容入口，继续原账本：

```bash
bash /home/admin2/releases/Javert-249-Web-performance-v2.1/watch/watch.sh start 300
bash /home/admin2/releases/Javert-249-Web-performance-v2.1/watch/watch.sh status
```

不要在性能v2生效时使用旧日期包的watch入口，它的文件校验清单仍对应旧Web代码。新入口保留原watcher、ledger、检查器和原版本标记的字节，只允许这9个性能文件的已知变化，并按新完整清单校验现场。账本context、盐、队列、完成记录均不迁移、不清空；批跑CODE_FILES也不改变。恢复监测会按原逻辑处理队列/新增输入，已成功且输入未变的患者继续跳过。

## 4. 回滚到本次安装前

先停止本包监测，等待当前患者完成，再停止Web。替换下列备份目录后执行：

```bash
cd /home/admin2/releases/Javert-249-Web-performance-v2.1 &&
bash watch/watch.sh stop &&
bash watch/watch.sh status
# 确认监测已退出，再继续
python3 stop_web.py &&
bash install.sh rollback --services-stopped --backup /home/admin2/Javert/output/web-performance-backup.替换为实际后缀 &&
bash install.sh check &&
bash /home/admin2/start_javert_web_bg.sh
```

回滚前会校验全部备份和当前文件，后续修改或损坏备份会拒绝覆盖。回滚输出`RESTORED_BASELINE=history-dates-v1`，恢复到已安装日期补丁、尚未安装性能补丁的状态。日期功能仍保留。之后如需恢复监测，重新使用原日期包的watch入口；账本无需迁移。不能使用旧性能v1或LIS安装器回滚本包。
