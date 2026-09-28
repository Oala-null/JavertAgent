# make-rules-code-portable

## Why

2026-07 扫描主线三: 系统对「本院费用字面名」的依赖是多院部署的最大矛盾——(1) router 预筛全压在 trigger_keywords 子串匹配上, keyword 是「PET-CT / 口腔颌面软组织清创术」类本院字面名, 换医院命名不同即**静默漏检** (预筛假阴性不进 LLM, 永远发现不了); (2) `applicable_*` 五段硬过滤是整段死代码 (156 条规则 0 填写 + adapter 写死 gender/age=None 守卫恒假); (3) `search_fees._classify` 费用分类是关键词启发式 (「造影」dict 顺序决定归类、未命中药落「其他类」药占比失真)——而 hub 契约与 szx/data-hub 数据里**本来就有**国标 MXFYLB 类别码与医保项目编码, 数据里有官方答案, 工具在用关键词猜. szx2.0 4680 患者已走 hub 路径, 编码维度已实际可用.

## What Changes

- **规则触发加编码维度**: rule yaml 新增 `trigger_codes` (医保目录编码前缀 / MXFYLB 类别码列表); router haystack 加入患者 fee 行的 `med_list_codg` / `medins_list_codg` / 类别码列; 命中语义 = **编码命中 或 keyword 命中** (编码是加法不是替换, 老数据无码时行为不变、零漏检回归)
- **费用分类切官方类别码**: `search_fees._classify` 优先读行内 `medins_chrgitm_type` / MXFYLB 码 (hub/szx 数据已带), 名称关键词降为兜底——「造影」归类不再靠 dict 顺序, 药品不再漏进「其他类」
- **applicable_\* 清场**: 删除 router 五段死过滤 + RouterDecision 废弃 Case-A 字段 (`overlapping_kept` 等) + adapter 恒 None 字段——50 行不可达复杂度与「大家以为存在的闸」一起消掉; 顺带评估 Phase-2 Track A (~100 行 + 5.1MB violation_dict, route() 从不调用) 是否随本 change 移出主路径
- **gate exam keyword 解耦**: `verdict_gate.extract_exam_keywords` 现靠 grep prompt_addon 文本 (「检索关键词」行 + 引号正则), 模板措辞一变即静默回退; 改读 rule yaml 显式字段 (与 trigger_codes 同批加)
- **补码工作流**: 先做机制 + P0 31 条补 `trigger_codes` (从 drug_audit_kb 编码与 hub 费用实数据反查), 其余分批; `build_rule_mapping.py` 重建 index 时校验 codes 字段格式
- **验证**: (a) J66252 + szx 患者 router off/on 对比 0 漏检 (复用 `compare_router_off_on.py`); (b) 换名模拟——把测试 fee 名替换为同义异名, 断言编码路径仍命中而 keyword 路径 miss (这正是换院场景)

## Capabilities

### New Capabilities
- `code-based-triggering`: 规则触发与费用分类的「编码优先、名称兜底」双维语义

### Modified Capabilities
- `routing`: haystack 加编码列 + applicable_* 死闸移除 (single-gate 语义不变)
- `data-access`: fee 行类别归类优先官方类别码

## Impact

- 代码: `src/javert/audit/rule.py` (+`trigger_codes`), `src/javert/routing/{router,types,adapter}.py` (加编码命中 + 删死代码), `src/javert/tools/search_fees.py` (`_classify`), `src/javert/audit/verdict_gate.py` (exam keyword 来源), `scripts/build_rule_mapping.py`, P0 31 条 yaml 补码
- 行为面: router 只增不减 (编码是 OR 加法), 分类变化影响 search_fees category 模式输出与工作台费用类别聚合——需 5 患者 dry-run 对照 + 工作台费用 tab 抽查
- 与 pilot-deterministic-precheck 正交但共享「规则结构化字段」方向, precheck 先行时 M1 的 A/B 项目集直接按「编码+名称」双维声明, 一次到位
- 长尾: 143 条全量补码是持续工作, 本 change 只承诺机制 + P0 档; 补码进度不阻塞发布
