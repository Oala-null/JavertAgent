## Context

Javert 的违规审计回路一直绕开药品: `drug_indication` 工具只有 52 种肿瘤靶向药, M2 的 `drug_check` 是反向给检查洗白, R007(超说明书 + 超医保限定)因缺药品目录搁置在 `drafting / P2`。现在 `data/药品类规则/` 落了 4 份知识库(限适应症 715 / 超说明书 144 / 限二线 112 / 禁忌症 63, ≈928 去重通用名), 每条带 `通用名 + 检出逻辑 + 限定/说明书原文`。

数据侧已探明三个约束, 直接决定设计:

1. **覆盖**: 928 种里 157 种真出现在 3243 个有西药 fee 的患者中(17%)。违规面真实但稀疏 —— 精选规则必须**数据驱动**, 不能凭空写。
2. **fee 名干净**: `(集)(基)阿卡波糖片(拜唐苹)` 这类名字, 通用名 `阿卡波糖片` 是其子串, 确定性匹配可行。
3. **盲命中 ≠ 违规**: `甲状腺片` 命中"超说明书"1765 人、钙片命中"禁忌"579 人, 但对甲状腺 / 低钙患者是对症合规。**LLM 的诊断-依据语义比对是整套设计的命门**, 不是工具的字典命中。

代码侧两个硬约束(已确认):
- `Rule.rule_id` 的 pydantic pattern 为 `^R\d{3}$`(严格 R + 3 位数字), `RD01` 会校验失败。
- 加载器 `load_all` 用 `glob("R*.yaml")` 发现规则, 按 `rule_id` 字符串去重。

## Goals / Non-Goals

**Goals:**

- 用 4 份 KB 撑起药品违规审计, 覆盖限适应症 / 超说明书 / 限二线 / 禁忌 4 类。
- 类型级规则**全覆盖** 928 药(工具 bulk 驱动)+ 精选 20-40 条盯高频高危药(router 精准触发)。
- on-label 误报闸: 甲状腺批的甲状腺片 / 钙必须几乎全 CLEAN, 作为验收硬指标。
- 零外部依赖变化、零 `audit_runs` schema 变更, 纯增量。

**Non-Goals:**

- 不做 R004 / R204(药品申请支付数量 > 实际采购 / 使用)—— 需采购 / 用量台账, 本期没有。
- 不动现有 52 药 `drug_indication`(M2 反向证据仍依赖它)。
- 不接上游"第二部分"知识库其余编号(到 71+), 本期只 -5/-8/-70/-71 这 4 份。
- 不做药品**单价 / 超量 / 串换厂家**(需价格目录 / 标准剂量 / 采购数据)。

## Decisions

### D1 — 新工具 `drug_audit_lookup`, 不复用 `drug_indication`

`drug_indication` 返回"适应症 + ICD 候选"且只 52 药, 被 M2 当反向证据用; 改它会破坏 M2。新工具职责不同: 拿 4 份监管 KB, 按患者药品做 bulk 命中。两者并存, 各管各的。

### D2 — KB 归一化为单 `configs/drug_audit_kb.json`, 按通用名合并跨类型

`scripts/build_drug_kb.py` 读 4 份 xlsx(跳 2 行表头), 产出按通用名聚合的 json:

```json
{
  "version": "1.0",
  "drugs": {
    "艾普拉唑肠溶片": [
      {"rule_type": "限二线", "detect_logic": "...无一线失败证据", "basis": "限...二线治疗"},
      {"rule_type": "限适应症", "detect_logic": "...不符合限定支付适应症", "basis": "限..."}
    ]
  }
}
```

一个通用名可跨多个 rule_type(如艾普拉唑既限适应症又限二线), 故 value 是 list。**选 json 不选直读 xlsx**: 工具运行时不该依赖 Excel 解析 + 避免每次跑都重读 4 文件; 重建时跑一次 build 脚本即可(同 M4 诊疗目录的做法)。

### D3 — 确定性匹配在工具侧, 语义判断在 LLM 侧

工具做"患者用药 ∩ KB"的 bulk 命中(确定性), 把命中药 + 原 fee 名 + 限定/说明书原文一并返回; LLM 只对这几条(通常每人几条到十几条)判"诊断是否落在依据范围内"。

匹配用**通用名 stem 子串**: 先剥 fee 名的 `(基)(集)(国谈)(集）` 等前缀标记, KB 通用名剥剂型后缀(`片/胶囊/注射液/注射用/…`)得 stem, 判 stem 是否为 fee 名子串。stem 长度 < 2 跳过(防"乳""散"误命中)。工具**回传原始 fee 名**, 让 LLM 复核复方 / 同名歧义。

**选 stem 子串不选精确名匹配**: KB `阿卡波糖片` vs fee `(集)(基)阿卡波糖片(拜唐苹)`, 也兼容剂型差异(KB `奥美拉唑肠溶片` 命中 fee `奥美拉唑肠溶胶囊`)—— 代价是复方制剂可能误命中, 用回传原名 + LLM 复核兜底。

### D4 — 单 M8 模板带 `drug_rule_type` 开关 (4 模式), 不拆 4 个模板

4 类共用骨架(`bulk 命中 → note_diagnosis → 比对 → 裁决`), 差异靠 jinja 分支:

| drug_rule_type | 比对逻辑 | 额外步骤 |
|---|---|---|
| 限适应症 | 诊断 **∉** 限定适应症 → V | — |
| 超说明书 | 诊断 **∉** 说明书适应症 → V | — |
| 禁忌症 | 诊断 **∈** 说明书禁忌 → V(**反向**) | — |
| 限二线 | 诊断 **∈** 适应症 但文书无一线失败证据 → V | `search_notes` 查一线用药史 |

