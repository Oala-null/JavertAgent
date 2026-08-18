# Javert Web 审核工作台 — 192.168.31.62 部署 runbook

**状态**: 🟢 已上线. systemd `javert-web.service` 运行中.

部署日: 2026-05-21. 部署初始数据基线: 106 病人 / 5016 audit_runs 行 / 529 V 待审
(当时 142 累计 13880 行含历史)；当前数量以工作台实时统计为准.
工作台版本: **v0.9** — 违规卡「命中项目」块 (编码·名称·限定) + 点证据右侧 **parallel 滑出原文对照面板** + 病人列表 facet (tag/费用/主诊/时间) + 费用类别就地展开. 详见 `review_workbench_user_guide.md`.
> **2026-06-03 UI 增量** (升级附加步骤见 §10.1): 病人列表按金额/生成时间排序 + 文书/费用右侧新增「检验记录」tab (检验+检查合并, 命中可 trace 跳转) + 命中名修复 (占位 locator → 具体药品/项目名) + 违规卡 AI 推理/证据块默认展开.

---

## 1. 服务器现状

| 项 | 值 |
|----|----|
| 主机 | `192.168.31.62` (Ubuntu 24.10, 6.11.0-19-generic) |
| 用户 | `admin2` (SSH key auth) |
| 端口 | **8090** (TCP, 仅内网) |
| 项目路径 | `/home/admin2/javert/`（`production-62` 稀疏 Git 工作树） |
| Python | 3.12.7 (系统) |
| uv | `/home/admin2/.local/bin/uv` (curl 装的, 用户级) |
| 系统 ODBC | `msodbcsql18` 18.6.1.1 + `unixodbc` 2.3.12 (apt 已装) |
| 同机服务 | Qwen3.6 FP8 / sglang :30000；W2 :30002 与 OCR :30001 当前已停；gdparse :8889、open-webui :8080、lethe :18090 |

---

## 2. 凭证 / 环境变量

**`.env`** 在 `/home/admin2/javert/.env` (mode 0600, 不入 git). 内容模板见 `deploy/javert-web.env.example`. 当前实际值由 systemd 通过 `EnvironmentFile` 加载:

```bash
# 142 SQL Server 双写
JAVERT_SQL_ENABLED=true
JAVERT_SQL_HOST=192.168.31.142
JAVERT_SQL_PORT=1433
JAVERT_SQL_DATABASE=zadig
JAVERT_SQL_USER=machendong
JAVERT_SQL_PASSWORD=<DBA-issued>
JAVERT_SQL_DRIVER=ODBC Driver 18 for SQL Server

# 工作台 session
JAVERT_SESSION_SECRET=<32+ 字符 token_urlsafe>
JAVERT_SESSION_HTTPS_ONLY=false   # 内网 http; 上 nginx TLS 后切 true
JAVERT_SESSION_MAX_AGE=2592000    # 30 天
# ⚠ harden-onsite-redlines 起 SESSION_SECRET 为**硬性启动前提** (with_mssql 生产形态):
# 缺失/仍是源码默认值时 create_app() 抛错拒绝启动 — `javert web` 与 `uvicorn ...main:app`
# 直起同样拦截 (此前 uvicorn 直起可绕过 CLI 检查). 本地 dev 用 `javert web --no-mssql` 不受限.

# raw 原文端点 (/api/patient/{pid}/raw) 每会话限流 — 默认 30/minute (slowapi 语法).
# 专家逐个点开病历不受影响; 脚本枚举患者号被 429 (且 429 请求同样落审计日志可追查).
# 现场误伤时一行调档: JAVERT_RAW_RATE_LIMIT=60/minute

# 工作台
JAVERT_WEB_HOST=0.0.0.0
JAVERT_WEB_PORT=8090
JAVERT_WEB_WITH_MSSQL=true
JAVERT_ALLOW_REGISTER=false       # 注册关闭, 走 mssql-user create

# LLM (audit-patient 跑 audit 时)
JAVERT_LLM_ENDPOINT=http://192.168.31.62:30000/v1
JAVERT_LLM_MODEL=Qwen/Qwen3.6-35B-A3B-FP8

# 肿瘤医保资格 v2（代码默认 off；62 于 2026-07-17 验收后启用）
JAVERT_ONCOLOGY_ELIGIBILITY_V2=on
# 生效期闸（代码默认 true；62 于 2026-07-18 设 false = 不分时间全部生效 + 窗口外核查提示）
JAVERT_ONCOLOGY_ENFORCE_EFFECTIVE_DATE=false
```

`JAVERT_ONCOLOGY_RELEASE_DIR` 对本 change 在 62 **不应设置**。只有专家批准、发布授权、
published bundle 部署和回滚演练均完成后，才能按 §10.7 把它指向本地 release 目录。
留空时继续读现行 `configs/` 离线资产，不改变 2026-07-18 已验收的 legacy 行为。

---

## 3. systemd 单元

文件: `/etc/systemd/system/javert-web.service`. 源码同 `deploy/javert-web.service`:

