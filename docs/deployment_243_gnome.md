# 243/249 院内运维入口（gnome-243）

维护线：`gnome-243`。243是历史称呼；本会话已确认的院内服务器是`192.168.60.249`、`gnome26`、用户`admin2`。不把历史`192.168.31.243`与249默认当成两台需要同时覆盖的服务器。

2026-09-20完成本地核验；2026-09-21用户回传现场检查截图，确认376个受控运行文件、14个热补丁文件与基线戳全部匹配，differences为空。进程内存代码尚未验证，状态与证据边界见[核验报告](243_249_hotfix_qa.md)。

## 1. 当前拓扑与文件位置

| 项目 | 位置/口径 |
|---|---|
| 应用 | `/home/admin2/Javert`（大写J） |
| 工作台 | `http://192.168.60.249:8090/workbench` |
| 本机SQL | `127.0.0.1:1533/sh_yb_platform`；保持保存配置，不改成142或历史243地址 |
| 模型 | 本机30000，llama.cpp Q8；既有单槽，并发1 |
| 保存配置 | `/home/admin2/releases/243-install-8645a2d.*/effective-config.json`，须唯一匹配有效install-receipt |
| 后台Web助手 | `/home/admin2/start_javert_web_bg.sh` |
| LIS补丁 | `/home/admin2/releases/Javert-249-LIS-hotfix-v1` |
| 独立EDA | `/home/admin2/releases/Javert-249-EDA-v1` |
| 批次/快照 | 应用`output/`下，仅留院内 |

运行身份为`8645a2d + settlement-v1 + sidebar + lis-medical-v1`。`DEPLOY_COMMIT`仍保留基线值，不能据此认为补丁不存在或文件一定相同。

## 2. 只读检查代码

将现场检查包整个文件夹上传到`/home/admin2/releases/Javert-249-code-check`，执行：

```bash
cd /home/admin2/releases/Javert-249-code-check
sha256sum -c SHA256SUMS
python3 check_249_hotfix.py --root /home/admin2/Javert --manifest known-release.json
```

`HOTFIX_FILES=PASS`表示14个热补丁相关文件匹配；`KNOWN_RELEASE_FILES=PASS`表示所列完整已知运行范围匹配。还须查看`deploy_stamp_match`；现场应为true。

工具不读取密码、环境配置、病历、批次名单或日志，不重启服务。`LOADED_PROCESS_CODE=UNVERIFIED`及`loaded_code_verified=false`固定表示工具未验证内存模块，不表示发现版本错误，重启后也不会自动变true。`started_after_code_mtime=false`只说明至少一个受控文件时间较新，先查具体文件再判断是否需重启。输出可用于回传文件状态，不要把现场配置/患者目录带出院。

本地对应命令：

```bash
python3 scripts/check_249_hotfix.py --root "$PWD"
```

## 3. 启动工作台

院内已有助手时直接运行：

```bash
bash /home/admin2/start_javert_web_bg.sh
```

此助手在8090已监听时跳过，因此它是启动命令，不是重启或更新代码命令。缺少助手时使用本分支`scripts/start_javert_web_bg.sh`上传到上述位置，再`chmod 700`、`bash -n`核验。后台tmux允许关闭SSH，不包含机器重启后的自动启动配置。

```bash
curl --noproxy '*' -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/login
```

预期200仅表示登录页面可用；还需原账号登录、查看本批病例和化验。别使用旧教程中的宽泛pkill或演示账号重置来代替定位。

## 4. 化验候选全量处理

首次试跑：

```bash
cd /home/admin2/Javert
bash scripts/lis_batch.sh start 1
bash scripts/lis_batch.sh status
```

`1`是本轮最多进入审计的人数，不是只检查第一个候选。数据未通过者按原因跳过；成功回执与工作台验收后，继续同批剩余候选：

```bash
bash /home/admin2/Javert/scripts/lis_batch.sh resume latest
```

多个未完成批次时用status显示的准确目录代替latest。`start`不写上限会另建一份全量名单；不要用新start代替旧批次恢复。全量名单已冻结，DE后来新增数据需要新批次，不能自动重试旧批次已跳过项。

`PASSED`才是规则执行和SQL同步均通过；`ROUTER_SKIPPED`无普通审计结果；`SKIPPED_LINKAGE`或`SKIPPED_NO_LABS`不算成功。没有可靠回执的STARTED不得手工改状态或删锁绕过。

## 5. 当前取数与排查

- 费用使用`TB_HIS_ZY_FEE_DETAIL`、`STFSJ`，保持结算口径。
- 化验候选走首页KH→住院医疗记录JZLSH→REPORT→INDICATORS；报告与指标按院区、报告号、报告日期连接。
- 正式化验还需本次住院归属校验；关联不清、其他住院与冲突报告另列，不送入审计。
- 费用/文书保留原关联预检，不能把“有报告”当成“完整审计输入已齐”。

只按首页号检查指标：用SSMS连接249的sh_yb_platform，打开[scripts/sql/249_lis_by_syxh.sql](../scripts/sql/249_lis_by_syxh.sql)，替换开头SYXH并整份执行。该查询会显示同卡全部报告及日期标记；同卡候选数不等于正式审计化验数。

## 6. 下班统计

```bash
bash /home/admin2/releases/Javert-249-EDA-v1/eda.sh
```

详见[EDA教程](../delivery/audit_eda/README_zh.md)。同时统计本批及工作台全量存量，按患者+规则最新结果去重；全量含本批，不可相加。违规金额缺少结构化记录时保持待核定；命中患者整次住院结算费用只是参考值。

## 7. 更新、回滚与机器故障

现有v1的安装/回滚严格按[LIS补丁教程](deployment_249_lis_medical.md)。`install.sh check`只是预检，安装是`install.sh install`；回滚填本次实际BACKUP目录，保留结算、侧栏和历史结果。

维护者读[发布Cookbook](243_release_cookbook.md)。不要从FP8或当前脏工作树打包覆盖，不再采用旧演示教程的裸tar覆盖、全量ETL覆盖或灌演示患者流程。源库由DE维护，应用热补丁不灌库、不建索引。

断电/黑屏等机器级历史故障参见[2026-07-15归因记录](243开机故障归因_20260715.md)，仅在现场同型号/同配置核实后适用。历史自启配置是否仍有效需现场检查，不能据旧记录保证当前Web自启。
