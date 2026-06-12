## 1. ToolExecutor 改造

- [x] 1.1 改 `src/javert/tools/tool_executor.py`: 加 `_patient_context: str | None = None` 字段
- [x] 1.2 加 `set_patient_context(patient_id: str)` 方法 (允许 None 也允许同值幂等)
- [x] 1.3 加 `clear_patient_context()` 方法 (设回 None)
- [x] 1.4 加 `_requires_patient_id: dict[str, bool]` 字段
- [x] 1.5 改 `register(name, func, description="", requires_patient_id=False)`: 把 requires 标记记到 dict
- [x] 1.6 改 `execute(tool_call)`: 进入函数后, 若 `requires_patient_id.get(name, False)` 且 `arguments` 中没 patient_id 且 `_patient_context` 非空, 用新 dict merge; cache key 用 merge 后的 arguments 算
- [x] 1.7 验证: 现有 `tests/test_tools.py` 全绿 (现有测试 LLM 都显式传 patient_id, 不触发注入分支)

## 2. 4 个工具模块加 REQUIRES_PATIENT_ID 常量

- [x] 2.1 `src/javert/tools/search_fees.py`: 顶部 `REQUIRES_PATIENT_ID = True`
- [x] 2.2 `src/javert/tools/search_notes.py`: 顶部 `REQUIRES_PATIENT_ID = True`
- [x] 2.3 `src/javert/tools/note_diagnosis.py`: 顶部 `REQUIRES_PATIENT_ID = True`
- [x] 2.4 `src/javert/tools/drug_indication.py`: 顶部 `REQUIRES_PATIENT_ID = False`

## 3. registry.build_executor 改造

- [x] 3.1 改 `src/javert/tools/registry.py`: register 4 个工具时传 `requires_patient_id=<module>.REQUIRES_PATIENT_ID`
- [x] 3.2 用 `getattr(module, "REQUIRES_PATIENT_ID", False)` 兜底 (新工具忘加常量也不崩)

## 4. Runner.audit 改造

- [x] 4.1 改 `src/javert/audit/runner.py:audit`: 在 `reset_cache` 之后立刻 `self.executor.set_patient_context(patient_id)`
- [x] 4.2 把 audit body (从 base_prompt load 到 return result) 包进 try/finally; finally 内 `self.executor.clear_patient_context()`
- [x] 4.3 确认 finally 不影响异常传播 (LlmUnavailableError 仍 raise 出去); 测试 `test_audit_finally_clears_on_exception` 覆盖

## 5. base.txt 文案

- [x] 5.1 改 `src/javert/audit/prompts/base.txt`: 在 # 工具调用格式 节后加 1 段
- [x] 5.2 文案落地: "**patient_id 注入说明**: 三个工具 (`search_fees`, `search_notes`, `note_diagnosis`) 的 `patient_id` 由 Runner 自动注入, 你可以不在 arguments 中显式写它 — 写了也不出错. `drug_indication` 不需要 patient_id."

## 6. 单测

- [x] 6.1 新 `tests/test_tool_executor_patient_context.py` (10 测试):
  - test_register_records_requires_patient_id
  - test_execute_injects_patient_id_when_missing
  - test_execute_does_not_override_explicit_patient_id
  - test_execute_no_inject_for_tools_not_requiring_patient_id
  - test_cache_key_unified_across_injection_modes
  - test_clear_patient_context_disables_injection
  - test_set_patient_context_idempotent
  - (+ 3 并发 cache 安全测试, 见 add-parallel-audit/tasks 3.2)
- [x] 6.2 扩 `tests/test_runner.py` (+4 场景):
  - test_audit_manage_context_true_sets_and_clears
  - test_audit_manage_context_false_preserves_pre_set_value (并发分支用法)
  - test_audit_finally_clears_on_exception
  - test_audit_injects_patient_id_when_llm_omits_it (端到端验证注入生效)

## 7. 端到端 sanity check

- [x] 7.1 跑 `uv run pytest tests/ -v` 全绿 (157 passed, 无回归)
- [x] 7.2 跑 `uv run javert dry-run R208 --patient J66252` 看 trace: LLM 多次发 `search_fees({"category":"X"})` / `search_notes({"section":"X"})` 无 patient_id, 工具仍正确执行无 TypeError — **注入生效验证 ✓**

## 8. 文档收尾

- [x] 8.1 `openspec validate fix-tool-patient-id-default --strict` 通过
- [x] 8.2 与 add-parallel-audit 一起验收: 组 F 实测 tool_calls 总数 68→40 (↓41%), R208 单条 130s→71s (↓45.6%), 详见 `docs/sample_audit_patient.md` 组 F

## 9. 验收

- [x] 9.1 spec scenario "patient_id auto-injection on missing arg" 通过
- [x] 9.2 spec scenario "explicit patient_id is preserved" 通过
- [x] 9.3 spec scenario "drug_indication unaffected" 通过
- [x] 9.4 spec scenario "cache key unified across injection modes" 通过
- [x] 9.5 spec scenario "context cleared after audit" 通过
- [x] 9.6 spec scenario "finally clears on LlmUnavailableError" 通过
