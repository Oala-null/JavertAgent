## Context

Javert 经过 v0 → v0.5 五次迭代, 已完成:

- 7 大模板 (M1-M7) 装载 111 条 ready 规则 (Y 覆盖率 92.7%)
- 50 病人 router v2 全跑, 累计 1764 裁决 (275 V + 120 I + 1369 C)
- Router B 单闸 prefilter 砍 68% LLM 调用, 不漏检
- 生成 `output/router_v2_50patients.html` (1.06 MB, 左侧 patient sidebar + 右侧违规详情)

这是一个**完整的 AI 初判结果**。下一步必须让医保专家**逐条审核 + 写批复**, 否则 Javert 只是个 demo, 没有交付价值。当前的瓶颈不是 AI 准度 (V 抽样 8/9 真违规), 而是**专家审核流程缺位**: 静态 HTML 无法多人协作 / 无法持久化批复 / 无法跨次 audit 累积。

**已知约束**:
- LLM 网关 `192.168.31.62:30000` (Qwen3.5-35B, sglang) — Linux 服务器
- Zadig 后端 SQL Server `192.168.31.142:1433` (zadig 库, user=machendong) — 跨机房可访问, **Mac 本地未装 ODBC**
- Javert 现状: 全本地 SQLite (`output/audit.sqlite` 20.5 MB, 3345 裁决), 零跨机依赖
- Zadig 现有 web API (8080) 完全无鉴权, 所有端点暴露 — 不能复用
- Javert `web` CLI 命令已有骨架 (端口 8090, FastAPI), 当前仅规则浏览页面

**stakeholder**:
- 操作者 (你) — 配置、部署、维护
- 医保专家 (N 个, 数量未定) — 浏览 V/I 违规, 写评语, 提交决策
- 监管部门 — 最终接收 Excel 导出报告

## Goals / Non-Goals

**Goals:**

- 把 `router_v2_50patients.html` 静态报告变成**有登录 / 多人协作 / 批复入库**的在线工作台
- 专家在工作台上对每条 V/I 违规 (默认范围 395 条) 写「认同/驳回/改判 + 评语」, 持久化到 SQL Server 142
- Javert 后续跑新 audit (单病人 / 批量) **自动双写到 142**, 工作台实时可见
- 工作台 SSE 广播: 专家 A 审完一条, 专家 B 浏览器自动显示「张医生已审」
- 一键导出 Excel: 全部审核结果给监管部门
- 仪表盘: 谁审了多少 / 哪些规则 V 多 / 专家与 Javert 一致率

**Non-Goals:**

- ❌ **复用 Zadig 后端 web/鉴权** — Zadig 无鉴权且数据模型不同, 自建更快
- ❌ **多角色 RBAC** (admin/reviewer/viewer 分层) — 自助注册无审核, 单角色, 后续真需要再加
- ❌ **OAuth/SSO** — 内网工具, username/password 够用
- ❌ **多专家投票/二审制** — 单审制, 每个专家独立审, 不强制达成一致 (但可以看其他人审的)
- ❌ **改 audit_runs 表 schema** — 142 上的 `javert_audit_runs` 镜像 sqlite 字段, 不增不减
- ❌ **手机端 / 移动审核** — 桌面浏览器优先, 移动端留给后续 change
- ❌ **在工作台触发 Javert 跑** — 工作台只读 + 评审, 不调度 audit (audit 仍是 CLI 触发, 后续可加)
- ❌ **审核结果回写 sqlite** — 142 单向存; sqlite 仅存 audit_runs (本地 source-of-truth)
- ❌ **修改专家批复** — insert-only, 老批复永不消失, "改" 走插新行 + audit_logs trail
- ❌ **病人原始数据 (fee/notes) 也搬 142** — 病人数据仍走 `data/*.csv` (随 audit 数据 join 时本地读), 不入 142

## Decisions

### D1. SQL Server 驱动: pymssql 而非 pyodbc

