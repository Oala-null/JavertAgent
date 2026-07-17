# Template Design Guide (M1-M8)

模板套填操作者手册. 给 `m1-rollout` ... `m7-rollout` 后续 change 的实施者看的.

> **何时读它**: 你拿到一个 `configs/templates/Mx.yaml` 占位文件, 准备把它从 `empty` 状态推到 `ready`,
> 然后用 `javert prompt-fit` 把它批量套到一批规则的 `prompt_addon` 上.

---

## 0. 模板地基快览

- 加载 / 渲染 / 写盘的代码在 `src/javert/templating/`, 不需要碰
- 8 个 `configs/templates/M{1..8}.yaml` 已就位且全 rollout 完成 (M1-M7 骗保类 + M8 药品适应症/限定审计)
- CLI: `javert template list / show / validate`, `javert prompt-fit <rule_id> --template <id> [mode]`
- Rule yaml schema 已加 `derived_from_template` 字段, 记录该规则的 `prompt_addon` 来自哪个模板

---

## 1. Template yaml schema

```yaml
template_id: M1                # 形如 M1; 必须与文件名一致 (M1.yaml)
name: 重复收费                  # 中文短名, 给 `template list` 显示
description: ...               # 一行违规模式概述, 给 list 显示
master_prompt: |               # Jinja2 字符串, 渲染为 rule.prompt_addon
  ...
keywords_template: |           # 可选; 渲染产物按 yaml list 解析 → rule.trigger_keywords
  {% for kw in aux_keywords %}- {{kw}}
  {% endfor %}
tools_template: |              # 可选; 同上, 给 rule.suggested_tools
  - search_fees
  - search_notes
signal_template: |             # 可选; 给 rule.expected_signal (str)
  典型违规: ...
fields:                        # personalization 字段表 (vars dict 的 schema)
  - name: a_concept            # 字段名, vars JSON 的 key
    type: str                  # 一种: str | list[str] | enum | bool | int
    desc: "A 类项目类别描述"     # 操作者提示文本 (interactive 模式作 prompt)
    required: true             # 缺省 true; false 时可省略并取 default
    default: ...               # 可选; 类型必须与 type 兼容
  - name: fee_category
    type: enum
    options: [手术类, 药品类, 耗材类, 检查类, 其他类]
    default: 检查类
    desc: "search_fees(category=...) 取值"
    required: true
```

### Loader 会拦的常见错

- `template_id` 缺失 / 与文件名不一致 → `TemplateValidationError`
- `enum` 类型 fields 没写 `options` → 拒绝
- `default` 类型不匹配 → 拒绝
- 同模板内 `fields[*].name` 重复 → 拒绝
- yaml 顶层不是 mapping → 拒绝

跑 `javert template validate <id>` 是最快的预检.

---

## 2. Jinja2 渲染特性 (易踩点)

- 引擎用 `Environment(undefined=StrictUndefined, trim_blocks=False, lstrip_blocks=False, keep_trailing_newline=True)`
- `{{ var }}` 引用未在 vars 中提供的字段 → `RenderError` (vars_validator 优先抛, jinja 兜底)
- `{% if x %}...{% endif %}` 与 `{% for it in xs %}...{% endfor %}` 都生效
- 行内 `{% for %}` 不引入额外换行, 因为 `trim_blocks=False`
- 写多行 master_prompt 用 yaml `|` 块. 渲染产物保留最后换行符
- list 联接用 `loop.last` 控分隔符:
  ```jinja
  {% for it in xs %}"{{it}}"{% if not loop.last %} / {% endif %}{% endfor %}
  ```
  vars `xs=['a','b','c']` → `"a" / "b" / "c"`

### keywords_template / tools_template 的规范

这两个字段被 `yaml.safe_load` 在渲染后解析为 list. 所以输出必须是合法 yaml list 字面量:

```yaml
keywords_template: |
  {% for kw in aux_keywords %}- {{kw}}
  {% endfor %}
```

渲染产物示例 (输入 `aux_keywords=['x','y']`):
```
- x
- y
```

被 `yaml.safe_load` 解析为 `['x', 'y']`. 若产物不是 list (例如忘了 `- ` 前缀), 渲染会抛 RenderError.

---

## 3. 三种模式怎么选

```
$ javert prompt-fit <rule_id> --template <M*> [--vars X.json | --interactive | --auto]
```

