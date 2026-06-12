## 1. Rule schema: 放宽 rule_id 支持 RD 命名段

- [x] 1.1 改 `src/javert/audit/rule.py`: `rule_id` 的 pattern 从 `^R\d{3}$` 改为 `^(R\d{3}|RD\d{2,3})$`
- [x] 1.2 grep 全仓硬编码 `R\\d{3}` / `^R\d` 假设 (router / web / scripts / 报告生成器), 逐一确认 RD 段不被误过滤; 发现的硬编码点登记到本任务备注
  - **发现并修复**: `src/javert/audit/result.py:36` `AuditResult.rule_id` 同样硬编码 `^R\d{3}$` — 不放宽则 RD 规则的审计结果会校验失败, 已同步改为 `^(R\d{3}|RD\d{2,3})$`.
  - **确认无需改 (filter 正确排除 RD)**: `scripts/sync_priority_csv.py:61` `re.match(r"^R\d{3}$", rid)` 是 0325 CSV ingest 的过滤器, RD 规则不在 0325 序号内, 被它跳过正是预期 (设计 D6: 药品规则由独立脚本建, 不走 CSV).
  - **确认 glob 天然兼容**: `rule_loader.py:46` / `build_rule_mapping.py:70` / `build_clerk_report.py:54` 的 `glob("R*.yaml")` 自动收 `RD*.yaml` (RD 以 R 开头), 无需改.
- [x] 1.3 扩 `tests/test_rule.py`: 加载 `RD01` 成功 / 加载 `R007` 仍成功 (backwards compat) / `RD` `RD1234` `RDx` 非法值拒绝 三个场景
- [x] 1.4 `uv run pytest tests/test_rule.py -v` 全绿 (25 passed)

## 2. KB 归一化 build 脚本

- [x] 2.1 新 `scripts/build_drug_kb.py`: 读 `data/药品类规则/` 下 4 份 xlsx (**改用"扫到含『药品通用名』的行作表头"**, header=2 实际落不到真表头 — 真表头在第 4 行, 前 3 行是空+标题+空; 偏移鲁棒且确定)
- [x] 2.2 按 4 文件 → 4 个 `rule_type` (限适应症=第二部分-8 / 超说明书=第二部分-70 / 限二线=第二部分-5 / 禁忌症=第二部分-71) 提取 `{通用名, 检出逻辑, 逻辑依据}`
- [x] 2.3 按 `通用名` 聚合: value 为 `[{rule_type, detect_logic, basis}, ...]` list (一药跨多类型合并到一个 key) — 艾普拉唑肠溶片验证 [限二线+限适应症] 合并到单 key
- [x] 2.4 写 `configs/drug_audit_kb.json` (含 version 字段, `ensure_ascii=False`, `sort_keys=True` 保证确定性)
- [x] 2.5 脚本同时打印「命中频次表」: KB 通用名 ∩ `data/shi_fee.csv` 西药/中药/草药 fee 名 (stem 子串), 按命中患者数降序 → `output/drug_kb_hits.csv`
- [x] 2.6 跑 `uv run python scripts/build_drug_kb.py`: **928 通用名 ✓**, 4 个 rule_type 齐 (限适应症 713/超说明书 142/限二线 110/禁忌症 61), **211 种命中** (略多于设计估的 157, 因 stem 跨剂型匹配略宽 — 是 D3 预期行为) 落盘
- [x] 2.7 二次运行确认 `drug_audit_kb.json` **byte-identical ✓**

## 3. drug_audit_lookup 工具

