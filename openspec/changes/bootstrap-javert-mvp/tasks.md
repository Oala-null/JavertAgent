## 1. 项目骨架

- [x] 1.1 在 `/Users/shane/26er/Javert/` 创建 `pyproject.toml` (uv-managed, Python ≥3.11),依赖:`pandas`, `pyyaml`, `httpx`, `pydantic>=2`, `nanoid`, `click`, `openpyxl`, `xlrd`
- [x] 1.2 创建 `.gitignore` 排除 `data/*.csv`, `output/`, `.venv/`, `__pycache__/`
- [x] 1.3 创建目录结构 `src/javert/{data,audit,tools,store}/`, `configs/rules/`, `data/`, `output/`, `tests/`
- [x] 1.4 创建 `src/javert/__init__.py` 暴露版本号 `__version__ = "0.1.0"`
- [x] 1.5 创建 `cli.py` 顶级入口骨架 (click 多命令组,占位 7 个 subcommand)

## 2. 配置层

- [x] 2.1 创建 `configs/llm.yaml` 默认值: endpoint=`http://192.168.31.62:30000/v1`, model=`Qwen/Qwen3.5-35B-A3B-GPTQ-Int4`, max_tool_calls=10, data_dir=`data/`, rules_dir=`configs/rules/`, audit_db=`output/audit.sqlite`
- [x] 2.2 实现 `src/javert/config.py` (pydantic BaseSettings 加载 yaml + `JAVERT_*` 环境变量覆盖)
- [x] 2.3 编写测试 `tests/test_config.py`:文件加载、环境变量覆盖、缺字段报错三个场景

## 3. 数据接入 (data-access)

- [x] 3.1 实现 `src/javert/data/loader.py` 抽象基类 `DataLoader` (abstractmethod `get_notes` / `get_fees`)
- [x] 3.2 实现 `src/javert/data/csv_loader.py` `CsvLoader` (lazy load + 进程内单例缓存 + 首次加载日志)
- [x] 3.3 实现快照拷贝逻辑 `src/javert/data/snapshot.py`:从 `../zadig_agent/data/case_notes/case_notes.csv` 与 `../zadig_agent/data/patients/shi_fee.csv` 复制到 `data/`,写 `data/_snapshot.json`,行数校验
- [x] 3.4 编写患者采样脚本 `src/javert/data/sample_pilot.py` 输出 50 患者到 `data/pilot_patients.txt` (规则:文书命中甲状腺 + 费用 ≥10 条 + 含手术费/化疗费/检验费三类的优先)
- [x] 3.5 编写测试 `tests/test_csv_loader.py`:已知患者返回非空 / 未知患者返回空表 / 二次调用走缓存

## 4. 工具层 (从 zadig_agent 移植)

- [x] 4.1 拷贝 `zadig_agent/src/llm_provider.py` → `src/javert/tools/llm_provider.py`,顶部加注释记录 source commit hash;移除 zadig_agent 项目特定的 import
- [x] 4.2 拷贝 `zadig_agent/src/tools/tool_executor.py` → `src/javert/tools/tool_executor.py`,适配 import 路径
- [x] 4.3 拷贝 `zadig_agent/src/skills/search_notes.py` → `src/javert/tools/search_notes.py`,把 `notes_df` 来源改为通过 `DataLoader.get_notes` 获取 (而不是 closure 传 DataFrame)
- [x] 4.4 拷贝 `zadig_agent/src/skills/search_fees.py` → `src/javert/tools/search_fees.py`,同 4.3 改造
- [x] 4.5 拷贝 `zadig_agent/src/skills/note_diagnosis.py` → `src/javert/tools/note_diagnosis.py`,同 4.3 改造
- [x] 4.6 拷贝 `zadig_agent/src/skills/drug_indication.py` + `configs/drug_indication_map.json` → `src/javert/tools/` 与 `configs/`,同 4.3 改造
- [x] 4.7 实现 `src/javert/tools/registry.py`:把上面 4 个工具按 `tool_executor` 协议注册,绑定 `DataLoader`
- [x] 4.8 编写测试 `tests/test_tools.py`:每个工具用一个已知患者跑一次,验证返回非空 + 字段命中

## 5. 规则注册表 (rule-registry)

- [x] 5.1 实现 `src/javert/audit/rule.py`:`Rule` pydantic 模型 (10 个字段) + `Status` Literal 枚举
- [x] 5.2 实现 `src/javert/audit/rule_loader.py`:`load_rule(path) -> Rule` + `load_all(dir) -> dict[rule_id, Rule]` + 校验报错
- [x] 5.3 实现 `src/javert/audit/rule_writer.py`:`write_rule(rule, path)` 保留字段顺序与注释 (用 `ruamel.yaml` 而不是 PyYAML 以保 round-trip)。**注意**:1.1 依赖里加 `ruamel.yaml`
- [x] 5.4 实现 `src/javert/audit/rule_init.py`:从 0325.xlsx 读取「做不了」+ 34 条 pilot 子集白名单 (rule_id 列表硬编码),生成 yaml 文件
- [x] 5.5 实现 `src/javert/audit/state_machine.py`:状态转移校验 (drafting→ready→validated;abandoned 任意可达;backward 需 force)
- [x] 5.6 编写测试 `tests/test_rule.py`:加载良好文件 / 缺字段报错 / 非法状态报错 / round-trip 保留注释 / 状态转移校验