**选项**:
- (a) `pyodbc` + Microsoft ODBC Driver 18 for SQL Server (Zadig 当前用)
- (b) `pymssql` (纯 Python, 基于 FreeTDS, 但 wheel 已经 bundle)

**选 b**, 因为:
- **Mac 本地开发不装 ODBC**: pyodbc 要装 unixODBC + msodbcsql18 (homebrew 不一定有, 走 microsoft repo 30 分钟起步), 而 pymssql 是 `uv add pymssql` 一行
- **Linux 部署也省事**: 192.168.31.62 上 pymssql wheel 直接装, 不依赖系统 ODBC
- **Zadig 用 pyodbc 不是约束**: javert_* 表是独立命名空间, 驱动选什么由 Javert 决定; Zadig 改不改和我们无关
- **代价**: pymssql 比 pyodbc 略慢 (~5-10% latency), 但 web 工作台的查询都是 ms 级, 用户感知不到; bulk insert 走 `executemany` 也够快
- 缺点: pymssql 社区比 pyodbc 小, 但成熟稳定 (维护 15+ 年)

### D2. 全表放 zadig 库 (不分库) + javert_* 前缀命名空间

**选项**:
- (a) 142 上新建 `javert` 独立库, 完全隔离
- (b) 全放 zadig 库, 用 `javert_*` 前缀

**选 b** (用户明确要求), 因为:
- 用户决策: "全部放在 zadig 那个库里 新建表就好"
- 跨表 join 方便 (未来若要关联 Zadig `patients` 表查患者人口学数据, 同库 join 简单)
- 单一 DB 凭证管理简单
- 命名隔离靠前缀: `javert_users` / `javert_audit_runs` / `javert_vio_review` / `javert_audit_logs`
- 风险: 误操作可能动到 Zadig 表 — 缓解: SqlServerStore 内所有 SQL 写死 `WHERE table_name LIKE 'javert_%'`, 不暴露 raw query API

### D3. 4 张表分开建, 不合 1 张

**用户原话**: "用户id和操作行为存库在142同一张表" — 当面澄清后确认是 "user 表和 review 表分开"

**最终设计 — 4 张表**:

```sql
-- 表 1: 用户主表
CREATE TABLE javert_users (
  id INT IDENTITY(1,1) PRIMARY KEY,
  username NVARCHAR(64) NOT NULL UNIQUE,
  pw_hash NVARCHAR(255) NOT NULL,  -- bcrypt
  display_name NVARCHAR(128),       -- 可空, 默认 = username
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  last_login DATETIME2 NULL
);

-- 表 2: audit_runs 镜像 (3345 + 后续累积)
CREATE TABLE javert_audit_runs (
  run_id NVARCHAR(64) PRIMARY KEY,
  patient_id NVARCHAR(32) NOT NULL,
  rule_id NVARCHAR(16) NOT NULL,
  verdict NVARCHAR(16) NOT NULL,     -- VIOLATION / CLEAN / INCONCLUSIVE
  confidence DECIMAL(4,3) NULL,
  reasoning NVARCHAR(MAX) NULL,
  evidence_json NVARCHAR(MAX) NULL,
  tool_calls_json NVARCHAR(MAX) NULL,
  duration_ms INT NULL,
  model NVARCHAR(64) NULL,
  started_at DATETIME2 NULL,
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  triggered_by NVARCHAR(64) NULL,
  INDEX ix_javert_runs_patient (patient_id),
  INDEX ix_javert_runs_rule (rule_id),
  INDEX ix_javert_runs_verdict (verdict)
);

-- 表 3: 专家批复 (insert-only)
CREATE TABLE javert_vio_review (
  id INT IDENTITY(1,1) PRIMARY KEY,
  run_id NVARCHAR(64) NOT NULL,
  user_id INT NOT NULL,
  review_verdict NVARCHAR(16) NOT NULL,  -- V / I / C (专家三态)
  comment NVARCHAR(MAX) NULL,
  created_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  is_latest BIT NOT NULL DEFAULT 1,       -- 同 user × 同 run 只有最新行 is_latest=1
  CONSTRAINT fk_review_run FOREIGN KEY (run_id) REFERENCES javert_audit_runs(run_id),
  CONSTRAINT fk_review_user FOREIGN KEY (user_id) REFERENCES javert_users(id),
  INDEX ix_review_run (run_id),
  INDEX ix_review_user (user_id),
  INDEX ix_review_latest (run_id, user_id, is_latest)
);

-- 表 4: 用户行为审计 trail (insert-only, 永不删)
CREATE TABLE javert_audit_logs (
  id BIGINT IDENTITY(1,1) PRIMARY KEY,
  user_id INT NULL,                       -- 可空 (未登录的 register/login 失败也记)
  action NVARCHAR(32) NOT NULL,           -- register/login/login_fail/logout/review_submit/review_update/export
  target_id NVARCHAR(64) NULL,            -- 如 run_id (review 时) / username (login 时)
  payload_json NVARCHAR(MAX) NULL,        -- 携带具体内容 (e.g. review verdict + comment)
  ip NVARCHAR(45) NULL,                   -- IPv4/IPv6
  user_agent NVARCHAR(255) NULL,
  ts DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
  INDEX ix_logs_user (user_id),
  INDEX ix_logs_action (action),
  INDEX ix_logs_ts (ts)
);
```

