# boost-llm-efficiency — design

## Context

四个事实 (扫描已核验):

1. `prompt_assembler.py:130-134` 把静态工具列表放在动态规则段之后, 公共前缀在规则段断裂 → sglang prefix cache hit 36%.
2. `runner.py:224-254` 本就逐个执行一条消息里的全部 `<tool_call>`, 但 `base.txt` 从没告诉模型可以并列发 → 模型一轮一调用, 8-9 轮往返.
3. `tool_executor.py:114-135` 的 `_cache_lock` 包住整个工具执行 (含 LabLoader 392MB 首建、pandas 全表切片), `--concurrency 5` 下所有线程排一把锁, 只有 LLM 调用真并行.
4. `search_fees.py:90-93` 只给 LLM 合计金额; 而内部 shi_fee 与 hub 契约 (`hub_source.py:94-95,106-109`) 都带 单价/数量/开单科室/开单医师——数据就绪, 缺口只在工具输出层.

约束: prompt 任何变化 = 裁决分布漂移, 项目协议要求 5 患者 dry-run 对照; 62 生产工作台在线, 顺手项不得引入读旧数据风险.

## Goals / Non-Goals

**Goals:**
- cache hit / LLM 轮数 / 并发吞吐三处结构性提速, 批跑时长可量化下降
- fee 明细信号 (量价、科室、医师) 对 LLM 可见, M4/分解/串换科室类规则不再盲跑

**Non-Goals:**
- 复合工具 / `patient_timeline` 工具 (P2, 另立 change)
- exam 跨天重复统计补齐 (与 lab 对称化, 另议)
- 对话历史裁剪 / token 预算 (扫描标低, 不做)
- 规则 yaml 与模板内容改动

## Decisions

### D1. 段顺序只挪不改

`assemble()` 输出段序改为: base → experience → hospital → tools → 规则个性化段. 各段内容逐字不动. 验证锚点: 任意两条规则的 assembled prompt 公共前缀必须延伸到 tools 段末尾 (单测直接断言).

### D2. 一轮多调用是 prompting 修复

`base.txt` 加一小节: "同一轮可并列发出多个 `<tool_call>`, 各自独立返回" + 一个双调用示例. 不改 runner (已支持), 不写复合工具 (YAGNI). 预期副作用: 模型可能一轮铺开全部查询而减少基于中间结果的追问——dry-run 对照重点看深挖类规则 (M2 指征联查) 的 trace 质量.

### D3. per-key compute-once 锁

`_cache_lock` 改两层: 外层短锁只保护 `dict[key, Lock]` 的创建, 内层 per-key 锁包工具执行. 同 key 并发 = 第二个线程等首个算完直接吃缓存 (compute-once 语义保留); 不同 key 完全并行. Loader 单例首建天然被首个触达它的 key 锁住, 其他 key 不再陪等.

替代: 干脆去锁、允许重复计算——LabLoader 首建 392MB 重复算代价太大, 拒.

### D4. fee 行加列, 缺列优雅省略

`search_fees` 行输出追加 `单价×数量` (如 `86.00×3`) 与 `开单科室/开单医师` 字段; 源数据缺列 (老 CSV / 外部院未供) 时整段省略不出现占位符, 不报错. 行锚 `⟨行=i⟩` 与 hit_resolver join 逻辑不动. token 增量 ~20 字符/行, 由 D2 的轮数下降抵消.

### D5. 工作台顺手项最小化

- `build_overview` 挂 `functools.lru_cache` (与 docstring 对齐), 失效挂进 `patient_overview.py` 既有 reset 钩子——不新增失效机制, 避开扫描指出的"双模块 reset 漏调"雷区扩大.
- detail 页不再复跑全表 ROW_NUMBER CTE. (实现修正: 原设计写"按 pid 窄查询", 实施时确认 detail 页 sidebar 渲染全患者列表, 窄查询会砍掉侧栏导航 → 改为路由层短 TTL (10s) 进程缓存 `_sidebar_patients`: detail 页 TTL 内复用列表页结果 (计数由 SSE 客户端增量更新, 秒级陈旧不可见), 列表页 `allow_cached=False` 始终现查并刷新缓存——语义不动.)

## Risks / Trade-offs

- [prompt 变化导致裁决漂移] → 5 患者 dry-run 对照 (J66252/J18906/J90508 + 2 szx), verdict 分布无 V 级意外差异才批跑; 漂移超预期则拆开单独上 D1 (纯布局, 风险最低)
- [一轮多调用减少追问深度] → trace 抽查 M2 类规则; base.txt 措辞保留"可分轮追问"自由度, 不强制一轮打完
- [per-key 锁引入并发 bug] → 单测双线程同 key 只算一次 / 不同 key 不互斥; 62 上 `--concurrency 5` 跑一个患者全量冒烟
- [fee 加列让个别规则 prompt 逼近上下文上限 (R103 前科)] → 与 fix-drug-audit-precision 的 `tool_result_max_chars` 配合; 4xx 快速失败 (harden change) 让超限可诊断

## Migration Plan

1. Mac 改码 → pytest 全绿 (含前缀断言 / 锁并发 / fee 缺列省略)
2. 5 患者 dry-run 对照 (改前后各一轮), 核 verdict 分布 + trace 轮数
3. 62 升级 → 单患者 `--concurrency 5` 冒烟 → 小批 (10 患者) 实测: sglang cache hit、平均轮数、批跑时长, 记录进 docs
4. 指标达预期后作为后续批次默认配置
5. 回滚: 三处互相独立, 可单独 revert

## Open Questions

- 5 患者对照集是否固定为 J66252/J18906/J90508 + szx 2 名 (建议固定, 后续 change 复用同一对照协议)
- cache hit 的采集口径 (sglang 侧 metrics vs 客户端估算) — 62 实测时定
