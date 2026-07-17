# AGENTS.md（Javert）

本文件是 Javert 项目内 AI 开发的规则手册。详细架构、运维和历史结果放在 `docs/`，
本文件只保留下一位开发者若不知道就容易犯错的约束。

## 协作约定

- 全程使用中文开发和沟通。
- 开工前说清假设、歧义和验收标准；不确定且会改变实现方向时先问。
- 优先最小可行改动：不做未要求的功能，不为一次性需求造抽象，不顺手重构相邻代码。
- 保留用户已有修改；每一行 diff 都应能追溯到当前任务。
- 修 bug 先用测试或可复现命令证明问题，再修复并跑同一验证。
- 多步骤任务写短计划，每一步都要有可验证的完成条件。

## 项目定位与当前口径

Javert 用规则 YAML、确定性预检/后置闸和本地 LLM，审计国家医保局 2026
《医疗机构自查自纠问题清单》中的违规情形。核心链路是：

```text
患者数据 → Router 预筛 → 确定性 precheck → LLM/结构化求值
        → verdict_gate → SQLite → SQL Server 142 → 专家工作台
```

不要在文档中手抄易漂移的库存数字，先运行：

```bash
.venv/bin/javert list
```

2026-07-17 的已核对基线是 159 条 YAML：118 ready、28 abandoned、13 drafting；
ready 模板分布 M1-M8 为 22/22/17/13/10/9/20/5。CLI 有 15 个顶层命令，
工具注册表当前提供 10 个工具。历史报告里的旧数字保留原样，但必须标明快照日期。

## 不可违反的边界

### 隐私与凭据

- Git 内的测试夹具、QA 报告和日志只允许语义化、去标识 ID；禁止新增真实患者号、姓名、
  原始病历或未盐化 run/ownership 标识。
- shadow/批处理的盐和数据库凭据只能通过环境或受控 env 文件注入，不能放进参数、manifest、
  Git 或终端回显。
- 含 PHI 的临时目录使用 0700、文件使用 0600，成功和异常退出都要清理。
- `.env`、session secret、SQL 密码和患者原文不得出现在测试输出或提交说明中。

### 配置与数据源

- 配置优先级是 `JAVERT_*` 进程环境 > `configs/llm.yaml` > 代码默认；
  环境地址、库名和凭据不要写进 YAML。
- 142 `sh_yb_platform` 是 DE 维护的数据中台，只读；`TP_data_hub` 是我方开发库；
  `zadig` 保存工作台业务结果。243 是独立产品环境，不能沿用 62 的上线授权。
- `scripts/push_data_hub_filled.py` 的 `--database` 是必填项；建库、建表、增量写和重灌全部
  必须命中 `JAVERT_OWNED_DBS`，无越权参数。142 上只能显式写 `TP_data_hub`；
  243 的同名产品库需在本机单独声明为 owned。
- `create_data_hub_indexes.sql` 不切库；必须用 `sqlcmd -d <db> -v HUB_DATABASE=<db> -b`
  双重指定且一致。142 `sh_yb_platform` 的 DDL/索引变更只交 DE/DBA 执行。
- `src/javert/data/hub_source.py` 是 TB_* 到内部契约映射的唯一来源；ETL 和工作台都复用它。
- `configs/schema_manifest.yaml` 是 onboarding 数据模型唯一真相源；UI、ETL、工具注册不得各写一套。
- 推送/重灌自有 hub 后必须对账；`--recreate` 会连索引一起删除，随后按上述目标库校验方式
  重跑索引。

### 规则、Router 与裁决

- 每条规则一个 `configs/rules/*.yaml`；状态后退必须 `javert mark ... --force`。
- 改规则状态、关键词、模板渲染结果或 M8 规则后，必须运行
  `scripts/build_rule_mapping.py`，并验证 YAML 与
  `data/router/javert_rules_index.json` 状态一致。
- `--rules` 会显式纳入 abandoned 规则；默认批跑不能依赖这一行为。
- `precheck` 只处理声明过的确定性费用形态；`verdict_gate` 只对 VIOLATION 生效且只降不升。
- 修改 SSE/2C 返回字段时保持“只加不删不改名”，同步
  `docs/2c对接_javert审计服务.md`，并检查下游 BFF 契约。

### 肿瘤医保资格 v2

- `JAVERT_ONCOLOGY_ELIGIBILITY_V2` 仅允许 `off|shadow|on`；代码和仓库配置默认 `off`，
  62 的受控运行值为 `on`。
- `on` 模式下 RD04 独占 `oncology=true AND source_type=insurance`，R007 只处理非肿瘤
  限适应症候选；`off/shadow` 保留 R007 旧候选集合。RD04 当前 ready，
  RD10-RD37 保持 abandoned。
- RD04 无净正收费候选时必须确定性 CLEAN、零 LLM；不得让方案文本凭空创建费用候选。
- 条件树、病理标志物和方案三份资产只有 schema/checksum/生效期合法且
  `review_status=approved` 才能自动裁决；其余一律显式 REVIEW_REQUIRED。
- `audit_disposition` 确定性投影到旧三态：
  `NO_VIOLATION_FOUND→CLEAN`、`VIOLATION_FOUND→VIOLATION`、
  `REVIEW_REQUIRED→INCONCLUSIVE`。
- SQLite/SQL Server 的 `eligibility_json` 是可空兼容字段；旧行不回填，不用新知识静默重写历史。
- 维护、shadow、生产验证和回滚只以 `docs/oncology/operations.md` 为准；
  验收事实见 `docs/oncology/qa_report.md`。