**为何 review 不带 UNIQUE 约束反而带 is_latest 字段**:
- 用户要求 "所有人工更新都要留log" → review 必须 insert-only
- 但查询时需要 "同 user × 同 run 的最新审核" — 走 `WHERE is_latest=1`
- 写入时事务: BEGIN → UPDATE is_latest=0 WHERE run_id=? AND user_id=? → INSERT 新行 is_latest=1 → COMMIT
- 既保留历史 (老行 is_latest=0 永存), 又方便查询 (新行 is_latest=1 唯一)

### D4. 单角色 + 自助注册 + 无审核

**用户原话**: "到了就开 不要审核"

**实现**:
- `/register` POST username + password → bcrypt hash → `INSERT javert_users` → set session cookie → 重定向 `/workbench`
- 任何人到 `/register` 都能开账号, 无邀请码, 无邮箱验证, 无 admin 批准
- 无 role 字段 (用户表无 `role` 列): 全部用户能力相同 (审核 + 看仪表盘 + 导出)
- 风险: 公网部署会被恶意注册刷库 — 缓解: **仅内网部署 192.168.31.62**, 不暴露公网。若未来要公网开放, 加 `add-invite-code` change

### D5. 单审制 + insert-only + audit_logs 双写

**用户原话**: "insert 所有人工更新都要留log"

**实现**:
- POST `/review {run_id, verdict, comment}` 走事务:
  1. SELECT 现有 is_latest=1 行 → 有则 UPDATE is_latest=0
  2. INSERT 新行 (is_latest=1, 新 verdict, 新 comment)
  3. INSERT javert_audit_logs (action='review_submit' 或 'review_update', target_id=run_id, payload_json={'verdict':..., 'comment':..., 'previous_verdict':...})
  4. COMMIT
- 查询 "某条 violation 谁审了什么": `SELECT * FROM javert_vio_review WHERE run_id=? AND is_latest=1`
- 查询 "某条 violation 的审核历史": `SELECT * FROM javert_vio_review WHERE run_id=? ORDER BY created_at`
- 导出 Excel 默认按 is_latest=1; "完整历史" 模式取全部
- audit_logs 是 cross-cutting trail: 不仅记 review_submit, 还记 register/login/login_fail/logout/export — 任何 mutating 行为都记

### D6. 双写策略: 本地 sqlite 仍是 source-of-truth, 142 是镜像

**选项**:
- (a) Javert audit 直接写 142, 废除本地 sqlite
- (b) 双写: 先写 sqlite (强一致) 后写 142 (best-effort)
- (c) 仅写 sqlite, 周期 cron sync 142

