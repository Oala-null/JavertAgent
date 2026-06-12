## Context

Bootstrap MVP (`bootstrap-javert-mvp`,已 complete) 把 Javert 的最小闭环跑通:`Rule yaml × 1 → Runner.audit → audit_runs 表`。当前 `javert run R191 --pilot` 是「**规则 → for each 患者**」迭代。

但院方实际场景反过来 —— 入院/出院归档时,审计员拿到一份病案,要回答「这份病案触发哪些违规条款」。这是「**患者 → for each 规则**」。两种模式跑同一台 LLM agent loop,但**驱动维度不同**,需要新的入口。

同时,我们需要给后续技术决策(并行 / 路由 / 模型替换)一个**真实数字依据**。当前对「单 patient × 30 P0 规则」串行总耗时的估计是 7-9 分钟,但没人实际跑过 —— 这次的核心交付就是这个 baseline 数字。

**已知约束**:
- LLM 网关 `192.168.31.62:30000` 共享 GPU(Qwen3.5-35B,sglang),与 zadig_agent 共用;单请求 ~10-20s
- 当前 31 条 P0 规则中,只有 10 条有 yaml(`R141 R143 R146 R151 R153 R156 R160 R161 R185 R191`),其余 20 条还没生成
- `ToolExecutor` 在每条 `Runner.audit` 开头都 `reset_cache()`(`runner.py:97`),跨规则缓存复用率 = 0
- `docs/163规则可行性分析表.csv`(5 列)是专家标注 source-of-truth,目前未进代码

**stakeholder**:操作者(你)是唯一用户;医保专家通过 CSV 给优先级标注,通过聊天告知排除原因(R312 = 工具看不到说明书)。

## Goals / Non-Goals

**Goals:**
- 给定一个 `patient_id`,一条 CLI 命令产出该患者触发了 P0 中哪些规则,及证据链
- 跑出真实 baseline 耗时数据(4 个测试组),写到 `docs/sample_audit_patient.md` 供后续决策
- 跨规则共享 ToolExecutor 缓存作为「可选优化态」,跟「冷启动 baseline」可对比
- 排除规则的管理走 `status: abandoned` 而非硬编码黑名单 —— 任何专家排除都走同一通道
- 不破坏现有 `dry-run` / `run --pilot` / `list` / `mark` / `report` / `show` 的行为(默认参数等同旧行为)

**Non-Goals:**
- ❌ **路由**(诊断/费用/LLM 元判断) —— 故意延后,baseline 不能被路由污染
- ❌ **并行** —— v0 串行;并发跑 30 条留给 `add-parallel-audit`
- ❌ **跨患者批跑** —— 一次只跑一个 patient;批量是后续 change
- ❌ **修改 audit_runs schema** —— 沿用现有表,patient-centric run 写 30 行就是 30 行
- ❌ **Web UI / 报告导出** —— pilot 纯 CLI
- ❌ **prompt_addon 优化** —— 20 条空骨架就是空骨架,跑出 INCONCLUSIVE 是预期;模板填充留给后续 change
- ❌ **CSV 双向同步** —— ingest 是单向 CSV → yaml,后续若 yaml 改了不回写 CSV

## Decisions

### D1. 零路由:v0 全跑 30 条,不基于诊断/费用预过滤

**选项**:
- (A) 零路由,brute force 全跑
- (B) 费用关键词预筛(用现有 `trigger_keywords` 匹配患者 fees,空命中 → 跳)
- (C) 诊断路由(rule yaml 加 `scope_diagnosis` 字段)
- (D) LLM 元判断(一次 LLM 调用挑出适用 rule)

**选 A**,因为:
- baseline 耗时是本期核心交付,**任何路由都会让 baseline 失真**
- P0 31 条中真正能被诊断路由砍掉的少(R191/R260/R300/R313 等极少数挑诊断;大多数挑费用结构)
- 费用关键词路由需要 trigger_keywords 填得好,但 20 条骨架 yaml 关键词为空,等于无效
- 路由决策应当**基于本期数据**做下一步:跑完看 INCONCLUSIVE/CLEAN 分布,再决定上哪种路由