**选单模板不选拆 4 个**: 复用 M2 的 prompt-fit / validate / render 全套基建, 维护一处; 真膨胀了再拆。M8 的 `master_prompt` 必须显式写 on-label 闸: 「命中 KB 仅代表该药在监管知识库, **不代表违规**; 必须确认患者诊断是否落在依据范围内, 落在即 CLEAN」。

### D5 — 规则粒度: 类型级 4 + 精选 20-40, 数据驱动选

- **类型级 4 条**(工具 bulk 驱动, 覆盖 928 药): `限适应症 / 超说明书 / 限二线 / 禁忌` 各一条。限适应症复用 **R007**(0325 锚点, 其 question 正好覆盖限定支付), 其余 3 条用 RD 段(见 D6)。R007 聚焦医保限定支付臂, 在 notes 里交叉引用超说明书姊妹规则。
- **精选 20-40 条**: 从 157 种真命中药里按 `命中患者数 × 危险度` 选(人血白蛋白 / 万古霉素 / ω-3鱼油 / 果糖 / 聚桂醇 / PPI 类 / PD-1 靶向 …), `derived_from_template: M8`, trigger_keywords = 该药通用名, router 精准触发。具体清单在 tasks 阶段据 build_drug_kb 输出的命中频次表定。

### D6 — 放宽 `rule_id` pattern 到 `^(R\d{3}|RD\d{2,3})$`, 药品规则用 RD 段

类型级 3 新规则 + 精选 20-40 在 0325 清单**无对应序号**, 不能套 `R\d{3}`。两条路:

- **(选) 放宽 pattern + RD 命名段**: `RD01-RD03` 类型级, `RD10-RD49` 精选。一行 pattern 改动, 药品规则 `RD*` 一眼可辨、可 grep、可单独批跑。属 `rule-registry` 的 MODIFIED(同 priority 字段先例)。
- (弃) 占用 R400+ 空号: 零代码改动, 但 `R401` 语义不透明, 且与"R 号来自 0325 序号"的隐含约定冲突。

副作用排查: `glob("R*.yaml")` 天然收 `RD*.yaml`; rule_id 是字符串 key 不受影响; CSV ingest (`sync_priority_csv.py`) 只处理 0325 的 Rxxx, 药品规则由独立 init 脚本建, 不冲突。tasks 里加一条 grep 全仓硬编码 `^R\d{3}$` 假设的核查。

### D7 — 诊断源 `shi_zd` 优先, `note_diagnosis` 兜底

甲状腺片陷阱说明诊断准确度是命脉。`shi_zd` 是病案首页 ground truth(主诊 maindiag_flag=1 + 全诊断), 比从文书正则派生的 `note_diagnosis` 准。M8 引导优先用病案首页诊断; 缺失时回退 `note_diagnosis`。

### D8 — router 接入: 类型级 always-on, 精选按药名

类型级规则触发条件 = "患者有西药 fee"(≈ 人人), router 砍不掉, 每患者 +最多 4 次 LLM call, 纳入耗时预期。精选规则 trigger_keywords = 该药通用名, 走 router 弹性 keyword 精准触发(只在该药出现时跑)。

## Risks / Trade-offs

- **通用名误匹配(复方 / 同名)** → stem 长度阈值(≥2)+ 工具回传原始 fee 名让 LLM 复核 + 精选规则用更长通用名。
- **限二线"一线失败证据"文书未必有结构化记录** → INCONCLUSIVE 会偏多, 符合「N/A 优于乱跑」原则, 可接受; 验证时单列其 I 率。
- **诊断抽取不准** → `shi_zd` 优先 + 文书兜底; 验证抽样人工核。
- **类型级 always-on 成本** → 50 患者 ×4 = +200 LLM call; 接受, 写进耗时报告。
- **禁忌症偏临床用药安全, 非骗保** → `violation_type` 单独标(用药禁忌 / 用药安全), 汇报口径与医保违规分开; 仍纳入 V 但归类清晰。
- **KB 仅 157/928 在本数据出现** → 精选数据驱动选已命中药; 其余 771 药休眠, 外部医院数据接入时自动激活(无需改规则)。
- **放宽 ID pattern 的连带假设** → tasks 强制 grep `R\d{3}` 全仓硬编码点(router / web / 报告脚本), 逐一确认。

## Migration Plan

纯增量, 无破坏性变更:

1. 跑 `build_drug_kb.py` 生成 `configs/drug_audit_kb.json`。
2. 加 `drug_audit_lookup` 工具 + registry 注册; 改 `rule.py` 放宽 pattern。
3. 装 M8 模板 + 建 R007/RD 规则 + 精选规则。
4. dry-run 两批 → 验 on-label 闸 → `mark ready` → 批跑。

**Rollback**: 删 `RD*.yaml` + 还原 `R007.yaml` 到 drafting + 还原 `rule.py` pattern + 删 `M8.yaml` / `drug_audit_kb.json` / `drug_audit_lookup.py` + registry 注销。`audit_runs` 无 schema 变更, 历史 run 不受影响。

## Open Questions

- 精选 20-40 条具体选哪些药 —— 待 `build_drug_kb.py` 出命中频次表后, 在 tasks 里据 `命中患者数 × 危险度` 定档。
- 限二线规则是否值得为"查一线失败证据"在 M8 里加深 `search_notes` 子流程 —— 先按现有 keyword 搜跑一轮看 I 率, 再决定。
- 禁忌症最终纳入 V 还是仅 advisory(倾向纳入但 `violation_type` 单独标)—— 看首轮裁决质量定。