```ini
[Unit]
Description=Javert Web — 自查自纠规则审计 + 专家审核工作台
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=admin2
Group=admin2
WorkingDirectory=/home/admin2/javert
EnvironmentFile=/home/admin2/javert/.env
ExecStart=/home/admin2/.local/bin/uv run javert web --with-mssql
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
KillSignal=SIGINT

NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

**⚠ admin2 没有 passwordless sudo** — `sudo systemctl restart` 在非交互 SSH 下会要密码 (失败). 读类命令无需 sudo:

```bash
systemctl status javert-web                       # 看状态 (无需 sudo)
systemctl show -p MainPID,ActiveState,SubState --value javert-web
journalctl -u javert-web -n 50 --no-pager         # 看日志 (admin2 在 adm 组则可读)
```

**无 sudo 重启法** (unit 设了 `Restart=on-failure` + `User=admin2`): 直接 `kill -9` 自己的进程, systemd 当作 failure 自动拉起 (~7s). SIGKILL 不在 systemd "干净退出" 信号集 {HUP,INT,TERM,PIPE} 内, 故必触发 on-failure restart:

```bash
OLD=$(systemctl show -p MainPID --value javert-web)
kill -9 "$OLD"                                    # admin2 杀自己进程, 无需 sudo
sleep 8 && systemctl is-active javert-web         # 应回到 active (新 PID)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8090/login   # 200
```

**静态前端 (static/*.css, *.js) 改动无需任何重启**: FastAPI StaticFiles 每请求读盘, scp 后即时生效; 且模板给资源加了 `?v={mtime}` 自动 cache-busting (每次部署版本号自动变, 用户普通刷新即拉新, 无需 hard-refresh). **仅 `src/*.py` / 模板 / yaml 改动才需 kill-9 重拉进程.**

---

## 4. 142 schema (一次性, 已完成)

```bash
# 已在 2026-05-21 跑过, idempotent, 不需要重跑
set -a && source .env && set +a
uv run javert ensure-mssql-schema
```

会创建/确认 4 张表 + 2 个视图:
- `Javert_audit_runs` (3345 + 后续累积 → 5016 行)
- `javert_users`
- `javert_vio_review` (含 denormalized `patient_id` / `rule_id` / `username` 列)
- `javert_audit_logs`
- `v_javert_reviews` (review + patient_id + rule_id + username + agree/disagree flag JOIN 视图)
- `v_javert_audit_logs` (audit_log + username JOIN 视图)

修改 schema 改 `scripts/sql/create_javert_tables.sql` + 重跑 `ensure-mssql-schema` (用 `ALTER TABLE IF NOT EXISTS` 风格写). 老库自动 migration 新列.

**v0.9 迁移**: `ensure-mssql-schema` 现幂等加 `Javert_audit_runs.anchors_json` 列 (命中项目/原文锚点确定性缓存). 渲染默认现算 (老数据立即生效, 无需回填); 想走缓存加速则跑一次回填 (零 LLM, 幂等, 不改 verdict/批注):

```bash
uv run python scripts/backfill_anchors.py --target mssql --dry-run   # 看量
uv run python scripts/backfill_anchors.py --target mssql             # 全量回填 anchors_json
```

隔离表族（如 `desus_`）的既有 run 不得依赖工作台当前默认 Hub 回查。应使用审计时的同一
收费快照，按患者与批次精确回填 v2 已验证缓存；`--verified-fee-snapshot` 缺任一范围参数会
直接拒绝执行：

```bash
uv run python scripts/backfill_anchors.py --target mssql --dry-run \
  --patient-id <去标识患者号> --batch-tag desus --verified-fee-snapshot
uv run python scripts/backfill_anchors.py --target mssql \
  --patient-id <去标识患者号> --batch-tag desus --verified-fee-snapshot
```

新版审计会直接用当次 loader 的收费切片生成该缓存；旧 list 格式缓存仍由工作台关联当前
净正收费重验，不会因历史缓存而绕过收费真实性约束。

**肿瘤资格 v2 迁移**: 同一命令会幂等增加
`javert_audit_runs.eligibility_json NVARCHAR(MAX) NULL`；SQLite 启动时自动增加
`audit_runs.eligibility_json TEXT NULL`。旧行保持 `NULL`，无需回填。运行模式、RD04/R007
所有权和回滚见 [`docs/oncology/operations.md`](oncology/operations.md)。

---

## 5. 一次性 sqlite → 142 import (已完成)

```bash
# 已在 2026-05-21 跑过 3345 行, ~3.5 min, 0 failed
set -a && source .env && set +a
uv run javert sync-to-mssql --dry-run                # 看 plan
uv run javert sync-to-mssql --batch-size 200         # 实跑
```

未来 audit-patient 双写 142 失败时 (例如 142 短暂挂掉), 用 `--pending-only` 补:

```bash
uv run javert sync-to-mssql --pending-only --batch-size 200
```

---

## 6. 浏览器访问

- `http://192.168.31.62:8090/login` — 登录
- `http://192.168.31.62:8090/register` — 403 (注册关闭, 见 §7)
- `http://192.168.31.62:8090/workbench` — 工作台 (登录后)
- `http://192.168.31.62:8090/dashboard` — 仪表盘
- `http://192.168.31.62:8090/export?format=xlsx&scope=v_and_i` — 导出

---

## 7. 账号管理 (注册关闭, 走 CLI)

`JAVERT_ALLOW_REGISTER=false` 是 production 默认. 自助注册 403, 由运维分配:

```bash
# SSH 到 62
ssh admin2@192.168.31.62
cd ~/javert
set -a && source .env && set +a

# 建账号 (交互式输 2 遍密码)
uv run javert mssql-user create dr_zhang --display-name "张医生 (放疗科)"

# 列账号
uv run javert mssql-user list

# 改密 (运维兜底; 忘密码时)
uv run javert mssql-user reset-password dr_zhang

# 删账号 (latest review 自动降级 is_latest=0, audit_log 保留)
uv run javert mssql-user delete dr_zhang --confirm
```

专家自己改密走工作台顶栏 "改密" 链接 → `/account/password` 表单 (老 + 新 + 确认), 改完强制重新登录.

---

## 8. 备份

```bash
# 每日 cron 备份 sqlite (本地副本) + 142 由公司 DBA 主备
0 3 * * * sqlite3 /home/admin2/javert/data/audit.sqlite \
  ".backup /home/admin2/backup/audit-$(date +\%F).sqlite"
```

142 整库备份走公司常规 DBA 流程, 不需要 Javert 操心.

---

## 9. 故障排查

| 症状 | 原因 | 处理 |
|------|------|------|
| `/login` 503 | 142 不可达 | `curl http://192.168.31.62:8090/api/health` 看 `sql_server_142` 字段; ping 142; kill-9 重拉 (见 §3) 后再看 |
| 改完 src/ 没生效 | systemd 仍跑旧代码 | kill-9 重拉进程 (见 §3, 无 sudo); **仅 static/ 改动无需重启** |
| 改完前端没生效 | 浏览器缓存旧 css/js | v0.9 已自动 cache-busting (?v=mtime); 仍旧则 Cmd+Shift+R 强刷一次 |
| sidebar 病人 0 | watcher 跑挂 / shi_zd 列名错 | `journalctl -u javert-web --since "10 min ago" | grep -i error`; 看是 fetch_runs_since_id 还是 build_overview |
| 注册时 409 | 用户名重复 | `javert mssql-user list` 看, 或换名 |
| audit 跑后工作台看不到 | 双写 142 失败 (网络抖) | `uv run javert sync-to-mssql --pending-only` 补; watcher 自动 poll 推 SSE |
| SSE 一直跳同一条 toast | (已修复 2026-05-21) DATETIME2 精度死循环 | 升级到 fetch_runs_since_id 版本; 重启 |
| 浏览器看 J??? "主诊未填" | shi_zd 没收录该 pid 或文书没 dx 段 | 看 `journalctl` 是否报 shi_zd column 错; 检查 `data/shi_zd.xls` 列名 |
| audit_watcher 启动失败 | 142 启动时不通 | log WARN 不阻塞 web 启动, 等 142 恢复后 kill-9 重拉重新初始化 |

---

## 10. 升级 / 改代码流程

62 自 2026-08-04 起使用稀疏 Git 工作树，只检出受控运行时范围。禁止再用“只打包当前工作树
的 `src`”作为标准发布流程。标准流程必须从已提交 `HEAD` 生成最小 Git 对象包，62 的
`production-62` HEAD 必须与本地 HEAD 相等，且受控工作树必须 clean。只比较 HEAD 而不检查
clean 会漏掉远端文件被手工覆盖但未 commit 的事故。

首次启用仓库内 Git hook：

```bash
git config core.hooksPath .githooks
```

此后每次本地 commit 完成都会自动执行只读核验。远端不可达或版本漂移只报警，不会回滚已经
完成的 commit。也可随时手动运行：

```bash
python3 scripts/deployment_sync.py check
```

Mac 上改完并提交代码后：

```bash
# 1. 从已提交 HEAD 构建最小 Git 部署物；运行时范围有未提交改动时默认拒绝
cd /Users/shane/26er/Javert
python3 scripts/deployment_sync.py artifact --output /tmp/javert-git-deploy.tgz

# 2. scp 部署物和安装器到 62
scp /tmp/javert-git-deploy.tgz scripts/deployment_sync.py admin2@192.168.31.62:/tmp/

# 3. 安装器在 62 上先备份源码、mode 0600 的 .env 和旧进程实际环境，再更新稀疏 Git 工作树
ssh admin2@192.168.31.62 'python3 /tmp/deployment_sync.py install \
    --artifact /tmp/javert-git-deploy.tgz --remote-root /home/admin2/javert'

# 4. 重拉进程 (无 sudo — admin2 杀自己进程, systemd Restart=on-failure 自动拉起)
ssh admin2@192.168.31.62 'OLD=$(systemctl show -p MainPID --value javert-web); \
    kill -9 "$OLD"; sleep 8; \
    echo "active=$(systemctl is-active javert-web) http=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8090/login)"'

# 5. HEAD + 62 clean 双重验收；必须输出 SYNCED
python3 scripts/deployment_sync.py check
```

部署物只携带当前 commit/tree 和 `src/`、`configs/`、`data/router/`、`scripts/`、
`pyproject.toml`、`uv.lock` 的 blob；不携带仓库历史、`.env`、`data/` 患者文件或 `output/`。
`artifact --allow-dirty` 仍然只取已提交 HEAD，仅供明确知道工作树未提交内容不属于本次发布时使用。

**纯前端改动虽然无需重启，但仍必须使用带清单的部署包并跑 step 5**；静态文件即时生效 +
cache-busting 自动刷新。仅 `*.py`/yaml 改动才需 step 4 重拉。

数据 (data/) 或 schema (scripts/sql/) 改了类似流程, 但 schema 改完还要在 62 跑 `ensure-mssql-schema`.

---

### 10.1 本次升级附加步骤 (2026-06-03 工作台 UI: 检验记录 tab / 命中名修复 / 列表排序 / 证据默认展开)

本批改动 = `src/javert/web/{hit_resolver.py, api/routes_workbench.py}` + 模板 (`patient_detail.html` / `_sidebar.html`) + `static/{app.js, style.css}`. 含 `.py` 改动, 故按 §10 step 1-4 推 src + **kill-9 重拉进程** (static/ 虽即时生效, 但本批改了后端逻辑, 仍需重拉). 重拉后另需两步:

**① 刷新命中锚点缓存 (必跑)** — 工作台优先读 142 `Javert_audit_runs.anchors_json` 缓存. 已缓存的老 run 仍会显示**旧命中名占位** (`费用明细检索` / `药品类明细检索` 等, 而非具体药品名) + **旧 lab/exam 锚点** (tab=exams). 重放确定性逻辑回填即修复 (零 LLM, 幂等, 不改 verdict / 批注):

```bash
# v0.11 起分批提交 (默认 batch=500), 锁短暂持有 → 可在工作台在线时跑, 每批打进度日志
ssh admin2@192.168.31.62 'cd ~/javert && set -a && source .env && set +a && \
  ~/.local/bin/uv run python scripts/backfill_anchors.py --target mssql'             # 全量回填 (~13881 行)
# 想更轻 (更短锁窗口) 可调小: --batch-size 200
```

> ⚠️ **务必用 v0.11+ 的脚本 (分批提交)**. 旧版是**单一大事务** (commit 在末尾), 会持有 `Javert_audit_runs` 表锁长达整批 (十几分钟), 在线跑会把工作台 `list_patients` 等读查询**挡死 → 整页变慢 + 病人列表空** (2026-06-04 实测踩坑). 分批版锁只在每批 (亚秒级) 内持有, 实测回填进行中 list_patients 仍 ~0.9s. **不要后台 detached 跑旧版**.
> 不回填也不报错: **未缓存 run 渲染时现算** (新提取逻辑即时生效), 仅**已缓存 run** 需回填刷新命中名.
> 前端 `app.js` 已把老缓存的 exam 锚点 (`tab=exams`) 兜底归并到「检验记录」tab, 故跳转不失效, 只是占位命中名要靠回填修.

**② 检验/检查数据文件 (检验记录 tab 取数依赖)** — 新「检验记录」tab 读这两个文件 (与 audit 期 `search_lab_results` / `search_examinations` 同源):

- `data/sy_检验.csv` (≈392 MB, 检验 / 化验)
- `data/sy_patient_examination.csv` (≈33 MB, 检查 / 影像)

62 上若缺文件, **tab 不报错**, 显示 `(无检验/检查记录)` (代码 try/except 优雅降级). 要展示真实数据则 scp 上去:

```bash
scp "data/sy_检验.csv" "data/sy_patient_examination.csv" admin2@192.168.31.62:~/javert/data/
```

> 首次有用户打开检验记录 tab / 点检验命中项时, `LabLoader` 流式建索引 ≈6 s (进程内缓存, 仅首次; 392 MB 文件需 ~1-2 GB 临时内存峰值). 之后命中即时返回.

**回归确认** (Mac 上已跑 464 passed / 1 skip): 改动覆盖 `tests/test_hit_resolver.py` (命中名提取 + lab/exam 归并 labs tab) + `tests/test_workbench_routes.py` (/raw 返回 labs/exams). 上线前在 62 `uv run pytest tests/test_hit_resolver.py tests/test_workbench_routes.py -q` 复核一遍.

### 10.2 本次升级附加步骤 (2026-07-06 add-workbench-sql-raw-source: 工作台原文 hub SQL 源)

本批改动 = `src/javert/{config.py, data/hub_source.py(新), web/hub_raw_source.py(新), web/api/routes_workbench.py}` + `scripts/etl_from_data_hub.py`(薄壳化). 按 §10 step 1-4 推 src + kill-9 重拉. 另需两步:

**① `.env` 显式配置只读 hub 原文源**:

```bash
# 编辑 /home/admin2/javert/.env，避免 echo 重复键
JAVERT_HUB_RAW_ENABLED=true
JAVERT_HUB_DATABASE=sh_yb_platform
# 同库隔离表族才设置，例如 desus_TB_* 使用 desus_；生产默认留空读取 TB_*。
JAVERT_HUB_TABLE_PREFIX=
# 混合工作台按患者 latest batch tag 选隔离源；JSON 单引号必须保留。
JAVERT_HUB_RAW_PROFILES='{"desus":{"database":"TP_data_hub","table_prefix":"desus_"}}'
# 重拉后生效；回滚 = RAW_ENABLED 改 false + 再重拉，一步回纯 CSV。
```

不加开关部署 = 行为与升级前完全一致 (开关默认 false，表名前缀默认空)。前缀值只允许
字母、数字和下划线且须以字母或下划线开头；非法值会在服务配置加载时失败，缺前缀表不会
静默回退无前缀表。

`JAVERT_HUB_RAW_PROFILES` 只在 CSV miss 且患者 latest tag 命中时生效，每个 profile 使用
独立 HubRawSource 连接与缓存；database/table prefix 均须为安全单段标识符。删除该变量即
回到单一默认 Hub，不需要改结果库。

**② TP_data_hub 索引（2026-07-06 历史开发库步骤）** — `scripts/sql/create_data_hub_indexes.sql` (9 个: 5 表 JZLSH + fee⋈EXT + LIS join + 2 RIS). 实测索引后单患者首查 2.44s → 0.31s。开发库重建后用 `sqlcmd <连接参数> -d TP_data_hub -v HUB_DATABASE=TP_data_hub -b -i scripts/sql/create_data_hub_indexes.sql`；脚本会在首条 DDL 前核对当前库。142 当前读取源 `sh_yb_platform` 由 DE 维护，Javert 侧不得直接执行 DDL，生产索引需求须交 DE/DBA 走变更。

**效果**: 数据在 `TP_data_hub` 的患者 (如 szx2.0 批次 4680 人) 原文/费用/检验/主诊断**即查即得**, 不再需要拷 CSV 到 62 data_import / 不再需要重启; 62 的大 overlay 文件 (case_notes/shi_fee/lab_results 追加的 szx2 数据, ~1GB) 验证 hub 路径正常后可删除回收内存 (`*.bak.preszx2full` 为追加前备份, 恢复 = cp 回去).

**验证**: 点一个只在 hub 的患者 (如 211318013) 原文 tab 应正常展示 (首次点开 <1s); 点 J66252 应与升级前一致; 断网 142 时老患者原文不受影响.

### 10.3 本次升级附加步骤 (2026-07-07 harden-onsite-redlines: 进院红线 7 项)

本批改动 = `src/javert/{config.py, cli.py, web/api/{main,routes_auth,routes_workbench,routes_audit}.py, tools/llm_provider.py, commands/audit_patient.py, data/{csv_loader,hub_source}.py}` + `scripts/sql/create_data_hub_indexes.sql` (追加 BA 四表索引) + `scripts/diff_fee_match.py` (新). 按 §10 step 1-4 推 src + kill-9 重拉. 注意事项:

**① session secret 变硬性前提** — 重拉前确认 `.env` 里 `JAVERT_SESSION_SECRET` 存在且非默认值 (62 一直有, 只是从此**缺了起不来**, 报错信息会直说). 见 §2 注释.

**② raw 端点限流 + 留痕** — 默认 30/minute/会话, 超限 429; 每次点开原文在 `javert_audit_logs` 落一行 (`action=raw_access`, target=患者号, payload.source ∈ csv|hub|rate_limited). 专家反馈被误伤时 `.env` 加 `JAVERT_RAW_RATE_LIMIT=60/minute` 重拉.

**③ 142 BA 四表索引（历史开发库步骤）** — `create_data_hub_indexes.sql` 追加了 SYJBK/SYZDK(+ZDDM)/SYSSK 索引；只可按上面的 `-d/-v/-b` 方式在自有 `TP_data_hub` 重跑（已有索引由 `IF NOT EXISTS` 跳过）。若当前服务读取 DE 的 `sh_yb_platform`，只提交索引需求，不在 Javert 侧执行该脚本。完成后量一次单患者 hub 首查耗时，对照历史基线 0.31s。

**④ fees 匹配收紧核对** — 62 上跑一次 `uv run python scripts/diff_fee_match.py` (可加 `--overlay data_import`), 预期输出「新旧命中集合完全一致」或仅列出长号误归属短号的修正行 (Mac 本地快照实测: 完全一致).

**⑤ ⚠ 2C BFF 契约通知 (SSE 只加不改)** — `/api/audit/run-batch` 新增语义, bff 侧需知悉 (升级由 bff 侧 change 承接):
- `fail` 事件新增 `stage` 字段 ∈ `audit` (裁决失败) / `persist` (落库失败, **此时不再发 result** — 此前 persist 失败仍发 result, BFF 会拿到库中不存在的 run) / `unknown_rule` (请求了不存在的 rule_id, 此前静默丢弃)
- `result` 事件字段逐字不变; `start`/`trace` 不变; `start.total` 仍只计已知规则
- `done.completed` 语义收紧为**落库成功数** (persist 成功才 +1, 此前是审计成功数) — audit/persist 失败的规则不计入 completed 也不发 result; bff 若用 `completed==total` 判「全成功」语义等价, 若用它做进度条需知失败条不递增
- bff 未升级前行为等价「该条无结果」, 无回退风险

**冒烟**: 登录 → 点开任一患者原文 (查 `SELECT TOP 5 * FROM javert_audit_logs WHERE action='raw_access' ORDER BY id DESC` 见留痕) → curl 触发一条 run-batch (故意带一个不存在的 R999 看 `fail stage=unknown_rule` 回执) → 打开一个 szx 缺首页行患者详情页看诊断/手术非空.

### 10.4 本次升级附加步骤 (2026-07-07 fix-scan-residuals: 复扫残余 6 项)

本批改动 = `src/javert/{data/hub_source.py, web/patient_overview.py, audit/runner.py, audit/prompts/base.txt}` + 对应测试. 纯堵洞/口径, 零裁决漂移 (不改 LLM 对话行为). 按 §10 step 1-4 推 src + 重拉. 注意事项:

**① fees 匹配差异核验留档 (csv_loader 两级精确匹配安全网)** — Mac 本地快照 (3309 键/695681 行) 实测 `diff_fee_match.py` **新旧命中集合完全一致**, 无「无分隔符 bah 患者丢费用」回归. 62 上再跑一次确认 (§10.3 ④ 同脚本): `uv run python scripts/diff_fee_match.py --overlay data_import`, 预期「完全一致」或仅列长号误归属短号的修正行.

**② hub 空 ZYZD 兜底** — `hub_source.fetch_zd` 现在剔除 SYJBK 中 ZYZD 空串行: 有首页行但主诊为空的 szx 患者回退 IH 诊断 (此前被剔 IH + 生成一条空主诊, 比 per-patient 回退前更糟). 冒烟: 若批次内有此类患者, 详情页主诊非空且无空 code/名的伪主诊行. (实测当前 TP_data_hub szx 空 ZYZD 患者数可能为 0, 此闸护未来批次.)

**③ gate 降级 confidence 口径** — verdict_gate 降级 (V→I/C) 落库 confidence 归一到 0.5, 原值写进 reasoning 的 `[gate: ... | 原 conf=x.xx]`. 工作台不再出现「INCONCLUSIVE conf=0.90」自相矛盾读数.

**④ 必留头部上限 + marker 语义** — `_truncate` 必留头部超 `tool_result_max_chars` 时对头部也硬截 (防单条工具结果整体超预算 → context 溢出 400); `base.txt` 加一行解释 `====[必留头部结束]====` 标记语义. base.txt 是 src 一部分, 随 src 推送即生效.

**⑤ build_overview 深拷贝护栏** — 概览缓存命中现返回深拷贝, 调用方就地改字段不再污染缓存. 无外部可见变化, 纯回归面收敛.

---

### 10.5 本次升级附加步骤 (2026-07-09 recover-deterministic-recall: 确定性召回回收)

本批改动 = `src/javert/{config.py, data/fee_netting.py, audit/verdict_gate.py, audit/runner.py, store/result_persister.py, tools/search_fees.py}` + `configs/{verdict_gate.yaml, rules/R063.yaml}` + `data/router/javert_rules_index.json` + `static/{app.js, style.css}` + `templates/patient_detail.html` + 新脚本 `scripts/{rescreen_gated.py, drift_report.py}` + 对应测试. 含 `.py` + configs + static 改动, 按 §10 step 1-4 推 (src + configs + scripts + static) + **kill-9 重拉进程**. 三个开关全默认 on, 各自独立回滚 (`JAVERT_VERDICT_GATE=off` / `JAVERT_DRIFT_GUARD=off` / gate yaml 删 `panel_rules` 节). 升级后附加步骤:

**① 存量单次放过重筛 (dry-run 先行 → 定阈值 → 落库)** — 套餐口径 (R155 同日不同项目名数 ≥ `min_distinct_items`) 把误杀的「单次放过」CLEAN 行回收为 INCONCLUSIVE (进专家队列). **先 sqlite 后 142, dry-run 先出分布**:
```bash
# 1. dry-run 出翻转量 + 同日项目数分布 (>100 条 → 收紧 min_distinct_items 再落)
uv run python scripts/rescreen_gated.py --target sqlite --dry-run
# 2. 与用户确认阈值后落 sqlite (source-of-truth)
uv run python scripts/rescreen_gated.py --target sqlite
# 3. 落 142 (工作台读的库; 自动跳过已有专家 review 的行)
set -a && source .env && set +a
uv run python scripts/rescreen_gated.py --target mssql
# 验证: 211419211×R155 → INCONCLUSIVE + gate_tag「单次闸重筛回升(原C)」; 工作台默认视图可见
```
**还原 (一键可逆)**: `uv run python scripts/rescreen_gated.py --target mssql --revert` (凭可逆标签把翻转行恢复 CLEAN/单次放过). sqlite 同理 `--target sqlite --revert`.

**② 存量漂移只读清单 (不自动改, 交专家)** — 列出全库 (rule,patient) 历史曾判 V 而当前 C 的漂移 (含 2026-07-08 晨 211440399 R063/R155):
```bash
uv run python scripts/drift_report.py --target mssql --out output/drift_142.csv
```
只读, 不写库. 结果交专家人裁 (存量翻转可能是新代码修对了, 机器分不清).

**③ 工作台「只看被闸降级」facet** — `patient_detail.html` + `app.js` + `style.css` 改动随 src/static 推送即生效 (kill-9 重拉后). 验证: 患者详情页勾「只看被闸降级」→ 列表只剩 gate_tag 非空卡片 (含降级标签 + LLM 原始推理), 与 verdict filter 正交叠加.

**④ FN 回归 62 复跑** — `uv run python scripts/fn_regression.py --against-baseline`: 重筛落库后 **FN-003 (211419211×R155) 应升档翻 I**; **FN-005 (R225) 保持 full** (gate 改动仅作用 R155, R225 路径逐字不变).

**⑤ search_fees 输出形状变化 (聚合按项目名净额)** — 关键词/类别/目录检索现按项目名聚合净额 (退费自动相抵 + 「含 N 次退费已抵消」注记 + 数量小数保真). 既有规则 prompt 依赖行式形态 → 只改聚合行内容不改结构. 抽查: 任意 M1/M4 精选规则 dry-run 看 search_fees 结果仍逐行可读.

**§10.5 实测落地记录 (2026-07-09)**: 部署 (git archive HEAD 干净包) + kill-9 重拉 ✓ http 200. ① 重筛已落: sqlite 4 行 + 142 6 行 (全部同日 11-12 项, 0 review 冲突, 阈值 3 保持). ⚠ 首轮暴露漏筛洞: R155 候选 75 行中 **69 行为 szx2.0 hub-only 患者** (CSV 取不到费用被当 0 项静默跳过, 含 211419211) — ec669df 已加 hub 批量兜底 + 双 miss WARN; 兜底依赖 `TB_HIS_ZY_FEE_DETAIL_EXT` (v2.2 恢复中), 就绪后**重跑 `rescreen_gated.py --target mssql` 回收 69 户** (211419211 单日 11 项必翻). ② `output/drift_142.csv` 506 对已出交专家. ④ FN 回归 62 实测: FN-001/002/005 full; FN-003 重跑 miss = **非闸问题** — R155 症状扫描豁免口把病程「肢体乏力」(脑梗神经科症状) 当炎症指征, LLM 自判 C; prompt 收紧列 follow-up. R317/R318 已正式重跑上工作台 (batch_tag=fn-fix-0709, 各 1 V).

### 10.6 本次升级附加步骤 (2026-07-18 RD04 卡片改造 + 生效期闸)

本批改动 = `src/javert/{config.py, audit/runner.py, oncology/{contracts,eligibility,pathology,runtime}.py, tools/{drug_audit_lookup,registry}.py, web/{templating.py, templates/patient_detail.html, static/style.css}}` + `tests/`。纯 `src` 覆盖, 按 §10 step 1-4 推 + kill-9 重拉。**无 schema 变更**（新字段在 `eligibility_json` blob 内, 无新列）。附加两点:

**① `.env` 加生效期开关**（见 §2）: `JAVERT_ONCOLOGY_ENFORCE_EFFECTIVE_DATE=false`, 重拉后 `/proc/<pid>/environ` 验实值。不加 = 行为按代码默认 `true`（按生效期过滤, 与升级前一致）。回滚改 `true` 重拉。

**② 受影响患者重跑**: RD04 卡片改造（自然语言推理 + 命中药明细定位 + 「肿瘤靶向药用药方案合理性」follow-up + 免疫组化定位原文双链）只对**新 run** 生效, 旧 `med_rst*` 卡进历史。生效期闸关闭后, 窗口外就诊也会求值并带"核查生效时间"提示——如 K57728 维迪西妥单抗（HER2 IHC 1+ < 2+/3+）已翻 VIOLATION（batch `onco-uro-fix`）, 定性前须人工核查该限定 2025 就诊时是否已生效（见 `docs/oncology/operations.md` KB 生效期待核对）。重跑: `export JAVERT_BATCH_TAG=<≤20字符>; uv run javert audit-patient <号> --rules RD04`。

### 10.7 肿瘤知识专家维护与 published release（142 authoring DDL 已落地，生产未发布）

`add-oncology-kb-authoring` 已提供工作簿、校验、staging/物化边界、幂等 DDL 和 published
release 离线加载能力。**本节不是发布记录**：截至 2026-07-22，142 `知识库_work` 已实际
建立 schema、23 个必需触发器和中文审核视图；两次历史失败物化均已回滚，最终修正版已
MATERIALIZED 为待审 DRAFT。专家审批、release candidate/publish 和 62 新 release 目录均未启用。

子命令库存以当前程序动态输出为准：

```bash
uv run javert oncology-kb --help
```

工作流分为三道不可合并的门禁：

1. **纯本地**：`export` 确定性生成两份工作簿；`validate` 只读 xlsx，
   不读 SQL 配置、不建连接。每次专家回传都先离线校验：

   ```bash
   uv run javert oncology-kb validate \
     /secure/path/肿瘤药指南适应证与医保限定条件树KB.xlsx --kind eligibility
   uv run javert oncology-kb validate \
     /secure/path/肿瘤治疗方案组成KB.xlsx --kind regimen
   ```

2. **知识库写入**：只能命中精确库名 `知识库_work`，且该库必须在该环境的
   `JAVERT_OWNED_DBS` 中被单独批准。所有路径先核对白名单和 `DB_NAME()`；
   DDL 还要求 ALTER 权限，内部用
   `sqlcmd -d 知识库_work -v KB_DATABASE=知识库_work -b` 双重指定。不得把
   `zadig`、`TP_data_hub` 或 `sh_yb_platform` 当成知识库目标。

   升级既有 schema 时，`kb.review_event.reviewed_content_checksum` 不允许推测回填：若历史
   审核行缺值，DDL 会在收紧 `NOT NULL` 前 `THROW 51003`。应由知识管理员根据当时被审核的
   typed 内容和原始审批记录人工补齐、复核并留痕，再重新执行 `schema-apply`；不得用当前内容
   checksum 覆盖历史事实。

   DBA 创库、备份策略、最小权限 principal 和 owned 授权四项均留痕后，才按顺序执行：

   ```bash
   uv run javert oncology-kb schema-apply --database 知识库_work
   uv run javert oncology-kb preflight /secure/path/专家回传工作簿.xlsx \
     --kind eligibility --database 知识库_work
   uv run javert oncology-kb upload /secure/path/专家回传工作簿.xlsx \
     --kind eligibility --database 知识库_work --dry-run
   ```

   dry-run 只保存目标、checksum、计数和去敏预检结果。人工确认后才能去掉
   `--dry-run` 并显式提供 `--uploaded-by`；已服务端校验为完整的 batch 才能进入
   `materialize --batch-id ... --kind ... --database 知识库_work`。物化为单事务，任一实体
   引用、日期、审核状态或对账失败都整批回滚。上传成功不等于批准或发布。

   两类工作簿均物化后，append-only 最新审核事件必须完整且无未落库编辑，再逐个批准父
   revision；`approve` 不创建或覆盖专家意见：

   ```bash
   uv run javert oncology-kb approve \
     --entity-type eligibility \
     --revision-id <eligibility-rule-revision-id> \
     --reviewer-id <domain-reviewer-id> \
     --database 知识库_work
   uv run javert oncology-kb approve \
     --entity-type regimen \
     --revision-id <regimen-revision-id> \
     --reviewer-id <domain-reviewer-id> \
     --database 知识库_work
   ```

   `APPROVE_WITH_EDIT` 会生成完整 superseding DRAFT 子图，必须先 materialize 并针对新 checksum
   追加已解决该编辑的最新审核事件；精选知识须按“编辑落 MAPPED → 新事件核验为 VERIFIED”
   两步执行，药物类别 authority 也必须独立审核；
   每条事件的 `reviewed_content_checksum` 还必须精确等于当前被审核 typed 内容；所有子项
   checksum 过期、审核人不一致、条件树/来源/日期/药品概念不完整时，批准会整事务失败。

3. **发布与 62 加载**：只有 approved 不可变 revision、来源全集分区、日期不重叠、
   精选知识保全和职责分离门禁全部通过，才能编译为包含四份 JSON、
   `release_manifest.json` 和 `coverage_manifest.json` 的不可变 bundle。先由发布管理员运行
   只读 authority 检查：

   ```bash
   uv run javert oncology-kb release-authority \
     --database 知识库_work \
     --pathology-bootstrap /secure/path/pathology_biomarker_kb.json \
     --pathology-bootstrap-checksum <sha256:pathology>
   ```

   将输出的 source、curated、pathology 三个 checksum pin 记录到 authoring 库之外的审批单。
   任一权威集合或病理文件变化都必须重新检查和审批。随后由不属于任何领域审核人的 release
   operator 构建数据库 candidate：

   ```bash
   uv run javert oncology-kb release-build \
     --database 知识库_work \
     --operator <release-operator-id> \
     --created-at <ISO-8601> \
     --pathology-bootstrap /secure/path/pathology_biomarker_kb.json \
     --pathology-bootstrap-checksum <sha256:pathology> \
     --source-authority-checksum <sha256:source> \
     --curated-authority-checksum <sha256:curated> \
     --releases-dir /secure/releases/oncology
   ```

   `release-build` 只登记 `CANDIDATE` 和 release items，不写本地 candidate bundle、不切指针。
   独立发布授权核对返回的 release ID、三项 pin 和职责分离后，由 build 的同一 operator 发布：

   ```bash
   uv run javert oncology-kb release-publish \
     --database 知识库_work \
     --release-id <release-id> \
     --operator <same-release-operator-id> \
     --published-at <ISO-8601> \
     --pathology-bootstrap /secure/path/pathology_biomarker_kb.json \
     --pathology-bootstrap-checksum <sha256:pathology> \
     --source-authority-checksum <sha256:source> \
     --curated-authority-checksum <sha256:curated> \
     --releases-dir /secure/releases/oncology
   ```

   publish 会在事务锁内从 authoring 全集重建 candidate；只有校验通过时才写不可变
   `PUBLISHED` bundle、数据库状态/pointer 和本地 `active_release.json`。本地激活或数据库
   commit 失败会恢复调用前的数据库与 active pointer；不可变 bundle 保留供使用原参数重试
   时先校验后复用。不得以上传成功或数据库 candidate 替代发布授权。

未来获授权部署时，将完整 published bundle 放到 62 的 mode 0700 本地目录，
用经校验的 `active_release.json` 原子指向目标 release，然后才在 `.env` 设置：

```bash
JAVERT_ONCOLOGY_RELEASE_DIR=/home/admin2/javert/releases/oncology
```

重拉后必须在 `/proc/<pid>/environ` 核对实值，并验证 active pointer、manifest、四资产
schema/checksum/review status/release ID 一致后再跑去标识回归。加载器只接受
`PUBLISHED`；任一校验失败都中止，不回退到 candidate、staging 或专家维护库。
回滚只切换到另一个已校验 published bundle，保留所有 release/revision/审计历史；
执行参数为：

```bash
uv run javert oncology-kb release-rollback \
  --database 知识库_work \
  --target-release-id <historical-published-release-id> \
  --operator <authorized-release-operator-id> \
  --reason <approved-reason> \
  --occurred-at <ISO-8601> \
  --releases-dir /secure/releases/oncology
```

本地回滚切换失败时会补偿恢复数据库 release 状态和 pointer，并保留已有 bundle 与事件；
同一目标已经 active 时返回 `reused=true`。职责固定为 DBA 管库与权限、领域专家管内容意见、
知识管理员管导入/物化/批准投影、release operator 管 build/publish/rollback、独立授权人决定
是否发布和部署。

截至 2026-07-22，真实 142 DDL/视图已执行，23 个必需触发器均已启用；两次历史 generated
DRAFT 失败 batch 留存且保持回滚，最终修正版已完成 validate/preflight/服务端校验并物化为
待审 DRAFT。专家批准、production publish、
62 新 bundle 启用、上一 release/数据库备份恢复演练及 paired shadow 均未执行；不得在 62
设置 `JAVERT_ONCOLOGY_RELEASE_DIR`。当前事实见
`docs/oncology/authoring/142_draft_seed_import_report.md`。

---

### 10.8 2C v2 联调热修复（2026-07-27）

- 仅覆盖 `src/javert/web/api/routes_audit.py`，未部署工作树中的其他规则、配置或肿瘤
  authoring 修改；远端文件 SHA-256 为
  `4a856e5062086959ddf3a76eeb98ee89a973d47d606140be89ee6cef791c4a59`。
- 覆盖前已把完整 `src`、目标源码、mode 0600 `.env` 和旧进程实际环境无回显固化到
  `/home/admin2/backup/javert-2c-v2-20260727-dYx4sX`；回滚时恢复其中
  `routes_audit.py` 后按 §3 重拉进程。
- `ensure-mssql-schema` 幂等执行成功，4 张表就绪；重拉后 PID `480014`，systemd
  `active/running`、登录页 HTTP 200、近期错误标记 0，新旧 `JAVERT_*` 环境逐项一致，
  SQL Server 142 与 Hub 连接均正常。
- J70782 v2 脱敏结构验收：`status=done`、`outcome=succeeded`、
  `total=completed=cards=39`、`failed=0`；时间格式错误、name-only 假命中、三数组对齐错误、
  applicability 缺失均为 0，16 张确定性不适用卡已标记 `NOT_APPLICABLE`。
- 2026-07-27 C 端数量诊断：增加不含患者标识、run/attempt id 和业务原文的
  `2c_v2_outbound` INFO 日志。一次现网请求记录
  `total=39 completed=39 v1_results=39 cards=39 matched_items=14`；进一步统计为
  13 张卡含 14 个 matched item、26 张卡无 matched item。日志版本回滚点为
  `/home/admin2/backup/javert-2c-log-20260727-upwle8`。

### 10.9 2C v3 收费明细行契约（2026-07-28）

- 新增 `/api/audit/v3/submit` 与 `/api/audit/v3/results/{SYXH}`；v3 复用既有任务、
  `attempt_id`、状态和完整 cards，在 `matched_items[]` 追加数量、单价、开单科室编码/名称、
  开单医生工号/名称。同项目同时间存在多条收费源行时逐行返回；v1/v2 不改。
- 本地门禁：2C 直接测试 **25 passed**，Web/2C 组合 **58 passed**，完整套件
  **1052 passed / 1 skipped**，`openspec validate add-2c-v3-charge-line-fields --strict`
  通过。
- 部署前备份：`/home/admin2/backup/javert-2c-v3-20260727-Afftan`，目录0700；包含完整
  `src`、mode 0600 `.env`、旧进程实际 SQL/Hub 环境和覆盖前源码 SHA。仅覆盖
  `routes_audit.py` 与 `middleware.py`，并保留62原有 `/scriv`、`/api/scriv` 鉴权边界。
- 部署文件 SHA-256：`routes_audit.py = 3291d4a5c95b353d47e6cc2cde78d488320fe7ed111a1bf4d8646492d6bd38c9`；
  `middleware.py = 990af4283f721373b87ee612413f2f3ec67079415b55d202694085f348dfa493`。
- `ensure-mssql-schema` 幂等执行成功，4张表就绪。重拉后 MainPID `721707`，systemd
  `active/running`、登录页 HTTP 200、关键 `JAVERT_*` 环境与旧进程完全一致，SQL
  `zadig` 与 Hub 均健康，部署后错误标记0。
- 生产脱敏结构验收：v1 `results=39`；v2 `cards=39/matched_items=14`；v3
  `cards=39/charge_lines=15`，说明1个同项目同时间命中正确拆为两条收费行。v3
  `total=completed=39`、`failed=0`，39个唯一规则；新增字段缺失、数字/文本类型错误、
  `hit_*` 对齐错误均为0，v2 新字段泄漏为0，三个版本规则集合一致。匿名日志记录
  `2c_v3_outbound ... cards=39 charge_lines=15`。
- 回滚：从上述备份恢复 `src/javert/web/api/routes_audit.py` 和
  `src/javert/web/middleware.py`，再按 §3 kill-9 MainPID 触发 systemd 重拉；无需数据库回滚。

### 10.10 2C v2/v3 结果轮询超时热修（2026-07-28）

- 根因：大结果患者每次 GET 都从头读取 run、解析 evidence/tool calls 并扫描费用行；现网
  64张卡的 v2 单次约35–47秒，超过2C的30秒读取超时。审计本体已完成，慢点在结果投影，
  不是 LLM 或网络连接。
- 修复：按 `api_version + projection_scope + run_id` 深拷贝缓存 cards；正常任务 scope 为
  `attempt_id`，重启历史回放使用 run ID 快照摘要。同 scope 构建单飞、LRU 上限2048张，
  v2 跳过不会返回的 v1 hits 重算，v3 独立缓存收费行展开。匿名日志只追加
  `cache_hits/cache_misses/build_ms`。
- 本地门禁：2C直接测试 **31 passed**，Web/2C组合 **70 passed**，完整套件
  **1058 passed / 1 skipped**；`openspec validate fix-2c-results-poll-timeout --strict` 通过。
- 部署前备份：`/home/admin2/backup/javert-2c-poll-20260728-A5DafB`（目录0700，含完整
  `src`、mode 0600 `.env`、旧进程实际环境和源码 SHA）；仅覆盖
  `src/javert/web/api/routes_audit.py`。部署 SHA-256 为
  `4a83ed3b97f313c4a714302377dc730f139cf07334cbedaadbccee92740effd3`。
- 重拉后 MainPID `745255`，systemd `active/running`、登录页 HTTP 200、全部 `JAVERT_*`
  与旧进程一致，SQL `zadig` 和同步线程健康，部署后错误标记0。
- 生产脱敏计时：v2 历史首次重建 `28.445s`、重复缓存命中 `0.099s`；v3 首次收费行展开
  `25.538s`、重复命中 `0.112s`。同版本首次/重复响应字节数完全一致；日志分别验证
  `cache_misses=64→cache_hits=64`，终态为64张卡，v2 398个 matched item、v3 427条收费行。
- 回滚：从上述备份恢复 `src/javert/web/api/routes_audit.py`，按 §3 kill-9 MainPID 触发
  systemd 重拉；无数据库变更，无需数据库回滚。

### 10.11 2C v3 恢复与 FP8 端点复位（2026-08-03）

- 用户确认 2C 生产对接固定使用最新 v3；v1/v2 只保留兼容。检查发现 62 后续部署曾把
  `routes_audit.py` 和 `middleware.py` 覆盖为仅 v1 的旧版，导致 v2/v3 匿名探针返回 401；
  同时 Javert 仍指向 FP8 `30000`，但端口未监听，不能受理真实审计。
- 恢复前备份：`/home/admin2/backup/javert-2c-v3-restore-20260803-Y2Nf2R`（目录0700，
  `.env`、重启前后进程环境、目标源码和完整 `src` 归档均为0600）。恢复后哈希：
  `routes_audit.py = 4a83ed3b97f313c4a714302377dc730f139cf07334cbedaadbccee92740effd3`；
  `middleware.py = 990af4283f721373b87ee612413f2f3ec67079415b55d202694085f348dfa493`。
- W2 试验服务在无连接后停止，`30002` 关闭；DeepSeek-OCR 在无连接后停止，`30001` 关闭；
  原 Qwen3.6 FP8 由 `/home/admin2/launch_qwen3.6.sh` 恢复到 `30000`。按 Javert 实际参数
  `enable_thinking=false` 的无患者探针返回 `OK`、`reasoning_tokens=0`。
- Javert Web 重拉后 MainPID `2731951`，systemd `active/running`、登录页 HTTP 200；重启前后
  `JAVERT_*`/SQL/Hub 环境逐值无差异，`JAVERT_LLM_ENDPOINT` 仍为
  `http://192.168.31.62:30000/v1`，SQL `zadig` 与同步线程健康，近5分钟错误标记0。
- v3 外部空数组 submit 为 HTTP 202；不存在的去标识号 results 为 HTTP 200、
  `api_version=3.0/status=unknown`；合成 Hub 查无探针为 HTTP 202 且明确 rejected，证明 Hub
  只读连接正常。验收未提交真实患者、未创建审计任务、未写入患者结果。

### 10.12 Promise 门禁、公开解释与原文懒加载（2026-08-05 已部署）

> 本节既是 `add-evolving-promise-harness` 的发布清单，也记录 2026-08-05 的生产验收。
> 后续发布仍须逐项重跑，不能沿用本次结果冒充新的生产完成。

发布前在已提交的本地 HEAD 运行：

```bash
.venv/bin/javert promise validate
.venv/bin/javert promise run
.venv/bin/pytest -q
openspec validate add-evolving-promise-harness --strict
```

Promise harness 必须离线完成，禁止访问 LLM、网络、SQL Server 或 hub；报告和日志只能记录
case/Promise 标识、状态、错误码、计数与耗时分桶，不能出现患者号、原始病历、SQL 参数、
连接信息或完整 Promise facts。

获授权后严格按 §10 的 `production-62` 已提交 HEAD 执行 artifact/install。覆盖代码、配置、
前端和 SQL schema 后，先运行 `.venv/bin/javert ensure-mssql-schema` 幂等增加可空
`promise_trace_json`，再重启服务。不得连接或修改只读 `sh_yb_platform` 的 schema；新增列
只属于 Javert 结果库，旧行保持 `NULL`，不回填、不静默重评。

重启后的生产验收必须同时满足：

1. 本地与 62 `HEAD` 相等、62 受控工作树 clean，systemd active，登录页 HTTP 200；
2. `/proc/<pid>/environ` 中 SQL、Hub、LLM 和肿瘤开关实值与发布前固化值一致；
3. SQL/Hub 健康、SQLite/SQL Server 新旧行兼容，Promise trace 可空双写；
4. v3 空数组 submit 返回 HTTP 202，不存在的去标识号 results 返回 HTTP 200/unknown；
   BFF 验证 `public_explanation.narrative`、可空 `promise` 和 `behavior_code` 只加字段，严格
   DTO 允许可选/未知字段，且不改变 card 数、旧字段名或收费行展开；
5. 用合成、去标识案例验证 Promise 锁命中时为 CLEAN、零 LLM，并验证 near-negative 不被清掉；
6. 原文链路分别采集 62 回环直连、客户端 `--noproxy` 和浏览器路径的无 PHI 阶段结果；
   对 notes/fees/labs 各验证成功、真实 404、可重试 503，确认费用跳转不等待其他 tab。

原文诊断只允许记录 `source/tab/outcome/duration_bucket/cache_hit/error_code`。直连成功而浏览器
失败应归代理路径；直连也失败再查应用 deadline、SQL Server 或 Hub，不以盲目放大超时作为
默认修复。回滚应用到上一受控 HEAD 后保留新增可空列，不删除历史 trace。

**2026-08-05 生产记录**：功能运行基线 HEAD `ec90ce0f1baa`，62 分支 `production-62`，两端
HEAD 相等且远端受控工作树 clean。schema 在重启前幂等完成，systemd `active/running`、登录页
HTTP 200，安装前后全部 `JAVERT_*` 进程环境一致；SQL、同步线程和只读 Hub 健康，新列可空且
旧 null 行未回填。生产 Promise validate 无 issue、run 15/15；v3 回环空数组 202、unknown
results 200，客户端 `--noproxy` 健康，浏览器去标识 notes/fees/labs 成功且真实缺失返回 404。
可重试 503 通过 62 已安装代码的独立进程内存故障注入验证，没有中断在线 Hub 或修改患者数据。
首次候选部署由生产 validate 发现稀疏范围缺少去标识案例，第二次发现被 Git 忽略的本机参考
工作簿不能成为产品依赖；最终部署范围加入 `tests/promise_cases`，H/I 门禁改用带源文件摘要的
`configs/behavior_source_pairs.yaml` 版本化最小快照，随后从最终提交完整重装并复验。

**2026-08-05 公开审核说明纠偏记录**：数据库旧 reasoning 未改写，功能 HEAD
`e6b05abdf050` 只新增 `public_explanation.narrative` 并在工作台默认渲染“审核说明”；结构化
数组仍不从散文猜测，原始 evidence JSON 继续隐藏。受控 artifact/install、schema、进程重拉
后，两端 HEAD 一致且远端 clean，27 个 `JAVERT_*` 进程环境无差异，systemd/登录/health/SQL
和 v3 submit/results 冒烟通过。浏览器真实详情页抽样 22 个说明区块，最长 399 字，内部工具/
英文裁决词和旧 evidence JSON 区块均为 0。

### 10.13 临床证据工具与眼科专家规则（2026-08-18）

本批新增费用 `unit/order_id` 读契约、结构化医嘱合流、`search_orders`、按服务日期查询的
`catalog_lookup`、检查/检验全文弱兜底，以及 R319-R322。生产制品范围新增根目录两份诊疗
目录工作簿；缺任一文件时目录工具会诚实返回资产未就绪，不得静默用未来版本代替历史日期。

发布前本地门禁：

```bash
PYTHONPATH=src .venv/bin/python scripts/build_rule_mapping.py
.venv/bin/javert promise validate
.venv/bin/javert promise run
.venv/bin/pytest -q
openspec validate close-clinical-audit-tool-gaps --strict
```

按 §10 从已提交 HEAD 执行 artifact/install；本批含 Python/YAML，必须重拉进程。无需修改
`zadig` 结果库 schema，也不得对只读 `sh_yb_platform` 执行 DDL。`aidb.intake_fees` 若仍作为
兜底接入源，由其库管理员另行幂等执行新版 `scripts/sql/create_aidb_tables.sql`，增加可空
“计价单位/医嘱编号”；主 Hub 路径不依赖该操作。

重启后除 §10 通用门禁外，追加：

1. `javert list` 可见 R319-R322 ready，工具列表含 `search_orders/catalog_lookup`；
2. 两份目录文件存在，合成查询 `眼压检查 + 2026-04-20` 返回单位“次”，不得返回未来“单侧”；
3. 去标识合成病例结果为 R319=V、R320=I、R321=I 且零 LLM、R322=I；
4. v3 空 submit=202、unknown results=200，SQL/Hub、登录和 systemd 正常；
5. 真实 OCR 病例重跑会向配置的内部 LLM 提交脱敏病历，必须在明确授权该目的地后才执行。

回滚到上一受控 HEAD 即恢复旧工具/规则；不删除目录、不回写历史 audit_runs。已生成的新规则
结果保留审计追溯，需要业务撤回时走专家 review，不做数据库删除。

## 11. 实测性能 (2026-05-21 50 病人 batch)

```
启动: 56 病人 / 3357 行
完工: 106 病人 / 5016 行 (+50 病人 / +1659 行)
违规: 529 V (dedup), 比之前 275 V 翻了一倍多
不明: 238 I
干净: 3287 C
LLM 总耗时: 522 min (8.7 h), avg 10.4 min/患者
单一 _failed.txt 异常: 0
2 个 exit=1 (J82821 / K51953): 部分规则 LLM 超时, 其他规则照写
```

跑法 (62 后台):
```bash
ssh admin2@192.168.31.62
cd ~/javert
set -a && source .env && set +a
nohup ~/.local/bin/uv run python scripts/run_batch_new.py \
    > output/batch_new/_main.log 2>&1 &
echo $! > output/batch_new/_pid
disown
```

跟进度:
```bash
tail -f output/batch_new/_main.log
wc -l output/batch_new/_progress.jsonl     # 当前完成数
cat output/batch_new/_done.txt 2>/dev/null  # 完工后才有
```

---

## 12. (后续) nginx 反代 + TLS

仅当对外开放 (不是内网) 才需要. 配置模板:

```nginx
server {
  listen 80;
  server_name javert.intranet;
  location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    # SSE 关键
    proxy_buffering off;
    proxy_read_timeout 3600s;
  }
}
```

上 TLS 后改 `JAVERT_SESSION_HTTPS_ONLY=true` + restart.

---

## 13. 已知约束

1. **uv 在 ~/.local/bin/uv (用户级)** — `which uv` 没用, systemd unit ExecStart 写全路径
2. **macOS tar 的 `._*` AppleDouble 文件** 解到 Linux 是垃圾, 升级脚本里 `find . -name "._*" -delete` 清掉
3. **NVARCHAR hook 只 coerce string** (2026-05-21 修) — datetime / int 让 driver auto-detect, 防止 SQL Server TOP (?) 拒绝 + 防止死循环精度 bug
4. **audit_watcher 用 BIGINT id** (2026-05-21 修) — 不用 datetime, 避免 DATETIME2(7) vs Python datetime(6) 精度丢失
5. **改 src/yaml 必须重拉进程才 pickup** — `--reload` 在生产关闭; 重拉走 kill-9 自动 restart (无 passwordless sudo, 见 §3). 静态前端 (static/) 例外: scp 即时生效 + `?v={mtime}` 自动 cache-busting, 无需重启/无需 hard-refresh
