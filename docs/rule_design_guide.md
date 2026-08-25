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
priority: P3                           # P0/P1/P2/P3; P0 最高
handling_level: 可疑（警告）             # 违规（阻断）/可疑（警告）/提醒（引导）
prompt_addon: ""                       # 由操作者编写; 给 LLM 的额外指引
trigger_keywords: []                   # 关键词列表; **router B v2 用它弹性匹配 fee_name + diagnoses 决定本病案是否需要 LLM 审**
trigger_codes: []                      # 医保码/院内码/类别 token; 与关键词命中取并集
exam_keywords: []                      # verdict gate 匹配检查/费用项目名; 空则回退 prompt
suggested_tools: []                    # 建议优先调用的工具
expected_signal: ""                    # 操作者备注: 预期 LLM 应找到什么
notes: ""                              # 设计 / 取舍 / 已知失败情况
derived_from_template: null            # M1-M8 模板来源; 手写规则可空
drug_rule_type: null                   # (v0.8) M8 药品规则填: 限适应症/超说明书/限二线/禁忌症; 非药品规则 null
render_hash: null                      # prompt-fit 最近渲染 hash; 覆盖手工改动的护栏
precheck: null                         # 可选: {a_items: [], b_items: [], mode: coexist|companion|presence}

# (v0.5 router B 新增, 全部 optional, 缺省即不限制 — yaml 缺这些字段 router 视为"不限")
applicable_visit_type: []              # ["ipt", "opt"] — 仅住院/门诊适用
applicable_gender: ""                  # "M" 或 "F" — 仅一种性别
applicable_age_min: null               # int — 年龄下限
applicable_age_max: null               # int — 年龄上限
applicable_diag_codes: []              # ["C73", "D34"] — ICD 前缀, 支持 'C73*' 通配
applicable_departments: []             # ["骨科", "肿瘤内科"] — 仅这些科室
```

`applicable_*` 是 Router 从原始 YAML 读取的扩展元数据，不属于 `Rule` Pydantic 的运行时
字段；其余字段应与 `src/javert/audit/rule.py` 一致。

## 字段含义

### 必填 / 来自 0325 表 (operator 不需要修改)

| 字段 | 说明 |
|------|------|
| `rule_id` | `R001-R318` 通常从 0325 表行号映射；`R319+` 仅用于 notes 明确标注来源的本地专家扩展；`RD{NN}` 为药品类命名段。pattern `^(R\d{3}\|RD\d{2,3})$`。已注册 ID 不要手改。 |
| `domain` | 所属领域. 与 spec 区分领域用. |
| `violation_type` | 违规类型: 重复收费 / 串换项目 / 过度检查 ... |
| `question` | 0325 表「问题」列原文. 这是判定的法律依据. |
| `example` | 0325 表「违规参考示例」. 给 LLM 看具体形态. |

### 操作者编辑

| 字段 | 关键点 |
|------|--------|
| `status` | 状态机: drafting → ready → validated; abandoned 任意可达. 用 `javert mark` 改, 不要手改. |
| `priority` | `P0` 最高、`P3` 最低；控制规则筛选和先审/先跑顺序，不代表违规程度或已通过验证。 |
| `handling_level` | 规则静态处理等级：`违规（阻断）` 可直接形成拦截类结论，`可疑（警告）` 需专家复核，`提醒（引导）` 用于补资料或现场核查。它不等于患者运行时 `verdict`，也不替代 `priority`。 |
| `prompt_addon` | 给 LLM 的「这条规则要注意什么」自然语言指引. 1-3 段为佳, 太长 LLM 抓不到重点. |
| `trigger_keywords` | **双重用途** (v0.5 起): (1) LLM 搜证据的提示关键词; (2) router B 用它弹性匹配 patient fee_name + diagnoses 决定本病案是否需要 LLM 审. 写得太严会假阴性 (router 漏过本病案), 太宽会噪声 (router 跑了 LLM 浪费). 例: R191 = ["人工报告", "断层显像", "全身断层"]. router 弹性策略: ≥3 字 keyword 用 60% prefix (e.g. "病理检查" → "病理"); ≤2 字精确包含. |
| `trigger_codes` | Router 的编码/类别补充召回条件，与 `trigger_keywords` 取并集；适合不同医院项目名不一致但编码稳定的场景。 |
| `exam_keywords` | verdict gate 用于定位检查/费用项目；空时才回退从 `prompt_addon` 提取。 |
| `suggested_tools` | 例: ["search_fees", "search_notes"]. 仅作 hint, 不强制. |
| `expected_signal` | 我心目中的「典型违规长这样」, 帮自己未来 review 用. 不会进 prompt. |
| `notes` | 设计变更日志 / 已发现的边界情况 / 为什么 abandon. |
| `derived_from_template` | 最近一次模板来源，如 `M1`/`M8`；不是模板生成的规则可空。 |
| `drug_rule_type` | M8 的 `限适应症/超说明书/限二线/禁忌症`；非药品规则为空。 |
| `render_hash` | `prompt-fit` 最近渲染产物 hash，用于发现模板渲染后又被手工改过。 |
| `precheck` | 确定性费用项目集。`coexist` 用于 M1 附属并存；`companion` 用于主术式与必备配套缺失；`presence` 用于目标收费存在性；`coexist_review` 只确认两组费用共存、不注重复收费偏置；`presence_review` 在目标费用存在时零模型进入外部资料复核。无预检则为空。 |
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

当前机器真相以 `configs/rules/R191.yaml` 为准：它已经是 `ready`，由 M1 派生并带
`precheck`。下面只保留一个便于阅读的缩略示例，不应复制回去覆盖现行规则。

```yaml
rule_id: R191
domain: 肿瘤
violation_type: 重复收费
question: 开展肿瘤全身断层显像, 重复收取人工报告费用.
example: ""
status: ready
priority: P0
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
  - 起点版本由 javert init 生成, prompt_addon 由 operator 添加.
