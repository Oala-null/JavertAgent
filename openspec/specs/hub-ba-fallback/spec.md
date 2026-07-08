# hub-ba-fallback Specification

## Purpose
TBD - created by syncing change harden-onsite-redlines. Update Purpose after archive.
## Requirements
### Requirement: 缺首页行患者 per-patient 回退 IH

szx (BA_HOSPS 院区) 患者的诊断取数 MUST 按患者粒度选择数据源: 病案首页 SYJBK
中**主诊 ZYZD 非空**的患者用首页源; SYJBK 无行、或有行但 ZYZD 为空串的患者
MUST 保留其 IH 侧诊断行, MUST NOT 因批次内其他患者命中首页而被整体排除,
MUST NOT 生成一条空主诊记录. 手术取数维持 SYSSK per-patient 源选择.

#### Scenario: 单个患者缺首页不清零

- **WHEN** 同批查询的 szx 患者 A 在 SYJBK 有非空 ZYZD、患者 B 没有 SYJBK 行, 且 B 在 IH 侧有诊断数据
- **THEN** A 返回首页源数据, B 返回 IH 源数据, 两者主诊均非空

#### Scenario: SYJBK 有行但 ZYZD 为空 → 回退 IH

- **WHEN** szx 患者在 SYJBK 有行但 ZYZD 为空串, 且该患者在 IH 侧有诊断数据
- **THEN** 该患者返回 IH 诊断 (不被剔), 且**不**出现一条空 code/空 name 的主诊行

#### Scenario: SYJBK 有主诊但 SYZDK 零行 (现状留档)

- **WHEN** szx 患者 SYJBK 有非空 ZYZD 但 SYZDK 无次诊行
- **THEN** 该患者只返回一条主诊 (maindiag_flag=1), 无次诊行

#### Scenario: 首页命中患者语义不变

- **WHEN** szx 患者在 SYJBK 有非空 ZYZD 首页行
- **THEN** 主诊锚/次诊列表/主手术标志与 2026-07-06 修复后的行为逐字一致

#### Scenario: sy 院区不受影响

- **WHEN** 查询 sy (0001) 患者
- **THEN** 仍走 IH/OPRATION 源, 行为与现状一致 (sy 首页库回填不全, 刻意不切)

### Requirement: BA 分支纯逻辑可单测

`hub_source.py` 的 BA 分支 (per-patient 源选择、ZYZD 前缀5码→名解析、主/次诊去重) MUST 有不依赖 142 真连的单元测试覆盖 (stub 查询函数即可测).

#### Scenario: 离线可回归

- **WHEN** Mac 上无 142 连接运行 `uv run pytest tests/ -v`
- **THEN** BA 分支的源选择/解析/去重逻辑测试全部执行并通过
