# 243维护者：已交付热补丁的代码与发布

本维护线是`gnome-243`，不合入FP8。当前院内对应`8645a2d`正式包叠加结算、侧栏、LIS三项修复；原始交付包不可原地重写。日常操作见[运维入口](deployment_243_gnome.md)。

## 1. 什么才算同步

1. 本地源码与`deploy/249/known-release.json`中运行文件摘要一致。
2. 已提交、已推送是独立状态；工作树改好了不代表远端Git已更新。
3. 现场运行同一只读检查并得到文件匹配，才能确认现场磁盘代码。
4. 进程启动/配置仍需核对；DEPLOY_COMMIT、登录HTTP200、截图均不能单独证明全部运行代码一致。

```bash
git branch --show-current
git status --short
python3 scripts/check_249_hotfix.py --root "$PWD"
```

检查目标必须是gnome，不是FP8。现场未知不得填写“已同步”；本次事实见[核验报告](243_249_hotfix_qa.md)。

## 2. 已交付代码的维护位置

| 内容 | 位置 |
|---|---|
| 正式业务代码 | src/、scripts/中已恢复的补丁文件 |
| 不可变v1摘要 | delivery/lis_hotfix/v1-manifest.json |
| 完整已知发行摘要 | deploy/249/known-release.json |
| v1安装器维护源 | delivery/lis_hotfix/installer.py、install.sh |
| 安装前代码夹具 | delivery/lis_hotfix/baseline-code.json.gz；只含受控代码，没有配置/患者 |
| EDA独立工具 | delivery/audit_eda/ |
| Web后台助手 | scripts/start_javert_web_bg.sh，现场置于/home/admin2/ |

热补丁原样回收不改变规则、模板、模型、依赖或数据库schema。后续若修改任何v1运行文件，不能继续称为同一份v1。

## 3. 验证与构建

直接相关测试命令见核验报告。先证明费用口径、医疗记录LIS关联、同卡多次住院隔离、快照范围、独立回执、断点恢复和回滚，再生成新交付。

仅复刻已交付v1运行源码时，可指定一个不存在的输出目录：

```bash
python3 scripts/build_243_lis_hotfix.py /tmp/javert-v1-rebuild-独立目录
```

工具会拒绝源码摘要变化及覆盖已有交付。重建ZIP的时间戳/压缩字节可能不同；不能把它称为原ZIP的相同SHA，应保留新的外层SHA。既有原ZIP与其SHA继续有效，不覆盖。

旧`scripts/gnome_release.py`是完整发行工具，会要求干净已推送的gnome HEAD。当前增量脚本仍校验原8645a2d基线；若将来发新HEAD完整包，必须同时设计新基线闸、配置保存和回滚，不能直接将当前完整包覆盖院内并假设增量批跑兼容。

## 4. 院内单例与全量验收

使用已保存配置和现有入口，不临时改SQL/LLM地址。安装过程按[LIS教程](deployment_249_lis_medical.md)；完成后：

```bash
cd /home/admin2/Javert
.venv/bin/python scripts/check_243_lis.py
.venv/bin/python scripts/check_243_lis.py --patient
bash scripts/lis_batch.sh start 1
bash scripts/lis_batch.sh status
```

检查首例`PASSED`、正式化验快照非空、SQL同步成功和工作台显示，再`resume latest`继续同批全量。原`scripts/find_243_patient.py`和结算500批入口有不同选人条件，不能代替LIS四表候选名单。

## 5. 原有关联仍适用于哪些数据

| 数据 | 当前路径 |
|---|---|
| 费用 | 首页/小结/登记唯一关联→结算DETAIL，收退费时间STFSJ |
| 文书 | 原BAH/就诊链路及同院身份、时间检查 |
| 化验 | 首页KH→医疗记录JZLSH→REPORT→INDICATORS；独立核对本次住院归属 |
| 页面 | 正式化验与归属待核对/其他住院分开 |

LINK_HOME_NOT_UNIQUE、LINK_SUMMARY_NOT_UNIQUE、LINK_ADMISSION_NOT_UNIQUE等是输入关系问题；不通过只取第一行或放宽KH解决。候选有报告仍可能缺少完整审计输入。

## 6. 回滚

回滚使用新LIS包记录的实际BACKUP，不能用旧完整包覆盖撤掉所有补丁：

```bash
cd /home/admin2/releases/Javert-249-LIS-hotfix-v1
bash install.sh rollback /home/admin2/Javert/output/lis-backup.实际目录
```

安装器检测进行中的任务和后续代码变化；不强杀、不绕过。不继续用新LIS批次恢复到旧LIS代码。账号、配置、模型和历史结果不随此回滚删除。