| 模式 | 何时用 | 形态 |
|------|--------|------|
| `--vars docs/foo.json` | **批量规整**: 同模板下 5+ 条规则, 用脚本生成 vars JSON 后逐条套填 | 配置驱动, 可 git diff |
| `--interactive` | **单条复杂**: M5/M6 字段多, 一字段一字段问最稳 | CLI 提示 → stdin 输入 |
| `--auto` | **冷启动**: 既无 vars 也不想手填, 让 Qwen 起草, 人审后再写 | LLM 起草 → stdout diff → `[y/N]` |

三模式互斥 (一次只能一种); 但可以**级联**:
1. `--auto` 让 Qwen 起草 → 保存 vars → 操作者手编 vars JSON → `--vars` 二次套填
2. `--interactive --save-vars docs/m1_R047_vars.json` 把交互输入留底, 下次 `--vars` 重跑

### `--auto` 注意

- 用 62 当前本地 sglang 模型（2026-07-17 为 Qwen3.6-35B-A3B-FP8，
  `http://192.168.31.62:30000/v1`），非 Claude
- Qwen 中文起草偏机械, 关键字段可能空泛 — **写盘前永远人审一遍**
- 默认拒绝 (输入非 `y` 都视为 abort)
- LLM 起草 vars 通过 `validate_vars` 校验后才进 render

---

## 4. 字段类型与 vars 文件

vars 文件可以是 JSON 或 YAML (按后缀 `.json` / `.yaml` 自动判). 字段值必须类型匹配:

| TemplateField.type | vars 中的 JSON 形态 | 说明 |
|-------------------|-------------------|------|
| `str` | `"text"` | UTF-8 字符串 |
| `list[str]` | `["a", "b"]` | 字符串数组 |
| `enum` | `"option_value"` | 必须 ∈ `options` |
| `bool` | `true` / `false` | JSON bool |
| `int` | `42` | 整型 (拒绝 bool 别混入) |

### required vs default

- `required: true` + 缺省 + 无 default → vars_validator 报错, exit 1
- `required: false` + 缺省 + 有 default → 取 default
- `required: false` + 缺省 + 无 default → 取类型零值 (`""` / `[]` / `0` / `False`)

### `unknown_key` 严格拒绝

vars 含模板未声明的 key → exit 1. 防止操作者拼错字段名而无声沉默.

---

## 5. 反推现有规则的 round-trip 方法

M1 的工作流是: 拿已有的 R191.yaml `prompt_addon` 当 ground truth, 设计 master_prompt + 16 个 personalization 字段, 写 `docs/m1_r191_vars.json`, 然后:

```bash
uv run javert prompt-fit R191 --template M1 --vars docs/m1_r191_vars.json --dry-run --output - \
  > /tmp/m1_rendered.txt
diff <(uv run python -c "import yaml; print(yaml.safe_load(open('configs/rules/R191.yaml'))['prompt_addon'], end='')") /tmp/m1_rendered.txt
```

字节级一致才算 M 模板能复刻原始规则. 若有 diff, 说明模板少抓了一个 personalization 维度,
要回到 master_prompt 加一个 `{{var}}` 或 `{% if %}` 分支.

**这是设计模板的最强证据**: 反推不下来的, 说明现有规则有特殊文案, 要么把它纳入 fields, 要么承认模板覆盖不到这条.

---

## 6. 写盘行为

`prompt-fit` 写盘时:

1. 用 ruamel.yaml round-trip 读 rule yaml (保留其他字段注释)
2. 覆盖 `prompt_addon` (用 LiteralScalarString 多行 `|` 块)
3. 覆盖 `trigger_keywords` / `suggested_tools` / `expected_signal` (若模板有对应 aux template)
4. **追加** 或 **覆盖** `derived_from_template = <template_id>` (插在 notes 之后)
5. 不动 `rule_id` / `domain` / `violation_type` / `question` / `example` / `status` / `priority` / `notes`

**`status` 不变**: 跑完 prompt-fit 后, 操作者需手动 `javert mark <rule_id> --status ready` 推进.

---

## 7. 后续 rollout change 工作流建议

