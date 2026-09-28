# 249 工作台性能补丁 v1

将整个 `Javert-249-Web-performance-v1` 文件夹上传到 `/home/admin2/releases/`，以应用所属账号 `admin2` 执行。无需安装依赖、联网下载或运行 SQL。

本包适用于 `8645a2d + settlement-v1 + sidebar + lis-medical-v1` 已交付基线。版本戳仍为 8645a2d，本包另外记录性能补丁摘要。仅修改 5 个源码/模板文件；保留配置、模型、规则、SQLite、SQL Server、批次账本与历史专家批注。

## 1. 在线只读检查

```bash
cd /home/admin2/releases/Javert-249-Web-performance-v1
sha256sum -c SHA256SUMS
bash install.sh check
```

所有校验都通过且输出 `PATCH_STATE=READY` 才继续。`INSTALLED` 表示已是本包，不用重复安装。任何 `STOP` 均先保留现场，不绕过摘要闸。若提示旧文件不符，说明还存在其它现场补丁，需要重新匹配版本，不能直接复制覆盖。

## 2. 维护窗口安装

先检查原批次状态，等待本轮批跑/单患者/取数结束，或按既有运维流程暂停可优雅暂停的 watch。单次 `lis_batch` 无优雅 stop，不要 kill 审计进程。

```bash
bash /home/admin2/Javert/scripts/lis_batch.sh status
```

确认任务不再运行后：

```bash
cd /home/admin2/releases/Javert-249-Web-performance-v1
python3 stop_web.py
bash install.sh install --services-stopped
bash install.sh verify
bash /home/admin2/start_javert_web_bg.sh
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/login
```

`stop_web.py` 只对该账号、该应用目录的唯一 `javert web` 发送 TERM；无匹配不会猜测 PID，多匹配拒绝停止。若由 systemd 自动重启，需要按既有服务管理方式停服务后重试。安装器还会检查批跑锁和同目录活动进程；`--services-stopped` 表示操作者已确认其它启动方式的任务也停止了。

保存安装输出的 `BACKUP=/home/admin2/Javert/output/web-performance-backup.<实际后缀>`。安装中途写失败会恢复已改文件。若安装失败，先 `bash install.sh check` 确认状态，再用既有启动助手恢复 Web。启动助手复用已保存配置；本补丁不读取或重写 `.env` 和 `effective-config.json`。

## 3. 页面验收

登录页应为 200。原账号登录后打开一个本批患者：详情先出现，侧栏独立加载且每页最多 50 人；住院号搜索覆盖全名单，翻页和更新时间排序正常。上海模式继续隐藏全局主诊/费用筛选；主诊费用在当前患者详情查看。

核对一条多次审核的规则：最新主卡证据正常，展开历史仍有结论、置信度、时间与当时专家批注。原文内“本次住院”与“未用于本次审计”的 LIS 区块保持分离。提交审核后确认计数和批注；使用原文按钮及缓存再次查看。

可选完整磁盘核对：

```bash
python3 check_249_hotfix.py --root /home/admin2/Javert --manifest known-release-after.json
```

`PATCH_STATE=INSTALLED` / 文件摘要仅证明磁盘。`LOADED_PROCESS_CODE=UNVERIFIED` 不会被本地测试或 HTTP 200 自动改成已验证。页面体验和实时 SQL Server 耗时应以本机实际验收为准。

恢复原批次时仍使用原账本/原 resume 流程；不新建名单、不重算历史。本包不改变现有批跑 `CODE_FILES` 指纹所覆盖的源码。

## 4. 回滚

同样先停止 Web 和审计/取数任务，然后使用安装时记录的准确备份目录：

```bash
cd /home/admin2/releases/Javert-249-Web-performance-v1
python3 stop_web.py
bash install.sh rollback --services-stopped --backup /home/admin2/Javert/output/web-performance-backup.替换为实际后缀
bash install.sh check
bash /home/admin2/start_javert_web_bg.sh
```

回滚校验全部备份及当前文件后才写入；后续改动或损坏备份会拒绝覆盖。回滚后应为 `PATCH_STATE=READY`，新增模板被移除，旧 LIS/结算功能、配置及数据保留。不能运行旧 LIS 安装器来回滚这个性能补丁。