**选 b**, 因为:
- 142 在 Linux 服务器机房, 偶尔抖动不该阻断本地 audit
- 本地 sqlite 单文件备份/迁移容易
- 双写允许独立查问题: sqlite 看 audit 是否真跑了, 142 看 web 是否看得到
- 失败回退路径清晰: 142 写失败 → log warning + 标记 `_sync_pending` → 启动时 `sync-to-mssql` 补
- 缺点: 两份数据可能短暂不一致 (秒级), 但 web 工作台容忍这点延迟

**双写代码点**:
- `src/javert/audit/runner.py`: `Runner.audit` 末尾 → `SqliteStore.upsert_run()` + try `SqlServerStore.upsert_run()` (catch 后 log + skip)
- `src/javert/commands/audit_patient.py`: 每条规则 `persist_one(result, rule, store=composite_store)` 同时写两个 store
- `scripts/sync_audits_to_mssql.py`: 一次性把 3345 条 sqlite → 142 + 后续补漏 (查 sqlite WHERE synced_at IS NULL 的, 推 142)

### D7. Web 部署到 Linux 192.168.31.62 (Zadig sglang 同机)

**用户原话**: "是那个 62"

**实现**:
- 192.168.31.62 已跑 sglang on :30000, 8090 端口空闲
- 部署形态: `scp src/ + configs/ + data/audit.sqlite (initial seed)` → `uv sync` → `systemd unit` 启动 javert web
- 系统服务: `systemctl start javert-web` → `journalctl -u javert-web -f` 看日志
- 内网域名/IP: `http://192.168.31.62:8090/` (后续可走 nginx 反代加 SSL)
- **Mac 本地仍可开发**: `javert web --no-mssql` 跑 sqlite-only 模式, 不连 142, 仅用于 UI 调试
- 凭证 (142 user/pw + session secret) 走 `JAVERT_MSSQL_*` env, 不进 git

### D8. 范围 filter: 默认 V+I (395), 可切 ALL (1764)

**用户原话**: "UI 默认 V+I, 可手动切 ALL"

**实现**:
- 工作台顶部 `<select>`: `[默认 V+I (审核所需)] / [仅 V] / [仅 I] / [ALL]`
- URL query: `/workbench?filter=v_and_i` (default) / `filter=v_only` / `filter=i_only` / `filter=all`
- 病人 sidebar 也跟随 filter (e.g. ALL 时 J40485 0 V 但显示, V+I 模式下 J40485 隐藏)
- 每个病人卡片角标显示当前 filter 下命中数量
- Filter 状态写 cookie, 跨会话保持

### D9. UI 形态: 服务端 Jinja2 渲染 + 轻 JS, 不上 SPA

**选项**:
- (a) Jinja2 服务端渲染 + 表单 POST + 轻 AJAX
- (b) Vue/React SPA + REST API + axios
- (c) Server-side React (Next.js) / Astro

**选 a**, 因为:
- pilot 团队规模小 (N 个专家, N < 10), 不需要 SPA 的复杂前端态管理
- Jinja2 模板天然支持 verdict badge / sidebar 渲染, 复用 `scripts/build_50_html.py` 的 HTML 字符串拼接
- 浏览器纯 fetch + simple JS (~200 行) 处理 SSE 订阅 + POST review + filter 切换
- 不引入 npm / webpack / vite 工具链, 部署只需 Python
- 缺点: 复杂交互 (拖拽 / 富文本编辑) 难做 — 但这个工作台**没有此需求**
- 后续若专家反馈"想要更花哨", 再决定升级 SPA

**配色 (用户决策: 统一蓝色商务风, 不沿用 router_v2 报告红/黄/绿)**:

- **基础配色**: 深蓝商务 token
  - `--primary`: `#1e40af` (深蓝, 主按钮 / 顶栏 / 链接)
  - `--primary-hover`: `#1d4ed8`
  - `--accent`: `#3b82f6` (中蓝, 强调元素 / focus ring)
  - `--bg-light`: `#eff6ff` (极浅蓝, card 背景)
  - `--bg`: `#ffffff`
  - `--text`: `#1e293b` (slate-800)
  - `--text-muted`: `#64748b` (slate-500)
  - `--border`: `#e2e8f0` (slate-200)
