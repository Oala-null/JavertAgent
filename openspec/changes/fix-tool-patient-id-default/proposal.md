## Why

m1-rollout 组 E 实测 (J66252, 15 条 M1-set, 18.6 min, avg 74.5s/rule) 暴露一个新瓶颈: **LLM 首轮发 tool_call 时常常漏传 `patient_id`**, 导致 `search_fees(category="手术类")` / `search_notes(keyword="关节镜")` 等调用以 TypeError 失败, 浪费 1-2 轮 tool call 重试. 在 4 个工具中, 3 个 (`search_fees`, `search_notes`, `note_diagnosis`) 都 require `patient_id`, drug_indication 唯一不需要.

R208 dry-run 单跑 82s, 但在 audit-patient 中变 130s — 多出来的 ~50s 与首轮漏 patient_id 相关 (sample_audit_patient.md §组 E 观察 2). 把 patient_id 由 LLM 显式传, 改成 Runner 自动注入, **可砍掉每条 ~10s 浪费**.

## What Changes

- `ToolExecutor` 加 `set_patient_context(patient_id)` 和 `clear_patient_context()` 方法
- `ToolExecutor.execute()` 调用工具前: 若该工具 INPUT_SCHEMA 要求 `patient_id` 且 `arguments` 中没传, 用当前 patient_context 注入
- `Runner.audit()` 在开始时 `executor.set_patient_context(patient_id)`, finally 块清空
- `base.txt` 加一行明确说明 `patient_id` 由 Runner 自动注入, LLM 无需在 arguments 里手填 (作为「冗余但安全」的告知)
- 每个工具模块新增 `REQUIRES_PATIENT_ID: bool` 模块常量 (drug_indication=False, 其余=True), 由 `registry.build_executor` 注册时连同 description 一起传给 `ToolExecutor.register`
- `ToolExecutor.register` 签名扩 1 个 kwarg `requires_patient_id: bool = False`

不在本期范围:
- ❌ 不动 4 个工具自身函数签名 / 行为
- ❌ 不改 tool_call 解析协议 (LLM 仍可主动传 patient_id, 会覆盖 default)
- ❌ 不改 cache key 算法 (注入后 arguments 已含 patient_id, cache key 自然带上)
- ❌ 不改 audit_runs schema / store 行为
- ❌ 不涉及并发 (那是 `add-parallel-audit` 的事)

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `audit-engine`: `Runner.audit` 在每次 audit 开始时把 patient_id 推到 executor 上下文, 工具调用时 ToolExecutor 自动 merge 进 arguments; `ToolExecutor` 加 patient_context 状态字段与 `set/clear` 操作 + register 增 `requires_patient_id`

## Impact

- **代码改动 (~60 行 + ~10 行测试)**:
  - `src/javert/tools/tool_executor.py`: +set/clear patient_context + execute 时注入 + register 加 kwarg
  - `src/javert/audit/runner.py`: audit() 开头 set, finally clear
  - `src/javert/tools/{search_fees,search_notes,note_diagnosis,drug_indication}.py`: 各加 `REQUIRES_PATIENT_ID` 常量
  - `src/javert/tools/registry.py`: 注册时传 `requires_patient_id=...`
  - `src/javert/audit/prompts/base.txt`: 加 1-2 行说明 patient_id 由 Runner 注入
- **测试**: 新 `tests/test_tool_executor_patient_context.py` 验证「set→execute 注入→clear→execute 不注入」; `tests/test_runner.py` 加场景「audit 后 executor 状态被清」 + 「LLM 不传 patient_id 仍能正确执行」
- **数据零改动**: 不动任何 rule yaml / vars.json / template yaml
- **回滚**: 单文件级 git revert; 测试足够覆盖新增逻辑
- **下游解锁**: `add-parallel-audit` 共享 executor 时需要保证「同 patient 上下文不被 race condition 污染」, 本期为后续做铺垫 — 因为 audit-patient 整次跑只有一个 patient_id, set_patient_context 调用是幂等的
