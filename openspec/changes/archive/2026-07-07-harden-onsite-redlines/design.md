# harden-onsite-redlines — design

## Context

七项均出自 `docs/系统扫描与优化方案_2026-07.md`「安全与合规」「主线二配套 loop 修复」「本轮增量」三节, 带 `文件:行号` 证据. 共性: 都是小改、都在进院/现场对接的暴露面上. 2fd3cdc 已修的三项 (SQL 密码出源码 / /api/audit 入 PROTECTED / CLI session fail-fast) 不重复.

约束: 2C BFF 消费 run-batch SSE, 契约变化只加不改; 62 生产在跑, 改动需可平滑重启; 142 索引 SQL 必须幂等.

## Goals / Non-Goals

**Goals:**
- 进院红线清零: PHI 有痕、session 不可伪造、丢数窗口关闭
- 现场批跑鲁棒: 单点失败不放大、错误分类正确
- szx hub 路径不丢数不掉速

**Non-Goals:**
- hub `in_clause` 改绑定参数 (P3 顺手项, 另清)
- SSE 多 worker 方案 (上多 worker 前再做)
- audit 独立线程池 (本 change 不动, 观察后定)
- 工具"≥1 次成功调用"门槛 (行为变化需对照评估, 放进 fix-drug-audit-precision 之后观察是否仍需)

## Decisions

### D1. PHI 审计复用 log_action + slowapi 限流

raw 端点记 `javert_audit_logs` (username / patient_id / 来源 csv|hub), 与 `/export` 同一套. 限流用已引入的 slowapi (login 已有 5/min 先例), raw 给宽松档 (如 30/min per session)——专家逐个点开病历不受影响, 脚本枚举 4700 患者被卡. 替代: 只留痕不限流——hub 面太大, 拒.

### D2. session guard 进 create_app

fail-fast 判定挪进 `create_app()`: `with_mssql=True` (生产形态) 且 secret 为默认值时抛错拒绝启动; `--no-mssql` 本地 dev 保持宽松. cli.py 原检查保留或删除以 create_app 为准 (单一来源). 替代: 文档约束"别用 uvicorn 直起"——不可执行, 拒.

### D3. run-batch persist 失败发 fail

`routes_audit.py` run-batch: persist 成功才发 `result`; persist 抛错则发 `fail` 事件 (`{rule_id, stage: "persist", error}`), 不发 `result`. 未知 rule_id 在解析请求时逐条发 `fail` (`{rule_id, stage: "unknown_rule"}`). 事件结构对齐现有 fail 形态 (只加 stage 字段级信息, 不动既有字段). bff 同步: 通知 + bff README 契约段更新由 bff 侧 change 承接.

### D4. fees 两级精确匹配

`csv_loader.py` 索引 `patient_id in k` 改: ① `k == pid` 精确; ② 复合键末段精确 (`k` 按合成键规则 `{hospital_code}-{patient_id}` 拆末段等值, 兼容尾随空格 strip). 与 `patient_overview.py` 的 pid 口径靠拢 (扫描"overlay 两套口径"问题顺带收敛一半). 风险: 若存在依赖子串宽匹配的历史调用方 (如带前缀查询), 用测试锁定 J66252/szx 复合键两类都命中.

### D5. per-patient IH 兜底

`hub_source.py` fetch_zd/fetch_ss (BA_HOSPS 分支): 排除 IH 行的集合从"全部请求患者"改为"**在 BA 侧真有行**的患者" (`NOT IN (有 BA 行的 pid)`), BA 无行的患者保留其 IH 诊断/手术. 现有 `len(jbk)==0` 全空回退自然被 per-patient 语义覆盖. 补纯逻辑单测: stub `q()`, 覆盖 BA 分支/前缀5解析/主次诊去重 (扫描指出的零覆盖).

### D6. BA 四表索引

`create_data_hub_indexes.sql` 幂等追加: SYJBK(YLJGYQDM,SYXH) / SYZDK(YLJGYQDM,SYXH) + (ZDDM) / SYSSK(YLJGYQDM,SYXH) / SYSSK_EXT(YLJGYQDM,SYXH,SSXH). 142 上执行后量一次单患者 hub 首查耗时 (基线 0.31s, 防止 BA 分支把索引收益吃回去).

### D7. 4xx 快速失败 + 串行不中断

`llm_provider.py` 重试循环: HTTP 4xx 且非 429 → 立即 raise (错误信息带 status + body 摘要, R103 类问题可诊断). `audit_patient.py` 串行分支: 单规则异常记 failed + 日志, `continue` (与并发模式行为对齐).

## Risks / Trade-offs

- [限流误伤专家正常使用] → 档位宽松 (30/min) + 429 提示语明确; 现场如误伤一行配置可调
- [fail 事件 bff 未升级前被忽略] → 至少不再错发 result, 数据不一致窗口关闭; bff 升级前行为等价"该条无结果", 与现状相比无回退
- [fees 匹配收紧漏掉某种历史键形态] → 上线前用 62 现网 patient 集合跑一遍匹配 diff (旧逻辑 vs 新逻辑命中集合对比), 差异逐条核
- [create_app fail-fast 让某个现存启动路径直接启不来] → 这正是目的; 部署 runbook (`docs/deployment_192_62.md`) 同步说明

## Migration Plan

1. Mac 改码 → pytest 全绿 (含新增 hub BA 分支单测 / fees 匹配 diff 测试)
2. fees 新旧匹配集合 diff 脚本跑 62 数据快照, 确认无意外差异
3. 62 升级 src → systemd 重启 → 冒烟: 登录/点开患者/raw 端点 (看审计日志落行)/跑一条 run-batch
4. 142 执行 BA 索引 SQL (幂等), 量 hub 首查耗时
5. 通知 2C bff 维护者: SSE 新增 fail 事件语义 (stage=persist/unknown_rule)
6. 回滚: git revert + 62 重部; 索引无需回滚

## Open Questions

- raw 端点限流档位 (30/min 是初值, 现场按专家实际点击密度调)
- cli.py 原 session 检查删除还是保留双保险 (倾向删除, create_app 单一来源)
