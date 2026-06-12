## Phase 0. 数据库准备 (Day 1)

**目标**: 142 上 4 张表建好, pymssql 通, 凭证就位. 是后续所有 M 的硬前提.

**实施备注**: 既有代码已用 pyodbc + SQLAlchemy (zadig_agent v2.10.1 同源), 故 D1
"pymssql" 决策被推翻 — 保留现有 pyodbc 路径 (Linux 服务器需 unixODBC +
msodbcsql18, 部署 runbook 已写). 现有 `src/javert/store/sqlserver_store.py` 已实现
连接池 / NVARCHAR 中文兼容 / `Javert_audit_runs` 双写, 在其上扩展 3 张工作台表
即可, 不另建 `persistence` 包.

- [ ] 0.1 确认 142 凭证: 找 DBA / 运维确认 `machendong` 用户在 zadig 库有 `CREATE TABLE` 权限. 如无, 申请新角色或新 user — **待 Phase 5 落地时核**
- [ ] 0.2 在 Linux 服务器 192.168.31.62 安装 unixODBC + msodbcsql18 — **runbook 已列, 待真上线**
- [x] 0.3 项目加依赖: `passlib[bcrypt] itsdangerous python-multipart slowapi sse-starlette` (放 pyproject.toml dependencies, 因为工作台模式默认装上)
- [x] 0.4 ~~新 `persistence/connection.py`~~ → 复用 `store/sqlserver_store.py` 现有 Engine 单例, `get_sqlserver_store()` 已是 lazy-init + pool_pre_ping
- [x] 0.5 扩 `scripts/sql/create_javert_tables.sql`: 加 `javert_users` / `javert_vio_review` / `javert_audit_logs` 3 张表 + 全部索引; 原 `Javert_audit_runs` 不动
- [x] 0.6 新 `src/javert/store/models.py`: pydantic `User` / `ReviewRecord` / `AuditLogRecord` / `SinceLastLoginStats` / `PatientSidebarItem` / `RunWithReviews` / `DashboardStats` 等 (AuditRunRecord 复用 `audit.result.AuditResult`)
- [x] 0.7 新 CLI 命令 `javert ensure-mssql-schema` 注册到 `cli.py` (`src/javert/commands/ensure_schema.py`, 支持 --drop-first + JAVERT_ALLOW_DROP 安全闸)
- [ ] 0.8 设环境变量 `JAVERT_SQL_*` + `JAVERT_SESSION_SECRET` (Mac `.env`, Linux `/etc/default/javert-web`) — **运营动作, 待 Phase 5**
- [ ] 0.9 跑 `javert ensure-mssql-schema` 实际建表 — **需 142 凭证, 待 0.8 之后**
- [ ] 0.10 SSMS 或 `sqlcmd` 验证 4 张表 + 索引 + FK — **同上**
- [x] 0.11 单测局部覆盖: `tests/test_humanize_zh.py` (9 cases) / `test_web_auth_helpers.py` (5 bcrypt) / `test_workbench_templating.py` (13 模板+filter) / `test_export.py` (10 xlsx/csv) / `test_event_bus.py` (6 SSE bus) / `test_web_app.py` (21 TestClient 路由+鉴权). SqlServerStore SQL 层走 142 集成测试时再补
- [x] 0.12 写部署文档 `docs/deployment_192_62.md` 完整 runbook (env / service / sql / nginx 反代 / 备份)

## Phase 1. 双写 + 一次性 import (Day 2-3)

**目标**: 3345 条本地 audit_runs 进 142, 后续 audit 自动双写.

**实施备注**: 现有 `audit_store._ensure_v2_columns` + `result_persister.persist_one`
+ `web.api.heartbeat.SyncWorker` 三件套已实现"sqlite 先 mssql 后 + 失败 mark
pending + 后台 retry"全套语义. `_sync_pending` 用 `synced_at IS NULL` 等价语义实
现 (老库无字段时自动 ALTER 加 `synced_at` / `sync_attempts` / `sync_last_error`).
故大部分 Phase 1 已就绪, 仅缺 CLI 入口.

