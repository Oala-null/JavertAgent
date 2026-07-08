# make-rules-code-portable — design

## Context

扫描核验的四个事实:

1. `router.py:283-285` `_build_haystack` 只拼 fee 名 + 诊断文本; 预筛命中全压在 `trigger_keywords` 子串匹配上. keyword 是「PET-CT / 口腔颌面软组织清创术」类**本院字面名**, 换医院命名不同即静默漏检 (预筛假阴性不进 LLM, 永远发现不了).
2. `router.py:235-281` `_yaml_applicable_to_patient` 五段硬过滤 (visit_type/gender/age/diag_codes/departments) 是**整段死代码**: `grep applicable_ configs/rules/` = **0 条 yaml 填写**, 且 `adapter.py:127-135` 把 gender/age 写死 None → `if meta.applicable_gender and record.gender` 守卫恒假. 50 行不可达复杂度 + 「大家以为存在的闸」.
3. `search_fees.py:61-65` `_classify` 是关键词启发式: `_CATEGORY_KEYWORDS` dict 顺序决定归类 (「造影」同时在 手术类/检查类, 手术类先命中), 未命中药落「其他类」→ 药占比失真. 而数据里 `medins_chrgitm_type` 列**本来就带官方类别** (手术/西药/中药/化验/检查/CT/拍片).
4. `verdict_gate.py:133-147` `extract_exam_keywords` 靠 grep `prompt_addon` 文本 (「检索关键词」行 + 引号正则), M2 模板措辞一变即静默回退 trigger_keywords.

**关键数据坑 (已核验, 决定设计)**: `data/shi_fee.csv` 里 `med_chrgitm_type` **数字码是本院自定义、非国标** (11→手术 *和* 饮食; 3→其他/手术/麻醉), 不可用于分类. 唯一可靠的类别信号是 `medins_chrgitm_type` **中文标签** (data-hub 路径已把 MXFYLB 2 位国标码回填成同款中文, 故此字段跨院可移植).

约束: router「只增不减」——编码命中是 OR 加法, 老数据无码时行为逐字不变、零漏检回归. 分类改动会漂移 `search_fees` category 输出与工作台费用 tab 聚合, 需 5 患者 dry-run + 工作台抽查.

## Goals / Non-Goals

**Goals:**
- 规则触发从「本院字面名子串」升级为「编码命中 OR 名称命中」双维, 换院命名不同仍能命中 (可移植性)
- 费用分类从「关键词 dict 顺序」升级为「官方中文类别标签优先, 名称兜底」, 消「造影」歧义与药占比失真
- 删掉 `applicable_*` 五段死过滤 + Case-A 废弃字段, 消 50 行不可达复杂度
- gate exam keyword 从 grep prompt 文本改读 rule yaml 显式字段
- P0 31 条补 `trigger_codes` 机制落地 (补码本身是长尾, 不阻塞发布)

**Non-Goals:**
- Phase-2 Track A (`java_engine_lookup` + 5.1MB `violation_dict`) 移除 — 评估结论见 D5, 本 change **不动** (已 lazy-load、route() 从不调、零运行时成本, 移除是独立 change)
- 143 条全量补码 — 本 change 只做机制 + P0 档
- `med_chrgitm_type` 国标数字码支持 — 本院数据里是脏码 (见 Context), 名称兜底已够, 未来 hub 全量国标码时再议
- 分类桶的重新设计 (仍是 手术/药品/耗材/检查/其他 五类)

## Decisions

### D1. `trigger_codes` 是 OR 加法, 不替换 keyword

`rule.py` Rule 加 `trigger_codes: list[str]` (医保目录编码前缀 / 类别 token, 默认空). router `_build_code_set(record)` 收集患者每条 fee 的 `med_list_codg` (国标 C 码) + `medins_list_codg` (本院码) + `chrgitm_type` (类别标签) 成 token 集; `_yaml_code_hit(meta, code_set)` = 任一 `trigger_code` 是任一患者 token 的**前缀**即命中 (前缀天然含相等, 覆盖「C03 命中 C03xxx 检查费」与「西药 命中 西药」). 单条规则最终命中 = `keyword_hit OR code_hit`.

零回归保证: `trigger_codes` 空 → `code_hit` 恒 False → 命中语义 = 老 `keyword_hit`, 逐字不变.

短码防泛滥: `build_rule_mapping.py` 校验 `trigger_codes` 每项 `len ≥ 2` 且非纯空白, 太短 (< 2) 报错拒建 index (类比 keyword short_threshold).

### D2. 分类切官方中文标签, 名称兜底 (只在自信桶覆盖)

`search_fees._classify(name, chrgitm_label)`:
1. `chrgitm_label` (行内 `medins_chrgitm_type`) 映射到**自信桶**: 含「手术」→手术类; 含「西药/中药/中成药」→药品类; 含「材料/耗材」→耗材类; 含「CT/检查/化验/拍片/病理/影像/超声/检验」→检查类.
2. 标签缺失 / 落在模糊类 (治疗/床位/护理/其他/麻醉/输血/饮食/专护…) → **回退现有 `_CATEGORY_KEYWORDS` 名称启发式** (行为不变).

