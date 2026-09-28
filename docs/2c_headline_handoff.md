# 2C headline 合同交付门禁

本页记录 `add-public-audit-headline-contract` 的交付证据。Javert upstream 已于 2026-08-26 部署
到 62；2C 后端与 app3 尚未部署，不能把 upstream 上线误认为用户界面已生效。

## Schema/API 就绪范围

- SQLite `audit_runs.headline TEXT NULL` 已进入新建 schema 与幂等 migration；旧行保持 NULL。
- SQL Server `javert_audit_runs.headline NVARCHAR(120) NULL` 已进入幂等 DDL、INSERT、完整读取、
  Workbench/SSE 回放与 SQLite→SQL Server 同步链路。
- v1 `results[]`、v2/v3 `cards[]`、审计 SSE、Workbench 公开投影同时 additive 返回顶层
  `headline` 与 `public_explanation.headline`；原 reasoning/evidence/matched_items 不删不改名。
- 去标识 v3 交付样例见
  [`fixtures/v3_headline_contract_fixture.json`](fixtures/v3_headline_contract_fixture.json)。

## 62 已完成证据（2026-08-26）

- Javert runtime `f24071e338c65b82369a71ee947795e928f35cc5` 已按 artifact/install 发布，
  `production-62` 受控 tracked scope clean；systemd、登录、SQL Server health 与 Hub 均正常。
- SQL Server 实测 `headline NVARCHAR(120) NULL` 恰一列，52,198 条旧行全部 NULL；SQLite 实测
  `headline TEXT NULL` 恰一列，26,755 条旧行全部 NULL。
- 62 新代码使用关闭 SQL/Hub 的临时 SQLite 完成纯合成 v3 completed-card 验证：两处 headline 相等，
  门控/回退、reasoning/evidence 保留、matched_items 对齐和日志隐私全部通过。
- 未触发真实患者，因而没有用生产新审计证明 SQLite→SQL Server 新行 headline 双写；空数组 submit
  POST 也尚未调用。上述两项不能用静态 fixture 或 unknown GET 冒充。

## 2C 开始消费前必须满足

1. Javert 已按 `deployment_192_62.md` §10.14 完成代码、schema、重启及隔离 v3 合同验收；
2. 2C 目标主机先用其实际只读账号确认 nullable 列存在一次且可 SELECT；生产新行双写证据需另用
   已授权去标识 completed case 补齐，不能为此触发真实患者；
3. 2C 严格 DTO 允许新增/未知字段，首屏读取 top-level headline，完整展开继续读取 reasoning/evidence；
4. v3 fixture 校验两处 headline 相等，且 card 数、matched_items 和三态语义不变；
5. 以上门禁满足后，`add-progressive-audit-disclosure` 才开始 OCR 查询/UI 升级。

本地验证命令和最终 collected/pass/skip/fail/error 记录在本 change 的 `tasks.md`；远程证据详见
`deployment_192_62.md` §10.14。普通 v3 headline 只有在 2C 实际目标主机发布后才会呈现，OCR
Workbench 开关在只读列/权限验证前继续保持 false。
