# harden-agent-loop — tasks

## 1. repair tool_call 可续 + 工具执行抽取 (agent-loop-resilience)

- [x] 1.1 `runner.py` 抽 `_execute_and_record(content, tool_calls, messages, tool_records) -> int` (返回成功次数), 主循环改调它
- [x] 1.2 repair 分支: repair 响应含 tool_call → 调 `_execute_and_record` 并 `continue` 回主循环 (不再直接 INCONCLUSIVE)
- [x] 1.3 单测: repair 回 tool_call 被执行且续跑 / repair 回合法 verdict 仍收敛

## 2. 裸 JSON 括号平衡扫描 (agent-loop-resilience)

- [x] 2.1 `runner.py` 加 `_scan_balanced_objects(text)` (深度计数 + 字符串/转义识别)
- [x] 2.2 `_parse_verdict_block` 无 fenced 时改用平衡扫描 (保留 reversed 取首个合法)
- [x] 2.3 单测: reasoning 含花括号不再拼废 / 多候选取末尾合法块 / 无合法仍 None

## 3. 畸形 tool_call 针对性反馈 (agent-loop-resilience)

- [x] 3.1 `tool_executor.py` 加 `parse_errors(text) -> list[str]`
- [x] 3.2 `runner.py` repair 分支: `parse_errors` 非空 → 针对性提示回传 err (复用同一 repair 调用)
- [x] 3.3 单测: 缺右括号的 tool_call → 提示含解析错误

## 4. 至少一次成功 tool_call 才解锁裁决 (agent-loop-resilience)

- [x] 4.1 `tool_executor.py` 加 `is_error_result(text)` classmethod + 错误串前缀共用常量 (不改 execute 签名)
- [x] 4.2 `runner.py` `n_success` 累计, 放行判定 `if not tool_records` → `if n_success == 0` (含 repair 接受路径)
- [x] 4.3 单测: 全失败不解锁 V (要求重查) / 有一次成功可裁决

## 5. prompt-fit 覆盖护栏 (rule-templating)

- [x] 5.1 `rule.py` 加 `render_hash: str | None` 字段 + `rule_writer._FIELD_ORDER` 末尾追加
- [x] 5.2 `rule_writer.py` 加 `compute_render_hash(prompt_addon)`; `update_from_template_render` 写盘时落 `render_hash`
- [x] 5.3 `prompt_fit_runner.py` 写盘前护栏 (不一致拒绝/缺失警告/一致放行) + `force` 参数; `cli.py` + `commands/prompt_fit.py` 加 `--force`
- [x] 5.4 单测: 手改后拒绝 / --force 绕过 / 缺 hash 仅警告 / 一致静默覆盖 / round-trip hash 稳定

## 6. 验证与对照

- [x] 6.1 `uv run pytest tests/ -v` 全绿 (含本 change 新测试)
- [x] 6.2 5 患者 (J66252/J18906/J90508/J40485/K03341) P0+router 实跑, 隔离 sqlite: 127 裁决 (103 走硬化 loop, 24 precheck 短路) 0 crash/0 failed; **malformed 率 0.0% + conf=0.00 率 0.0%**; 7 个 INCONCLUSIVE 全 legit (conf 0.50-0.60, 真实证据摇摆非 parse 失败), 7 个 V 全 conf 0.90. 严格 git A/B 因并发未提交 precheck 与本 change 在同 hunk 纠缠 + HEAD 落后多个 change 而不可隔离; 改测硬化栈健康度 (temperature 0 下 divergent 路径未被触发 = 与旧码同判, 触发时旧码严格更差[丢裁决/conf 0]). R103 (曾 400 上下文超长) 本次干净收敛