- **Verdict badge 仍保留语义色, 但调成专业暗色** (合规场景 verdict 颜色是功能性的, 不只装饰; 完全用蓝色无法分辨 V/I/C):
  - V (VIOLATION): `#b91c1c` 深红 + 白字 (取代原 router_v2 的 `#dc2626` 艳红)
  - I (INCONCLUSIVE): `#b45309` 深琥珀 + 白字 (取代原 `#d97706`)
  - C (CLEAN): `#047857` 深绿 + 白字 (取代原 `#16a34a`)
- 字体: `'PingFang SC', 'Microsoft YaHei', system-ui, sans-serif` (中文优先)
- 设计语言: card 浅蓝背景 + 1px slate-200 边框 + 圆角 6px + subtle shadow; 顶栏深蓝实心; 按钮深蓝
- 此配色应用到所有页面 (login / register / workbench / patient_detail / dashboard / welcome banner)
- favicon: 简单 "J" 字母深蓝底白字 SVG, 不用第三方图标库

### D10. SSE 广播: 两类事件 (review_submitted + new_audit_run)

**实现**:
- 端点 `GET /sse/reviews` (text/event-stream, 名字保留向后兼容; 实际既推 review 也推 audit)
- 浏览器 EventSource 订阅, server side 心跳 30s
- **事件 1: `review_submitted` `{run_id, reviewer_username, verdict}`** — 仅广播这 3 字段, 不广播评语 (防止评语彼此影响, 评语点击展开才看)
- **事件 2: `new_audit_run` `{run_id, patient_id, rule_id, verdict, is_new_patient}`** — 详见 D13 (DB poll watcher 触发)
- 前端按 event 名分流处理: `review_submitted` → 在违规卡片底部追加评论行; `new_audit_run` → sidebar 增量插入新病人卡片 (或更新已有计数) + 顶部 toast 提示
- 后端 EventBus 用 asyncio Queue 简化 (`sse_starlette`), 不引入 Redis Pub/Sub; 两类事件共用同一个 Queue, 客户端单连接

### D11. Excel 导出格式

**实现**:
- 端点 `GET /export?format=xlsx&scope=v_and_i&include_history=false`
- 文件名规范 (用户要求 snake_case 英文 + 时间戳): `javert_reviews_<YYYYMMDD>_<HHMMSS>.xlsx` (e.g. `javert_reviews_20260520_143022.xlsx`); CSV 同理 `javert_reviews_20260520_143022.csv`; `include_history=true` 时文件名加后缀 `_with_history`
- openpyxl 生成多 sheet workbook (sheet 名走中文, 数据在内):
  - Sheet 1 "审核结果": 一行一条 review, 列 = run_id / patient_id / rule_id / javert_verdict / javert_conf / reviewer / review_verdict / comment / reviewed_at
  - Sheet 2 "审核进度": 按 reviewer 汇总 (谁审了多少 / 一致率)
  - Sheet 3 "规则维度": 按 rule_id 汇总 (V 数 / 专家驳回率)
- `include_history=true` 时 Sheet 4 "审核历史": 全部 javert_vio_review 行 (含 is_latest=0 的老批复)

### D12. 登录欢迎 banner: "自上次登录的增量"

**用户原话**: "每次登录要显示上新了几个病人 几个违规 几个意思 距离上次提交批复"

**字段解读**:
- "上新了几个病人" = 自该 user 上次 last_login 之后, 新出现的 patient_id 数量 (即 javert_audit_runs 里 created_at > prev_last_login 的 DISTINCT patient_id)
- "几个违规" = 自上次 last_login 后新增的 verdict='VIOLATION' 行数
- **"几个意思"** = 假设为 verdict='INCONCLUSIVE' 行数 (中文俚语 — "结果不明确" 简称 "意思")。**该假设未与用户最终确认, 若实意非此, 调 spec D12 + persistence get_since_last_login_stats**
- "距离上次提交批复" = (now - max(javert_vio_review.created_at WHERE user_id=current AND is_latest=1)) 的相对时间字符串 (e.g. "3 天前" / "刚刚" / "从未审核")

