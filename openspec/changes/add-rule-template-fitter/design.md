## Context

刚跑完的 `add-patient-centric-audit` 组 A 实测告诉我们几件事:
- **空 prompt_addon 是双重病灶**: 慢规则 Top-3 (R219=172s, R220=137s, R208=129s) 全是骨架, LLM 缺指引时反复 tool call 探索, 既慢又把 28 条判 CLEAN (其中大半是「找不到证据 → 默认 CLEAN」, 在生产是 false negative 风险)
- **prompt 越具体 LLM 越果断**: R191 装好 prompt 跑 70s + conf 1.00, 是组 A 最快段最高置信度
- **结论**: 装 prompt 同时提速 + 提准, 一招两吃

医保专家本周给出 `docs/163规则可行性分析表-已标注.xlsx` 最终筛查:
- **Y = 109 条** (要做), N = 54 条 (不做)
- 现有 yaml 55 条中, 41 是 Y, 14 是 N
- **缺骨架的 Y rules = 68 条** (P1: 25, P2: 41, P3: 2)

现有 3 大模板 (`docs/templates/{M1,M2,M3}.md`) 是 markdown 文档形态, 给人读用 —— 描述了 master_prompt 骨架 + reference yaml + personalization 字段表, 但**机器无法直接套填**。M1 用 R191 reference 已完成, M2 (R151) / M3 (R245) 模板待补。

本次 change 是后续 6 个 rollout change 的**工具地基**。

## Goals / Non-Goals

**Goals:**
- 把模板从 markdown 文档迁到机器可读的 yaml schema (`configs/templates/Mx.yaml`)
- 提供 CLI `javert prompt-fit <rule_id>` 三种 personalization 输入模式: `--vars` (批量) / `--interactive` (单条复杂) / `--auto` (LLM 兜底)
- 提供 CLI `javert template list/show/validate` 给操作者审查模板
- Verification: 用 R191 已写好的 prompt_addon 反推完成 M1 模板 + round-trip 验证
- 清场: rm 14 条 N 标记 yaml (Phase 0)

**Non-Goals:**
- ❌ 写 M2-M6 任何模板内容 (那是 rollout change 的事)
- ❌ 批量产 rollout yaml (m1-rollout 自己跑 prompt-fit 23 次)
- ❌ Web UI / 模板编辑器 (CLI 已足够)
- ❌ 改 audit_runs 或 Rule schema
- ❌ 新增 LLM 路由 / 并发 (留给 `add-parallel-audit`)
- ❌ 跨规则模板组合 / "二级派生模板" / 模板继承 (本期单层)
- ❌ 模板版本号 / 模板演进迁移 (反正进 git diff, 暂不引入版本字段)

## Decisions

### D1. 模板存储用 yaml, 不用 jinja `.j2` 文件

**选项**:
- (a) 纯 jinja2 `.j2` 文件 + 配套 schema.yaml 分两个文件
- (b) 单 yaml 文件含 master_prompt 字符串 (Jinja2 兼容 `{{var}}` 语法) + fields 列表

**选 b**, 因为:
- 模板 + 字段 schema 在同一文件, git diff 时看变化方便
- yaml 的 `|` 多行字符串很适合 master_prompt 这种长文本 (现有 R191 的 prompt_addon 已是此形态)
- Jinja2 引擎可以直接 render 字符串, 不一定要文件来源 (Environment.from_string)
- 不引文件类型膨胀

### D2. Jinja2 vs str.format vs 自定义 mini DSL

**选项**:
- (a) Python `str.format` (`{var}`)
- (b) Jinja2 (`{{var}}`, `{% if %}`, `{% for %}`)
- (c) 自己写 mini 解析器

**选 b**, 因为:
- 模板里几乎肯定会出现条件分支 (例如 M5 虚构: "若 question 含'肿瘤' 则诊断必须含'癌'"), str.format 不支持
- Jinja2 是 Python 生态事实标准, 引入成本低 (~MB 级)
- 后续若要 `--auto` LLM 起草模板内容, Jinja2 错误信息也比自写 DSL 友好

### D3. CLI 形态: 一个 `prompt-fit` 三模式 vs 三个独立命令

