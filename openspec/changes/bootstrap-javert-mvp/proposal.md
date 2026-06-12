## Why

国家医保局《2026 年医疗机构自查自纠问题清单》共 315 条违规情形,其中 163 条标记为「做不了」——指现有信息系统的脚本规则无法表达,需要结合病历文书与费用明细做跨段落、跨字段的语义判断 (例如「套用模板病历」「适应症外用药」「项目串换」)。院方目前对这部分只能靠人工抽查,既漏检率高也无法形成台账。

我们已有 `zadig_agent` 的成熟基础设施 (Qwen3.5 LLM 网关 + 文书/费用 csv + search_notes / search_fees / drug_indication 等工具),数据层覆盖 ~3000 例甲状腺癌为主的住院患者 (文书+费用齐全)。当下需要一个**可扩容的实验脚手架**,把每条「做不了」的规则当成独立单元——逐条设计 prompt、逐条试跑、逐条决定保留或废弃。先做 MVP 验证形态,再扩规模。

## What Changes

- 新建独立 Python 项目 `Javert/`,与 `zadig_agent` 代码完全解耦,但通过快照拷贝复用其文书/费用 csv 数据
- 引入「规则即文件」的工作模式:每条「做不了」规则对应一个 `configs/rules/Rxxx.yaml`,字段含 `status (drafting/ready/validated/abandoned)` + `prompt_addon` + `trigger_keywords` + `notes`,作为院方自查自纠表单的数据载体
- 实现 LLM 审计 runner:读取规则 yaml + 调用复用工具 + 输出结构化 verdict (`VIOLATION/CLEAN/INCONCLUSIVE` + 证据 + 推理 + 置信度)
- 提供单条单跑的迭代闭环 CLI:`init / list / dry-run / run / mark / report / show`
- 审计结果写入本地 SQLite (`output/audit.sqlite`),每次跑产生一条 trace 记录,支持后续按 rule/patient/verdict 反查
- pilot 范围锁定 34 条 (肿瘤 7 + 通用类 10 + 临检 17),50 个甲状腺癌测试患者
- 抽象 `DataLoader` / `AuditStore` 接口,初版分别实现 `CsvLoader` / `SqliteStore`,为后期换 SQL Server 留口

## Capabilities

### New Capabilities

- `rule-registry`: 把 0325.xlsx 的「做不了」条款转换成版本化、可编辑、带状态机的 yaml 文件集合,支持加载/校验/状态变更/进度查询
- `data-access`: 抽象患者文书 (case_notes) 与费用明细 (shi_fee) 的读取接口,初版 csv 实现,后续可换 SQL/HTTP 不动业务代码
- `audit-engine`: LLM agent 主循环——基于规则 yaml + 患者数据 + 复用工具,产出结构化审计裁决与证据链
- `audit-store`: 审计结果的 SQLite 持久化层,支持按 rule_id / patient_id / verdict / 时间窗口反查
- `cli`: `javert <subcommand>` 命令行入口,覆盖 init/list/dry-run/run/mark/report/show 的迭代工作流

### Modified Capabilities

(none — 新项目)

## Impact

- **新增项目**: `/Users/shane/26er/Javert/` (独立 Python 项目, 用 `uv` 管理依赖,与 `zadig_agent` 同 Mac 本地共存)
- **代码复用**: 从 `zadig_agent` 拷贝以下模块作为内置工具 (后续随上游升级保持手动同步): `llm_provider.py`, `tool_executor.py`, `skills/search_notes.py`, `skills/search_fees.py`, `skills/note_diagnosis.py`, `skills/drug_indication.py`
- **数据快照**: 一次性从 `zadig_agent/data/` 拷贝 `case_notes.csv` (~120MB) 与 `shi_fee.csv` (~200MB) 到 `Javert/data/`;数据冻结在拷贝时点,后续重跑由 `javert init --refresh-data` 触发
- **外部依赖**: Qwen3.5 sglang 服务在 `192.168.31.62:30000` (与 zadig_agent 共享 GPU);本地 SQLite 无外部依赖
- **零跨机依赖**: pilot 阶段不依赖 142 SQL Server,不依赖 zadig_agent 运行时,本地能完整跑通
- **后续扩展**: pilot 验证完规则形态后,后续 changes 可以加 `sql-loader` (走 142)、`web-form` (院方 UI)、`html-report` 导出等
