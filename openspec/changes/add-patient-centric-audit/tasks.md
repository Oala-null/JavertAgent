## 1. Rule schema 加 priority 字段

- [x] 1.1 改 `src/javert/audit/rule.py`: 加 `priority: Literal["P0","P1","P2","P3"] = "P3"` 字段
- [x] 1.2 改 `src/javert/audit/rule_writer.py`: 在写盘字段顺序中插入 `priority` (建议放在 `status` 之后)
- [x] 1.3 扩 `tests/test_rule.py`: 加载带 priority yaml / 加载无 priority yaml (default 兼容) / 非法 priority 值拒绝 三个场景
- [x] 1.4 `uv run pytest tests/test_rule.py -v` 全绿

## 2. CSV ingest 脚本

- [x] 2.1 新 `scripts/sync_priority_csv.py`: 读 `docs/163规则可行性分析表.csv` (用 stdlib `csv` + utf-8-sig)
- [x] 2.2 实现 `--dry-run` 模式: 打印每行的 `would create` / `would patch priority X→Y` / `unchanged` / `warning: missing priority`
- [x] 2.3 实现 `--write` 模式: 对存在的 yaml 走 `rule_loader.load_rule` + `rule_writer.update_priority` 只改 priority 字段
- [x] 2.4 实现「骨架生成」分支: 不存在的 yaml 创建 `Rule(rule_id=Rxxx, priority=..., status="drafting", ...)` 写盘
- [x] 2.5 跑 `uv run python scripts/sync_priority_csv.py --dry-run` 检查 diff 合理
- [x] 2.6 跑 `--write` 实际执行; 确认已有 yaml 只动 priority 字段 (R191.yaml prompt_addon 等保留)
- [x] 2.7 确认 21 条骨架 yaml 已生成 (R045 R047 R069 R077 R109 R112 R116 R118 R119 R129 R130 R131 R208 R219 R220 R226 R228 R260 R300 R312 R313) — 含 R312 共 21 条 (R312 下一步 mark abandoned)

## 3. R312 标记 abandoned

- [x] 3.1 跑 `uv run javert mark R312 --status abandoned`
- [x] 3.2 手编辑 `configs/rules/R312.yaml` 的 `notes` 字段写专家排除原因 + 解锁条件
- [ ] 3.3 git commit 这次 status 变更 — **留给操作者**, 整个 change apply 完一并 commit 更合理

## 4. Runner 加 reset_cache 形参

- [x] 4.1 改 `src/javert/audit/runner.py:audit`: 加 `reset_cache: bool = True` kwarg, 把 `self.executor.reset_cache()` 包进 `if reset_cache`
- [x] 4.2 扩 `tests/test_runner.py`: 加场景「同 executor 跨两次 audit, 第二次 reset_cache=False 时 ToolCall.cached=True」(+ 默认行为不变的对照测试)
- [x] 4.3 确认 dry-run / run --pilot 行为未变 — `test_reset_cache_true_default_invalidates` 已证明 default=True 时行为与旧版完全等价; live dry-run 留待 G8 端到端验证

## 5. list 命令加 priority 列

- [x] 5.1 改 `src/javert/commands/list_rules.py`: tabulate 输出加 `priority` 列 + 末尾加优先级/状态分布汇总行
- [x] 5.2 扩 `tests/test_cli.py`: list happy path 验 stdout 含 `priority` 表头 + 汇总行

## 6. audit-patient CLI

- [x] 6.1 新 `src/javert/commands/audit_patient.py`: `run_audit_patient(patient_id, priority, rules_arg, share_tool_cache)` 主函数
- [x] 6.2 规则集合解析: 显式 `--rules` > priority 过滤; 默认排除 `status==abandoned` (除非 `--rules` 显式纳入)
- [x] 6.3 单 `ToolExecutor` 实例化, 整个 patient run 共享; `share_tool_cache=False` 时每条 audit 走 `reset_cache=True`, `True` 时走 `reset_cache=False`
- [x] 6.4 顺序循环: `Runner.audit(rule, pid, reset_cache=...)` + `persist_one(result, rule, triggered_by="cli-audit-patient")`
- [x] 6.5 进度行输出到 stderr: `[i/N] Rxxx → V conf=X.XX Y.Ys tc=N (cached M)`
- [x] 6.6 mid-batch `LlmUnavailableError` 处理: 已完成的 result 已持久化, 打印 `LLM failed at` + exit code 1
- [x] 6.7 patient summary 计算: total_ms / avg / p50 / slowest-3 / verdict counts / tool_cache_hits / hit_rate
- [x] 6.8 summary block 打印到 stdout (格式见 specs/cli/spec.md scenario)
- [x] 6.9 cli.py 注册 `@main.command("audit-patient")` + click 参数 (`patient_id`, `--priority`, `--rules`, `--share-tool-cache`)