- [x] 3.1 新 `src/javert/tools/drug_audit_lookup.py`: 实现 stem 匹配 (剥 fee 名 `(基)(集)(国谈)(集）` 前缀 + 剥 KB 通用名剂型前后缀, stem 长度 ≥2 子串判定); 纯函数 `fee_clean/kb_stem/stem_match` 供 build_drug_kb 复用
- [x] 3.2 bulk 模式 `(patient_id, rule_type?)`: `bah str.contains` 复合键; 过滤 `{西药,中药,草药}`; ∩ KB → `[{原始fee名(list), 通用名, rule_type, basis}]`; **额外带 shi_zd 病案首页诊断** (设计 D7, 因无 shi_zd 工具暴露给 LLM); 无命中显式空结果不抛错
- [x] 3.3 single 模式 `(drug_name)`: 返回该药全部 rule_type 条目 (精确名 + stem 宽松回退) / 显式 not-found (无联网回退)
- [x] 3.4 `format_for_agent`: 命中药 + 原始 fee 名 (必带, 供复方/同名复核) + 限定原文 + 病案首页诊断 + on-label 提醒
- [x] 3.5 注册进 `registry.py` (bind loader+KB+zd_path); **`drug_indication` 52 药未改动验证**: 曲妥珠单抗仍返回 Tier1 + ICD 候选; 两工具并存
- [x] 3.6 新 `tests/test_drug_audit_lookup.py`: stem 命中 (`(集)(基)阿卡波糖片(拜唐苹)`←`阿卡波糖片` + 跨剂型) / rule_type 过滤 / 无命中空结果 / single hit+miss / 复合键 / 诊断源 / executor 模式判定
- [x] 3.7 `uv run pytest tests/test_drug_audit_lookup.py -v` 全绿 (12 passed)
  - **实测发现 (写入 M8 prompt)**: stem 跨剂型匹配会把同名异药混入 (KB `布地奈德肠溶胶囊`[限IgA肾病] 命中 fee `吸入用布地奈德混悬液`). 工具按 D3 回传原始 fee 名, M8 master_prompt 加「同名异药/剂型复核」闸: 剂型/给药途径与依据明显不符 → INCONCLUSIVE 而非 V.

## 4. M8 模板

- [x] 4.1 新 `configs/templates/M8.yaml`「药品适应症/限定审计」: master_prompt 复用 M2 骨架 + `drug_rule_type` enum 字段 ∈ {限适应症,超说明书,限二线,禁忌症}
- [x] 4.2 jinja 4 分支: 限适应症/超说明书 (诊断∉依据→V) / 禁忌症 (诊断∈禁忌→V 反向) / 限二线 (诊断∈适应症 但 `search_notes` 无一线失败证据→V)
- [x] 4.3 master_prompt 显式写 **on-label 误报闸** + 诊断源优先 `shi_zd` 病案首页 (经 drug_audit_lookup 带出), `note_diagnosis` 兜底 + **同名异药/剂型复核闸** (实测发现)
- [x] 4.4 字段声明 (6 字段: drug_rule_type/target_desc/drug_focus/special_notes/pilot_caveat/trigger_kw) + tools_template 派生 suggested_tools (`drug_audit_lookup`/`note_diagnosis`/限二线加`search_notes`)
- [x] 4.5 `uv run javert template validate M8` 报 `ready` ✓; 人工核 4 模式 (禁忌症反向 / 限二线 search_notes / 限适应症&超说明书 ∉依据→V) 通过
- [x] 4.6 新 `tests/test_m8_template.py`: validate ready / 渲染 `禁忌症` 出反向逻辑 / 渲染 `限二线` 含 search_notes 步骤 (7 passed)
  - **附带 rule-registry 修改**: `Rule` 模型加 optional `drug_rule_type` 字段 (默认 None, 不影响既有 111 规则) + `rule_writer._FIELD_ORDER` 同步, 让"yaml 声明 drug_rule_type"持久且 model_dump-safe (priority/derived_from_template 字段先例). test_rule.py 加 2 个 round-trip 测试.

## 5. 类型级 4 条规则 (全覆盖 928 药)

