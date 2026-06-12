## Context

m1-rollout 跑完后 `docs/sample_audit_patient.md` 组 E 观察 (2) 已点出: "R208 130s 反而比 dry-run 单跑 82s 慢, 推测原因是 `note_diagnosis/search_fees` 缺 `patient_id` 的首轮调用失败 (LLM 第一次没传 patient_id), 重试浪费 1-2 轮 tool call. 这是 base prompt 的固有问题, 与 M1 模板无关; 可通过 base.txt 加示例或 ToolExecutor 自动注入 patient_id 修复."

`tests/test_audit_patient.py` 的 _audit_script helper 已经在 mock 里手动写了 `"arguments": {"patient_id": "..."}`. 但生产 LLM 不总是这样规范 — 尤其 Qwen3.5-35B 在 multi-rule 跑批时偶尔吞掉 patient_id 字段, 导致工具抛 TypeError(`missing 1 required positional argument: 'patient_id'`), Runner 把错误当成普通 tool 返回写进对话, LLM 才在下一轮加回 patient_id.

每条规则平均浪费 1-2 轮, 每轮 1-2 个 LLM 调用, 单条 audit 浪费 5-15s, 整 15 条加起来 ~2-3 min. 在 18.6 min total 里占 10-15%.

约束:
- 不破坏 LLM 显式传 patient_id 的行为 (兼容 mock 测试 / 现有 prompt)
- 必须保证 cross-patient 不串味 (audit 完成后 executor 不能残留 patient_id)
- 共享 executor 在 `audit-patient --share-tool-cache` 跑批时仍要正确 — 因为整个 batch 都是同一个 patient_id, 这是天然安全的; 但仍需要把 set/clear 严格管理在 audit 边界内
- Cache key 不能因 patient_id 注入与否产生 split (即"显式传" vs "default 注入" 应 hit 同一 cache 键)

## Goals / Non-Goals

**Goals:**
- LLM 不传 patient_id 也能正确调用 search_fees / search_notes / note_diagnosis
- Cache key 在 "显式 patient_id" 与 "default 注入" 两种调用模式下保持一致 (即 merge 发生在 cache lookup 之前)
- 不破坏现有 7 测试场景在 test_audit_patient.py / test_runner.py 的 mock 写法 (它们仍然显式传 patient_id, 行为不变)
- audit 结束后 executor.patient_context == None — 跨 audit 不串味
- drug_indication 不受影响 (不要求 patient_id)

**Non-Goals:**
- ❌ 改 4 个工具签名 (它们仍然 require patient_id 作为函数参数)
- ❌ 改 tool_call 解析协议 (LLM 仍可主动传 patient_id 覆盖 default)
- ❌ 改 base.txt 让 LLM 强制传 patient_id (那是反向方案, 不如 Runner 注入)
- ❌ 并发安全增强 (并发用单独 change 处理; 单线程下 set/clear 是顺序的, 天然安全)
- ❌ 通过 INPUT_SCHEMA 反射判断 (虽然能做, 但增加复杂度; 模块级常量 `REQUIRES_PATIENT_ID` 更直接)

## Decisions

### D1. 注入点: ToolExecutor.execute 内, lookup cache 之前

**选项**:
- (A) Runner 层: Runner 解析完 tool_call, 在调用 executor.execute 前 patch arguments
- (B) ToolExecutor.execute 内: 进入函数后先 merge default args, 再算 cache key
- (C) Tool 函数自身: 改 4 个函数加默认值 (例如 `def execute(patient_id=current_context())`)

**选 B**, 因为:
- A 让 Runner 知道 "哪个工具 require 什么", 违反职责边界 — ToolExecutor 才应该知道工具元信息
- C 把 default 状态散到 4 个文件, 难维护 / 难测; 且每个函数都要 import 一个全局 context
- B 把 patient_id 默认值集中在 ToolExecutor, register 时声明 require, execute 时按需注入; cache key 在 merge 后算, 自然兼容显式传与 default 注入

### D2. patient_context 是 ToolExecutor 字段, 不是参数

**选项**:
- (a) ToolExecutor 加 `_patient_context: str | None` 字段, set/clear 方法
- (b) ToolExecutor.execute 加 `default_patient_id: str | None = None` 参数, 每次调用传入
- (c) 用 contextvars / threading.local

