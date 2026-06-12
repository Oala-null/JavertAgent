## Context

`/Users/shane/26er/Javert/` 是新建空目录,与 `/Users/shane/26er/zadig_agent/` 同 Mac 本地共存。zadig_agent 已部署到 192.168.31.62 容器化运行,本仓库不依赖远端服务器。`zadig_agent` 自身未把文书/费用进 SQL Server (代码层 `paths.py` + `main.py` 直接 `pd.read_csv`),所以 Javert 的「数据复用」实际是 csv 文件复用,不是数据库复用。

源数据规模:`case_notes.csv` 357,418 行 / 3,062 患者,`shi_fee.csv` 695,608 行 / 3,308 患者,~98% 患者命中甲状腺关键词。0325.xlsx 共 315 条违规情形,163 条标记「做不了」,本期锁定其中 34 条做 pilot (肿瘤 7 + 各科室通用类 10 + 临床检验 17,选取标准是「仅依赖基础工具即可推断,不需要先建项目内涵字典/计价规则字典」)。

LLM 后端复用 zadig_agent 的 sglang 服务 (`192.168.31.62:30000`,Qwen3.5-35B),但作为外部 HTTP 依赖,Javert 自身不部署模型。

操作者画像:用户本人逐条设计 prompt + 跑 dry-run 看 trace + 改 yaml + 再 dry-run。pilot 阶段 不追求自动化运行,追求「单条规则的迭代闭环要快」。

## Goals / Non-Goals

**Goals:**

- 让操作者在「编辑 yaml → dry-run 看 trace → 改 yaml → 再 dry-run」的闭环里,单次 round-trip 控制在 30 秒级 (dry-run 单患者审计 ~10-20s 含 LLM 延迟)
- 34 条规则的设计/试跑/标记/汇总,每条独立可推进,互不阻塞;任何一条放弃 (status: abandoned) 不破坏其他规则的状态
- 项目骨架对扩展友好:加新规则无须改代码,加新工具只需注册到 tool_executor,加新数据源 (SQL/HTTP) 只需实现 `DataLoader` 接口
- 与 zadig_agent 完全代码独立;复用模块以「拷贝粘贴 + 移除项目特定 import」方式接入,不引入 zadig_agent 作为 Python 包依赖

**Non-Goals:**

- ❌ Web UI / 院方界面 — pilot 阶段纯 CLI,UI 留给后续 change
- ❌ 实时反查 HTTP API — pilot 阶段离线批跑足够
- ❌ 依赖 142 SQL Server — 结果存本地 SQLite,加 SQL 适配器留给后续 change
- ❌ 多机部署 / Docker — pilot 阶段单机本地运行
- ❌ 项目内涵字典 / 计价规则字典 / 医疗器械注册证库 — 这些规则不在本期 34 条 pilot 范围内
- ❌ 进化循环 / 自动 prompt 优化 — pilot 阶段所有 prompt 由人手写,evolve_loop 留给后续
- ❌ 跨规则联合裁决 / 仲裁层 — 每条规则独立产出 verdict,汇总只做计数,不做加权融合
- ❌ 数据脱敏 / PHI 哈希 — 本地数据本地跑,不外发,沿用源 csv 患者 ID

## Decisions

### Decision 1: 规则即 YAML 文件,不进数据库

每条规则一个 `configs/rules/Rxxx.yaml`,而不是建一张 SQLite/SQL Server `rules` 表。

**为什么**:操作者要频繁手动编辑 prompt_addon、调试 trigger_keywords、留 design notes。yaml 比 SQL 行编辑友好得多 — 可以 vim/编辑器、git diff 看演进、跨 commit 回滚单条规则。Pilot 阶段「规则」本质上是源代码的一部分,不是运行时数据。

**替代方案**:

- 单个 `rules.yaml` 大文件 — 否定:34 条规则同时有人编辑容易冲突 / git 合并难
- SQLite `rules` 表 + admin UI — 否定:UI 是工程量,与 pilot 阶段「快速试错」目标冲突
- Markdown 表格 — 否定:无法表达 trigger_keywords list / 多行 prompt_addon

### Decision 2: 复用 zadig_agent 工具的方式 = 拷贝粘贴

把 `llm_provider.py / tool_executor.py / search_notes.py / search_fees.py / note_diagnosis.py / drug_indication.py` 直接拷到 `src/javert/tools/`,并修改 import 路径与字段名 (zadig_agent 用 `住院号`,Javert 也用 `住院号`,无需改)。

**为什么**:`zadig_agent` 不是 PyPI 包,没有版本化发布通路;作为本地路径依赖会导致两个项目互相干扰 (zadig_agent 自身在迭代 v2.x,接口仍可能改)。拷贝换来代码独立、版本冻结、跨项目演进自由,代价是上游 bugfix 需手动同步 — 但 pilot 阶段工具用法稳定,这个代价小。

