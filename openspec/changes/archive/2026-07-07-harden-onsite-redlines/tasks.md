# harden-onsite-redlines — tasks

## 1. PHI 留痕 + session guard

- [x] 1.1 `/api/patient/{pid}/raw` 补 log_action 审计日志 (user/pid/来源 csv|hub, 写失败不阻断)
- [x] 1.2 raw 端点加 slowapi 每会话限流 (初值 30/min) + 429 响应
- [x] 1.3 session secret fail-fast 挪进 `create_app()` (with_mssql 生产形态强制, --no-mssql dev 宽松), cli.py 原检查收敛为单一来源
- [x] 1.4 单测: 留痕落行 / 限流 429 / uvicorn 形态 fail-fast / dev 形态放行

## 2. run-batch 完整性

- [x] 2.1 persist 成功才发 `result`; persist 失败发 `fail` (rule_id + stage=persist + error)
- [x] 2.2 未知 rule_id 逐条发 `fail` (stage=unknown_rule)
- [x] 2.3 单测: 落库抛错 → fail 无 result / 未知规则回执 / 正常路径事件逐字回归
- [x] 2.4 通知 2C bff: SSE 新增 fail 事件语义 (契约只加不改)

## 3. audit loop 失败隔离

- [x] 3.1 `llm_provider.py` 4xx (非 429) 立即 raise, 错误含 status + body 摘要; 429/5xx 重试不变 + 单测
- [x] 3.2 `audit_patient.py` 串行分支单条失败标 failed 继续, 与并发模式对齐 + 单测

## 4. fees 精确匹配

- [x] 4.1 `csv_loader.py` 索引改两级精确 (等值 / 复合键末段等值, strip 空白)
- [x] 4.2 新旧匹配集合 diff 脚本跑 62 数据快照, 差异逐条核 (预期只消子串误归属)
- [x] 4.3 单测: 短号不吃长号 / 复合键命中 / J66252 基线回归

## 5. szx hub 兜底 + 索引

- [x] 5.1 `hub_source.py` fetch_zd/fetch_ss BA 分支改 per-patient 源选择 (IH 排除集 = 真有 BA 行的患者)
- [x] 5.2 补 BA 分支纯逻辑单测 (stub q(): 源选择 / 前缀5解析 / 主次诊去重)
- [x] 5.3 `create_data_hub_indexes.sql` 幂等追加 SYJBK/SYZDK(+ZDDM)/SYSSK/SYSSK_EXT 索引

## 6. 部署验证

- [x] 6.1 `uv run pytest tests/ -v` 全绿
- [x] 6.2 62 升级 + systemd 重启, 冒烟: 登录 / raw 端点留痕落行 / run-batch 一条 / szx 患者详情页
- [x] 6.3 142 执行 BA 索引 SQL, 量单患者 hub 首查耗时 (对照基线 0.31s)
- [x] 6.4 `docs/deployment_192_62.md` 补 session secret 强制与限流说明
