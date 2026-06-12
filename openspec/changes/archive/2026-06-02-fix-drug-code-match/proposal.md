## Why

`drug_audit_lookup` 现在按**通用名 stem 子串**匹配患者用药与监管知识库 (`drug_audit_lookup.py:111 stem_match`)。子串匹配让**相似药名串味**: `奥美拉唑` 是 `艾司奥美拉唑` / `艾普拉唑` 的子串、`丁苯那嗪` 是 `氘丁苯那嗪` 的子串 —— 它们是不同药、国家医保码不同、临床指征也不同, 子串法却误命中, 直接产生假阳性 V。这是 v0.8 药品规则上线后用户实测发现的工具正确性 bug。

用户已落地 `data/药品类规则/含代码/` 4 张「含代码」表 (每知识点带**国家医保药品码**)。实测: 这些码与 `shi_fee.med_list_codg` **完全同格式** (`XB05BAU005B002...`), 且 `med_list_codg` 覆盖全量药品 fee 行的 **99.1%**。按码精确 join 可把同名串味**一刀根除**, 这是用户要的「百分百解决」。

## What Changes

- `scripts/build_drug_kb.py` 增读 `data/药品类规则/含代码/` 4 表 (表头在第 4 行: `对应知识点序号|药品通用名|序号|药品代码`), 按通用名聚合**国家药品码集合**, 与既有无码 4 表的 `basis`/`检出逻辑` 按通用名 join。
- `configs/drug_audit_kb.json` schema 增 `codes: []` 字段 (向后兼容, 既有 `entries`/通用名键不动)。
- `drug_audit_lookup` 匹配主路改**码精确 join**: 患者 fee 行 `med_list_codg ∈ KB 该知识点 code set` → 命中; `med_list_codg` 为空 (<0.9%) → 退回 name-stem 并标 `needs_review`。
- `hit_resolver._match_fee_rows` drug 分支同样**码优先** (修复前端给相似药显示**错编码**的问题)。
- 新增确定性纯函数 `code_match(fee_codes, kb_code_set)`, 供 `drug_audit_lookup` 与 `hit_resolver` 共用 (单点真值, 防两处再分叉, 复用现有 `kb_stem`/`fee_clean` 的同源模式)。
- KB 重建后按 CLAUDE.md 约定重跑 `scripts/init_drug_rules.py` + `scripts/build_rule_mapping.py` (router index 同步)。
- **不** BREAKING 对外 API; 仅内部匹配精度提升, KB 结构为增量字段。

## Capabilities

### New Capabilities

<!-- 无新 capability; 本 change 纯精度修复 -->

### Modified Capabilities

- `drug-audit`: 药品命中匹配从「通用名 stem 子串」升级为「国家药品码精确 join + name-stem 兜底」; `drug_audit_kb.json` 增国家码维度; `build_drug_kb` 增读含代码 4 表。
- `evidence-anchoring`: `hit_resolver` 的 drug → 患者 fee 行匹配改码优先, 消除相似药错码 (#5 命中项目块编码正确)。

## Impact

- **代码**: `src/javert/tools/drug_audit_lookup.py` (码 join 主路 + needs_review 兜底)、`src/javert/web/hit_resolver.py` (`_match_fee_rows` drug 分支码优先)、`scripts/build_drug_kb.py` (读含代码表 + 聚合 code set)、共用 `code_match` 纯函数落点。
- **数据/配置**: `configs/drug_audit_kb.json` 重建 (增 `codes[]`)。`scripts/init_drug_rules.py` + `scripts/build_rule_mapping.py` 重跑产物。
- **外部依赖**: 零变化 (沿用 csv 数据源 + sglang, 不连 142, 不动 zadig_agent, 不动 `drug_indication` 52 药)。
- **验证**: 重跑现有 15 条药品 V 复核 + `艾普拉唑/奥美拉唑`、`丁苯那嗪/氘丁苯那嗪` 串味回归 (应零误命中)。
