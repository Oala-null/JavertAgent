# Rule 设计指南

每条「做不了」规则对应一个 `configs/rules/Rxxx.yaml`. 本指南逐字段说明 + 给出 R191 示范.

## yaml schema

```yaml
rule_id: R191                          # 必填; 形如 RNNN (0325 序号) 或 RDNN (药品类, 无 0325 序号). pattern: ^(R\d{3}|RD\d{2,3})$
domain: 肿瘤                            # 必填; 所属领域 (肿瘤/各科室通用类/临床检验...)
violation_type: 重复收费                 # 必填; 违规类型 (从 0325 表 copy)
question: |                            # 必填; 问题原文
  开展肿瘤全身断层显像, 重复收取人工报告费用.
example: |                             # 可空; 违规示例 (清单 0325 内的参考示例)
  示例: 某医院..., 在收取了肿瘤全身断层显像的同时, 还另外收取了人工报告费.
status: drafting                       # 必填; drafting/ready/validated/abandoned
prompt_addon: ""                       # 由操作者编写; 给 LLM 的额外指引
trigger_keywords: []                   # 关键词列表; **router B v2 用它弹性匹配 fee_name + diagnoses 决定本病案是否需要 LLM 审**
suggested_tools: []                    # 建议优先调用的工具
expected_signal: ""                    # 操作者备注: 预期 LLM 应找到什么
notes: ""                              # 设计 / 取舍 / 已知失败情况
drug_rule_type: null                   # (v0.8) M8 药品规则填: 限适应症/超说明书/限二线/禁忌症; 非药品规则 null

# (v0.5 router B 新增, 全部 optional, 缺省即不限制 — yaml 缺这些字段 router 视为"不限")
applicable_visit_type: []              # ["ipt", "opt"] — 仅住院/门诊适用
applicable_gender: ""                  # "M" 或 "F" — 仅一种性别
applicable_age_min: null               # int — 年龄下限
applicable_age_max: null               # int — 年龄上限
applicable_diag_codes: []              # ["C73", "D34"] — ICD 前缀, 支持 'C73*' 通配
applicable_departments: []             # ["骨科", "肿瘤内科"] — 仅这些科室
```

## 字段含义

### 必填 / 来自 0325 表 (operator 不需要修改)

| 字段 | 说明 |
|------|------|
| `rule_id` | `R{序号:03d}` 从 0325 表行号映射; **或 `RD{NN}` 药品类命名段** (v0.8, 0325 清单无对应序号: `RD01-03` 类型级 / `RD10+` 精选). pattern `^(R\d{3}\|RD\d{2,3})$`. 不要手改. |
| `domain` | 所属领域. 与 spec 区分领域用. |
| `violation_type` | 违规类型: 重复收费 / 串换项目 / 过度检查 ... |
| `question` | 0325 表「问题」列原文. 这是判定的法律依据. |
| `example` | 0325 表「违规参考示例」. 给 LLM 看具体形态. |

### 操作者编辑

| 字段 | 关键点 |
|------|--------|
| `status` | 状态机: drafting → ready → validated; abandoned 任意可达. 用 `javert mark` 改, 不要手改. |
| `prompt_addon` | 给 LLM 的「这条规则要注意什么」自然语言指引. 1-3 段为佳, 太长 LLM 抓不到重点. |
| `trigger_keywords` | **双重用途** (v0.5 起): (1) LLM 搜证据的提示关键词; (2) router B 用它弹性匹配 patient fee_name + diagnoses 决定本病案是否需要 LLM 审. 写得太严会假阴性 (router 漏过本病案), 太宽会噪声 (router 跑了 LLM 浪费). 例: R191 = ["人工报告", "断层显像", "全身断层"]. router 弹性策略: ≥3 字 keyword 用 60% prefix (e.g. "病理检查" → "病理"); ≤2 字精确包含. |
| `suggested_tools` | 例: ["search_fees", "search_notes"]. 仅作 hint, 不强制. |
| `expected_signal` | 我心目中的「典型违规长这样」, 帮自己未来 review 用. 不会进 prompt. |
| `notes` | 设计变更日志 / 已发现的边界情况 / 为什么 abandon. |
| `applicable_visit_type` | (v0.5) optional, 例 `["ipt"]` 表示仅住院适用. router 在 patient visit_type 不匹配时直接 prune. yaml 缺省 = 不限制. |
| `applicable_gender` | (v0.5) optional, `"F"` 或 `"M"`. router prune 不符病人. |
| `applicable_age_min` / `applicable_age_max` | (v0.5) optional, int 年龄区间. 用于"限儿童 / 限老年人"类规则. |
| `applicable_diag_codes` | (v0.5) optional, ICD 前缀列表如 `["C73", "D34*"]`. router 看 patient.diagnosis_codes 是否 startswith 任一前缀. |
| `applicable_departments` | (v0.5) optional, 仅这些科室适用. 用于"限骨科 / 限重症医学"类规则. |