### 62 部署

- 62 的 `/home/admin2/javert` 是 tar 部署目录，不是 Git 仓库。
- 覆盖源码前先无回显读取并固化旧进程实际生效的 SQL/Hub 配置，备份代码与 mode 0600 的
  `.env`；不要让新代码默认值覆盖生产连接。
- 先部署代码/配置/router index，再运行 `javert ensure-mssql-schema`，最后重启服务。
- 重启后必须验证 systemd active、登录页 HTTP 200、`/proc/<pid>/environ` 中关键开关实值，
  以及 SQL/Hub 健康；只检查 `.env` 文件不算完成。
- 不要无条件运行 `sync-to-mssql --pending-only`，它会处理全部历史 pending；先看 dry-run/范围。
- 详细命令与回滚步骤见 `docs/deployment_192_62.md`。

## 代码导航

| 路径 | 角色 |
|---|---|
| `src/javert/cli.py` | Click 顶层命令 |
| `src/javert/config.py` | Pydantic 配置与环境覆盖 |
| `src/javert/audit/runner.py` | 核心审计循环与结果接线 |
| `src/javert/audit/precheck.py` | M1/companion 确定性前置预检 |
| `src/javert/audit/verdict_gate.py` | 只降不升的后置闸 |
| `src/javert/audit/rule.py` | Rule/Precheck schema |
| `src/javert/oncology/` | 资格合同、知识加载、病理/方案归一与运行时 |
| `src/javert/tools/registry.py` | manifest 驱动的工具注册 |
| `src/javert/routing/` | Router 单闸 |
| `src/javert/store/` | SQLite、SQL Server 双写与迁移 |
| `src/javert/web/` | FastAPI、工作台、SSE、2C 接口 |
| `src/javert/onboarding/` | manifest 驱动接入、归类与连接预检 |
| `configs/rules/` | 规则唯一落盘位置 |
| `configs/templates/` | M1-M8 机器可读模板 |
| `data/router/` | Router 生成资产 |
| `scripts/` | 构建、迁移、批跑、回填和部署辅助 |

`output/`、`data_import*`、`Scriv/`、`规则引擎代码/` 可能只存在于原项目、同级项目或部署机，
不能假设每个 worktree 都有。

## 常用命令

```bash
# 规则与单患者
.venv/bin/javert list
.venv/bin/javert dry-run R191 --patient <去标识测试号>
.venv/bin/javert audit-patient <测试号> --priority all --use-router --concurrency 5
.venv/bin/javert audit-patient <测试号> --rules RD04

# 模板与 Router
.venv/bin/javert template list
.venv/bin/javert template validate M8
PYTHONPATH=src .venv/bin/python scripts/build_rule_mapping.py

# 肿瘤知识资产
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_oncology_eligibility_assets.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_oncology_regimen_kb.py

# 存储与工作台
.venv/bin/javert ensure-mssql-schema
.venv/bin/javert sync-to-mssql --dry-run
.venv/bin/javert web --with-mssql --host 127.0.0.1 --port 8090

# 外部数据
.venv/bin/python scripts/etl_import.py --mapping configs/column_mapping.yaml --dry-run
.venv/bin/python scripts/loaded_status.py
```

仓库文档中的 `uv run` 命令适合标准环境；受限沙箱里优先直接调用 `.venv/bin/*`，
避免 `uv` 写用户缓存失败。

## 验证要求

1. 先跑与改动直接相关的测试。
2. 再跑受影响模块的组合测试和一个端到端命令。
3. 修改规则/模板后验证 Router index；修改存储后验证旧行兼容和双写；修改 Web 后验证 API/SSE。
4. 全量测试若受既有 fixture/环境债务影响，必须列出原始 collected/pass/skip/fail/error，
   再给排除清单后的门禁；不能把“排除既有债务后全绿”写成“全量全绿”。
5. 当前 oncology 门禁和既有债务清单以 `docs/oncology/qa_report.md` 为准。
6. OpenSpec change 完成前运行严格校验，并让 `tasks.md` 与真实验证一致。

## 文档分工

| 文档 | 受众与用途 |
|---|---|
| `README.md` | 新人入口、命令和当前能力 |
| `docs/how_javert_works.md` | 面向管理层/信息科的当前架构 |
| `docs/数据接入清单.md` | 医院数据接入 |
| `docs/2c对接_javert审计服务.md` | 2C API 契约 |
| `docs/deployment_192_62.md` | 62 运维 runbook |
| `docs/review_workbench_user_guide.md` | 专家工作台使用 |
| `docs/rule_design_guide.md` | Rule YAML 设计 |
| `docs/template_design_guide.md` | M1-M8 模板维护 |
| `docs/oncology/operations.md` | 肿瘤资格维护、shadow、生产与回滚 |
| `docs/oncology/qa_report.md` | 肿瘤资格验收事实 |
| `docs/CHANGES.md` | 完整历史；不要把版本流水账复制回本文件 |

## OpenSpec 工作流

- 实现/继续 change：按 `.agents/skills/openspec-apply-change/SKILL.md`。
- 探索或提案：使用对应 OpenSpec skill，不直接跳过 proposal/design/spec/tasks。
- 只有用户明确要求归档时才 archive；完成实现不等于自动归档。
- change 结束时同时检查代码、测试、README、架构、runbook、对接契约和 CHANGES，
  不把 AGENTS.md 写成发布日志。
