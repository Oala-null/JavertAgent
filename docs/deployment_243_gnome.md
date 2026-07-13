# 243 (gnome) 部署手册 — 医院机房产品实例

> 2026-07-10 首次部署。原则：**自包含**（库/LLM/数据全在本机）、**零本地隐私**（代码无凭据无内网指向，只带 5 个测试病人）。
> 读者：接手运维/复制部署的人。5 分钟读完能重启、能排障、能复制到新机器。

## 拓扑（全部本机）

| 组件 | 位置 | 说明 |
|---|---|---|
| Web 工作台 | `0.0.0.0:8090`，uvicorn（crontab `@reboot` 自启，脚本 `~/start_javert_web.sh`） | 登录账号在库 `javert_users` |
| SQL Server | docker，端口 **1533**，`sa`；compose: `/home/admin2/upload/docker/sqlserver/docker-compose.yml` | 库 **`sh_yb_platform`**：46 国标表(5 病人数据) + 2 扩展表 + 4 业务表 + 索引 + 中文列注释 |
| LLM | llama.cpp `llama-server` 端口 **30000**，启动脚本 `~/launch_llama_q8.sh` | Qwen3.6-35B-A3B **Q8_0 GGUF**；alias=`Qwen/Qwen3.5-35B-A3B-GPTQ-Int4`；`--parallel 1` → **审计并发只能 1** |

所有环境指向在 `~/Javert/.env`（不进 git）。代码里没有任何主机名/凭据。

## 连接架构（为什么换 IP 不怕）

**内部链路全部 127.0.0.1，运行时零硬编码 IP**（2026-07-12 全码扫描验证，残留仅注释）：

```
浏览器 ──(新IP:8090)──▶ web(绑 0.0.0.0:8090)
                          ├─▶ SQL Server   127.0.0.1:1533  (docker)
                          └─▶ LLM llama.cpp 127.0.0.1:30000
```

对外暴露只有 **8090(web) 和 22(ssh)**；机器 IP 变化时应用内部零感知。LLM 30000 当前也绑 0.0.0.0——院内加固可改 `~/launch_llama_q8.sh` 的 `--host 127.0.0.1`（改后重启 llama-server 服务）。

## 断电重启后逐步起服务 SOP（人工操作规程）

> 面向驻场/院方运维。假设机器刚来电，从零到全链路可用，**按顺序执行，每步做完看到预期输出再进行下一步**。全程约 5-10 分钟（大头是 LLM 加载模型）。
> 备注：本机已配置自动恢复（docker 自启 + llama systemd + web crontab），多数情况下开机 8 分钟后直接跳到【第 5 步】验证即可；以下手动流程用于自动恢复失效或需要确定性操作的场合。

### 第 0 步：登录机器

```bash
ssh admin2@<本机IP>          # 或直接接显示器登录, 用户 admin2
```

### 第 1 步：起数据库（SQL Server, docker）

```bash
cd /home/admin2/upload/docker/sqlserver
docker compose up -d
```
预期输出：`Container sqlserver Started`（或 Running）。

验证（等约 30 秒再执行）：
```bash
docker ps | grep sqlserver
```
预期：一行含 `sqlserver   Up`。没有 → `docker logs sqlserver --tail 20` 看报错。

### 第 2 步：起大模型（llama.cpp, systemd）

```bash
sudo systemctl start llama-server
```
无输出即正常。模型加载需 **2-5 分钟**，期间用下面命令轮询：

```bash
curl -s http://127.0.0.1:30000/v1/models | head -c 100
```
预期：出现 `{"models":[{"name":"Qwen/...` 的 JSON。
还没好 → 等 30 秒再试；超过 8 分钟 → `sudo journalctl -u llama-server -n 30` 看日志。

### 第 3 步：起 Web 工作台

```bash
bash /home/admin2/start_javert_web.sh
```
预期输出：`web 已拉起 <时间>`（如果显示 `web 已在运行` 说明自动恢复已经拉起过了，正常）。

验证：
```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/
```
预期：`200`。不是 → `tail -30 ~/javert-web.log` 看报错。

### 第 4 步：全链路业务验证

