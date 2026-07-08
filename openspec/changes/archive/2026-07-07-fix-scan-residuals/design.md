# fix-scan-residuals — design

## 背景

2026-07-07 复扫三个 change (fix-drug-audit-precision / harden-onsite-redlines /
boost-llm-efficiency) 后的残余项. 全部小改、零裁决漂移 (不改 LLM 对话行为),
验证靠单测 + 数据核验, 不需 dry-run 对照 — 故与 harden-agent-loop 分开成 change.

## 6 项决策

### 1. hub 空 ZYZD 兜底 (hub_source.fetch_zd)

**现状问题**: `ba_keys` 收所有 SYJBK 行, 含 ZYZD 为空串的行. 空 ZYZD 患者:
IH 行被剔 + main 从空 ZYZD 生成一条空主诊 — 比 per-patient 回退前更糟.

**决策**: `jbk` 查询后立即过滤掉 ZYZD 空串行, `ba_keys` 从过滤后的 jbk 派生.
一处过滤让 ba_keys / main / zyzd_of 全一致: 空 ZYZD 患者不在 ba_keys → 保留 IH.
若整批 jbk 全空 ZYZD → 过滤后 `len(jbk)==0` → 走 `return ih` 分支.

### 2. fees 匹配差异核验 (运维, 无代码)

`scripts/diff_fee_match.py` 在数据快照上跑, 核对新旧命中差异清单只含
「子串误归属修正」, 无「无分隔符 bah 患者丢费用」回归. 结论记入 docs.

### 3. bff 契约通知 (文档, 无代码)

`done.completed` 语义收紧为「落库成功数」+ fail 事件加 `stage` 字段
(harden-onsite-redlines 已改的 SSE). 按 CLAUDE.md「改 SSE 字段先同步 bff」发通知文档.

### 4. build_overview 深拷贝护栏

lru_cache 命中返回共享可变 dict, 现仅 docstring 约束「不得修改」. 拆成
`_build_overview_cached` (带 lru_cache) + `build_overview` 薄壳返回 `deepcopy`.
overview 体积小, 每请求一次 deepcopy 可承受; 消跨请求串数据回归面.
`reset_caches` 改清内层缓存.

### 5. gate 降级 confidence 口径 (runner)

gate 降级 (V→I/C) 时 confidence 保留原 V 值 → 落库「INCONCLUSIVE conf=0.90」误读.
**决策**: `outcome.changed` 时把原 conf 写进 `[gate: ...]` 注记, confidence 归一到 0.5.
下游看到 I/C + conf=0.5 口径自洽, 原值仍可从 reasoning 追溯.

### 6. 必留头部上限 + marker 语义 (runner._truncate + base.txt)

**现状问题 (真实风险)**: 必留头部无上限, 极端诊断数患者 head 超
`tool_result_max_chars` → 整条工具结果超预算 → context 溢出 400 (r103 类).
**决策**: `_truncate` 中 `len(head) > limit` 时对 head 本身硬截 (附「头部超预算」提示).
**权衡**: 硬截 head 可能丢部分 ground truth, 但换来总长有界 (防 400). 提示让模型可感知.
—— 这**推翻** fix-drug-audit-precision 的「头部无条件全留」旧断言, 对应更新其单测.
`base.txt` 加一行解释 `====[必留头部结束]====` 标记语义 (现进 LLM 输入但模型无从知晓).

## 验证协议

- `uv run pytest tests/ -v` 全绿 (+ 空 ZYZD / deepcopy / gate 归一 / head 硬截 测试)
- `scripts/diff_fee_match.py` 差异清单核验 + 记 docs
- 无 dry-run 对照 (不改 LLM 对话行为)
