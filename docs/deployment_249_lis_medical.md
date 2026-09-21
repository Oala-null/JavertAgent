# 249 LIS医疗记录路径热补丁：操作教程

当前维护源码已回收到gnome-243，逐文件证据见[热补丁核验](243_249_hotfix_qa.md)。下文描述原v1操作，原签名包没有修改。用户此前反馈安装及首例成功；全量完成情况以现场status/回执为准。日常入口和代码核验见[运维手册](deployment_243_gnome.md)，手动统计见[EDA教程](../delivery/audit_eda/README_zh.md)。

适用机器：院内Ubuntu `192.168.60.249`（gnome26，登录用户admin2）。应用目录 `/home/admin2/Javert`，工作台8090，数据库由原保存配置连接。Windows跳板机只负责中转文件和打开SSH/SSMS。

适用版本：`DEPLOY_COMMIT=8645a2deb1c4c99921a65a3ab01754275ec2ac4e`，已安装此前的DETAIL结算费用补丁和侧栏补丁。安装器会检查文件摘要；版本不同会停止，不覆盖未知改动。

## 1. 这次具体改什么

候选名单使用你已经查通的路径：

`TB_BA_SYJBK → TB_YL_ZY_MEDICAL_RECORD → TB_LIS_REPORT → TB_LIS_INDICATORS`

选名单不经过出院小结，不按费用或月份提前删掉候选。你提供的截图中，3,054份报告对应633个首页；实际运行以当时数据库查询为准，不固定写死633，也不截断成最近1500个。

化验的实际读取也改用医疗记录的就诊号，审计快照与Workbench共用这段代码。报告与指标仍按院区、报告号、报告日期三个字段关联。

同卡多次住院时，按本院、卡号/卡类型、正常医疗记录及入出院日期核对归属；日期粒度允许不同系统时分秒精度不同。若有多个日期候选且病案号可以进一步区分，则用病案号消歧。仍不能唯一归属、属于其他次住院或报告键冲突的，单独显示为未纳入本次审计。单一候选首页本身不被当作住院归属证明。

费用继续使用 `TB_HIS_ZY_FEE_DETAIL`，时间继续采用 `STFSJ`。原文、费用等其他数据仍保留原关联预检；这些数据缺失会在逐人处理时列出原因，不影响全量候选名单的生成。原有侧栏修复、账号、密码、模型、规则和历史审计结果保留。

**全量处理不等于保证每个首页都审计成功。** 全部候选都会进入名单；缺临床数据、化验不能确认归属、Router跳过或审计失败分别统计，不能把这些状态算成成功。

## 2. 文件放在哪里

推荐把已经解压好的 `Javert-249-LIS-hotfix-v1` 整个目录，通过Windows跳板机的MobaXterm/SFTP上传到249：

```text
/home/admin2/releases/Javert-249-LIS-hotfix-v1/
    install.sh
    installer.py
    manifest.json
    SHA256SUMS
    README_zh.md
    START_HERE.txt
    files/...
```

不要把 `files/` 中的文件直接拖到正在运行的应用目录覆盖。让安装器处理备份、版本检查和替换。

若MobaXterm上传时多套了一层目录，先查看文件，确保下面目录里能直接看到 `install.sh`：

```bash
ls -l /home/admin2/releases/Javert-249-LIS-hotfix-v1/install.sh
```

### 如果选择上传ZIP

将补丁ZIP及同名 `.zip.sha256` 放进249的 `/home/admin2/releases/lis-upload/`，在249 SSH终端执行：

```bash
cd /home/admin2/releases/lis-upload
sha256sum -c Javert-249-LIS-hotfix-v1.zip.sha256
```

必须显示 `OK`。如果 `/home/admin2/releases/Javert-249-LIS-hotfix-v1` 尚不存在，再解压：

```bash
test ! -e /home/admin2/releases/Javert-249-LIS-hotfix-v1 && \
python3 -m zipfile -e Javert-249-LIS-hotfix-v1.zip /home/admin2/releases
```

这套命令不依赖unzip，也不用在Windows重新打包。ZIP内使用英文文件名，中文在文件内容中；代码采用UTF-8和Linux换行。你习惯在Mac先解压再上传也可以，后续仍需校验内部文件。

## 3. 安装前检查

以下命令都在249 SSH终端运行，不是在SSMS中运行。

先关闭Workbench浏览器页面，避免长连接拖住Web退出。等待原有批跑或单患者任务结束；不要执行 `pkill python`、不要强杀模型或数据库服务。

```bash
cd /home/admin2/releases/Javert-249-LIS-hotfix-v1
sha256sum -c SHA256SUMS
bash install.sh check
```