院内任意电脑浏览器打开 `http://<本机IP>:8090`：
1. 用 admin 账号登录 → 应进入工作台
2. 工作台应显示 **5 个测试病人**（J66252 / K03341 / J13365 / 211530148 / 211345984）
3. 点开任一病人 → 能看到审计结果明细

### 第 5 步（可选）：跑一条真实审计确认 LLM 链路

```bash
cd /home/admin2/Javert
~/.local/bin/uv run --extra sqlserver javert audit-patient J66252 --use-router --concurrency 1
```
预期：逐条规则输出，约 2-3 分钟结束，末尾 `Verdicts: ... 0 failed`。

### 常见失败速查

| 现象 | 处理 |
|---|---|
| 第 1 步 docker 命令不存在/守护进程未起 | `sudo systemctl start docker` 后重来 |
| 第 2 步 30000 端口被占 | `pgrep -fa llama-server` 看是否有手动残留进程, `pkill -f llama-server` 后重新 systemctl start |
| 第 3 步 web 起了但 502/500 | `tail -30 ~/javert-web.log`; 常见为数据库未就绪 → 回第 1 步验证后重跑第 3 步 |
| 登录 401 | 密码问题: `uv run --extra sqlserver javert mssql-user reset-password admin` |
| 断电后系统时间漂移 | `timedatectl` 查看; 偏差大找网管校时 (影响审计时间戳) |

## 接入医院内网 / 更换 IP 手册（逐步）

前提认知：**应用配置一行都不用改**（内部全 127.0.0.1）。要做的只是网络层和访问入口。

```bash
# ① 改机器 IP (医院网管通常直接给网口配; 如需自己配, Ubuntu netplan 示例)
sudo vim /etc/netplan/01-netcfg.yaml     # 按医院分配的 IP/网关/DNS 填
sudo netplan apply
ip addr show | grep "inet "              # 确认新 IP 生效

# ② 重启一次做断电恢复演练 (顺便验证自启链路)
sudo reboot
# ...等 5-8 分钟...

# ③ 来电验证 (本机, ssh 或接显示器)
docker ps | grep sqlserver
curl -s http://127.0.0.1:30000/v1/models | head -c 80
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/

# ④ 院内其他机器访问验证
#    浏览器打开 http://<新IP>:8090 → admin 登录 → 工作台 5 病人可见
#    打不开先查医院防火墙是否放行 8090 入站

# ⑤ (可选加固) LLM 端口不对外
sed -i 's/--host 0.0.0.0/--host 127.0.0.1/' ~/launch_llama_q8.sh && sudo systemctl restart llama-server
```

**换 IP 后无需检查的清单**（都验证过与 IP 无关）：`.env` 数据库/LLM 指向、前端页面资源（相对路径）、审计批跑、账号登录。
**维护通道变化**：从我方网络 ssh 不再可达（医院内网隔离）——进院前把本手册 + 仓库同步给驻场人员。

## 新病人导入后的跑批流程

> 场景：院方把新病人的数据灌进了本机库 `sh_yb_platform`（国标表 + 文书扩展表，口径见对接材料）。
> 之后两步：**取数 → 跑批**，结果自动进工作台。

### 第 1 步：从库取数（ETL）

```bash
cd ~/Javert
# 推荐: 全量取 (取库里所有病人, 保证工作台病人列表完整; 几个病人也就几秒)
~/.local/bin/uv run --extra sqlserver python scripts/etl_from_data_hub.py --all

# 或只取指定病人 (⚠ 会整体覆盖取数文件 → 工作台列表只剩这次取的人; 一般用 --all)
~/.local/bin/uv run --extra sqlserver python scripts/etl_from_data_hub.py --patients 病人号1,病人号2
```
预期输出：`→ data_import_hub/` 下逐文件行数（shi_fee / case_notes / shi_zd / shi_ss / lab_results / examinations）。
新病人的文书或费用为 0 行 → 先回对接材料查该病人数据是否入库齐全。

### 第 2 步：跑审计批