### D2. 优先级字段进 yaml,而非 CSV 全局读

**选项**:
- (a) CSV 是 source-of-truth,启动时 in-memory 解析
- (b) Rule yaml +1 字段 `priority`,从 CSV 一次性 ingest
- (c) 单独 roster 文件 `data/p0_roster.txt`

**选 b**,因为:
- yaml 是 Javert 已有的「规则即文件」模式(进 git, diff 可见,与 `status` 同源)
- priority 与 status 都是规则的状态属性,放一起天然
- ingest 是一次性脚本,后续专家若调整 priority,跑一次同步即可;不需要常驻读 CSV
- 缺点是 yaml 字段多了一个,但兼容性靠 pydantic default 兜底

### D3. R312 排除靠 status,不靠 CLI 参数

**选项**:
- (a) `javert audit-patient ... --exclude R312,R???` 命令行参数
- (b) `javert mark R312 --status abandoned` + `audit-patient` 自动跳 abandoned

**选 b**,因为:
- 专家排除原因是「持久信息」,不应每次跑都靠记忆传 `--exclude`
- abandoned 在 `bootstrap-javert-mvp` 已定义,语义恰好就是「不入审」
- 未来若专家再排除 N 条,流程完全一致(mark abandoned + notes 写原因),无须改 CLI
- 不阻断 `--rules R045,R312` 显式列表的写法(显式列表 > status 过滤)

### D4. ToolExecutor 缓存共享是「可选开关」,默认关

**选项**:
- (a) audit-patient 永远共享 cache(强制优化)
- (b) audit-patient 永远不共享(强制冷启动)
- (c) `--share-tool-cache` flag,默认关,baseline 干净

**选 c**,因为:
- 本期就是要**对比两边、建立 sense**,默认关意味着首次跑就是 baseline
- 共享 cache 的语义边界要测出来(`note_diagnosis` 100% 安全;`search_fees(category=X)` 跨规则命中率低;`search_notes(keyword=Y)` 几乎不复用)—— 在 docs 里写清楚
- Runner 加 `reset_cache` 形参 default=True 是为了不破坏旧行为(dry-run / run --pilot 路径不动)

### D5. 20 条骨架 yaml 现在生成,prompt_addon 空

**选项**:
- (i) 先 `javert init` 补骨架,允许 status=drafting 跑(verdict 多为 INCONCLUSIVE)
- (ii) 只跑现有 10 条已有 yaml 的
- (iii) 等 `add-rule-template-fitter` 做完模板化批量产 yaml 后再回来

**选 i**,因为:
- 本期目标是测耗时不是测准确率,30 条 vs 10 条对**单条耗时**影响有限,对**总耗时**线性影响 —— 30 条数据更有代表性
- 空骨架跑出的耗时偏乐观(LLM 最少 1 次 tool call 即 INCONCLUSIVE,~6-10s),但**这是耗时下限**,真实 baseline 上限会更高 —— docs 里要写明

### D6. ingest 脚本独立到 `scripts/`,不进 cli

**选项**:
- (a) 新 cli 子命令 `javert sync-csv`
- (b) 独立 `scripts/sync_priority_csv.py`,`uv run python scripts/sync_priority_csv.py` 跑

**选 b**,因为:
- CSV ingest 是一次性运维操作,不是日常 CLI(类似数据库 migration,不进主 CLI)
- 避免 CLI 命令面无谓膨胀(本期已经在加 `audit-patient`,够了)
- 后续若 ingest 频繁(每次 CSV 更新都跑),再迁到 CLI

### D7. patient summary 直接 stdout,不存表

**选项**:
- (a) summary 持久化到 `output/patient_summary_<pid>_<ts>.json`
- (b) summary 只 stdout,要回查就走 `audit_runs` 自己 query

**选 b**,因为:
- summary 是「跑完一次的快照」,数据已经存在 `audit_runs` 30 行里,query 还原即可
- 多一种持久化文件等于多一处一致性维护点(规则 changed 后 summary 滞后)
- 后续若需要 patient-level 视图,直接给 `audit_store` 加 `summary_by_patient(pid)` 方法即可,不需要新文件