- [x] 1.1 ~~audit_runs 加 `_sync_pending` 列~~ → 既有 `synced_at` / `sync_attempts` / `sync_last_error` 三列等价
- [x] 1.2 ~~migration on startup~~ → 既有 `audit_store._ensure_v2_columns` ALTER 添加, 幂等
- [x] 1.3 ~~CompositeStore~~ → 既有 `result_persister.persist_one(result, rule, ...)` 包装 sqlite + 142 写, 失败 mark_sync_failed
- [x] 1.4 ~~Runner 用 CompositeStore~~ → Runner 不直接知道存储, 命令层 (commands/) 调 persist_one
- [x] 1.5 `audit-patient` summary 新增 `mssql_sync: N/M succeeded (P pending)` 行 + 待补 run_id 列表 (`slow_sync: [...]`) + `local-only mode` 提示
- [x] 1.6 `persist_one` 已实现失败 mark_sync_failed → 后台 SyncWorker 重试
- [x] 1.7 `SqlServerStore.write_audit` 已支持单条 upsert (`run_id` UNIQUE 去重); bulk 走 SyncWorker 心跳
- [x] 1.8 新 `src/javert/commands/sync_to_mssql.py`: --dry-run / --pending-only / --batch-size
- [x] 1.9 新 CLI `javert sync-to-mssql` 注册到 cli.py
- [ ] 1.10 单测 `tests/test_composite_store.py` — **跳过本次, 后续补**
- [ ] 1.11 单测 `tests/test_sync_audits.py` — **跳过本次, 后续补**
- [ ] 1.12 实跑 `--dry-run` — **需 142 凭证**
- [ ] 1.13 实跑 batch sync 3345 条 — **需 142 凭证**
- [ ] 1.14 SSMS COUNT 验证 — **需 142 凭证**
- [ ] 1.15 实跑双写 — **需 142 凭证**
- [ ] 1.16 模拟 142 挂掉验证 fallback — **需 142 凭证**
- [ ] 1.17 恢复 142 + `--pending-only` 补漏 — **需 142 凭证**

## Phase 2. 登录系统 + web app skeleton (Day 4-5)

**目标**: Javert web 可以 register/login, session 通, 中间件保护路由.

- [x] 2.1 改 `web/api/main.py`: `create_app(with_mssql)` 工厂, 注册 SessionMiddleware (starlette.middleware.sessions)
- [x] 2.2 新 `web/auth.py`: `hash_password` / `verify_password` (passlib bcrypt cost=12) + session helper
- [x] 2.3 新 `web/api/routes_auth.py`: `GET/POST /login`, `GET/POST /register`, `POST /logout`
- [x] 2.4 新 `web/middleware.py:AuthMiddleware`: 保护 `/workbench/*` `/review` `/dashboard` `/export` `/sse/*` `/api/banner` `/api/patient/*`, 未登录浏览器 302, API 401
- [x] 2.5 新 `web/templates/base.html`: 顶栏 + 中文字体栈 + 退出 form
- [x] 2.6 新 `web/templates/login.html` + `register.html`
- [x] 2.7 新 `web/static/style.css`: 蓝色商务风 (--primary `#1e40af` / verdict `#b91c1c #b45309 #047857` / `PingFang SC`)
- [x] 2.7a 新 `web/static/favicon.svg`: 32x32 `#1e40af` 底白 J
- [x] 2.8 新 `configs/web.yaml` (session_max_age 30d, cookie_name, https_only=false 暂时) + `JavertConfig` 加 `session_secret / session_max_age / session_cookie_name / session_https_only / session_same_site` 字段
- [x] 2.9 rate limiting (slowapi): `routes_auth.limiter` 全局 Limiter (key=remote_address), `POST /login` 限 5/min, `POST /register` 限 3/min; create_app 注册 `_rate_limit_exceeded_handler` 返 429
- [x] 2.10 `javert web` CLI 加 `--with-mssql/--no-mssql` flag, 默认走 `cfg.web_with_mssql`
- [ ] 2.11 单测 `tests/test_web_auth.py` — **跳过本次**
- [ ] 2.12 实跑 (Linux): `javert web --with-mssql --host 0.0.0.0 --port 8090` — **待 Phase 5**
- [ ] 2.13 手测注册→workbench→logout — **待 Phase 5**
- [ ] 2.14 SSMS 验证 javert_users + javert_audit_logs 行 — **待 Phase 5**
- [x] 2.15 `POST /login`: SELECT 老 last_login → stash session["prev_last_login"] → UPDATE → 302; first_login sentinel 保留

## Phase 3. 工作台核心 (Day 6-9)