**替代方案**:

- `pip install -e ../zadig_agent` 路径依赖 — 否定:zadig_agent 自身的 import path (`from src.skills...`) 与新项目冲突,且 zadig_agent 用 uv 管 deps,集成进 pip 不顺
- Git submodule — 否定:跨子模块改 import 路径成本高,且 pilot 阶段我们只用其中 ~6 个文件
- 重写工具 — 否定:浪费,搜索逻辑 zadig_agent 已经验证过了

### Decision 3: 数据接入抽象 `DataLoader`,初版仅 csv 实现

定义一个最小接口 `DataLoader.get_notes(patient_id)` + `get_fees(patient_id)` 返回 `pd.DataFrame`,工具消费者只调接口。`CsvLoader` 进程内 lazy-load + 内存缓存 (DataFrame 全表在内存里 ~300MB,可接受)。

**为什么**:用户明确说先 csv 起步,后续不排除接 SQL Server。如果初版直接在 `search_notes.py` 内部 `read_csv`,后期要换 SQL 就要改每个工具的内部逻辑;抽象一层接口,换实现只改一个 `loader = SqlLoader()`。

**替代方案**:

- 直接 `pd.read_csv` 嵌入工具 (zadig_agent 当前做法) — 否定:违反了我们「pilot 验证完后能换 SQL 不动业务代码」的目标
- 全表加载到 SQLite 提供 SQL 接口 — 否定:增加导入步骤、降低开发反馈速度
- ORM (SQLAlchemy) — 否定:csv 阶段过度设计

### Decision 4: 审计结果存本地 SQLite,不存文件 / 不存 SQL Server

`output/audit.sqlite` 一张主表 `audit_runs` + `_meta`。`evidence_json` 与 `tool_calls_json` 序列化为 TEXT 存 JSON。

**为什么**:

- 操作者要按 rule_id / patient_id / verdict / 时间窗口反查,纯文件目录 (`output/Rxxx/Pxxx.json`) 在 50 患者 × 34 规则 = 1700 条 / 月级别还能 walk,但加几轮迭代很快上 1 万条,grep 卡顿
- 142 SQL Server 在飞检场景适合做最终归档,但 pilot 阶段开发本地运行不连内网更顺
- SQLite 单文件,git 的 .gitignore 可以排除,备份/分享都方便

**替代方案**:

- DuckDB — 否定:SQLite 标准库够了,DuckDB 引入新依赖收益不大 (查询量小)
- 142 SQL Server — 否定:绑死内网,本地 dev/调试不便
- 纯 JSON 文件目录 — 否定:跨规则汇总、verdict 分布统计需要 SQL

### Decision 5: LLM 协议沿用 zadig_agent 的 `<tool_call>` 文本标签

不用 OpenAI Function Calling,因为 sglang 当前禁用了 `enable_tool_use`。沿用 zadig_agent 在 v2.x 验证过的 `<tool_call>{...}</tool_call>` 文本标签 + 正则解析。

**为什么**:这个协议在 zadig_agent 已经跑了 ~5000 个 patient 推理,parser 的边界 case 都磨过了,直接拷代码即用。如果改用 Function Calling,需要切 sglang 配置,影响 zadig_agent 在 62 上的运行。

**Trade-off**:文本标签稳定性弱于结构化 Function Calling,模型偶尔会输出格式偏移 — 但 zadig_agent 经验是 Qwen3.5-35B 95%+ 输出合规,容错由 `tool_executor.py` 的双正则 (`<tool_call>` + `\`\`\`tool_call`) 兜底。

### Decision 6: 验收 verdict 用结构化 JSON 块,而不是自由文本

要求 LLM 在最后输出 `\`\`\`json\n{"verdict": "VIOLATION", "confidence": 0.85, "evidence": [...], "reasoning": "..."}\n\`\`\``,Runner 解析 JSON 块得到 `AuditResult`。

**为什么**:

- verdict 三态 + 置信度 + 证据列表是机器消费的字段,自由文本要二次解析准确率差
- 一次解析失败的容错路径已经设计好 (再发一轮 repair turn,二次失败转 INCONCLUSIVE)
- json 块在 zadig_agent 的 `code_validate` / `crag_evaluate` skills 已经验证可用

**替代方案**:

- 强类型 Function Calling 工具 `submit_verdict()` — 否定:同 Decision 5,sglang 当前关 Function Calling
- XML tags `<verdict>...</verdict>` — 否定:嵌套字段表达不如 JSON 自然

### Decision 7: pilot 不连 142 SQL Server,但保留接口位置