**选 a**, 因为:
- b 让 Runner 每次 execute 都要传 patient_id, 等于把状态外移到 Runner 循环里 — 写法冗余
- c 是并发场景的解, 但本期单线程; 等 `add-parallel-audit` 上来再讨论是否换 contextvars
- a 最简单, 与 ToolExecutor 已有的 `_cache: dict` 同模式 — instance 字段 + 显式 set/clear

并发场景下若 N 线程共享一个 executor + 不同 patient_id, set_patient_context 是 race 的. 但 audit-patient 一整个 batch 都是同一个 patient_id, 一次 set 一次 clear 在最外层, 中间所有 audit 都用同一 patient_id, 天然无 race. 这是 D2 的关键 invariant — 由 audit_patient 编排层保证, 不在 ToolExecutor 内部加锁.

### D3. 工具 require 标识用模块级常量, 不用 INPUT_SCHEMA 反射

**选项**:
- (i) 每个工具模块加 `REQUIRES_PATIENT_ID: bool = True/False`, registry 显式读
- (ii) ToolExecutor 用 INPUT_SCHEMA["required"] 列表反射
- (iii) ToolExecutor 在调用前用 inspect.signature 看函数签名

**选 i**, 因为:
- ii 把 schema 当 "事实", 需要确保 INPUT_SCHEMA 永远准 — 现在 search_fees 的 required 只列 `["patient_id"]`, 但 ToolExecutor 不强制校验 schema, schema 漂移风险
- iii 反射函数签名脆弱 — 4 个工具都用 `**_kwargs` 兜底, signature 里有就有, 没有就没有, 但若改实现 (例如 functools.partial) 反射就坏
- i 显式声明, 一目了然, ide 跳转友好; 4 个模块各 1 行常量

### D4. 注入后 cache key 含 patient_id, 与显式传一致

ToolExecutor.execute 内:
1. 收到 `arguments` dict
2. 若 `name` 对应 `requires_patient_id=True` 且 `"patient_id"` not in arguments 且 `self._patient_context is not None`:
   - `arguments = {**arguments, "patient_id": self._patient_context}` (不修改原 dict, 用新 dict 计算 cache key)
3. `cache_key = f"{name}:{json.dumps(arguments_with_patient, sort_keys=True, ensure_ascii=False)}"`
4. 查 cache → hit 直接返回; miss → 调 `self._tools[name](**arguments_with_patient)` 并写 cache

这保证: LLM 传 `{"category": "手术类"}` 和 `{"patient_id": "J66252", "category": "手术类"}` 都算同一 cache 键, 不会因 "默认注入与显式传" 而 split cache.

### D5. base.txt 加 1-2 行说明

加在 # 工具调用格式 后:

> **patient_id 注入说明**: 三个工具 (`search_fees`, `search_notes`, `note_diagnosis`) 的 `patient_id` 由 Runner 自动注入, 你可以不在 arguments 中显式写它 — 写了也不出错. `drug_indication` 不需要 patient_id.

虽然 Runner 注入是 D4 保证的, 但**在 base.txt 告知 LLM "你可以不写"**, 进一步降低 LLM 反复探索的可能性. 这是双保险的设计 — Runner 注入是 "硬保证", base.txt 是 "软引导".

### D6. patient_context set/clear 时机: audit() 开头与 finally

**选项**:
- (α) Runner.__init__: 不行 — Runner 是每个 patient 跑前可能复用的实例
- (β) Runner.audit() 头: `self.executor.set_patient_context(patient_id)` 写在 reset_cache 之后
- (γ) audit_patient 命令层: 整个 patient run 前 set, 后 clear

**选 β**, 因为:
- γ 假设了 "audit_patient 是唯一调用入口", 但 dry-run / run --pilot / web API 也会调 Runner.audit, 需要每个入口都加 set/clear, 漏一个就出 bug
- β 把 set/clear 紧贴 audit 边界, 单点保证

实现上, β 不能简单写在 try 块外 — 必须用 try/finally 保证异常路径也清; 但更直接的写法是: `set` 在 reset_cache 之后, 整个 audit body 加 try/finally clear. 这样异常逃出去时也清.

### D7. 并发兼容性

