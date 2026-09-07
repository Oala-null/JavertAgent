# 243 真数据单患者：最小修复、发布与验收

本版只修医院真实数据接入，基线是 gnome-243 的 3da4d80；不合入 fp8，不改变规则状态、模型或肿瘤数据库。
本地测试/推送/打包不代表已经在院内安装或通过真实患者验收。

## 0. 这次修的是什么

| 数据 | 院区内关联方式 |
|---|---|
| 首页 TB_BA_SYJBK | 输入 SYXH，取得 BAH、KH、KLX |
| 出院小结 TB_CIS_LEAVEHOSPITAL_SUMMARY | BAH 唯一匹配，同时核对 KH/KLX、入出院时间 |
| 住院登记 TB_HIS_ZY_ADM_REG | 小结 JZLSH，唯一记录、同卡、入院时间一致 |
| 费用 TB_HIS_ZY_FEE_DETAIL_FS | 小结 JZLSH，不按出院时刻截断费用 |
| 扩展文书 TB_CIS_MEDICAL_DOCUMENT | BAH，不使用其实际承载病案号的 JZLSH |
| 首页诊断/手术 TB_BA_SYZDK / TB_BA_SYSSK | 首页 SYXH |
| 检验/检查 TB_LIS_* / TB_RIS_* | 本次 JZLSH；不以同卡全历次兜底 |

所有查询限定真实院区。两种源时间允许最多60秒差异，仅用于已唯一匹配BAH的交叉检查，绝不按“最近时间”选择住院。
内部统一使用首页SYXH，因此不能把长就诊流水号当作新版命令的输入。
旧测试模式仍默认legacy；下面两个启动器显式启用shanghai，并排除测试院区0001/0003。

## 1. 本地发布规则

在干净的 gnome-243 工作目录完成测试、提交并成功推送；不在fp8脏工作目录打包。

```bash
git push -u origin gnome-243
python3 scripts/gnome_release.py pack --repo . --output /tmp/javert-243-delivery
```

工具核验origin/gnome-243与HEAD一致，输出完整运行包、外层SHA256和同提交安装器。
同一次发布认准同一SHA，不用移动中的分支名判断机上版本。

包内：完整src、必要configs（规则/知识资产）、scripts、data/router、pyproject.toml、uv.lock、本文。
明确排除：.env、configs/llm.yaml、configs/hospital_config.yaml、.venv、模型、患者数据、SQLite和日志。
这是代码更新，不是清空整个Javert目录。原启动脚本及未列入旧发布清单的现场文件不会被删除。

## 2. 传输和安装：只在243上执行

通过院方允许的文件上传通道将三个文件送到跳板机，再用现有SSH/SFTP会话传到243的同一临时目录：
运行包.tar.gz、同名.sha256、gnome_release.py。医院数据库/患者文件不往外传。
本机直连医院或绕过堡垒机不是本手册的前提。

先记录非秘密版本信息（不要cat .env）：

```bash
cd /home/admin2/Javert
hostname
test ! -f DEPLOY_COMMIT || head -1 DEPLOY_COMMIT
git rev-parse HEAD 2>/dev/null || true
git diff --stat -- src configs scripts 2>/dev/null || true
.venv/bin/python -c 'import pandas,pyodbc,httpx,fastapi,sqlalchemy; print("运行依赖可导入")'
```

确认机上属于gnome历史版本并保留本次手工修复记录；未知版本不要直接覆盖。真实数据库当前由现场配置决定，已知源是192.168.60.249:1533/sh_yb_platform，不能让旧文档的127.0.0.1或142默认值覆盖它。

在上传目录核验包。将文件名替换为这次收到的实际名字：

```bash
sha256sum -c javert-243-提交号.tar.gz.sha256
python3 gnome_release.py verify javert-243-提交号.tar.gz
```

用现机已有方式停止Web和批跑，暂停可能自动拉起它们的cron/监控。不要套62的systemd单位，不要pkill所有Python，也不要停止SQL Server或LLM。
原Web有效配置可能来自启动脚本/export，而不仅是.env：保持当前已能连接医院数据库的终端环境，保留原启动脚本；新终端需加载同一环境。

服务已停后才安装：

```bash
python3 gnome_release.py install javert-243-提交号.tar.gz \
  --target /home/admin2/Javert --services-stopped
```

记下输出的回滚备份路径。安装器不启动服务、不运行uv sync/pip、不改任何数据库。
首次安装没有旧发布清单时，仅覆盖包内代码，不删除未知现场文件；后续删除旧清单中已退役的代码前会备份。
.env和上述现场文件字节保持不变；本次不会替换SQL密码、session secret或LLM模型别名。

## 3. 先只读预检，不调用LLM

继续使用刚才可连接数据库的同一个终端：

```bash
cd /home/admin2/Javert
export JAVERT_HUB_LINKAGE_MODE=shanghai
export JAVERT_HUB_HOSPITAL_CODE=AYY8BNRF
export PYTHONPATH="$PWD/src"
read -r -p '输入病案首页SYXH：' PATIENT
bash scripts/run_243_patient.sh "$PATIENT" precheck --check-only
```