- [x] 3.1 新 `web/api/routes_workbench.py`: `GET /workbench` + `GET /workbench/{patient_id}`
- [x] 3.2 `SqlServerStore.list_patients_with_violations(filter, user_id)`: 单 query 取 V/I/C 计数 + LEFT JOIN javert_vio_review 算当前 user reviewed_count + fully_reviewed flag
- [x] 3.2a `SqlServerStore.get_since_last_login_stats(user_id, since)`: ≤3 query, 返回 `SinceLastLoginStats` pydantic, since=None → 全工作区 count
- [x] 3.2b 新 `web/templates/welcome_banner.html` (Jinja2 partial): 首次登录 / 老用户 / 从未审核 三变体
- [x] 3.2c 新 `web/utils/humanize_zh.py`: `humanize_delta_zh` + `humanize_since_zh` 中文相对时间
- [x] 3.2d `POST /api/banner/dismiss`: set cookie `welcome_dismissed=<login_ts>`, workbench 渲染前查 cookie
- [x] 3.3 `SqlServerStore.list_runs_for_patient(patient_id, filter)`: 返回 List[RunWithReviews] (含 reviews JOIN javert_users)
- [x] 3.4 新 `web/templates/workbench.html` + `_sidebar.html`: 顶部 banner + 左 sidebar (patient cards + filter dropdown + 已审 N/M + ✓ 全审图标) + 右 panel (默认空)
- [x] 3.5 新 `web/templates/patient_detail.html`: header + 违规卡片 loop + 审核 form / read-only review + 其他专家行 + 原始病历按钮
- [x] 3.6 `POST /review` 路由 → submit_review → SSE broadcast `review_submitted`
- [x] 3.7 `SqlServerStore.submit_review` 事务: UPDATE 老 is_latest=0 → INSERT 新 is_latest=1 → INSERT javert_audit_logs (action=review_submit/review_update + previous_verdict)
- [x] 3.8 新 `web/static/app.js` (~250 行): form 提交 fetch → reload 显示; SSE 订阅; banner dismiss; modal
- [x] 3.9 新 `GET /api/patient/{pid}/raw`: 读 data/shi_fee.csv + case_notes.csv 返 JSON
- [x] 3.10 原始病历 modal (在 patient_detail.html + app.js, 不另起模板)
- [x] 3.11 filter 写 cookie `javert_filter` (30 天)
- [x] 3.12 单测 `test_workbench_templating.py` + `test_web_app.py` 覆盖 workbench 渲染 + 路由保护 (21 + 13 cases)
- [x] 3.13 单测 `test_web_app.py` test_with_mssql_review_api_unauth_401 (POST /review 未登录 → 401)
- [x] 3.14-3.20 端到端实跑 (2026-05-21 完成): J13365 → 21 张违规卡 dedup + 主诊 shi_zd; J61556 → 主诊 "非霍奇金淋巴瘤(B细胞型) C85.100x001" + 主手术腰椎穿刺术 (修了 shi_zd/shi_ss 列名 4 处 bug); J18122 fresh audit → SSE 推 1 个 new_audit_run → sidebar 实时 prepend
- [x] 3.21 单测 `test_humanize_zh.py` (9 cases) + welcome_banner 模板 render in `test_workbench_templating.py`

## Phase 4. SSE + dashboard + Excel (Day 10-12)