audit-patient 的 invariant: 整个 batch 同 patient_id. 即使后续 `add-parallel-audit` 让 N rules 在线程池里跑, set_patient_context(patient_id) 在所有线程上都是同一个值, 不存在 race. 但 clear 时机需要小心 — 若一个线程清了 patient_context, 别的线程的 audit 还在跑, 会出问题.

mitigation: `add-parallel-audit` 会把 set_patient_context 提到 batch 入口 (audit_patient 命令), 一次 set, 全 batch 不 clear, 整个 batch 结束才 clear. Runner.audit 内部的 set/clear 仍保留 (单 audit 调用场景的 dry-run / run --pilot), 但 audit_patient 入口可重复 set (幂等) — 没有 race.

更稳的版本: 改 ToolExecutor.set_patient_context 为 "若 self._patient_context 已是同值则 no-op, 否则 set". 但本期不上, 让 add-parallel-audit 引入并发时再做.

## Risks / Trade-offs

**[R1] cache 命中率下降** → 若注入后 cache key 变 (含 patient_id), 跨 patient 不再 share. Mitigation: 这本来就该如此 — search_fees(category=X, patient_id=A) 和 search_fees(category=X, patient_id=B) 本来就是不同结果, share cache 是 bug. 实测 cache hit 35% 已经是 patient_id 相同跨规则的 cache 命中, 不受影响

**[R2] LLM 现在显式传 patient_id 的 prompt 仍然能跑, 但 cache key 是否一致**? 是. D4 决定: 若 LLM 传 `{"patient_id": "J66252", "category": "手术类"}`, 不会触发 D4 的注入分支 (因为 patient_id 已在), cache key = `{"category":"手术类","patient_id":"J66252"}`. 若 LLM 传 `{"category": "手术类"}`, D4 注入后 arguments = `{"patient_id": "J66252", "category": "手术类"}`, cache key 一致

**[R3] Runner.audit 异常路径可能漏 clear** → 用 try/finally 保护

**[R4] 模块常量 REQUIRES_PATIENT_ID 漂移** → 若后续加新工具, 忘了加常量, default = False (不注入), 行为退化为旧版 — 安全 fallback. registry.build_executor 显式读 require=getattr(module, "REQUIRES_PATIENT_ID", False), 不存在不报错

**[R5] base.txt 文案改动可能让 LLM 反而困惑** → 测试时若发现 dry-run trace 出现 LLM "我现在不传 patient_id 了" 之类奇怪行为, 回退 D5; D4 单独保留 (硬保证)

## Migration Plan

按 4 个 commit-window:

1. **W1**: ToolExecutor 改造 + 4 个工具 REQUIRES_PATIENT_ID 常量 + registry 改造; 跑 `pytest tests/test_tool_executor*` 全绿
2. **W2**: Runner.audit set/clear + try/finally; 跑 `pytest tests/test_runner.py` 全绿
3. **W3**: base.txt 文案改; 跑 `dry-run R208 --patient J66252` 看 trace 看 LLM 是否真的不传 patient_id (软引导生效与否)
4. **W4**: 跑 `audit-patient J66252 --share-tool-cache --rules R045,R047,...,R300` 实测耗时对比组 E

回退: W3 可单独 revert (文案改); W1-W2 须一起 revert (代码改).

## Open Questions

- **Q1**: 若 LLM 显式传错的 patient_id (例如把别人的 ID 抄了进来), 应该覆盖还是 reject? **倾向覆盖** — LLM 传什么就跑什么, Runner 不审 input. 若担心可在 D5 base.txt 加 "不要手填 patient_id, 由系统注入"
- **Q2**: 是否同时给 `INPUT_SCHEMA` 加 `"patient_id_default": "<inject_at_runtime>"` 之类 marker, 让外部 (如 web UI) 也能看出哪些字段是 runtime 注入的? 倾向 **否** — INPUT_SCHEMA 是协议层定义, 不该掺 runtime 状态. 模块常量 REQUIRES_PATIENT_ID 是分离的元数据
- **Q3**: 是否在 register 时同时强制把 INPUT_SCHEMA `required` 列表与 `REQUIRES_PATIENT_ID` 校验一致? 倾向 **否, 本期不做** — 两者都是 source-of-truth 的不同视角, 强校验等于双重定义, 增加运维成本