只在标签自信时覆盖 = 最小漂移: 「西药」标签的药一定进药品类 (修药占比失真), 「造影」按其真实标签 (检查/拍片) 归类不再靠 dict 顺序; 而「消融治疗」这类模糊标签仍走名称关键词命中手术类 (保留旧信号). 数字码 `med_chrgitm_type` **不参与** (本院脏码).

调用点: `fees["_category"] = fees.apply(lambda r: _classify(r["_name"], r.get("_chrgitm_label","")), axis=1)`, 新增 `_chrgitm_label` 列从 `medins_chrgitm_type` 抽 (列缺失则空串 → 纯名称兜底, 老 CSV 零变化).

### D3. `applicable_*` 五段死闸清场

删除 `router.py` `_yaml_applicable_to_patient` 方法 + route() 里的 applicable prune 步 + `_JavertMeta` 六个 applicable 字段; 删 `build_rule_mapping.py` 的 applicable_* 扫描段. stats dict 去掉 `passed_applicable`/`pruned_applicable` 两键. 0 条 yaml 用、adapter 恒 None → 纯删死代码, 无行为面影响. route() 从三步 prune 收敛为两步 (status/priority → hit).

### D4. Case-A 废弃字段清场

`RouterDecision` 删 `overlapping_kept` + `javert_only_kept` (single-gate 下 overlapping 概念已废, javert_only == final_rules 冗余). route() 不再构造二者; `audit_patient.py:405-406` 的日志行改为只报 `final` 计数. **保留** `java_triggered` / `TriggerEvidence` / `evidence_pointers` (属 Phase-2 Track A, 见 D5, 非 Case-A).

### D5. Phase-2 Track A 不随本 change 移除 (评估结论)

`java_engine_lookup` + `_deterministic_lookup` + 5.1MB `violation_dict.json` 已 lazy-load (`from_paths` 首调才读盘)、`route()` 从不调用、零运行时成本. 移除需连带动 `extract_router_data.py` / build 脚本 / 3 个 data 文件, 回归面独立且大. 按「surgical / 不删预存死代码除非被要求」——**本 change 不动**, 若未来确认 Java port 不做再单独立 change 清理.

### D6. gate exam keyword 读显式字段

`rule.py` Rule 加 `exam_keywords: list[str]` (默认空). `verdict_gate.extract_exam_keywords`: `rule.exam_keywords` 非空 → 直接用; 否则**保持现有** grep prompt_addon → trigger_keywords 回退链. 只填了显式字段的规则改变来源, 其余零回归.

## Risks / Trade-offs

- [分类漂移影响工作台费用 tab / category 输出] → D2 只在自信标签覆盖, 模糊类回退名称; 5 患者 dry-run 对照 category 模式输出 + 工作台费用 tab 抽查, 无预期外大迁移才推
- [trigger_codes 短码泛滥误召回] → build_rule_mapping 校验 len≥2 + 前缀语义 (非任意子串); 换名模拟测试断言编码路径精准命中
- [删 applicable_*/Case-A 字段破坏未知调用方] → 已全仓 grep: 仅 router/types/audit_patient/build_rule_mapping 四处引用, 无 test 依赖 (RuleRouter 本无单测); 一并改
- [P0 补码错填漏召回] → 补码从 `drug_audit_kb` 编码 + hub 真实 fee 编码反查, 非拍脑袋; router off/on 对比断言 0 漏检

## Migration Plan

1. Mac 改码: rule.py 加两字段 → router 加编码命中 + 删死闸 → types 删 Case-A 字段 → search_fees `_classify` → verdict_gate exam field → build_rule_mapping (加 trigger_codes 校验 + 删 applicable 扫描)
2. 新增单测 (RuleRouter 首套): 编码命中 / 名称命中 / OR 语义 / 无码零回归 / 换名模拟 (名换编码不变→编码路径命中、keyword 路径 miss); `_classify` 标签优先 + 缺列兜底; extract_exam_keywords 显式字段优先
3. `uv run pytest tests/ -v` 全绿
4. `build_rule_mapping.py` 重建 index → `compare_router_off_on.py` J66252 + szx off/on 断言 0 漏检
5. P0 31 条补 `trigger_codes` (分批, 从 drug_kb + hub fee 编码反查) → 重建 index
6. 5 患者 dry-run 对照 category 输出 + 工作台费用 tab 抽查 → 无 V 级意外 → 62 部署
7. 回滚: 各 decision 互相独立, 编码命中/分类/死代码清场可单独 revert

## Open Questions

- P0 补码的编码来源优先级 (drug_kb 国标码 vs hub 实数据 med_list_codg 前缀) — 补码阶段逐条定, 机制不阻塞
- 是否给 `trigger_codes` 加类别 token 专用字段 (vs 混在 codes 里前缀匹配) — 现阶段前缀匹配够用, YAGNI