**选项**:
- (a) 三个命令: `prompt-fit-vars` / `prompt-fit-interactive` / `prompt-fit-auto`
- (b) 一个命令 + 三 flag: `prompt-fit --vars X` / `--interactive` / `--auto`

**选 b**, 因为:
- 同一操作 (套模板填 yaml) 不应分裂三个动词
- click 子命令面已经 8 个 (`audit-patient` 刚加), 控制规模
- 三种模式可级联: `--auto` 起草后, 仍可 `--interactive` 微调; 实现简单

### D4. LLM-auto 模式用 sglang Qwen 而非 Claude

**选项**:
- (a) sglang Qwen3.5-35B (本地, 项目已有)
- (b) Claude (远端, 起草质量更高但有外部依赖)

**选 a**, 因为:
- 跟 audit-engine 共用一个 LLM 端, 不引第二个外部依赖
- Qwen 中文支持充分, 写中文 prompt_addon 没问题
- 起草质量参差, 但**这是兜底模式**, 人审在后, 不是终态
- 若后续 `--auto` 质量不够再换 Claude, 当前接口设计应允许 swap (LlmDrafter 抽象)

### D5. N 标记 yaml 处理: 直接 rm 而非 mark abandoned

**选项**:
- (a) mark abandoned + notes 写「专家标记 N」(保留 yaml 文件)
- (b) `rm configs/rules/Rxxx.yaml` 直接删

**选 b** (用户决定), 因为:
- N 的语义是 "专家说不做", 与 abandoned ("我们想做但工具不够") 不同
- 不留 yaml 减少 list/audit 噪音
- git history 仍可追溯, 想复活随时 git checkout
- 历史 audit_runs 行保留, 但 `javert show R001 --patient X` 会因找不到 yaml 失败 —— 已接受这个 trade-off
- R312 之前 mark abandoned, 本期一并 rm (跟其他 13 条一视同仁)

### D6. N-purge 合并到本 change, 不单独成 change

**选项**:
- (a) 单独 change `purge-n-marked-yaml`
- (b) 合并到 add-rule-template-fitter 作 Phase 0

**选 b**, 因为:
- 单条 git rm × 14 是 15 分钟工作, 单独 change (proposal + design + tasks) 反而是负担
- "清场后开始装新工具" 是合理的 narrative
- N-purge 的 audit-run 影响 (`show` 失败) 与本 change 无关, 单独还是合并都一样
- 后果是这个 change scope 比纯工具略大, 但仍单一主线 (模板地基 + 数据清场)

### D7. 模板 schema 的字段类型支持

**选项**:
- (a) 全 string (人填字符串, 模板里 jinja 自己处理)
- (b) 多类型: str / list[str] / enum / bool / int

**选 b**, 因为:
- M1 模板的 A 类 / B 类 keywords 天然是 list
- fee_category 是 enum (手术类/药品类/耗材类/检查类/其他类), 校验时拒绝拼错
- 校验能在 `template validate` 阶段就拦下错, 不让烂数据进 prompt 渲染
- 实现成本低 (pydantic 一个 TemplateField 模型)

### D8. 模板字段渲染失败的处理

**选项**:
- (a) 严格模式: 任何字段缺失即 abort
- (b) 宽松: 缺字段用 default; 无 default 则渲成空串, warn

**选 b 但默认值通过 `defaults` 显式声明**, 因为:
- 操作者可能想先草草填 50% 字段看出来啥样
- defaults 在 template yaml 写明, 不是隐式行为
- 还有 required: true 字段强制非空 (TemplateField.required)
- jinja2 `StrictUndefined` 模式可控

### D9. Verification: R191 反推 round-trip

把 R191 已写好的 prompt_addon 反推到 M1 模板 + R191 vars file, 然后用 `prompt-fit R191 --vars docs/m1_r191_vars.json --output -` 渲染再 diff. 字节级一致才算 verification 通过. 这是确保模板设计够灵活的最强证明.

但 R045 (P0 骨架, 同 M1 重复收费) 也属于 M1, 我们也跑一遍 `prompt-fit R045 --vars docs/m1_r045_vars.json` 看输出合理 —— 这是 R191 之外第二个验证点.

