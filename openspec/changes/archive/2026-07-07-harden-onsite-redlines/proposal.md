# harden-onsite-redlines

## Why

正在接外部医院现场 (szx2.0 hub 原文路径刚部署 62), 2026-07 系统扫描确认 2fd3cdc「进院前修复」之后**仍在**的一组进院红线与丢数窗口: PHI 可无痕枚举 (hub 开启后面扩大到 4700+ 患者)、uvicorn 直起绕过 session guard、run-batch persist 失败静默丢数、fees 子串匹配可能串患者、400 被当 503 重试、串行单条失败拖垮整患者、szx 缺首页行患者诊断/手术双清零. 均为小改, 进院前应清零.

## What Changes

- **PHI 访问留痕 + 限流**: `/api/patient/{pid}/raw` 补审计日志 (对齐 `/export` 的 log_action, 记 user/pid/来源) + 每用户频率限制
- **session guard 挪进 `create_app()`**: 默认 session_secret 的 fail-fast 对 `uvicorn ...main:app` 直起同样生效, 不再只护 `javert web` CLI
- **run-batch 完整性**: persist 失败发 `fail` 事件且不发 `result` (堵 2C BFF 收到 verdict 但 sqlite 未落库的真丢数窗口); 请求不存在的 rule_id 发 `fail` 事件 (BFF 可区分"规则不存在"与"漏返回"). ⚠ SSE 契约新增事件, 须同步 2C `bff` 消费端
- **LLM 4xx 快速失败**: `llm_provider.py` 对 4xx (除 429) 不再重试 3 次 (R103 "LLM unavailable" 真因即 400 超长)
- **串行失败不中断**: `audit_patient.py` 串行模式单条 LLM 失败标 failed 继续跑剩余规则, 不 break 整患者
- **fees 患者号精确匹配**: `csv_loader.py` 索引的 `patient_id in k` 子串语义改"精确 或 复合键末段精确"两级, 短住院号不再命中长住院号 (别人的费用混入)
- **szx hub per-patient 兜底 + BA 表索引**: 缺首页行的 szx 患者按患者回退 IH 诊断/手术 (不再仅 `len(jbk)==0` 全空才回退); SYJBK/SYZDK/SYSSK/SYSSK_EXT 四表索引幂等追加进 `create_data_hub_indexes.sql`

## Capabilities

### New Capabilities
- `phi-access-audit`: 患者原始数据端点的审计留痕、频率限制与生产 session secret fail-fast
- `batch-run-integrity`: run-batch SSE 事件的诚实性 (persist 失败/未知规则可感知) + audit loop 失败隔离 (4xx 快速失败、串行不中断)
- `hub-ba-fallback`: szx 病案首页缺行患者的 per-patient IH 回退语义

### Modified Capabilities
- `data-access`: 费用行患者归属匹配语义收紧 (精确/复合键末段精确, 以 ADDED requirement 形式补充)

## Impact

- 代码: `routes_workbench.py` (raw 端点), `web/api/main.py` (create_app), `routes_audit.py` (run-batch), `llm_provider.py`, `commands/audit_patient.py`, `data/csv_loader.py`, `data/hub_source.py`, `scripts/sql/create_data_hub_indexes.sql`
- 外部契约: 2C 平台 BFF 依赖 run-batch SSE (CLAUDE.md 明示改 SSE 字段先同步 bff)——本 change 只**新增** `fail` 事件不改旧字段, 但 bff 需升级处理才能受益
- 部署: 62 升级 + 重启; 142 执行索引 SQL (幂等)
- 不影响: 老患者 CSV 路径行为、工作台 UI
