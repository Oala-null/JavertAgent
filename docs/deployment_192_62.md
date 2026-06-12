# Javert Web 审核工作台 — 192.168.31.62 部署 runbook

**状态**: 🟢 已上线. systemd `javert-web.service` 运行中.

部署日: 2026-05-21. 当前数据: 106 病人 / 5016 audit_runs 行 / 529 V 待审 (142 累计 13880 行含历史).
工作台版本: **v0.9** — 违规卡「命中项目」块 (编码·名称·限定) + 点证据右侧 **parallel 滑出原文对照面板** + 病人列表 facet (tag/费用/主诊/时间) + 费用类别就地展开. 详见 `review_workbench_user_guide.md`.
> **2026-06-03 UI 增量** (升级附加步骤见 §10.1): 病人列表按金额/生成时间排序 + 文书/费用右侧新增「检验记录」tab (检验+检查合并, 命中可 trace 跳转) + 命中名修复 (占位 locator → 具体药品/项目名) + 违规卡 AI 推理/证据块默认展开.

---

## 1. 服务器现状

| 项 | 值 |
|----|----|
| 主机 | `192.168.31.62` (Ubuntu 24.10, 6.11.0-19-generic) |
| 用户 | `admin2` (SSH key auth) |
| 端口 | **8090** (TCP, 仅内网) |
| 项目路径 | `/home/admin2/javert/` |
| Python | 3.12.7 (系统) |
| uv | `/home/admin2/.local/bin/uv` (curl 装的, 用户级) |
| 系统 ODBC | `msodbcsql18` 18.6.1.1 + `unixodbc` 2.3.12 (apt 已装) |
| 同机服务 | sglang :30000 (不干扰); gdparse :8889; open-webui :8080; lethe :18090 (各自端口) |

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

# 工作台
JAVERT_WEB_HOST=0.0.0.0
JAVERT_WEB_PORT=8090
JAVERT_WEB_WITH_MSSQL=true
JAVERT_ALLOW_REGISTER=false       # 注册关闭, 走 mssql-user create

# LLM (audit-patient 跑 audit 时)
JAVERT_LLM_ENDPOINT=http://192.168.31.62:30000/v1
```

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

Mac 上改完代码:

```bash
# 1. Mac 上 tar src
cd /Users/shane/26er/Javert
tar -czf /tmp/javert-src.tgz --exclude='__pycache__' -C . src

# 2. scp 到 62
scp /tmp/javert-src.tgz admin2@192.168.31.62:/tmp/

# 3. 62 unpack
ssh admin2@192.168.31.62 'cd ~/javert && tar xzf /tmp/javert-src.tgz \
    && rm /tmp/javert-src.tgz && find . -name "._*" -delete 2>/dev/null'

# 4. 重拉进程 (无 sudo — admin2 杀自己进程, systemd Restart=on-failure 自动拉起)
ssh admin2@192.168.31.62 'OLD=$(systemctl show -p MainPID --value javert-web); \
    kill -9 "$OLD"; sleep 8; \
    echo "active=$(systemctl is-active javert-web) http=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8090/login)"'
```

**纯前端改动 (只 scp `src/javert/web/static/*` 或模板) 跳过 step 4** — 静态文件即时生效 + cache-busting 自动刷; 仅 `*.py`/yaml 改动才需 step 4 重拉.

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

---

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
