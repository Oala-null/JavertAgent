## 1. KB 重建 (含国家药品码)

- [x] 1.1 `build_drug_kb.py` 增读 `data/药品类规则/含代码/` 4 张 xlsx, 按「药品通用名」表头行 (第 4 行) 定位, 解析 (知识点序号, 通用名, 药品代码) 三元组
- [x] 1.2 按通用名聚合国家药品码 → code set, 与既有无码 4 表的 `entries`(basis/检出逻辑) 按通用名 (kb_stem 归一) join
- [x] 1.3 `drug_audit_kb.json` schema 增 `codes: []` (drugs[通用名] 下), 保持 `entries`/通用名键向后兼容
- [x] 1.4 含代码表通用名无法 join 无码表 entries 时 `log.warning` + 落核对清单 (不静默丢)
- [x] 1.5 重生成 KB, diff 出 `codes[]` 字段, 抽查 165 个受监管码覆盖正确

## 2. 共用码匹配纯函数

- [x] 2.1 在 `drug_audit_lookup.py` 落 `code_match(fee_codes: set, kb_code_set: set) -> bool` 纯函数 (国家码精确交集)
- [x] 2.2 单测: `奥美拉唑` 不命中 `艾司奥美拉唑`/`艾普拉唑`; `丁苯那嗪` 不命中 `氘丁苯那嗪` (码不同反例全过)

## 3. 切 drug_audit_lookup 码主路

- [x] 3.1 `lookup_patient_drugs` 取患者 fee 行的 `med_list_codg`, 按知识点 code set 做 `code_match` 命中 (替代 `_kb_stems` 子串主路)
- [x] 3.2 fee 行 `med_list_codg` 空 → 退回 `kb_stem` 子串, 命中结果标 `needs_review=true`
- [x] 3.3 `format_for_agent` bulk 输出在码兜底命中处显示「(名兜底, 需复核)」提示
- [x] 3.4 `lookup_single_drug` 保持名查 (single 模式按通用名查 KB 事实, 不涉患者 fee)

## 4. 切 hit_resolver 码优先

- [x] 4.1 `_match_fee_rows` drug 分支改调 `code_match` (患者 fee 行国家码 ∈ 证据通用名 code set) 优先, 子串兜底
- [x] 4.2 无码匹配行时命中项目编码列留空 + `needs_review`, 不猜配相似药码
- [x] 4.3 确认 `hits_to_json`/`hits_from_json` 序列化稳定 (码路下二次调用 byte-identical)

## 5. 下游同步与回归

- [x] 5.1 重跑 `scripts/init_drug_rules.py` (M8 规则重渲染) + `scripts/build_rule_mapping.py` (router index)
- [x] 5.2 `uv run pytest tests/ -v` 全绿 (含新增 code_match 反例单测)
- [x] 5.3 重跑现有 15 条药品 V (audit DB) 复核: 确认降量的都是串味误报, 真违规不漏
- [x] 5.4 端到端: `audit-patient` 跑 1-2 个药品丰富患者 (如 J90508), trace 里药品命中走码路 + 无串味
- [x] 5.5 工作台抽查某药品违规卡「命中项目」编码正确 (与审计码一致)
