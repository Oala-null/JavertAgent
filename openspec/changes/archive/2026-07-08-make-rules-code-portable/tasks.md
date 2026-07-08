# make-rules-code-portable — tasks

## 1. rule schema 加结构化字段

- [x] 1.1 `rule.py` Rule 加 `trigger_codes: list[str]` (默认空) + `exam_keywords: list[str]` (默认空)
- [x] 1.2 单测: 两字段默认空、可加载、pydantic 校验通过 (随 routing/verdict_gate 测试覆盖)

## 2. router 编码命中 + 死代码清场

- [x] 2.1 `types.py` FeeItem 加 `med_list_codg: Optional[str]`; `adapter.py` 从 fee 行填充; `RouterDecision` 删 `overlapping_kept` / `javert_only_kept`
- [x] 2.2 `router.py` `_JavertMeta` 加 `trigger_codes`; 删六个 applicable_* 字段 + `_yaml_applicable_to_patient` 方法 + route() applicable 步; 加 `_build_code_set` + `_yaml_code_hit`; route() 命中 = `keyword_hit OR code_hit`; stats 去 `passed_applicable`/`pruned_applicable`
- [x] 2.3 `audit_patient.py:405-406` 日志行改为只报 `kept`/`scanned` 计数 (不引用已删字段)
- [x] 2.4 单测 (RuleRouter 首套, `tests/test_routing.py`): 编码前缀命中 / 名称命中 / OR 语义 / 无 trigger_codes 零回归 / 换名模拟 (名换编码不变→编码路径命中且 keyword miss) / status 闸保留 / 无 Case-A 字段

## 3. 费用分类切官方类别标签

- [x] 3.1 `search_fees._classify(name, chrgitm_label)` 标签自信桶优先 + 名称兜底; 调用点抽 `_chrgitm_label` 列 (缺列空串)
- [x] 3.2 单测 (`tests/test_search_fees_classify.py`): 西药标签→药品类 / 造影按标签→检查类 / 模糊标签 (治疗) 回退名称 / 缺 `medins_chrgitm_type` 列输出与旧逐字一致

## 4. gate exam keyword 读显式字段

- [x] 4.1 `verdict_gate.extract_exam_keywords` 优先 `rule.exam_keywords`, 缺省回退现有链
- [x] 4.2 单测: 显式字段优先 / 未填回退旧行为

## 5. build_rule_mapping 支持 trigger_codes

- [x] 5.1 `scan_javert_yamls` 输出 `trigger_codes`; 删 applicable_* 扫描段; 加 `len≥2` 校验 (过短报错)
- [x] 5.2 重建 index: `uv run python scripts/build_rule_mapping.py` → verified: `javert_rules_index.json` 156 条全含 trigger_codes、无 applicable_*

## 6. 验证 (机制)

- [x] 6.1 `uv run pytest tests/ -v` 全绿: **616 passed + 1 skipped** (含本 change +54)
- [x] 6.2 J66252 真数据 route() 冒烟: 156 扫描→18 P0 final, code_set 351 token (含国标 C 码) 无报错. **0 漏检 by-construction**: applicable_* 本是 no-op (0 规则声明 + adapter 恒 None), trigger_codes 现全空 → code_hit 恒假 → final_rules 与改前逐字相同 (szx off/on 待补码后随 7.2 一并对照)

## 7. P0 补码 (长尾, 不阻塞发布)

- [x] 7.1 P0 **32 条** yaml 补 `trigger_codes` (数据驱动: keyword→shi_fee 命中项→国标 S/X 码 grow-until-exclusive 安全前缀 + 人审去 off-target: 支架→非冠脉/加收→免疫组化/电解质→输液 等剔除) → `rule_writer._FIELD_ORDER` 加 trigger_codes/exam_keywords → 重建 index (32 条带码, len≥2 校验过). **零回归证实**: J66252 P0 final 仍 18, 仅靠编码召回=无 (本院名称皆命中, 码是换院冗余保险); **可移植性证实**: J66252 麻醉项改异名后 R218/R212 keyword-miss 但 code-hit 仍召回
- [~] 7.2 classify 门控 dry-run **通过** (2 患者本地 A/B, new vs old classify, 不落库): 确定性迁移 41.7% 方向正确 (58% 垃圾其他类 + labs 误判修复); LLM A/B **0 假阳性回归** — J66252 0 diff, J90508 3 diff = R020 FAILED→CLEAN + R130 瞬时噪声(同代码×3 稳定 CLEAN) + R225 I→V **真阳性** (康复评定¥45 无神经/运动指征无康复文书, 证据充分). ⏳ 剩: szx (需 hub data dir) + 工作台费用 tab 视觉抽查 (restart 后)
- [~] 7.3 62 部署: **已staged+验证** — mechanism src 已在 62 (另一分支今日 10:51 push: router code-hit / classify label-first / rule.py trigger_codes / types Case-A 移除); 本轮补齐 32 补码 yamls + build_rule_mapping.py + rule_writer.py, 62 就地重建 index = **32 trigger_codes 活**; clobber 校验过 (62 yamls md5==本地 base). ⏳ 剩: `sudo systemctl restart javert-web` (需用户 sudo 密码, 我无passwordless)