预期：所有内部文件 `OK`，然后出现 `SOURCE_CHECK=PASS` 和 `PREFLIGHT=PASS`。

这一步只校验版本、包文件、已有配置和四表字段可读，不安装、不停止Web、不写中台表。它不代表某个真实患者已经取数成功。

遇到版本不符、混合版本、字段缺失或配置不是已确认的249目标时，保留STOP信息。不要删检查条件或直接复制源码覆盖。

## 4. 安装热补丁

```bash
cd /home/admin2/releases/Javert-249-LIS-hotfix-v1
bash install.sh install
```

安装器会：

1. 检查批跑/单患者锁和活动进程。
2. 确认8090属于这套Javert及原保存配置，正常停止Web。
3. 备份本次受控文件，原子替换后，用原配置后台启动Web。

预期输出包括：

```text
PATCH_INSTALLED=PASS
BACKUP=/home/admin2/Javert/output/lis-backup.实际随机目录
WEB_HTTP_200=PASS
```

**把BACKUP完整路径记下来。** 它用于本次回滚，不能拿旧结算补丁的备份代替。

如果显示 `ALREADY_INSTALLED`，表示代码已经是本包版本，安装器不重复覆盖或重启；仍需继续检查Web和真实取数。

如果Web正常退出超时：本轮不会安装文件。保持浏览器页面关闭，稍后检查8090，再重新运行安装。不要强杀未知PID。

如果文件已安装但Web健康检查失败：查看安装器指出的Web日志，并按第10节回滚；不能只看到 `PATCH_INSTALLED` 就认为上线完成。

## 5. 检查新路径是否生效

```bash
cd /home/admin2/Javert
.venv/bin/python scripts/check_243_lis.py
```

预期包含：

```text
LIS_SOURCE=medical-record-v1
CANDIDATE_HOMES=实际首页数
```

数据与截图同一时点时，候选首页数应与你的名单相符。DE继续同步后，数量可能变化。

也可隐藏输入一个首页号，单独看新路径：

```bash
cd /home/admin2/Javert
.venv/bin/python scripts/check_243_lis.py --patient
```

输入不回显。脚本先独立读取LIS，再检查完整审计取数；即使费用或小结等其他输入未通过，也能分别看到化验关联情况。

- `NEW_LIS_ASSIGNED_ROWS`：能归属本次住院的指标行数。
- `candidate_reports`：通过原四表路径关联到的报告数。
- `assigned_reports`：可用于本次住院的报告数。
- `pending_reports`、`reasons`：未纳入本次审计的报告数及原因。
- `AUDIT_SOURCE_PREFLIGHT=PASS`：完整审计输入的取数预检通过，尚未启动审计。

这里报告数、指标行数、首页数是不同单位。名单、患者号和原文留在院内，只反馈计数和错误码即可。

## 6. 用完整名单先试跑1个患者审计

```bash
cd /home/admin2/Javert
bash scripts/lis_batch.sh start 1
```

这会先冻结**完整候选名单**，再从头逐人取数；数据不全者记录并跳过，最多启动1个实际患者审计后暂停。它不是把名单截成1人。

脚本会显示启动日志路径，随后状态里会显示本次批次目录与TAG：

```text
/home/admin2/Javert/output/lis-batch.实际随机目录
st-lis-运行日期-随机后缀
```

查看进度：

```bash
bash /home/admin2/Javert/scripts/lis_batch.sh status
```

提交成功后可以关闭SSH窗口，tmux里的任务仍会运行。没有配置机器重启后的自动启动。

如果所有候选都被跳过，任务会遍历完名单并给出各类计数，不会假称已经审计成功。若出现 `FAILED_...`，本轮会停下，保留已完成结果及剩余名单。

## 7. 验收工作台

打开原地址：`http://192.168.60.249:8090/workbench`，使用原账号密码。安装后用Ctrl+F5刷新一次。

根据状态输出的TAG查本次审计结果，打开“检验记录”：

- “本次住院 · 检验化验”是正常审计使用的指标。
- “关联检验 · 归属待核对或其他次住院”单独折叠显示，明确未用于本次审计。
- 报告键或卡信息存在冲突时，不展示可能错归属的明细值，只给出待核查提示。

核对试跑结果为 `PASSED`，化验快照有数据，SQL双写完成，页面对应本次住院检验可见。`ROUTER_SKIPPED` 表示没有选中规则，不会产生普通审计结果行，不能代替此项验收。

页面和后台缓存有短TTL，新同步数据会在后续读取时刷新。**旧审计结果不会因为页面刷新而重新计算**；本次新标签的新审计才使用重新抽取的快照。

## 8. 同一批次继续跑全部候选

试跑确认后，不要再次执行 `start`，用原批次继续：