## 7. audit-patient 单测

- [x] 7.1 新 `tests/test_audit_patient.py`: mock LLM 跑 3 条规则, 验返回 3 个 AuditResult, summary 字段齐
- [x] 7.2 测试场景: abandoned 默认跳过 (4 条 P0 中 1 条 abandoned, 实跑 3 条, R154 P1 不入选)
- [x] 7.3 测试场景: `--rules R045,R312` 强制纳入 abandoned, 实跑 2 条, summary 标"force-included abandoned: R312"
- [x] 7.4 测试场景: `--share-tool-cache` 模式下两条规则共用 executor, 第二条的 note_diagnosis 命中 cached
- [x] 7.5 测试场景: `--priority P9` Click 拒绝, exit 2 (+ priority 合法但无匹配规则也 exit 2)
- [x] 7.6 测试场景: 第 2 条规则 raise LlmUnavailableError, 第 1 条已落 store, 进程 exit 1
- [x] 7.7 `uv run pytest tests/test_audit_patient.py -v` 全绿 (6/6 passed)

## 8. 实测耗时 (核心交付 — 4 组实测留给操作者)

- [x] 8.1 确认 sglang `192.168.31.62:30000` 可达 (`curl -s http://192.168.31.62:30000/v1/models` 返回正常)
- [x] 8.1.5 live smoke test: `javert audit-patient J66252 --rules R141` → 73.5s, verdict CLEAN, 5 tool calls — wiring 通
- [x] 8.2 测试组 A: J66252 冷启动 — **实测 2491.7s (41.5 min), avg=83.1s, V=1/C=28/I=1**; 已入 `docs/sample_audit_patient.md` 组 A 节, 含慢规则 Top-3 / R220 VIOLATION 异常 / R228 INCONCLUSIVE 早退 / 修正后的预期 (骨架 ≠ 短耗时)
- [ ] 8.3 测试组 B: `uv run javert audit-patient J66252 --share-tool-cache` — **操作者执行**
- [ ] 8.4 测试组 C: `uv run javert audit-patient J18906 --share-tool-cache` — **操作者执行**
- [ ] 8.5 测试组 D: `uv run javert audit-patient J13365 --share-tool-cache` — **操作者执行**
- [x] 8.6 分析节框架已就位 (基于组 A 已得 5 项结论 + 6 个候选 change 排序); 组 B/C/D 后再 refine
- [x] 8.7 docs 顶部 caveat 已修正: 骨架 yaml 反而**最慢** (R219=172s), 推翻原"骨架短"预期

## 9. 文档

- [x] 9.1 更新 `CLAUDE.md` 的「常用命令」节: 加 `javert audit-patient` + sync_priority_csv 示例
- [x] 9.2 更新 `CLAUDE.md` 的「当前阶段标记」节: 标 ✅ "patient-centric 入口实装"; baseline 实测仍 🚧
- [x] 9.3 README.md 更新: 7 个 → 8 个子命令; 增加「一次性脚本」节; 路线图加 `add-patient-centric-audit` 完成 / `add-rule-routing` 候选

## 10. 验收

- [x] 10.1 `openspec validate add-patient-centric-audit --strict` 通过
- [x] 10.2 全部单测通过 (`uv run pytest tests/` → 87 passed)
- [x] 10.3 `javert list` 显示 55 条规则 (原 34 + 新增 21 = 55), 含 priority 列 + 末行汇总 (P0:31 / P1:3 / P2:10 / P3:11, abandoned:1)
- [x] 10.4 `javert audit-patient J66252` 端到端完整跑通 (组 A: 30 行进度 + summary block, 2491.7s 总耗时)
- [ ] 10.5 `docs/sample_audit_patient.md` 含 4 组实测 — **组 A 已入**, B/C/D 待操作者跑
- [x] 10.6 `output/audit.sqlite` 已含 30 条本次 audit-patient 产生的记录 (J66252 today, `triggered_by="cli-audit-patient"`); 组 B/C/D 跑完会再加 ~90 行
