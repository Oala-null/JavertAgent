## Context

`hub_source.py` 是 `TB_*` 到内部六文件契约的唯一映射源，但当前每条 SQL 都直接引用固定表名。`TP_data_hub` 中新增的脱敏数据使用 `desus_TB_*`，其列结构与原表一致；需要在不复制映射逻辑、不改变默认生产源的前提下选择该表族。表名不能用 SQL 参数绑定，因此配置值必须先经过严格标识符校验。

## Goals / Non-Goals

**Goals:**

- 以一个环境可覆盖的配置选择 Hub 表名前缀。
- 批量 ETL、2C Hub 兜底和工作台实时 Hub 原文源复用同一前缀化 SQL。
- 默认空前缀时保持当前 SQL 与取数结果不变。
- 只读取 `desus_*` 中的唯一脱敏患者，并以 `desus` batch tag 持久化审计结果。

**Non-Goals:**

- 不创建、修改或重灌 `TP_data_hub` 表。
- 不把前缀变成任意 schema/table SQL 入口，也不支持每张表分别配置。
- 不改变内部六文件列契约、规则集、Router 或 workbench 展示契约。

## Decisions

1. 新增 `JAVERT_HUB_TABLE_PREFIX` 对应配置，默认 `""`。选择独立配置而不是修改 `JAVERT_HUB_DATABASE`，因为数据库与表族是两个正交维度，且本次两套表位于同一库。
2. 在 `hub_source.py` 提供唯一的表名构造函数，只接受空值或 SQL 标识符安全字符，并对长度设限。表名仍由代码内固定的 `TB_*` 基名组成；拒绝点号、括号、引号、空白和其他 SQL 片段。
3. 所有 `fetch_*` 保留默认空前缀参数，已有调用方和离线测试不传参时行为不变；知道配置的入口显式传入 `cfg.hub_table_prefix`。
4. 单病人运行通过只读 `desus_patient_manifest` 核对患者集合，再用带前缀 ETL 生成受限权限的临时六文件目录；审计进程显式设置 `JAVERT_BATCH_TAG=desus`，直接走既有持久化链路，不执行全量 pending 同步。
5. `anchors_json` 新写入采用版本化 envelope，只有由持久化入口使用本次审计 loader 的患者收费切片生成时才标记 `verified_fee_snapshot=true`。workbench 可直接复用这种缓存；历史 list 格式仍视为未验证并重新关联实时净正收费。选择自包含缓存而不是按 `batch_tag` 动态切换全局 Hub，是为了避免一个 desus 患者让同进程其他患者错误切源。

## Risks / Trade-offs

- [新增调用方忘记传前缀而回到默认表] → 覆盖当前批量 ETL、2C Hub 兜底和工作台实时源，并用调用参数测试锁定。
- [配置值进入 SQL 造成注入] → Pydantic 配置与表名构造函数双重校验，且基名只来自代码常量。
- [脱敏表缺少某个运行时必需表] → 运行前核对所需表清单，真实单患者 ETL 作为端到端门禁；失败不回退无前缀表，避免混源。
- [结果错误关联其他患者或历史 pending] → 从 manifest 精确得到唯一患者号，只运行该患者；使用现有逐条双写，不调用无范围的 pending 同步。
- [缓存把检索词冒充收费项目] → verified envelope 只能由 `resolve_hits` 对本次患者实际净正收费切片生成；legacy/损坏 envelope 继续 fail closed 并重算。

## Migration Plan

1. 部署兼容代码，未设置新配置时行为不变。
2. 仅在本次进程设置 `JAVERT_HUB_DATABASE=TP_data_hub`、`JAVERT_HUB_TABLE_PREFIX=desus_` 和 `JAVERT_BATCH_TAG=desus`。
3. 先取数并核对六个数据域只含 manifest 中的唯一患者，再运行单患者审计并检查 SQLite/SQL Server latest rows 的 tag。
4. 对已存在的 `desus` run 用同一私密收费快照按患者和 batch tag 定向回填 verified anchors，核对卡片命中与费用定位。
5. 回滚时移除 `JAVERT_HUB_TABLE_PREFIX`；verified envelope 仍可被旧代码视为 cache miss 并安全回退，无需数据库迁移。

## Open Questions

无。真实表名已只读确认采用 `desus_TB_*`。
