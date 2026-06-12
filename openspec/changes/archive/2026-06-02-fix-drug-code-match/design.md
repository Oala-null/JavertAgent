## Context

药品命中现在靠**通用名 stem 子串**判定, 同一套逻辑散落三处:
- `drug_audit_lookup.py` (审计工具 `stem_match` / `_kb_stems`)
- `hit_resolver.py:_match_fee_rows` (前端命中项目 → fee 码, 同 `kb_stem` 子串)
- `build_drug_kb.py` (KB 按通用名建键)

实测数据 (本仓 `data/`):
- 含代码 4 表: **928** 去重通用名 / **21694** 国家药品码 (一通用名平均挂 ~23 码, 含多规格多厂商)。
- `shi_fee` 药品行 (西药/中药/草药) 共 **156926** 行: `med_list_codg` (国家码) 非空 **99.1%**, `medins_list_codg` (院内码) 非空 **100%**。
- 患者药品 fee 去重国家码 **1047** 个, 其中 **165** 个落在含代码受监管表 (真实可审面)。
- 子串串味实证 (KB 内部即可证): `丁苯那嗪⊂氘丁苯那嗪`、`乌拉地尔⊂盐酸乌拉地尔`、`人免疫球蛋白⊂静注人免疫球蛋白` —— **码全部不同**。

## Goals / Non-Goals

**Goals:**
- 受监管药命中从「名子串」升级为「国家码精确 join」, 相似药**零串味**。
- `<0.9%` 无国家码的 fee 行 graceful 兜底 (退 name-stem + `needs_review`), 不丢召回。
- 前后端 (`drug_audit_lookup` + `hit_resolver`) 共用同一套确定性匹配, 单点真值。

**Non-Goals:**
- 不改 M8 的 4 类违规判定 prompt (限适应症/超说明书/限二线/禁忌); 命中后的语义裁决逻辑不动。
- 不新增/删除规则; 不动 `drug_indication` (52 药反向洗白); 不连网。
- 退费净额 (Change B `fix-fee-refund-netting`) 与本 change 正交, 不在此处理。

## Decisions

- **D1 码为主键, 名为兜底**: 患者 fee 行 `med_list_codg ∈ KB 知识点 code set` → 命中; `med_list_codg` 空 → 退 `kb_stem` 子串并在结果标 `needs_review=true`。
  - *Why*: 99.1% 覆盖走精确路, 余 0.9% 不静默丢。*Alt*: 纯码 (丢 0.9% 召回, 漏报) / 用 `medins_list_codg` 院内码 (100% 但跨院不通用, 与 KB 同源的是国家码) —— 均不取。

- **D2 KB schema 增 `codes[]` 不删通用名键**: `drugs[通用名] = {entries:[...], codes:[...]}`。既有消费者读 `entries` 不受影响, 匹配方按需切码路。
  - *Why*: 渐进迁移, `drug_audit_lookup` 与 `hit_resolver` 可分别切换, 回滚只需忽略 `codes`。

- **D3 `build_drug_kb` 读含代码表三元组 (知识点序号, 通用名, 药品代码), 按通用名聚合 code set 后与无码表 entries join**。含代码表只有码, `basis`/`检出逻辑` 仍来自原无码 4 表。
  - *Why*: 两套表同知识点不同视图; 通用名是天然 join 键。表头实测在第 4 行 (前 3 行是标题/空行), 解析须按「药品通用名」表头行定位, 鲁棒于偏移 (沿用 `build_drug_kb` 现有扫表头风格)。

- **D4 抽 `code_match` 纯函数, `drug_audit_lookup` + `hit_resolver` 共用**。
  - *Why*: 现在子串逻辑两处分叉是隐患; 码匹配只此一处真值。放 `drug_audit_lookup.py` (hit_resolver 已 import 它的 `fee_clean`/`kb_stem`, 同源)。

## Risks / Trade-offs

- [含代码表通用名与无码表通用名因剂型/标点不完全一致, join 漏配] → 按 `kb_stem` 归一后再 join; 不一致项 build 时 `log.warning` + 落核对清单, 不静默丢。
- [某受监管知识点的码在患者数据里只以院内码出现、国家码恰好空] → 退 name-stem `needs_review`, 交专家复核; 量级 <0.9%, 可接受。
- [KB 重建后 router index / 规则未同步] → tasks 强制重跑 `init_drug_rules.py` + `build_rule_mapping.py` (CLAUDE.md 已列为 KB 改动后必跑)。
- [码精确后召回比子串**降低** (子串本来多命中了串味药)] → 这正是目标 (那些是假阳性); 用 15 条 V 复跑确认降的都是误报, 不是真违规。

## Migration Plan

1. `build_drug_kb.py` 增读含代码表 → 重生成 `drug_audit_kb.json`, diff 出 `codes[]` 字段 + 核对 join 漏配清单。
2. 落 `code_match` 纯函数 + 单测 (含 `丁苯那嗪/氘丁苯那嗪` 反例)。
3. 切 `drug_audit_lookup` 码主路 + name 兜底。
4. 切 `hit_resolver._match_fee_rows` drug 分支码优先。
5. 重跑 `init_drug_rules.py` + `build_rule_mapping.py`。
6. 回归: 现有 15 条药品 V 复跑 + 串味反例; `uv run pytest tests/ -v` 全绿。
- **回滚**: KB 保留旧版快照; 匹配切换在 feature 分支, 一行切回 name-stem。

## Open Questions

- 无码兜底命中的 `needs_review` 要不要在工作台前端也打 facet 标记? → 倾向要, 但归属 Change C (`add-verdict-gate-layer`) 的 facet 工作, 本 change 只在工具结果里带该字段。