## 6. 审计存储 (audit-store)

- [x] 6.1 编写 `src/javert/store/schema.sql`:`audit_runs` 主表 + `_meta` + 两个索引,符合 spec
- [x] 6.2 实现 `src/javert/store/audit_store.py`:`AuditStore` 抽象 + `SqliteStore` 实现 (open/init/write/find_by_*/summary_by_rule/show)
- [x] 6.3 实现 `AuditResult` / `Evidence` / `ToolCall` pydantic 模型 (`src/javert/audit/result.py`)
- [x] 6.4 实现 `run_id` 生成 (`aud_` + 12 字符 nanoid)
- [x] 6.5 编写测试 `tests/test_audit_store.py`:首次创建 schema / 插入读取 round-trip / 重复 (rule, patient) 插入两条 / verdict 分布查询 / time-window 查询

## 7. 审计引擎 (audit-engine)

- [x] 7.1 编写基础 system prompt `src/javert/audit/prompts/base.txt`:定义审计员角色、要求 ≥1 工具调用、要求最终 fenced JSON、引用证据格式
- [x] 7.2 实现 `src/javert/audit/prompt_assembler.py`:base + rule.prompt_addon + rule.question + rule.example + trigger_keywords 拼装
- [x] 7.3 实现 `src/javert/audit/runner.py` 核心 agent loop:LLM 调用 → 解析 tool_calls → 执行 → 结果回灌 → 直到拿到 fenced JSON 或达到 max_tool_calls
- [x] 7.4 实现 verdict JSON 解析 + 一次 repair turn 兜底 (二次失败转 INCONCLUSIVE)
- [x] 7.5 实现 `Runner.audit(rule, patient_id) -> AuditResult` 顶层 API
- [x] 7.6 实现 dry-run 输出格式 (`[Tool]` / `[LLM]` / `[Verdict]` 行)
- [x] 7.7 实现 LLM 不可达时的 `LlmUnavailableError` 异常 + 重试逻辑
- [x] 7.8 编写测试 `tests/test_runner.py`:用 mock LLM 模拟成功 / max_tool_calls / 格式错误 / 连接失败四种路径

## 8. CLI 子命令 (cli)

- [x] 8.1 实现 `javert init` (调用 snapshot + sample_pilot + rule_init + AuditStore.init + LLM 探活)
- [x] 8.2 实现 `javert list` (扫描 rules dir + 读 audit_store.summary_by_rule + tabulate 输出)
- [x] 8.3 实现 `javert dry-run <rule_id> --patient <id>` (单跑 + stdout trace + 写入 store)
- [x] 8.4 实现 `javert run <rule_id> [--patient <id> | --pilot]` (批跑 + 进度日志)
- [x] 8.5 实现 `javert mark <rule_id> --status <s> [--force]` (调用 state_machine + rule_writer)
- [x] 8.6 实现 `javert report [--since <date>] [--rule <id>]` (audit_store.summary 表格化输出)
- [x] 8.7 实现 `javert show <run_id> | <rule_id> --patient <id>` (从 store 还原 trace 重放打印)
- [x] 8.8 编写 CLI 集成测试 `tests/test_cli.py`:每个子命令的 happy path + `--help` 输出格式

## 9. 文档与首跑

- [x] 9.1 写 `README.md`:项目目标 / 安装步骤 / 7 子命令的基础示例 / 首跑 walkthrough
- [x] 9.2 写 `docs/rule_design_guide.md`:逐字段说明 yaml schema + 给一条 R191 的示范 rule.yaml 作为 reference
- [x] 9.3 写 `CLAUDE.md` (Javert 自己的):基本架构图 + 重要文件指引 + 常见命令
- [x] 9.4 跑 `javert init` 完整一遍,确认 5 个步骤都成功,34 yaml 生成,sqlite 建好,LLM 通
- [x] 9.5 选 1 条规则 (建议 R191 「肿瘤断层重复收费」) 写完整 prompt_addon,在 5 个 pilot 患者上 dry-run,把 trace 截图 / 复制到 `docs/sample_run_R191.md` 作为后续设计参考

## 10. 验收

- [x] 10.1 `openspec validate bootstrap-javert-mvp --strict` 通过
- [x] 10.2 全部单测通过 (`uv run pytest tests/ -v`)
- [x] 10.3 `javert list` 输出 34 条 drafting 状态规则
- [x] 10.4 至少 3 条规则跑通 `dry-run` (任意患者),无 crash
- [x] 10.5 `output/audit.sqlite` 含 ≥3 条 audit_runs 记录,`javert report` 能正常汇总
