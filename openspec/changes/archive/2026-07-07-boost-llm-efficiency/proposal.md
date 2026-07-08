# boost-llm-efficiency

## Why

106 患者批跑 ~9h、prompt cache hit 仅 36%、单规则 8-9 轮 LLM 往返、`--concurrency` 被全局锁串行化成假并发——现场演示与批次交付的时长直接受制于此. 2026-07 扫描确认三个近零成本优化, 外加一组「数据已就绪、只差工具层暴露」的审计信号 (单价×数量、开单科室/医师), 后者直接决定 M4 超标准收费 12 条与分解/串换科室类规则的审计上限. 本 change 吃掉路线图 `prompt-cache-optimize` + `tool-call-merge` 两个候选.

## What Changes

- **prompt 静态段前置**: `prompt_assembler.py` 把静态 `tools_prompt` 从规则段之后挪到之前, 公共前缀 = base + experience + hospital + tools, cache hit 36% → 60%+ 预期
- **一轮多 tool_call**: `base.txt` 明示模型可在同一轮并列发多个 `<tool_call>` + 示例 (runner 本就逐个执行同轮全部调用, 纯 prompting 缺口), 8-9 轮 → 2-3 轮预期; 不写复合工具
- **工具缓存真并发**: `tool_executor.py` 全局 `_cache_lock` 改 per-key compute-once 锁, 不同工具调用不再互相排队 (LabLoader 392MB 首建不再挡住所有线程)
- **fee 行明细信号解锁**: `search_fees` 输出行加 单价×数量 + 开单科室/开单医师 (内部 shi_fee 与 hub 契约均已有列), 列缺失时优雅省略——超量/超标准/分解收费从全盲变可审
- **工作台顺手项**: `build_overview` 补真缓存 (docstring 宣称 lru_cache 但没加装饰器, 挂进既有 reset 钩子); detail 页不再重跑全表 ROW_NUMBER CTE (改按患者过滤)

## Capabilities

### New Capabilities
- `prompt-efficiency`: assembled prompt 的静态前缀布局 + 一轮多 tool_call 的对话契约
- `tool-cache-concurrency`: 工具缓存的 per-key 锁语义 (不同 key 不互斥、同 key 只算一次)

### Modified Capabilities
- `data-access`: 费用行明细字段暴露 (单价/数量/开单科室/开单医师, 以 ADDED requirement 形式补充)

## Impact

- 代码: `src/javert/audit/prompt_assembler.py`, `src/javert/audit/prompts/base.txt`, `src/javert/tools/tool_executor.py`, `src/javert/tools/search_fees.py`, `src/javert/web/patient_overview.py`, `src/javert/web/api/routes_workbench.py` + `store/sqlserver_store.py` (detail 查询)
- 行为面: prompt 变化会引起裁决漂移, 须按项目协议 5 患者 dry-run 对照后再批跑; fee 行加列增加少量 token (行宽 +~20 字符), 被轮数下降抵消
- 预期量化: 批跑时长约减半 (轮数 + cache + 真并发三者叠加); 62 部署后以 sglang cache hit 与平均轮数实测
- 不影响: 工具集合、规则 yaml、hit_resolver 锚点 (行锚 `⟨行=i⟩` 序号语义不动)