- [x] 4.1 新 `web/api/routes_sse.py`: `GET /sse/reviews` text/event-stream (sse_starlette EventSourceResponse) + 30s heartbeat
- [x] 4.2 `EventBus` asyncio Queue fan-out (per-process); `submit_review` publish `review_submitted`; `audit_watcher` publish `new_audit_run`
- [x] 4.3 `app.js`: EventSource listener review_submitted 增量插入 other-row; new_audit_run 走 4.4b
- [x] 4.4 单测 `test_event_bus.py` (6 cases: subscribe/publish fan-out/unsubscribe/queue full/datetime payload/subscriber count)
- [x] 4.4a 新 `routes_sse.AuditWatcher`: lifespan startup 启 task; 每秒 fetch_runs_since_id → publish; transient error 5s backoff. **(v0.6 hotfix)** 原本按 `created_at` 追踪, 但 SQL Server DATETIME2(7) 比 Python datetime(6 位 μs) 多 1 位精度, watcher 读 created_at 丢第 7 位 → `WHERE created_at > @last_seen` 永真死循环. 改用 `BIGINT IDENTITY id` 严格递增 (`fetch_runs_since_id` 新方法), 整数比较精确; 同时 NVARCHAR hook 改为只 coerce string param (datetime/int 让 driver auto-detect)
- [x] 4.4b `app.js` new_audit_run listener: is_new_patient → prepend sidebar card; else → update badges + 已审 N/M 分母; toast 4 秒自动消失
- [ ] 4.4c 单测 audit_watcher — **跳过 (集成测试性质, 在 4.4d 实跑覆盖)**
- [x] 4.4d 实跑跨进程 IPC (2026-05-21): SSE 30s 静置 0 推送 (loop bug 消失) + Mac audit-patient J18122 → SSE 1 推 (correct); J18122 R191 第 2 次 → SSE 1 推 (is_new_patient=false correct); 22 / 23 / 28 → batch 跑 J20598 / J22714 等 50 病人, 工作台 sidebar 实时 prepend (toast + 增量插卡)
- [x] 4.5 新 `GET /dashboard` 渲染汇总卡片 + leaderboard + per-rule table
- [x] 4.6 `SqlServerStore.dashboard_stats()`: 总进度 + reviewers + per-rule agreement
- [x] 4.7 新 `web/templates/dashboard.html`: 进度条 (CSS only, 不上 chart.js) + table
- [x] 4.8 新 `GET /export?format=xlsx&scope=v_and_i&include_history=false`
- [x] 4.9 openpyxl 多 sheet: 审核结果 / 审核进度 / 规则维度 / (可选) 审核历史; sheet 名中文
- [x] 4.9a 文件名 `javert_reviews_<YYYYMMDD>_<HHMMSS>.xlsx`, history 加 `_with_history` 后缀, Content-Disposition RFC5987
- [x] 4.10 `?format=csv`: UTF-8 BOM 单 sheet
- [x] 4.11 单测 `test_export.py` (10 cases: xl_cell datetime/bool/None / build_csv UTF-8 BOM + datetime / build_xlsx multi-sheet + 中文 sheet 名 + 截 31 字 + 空 sheet 兜底)
- [x] 4.12 dashboard 实测 (2026-05-21): smoke_xxx / Parker / shane 三账号各审若干, dashboard 出 1/767 进度 + leaderboard 显示 3 reviewers + 45 条规则 agreement table
- [x] 4.13 实跑导出 (2026-05-21): /export?xlsx → 33 KB 3 sheet (审核结果 585 / 审核进度 2 / 规则维度 46); /export?csv → UTF-8 BOM 中文 OK; /export?xlsx&patient_id=J18122 → 单病人 8 KB 仅 2 行 (J18122 的 2 条)
- [x] 4.14 多 tab SSE (2026-05-21): smoke + parker 两 tab 同时 connect, parker 改批复 → smoke 立刻看到 "parker: ..." 增量插入 others-reviews 区

## Phase 5. 部署 + runbook + 文档 (Day 12)

- [x] 5.1 落盘 `deploy/javert-web.service` (systemd unit) + `deploy/javert-web.env.example` (环境变量模板, 不入 git). **实装**: /etc/systemd/system/javert-web.service
- [x] 5.2 tar src/configs/scripts/deploy/data → scp 到 admin2@192.168.31.62:/home/admin2/javert/
- [x] 5.3 服务器: curl uv install → ~/.local/bin/uv; `uv sync --extra sqlserver`; `.env` 落 /home/admin2/javert/.env (mode 0600); msodbcsql18 + unixodbc 已预装
- [x] 5.4 `ensure-mssql-schema` 跑过 (4 表 + 2 视图 idempotent ready)
- [x] 5.5 `sync-to-mssql --batch-size 200` 跑过 (3345 行 ~3.5 min, 0 failed)
- [x] 5.6 `sudo systemctl enable --now javert-web` 跑过, `journalctl -u javert-web` 跟日志 active running
- [x] 5.7 浏览器内网测: http://192.168.31.62:8090/login 渲染 OK + register 注册 shane → /workbench (46 病人 sidebar) + 改密 + 提交 review + dashboard / export 全通
- [x] 5.8 写 `docs/deployment_192_62.md` (13 节完整 runbook + 故障排查表 + nginx 反代模板 + 升级流程 + 实测性能 + 已知约束)
- [x] 5.9 写 `docs/review_workbench_user_guide.md` 专家手册 (11 节, **拿账号** (运维分配, 注册关闭) → **首次登录改密** (强制) → 工作台 → 病案概览 → 违规审核 → 原始病历 Ctrl+F → SSE → Dashboard → 单/全量 Excel → 退出 → FAQ)
- [x] 5.10 给操作者发链接 (你自己跑): 用 `javert mssql-user create` 分账号给专家

## Bonus (本来不在 spec/proposal 内, 实际落了)

