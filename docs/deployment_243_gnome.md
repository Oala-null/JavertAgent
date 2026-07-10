# 243 (gnome) 部署手册 — 医院机房产品实例

> 2026-07-10 首次部署。原则：**自包含**（库/LLM/数据全在本机）、**零本地隐私**（代码无凭据无内网指向，只带 5 个测试病人）。
> 读者：接手运维/复制部署的人。5 分钟读完能重启、能排障、能复制到新机器。

## 拓扑（全部本机）

| 组件 | 位置 | 说明 |
|---|---|---|
| Web 工作台 | `0.0.0.0:8090`，`~/Javert` 下 nohup uvicorn（**无 systemd**） | 登录账号在库 `javert_users` |
| SQL Server | docker，端口 **1533**，`sa`；compose: `/home/admin2/upload/docker/sqlserver/docker-compose.yml` | 库 **`sh_yb_platform`**：46 国标表(5 病人数据) + 2 扩展表 + 4 业务表 + 索引 + 中文列注释 |
| LLM | llama.cpp `llama-server` 端口 **30000**，启动脚本 `~/launch_llama_q8.sh` | Qwen3.6-35B-A3B **Q8_0 GGUF**；alias=`Qwen/Qwen3.5-35B-A3B-GPTQ-Int4`；`--parallel 1` → **审计并发只能 1** |

所有环境指向在 `~/Javert/.env`（不进 git）。代码里没有任何主机名/凭据。

## 常用操作

```bash
# 重启 web
pkill -f "uvicorn javert[.]web"   # 注意 [.]: 裸 pkill -f 会自匹配杀掉你自己的 shell
cd ~/Javert && nohup setsid ~/.local/bin/uv run --extra sqlserver \
  uvicorn javert.web.api.main:app --host 0.0.0.0 --port 8090 > ~/javert-web.log 2>&1 &

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

## 红线

- 这台机器进医院机房：**永远只放测试病人数据**，任何全量病人数据不得上机
- `.env` 含密码，不出机、不进 git
- llama-server `--parallel 1` 时审计 `--concurrency` 必须为 1