## Risks / Trade-offs

**[R1] 首次跑的 baseline 偏乐观** → 20 条空骨架的 ~6-10s 比真实带 prompt_addon 的 ~15-30s 短一半。Mitigation: docs 报告里明确写「这是耗时下限,模板填满后总耗时会涨 30-50%」;并在已有 10 条 ready/validated yaml 上单独跑一组,作为「真实态参考点」对照

**[R2] 共享 cache 的命中率可能远低于直觉** → `note_diagnosis(pid)` 单参数确定共享,但 `search_fees / search_notes` 因 LLM 每条规则给的参数不同,命中率经验值可能只有 10-30%。Mitigation: summary 中明确分工具统计命中率;若命中率低,后续考虑做「预热」逻辑(主动按常见 category 跑一遍 fees 缓存)

**[R3] sglang 端单 GPU 长占用** → 串行跑 30 条平均 18s = 9 分钟,期间 zadig_agent 共用 endpoint 会排队。Mitigation: 测耗时阶段只在与 zadig_agent 协调的窗口跑;实际生产化前要做并发预算(后续 change)

**[R4] CSV ingest 覆盖已有 yaml 字段** → 一次性脚本若实现错误,可能把已编辑的 `prompt_addon` 覆盖。Mitigation: ingest 脚本严格只 patch `priority` 字段,不动其他字段;dry-run 模式先打印 diff,确认后才写盘;git 已 track 所有 yaml,误覆盖也可恢复

**[R5] R312 之外的专家排除来源不规范** → 后续可能有更多「专家说用不上」的规则,但散落在聊天记录里。Mitigation: 约定在 `notes` 字段写「医保专家 YYYY-MM-DD: 原因」,这就是审计 log 本身;后续若量大可补 CSV 第 6 列「专家排除原因」

**[R6] priority 默认 P3 会让漏标记的 rule 沉默** → 若 CSV 中漏标 priority,新加的 rule 默认 P3 不会被 P0 跑命中。Mitigation: ingest 脚本对 CSV 中无 priority 标注的 rule 报 warning 而非默默写 P3;list 命令未来可加 priority 列辅助巡检

## Migration Plan

**单向、低风险、可分阶段**:

1. **阶段 1: schema** — 改 `Rule` 加 `priority` 字段,跑 `uv run pytest tests/test_rule.py -v`,确认 default 兼容旧 yaml
2. **阶段 2: ingest** — 跑 `python scripts/sync_priority_csv.py --dry-run` 看 diff;确认后跑 `--write`;`git diff configs/rules/` 复检
3. **阶段 3: R312 abandoned** — `javert mark R312 --status abandoned`,手编辑 `notes` 字段写原因,git commit
4. **阶段 4: runner 改造** — 改 `Runner.audit` 加 `reset_cache`,跑 `uv run pytest tests/test_runner.py -v` 确认 default 行为不变
5. **阶段 5: 新 CLI** — 加 `audit_patient` 命令 + 单测
6. **阶段 6: 实测** — 跑 4 测试组,数据写 `docs/sample_audit_patient.md`

**回滚**:每个阶段独立可 git revert;ingest 写盘前有 `--dry-run`;R312 status 改回 drafting 通过 `--force` 即可。

## Open Questions

- **Q1**: 后续若 `add-rule-routing` 启动,路由层是包在 `audit_patient` 里还是抽到 runner 外? 暂定包在 commands 层,但若多入口都要路由(例如未来 web UI 调用),应抽 service。本期不解决。
- **Q2**: patient summary 是否要包含「该患者已有结果回放」(如果同一 patient 跑过多次,只看最新)? 当前每次跑都新增 30 行,summary 直接报本次新增;若用户想看历史最优,走 `javert report --patient` 通道。本期默认本次 only。
- **Q3**: priority `--rules` 显式列表是否绕过 `abandoned` 过滤? 倾向「显式列表绕过」(允许调试 abandoned 规则),但需要在 summary 里标注「N 条 abandoned 被 --rules 强制纳入」。本期定:`--rules` 绕过,日志显式提示。