- [x] **mssql-user admin CLI**: `javert mssql-user list / create <user> / delete <user> [--confirm] / reset-password <user>`. `create` 是注册关闭后的主要分账号入口 — 操作者交互式输 2 遍默认密码, 用户拿到后首次登录走 `/account/password` 自助改
- [x] **/healthz** endpoint — 不需要登录, 返 `{status, mssql}` 给 monitoring 用
- [x] **/account/password 自助改密** (spec 未列, 上线必需): 三栏表单 (旧 + 新 + 确认) → bcrypt + UPDATE + audit_log + session 强制清空 → /login?msg=pw_changed 显示绿 banner. 旧密码错 401, 新旧相同 400
- [x] **关注册 (allow_register=False)**: GET/POST `/register` 都返 403 + 友好页面 "请联系运维". 顶栏 / 登录页都不再有 "注册" 链接. `JAVERT_ALLOW_REGISTER` env 可显式打开
- [x] **rule subtitle**: 违规卡顶部加 `{domain}.{violation_type}.{priority}.模板{Mx}` chip (跟原 router_v2 html 一致), 通过 `web/rule_meta.py` lazy 加载 111 yaml
- [x] **patient overview 块**: workbench detail 页顶部加可折叠概览块 (复刻 build_clerk_report 风格) — 病案基本信息 + 主诊 + 其他诊断 + 手术 + 费用结构 + 文书阶段. `web/patient_overview.py` 从 shi_zd/shi_ss/notes/fees 现场算
- [x] **shi_zd / shi_ss 列名 4 处 bug 修**: `inhosp_diag_name` 不是 `dx_name`, `inhosp_diag_code` 不是 `dx_code`, `ba_id` 不是 `bah`, `bilg_dept_name`/`acord_dept_name` 不是 `medins_chrgitm_dept_name`. 之前 cache 一直 0 patient
- [x] **sex/age regex extraction**: `_KEY_NOTES` 子阶段直取在所有病人都不命中. 新加 `_SEX_AGE_PATTERN = r"[，, ]([男女])[，, ]\d+岁"` 从 "病例特点" 内容前 300 字搜. 脱敏 "姓名:某某" 跳. 55 → 6 无性别 (剩 6 全是数据 genuinely 缺)
- [x] **单病人导出**: `/export?patient_id=Jxxx` 在违规列表头加 "⬇ 导出此病人" 按钮; 全量导出顶栏链接保留
- [x] **NVARCHAR hook 类型感知** (v0.6 hotfix): 原本盲目把所有 param coerce 成 SQL_WVARCHAR — datetime / int 被 string-roundtrip 丢精度. 改成只 string 才 coerce, datetime / int / None 用 None 占位 (driver auto-detect)
- [x] **audit_watcher 切 BIGINT id**: 原本按 `created_at` 追踪, DATETIME2(7) vs Python datetime(6) 精度丢失死循环 (`fetch_runs_since` 永真); 改用 `fetch_runs_since_id` + `BIGINT IDENTITY id` 严格递增
- [x] **vio_review 加 denormalized 列**: 原本只有 `(id, run_id, user_id, review_verdict, comment, created_at, is_latest)`; 新加 `patient_id` / `rule_id` / `username` 在 INSERT 时从 JOIN 复制 (老库 ALTER + UPDATE 回填). 同时建 `v_javert_reviews` JOIN 视图给 SSMS 用
- [x] **dedup latest-per-(rule_id, patient_id)**: workbench 4 个 read 方法 (`list_patients_with_violations` / `list_runs_for_patient` / `dashboard_stats` / `fetch_export_rows`) 全部用 `ROW_NUMBER() OVER (PARTITION BY patient_id, rule_id ORDER BY created_at DESC)` 取 latest. audit_runs raw 仍保留历史
- [x] **原始病历 2 tab + Ctrl+F**: modal 拆成文书 / 费用两 tab + 搜索栏 + `<mark>` 高亮 + Enter/↑↓ 跳转 + Esc 关. ~430 行 vanilla JS, 无第三方库
- [x] **K_DIRECT_SYNC sentinel 清**: May 9 留下的 `triggered_by=direct-test` 测试行 1 条, DELETE 掉

## Phase 6. 反馈迭代 (Day 13+, out of scope)

- [ ] 6.1 收集第一周反馈
- [ ] 6.2 决定下一个 change


## 二批扩量 (2026-05-21 → 22)

- [x] 用 batch_50_new.txt (50 J/K 5 位数字 + 50-400 段文书, seed=2026) 跑第二批 100 → 106 病人
- [x] 在 62 nohup detached 跑 `scripts/run_batch_new.py`, 总耗 8.7 h
- [x] 结果: 48 ok + 2 部分失败 (J82821 / K51953 LLM 永久超时, 其他规则照写). 累计 106 病人 / 5016 行 / **529 V** / 238 I / 3287 C
- [x] workbench sidebar 期间全程 SSE 实时同步, audit_watcher BIGINT id 追踪 不死循环
