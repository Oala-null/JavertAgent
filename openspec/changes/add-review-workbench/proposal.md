## Why

Javert v0.5 已跑出 50 病人 router v2 全 ready 审计基线: 1764 裁决 (275 V + 120 I + 1369 C), 凝结成 `output/router_v2_50patients.html` 这份 1.06 MB 静态报告。下一步必须让医保专家**逐条审核 V/I 并写批复**, 但当前形态是个本地大 HTML, 没办法:

- 多专家分头审 (打不开同一份文件改不了同一行)
- 持久化批复 (静态 HTML 没库)
- 后续 javert 新跑的病人自动并入 (HTML 是 build-once 死页)
- 防止重复审核 (谁审了什么没记录)

院方实际场景是「N 个医保专家拿到 Javert AI 初判, 各自打开浏览器, 逐条点'认同/驳回/改判', 写一段评语, 提交进库」, 最后导出给监管部门。这是一个**有登录的在线工作台**, 不是静态报告。

同时, audit_runs 本地 SQLite (`output/audit.sqlite`, 3345 行) 当前是单机 source-of-truth, 和 Zadig 后端的 SQL Server 142 完全脱节 —— 专家批复要持久化, 跨机访问, 必须搬到 142。

## What Changes

- **新 capability `persistence`**: SQL Server 142 (zadig 库) 上新建 4 张表 (`javert_users` / `javert_audit_runs` / `javert_vio_review` / `javert_audit_logs`), 实现 `SqlServerStore` (pyodbc), 与现有 `SqliteStore` 共存
- **新 capability `review-workbench`**: 扩展 `javert web` 命令为完整工作台 — 登录/注册 + 病人 sidebar + 违规卡片 + 三态决策 + 评语 textarea + SSE 实时同步 + Excel 导出 + 仪表盘
- **modified `audit-engine`**: `audit-patient` / `run` 跑完后**双写** (本地 sqlite + 142 SQL Server), 本地仍是 source-of-truth (142 挂掉不阻断 audit)
- **modified `cli`**: 新增 `javert sync-to-mssql` 一次性 migration 命令 (3345 条本地裁决 → 142); `javert web` 加 `--with-mssql` flag 控制启动模式
- **部署**: Javert web 部署到 Linux 服务器 192.168.31.62 (Zadig sglang 同机) — Mac 仅开发, 内网域名/IP 访问。Mac 本地开发期间 web 可单跑 sqlite-only 模式 (不连 142)
- **账号模型**: 自助注册无审核 (POST /register 直接创账号), 单角色 (无 admin/reviewer 分层), 用户 ID + 操作行为分两张表存
- **审核模型**: 单审制 (UNIQUE 约束 run_id × user_id 不许同人重审同条) + 三态决策 (V/I/C) + 评语 textarea; **insert-only**, 同一专家想改自己上次的审核 → 插新行 + `javert_audit_logs` 留 trail, 老行不删
- **审核范围**: UI 默认显示 V + I (395 条), 顶部 filter 可切 ALL (1764 条)

## Capabilities

### New Capabilities

- `persistence`: SQL Server 142 数据层 — 4 张表 DDL + `SqlServerStore` 双写适配器 + 连接池 + 健康检查
- `review-workbench`: 工作台 web 应用 — FastAPI 路由 + Jinja2 模板 + bcrypt 鉴权 + session cookie 中间件 + SSE 广播 + Excel/CSV 导出

### Modified Capabilities

- `audit-engine`: `Runner` / `audit-patient` orchestrator 在写 sqlite 后追加 try-write 到 `SqlServerStore` (新 capability), 失败仅 log 不抛, 等待 cron sync 补
- `cli`: 新 `sync-to-mssql` 命令 (existing 3345 条 sqlite → 142 一次性 import); `web` 命令扩出工作台 (原仅规则浏览), 加 `--with-mssql / --no-mssql` 切本地 dev 模式

## Impact