## Risks / Trade-offs

**[R1] LLM-auto 模式起草质量不可控** → Qwen3.5-35B 中文起草偏机械, 关键字段可能空泛。Mitigation: --auto 永远后接人审一步, 写盘前 stdout diff + 确认 prompt; 起草 prompt 后允许立即 `--interactive` 微调字段

**[R2] Jinja2 模板的注入风险** → 操作者 vars 如含恶意 jinja 表达式, 渲染时执行。Mitigation: vars 永远走 string 类型, 渲染前 `markupsafe.escape` (实际上 prompt_addon 是写给 LLM 的, 不是 web 输出, 风险低; 但要在 docs 注明 "vars 来自可信源")

**[R3] N-purge 后已有 audit_runs 历史 trace 不可重放** → 14 条 N rule 之前跑过 audit (例如 R001/R141 在 J40808/K_DIRECT_SYNC 跑过), 删 yaml 后 `javert show R001 --patient J40808` 会 fail。Mitigation: 这是已接受的 trade-off (D5 决定); 真要查历史走 sqlite 直查 audit_runs

**[R4] 模板 yaml schema 设计不足 → rollout 中途要改** → 6 模板内容由后续 change 各自填, 若发现 schema 缺字段会反向改本 change. Mitigation: R191 反推时如有缺字段就当场加, rollout 中如发现共性缺失先发 change `extend-template-schema`

**[R5] 6 个空模板占位 vs 不占位** → 占位让 `template list` 立刻显示 6 个, 但内容空。Mitigation: 占位 yaml 含 `template_id` + `name` + `description` + empty `master_prompt` + 空 fields; `template validate` 区分 "fields/prompt 未填" vs "schema 错误"; rollout change 各自完成内容

**[R6] R191 反推会暴露现有 prompt 的实现细节冲突** → 现 R191 prompt 里写了「患者基础诊断必须含肿瘤」这种条件分支, 反推到模板需要 Jinja2 条件。Mitigation: 这正是 D2 选 Jinja2 的原因; 若反推不下来, R191 prompt 本身可能需要小调整

## Migration Plan

**阶段化, 每阶段可独立 git commit**:

1. **Phase 0 (cleanup)**: `rm configs/rules/{R001,...,R312}.yaml` (14 files) + 跑 `javert list` 验证剩 41
2. **Phase 1 (schema)**: 加 jinja2 依赖; 写 `Template` pydantic + `TemplateLoader`; 6 空模板占位文件
3. **Phase 2 (CLI)**: 实现 `template list/show/validate` + `prompt-fit` 三模式
4. **Phase 3 (verification)**: 把 R191 prompt 反推到 M1 模板 + vars, round-trip CLI 验证字节一致
5. **Phase 4 (smoke)**: 在临时 yaml 上跑 prompt-fit --auto 看 Qwen 起草效果 (不入库)

**回滚**: Phase 0 通过 `git checkout configs/rules/` 可恢复; Phase 1-4 全是新文件, 直接 git revert

## Open Questions

- **Q1**: `--auto` 模式起草后是否自动入 `prompt_addon`? 倾向 **不自动**: 起草 → stdout diff → 用户确认 (回车) 才写盘。本期实现默认。
- **Q2**: 模板字段表能否支持 "从另一规则继承默认值"? 例如 M1 的所有规则 fee_category 都是「检查类」, 不想每条 vars 都写。倾向 **本期不做**, 用模板的 `defaults` 块解决。
- **Q3**: `prompt-fit` 写盘时, 是否同时更新 `trigger_keywords` / `suggested_tools` / `expected_signal` 等字段? 模板可以同时填这些。**本期 yes** —— 模板渲染输出是一个完整的「prompt_addon + 三辅字段」字典, 一次性写回 yaml。
- **Q4**: 是否给 template 加 `template_id` 字段到 Rule yaml, 让规则记得自己是从哪个模板派生? 倾向 **是**, Rule schema +1 字段 `derived_from_template: str | None`。便于后续模板演进时批量 re-fit。这是 schema 改动, 算 rule-registry 的 ADDED, specs 里写一下。