1. 选定模板, 跑 `javert template show M7` 确认占位 (或换为目标模板 id)
2. 设计 master_prompt (参照 `docs/templates/模板X_*.md` 的 markdown 设计稿)
3. 选一条 reference 规则做反推 round-trip 验证 (M1 = R191 / M3 = R245 / M7 = R083 等)
4. 写 master_prompt + fields + keywords/tools/signal 模板 → `configs/templates/MX.yaml`
5. `javert template validate MX` 应报 `ready`
6. 为每条对应规则准备 `docs/mX_Rxxx_vars.json`
7. 批量跑 `for rid in R001 R002 ...; do javert prompt-fit $rid --template MX --vars docs/mX_$rid_vars.json; done`
8. `javert audit-patient J66252 --rules R001,...` 用一组规则 dry-run 看 trace
9. 满意 → `javert mark` 推 ready
10. 不满意 → 改 master_prompt → 重跑 prompt-fit (`derived_from_template: MX` 在, 复套不会有歧义)

### 当前模板状态 (2026-07-17)

| 模板 | name | rollout 状态 | ready 数 | reference |
|------|------|------------|---------|-----------|
| M1 | 重复收费 | ✅ 完成 | 22 | R191 (byte-equal round-trip 验证) |
| M2 | 过度检查 | ✅ 完成 | 22 | R151 |
| M3 | 串换项目 (含口腔) | ✅ 完成 | 17 | R245 |
| M4 | 超标准收费 | ✅ 完成 | 13 | R193 (体表肿物切除) |
| M5 | 虚构医药服务 | ✅ 完成 | 10 | R203 / R317 / R318 |
| M6 | 过度诊疗 | ✅ 完成 | 9 | R310 (精神科住院) |
| M7 | 项目身份串换收费 | ✅ 完成 | 20 | R083 (冰袋 vs 冷疗) |
| **M8** | **药品适应症/限定审计** | ✅ **bulk 收敛；肿瘤 v2 on** | 5 ready (RD04+R007+RD01-03)；RD10-37 abandoned | `scripts/init_drug_rules.py` 维护通用 bulk；RD04 独立维护 |

M5 的 10 条由原 8 条虚构服务规则加 R317（溶栓术配套）和 R318（内镜治疗）组成；
后两条体现当前的举证倒置/companion 预检口径。

#### M8 schema 特殊点 (与 M1-M7 不同)

- **drug_rule_type** (enum 必填): 驱动 4 种比对逻辑 —— 限适应症/超说明书 (诊断∉依据→V) / 禁忌症 (诊断∈禁忌→V **反向**) / 限二线 (诊断∈适应症但无一线失败证据→V, 多查 `search_notes`).
- **drug_focus** (str 可空): 精选规则填单一通用名 (如 `人血白蛋白`) → 渲染出聚焦语 + 与该药 `trigger_keywords` 配对走 router 精准触发; 类型级留空 → bulk 全覆盖 + trigger_keywords=[] (router always-on) + 渲染「类型级收敛」预算控制块.
- **on-label 误报闸 + 同名异药/剂型复核**: master_prompt 硬写「命中 KB ≠ 违规」「诊断与依据合理临床外延算落在范围内」「fee 剂型与依据明显不符 → 同名异药 → INCONCLUSIVE」.
- 诊断源: 工具 `drug_audit_lookup` bulk 直接带出 shi_zd 病案首页诊断 (ground truth), M8 引导优先用它, `note_diagnosis` 兜底.
- 配套 Rule 字段: `drug_rule_type` (optional, M8 规则填; 非药品规则 None). 禁忌症规则 `violation_type` 单列「用药安全/禁忌」.
- 当前所有权: `RD04` 负责肿瘤医保限定臂，`R007` 负责非肿瘤医保限适应症，`RD01-03` 负责其余三类 bulk；`RD10-RD37` 不进入默认执行集。详见 `docs/oncology/operations.md`.

---

## 8. 反模式

- ❌ master_prompt 内嵌操作者本机路径 / 患者特定 ID (那是 vars 的事)
- ❌ 用 `str.format` 风格 `{var}` (会被 yaml 当字面量, jinja 不识别)
- ❌ keywords_template 输出非 yaml list (例: 用 `,` 分隔成一行) — 渲染会拒绝
- ❌ enum 写错 options 值 — vars_validator 拒绝, exit 1
- ❌ `--auto` 后没看输出就回车 `y` — Qwen 起草质量参差, 必须 review
- ❌ 模板 fields 加得过细 (>30 个) — 操作者会写错; 拆模板比加字段好