- [x] 5.1 `R007` (限适应症): `drafting→ready`, `derived_from_template: M8`, `drug_rule_type: 限适应症` + M8 prompt_addon (bulk 全覆盖); 0325 question/example 原文保留; notes 交叉引用 RD01 超说明书姊妹规则 (经 `scripts/init_drug_rules.py`)
- [x] 5.2 `RD01` (超说明书): M8 类型级, `derived_from_template: M8`, drug_rule_type 超说明书, ready
- [x] 5.3 `RD02` (限二线): M8 类型级, ready (notes 标注一线史常缺 → I 偏多)
- [x] 5.4 `RD03` (禁忌症): M8 类型级, ready; `violation_type: 用药安全/禁忌` (与 M1-M7 骗保类型可区分)
- [x] 5.5 确认 R007/RD01/RD02/RD03 全 ready + `derived_from_template: M8` + drug_rule_type 正确 (load_all 校验 non-conforming=[])
- [x] 5.6 dry-run R007 (限适应症 type-level) on J90508 看 trace: drug_audit_lookup bulk 13 命中 → note_diagnosis 39 诊断 → lab/notes 逐药核对 (白蛋白<30 / 曲霉菌↔伏立康唑) → verdict 链路通. **暴露 2 个问题, 已修** (见下方实测发现).
  - **实测发现 1 (runner bug, 已修)**: deadline turn 吐**裸 JSON** (无 ```围栏), 旧 `_parse_verdict_block` 只认 fenced → 合法 verdict (conf 0.55/0.90) 被丢成 conf=0.00. 修 `runner.py` 加裸 JSON 回退 + test_runner 加回归测试. 这是 pre-existing bug, 药品规则因常触顶而频繁撞上.
  - **实测发现 2 (M8 prompt, 已调)**: 类型级覆盖多药时 evidence 数组过长致 JSON 截断; on-label 判定过严 (甲状腺癌未逐字写"甲减"→ 误判 INCONCLUSIVE). 加「类型级收敛」+「evidence 聚焦 1-3 条」+「合理临床外延算落在依据内」.

## 6. 精选规则 (数据驱动 20-40 条)

- [x] 6.1 据命中频次表按 `命中患者数 × 危险度` 选 **29 高频高危药** (RD10-RD37): 限适应症 14 (人血白蛋白/聚桂醇/果糖/ω-3鱼油/万古霉素/复方氨基酸20AA/恩替卡韦/福沙匹坦/右酮洛芬/重组人血小板生成素/艾普拉唑钠/泽布替尼/拓培非格司亭/莫西沙星) + 超说明书 5 (甲状腺片/尼卡地平/二甲双胍/氨氯地平/缬沙坦) + 禁忌症 8 (奥美拉唑/葡萄糖酸钙/碳酸钙/对乙酰氨基酚/乙酰半胱氨酸/甲氧氯普胺/阿司匹林/瑞舒伐他汀) + 限二线 1 (艾普拉唑肠溶片). 选单含 on-label 闸用例 (甲状腺片/钙) + 同名异药闸用例 (果糖)
- [x] 6.2 每条精选规则: `RD` 段 id, `derived_from_template: M8`, `drug_rule_type` 按 KB 类型, `trigger_keywords` = [通用名(+stem)] (router 弹性精准触发; stem 保证 fee 名子串命中)
- [x] 6.3 `scripts/init_drug_rules.py` 批量 init + M8 渲染; **每条校验 (a) 在 `drug_audit_kb.json` (b) 在命中频次表** — 不满足直接 SystemExit (无纯休眠药)
- [x] 6.4 确认精选规则全 ready; `RD*` 总数 32 (3 类型级 + 29 精选), 加 R007 共 **33 条 M8 药品规则**
- [x] 6.5 dry-run RD24 甲状腺片 (超说明书 curated) on J66252 [on-label 闸]: 修复后 → **CLEAN conf=0.90** ✓ (reasoning: 甲状腺恶性肿瘤术后→甲减替代→合理临床外延→对症, 不构成超说明书违规). RD10/R007 并入组 8 批量跑.

## 7. Router 接入

- [x] 7.1 类型级 4 条 trigger_keywords=[] → `_yaml_keyword_hit` 空即命中 → router always-on; smoke 验证 J90508 + J66252 均保留全 4 条 (type-level all-on? True)
- [x] 7.2 精选规则 `trigger_keywords`=[通用名(+stem)] 弹性命中: J90508 (drug-rich) 保留 11 条精选 (人血白蛋白/ω-3/万古/复方氨基酸/钙/乙酰半胱氨酸/艾普拉唑...); J66252 (thyroid) 保留 RD24 甲状腺片 + RD30 钙 (on-label 闸队列). 注: 注射用盐酸X 类通用名 60% prefix 偶有 harmless over-keep (router 多留 → LLM 跑出 CLEAN, false-negative 安全)
- [x] 7.3 `uv run python scripts/build_rule_mapping.py` 重建 `javert_rules_index.json` (156 yaml, M8: 32) — router 读 index 非 yaml, 必跑
- [x] 7.4 router smoke (等价 `/tmp/router_smoke.py`): 综合科 J90508 final 含药品规则 (4 type-level + 11 curated), 甲状腺 J66252 亦含 (4 type-level + RD24/RD30) — always-on 验证通过

## 8. 两批对照验证 + on-label 闸 (验收硬指标)

- [x] 8.1 综合科批 J90508/J94056 (113/137 药) 跑 32 M8 规则 (`--rules` 聚焦本 change 交付物, 非全 143 套件): **真违规信号 14 条** (人血白蛋白臂经核为 on-label CLEAN, 但 重组人血小板生成素/布地奈德/复方氨基酸/乌司他丁/尼卡地平/艾普拉唑 出 V — LLM 查 lab 值+生命体征+限定支付字面)
- [x] 8.2 甲状腺批 J66252/K03341/J40485 同跑: **on-label 闸验收通过** —— 甲状腺片 RD24 全 C 0.90 + 钙 RD30/31 全 C 0.95 + 类型级超说明书/禁忌全 CLEAN; J66252/J40485 **0 V**, K03341 唯一 1 V 是真 off-indication (艾地骨化醇 限绝经后骨质疏松). 闸按诊断判别非队列一刀切, 无需回调措辞
- [x] 8.3 抽样 ground-truth: 8 条综合科 V 核验 **6 硬 V + 2 边缘** (方向对, 严判); 限二线 RD02/RD37 全 V 单列 (单病历缺一线史→偏 V, 运营需线下复核); **剂型闸生效** (J94056 RD29 注射 vs 口服 → INCONCLUSIVE)
- [x] 8.4 写实测报告 `docs/sample_drug_audit.md`: 两批裁决分布 + on-label 闸表 + 抽样核验表 + always-on 耗时增量 + 2 个修复 narrative + 已知残留 (2/160 deadline JSON 截断)
- [x] 8.5 `uv run pytest tests/ -v` 全量全绿 (**294 passed, 1 skipped**; test_tools 六工具→七工具断言已更新, 含新增 drug_audit_lookup / m8_template / runner 裸JSON 回归 / rule RD 命名段 + drug_rule_type)

## 9. 文档同步 (CLAUDE.md 文档协议)

- [x] 9.1 更 `Javert/CLAUDE.md`: 架构图加 drug-audit 块 (tools 加 drug_audit_lookup / 数据加 drug_audit_kb.json / 模板 M8) + 关键文件表 5 行 + 常用命令 5 行 + 当前阶段标记 v0.8 完整条目
- [x] 9.2 更 `docs/做不了163规则可行性分析.md`: R007 红区 E 标 ✅ 已解锁; drug_indication 局限段加 v0.8 已由 drug_audit_lookup + 928 药 KB 补足
- [x] 9.3 更 `docs/template_design_guide.md` (标题 M1-M8 + 状态表加 M8 + M8 schema 特殊点段) + `docs/rule_design_guide.md` (rule_id RD 命名段 + drug_rule_type 字段)
- [x] 9.4 更 `README.md`: 七大模板→八大模板 + M8 行 (33 条) + 合计 144 + sample_drug_audit.md 入 docs 表
- [ ] 9.5 git commit 整个 change — **留给操作者** (status/yaml/代码变更一并提交)
