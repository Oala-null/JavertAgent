# hub-ba-fallback — delta spec

## ADDED Requirements

### Requirement: 缺首页行患者 per-patient 回退 IH

szx (BA_HOSPS 院区) 患者的诊断/手术取数 MUST 按患者粒度选择数据源: 病案首页 (SYJBK/SYZDK/SYSSK) 有行的患者用首页源; 首页无行的患者 MUST 保留其 IH 侧 (TB_IH_DIAGNOSIS_DETAIL / OPRATION) 诊断与手术行, MUST NOT 因批次内其他患者命中首页而被整体排除.

#### Scenario: 单个患者缺首页不清零

- **WHEN** 同批查询的 szx 患者 A 在 SYJBK 有首页行、患者 B 没有, 且 B 在 IH 侧有诊断/手术数据
- **THEN** A 返回首页源数据, B 返回 IH 源数据, 两者诊断/手术均非空

#### Scenario: 首页命中患者语义不变

- **WHEN** szx 患者在 SYJBK/SYSSK 中有完整首页行
- **THEN** 主诊锚/次诊列表/主手术标志与 2026-07-06 修复后的行为逐字一致 (ZYZD 主诊锚、SYZDK 次诊、SFZYSS 主手术)

#### Scenario: sy 院区不受影响

- **WHEN** 查询 sy (0001) 患者
- **THEN** 仍走 IH/OPRATION 源, 行为与现状一致 (sy 首页库回填不全, 刻意不切)

### Requirement: BA 分支纯逻辑可单测

`hub_source.py` 的 BA 分支 (per-patient 源选择、ZYZD 前缀5码→名解析、主/次诊去重) MUST 有不依赖 142 真连的单元测试覆盖 (stub 查询函数即可测).

#### Scenario: 离线可回归

- **WHEN** Mac 上无 142 连接运行 `uv run pytest tests/ -v`
- **THEN** BA 分支的源选择/解析/去重逻辑测试全部执行并通过