输出费用、文书、诊断、手术、检验、检查行数及警告。
没有小结、关联多匹配、卡号冲突、时间冲突、费用空或文书空时，明确失败，不写CSV，不会拿同卡其他住院补齐。

如果仍然拿着七月那张没有对应费用的首页，不要反复重跑。可以限量寻找一个有首页/小结/费用且通过全部取数检查的候选：

```bash
PATIENT=$(.venv/bin/python scripts/find_243_patient.py --max-candidates 20) || unset PATIENT
```

诊断写stderr，stdout只返回选中首页号供院内shell接收；无通过者则返回失败。它不调用LLM、不修改数据库，只检查最近最多20个候选，不代表全院数据覆盖。
后续命令会拒绝空PATIENT，防止失败后误跑旧号码。

## 4. 跑一个患者

```bash
TAG="one-$(date +%y%m%d%H%M%S)"
bash scripts/run_243_patient.sh "$PATIENT" "$TAG"
```

阶段：
1. 只读验证SQL源、现有结果库表、LLM连接和/models中的实际模型别名。
2. 验证关联并写全套私密CSV快照（目录0700/文件0600），失败清理未完成暂存。
3. 只使用该快照执行ready规则+Router，单患者concurrency=1。
4. 在同一目录保留etl.log、audit.log和preflight.json，检查failed与mssql_sync/pending。

SQL是否双写沿用现场JAVERT_SQL_ENABLED，不强制切到其他结果库；false会明确提示“仅SQLite”。
不新增SQL Server schema：shanghai模式下结果表只读检查失败就停止；不得照旧手册先ensure-schema建库。
SQLite使用原版本既有初始化流程，本次不引入新schema版本。
tag必须1-20位ASCII字母数字、下划线或连字符。每次使用新tag；不要直接sync所有历史pending。

## 5. 工作台启动与核对

先确认旧Web/定时拉起已停。使用相同SQL/LLM/session环境启动：

```bash
cd /home/admin2/Javert
bash scripts/run_243_web.sh
```

确认成功后，再按院内原有进程管理方式后台运行这个启动器；不要直接用62的unit。
保持8090端口原监听方式。浏览器地址是你通过堡垒机/隧道访问的243地址加 `:8090/workbench`；
若在243本机或已建立本地转发，则使用对应的 `http://127.0.0.1:8090/workbench`。
SQL的192.168.60.249不是工作台地址，不要把数据库IP当Web服务器。

新版shanghai模式原文页直接使用共享Hub关联，不读取旧测试CSV/overlay；
诊断手术概览也使用同次数据。历史pending自动回灌在此模式关闭，避免Web重启后把旧测试结果批量写到新结果库。
这不影响本次CLI即时双写；有pending需按本次tag分析，不能泛化补漏。

验收：同tag有本次结果、failed=0；若启用双写则结果在正确SQL库出现；工作台同首页原文费用/文书与快照一致。
终端返回0本身不等于这些条件都满足；没有候选规则也不算LLM端到端验收。

## 6. 错误怎么定位

| 输出 | 优先检查 |
|---|---|
| LINK_HOME/SUMMARY/ADMISSION_NOT_UNIQUE | 0条是缺数据，2条是关联有歧义；不靠换院区码/改编号修 |
| LINK_IDENTITY_* | BAH/KH/KLX不一致或缺失；卡类型0是有效值 |
| LINK_TIME_* | 非真实时间/日期格式/住院时间冲突；不要伪造文书时间 |
| SOURCE_FEES_EMPTY | 当前住院无费用；不使用其他住院费用 |
| SOURCE_NOTES_EMPTY | 本次没有有效正文或小结内容 |
| SOURCE_*_QUERY_FAILED | 定位到具体源，再核对字段/权限/超时，不输出凭据 |
| LINK_FEE_DUPLICATED | FS或EXT连接扩行，避免重复计费；不要直接去重掩盖来源问题 |
| NOTE_TIME_MISSING | 文书日期占位保留为空，依赖时序的审计需人工复核 |
| LABS_EMPTY / EXAMS_EMPTY | 仅本次JZLSH无结果，尚不证明全院无数据，不以KH跨住院拼接 |
| LLM_MODEL_NOT_SERVED | .env/原启动脚本的模型别名与本机/models不一致，不换模型文件 |
| RESULT_SCHEMA_CHECK_FAILED | 原结果库/现有表不匹配；停止，不自动迁移 |

截图只发阶段、错误码和计数。日志与原始CSV留在医院，不能把包含患者正文的目录上传Git。

## 7. 回滚

再次停止Web/批跑，使用安装时打印的确切备份目录：

```bash
python3 /上传目录/gnome_release.py rollback /home/admin2/Javert/.243-backups/实际备份名 \
  --target /home/admin2/Javert --services-stopped
```

恢复的是受控代码及版本戳，不会回滚患者数据、SQLite、SQL结果或env。
随后按原启动方式启动旧Web；既有审计结果保留，不因代码回滚而DELETE。
新shanghai启动器在旧版本中可能不存在，不能继续沿用新版启动命令。
