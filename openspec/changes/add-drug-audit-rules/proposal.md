## Why

Javert 现有 7 套模板 (M1-M7) 覆盖了重复收费 / 过度检查 / 串换 / 超标准 / 虚构 / 过度诊疗等违规类型, 但**药品几乎是盲区**: 唯一的 `drug_indication` 工具只有 52 种(且几乎全是肿瘤靶向药), M2 里的 `drug_check` 是**反向**用来给检查洗白, 真正的药品规则 R007(超说明书 + 超医保限定)因"没有药品目录"一直 `drafting / P2` 趴着。

现在 `data/药品类规则/` 落地了 4 份结构规整的知识库 —— 限适应症 715 / 超说明书 144 / 限二线 112 / 禁忌症 63(≈928 去重通用名), 每条带 `通用名 + 检出逻辑 + 限定/说明书原文`。数据侧已验证: 928 种里 **157 种真出现在**我们 3243 个有西药 fee 的患者中, 药品违规面真实存在。卡了三期的那块数据补上了, 该做药品类规则了。

**关键设计命门 —— 盲命中 ≠ 违规**: `甲状腺片` 在 1765 个患者命中"超说明书"、钙片在 579 个命中"禁忌", 但对甲状腺术后 / 低钙患者它们是**对症合规**的。公司既有 Java 引擎那套"字典命中即报"会在这里疯狂误报; Javert 的价值正是让 LLM 拿命中药 × 患者诊断做**语义比对, 有指征即判 CLEAN**。这条误报闸是整套设计成败的关键, 必须作为验收硬指标。

## What Changes

- 新数据资产 `configs/drug_audit_kb.json`: 由 `scripts/build_drug_kb.py` 把 4 份 xlsx 归一化(按通用名合并跨类型, 带 `rule_type` + `检出逻辑` + `逻辑依据原文`)
- 新工具 `drug_audit_lookup`(注册进 tools registry, **不动**现有 52 药 `drug_indication`, M2 反向证据还在用它):
  - **bulk** `(patient_id[, rule_type])` → 该患者用药 ∩ KB 的命中药 + 各自限定 / 说明书原文(确定性匹配: 去 `(基)(集)(国谈)` 前缀 + 剂型后缀做通用名 stem 子串匹配)
  - **single** `(drug_name)` → 单药 KB 事实
- 新模板 `configs/templates/M8.yaml`「药品适应症 / 限定审计」: 复用 M2 骨架 + `drug_rule_type` 开关驱动 4 种比对逻辑(限适应症 / 超说明书 = 诊断 ∉ 依据 → V; 禁忌 = 诊断 ∈ 禁忌 → V 反向; 限二线 = 诊断 ∈ 适应症 但文书无一线失败证据 → V)
- 新规则集(**两种粒度**):
  - **类型级 4 条**(工具 bulk 驱动, 全覆盖 928 药): 限适应症复用 / 挂既有 **R007**, 超说明书 / 限二线 / 禁忌用药品专用新 ID
  - **精选 ~20-40 条**(从 157 种真命中药里**数据驱动**选高频高危: 人血白蛋白 / 万古霉素 / ω-3鱼油 / 果糖 / 聚桂醇 / PPI 类 / PD-1 靶向 …), router 按药名 keyword 精准触发
- 诊断源优先 `shi_zd` 病案首页(ground truth 主诊 + 全诊断), `note_diagnosis` 兜底
- 两批对照验证: 综合科(H31010600042 系列, 出真违规信号)+ 甲状腺 pilot(on-label 误报回归)

## Capabilities

### New Capabilities

- `drug-audit`: 药品违规审计子系统 —— 药品知识库归一化(4 xlsx → `drug_audit_kb.json`)+ `drug_audit_lookup` 工具(bulk / single)+ M8 模板(4 模式)+ 类型级 4 条 + 精选 ~20-40 条规则 + 「命中药 × 患者诊断」语义裁决语义 + on-label 误报闸。

### Modified Capabilities

- `rule-registry`: 为药品规则引入专用 ID 命名段(0325 清单无对应行), 让 loader / `list` / router 一致识别; 接受 `derived_from_template: M8` 的派生规则。

## Impact

- **代码**:
  - 新 `src/javert/tools/drug_audit_lookup.py` + `src/javert/tools/registry.py` 注册
  - 新 `scripts/build_drug_kb.py`
  - 可能改 rule loader 的 ID 校验(若现为严格 `^R\d+$`)
  - 可能改 `src/javert/routing/`(药品规则触发: 有西药 fee 即触发; 精选规则按药名 keyword)
- **数据 / 配置**:
  - 新 `configs/drug_audit_kb.json`(由 4 xlsx 生成, ≈928 药)
  - 新 `configs/templates/M8.yaml`
  - 改 `configs/rules/R007.yaml`(`drafting → ready`, 装 M8 prompt + 类型级配置)
  - 新 ~23-43 条 `configs/rules/RDxx.yaml`(类型级 3 + 精选 20-40)
- **外部依赖**: 零变化 —— 沿用 sglang `192.168.31.62:30000` 与 csv 数据源; 不连 142 SQL Server, 不动 zadig_agent
- **测试产出**:
  - 新 `tests/test_drug_audit_lookup.py`(stem 匹配 / bulk / single)
  - 新 `tests/test_m8_template.py`(4 模式渲染 + `validate ready`)
  - 实测报告 `docs/sample_drug_audit.md`(两批对照 + 误报闸验证 + 抽样 ground-truth)
- **本期不做 / 后续解锁**:
  - R004 / R204(药品申请支付数量 > 实际采购 / 使用)仍待采购 / 用量台账, 不在本期
  - 上游"第二部分"知识库编号到 71+, 本期只接 -5/-8/-70/-71 这 4 份, 将来可增量扩 KB
