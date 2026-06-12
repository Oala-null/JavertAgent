## Why

`add-patient-centric-audit` 的组 A baseline 实测告诉我们: **空 `prompt_addon` 是耗时与质量的同一个根因** —— J66252 跑 30 条 P0 用了 41.5 分钟 (avg 83s/rule), 21 条空骨架占了慢规则 Top 全部, 而带 `prompt_addon` 的 R191 跑 70s + conf 1.00 反而最快。medical 专家这周完成了 163 条筛查 (`docs/163规则可行性分析表-已标注.xlsx`), 109 条标 Y 要做 —— 这意味着我们要在 **41 条已有 yaml + 68 条新建** 上装 prompt_addon。

**手编 yaml 不可承受**: 单条 15-30 min × 109 ≈ 30-50 小时, 还容易写得参差。现有 3 大模板 (`docs/templates/{M1,M2,M3}.md`) 是 markdown 文档形态, 机器无法直接套填。我们需要 **模板工具化** 与 **yaml 自动产出**, 后续按违规类型分 6 个 rollout change (M1-M6) 才能跑得动。

## What Changes

- 新 capability `rule-templating`: 模板存储 (`configs/templates/Mx.yaml`) + 加载 + 字段校验 + 渲染引擎
- 新 CLI 子命令 `javert prompt-fit <rule_id>` 把模板渲染结果写进规则的 `prompt_addon`, 支持三种模式:
  - `--vars personalization.json`: 配置驱动 (适合批量 M1/M2 规整字段)
  - `--interactive`: 交互式一字段一字段问 (适合复杂 M5/M6)
  - `--auto`: 把 `rule.question + example` 喂给 sglang Qwen3.5, LLM 起草 personalization 字段 (兜底, 人审后写入)
- 新 CLI 子命令 `javert template list / show <id> / validate <id>`
- 模板 yaml schema: `template_id` / `name` / `description` / `master_prompt` (Jinja2 风格 `{{var}}`) / `fields` (name/type/desc/required) / `defaults`
- 6 个空模板文件占位 (`configs/templates/M1.yaml` ... `M6.yaml`), 内容由后续 rollout change (m1-rollout, m2-rollout, ...) 各自填
- **Phase 0 (setup, inline 在本 change 内)**: 删除 14 条专家 N 标记 yaml: `R001 R002 R003 R004 R005 R006 R011 R134 R170 R173 R174 R178 R202 R312` —— 全部 `rm`, R312 的 abandoned 状态也一起清掉
- Verification: 把 R191 已写好的 `prompt_addon` 反推到 `configs/templates/M1.yaml` master_prompt + 字段表, 然后用 `prompt-fit R191 --vars docs/m1_r191_vars.json` 一次性 round-trip 复原 (证明模板设计与套填 CLI 都 work)

## Capabilities

### New Capabilities

- `rule-templating`: 模板 yaml schema + 加载器 + Jinja2-style 渲染 + 三种 personalization 输入模式 (`--vars` / `--interactive` / `--auto`) + LLM-auto 起草接入

### Modified Capabilities

- `rule-registry`: 删除 14 条 N 标记 yaml; 不动 Rule schema (priority / status 已就位)
- `cli`: 新增 `prompt-fit` 与 `template` 两个子命令组 (`template list/show/validate`)

## Impact

- **新代码 (~400 行)**:
  - `src/javert/templating/` 新子包 (template_loader.py / template_model.py / renderer.py / prompt_fit_runner.py / llm_drafter.py)
  - `src/javert/commands/prompt_fit.py` 与 `src/javert/commands/template.py`
  - `src/javert/cli.py` +2 subcommand 注册
- **新文件**:
  - `configs/templates/M{1..6}.yaml` 6 个空模板骨架 (内容由后续 rollout 填)
  - `docs/m1_r191_vars.json` (M1 模板的 R191 personalization 反推数据, verification 用)
  - `tests/test_template.py` / `tests/test_prompt_fit.py`
- **数据变更**:
  - `rm configs/rules/{R001,R002,R003,R004,R005,R006,R011,R134,R170,R173,R174,R178,R202,R312}.yaml` (14 files)
  - 完事后 yaml 总数 55 - 14 = **41 条**, 全是 Y rules
- **依赖**: 新增 `jinja2>=3.1` (pyproject.toml). LLM-auto 模式复用现有 `Qwen35Provider`, 不引新依赖
- **零外部依赖变化**: 不动 sglang / 142 SQL / zadig_agent
- **后续 change 解锁**: m1-rollout / m2-rollout / m3-rollout / m4-rollout / m5-rollout / m6-rollout 6 个 rollout change 可启动, 每个独立完成自己的模板内容 + 跑批 prompt-fit
- **不在本期范围**: 不写 M1-M6 任何模板内容 (除 R191 反推作 verification); 不批量产任何 rollout yaml; 不动 audit_runs schema
