# fix-scan-residuals

## Why

2026-07-07 复扫确认三个 change (fix-drug-audit-precision / harden-onsite-redlines / boost-llm-efficiency) 16 项修复全部落地、562 测试全绿, 但实现审查发现一组残余项: 1 个「比修复前更糟」的数据边界 (hub 空 ZYZD 患者诊断被清空)、2 个上线前确认项 (fees 匹配差异核验 / bff 契约通知)、3 个护栏与口径项. 均为小改且**零裁决漂移风险** (不改 LLM 对话行为), 与 harden-agent-loop 分开成 change 正是因为验证协议不同——本 change 只需单测 + 数据核验, 不需 dry-run 对照.

## What Changes

- **hub 空 ZYZD 兜底**: `hub_source.py` fetch_zd 的 `ba_keys` 只收 ZYZD **非空**的 SYJBK 行——SYJBK 有行但主诊为空串的患者回退 IH 诊断 (现状: IH 被剔 + 生成一条空主诊, 比 per-patient 回退前更糟). 补两条测试: 空 ZYZD 回退 IH / jbk 有行但 zdk 零行只剩主诊 (现状留档)
- **fees 匹配差异核验**: 在 62 快照上跑 `scripts/diff_fee_match.py`, 核对新旧命中集合差异清单**只含「子串误归属修正」**, 无「无分隔符 bah 患者丢费用」回归; 结论记入 docs (这是 csv_loader 两级精确匹配的最后一道安全网)
- **bff 契约通知**: `done.completed` 语义已由「审计成功数」收紧为「落库成功数」+ fail 事件新增 `stage` 字段——按 CLAUDE.md「改 SSE 字段先同步 bff」约定, 向 2C `bff` 消费端发契约变更说明 (文档级, 无代码)
- **build_overview 共享 dict 护栏**: lru_cache 命中返回共享可变对象, 现仅靠 docstring 约束「不得修改」; 改为返回深拷贝 (每请求一次 deepcopy, overview 体积小可承受), 消除跨请求串数据的回归面
- **gate 降级 confidence 口径**: verdict_gate 降级 (V→I/C) 时 confidence 保留原值, 落库出现「INCONCLUSIVE conf=0.00/0.90」误读; 降级时将 confidence 归一到 0.5 并在 reasoning 的 `[gate: ...]` 注记原值, 下游口径自洽
- **必留头部上限 + marker 语义**: `_truncate` 的必留头部现无上限, 极端诊断数患者会超 `tool_result_max_chars`; head 超预算时对 head 本身也按 limit 硬截 (附截断提示). `base.txt` 加一行解释 `====[必留头部结束]====` 标记语义 (现每次进 LLM 输入但模型无从知晓含义)

## Capabilities

### Modified Capabilities
- `hub-ba-fallback`: BA 覆盖判定收紧——SYJBK 行的 ZYZD 为空不构成 BA 主诊覆盖, 该患者回退 IH
- `tool-result-integrity`: 必留头部本身受总预算约束 (head 超限也截并可感知); marker 语义进 base prompt
- `verdict-gate`: 降级裁决的 confidence 归一口径 (0.5 + reasoning 注记原值)

## Impact

- 代码: `src/javert/data/hub_source.py`, `src/javert/web/patient_overview.py`, `src/javert/audit/verdict_gate.py` (或 runner 降级处), `src/javert/audit/runner.py` (`_truncate`), `src/javert/audit/prompts/base.txt` (一行), 对应测试
- 运维: 62 上跑一次 `diff_fee_match.py` 并留档差异结论
- 外部契约: 2C bff 收到 run-batch 语义变更通知 (只通知, 本 change 不再改 SSE)
- 不影响: LLM 对话行为、路由、规则 yaml——无需 dry-run 对照