```bash
bash ~/run_audit.sh 病人号1 病人号2 ...
```
- 每个病人约 **2-10 分钟**（视病历复杂度，llama 串行）；逐条规则实时落库
- 只跑新病人即可——老病人的结果都在库里，不用重跑；重复跑也安全（结果保留历史、前端取最新）
- 挂后台跑大批量：`nohup bash ~/run_audit.sh 病人号... > ~/javert-batch.log 2>&1 &`，进度 `tail -f ~/javert-batch.log`

### 第 3 步：工作台验证

浏览器 `http://<本机IP>:8090` → 病人列表出现新病人 → 点开看审计结果。
列表没出现新病人 → 第 1 步是否用了 `--all`；有病人无结果 → 第 2 步日志找该病人的报错。

## 常用操作

```bash
# 重启 web (幂等脚本, 也是 @reboot 自启入口)
pkill -f "uvicorn javert[.]web"   # 注意 [.]: 裸 pkill -f 会自匹配杀掉你自己的 shell
bash ~/start_javert_web.sh

# 冒烟 (三件套)
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8090/          # 200
curl -s http://127.0.0.1:30000/v1/models | head -c 120                    # LLM alias
tail -20 ~/javert-web.log                                                  # 无 pyodbc/schema 报错

# 跑一个患者审计 (结果实时进库, 工作台可见)
cd ~/Javert && ~/.local/bin/uv run --extra sqlserver javert audit-patient J66252 --use-router --concurrency 1

# 5 病人批跑
bash ~/run_243_baseline.sh   # log: ~/javert-batch.log
```

测试病人：`J66252 K03341 J13365 211530148 211345984`。性能基线（2026-07-10 实测）：135 规则 25 分钟，单规则 p50 8.9s，患者 2-10 分钟。

## 排障速查

| 症状 | 原因 | 处理 |
|---|---|---|
| 启动日志 `No module named 'pyodbc'` | uv sync 没带 extra | `uv sync --extra sqlserver` 后重启 |
| `.env` 的值不生效 | **配置优先级 env > configs/llm.yaml > .env 文件**，yaml 里残留了同名键 | 从 `configs/llm.yaml` 删掉该键（yaml 严禁放环境指向） |
| 登录 401 | 账号/密码；账号管理走 CLI | `uv run --extra sqlserver javert mssql-user list / reset-password` |
| 客户端 502 但服务正常 | 客户端走了 HTTP 代理劫持内网地址 | 绕过代理（curl `--noproxy`、httpx `trust_env=False`） |
| 工作台无患者 | 业务表 `javert_audit_runs` 空 | 跑一轮审计即可 |
| CREATE VIEW 权限拒绝（启动 warning） | init_schema 试建人工查询视图 | 无害，代码不依赖视图；sa 部署无此问题 |

## 从零复制到新机器（例：正式院内机）

1. 前置：docker SQL Server、llama.cpp+模型、`uv`、`msodbcsql18`
2. Mac 侧打包：`tar src configs scripts tests pyproject.toml uv.lock` + `data_import_hub/`（5 病人）→ 解到 `~/Javert`
3. 写 `.env`（模板见仓库 `.env.243`，改 SQL 密码/SESSION_SECRET）
4. 灌库（可从任意可达机器执行，env 指向目标库）：
   `push_data_hub_filled.py --data-dir <5p目录>` → `create_data_hub_indexes.sql` → `create_javert_tables.sql` → `apply_tp_comments.py` → 建 admin
5. 起 web（上方命令）→ 冒烟 → 跑批
6. **交付前**：改 admin 密码；确认 `~/Javert/data/` 只有 `router/`（无任何病人 CSV）

## 进院前待办
- [ ] **校时**：2026-07-12 实测系统时钟快约 1 天（显示 Jul 13）——审计时间戳/日志全用它，进院时让网管配 NTP 或手动校准 (`sudo timedatectl set-time ...`)
- [ ] admin 密码从演示值改为正式值（`javert mssql-user reset-password`）
- [ ] （可选）LLM 端口收敛到 127.0.0.1（见换 IP 手册 ⑤）

## 红线

- 这台机器进医院机房：**永远只放测试病人数据**，任何全量病人数据不得上机
- `.env` 含密码，不出机、不进 git
- llama-server `--parallel 1` 时审计 `--concurrency` 必须为 1
