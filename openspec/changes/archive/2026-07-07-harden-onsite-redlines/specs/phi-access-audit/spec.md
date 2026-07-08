# phi-access-audit — delta spec

## ADDED Requirements

### Requirement: 患者原始数据访问留痕

`/api/patient/{pid}/raw` 的每次成功访问 MUST 写入审计日志 (用户名、患者号、时间、数据来源 csv|hub), 与 `/export` 的留痕机制同库同表. 日志写入失败 MUST NOT 阻断正常响应 (记 warning).

#### Scenario: 点开原文有痕

- **WHEN** 已登录用户 dr_zhang 打开患者 J66252 的原始病历 modal
- **THEN** `javert_audit_logs` 新增一行含 dr_zhang / J66252 / 时间 / 来源

#### Scenario: hub 患者同样有痕

- **WHEN** hub 源开启且访问仅存于 `TP_data_hub` 的患者
- **THEN** 审计日志同样落行, 来源标 hub

### Requirement: 患者原始数据访问限流

`/api/patient/{pid}/raw` MUST 有每用户 (session) 频率限制; 超限 MUST 返回 429 而非数据. 限制档位 MUST 不影响专家正常逐个点开病历的使用节奏.

#### Scenario: 枚举被卡

- **WHEN** 单一会话在一分钟内高频请求大量不同患者号的 raw 数据 (超过档位)
- **THEN** 超限请求收到 429, 且这些请求同样留有审计痕迹可事后追查

### Requirement: 生产启动 session secret fail-fast

以生产形态 (with_mssql) 创建应用时, 若 session secret 为默认值/缺失, `create_app()` MUST 抛错拒绝启动——无论通过 `javert web` 还是 `uvicorn ...main:app` 直起.

#### Scenario: uvicorn 直起不再绕过

- **WHEN** 62 上以 `uvicorn src.javert.web.api.main:app` 直接启动且 `.env` 未配 `JAVERT_SESSION_SECRET`
- **THEN** 进程启动失败并给出明确报错, 不会以可伪造 cookie 的状态对外服务

#### Scenario: 本地 dev 不受影响

- **WHEN** Mac 上 `javert web --no-mssql` 启动
- **THEN** 照常启动 (工作台路径本就 503)