derived_from_template: M1
precheck:
  a_items: [PET-CT 全身显像, SPECT 全身骨显像, 全身断层, 肿瘤全身断层显像]
  b_items: [图文报告, 人工报告, PET-CT 报告, SPECT 报告费]
  mode: coexist
```

`RD04` 是例外的生产规则：它由人工维护，负责肿瘤药
`oncology=true AND source_type=insurance` 的确定性医保资格链，不由
`scripts/init_drug_rules.py` 生成，也不能用通用 M8 重渲染覆盖。现行所有权和上线模式见
`docs/oncology/operations.md`。

## 已确认漂移何时进入 Promise

Rule YAML 仍是规则定义的唯一位置。Promise 只保护一个已经复现、确认且能写成确定性事实的
窄边界，不用于补写完整规则，也不接受任意表达式或代码。处理顺序是：

1. 用语义化、去标识事实登记 `tests/promise_cases/DRIFT-*.yaml`，不得保存患者号、run ID、
   原始病历、连接信息或凭据。
2. 逐条核对当前 ready 规则，显式列出适用 scope；同一公开行为类别不能代替规则语义核实。
3. 为 `configs/promises/PR-*.yaml` 同时提供 positive 与最相邻 near-negative。一次即可违规、
   组合项目、串换、虚构、限定支付等边界必须明确排除，不能被宽泛 CLEAN Promise 清掉。
4. active 内容不可就地修改；边界变化必须增加版本并显式 `supersedes`。
5. 提交前运行 `.venv/bin/javert promise validate` 和 `.venv/bin/javert promise run`。

若修改规则状态、`trigger_keywords`、模板渲染结果或 M8，还要按既有流程重建并核对 Router
index；若修改会改变既有 Promise 的 scope 或 near-negative，应在同一 change 更新 Promise
资产，不能让旧 Promise 静默扩大解释范围。