**实现**:
- 登录成功后跳 `/workbench`, 工作台顶部插一个 **dismissible banner** (不另开 /welcome 页, 减少 navigation):
  ```
  ┌─────────────────────────────────────────────────────────────────┐
  │  欢迎回来, alice                                          [✕]    │
  │  自上次登录 (2 天前): +3 病人 / +15 违规 / +7 不明              │
  │  你上次提交批复: 1 天前                       [立即开始审核]    │
  └─────────────────────────────────────────────────────────────────┘
  ```
- banner 关闭后写 cookie `welcome_dismissed_<user_id>=<login_ts>`, 同一次登录会话内不再显示, 下次登录重置
- 首次注册用户 (prev_last_login=NULL) banner 显示 "首次登录: 欢迎. 工作区有 X 病人 / Y 违规待审"
- 从未提交批复的用户显示 "你尚未提交批复"
- 服务端单 query 算出全部增量数: `SqlServerStore.get_since_last_login_stats(user_id, since=prev_last_login) → {new_patients, new_violations, new_inconclusive, last_review_at}`
- 注意 last_login 时机问题: login 路由会更新 last_login, 但 banner 要的是 **本次登录之前** 的 last_login。解决: login 路由先 SELECT 老 last_login 存进 session, 再 UPDATE last_login; banner 渲染时从 session 读老值

### D13. SSE 实时 new_audit_run 广播 — DB poll 跨进程

**用户决策**: web 工作台要实时显示新跑出来的病案 (audit-patient CLI 跑完, 专家浏览器自动看到新病人/新违规, 不需要刷新)

**跨进程 IPC 难题**: audit-patient 是 CLI 进程, web 是另一个进程, 两边怎么通信?

**选项**:
- (a) **DB 轮询** (SSE handler 每秒 poll `MAX(created_at) FROM javert_audit_runs`, 拿增量行推)
- (b) **Redis Pub/Sub** (audit-patient 写完 publish, web 订阅)
- (c) **SQL Server Service Broker** (重, 不推荐)
- (d) **内部 HTTP webhook** (audit-patient 写完 POST `localhost:8090/api/internal/audit-done`)
- (e) **文件 inotify** (touch trigger 文件)

**选 (a) DB 轮询**, 因为:
- **零新依赖**: 不引入 Redis, 不写 webhook 端点, 不要 inotify 跨平台兼容代码
- **跨机解耦**: audit-patient 不需要知道 web 在哪个 host:port (可能未来 web 在 Linux, audit 在另一台机器)
- **142 已是 source-of-truth**: 直接 poll DB 而不是搭额外消息中间件; DB 写入就是 fact
- **1 秒延迟可接受**: pilot 阶段专家不需要亚秒级实时
- **简单可靠**: SELECT 走 `ix_javert_runs_created_at` 索引 (M1 schema 已加), 单 query 100 行查 < 10ms; 即使 100 个浏览器订阅, 也只是一次 DB query fan-out 到 100 个 SSE queue

**实现**:
```python
# 在 web app 启动时注册 background task
async def audit_watcher_task():
    last_seen = await get_initial_max_created_at()  # 启动时拿当前 max
    while True:
        await asyncio.sleep(1.0)
        new_runs = mssql.fetch(
            "SELECT run_id, patient_id, rule_id, verdict, confidence, created_at "
            "FROM javert_audit_runs WHERE created_at > %s ORDER BY created_at",
            (last_seen,)
        )
        for run in new_runs:
            await event_bus.publish('new_audit_run', {
                'run_id': run.run_id,
                'patient_id': run.patient_id,
                'rule_id': run.rule_id,
                'verdict': run.verdict,
                'is_new_patient': not await mssql.has_other_runs(run.patient_id, exclude_run_id=run.run_id)
            })
            last_seen = run.created_at
```

