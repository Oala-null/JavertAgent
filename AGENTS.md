# AGENTS.md（Javert · gnome-243）

本文件只保存开发硬边界与导航；架构、操作和验证事实见docs。

## 协作与分支

- 全程中文；开工前说明假设、范围与可验证的完成条件。
- 院内243/249维护线是`gnome-243`。用户要求维护此线时，不切回或修改FP8分支，不夹带2C/模型/新知识库功能。
- 保留用户已有修改和未跟踪资料；禁止无范围`git add -A`、清理或覆盖环境文件。
- 最小改动，修bug先用测试/可复现命令证明，再修并复验；不顺手改规则和相邻功能。
- 多步骤变更按OpenSpec proposal/design/spec/tasks实施，完成时严格校验；仅用户要求时归档。
- Java/Vue相关工作遵守用户级java-vue-development-standards技能，项目实际Python/FastAPI/原生JS/SQL Server栈优先。

## 环境与隐私

- 243为历史称呼；已确认院内地址是249、主机gnome26，应用目录大小写为`/home/admin2/Javert`。不可凭两个编号推断为两台独立机器。
- 不把62或142的配置/部署授权套到院内环境；142 `sh_yb_platform`只读，索引和DDL交DE/DBA。
- 自有hub写入必须显式指定库名并命中owned白名单；142仅TP_data_hub允许此类写入，院内同名库需另行授权。肿瘤专家维护库仅`知识库_work`，不能用业务/中台库代替。
- 中台重灌/重建索引不是热补丁安装步骤；索引SQL须连接库名与HUB_DATABASE双重一致，142中台DDL交DE/DBA。
- 配置优先级为JAVERT_*进程环境、configs/llm.yaml、代码默认；现场保存配置和实际进程值优先于旧教程。
- `.env`、`243_config.env`、effective-config.json、密码、session secret、患者原文和名单不入Git、不回显、不出院。
- 含患者数据的目录0700、文件0600；测试只用SYNTHETIC/CASE等去标识夹具。
- 不读取全量`/proc/*/environ`到输出、不做宽泛进程kill；运行中批次的代码/规则指纹不可偷偷更换。

## 取数与审计

- `src/javert/data/hub_source.py`是TB_*到内部契约的唯一映射；ETL和工作台复用。
- 上海模式须显式配置`hub_linkage_mode=shanghai`和院区；默认legacy保持原行为。
- 费用读取`TB_HIS_ZY_FEE_DETAIL`，时间`STFSJ`，不是FS/FYFSSJ；退费按共享映射处理，不重新按名称拼金额。
- LIS候选：首页KH→医疗记录KH/JZLSH→REPORT→INDICATORS；报告/指标完整键为YLJGYQDM+BGDH+BGRQ。候选名单不接小结、费用或日期预筛。
- 本次住院正式LIS还需验证卡信息、正常医疗记录、住院日期和唯一归属。其他住院、歧义、缺失进入pending，不进入审计CSV，不通过只按KH连接放宽。
- 费用/文书等仍有原住院预检；有候选报告不等于可以完成审计，跳过须带原因。
- 上海审计须验证DETAIL和LIS快照来源、摘要、范围、行数；新结算批次tag使用`st-`前缀。
- 默认批跑使用Router及并发1；不靠`--rules`绕过abandoned规则，不自动重算历史结果。
- `PASSED`需独立审计回执与SQL同步计数成立；日志文本、退出码、名单finished均不能单独代表成功。
- 上海Web启动不自动执行DDL，不自动回灌全部历史pending。
- 不改规则状态/关键词/模板后遗忘Router：修改后运行scripts/build_rule_mapping.py并核对YAML与索引。
- precheck仅对已声明的确定性模式生效；verdict_gate只对VIOLATION降级，不凭空升级。
- API/SSE字段修改只加不删不改名，同步实际调用方契约。此维护线不能宣称FP8/62新契约已上线。

## 发行与版本证明

- v1基线戳仍为8645a2d；完整身份是基线+结算/侧栏/LIS补丁摘要，不是单独DEPLOY_COMMIT。
- `deploy/249/known-release.json`固化已交付运行范围；核验命令见运行手册。额外管理工具/文档与受保护配置不算服务器字节一致范围。
- 已签名交付包不可原地改写。`scripts/build_243_lis_hotfix.py`仅在v1源码摘要一致且输出目录不存在时重建，不能用v1名发布新逻辑。
- 不从当前工作树裸tar覆盖院内；新完整发行须重新验证基线闸、安装器、配置和恢复流程，不能把旧v1脚本直接套到新DEPLOY_COMMIT。
- 区分本地工作树匹配、已提交/推送、现场磁盘匹配和进程已加载代码；连接失败保持UNKNOWN，不以本地测试/截图冒充实时现场核验。
- 后台tmux只保证关闭SSH后继续，不代表机器重启自启。start新名单与resume原批次不能混用。
- 只读EDA不调用LLM、不改库。人数按首页、命中按患者×规则最新结果；整次住院净费用不是违规金额，未知金额不填0。

## 常用验证

```bash
.venv/bin/javert list
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/check_249_hotfix.py --root "$PWD"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest tests/test_lis_medical_path.py tests/test_lis_cohort_batch.py tests/test_lis_surface.py tests/test_lis_hotfix_installer.py tests/test_249_hotfix_sync.py -q
openspec validate sync-243-hotfix-and-docs --strict
```

先跑直接相关测试，再组合及端到端。若有既有夹具/环境债务，列原始collected/pass/fail/skip/error，再给排除结果，不能写“全量全绿”。库存数字用CLI，不从旧文档推断。

## 导航

| 文档或目录 | 用途 |
|---|---|
| README.md | 项目入口与当前维护线 |
| docs/deployment_243_gnome.md | 当前院内运维入口、文件位置和启动 |
| docs/deployment_249_lis_medical.md | 原LIS热补丁安装、验收与回滚 |
| docs/243_249_hotfix_qa.md | 本次同步证据、测试结果和现场核验边界 |
| docs/243_release_cookbook.md | 维护者发布与单例核验流程 |
| docs/how_javert_works.md | 架构与两种取数模式 |
| docs/数据接入清单.md | CSV通用要求及上海模式字段 |
| docs/review_workbench_user_guide.md | 工作台操作与化验归属显示 |
| delivery/audit_eda/README_zh.md | 手动本批/存量统计教程 |
| scripts/sql/249_lis_by_syxh.sql | 仅替换SYXH的只读指标排查 |
| docs/CHANGES.md | 历史记录；不复制回本手册 |
| src/javert/data/hub_source.py | 共享取数与归属校验 |
| scripts/run_243_lis_batch.py | 冻结名单、回执、状态和恢复 |
| delivery/lis_hotfix/ | v1安装器及只含代码的回滚测试夹具 |

`output/`、data_import*、患者快照和外部Scriv资料不是可假定存在的测试依赖。安装器测试使用受控代码夹具，不需要临时工作树。