```bash
bash /home/admin2/Javert/scripts/lis_batch.sh resume latest
```

`latest` 仅在恰好存在一个未完成批次时生效。若提示批次不唯一，先 `status`，然后使用本次真实目录：

```bash
bash /home/admin2/Javert/scripts/lis_batch.sh resume /home/admin2/Javert/output/lis-batch.实际随机目录
```

恢复会沿用原名单和TAG，核验已完成患者回执后跳过他们，继续剩余候选，不重新审计已完成者。

如果你已经完成单例验收，希望另起一个新的全量批次，命令是：

```bash
bash /home/admin2/Javert/scripts/lis_batch.sh start
```

不写人数就是全量。**新的start会重新查询名单、生成新TAG，并可能再次审计以前批次中的患者。** 同一批续跑用resume。

## 9. 状态和断点恢复怎么理解

| 状态 | 含义 |
|---|---|
| PENDING | 名单内尚未处理 |
| STARTED | 正在处理或中断待核对 |
| PASSED | 规则执行完成且SQL双写通过，有独立回执 |
| ROUTER_SKIPPED | 未选中审计规则，未生成普通结果行 |
| SKIPPED_LINKAGE | 住院关联、费用或文书等输入未通过 |
| SKIPPED_NO_LABS | 关联到报告，但本次快照没有可确认归属的化验，未启动审计 |
| INVALID_ID | 首页号格式不符合执行入口 |
| FAILED_SOURCE | 取数、运行环境或快照失败，本轮停止 |
| FAILED_AUDIT | 审计或双写未通过，本轮停止 |

`finished=True` 表示整份名单处理结束，**不等于所有患者都是PASSED**。看各状态计数。

恢复时使用独立JSON回执，不以日志中出现“成功”字样为准。若有 `INFLIGHT_UNCERTAIN_INDEX_...`，表示某个已经开始的患者没有可靠完成凭据，脚本不会自动重跑。保留目录和日志核对，不能手工把状态改成PASSED，也不要删锁或进度文件绕过检查。

完整日志在批次目录的 `patient-序号.log`；名单、回执和summary包含患者标识，仅留院内。控制台状态只显示TAG、序号、计数和错误码。代码/规则或关键配置变化后，旧批次恢复会停止，避免同一TAG混用执行口径。

DE后续补齐数据不会自动重新处理已跳过的记录；需要重新验证后启动新批次。不同批次保留独立历史。

## 10. 回滚本次LIS补丁

先确认没有批跑或单例任务在执行，再回到补丁目录，填第4节记录的真实BACKUP：

```bash
cd /home/admin2/releases/Javert-249-LIS-hotfix-v1
bash install.sh rollback /home/admin2/Javert/output/lis-backup.实际随机目录
```

回滚会正常停止已确认Web，只恢复本次LIS改动，保留此前DETAIL/STFSJ、侧栏修复、配置、模型与历史审计结果，再启动Web。若文件已有其他后续改动或备份校验失败，会停止，不覆盖未知修改。

回滚不会撤销已经产生的审计结果；回滚后不要恢复未完成的新LIS批次。可保留其目录供核查。

## 11. 原来的Workbench启动脚本

原来的 `/home/admin2/start_javert_web_bg.sh` 可以继续使用，位置不变：

```bash
bash /home/admin2/start_javert_web_bg.sh
```

本次交付文件夹提供了它的副本。若249没有该文件，把副本上传到 `/home/admin2/` 后 `chmod 700` 即可。该脚本只负责启动：8090已有服务时会跳过，不等于重启或清缓存。安装器完成安装/回滚时会自行重启Web。

## 12. 常见STOP处理

- **有任务持锁**：查看任务进度，等待完成；不删锁文件、不重复启动。
- **受控文件或依赖版本不同**：保留提示和当前版本，核对补丁叠加状态；不跳过摘要检查。
- **LIS字段缺失**：把缺少的表/字段名交DE核对；本包不在中台建表或改字段。
- **候选数为0**：此时选人已不接小结/费用，先用交付的原E口径名单SQL对照同一实例/院区。
- **候选有数但全部SKIPPED**：看原因计数和单患者只读检查。候选关系不自动等于完整审计输入或本次住院归属。
- **SQL双写失败**：先恢复结果库连接并核对已写结果，resume会保留失败记录并继续未处理者；不会自动重跑失败患者。
- **页面仍空**：先看新路径只读检查的assigned/pending，再检查新TAG、快照和页面缓存；不要把待核查报告当成本次住院已验证指标。

本地测试、包校验和登录页HTTP200，都不等于院内真实病例验收完成。本包不含患者数据、账号密码、环境配置或Mac虚拟环境。
