# 2C headline 合同交付门禁

本页记录 `add-public-audit-headline-contract` 的本地交付证据，不代表 62 已部署。

## Schema/API 就绪范围

- SQLite `audit_runs.headline TEXT NULL` 已进入新建 schema 与幂等 migration；旧行保持 NULL。
- SQL Server `javert_audit_runs.headline NVARCHAR(120) NULL` 已进入幂等 DDL、INSERT、完整读取、
  Workbench/SSE 回放与 SQLite→SQL Server 同步链路。
- v1 `results[]`、v2/v3 `cards[]`、审计 SSE、Workbench 公开投影同时 additive 返回顶层
  `headline` 与 `public_explanation.headline`；原 reasoning/evidence/matched_items 不删不改名。
- 去标识 v3 交付样例见
  [`fixtures/v3_headline_contract_fixture.json`](fixtures/v3_headline_contract_fixture.json)。

## 2C 开始消费前必须满足

1. Javert 先按 `deployment_192_62.md` §10.14 完成代码、schema、重启和 v3 合同验收；
2. 2C 提供“nullable 列存在一次、旧行 NULL、新行双写一致”的去标识证据；
3. 2C 严格 DTO 允许新增/未知字段，首屏读取 top-level headline，完整展开继续读取 reasoning/evidence；
4. v3 fixture 校验两处 headline 相等，且 card 数、matched_items 和三态语义不变；
5. 以上门禁满足后，`add-progressive-audit-disclosure` 才开始 OCR 查询/UI 升级。

本地验证命令和最终 collected/pass/skip/fail/error 记录在本 change 的 `tasks.md` 完成状态与会话
交付摘要中；远程生产证据只能在单独授权发布后补充，不能用本地结果冒充。
