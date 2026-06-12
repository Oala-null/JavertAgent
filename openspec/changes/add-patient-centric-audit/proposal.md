## Why

Javert 当前的运行模式是「规则驱动」:选一条规则 R191,然后对 50 个 pilot 患者批跑;迭代闭环也以单条规则为单位。但院方实际审计场景反过来 —— 病案一份份过,每份要回答「这位患者触发了哪些违规条款」。我们需要把审计入口翻转成**患者驱动**:输入一个住院号,自动把全部 P0 优先级规则 (排除 R312) 过一遍,产出「触发清单 + 证据链」。

短期目标也很具体:**先测一个 baseline —— 一个患者跑 30 条规则要多久**。这个数字直接决定后续要不要做路由 / 并行 / 模型替换。所以 v0 故意**不做路由**,先把端到端跑串起来拿耗时数据。

## What Changes

- 新 CLI 子命令 `javert audit-patient <patient_id>`:一次性把指定优先级(默认 `P0`)的所有规则在该患者身上跑一遍,产出患者级 summary(总耗时 / 各 verdict 计数 / 慢规则 top-3 / 工具缓存命中率)
- `Rule` pydantic 模型 +1 字段 `priority: Literal["P0","P1","P2","P3"]`,默认 `"P3"`(最低)
- 一次性 ingest 脚本:读 `docs/163规则可行性分析表.csv` 把 `priority` 灌进所有现有 yaml,并为 P0 中暂无 yaml 的 20 条生成骨架(`status: drafting`,`prompt_addon` 留空)
- `Runner.audit()` 加 `reset_cache: bool = True` 形参 —— 默认保留旧行为不变;新 CLI 通过 `reset_cache=False` 在同患者的多规则间复用 ToolExecutor 缓存
- R312 通过 `javert mark R312 --status abandoned` 标记;`audit-patient` 自动跳过 `status: abandoned` 的规则 —— 排除逻辑由 status 驱动,不写硬编码黑名单
- 测耗时:四个测试组(J66252 冷启动 / J66252 共享缓存 / J18906 / J13365),报告写到 `docs/sample_audit_patient.md`
- **v0 不做路由**(brute force 跑全 30 条) —— 故意如此,baseline 数据不能被路由污染;路由策略留给后续 change

## Capabilities

### New Capabilities

(none — 在现有 capability 上扩展)

### Modified Capabilities

- `rule-registry`: 给 `Rule` schema 加 `priority` 字段;扩 `rule_init` 支持「从 CSV ingest」分支(已有的从 0325.xls ingest 不变)
- `audit-engine`: `Runner.audit` 增加 `reset_cache` 形参以支持跨规则共享 ToolExecutor 缓存;新增患者级编排逻辑(规则集合 → 跳 abandoned → 顺序跑 → 患者 summary)
- `cli`: 新增 `audit-patient` 子命令(原 7 子命令保持不变)

## Impact

- **代码改动**(估约 200 行新代码 + ~10 行 patch):
  - 改 `src/javert/audit/rule.py`(+1 字段)
  - 改 `src/javert/audit/runner.py`(+1 形参,if-guard reset_cache)
  - 改 `src/javert/cli.py`(+1 subcommand 注册)
  - 新 `src/javert/commands/audit_patient.py`
  - 新 `scripts/sync_priority_csv.py`(一次性 ingest 工具)
- **数据改动**:
  - `configs/rules/*.yaml` 全部加 `priority` 字段(in-place 编辑)
  - 新增 20 条骨架 yaml(R045 R047 R069 R077 R109 R112 R116 R118 R119 R129 R130 R131 R208 R219 R220 R226 R228 R260 R300 R313)
  - `configs/rules/R312.yaml`:`status` 改为 `abandoned`,`notes` 写专家排除原因(医保专家 2026-05-12 标注: 此条 PILOT 不入审,工具看不到说明书)
- **存储**:沿用现有 `audit_runs` 表,**无 schema 变更**;一次 `audit-patient` 写入 30 行(每条规则一行,与现 `run --pilot` 行为一致)
- **测试产出**:
  - 新 `tests/test_audit_patient.py`:CLI happy path + priority 过滤 + abandoned 跳过 + 缓存共享语义
  - 实测报告 `docs/sample_audit_patient.md`:4 组测试的真实耗时 / 调用次数 / 缓存命中率表格
- **零外部依赖变化**:不动 sglang endpoint(192.168.31.62:30000)、不动 142 SQL Server、不动 zadig_agent;LLM 网关与 csv 数据源都沿用现有
- **后续 change 解锁**:本期 baseline 出来后,可启动 `add-rule-routing`(基于真实数据决定是否上费用关键词/诊断路由)、`add-parallel-audit`(并发跑 30 条提速) 等优化型 change