**前端处理 (app.js)**:
```javascript
es.addEventListener('new_audit_run', (e) => {
    const data = JSON.parse(e.data);
    if (data.is_new_patient) {
        // sidebar 增量插入新病人卡片 (排序自动重排)
        insertPatientCard(data.patient_id);
    } else {
        // 已有病人卡片更新计数 + 进度
        updatePatientBadges(data.patient_id, data.verdict);
    }
    // 顶部 toast: "新增 1 条审计: J88888 R191 → V"
    showToast(`新增审计: ${data.patient_id} ${data.rule_id} → ${data.verdict}`);
});
```

**冷启动 last_seen**: web app 启动时 `SELECT MAX(created_at) FROM javert_audit_runs`, 之后增量。重启 web 不会重推老数据。

**与 review_submitted 共用 EventBus**: 两类事件用同一个 asyncio Queue, 客户端按 event 名分流处理 — 简化 SSE handler。

**边缘场景**:
- audit 写 142 失败 (走 _sync_pending) → 走 `javert sync-to-mssql --pending-only` 补漏后, watcher 自动 poll 到, 同样推 — 不需要特殊路径
- 多个 web 进程 (未来 scale): 每个进程独立 poll + 独立 fan-out 给自己的 SSE 客户端; DB query 重复但小, 无 race

## Risks / Trade-offs

### R1. 142 SQL Server 可能挂或断网

**影响**: Javert audit 双写失败 (本地仍成功) + web 工作台无法启动 (142 是 review 表所在)
**缓解**:
- 启动时健康检查 `SELECT 1`, 失败时 log warning, web 仍起服务但 review 路由返回 503 + UI 显示 "数据库不可用, 请联系管理员"
- 双写失败时记录 `_sync_pending=1` 到 sqlite, `sync-to-mssql` 命令可补
- 不做自动 failover (无足够工程价值)

### R2. 自助注册被刷库

**影响**: 公网部署会有数十万恶意账号
**缓解**:
- 内网部署 (192.168.31.62 仅内网可访问), 默认风险接受
- 加 rate limiting 中间件 (`slowapi`): 同 IP 1分钟最多 3 次 register, 防爆破
- 后续若上公网, 加 `add-invite-code` change 锁定注册

### R3. 多专家审核语义混乱

**影响**: 专家 A 判 V, 专家 B 判 C, 监管部门看哪个? insert-only 留 trail 但 "最终决策" 是哪条?
**缓解**:
- pilot 阶段单审制, 不强制达成一致, 每条 review 是独立观点
- 导出时按 reviewer 分组, 监管部门可看 N 份意见并查
- 若专家间反复审改, **每次都 insert 新行**, 但 is_latest=1 保留每个 reviewer 的最新观点
- 后续若需要 final verdict (e.g. 投票或仲裁), 开 `add-voting-review` change

### R4. SSE 长连接占用

**影响**: 每个浏览器 tab 一个长连接, 10 专家同时在线 = 10 个长连接占 server
**缓解**:
- FastAPI / uvicorn 默认 1000+ 并发连接没问题, pilot 规模 10 远不到瓶颈
- 心跳 30s + 客户端断线重连
- 浏览器 tab 关掉自动断
- 后续若上百专家再上 Redis Pub/Sub

### R5. 双写一致性

**影响**: sqlite 成功 + 142 失败 → 用户在 web 看不到这条 audit
**缓解**:
- 失败时 sqlite 标记 `_sync_pending=1`
- `sync-to-mssql` 定时跑补漏 (cron 或手动)
- 启动时 web `/sse/reviews` 第一次推送时, server side 查 sqlite 的 _sync_pending 提示用户

### R6. 部署对 sglang 性能影响

