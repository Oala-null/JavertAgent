## 0. Phase 0 — 数据清场 (rm 14 条 N yaml)

- [x] 0.1 确认专家标注最终: `python -c "import openpyxl; wb=openpyxl.load_workbook('docs/163规则可行性分析表-已标注.xlsx', data_only=True); ..."` 重新打印 N 列表, 与设计中 14 条名单一致
- [x] 0.2 `git rm configs/rules/{R001,R002,R003,R004,R005,R006,R011,R134,R170,R173,R174,R178,R202,R312}.yaml` (项目非 git 仓库, 改用 `rm`)
- [x] 0.3 跑 `javert list` 验证剩 41 条 yaml; 优先级分布 P0:30(R312 已删) / P1:3 / P2:8 / P3:0
- [x] 0.4 git commit "phase0: purge 14 N-marked rules" 单独成 commit (跳过, 非 git 仓库)

## 1. Phase 1 — 模板存储 schema

- [x] 1.1 加 `jinja2>=3.1` 到 `pyproject.toml` dependencies; `uv sync`
- [x] 1.2 新 `src/javert/templating/template_model.py`: `TemplateField` (name/type/desc/required/default/options) + `Template` (template_id/name/description/master_prompt/fields/keywords_template/tools_template/signal_template) pydantic 模型
- [x] 1.3 新 `src/javert/templating/template_loader.py`: `load_template(path)` + `load_all_templates(dir)` + `TemplateValidationError`
- [x] 1.4 新 `src/javert/templating/renderer.py`: `render(template, vars) -> dict` 用 jinja2 `Environment(undefined=StrictUndefined)`; 支持 master_prompt + keywords_template + tools_template + signal_template 各自渲染
- [x] 1.5 新 `src/javert/templating/vars_validator.py`: `validate_vars(template, vars_dict) -> ValidatedVars` 校验字段类型 / 枚举 / required / default
- [x] 1.6 创建 6 空 yaml 占位 `configs/templates/M{1..6}.yaml`, 各填 `template_id` / `name` / `description`, 其余字段空; 命名:
  - M1 重复收费
  - M2 过度检查
  - M3 串换项目 (包含口腔)
  - M4 超标准收费
  - M5 虚构医药服务 (含复合"虚构或串换")
  - M6 过度诊疗 + 杂项 (含"将不属医保支付范围")
- [x] 1.7 单测 `tests/test_template.py`:
  - load 正常 template / 加载占位 (无 master_prompt) / 缺 template_id 抛错 / enum 缺 options 抛错
  - render 字段全填 / 缺 required 抛错 / default 兜底 / conditional 分支 / list 字段插入
  - validate_vars 类型不匹配 / enum 值非法 / required 空抛错
- [x] 1.8 `uv run pytest tests/test_template.py -v` 全绿 (26 passed)

## 2. Phase 2 — Rule schema 与 CLI

- [x] 2.1 改 `src/javert/audit/rule.py`: 加 `derived_from_template: str | None = None` 字段
- [x] 2.2 改 `src/javert/audit/rule_writer.py`: `_FIELD_ORDER` 末尾加 `derived_from_template`
- [x] 2.3 扩 `tests/test_rule.py`: 加载带 derived_from_template / 不带 (None) / 字段位置在 notes 之后 三个场景
- [x] 2.4 `uv run pytest tests/test_rule.py -v` 全绿 (19 passed)

- [x] 2.5 新 `src/javert/commands/template.py`: `run_template_list()` / `run_template_show(tid)` / `run_template_validate(tid)`
- [x] 2.6 新 `src/javert/templating/llm_drafter.py`: `draft_vars(rule, template, provider) -> dict` 调 sglang Qwen, 系统 prompt 让 LLM 输出 fenced JSON 含模板的 fields 字典; 失败时抛 `DrafterError`
- [x] 2.7 新 `src/javert/templating/prompt_fit_runner.py`: 总编排 `run_prompt_fit(rule_id, template_id, mode, vars_file=None, dry_run=False, output_path=None, save_vars=None) -> int`
- [x] 2.8 新 `src/javert/commands/prompt_fit.py`: 接 click 参数到 prompt_fit_runner; 输入校验 (三模式互斥) + exit code 区分 0/1/2
- [x] 2.9 改 `src/javert/cli.py`: 注册 `@main.command("prompt-fit")` + `@main.group("template")` 含 list/show/validate

