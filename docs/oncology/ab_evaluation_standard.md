# Oncology 架构 A/B Evaluation Standard v0.1

## 1. 要回答的问题

本标准比较：

- **A（legacy）**：原有 Javert oncology/LLM 路径在本次冻结输入上的 patient-level verdict。
- **B（structured）**：同一次 `shadow` 运行中的结构化 oncology eligibility/proof 路径。

Workbench 历史行不是严格 A。它们可能来自不同病历窗口、规则、模型和知识版本，只能提供历史背景；
其中的专家批复只有在病例、证据和版本仍可核对时才能成为候选 reference，正式效果评测优先重新进行
盲化 adjudication。现有 `shadow_comparison.json` 保持 `historical_unpaired`，不得改名为 paired。

评测有两个正交问题：

1. **paired effect**：在同一病例输入下，B 是否比 A 更安全、更正确、更有用、更易复核？
2. **repeat consistency**：同一 arm 对相同输入重复执行是否稳定？A 的 LLM 波动必须暴露，B 的
   canonical structured output 应 100% 一致。

只有第二项通过不能证明临床优效；缺少 same-input cohort 或专家 reference 时，状态只能是
CONFORMANCE/`INSUFFICIENT_EVIDENCE`。

## 2. 不可违反的实验设计

`EvaluationPlan` 必须冻结并计算 checksum：

- source snapshot、候选查询及独立分母；
- 分析单位、cohort/stratum；
- A/B code commit、配置、模型、工具、Ontology、知识和 release 版本；
- 每 case 重复次数、modal 规则（平票→INCONCLUSIVE）；
- harm matrix、bootstrap seed/次数和 acceptance profile；
- 临床、医保/药学、数据和工程签署角色。

真实 paired 输入优先由 Hub Evidence Snapshot 提供：A/B 必须引用同一
`manifest.snapshot_id/snapshot_checksum` 和 canonical artifact digests。`sh_yb_platform` 与
`TP_data_hub` 数据量不同，跨库结果永远不能配对。当前 `atomic_snapshot=false` 的物化包可用于
工程 SHADOW；PROMOTION 前需 DBA 原子 snapshot/backup 或 Snapshot Isolation。

主要临床单位是 patient-level A/B outcome。B 的 drug/policy-scope/criterion 只用于 evidence/proof
诊断，不得把一个 patient verdict 复制到多个候选后虚增样本量。任一 arm 缺失、snapshot 不同、
候选漂移、版本浮动或分母不独立，paired report 必须 INVALID。

## 3. 指标体系：不设总分

速度、token 或覆盖率不能抵消安全失败。验收按以下顺序逐门禁：

| 维度 | 核心指标 | 对专家的解释 |
|---|---|---|
| Validity | paired/input completeness；schema/PHI/technical error | 两套系统是否真的看了同一批资料，结果是否完整可比 |
| Safety | false violation、false clean、unsafe auto-decision、paired harm loss | 是否把不该违规的人判违规，或把真问题放过 |
| Useful automation | correct automation、appropriate/unnecessary abstention | 是否只是全部推给人工，还是在安全前提下真正减少工作 |
| Evidence integrity | decisive grounding、locator resolvability、proof/provenance/version completeness | 专家能否从决定性条件一键回到来源，并知道用了哪版知识 |
| Epistemic honesty | UNKNOWN/CONFLICT visibility | 缺资料和证据冲突是否被诚实展示，而不是猜成阴性或阳性 |
| Reproducibility | canonical digest stability、outcome disagreement、technical failure | 同一病例重复运行会不会漂移 |
| Expert usability | source retrieval、解释充分性 1–5、复核耗时、correction count | 专家能否快速找到原文、理解为何改变结论 |
| Cost（次级） | latency、token、tool calls | 达成同等安全与有效性后，哪条路径更省资源 |

每个 rate 必须保存 numerator、denominator、value 和 Wilson 95% interval；分母为 0 时是
`not_estimable`，不是 0%。paired harm-loss B−A 使用 plan 固定 seed 的 bootstrap 单侧 95%
upper bound，并同时报告 win/tie/loss 与 exact sign-test。A 的自报 confidence 与 B 的四态证据充分性
不是同语义概率，默认 `not_comparable`，不得画伪校准曲线。

## 4. 默认验收门槛

门槛属于 versioned plan；领域负责人可以用新版本收紧，不得改写既有报告。

| Gate | CONFORMANCE | SHADOW | PROMOTION |
|---|---:|---:|---:|
| paired/input completeness | 100% | 100% | 100% |
| schema/PHI/technical errors | 0 | 0 | 0 |
| adjudicated sample | 合成必需场景全覆盖 | ≥30；实际出现的每类 ≥5 | ≥100；每类 ≥20 |
| B 新增 safety-critical regression | 0 | 0 | 0 |
| B unsafe-auto | golden=0 | 不高于 A；paired upper≤5pp | Wilson upper≤5%；不高于 A |
| harm-loss B−A upper 95% | ≤0 | ≤0.05 | ≤0.02 |
| correct automation B−A lower 95% | N/A | ≥−5pp | ≥−5pp |
| grounding / locator | 100% / 100% | ≥95% / ≥95% | 自动裁决 100%；全体 ≥98% |
| proof/provenance/version | 100% | 100% | 100% |
| B repeat canonical stability | 100% | 100% | 100% |
| explanation / source retrieval | 结构完整 | 样本足够时 median≥4/5、≥95% | median≥4/5、≥95% |

样本或类别分层不足必须 `INSUFFICIENT_EVIDENCE`。PASS 只表示可以进入下一次人工决策，
`deployment_authorized` 永远由独立发布流程决定，评测器不得自动切 flag、publish 或部署。

## 5. 专家盲化与 reference

- 每 case 至少两名独立 reviewer；看不到 A/B 身份和对方结论。
- 两人分歧时增加独立 adjudication；原始意见不得覆盖。
- 记录 reference outcome、reason code、证据充分性、source retrieval、解释评分、复核耗时和纠错数。
- 公共报告不保存自由文本病历意见；需保留的专家自由文本只进入受控私有工件。
- Review packet 使用随机 X/Y 槽位；若泄露 legacy/structured 身份，该 usability 样本无效。

## 6. 持久化与可重放

每次评测创建新目录和 evaluation ID，禁止覆盖：

```text
evaluation_plan.json
evaluation_cases.json
arm_observations.json
expert_adjudications.json
evaluation_report.json
manifest.json
```

每个文件是 canonical JSON + SHA-256；report 引用 plan/cases/observations/adjudications digest，
manifest 再记录全部工件 digest。合成去标识包可以进 Git；真实病例包必须位于批准的 Git 外
0700 目录、0600 文件，只用 salted case/run ref。公开报告仅含聚合指标、版本、reason code 和 digest。

## 7. 当前完成与未完成

仓库已经完成：

- Evaluation Contract、统计方法和三档 gates；
- oncology same-run paired adapter、case delta、盲化 packet schema；
- 合成 paired fixtures 和 CONFORMANCE/SHADOW/PROMOTION 门禁测试；
- Diagnosis 对同一合同的 extraction/evidence conformance 复用。

仓库尚未完成也未获授权：

- 从真实 source snapshot 重跑 legacy A 与 structured B 的正式 paired cohort；
- 两名以上专家的盲化 adjudication；
- 真实 SHADOW/PROMOTION 结论或生产切换。

因此，当前可以宣称“评测合同与 consistency/conformance 门禁已可重放”，不能宣称“B 已被证明
临床优于 A”。