## status 状态机说明

- **drafting**: 初始状态. yaml 还在迭代 prompt_addon, dry-run 多轮.
- **ready**: 我认为 prompt 设计好了, 在 50 patient 上做批跑前的状态.
- **validated**: 50 patient 批跑完, verdict 分布合理 (V/C/I 比例可解释), 准入清单.
- **abandoned**: 这条规则在现有数据下做不出来 (假阴性 / 假阳性都太高), 弃之.

## 设计 prompt_addon 的 checklist

1. **明确「正向证据」与「负向证据」**: 例如 R191 正向 = 同一次住院出现「肿瘤断层显像」+ 「人工报告」两笔, 负向 = 报告费在打包项内.
2. **示例引用**: 在 prompt_addon 里直接引用 1-2 个 violation 与 1-2 个 clean 的对比例子.
3. **避免诱导**: 不要写「请输出 VIOLATION」之类强引导, 让模型自由判断.
4. **限定数据来源**: 强调"必须从 search_fees / search_notes 调用结果中找到证据".
5. **置信度提示**: 当只有 1 条间接证据时, 提示模型置信度 ≤0.6.

## 设计 trigger_keywords 的 checklist (v0.5 起更重要)

trigger_keywords 在 v0.5 之后兼任 **router B 的命中钥匙** — 它决定本规则在哪些病案上要请 LLM. 写法影响:

1. **既不能太严, 也不能太宽**:
   - 太严 (yaml: `["全器官大切片", "蜡块"]`, 但患者 fee 叫 "病理切片诊断") → router 漏过本病案 → V 假阴性
   - 太宽 (yaml: `["微波"]`, 命中所有"射频/微波消融") → router 让无关病案也跑 LLM → 浪费 GPU
2. **优先用领域术语 + 不带后缀的"核心 token"**:
   - 好: `["病理", "蜡块", "组织块"]` (router 子串命中 "病理切片诊断")
   - 差: `["病理检查"]` (子串不命中 "病理切片诊断", 需靠 60% prefix 弹性)
3. **多关键词覆盖同一规则的多种写法**:
   - R103 (影像虚构) `["超声检查", "CT 检查", "MRI 检查", "影像报告", "影像号", "阅片"]` — 覆盖各种影像类
4. **看实际 fee_name 写**: 不知道写什么时, `head data/shi_fee.csv | grep <领域>` 看医院实际项目名.
5. **router 的 fallback**: 若 trigger_keywords 为空 (空列表), router 视为"无限制命中", 所有病案都送 LLM. 等同于不参与 prefilter.

调试: `uv run python scripts/test_router_smoke.py` 跑 5 patient 看本条 yaml 是否被 router 命中.

## 示范: R191

参考 `configs/rules/R191.yaml`. 该 yaml 由 `javert init` 生成时只有源字段; `prompt_addon` /
`trigger_keywords` 等需要操作者编写, 详见 `docs/sample_run_R191.md` (待 dry-run 后补充).

```yaml
rule_id: R191
domain: 肿瘤
violation_type: 重复收费
question: 开展肿瘤全身断层显像, 重复收取人工报告费用.
example: ""
status: drafting
prompt_addon: |
  本规则关注: 同一次住院里, 患者既被收取了「肿瘤全身断层显像」类项目, 又被
  额外收取了「人工报告」/「图文报告」/「PET-CT 报告费」等独立报告费用. 一般来说,
  断层显像项目本身已包含报告费, 单独收取报告费即是重复收费.

  审计步骤建议:
  1. 用 search_fees(category=检查类) 把所有检查类费用列出, 看是否同时出现「PET-CT」/「全身断层」/「SPECT 全身」+ 「报告费」/「人工报告」.
  2. 若只见显像项目无独立报告费, verdict=CLEAN.
  3. 若两者并存, 进一步看是否文书 (search_notes section=辅助检查) 提到该次扫描有专门人工报告流程, 没有则 verdict=VIOLATION.
  4. 信息不足以判断 (例如只有显像而无费用项明细) → INCONCLUSIVE.
trigger_keywords:
  - 全身断层
  - PET-CT
  - SPECT
  - 人工报告
  - 图文报告
  - 报告费
suggested_tools:
  - search_fees
  - search_notes
expected_signal: |
  典型违规: 一次住院里同时存在 "PET-CT 全身断层显像" 与 "PET-CT 人工图文报告费" 两笔费用.
notes: |
  - 起点版本由 javert init 生成, prompt_addon 由 operator 添加 v0.1.
  - TODO: 在 5 个 pilot 上 dry-run 验证假阴性/假阳性比.
```