## 3. Phase 3 — Verification (R191 反推 M1 round-trip)

- [x] 3.1 完成 `configs/templates/M1.yaml`: 把 R191 现有 prompt_addon 反推成 master_prompt (Jinja2) + fields 列表 + keywords_template + signal_template
- [x] 3.2 新 `docs/m1_r191_vars.json`: 写 R191 personalization 字段值 (a_concept="肿瘤全身断层显像类" / b_concept="人工/图文报告" / fee_category="检查类" / basic_diagnosis_subject="肿瘤" / 19 fields 全填)
- [x] 3.3 跑 `uv run javert prompt-fit R191 --template M1 --vars docs/m1_r191_vars.json --dry-run --output -` → 与 R191.yaml 当前 prompt_addon **byte-equal (620 chars, 含 trailing newline)**
- [x] 3.4 跑 `uv run javert prompt-fit R045 --template M1 --vars docs/m1_r045_vars.json` (R045 骨科重复收费, 主项手术 + 附属手术) 写盘成功, R045.yaml 含 `derived_from_template: M1`

## 4. Phase 4 — 集成测试

- [x] 4.1 新 `tests/test_prompt_fit.py`:
  - `--vars` 模式 happy path (vars file → rendered prompt_addon → 写盘验证)
  - `--auto` 模式 mock LLM provider 返回 vars json → render → confirm 拒绝 / 接受 两路径
  - 三模式互斥 (`--vars X --interactive` exit 2)
  - 未知 template_id exit 2
  - `--dry-run` 不写盘
  - exit code 区分: 0 happy / 0 aborted / 1 render error / 2 usage
  - R191 round-trip byte-equal (真实 configs)
- [x] 4.2 新 `tests/test_template_cli.py`:
  - `template list` 输出含 empty/ready/partial 三态
  - `template show M2` 输出含 master_prompt + fields
  - `template validate` empty/partial/ready/schema-error 四态
- [x] 4.3 `uv run pytest tests/ -v` 全绿 (134 tests passed)

## 5. Phase 5 — 文档与首跑

- [x] 5.1 更新 `CLAUDE.md` 常用命令节: 加 `prompt-fit` 与 `template` 示例
- [x] 5.2 更新 `CLAUDE.md` 当前阶段标记: 标 ✅ "模板地基 + 14 N 清场"; 标 🚧 "M1-M6 内容由 rollout change 各自填"
- [x] 5.3 更新 `README.md`: 8 子命令 → 10 子命令 (新增 prompt-fit + template); 路线图把 m1-rollout ... m6-rollout 6 个候选 change 写进去
- [x] 5.4 写 `docs/template_design_guide.md`: 给后续 rollout change 操作者看的指南 — 字段类型, Jinja2 语法, vars file 格式, 三种模式选用建议

## 6. 验收

- [x] 6.1 `openspec validate add-rule-template-fitter --strict` 通过
- [x] 6.2 全部单测通过 **134 tests passed**
- [x] 6.3 `javert template list` 显示 6 个模板, M1 ready (19 fields), M2-M6 empty
- [x] 6.4 `javert prompt-fit R191 --template M1 --vars docs/m1_r191_vars.json --dry-run --output -` 输出与 R191.yaml prompt_addon 字节一致 (620 chars)
- [x] 6.5 `javert prompt-fit R045 --template M1 --vars docs/m1_r045_vars.json` 写盘成功, R045.yaml 含 `derived_from_template: M1`, prompt_addon 674 chars, trigger_keywords 5 项
- [ ] 6.6 (可选 smoke) `javert prompt-fit Rxxx --template M1 --auto` 跑一遍看 Qwen 起草质量; 不入库, 仅记观察 — **跳过, 非阻塞 (需 sglang 端在线)**
- [x] 6.7 git status 干净, 可 push (项目非 git repo, 无 status 概念; 所有改动在文件树中)
