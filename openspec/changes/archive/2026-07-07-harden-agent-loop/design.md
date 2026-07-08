# harden-agent-loop — design

## Context

2026-07 系统扫描确认 agent loop 有 5 处「静默吞 / 静默丢」路径 (见 proposal). 本组改的是 LLM 对话行为与 prompt-fit 写盘护栏, 按项目协议须 5 患者 dry-run 对照后再批跑.

## Goals / Non-Goals

- **Goals**: repair 路径不丢 tool_call; 裸 JSON 回退不拼废/误取; 畸形 tool_call 有针对性反馈; 工具全失败不解锁裁决; prompt-fit 不静默覆盖人工手改.
- **Non-Goals**: 不动工具语义 / 路由 / gate / 工作台; 不新增 LLM 轮数上限; 不改 `ToolExecutor.execute` 的返回签名 (下游/测试依赖 2-tuple 解包).

## Decisions

### D1. repair 与主循环共用一段工具执行逻辑
把主循环里执行一批 tool_call 的块抽成 `Runner._execute_and_record(content, tool_calls, messages, tool_records) -> int` (返回成功次数). 主循环与 repair 路径都调它 — repair 响应含 tool_call 时执行并 `continue` 回主循环, 不再丢弃直接 INCONCLUSIVE. 抽取即消掉 ~28 行重复, 同时让 repair 续跑成为一行改动.

### D2. 裸 JSON 用括号平衡扫描, 保留「从后往前取首个合法」
新增 `_scan_balanced_objects(text)`: 逐字符括号深度计数, 识别字符串字面量与 `\` 转义, 返回所有顶层 `{...}` 平衡子串. `_parse_verdict_block` 无 fenced 候选时改用它 (替代首`{`到末`}`). 后续「reversed 逐个取首个含合法 verdict 字段」逻辑不变 — 只换候选来源, 不改选取语义.

### D3. 畸形 tool_call 反馈复用 repair 一次机会, 不新增计数
`ToolExecutor` 加 `parse_errors(text) -> list[str]` (对 `<tool_call>` 标签内 `json.loads` 失败收集错误串). runner 在「无 tool_call 无 verdict」分支里, 若 `parse_errors` 非空则把 repair 提示替换为针对性的「tool_call JSON 非法: <err>」; 否则用原通用提示. 二者都走同一个既有 repair 调用 (不加轮数).

### D4. 成功判定放 ToolExecutor, 生产与判定共用常量 — 不改 execute 签名
`ToolExecutor.execute` 仍返回 `(text, cached)` (测试 `test_tool_executor_patient_context.py` 依赖 2-tuple 解包, 改签名代价大). 改为加 `ToolExecutor.is_error_result(text) -> bool` classmethod, 依据 execute 自己生成错误串的两种前缀判定 (未知工具 / 执行失败). `_execute_and_record` 累计成功次数, `_audit_body` 维护 `n_success`; 「放行判定」从 `if not tool_records` 收紧为 `if n_success == 0`. **ponytail**: is_error_result 耦合 execute 的错误串形态, 若新增错误形态需同步更新 (已用注释标注). deadline turn 的 `if tool_records` 保持不变 (终局收敛路径, 天然倾向 INCONCLUSIVE, 不在本 change 收紧范围).

### D5. prompt-fit 护栏用 render_hash, 只存 hash 故无字面 diff
Rule 加 `render_hash: str | None` (末字段, 缺失向后兼容). `rule_writer.update_from_template_render` 写盘时计算并落 `render_hash = sha256(normalize(prompt_addon))`; `compute_render_hash` 与 normalize 规则 (多行补尾 `\n`) 供 prompt_fit_runner 复用. prompt_fit_runner 在写盘前比对: 不一致→拒绝 (exit 1) 除非 `--force`; hash 缺失→warn 放行; 一致→静默覆盖. **ponytail 偏差**: proposal 写「提示 diff」, 但只存 hash 无从产出字面 diff — 改为清晰告知「prompt_addon 已被人工修改, 拒绝覆盖, 用 --force 强制」+ 文件路径, 更诚实且够用.

## Risks / Trade-offs

- repair 续跑消耗 tool budget: 极端下多用 1-2 轮, 但换回被丢弃的裁决, 净正.
- `n_success` 收紧可能把「工具全失败」的少数病例从(错误证据) V/C 推向 INCONCLUSIVE — 这是预期方向.
- 现有已渲染但无 render_hash 的规则 (含 R151) 首次重渲仍会 warn-放行 (无历史 hash 可比), 之后才受保护 — proposal 已接受此向后兼容窗口.

## Migration Plan

- rule yaml 加 `render_hash` 字段向后兼容 (缺失 = 未知来源仅警告). 无需批量回填; 各规则下次 prompt-fit 时自然补齐.
- 按协议 5 患者 dry-run 对照 (含 1 个曾触发 repair/deadline 的病例), 确认无 V 级系统性漂移 + malformed 率不升, 再批跑.

## Open Questions

- 无. (deadline 路径是否也收紧成功判定 — 本 change 明确不动, 留待实测若发现全失败→V 泄漏再议.)
