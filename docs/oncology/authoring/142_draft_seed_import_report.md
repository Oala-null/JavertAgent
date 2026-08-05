# 142 `知识库_work` DDL 与 generated DRAFT 物化探针报告

快照日期：2026-07-22。本文只记录去敏后的数据库对象、批次、checksum 和聚合计数；不记录
主机凭据、连接串、患者标识、病历原文或上传 principal。

## 当前结论

- 精确目标经 `DB_NAME()` 核验为 `知识库_work`；`sh_yb_platform`、`TP_data_hub` 和 `zadig`
  均未作为知识库写入目标。
- 最终幂等 DDL 连续执行两次成功；实际核验为 3 个 schema、25 张 KB 表、6 个中文审核视图、
  129 个约束、44 个索引和 23 个必需触发器（含
  `tr_curated_atom_target_authority`）。
- 当前账号具备 SELECT/INSERT/UPDATE/ALTER，但为 `db_owner`，不是最小权限 principal；截至
  快照日可见的最新 full backup 为 2026-06-03，当前备份策略与恢复演练没有得到证明。因此
  OpenSpec 9.3 和 9.10 仍未完成。
- 两次旧探针 materialize 都由同一事务完整回滚，FAILED staging/history 未删除或改写。
  用户重新授权后，最终两份 DRAFT 均已上传并物化；当前 `review_event=0`、
  `knowledge_release=0`，未产生任何专家批准或发布。
- 最终修正版两份工作簿均为 `valid=true`、`errors=0`、`contains_phi=false`，并通过 142
  preflight、服务端校验、单事务 materialize 与重复上传幂等复用检查。

## 人工可读视图

| 视图 | 用途 |
|---|---|
| `kb.vw_eligibility_rule_overview` | 药品、来源、规则版本、分支及最新审核决定总览（462 行） |
| `kb.vw_eligibility_condition_detail` | 条件树逐节点、目标中文名、来源定位和审核状态（2,900 行） |
| `kb.vw_regimen_composition` | 方案名称、上下文、别名和精确组分（4 行） |
| `kb.vw_latest_review_status` | 各实体 append-only 最新审核事件（0 行） |
| `kb.vw_import_reconciliation` | staging 状态及逐实体物化对账（4 行，含两次历史失败） |
| `kb.vw_release_overview` | candidate/published release、成员和 active 状态（0 行） |

上述行数为物化后的只读聚合对账；专家可在 142 直接审阅 DRAFT，不需通过 Javert 运行时。

## 最终修正版 DRAFT 物化

| 工作簿 | SHA-256 | batch | staging 行 | 结果 |
|---|---|---|---:|---|
| 肿瘤药指南适应证与医保限定条件树 KB | `sha256:58ad7dc5f10fdbdb249347a8d55d105a7b2e0b83707882aee280bc95287f509c` | `batch_c2e4cf39a6e406aea8308953` | 9,511 | MATERIALIZED：358 concept、1,608 product、213 source fragment、221 rule revision、462 branch、2,900 node、8 class |
| 肿瘤治疗方案组成 KB | `sha256:ff4648982b31c486f6bebc383ee0118a348a385a59d104f9c6160e0befd6deb3` | `batch_e06cc07d61563c5f48479534` | 339 | MATERIALIZED：4 regimen revision、130 alias、11 context、16 exact-CONCEPT component、16 disabled schedule |

生成期跨工作簿闭包为 0 dangling：16 个方案组分全部引用已有精确 `CONCEPT`；无法证明的
自由组合保留原文并降为 `unsupported/VALUE` 待专家映射，不创建空壳方案。泼尼松与地塞米松
只从医院目录保守全等匹配真实产品/编码，不生成假产品。

## 保留的失败证据

| batch | 工作簿 SHA | 行数 | 状态 | 去敏原因 | typed 结果 |
|---|---|---:|---|---|---|
| `batch_0b180e74cce9686bf955cce4` | `sha256:20aeef888249639d093fcea08cfbca796033d332de07b625483e9c4ebdfa7412` | 339 | FAILED | 旧方案 concept namespace 未进入 authority，数据库触发器阻断 | 全量回滚，0 行 |
| `batch_7def0f80fcb37b8684a2b88f` | `sha256:26c7cf605cc0f753538d152bd89849209de4626063adaafbb2c7f54358c03885` | 9,535 | FAILED | 相同来源文本的不同锚点未合并，source fragment 唯一约束阻断 | 全量回滚，0 行 |

上述问题均已在最终修正版中修复并增加回归：精确成分共用 concept authority；同一文件、同一
内容 checksum 的多锚点合并为一个 fragment 并保留全部稳定定位；`MAPPED` 精选知识允许保存
计划 target，但只有 live target + verification evidence 才可转 `VERIFIED`。

## 剩余门禁

1. 专家在上述视图/工作簿审核 DRAFT，并通过 append-only `review_event` 留痕；当前不得将
   `review_event=0` 解释为默示批准。
2. DBA/运维补齐最小权限 principal、备份策略和恢复演练记录；当前账号为 `db_owner` 不改变
   已物化 DRAFT 的事实，但 OpenSpec 9.3/9.10 仍未完成。
3. 只有专家批准、独立发布授权、release candidate、paired shadow 和回滚演练全部完成后，
   才能生成 `PUBLISHED` bundle 并在 62 启用运行时 release 路径。

最终只读 lifecycle 对账为 221 条 eligibility revision 与 4 条 regimen revision，均为 `DRAFT`。

generated DRAFT 只供专家在 142 审阅，不参与 Javert 自动裁决。没有专家 append-only 审核、
独立发布授权和 `PUBLISHED` bundle 前，62 不得配置 `JAVERT_ONCOLOGY_RELEASE_DIR`。