- **代码改动** (估约 1500-1800 行新代码):
  - 新 `src/javert/persistence/`: `sql_server_store.py` / `connection.py` / `schema.sql` / `__init__.py` (~400 行)
  - 新 `src/javert/web/`: `app.py` 扩 (~80 行) + `routes/{auth,workbench,review,export,dashboard,sse}.py` (~600 行) + `templates/*.html` Jinja2 (~400 行) + `static/*.css/*.js` (~200 行)
  - 改 `src/javert/audit/runner.py` 和 `src/javert/commands/audit_patient.py`: 调 SqlServerStore.upsert_run (~50 行)
  - 新 `scripts/sync_audits_to_mssql.py`: 一次性 import (3345 sqlite → 142) (~150 行)
  - 改 `src/javert/cli.py`: 注册 `sync-to-mssql` + `web` flag (~30 行)
- **数据库改动** (SQL Server 192.168.31.142, zadig 库):
  - 新建 `javert_users` 表 (id PK / username UNIQUE / pw_hash / created_at / last_login)
  - 新建 `javert_audit_runs` 表 (镜像 sqlite audit_runs, run_id PK + 全部字段)
  - 新建 `javert_vio_review` 表 (id PK / run_id FK / user_id FK / review_verdict / comment / created_at, insert-only)
  - 新建 `javert_audit_logs` 表 (id PK / user_id / action / target_id / payload_json / ts) — 所有用户行为留 trail
  - **不动 Zadig 现有 9 张表** (patients/fees/case_notes/inference_results/coding_decisions/import_batches/inference_jobs 等), javert_* 命名空间隔离
- **依赖改动**:
  - `pyproject.toml` 加 `pymssql` 注: 用 pymssql 不用 pyodbc — 纯 Python, Mac/Linux 通吃, 不需要 ODBC 驱动 (理由见 design.md D1)
  - 加 `passlib[bcrypt]` 密码哈希
  - 加 `itsdangerous` session 签名 (FastAPI session 中间件依赖)
  - 加 `openpyxl` Excel 导出
  - 加 `sse-starlette` SSE 中间件
- **配置改动**:
  - `configs/llm.yaml` 加 `mssql:` 块 (host / port / db / user / pw, **不进 git** — 走 `JAVERT_MSSQL_*` env 覆盖)
  - 新 `configs/web.yaml`: session secret / cookie 配置 / SSE 心跳
- **部署改动**:
  - Linux 服务器 192.168.31.62 部署 (与 sglang 同机) — `scp` + `uv sync` + `systemd` 单元
  - 内网访问: `http://192.168.31.62:8090/`
  - Mac 本地仍可 `javert web --no-mssql` 开发, 不访问 142
- **零外部依赖变化**:
  - 不动 sglang endpoint (`192.168.31.62:30000`), audit-engine LLM 调用沿用
  - 不动现有 `output/audit.sqlite` (双写保留, 本地仍是 source-of-truth)
  - 不动 zadig_agent / Zadig 主体 (javert_* 表完全独立命名空间)
- **测试产出**:
  - `tests/test_sql_server_store.py`: pymssql 连接 / DDL idempotent / upsert_run 幂等性 / 鉴权流程 (mock 142 endpoint)
  - `tests/test_web_auth.py`: register / login / session cookie / logout
  - `tests/test_review_workflow.py`: POST review → insert javert_vio_review + 同步 javert_audit_logs; 重复审核 (同 user × 同 run) 被 UNIQUE 拒绝; insert-only 语义
  - `tests/test_sync_audits.py`: 干跑 + 实跑 (mock 142) 3345 条 round-trip 验证
- **文档产出**:
  - `docs/review_workbench_design.md`: 表 schema 全字段 + ER 图 + UI 截图
  - `docs/deployment_192_62.md`: 部署 runbook
- **里程碑分期**: Phase 0 (数据库准备) → Phase 1 (双写 + 一次性 import) → Phase 2 (登录系统) → Phase 3 (工作台核心) → Phase 4 (附加能力) — 约 10-12 天 (详见 tasks.md)
- **后续 change 解锁**:
  - `add-cross-patient-stats`: 利用 javert_vio_review 累积评语做规则质量评估
  - `add-rule-iteration-from-feedback`: 把驳回 (专家判 V→C) 反馈进规则 prompt 迭代
  - `add-mobile-review`: 手机端审核 UI (基础已有 SQL Server, 仅前端)