**影响**: javert web 在 192.168.31.62 跑会和 sglang 争 CPU / 内存
**缓解**:
- web 工作台 CPU 占用极低 (FastAPI + uvicorn 单 worker 够), 内存 < 200MB
- sglang 是 GPU bound, CPU 用得很少
- 实际冲突预期为零
- 若仍担心, 后续部署到独立 Linux 机

### R7. pymssql Mac 安装陷阱

**影响**: Mac 上 `uv add pymssql` 可能因 FreeTDS 链接问题失败
**缓解**:
- pymssql 现代 wheel (≥2.2) bundle FreeTDS, 无需系统包
- 若 wheel 失败, fallback: `brew install freetds` + `uv add pymssql`
- Mac 本地连不上 142 是 OK 的, dev 走 `--no-mssql` 即可
- Linux 服务器上肯定能装

## Migration

**3345 条本地 sqlite audit_runs → 142 javert_audit_runs**:

1. Phase 0 阶段先建表 (4 张 DDL idempotent)
2. Phase 1 阶段 `javert sync-to-mssql --dry-run` 看 plan (期望: 3345 条 INSERT, 0 UPDATE)
3. `javert sync-to-mssql --batch-size 200 --commit-every 50` 实跑 (~5 分钟)
4. 验证: SELECT COUNT(*) FROM javert_audit_runs → 应该 = sqlite 行数
5. 启用双写 (audit-engine modification)
6. Cron 每 6 小时跑 `sync-to-mssql` 补漏 (catch 双写失败的)

**用户表初始化**: 无 — 第一个专家手动 /register 注册, 操作者用 SQL `UPDATE javert_users SET role='admin' WHERE username='shane'` 提权 (虽然 D4 决定单角色, 但保留 admin 字段不用作未来兜底)。

**等等 — D4 说无 role 字段**, 那 admin 提权怎么办? **决定**: 删 admin 概念, 操作者维护数据库走 SSMS / 直连 SQL 命令, 不走 web。

## Open Questions (RESOLVED 2026-05-20)

1. ~~**142 凭证发放**~~ ✅ **Resolved**: machendong 用户有 CREATE TABLE 权限, Phase 0 直接跑 `ensure-mssql-schema`
2. ~~**session secret 怎么生成 / 存哪里**~~ ✅ **Resolved**: `python -c "import secrets; print(secrets.token_urlsafe(32))"` 一次性生成, 存 `/etc/default/javert-web` (Linux 服务器, root:root mode 0600) + Mac 本地 `.env` (gitignore); 不进 git, 不进 configs/web.yaml
3. ~~**配色和 favicon**~~ ✅ **Resolved**: 统一蓝色商务风 (详见 D9 配色 token 表); favicon = 深蓝 "J" SVG; verdict badge 保留语义色 (深红/深琥珀/深绿) 因为 V/I/C 区分是功能性需求
4. ~~**导出文件名规范**~~ ✅ **Resolved**: `javert_reviews_YYYYMMDD_HHMMSS.xlsx` (snake_case + 时间戳, 详见 D11)
5. ~~**sidebar 已审条数显示**~~ ✅ **Resolved**: 显示, 格式 "已审 5/18"; **同时新增**登录欢迎 banner 显示自上次登录的增量 (详见 D12)

## Remaining Assumptions (低风险, 不阻塞 Phase 0)

- **A1. "几个意思" = INCONCLUSIVE**: D12 假设用户口语 "意思" 指 verdict=INCONCLUSIVE 的行数。若实意是 review-pending (新增但未审) / 别的语义, 调 D12 + `get_since_last_login_stats` 实现, 不动表结构
- **A2. admin 维护走 SSMS 直连**: D4 决定无 role 字段, 操作者 (你) 维护数据库 (改密码 / 删用户 / 看 logs) 走 SSMS 或 `javert mssql-user` CLI; web 工作台所有用户能力相同, 无 admin 面板
- **A3. 首次登录 banner 文案**: prev_last_login=NULL 时显示 "首次登录, 工作区有 X 病人 / Y 违规待审"; 这是 reasonable default, 后续可调