在 `src/javert/store/audit_store.py` 暴露 `AuditStore` 抽象接口,`SqliteStore` 是初版唯一实现。后续 change 加 `SqlServerStore` 时,接口已经定好,业务代码无需改。但 pilot 阶段不实现 `SqlServerStore`,也不引入 pyodbc 依赖。

**为什么**:用户态度:「我们目前不知道哪些规则能用」 — 在规则形态没稳定前先不要把工程铺到内网集成。

### Decision 8: 数据快照而不是软链接

`javert init` 把 csv 物理拷贝到 `Javert/data/`,不是 symlink。

**为什么**:pilot 阶段我们要冻结数据时点,zadig_agent 后续可能改源 csv 列名 (例如 v2.10 把 `case_notes.csv` 列改名),软链接会让 Javert 突然崩。物理拷贝换来稳定性,代价是 ~320MB 重复存储,可接受。

`init --refresh-data` 提供主动刷新通路。

### Decision 9: pilot 患者 50 例的选取

从 zadig_agent 的 `evolve_loop.py` 进化采样器使用过的患者池里挑 50 个甲状腺癌相关患者。具体策略:

1. 文书命中甲状腺关键词 (≥1 条)
2. 同时有费用记录 (≥10 条)
3. 优先包含手术费 + 化疗费 + 检验费 三类的患者 (覆盖 34 条规则的不同信号)
4. 在 GroundTruth 里有标注的优先 (调试更可信)

具体名单写入 `data/pilot_patients.txt`,首次 init 时由脚本生成,后续可手动调整。

### Decision 10: 项目用 `uv` 管依赖,沿用 zadig_agent 同款

`pyproject.toml` + `uv.lock`,Python 3.11+。依赖最小集:`pandas`, `pyyaml`, `httpx`, `pydantic`, `nanoid`, `click` (CLI),不引入 sqlalchemy/pyodbc/fastapi。

## Risks / Trade-offs

- [Risk] LLM 在 34 条规则上 verdict 准确率不可知,可能很多规则 INCONCLUSIVE 率超 50% → Mitigation:dry-run 优先,每条规则先在 5 患者上看 trace,准确率太低则 abandon,不浪费在 50 患者批跑
- [Risk] zadig_agent 后续升级修改 csv 列名或工具接口,Javert 拷贝的代码会过期 → Mitigation:冻结当前 zadig_agent v2.10.1 提交点的实现,在 `src/javert/tools/` 顶部注释里记录 source commit,后续手动 cherry-pick bugfix
- [Risk] 部分 trigger_keywords 设计过窄,LLM 找不到证据导致默认 CLEAN 假阴性 → Mitigation:prompt base 里要求 LLM 先 search_fees 全部费用类别 + search_notes 子阶段目录再决定调用,提高搜索覆盖
- [Risk] 50 患者 × 34 规则 = 1700 次 LLM 调用,~30s/次 = ~14 小时单线程 → Mitigation:pilot 阶段先 5 患者试跑每条,确定形态后才批跑 50;批跑可后台 nohup
- [Risk] sglang 在 62 上与 zadig_agent 竞争 GPU 时段 → Mitigation:用前 `curl /v1/models` 探活,长任务避开 zadig_agent evolve_loop 跑动时段;两者共享 GPU 是设计接受的代价
- [Risk] 操作者修改 yaml 时手抖把 status 改成非法值 → Mitigation:`Rule` 加载用 pydantic Literal 校验,加载失败明确报错而不是默默吃掉
- [Risk] 审计结果 JSON 字段超长 (大量 tool_calls 历史) 让 SQLite 写入慢 → Mitigation:`tool_calls_json` 截断每个调用 result text 到 ≤2000 字符,reasoning 同样限长

## Migration Plan

零迁移 — 新项目从空目录起步。`javert init` 是唯一的初始化路径。回滚 = 删除 `Javert/` 目录。

## Open Questions

1. 50 患者列表具体怎么采:写一个一次性脚本扫 case_notes.csv 自动选,还是手挑?**初版倾向自动选 + 手工剔除**。
2. `prompt_addon` 是否需要支持模板变量插值 (例如 `{{patient_id}}`)?**初版不支持**,等出现实际重复模板再加。
3. pilot 期间 schema 还会演进 — 升级 SQLite 用 `_meta.schema_version` 加迁移函数,还是直接 drop 重建?**初版倾向 drop 重建**,数据无业务价值,操作者每次升级跑一次 `javert init --rebuild-store`。
4. `dry-run` 是否同时也写入 SQLite?**初版 yes** — 操作者改 prompt 多轮 dry-run 时,所有 trace 都进库可以后续比较「prompt 改前后命中率变化」。
