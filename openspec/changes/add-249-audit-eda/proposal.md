## Why

249已安装LIS补丁并完成首例试跑，需要同批续跑全部候选，并在下班前统计本批和工作台既有结果。当前工作台缺少可独立导出的患者、违规类别、金额覆盖和耗时汇总。

## What Changes

- 提供独立EDA shell/Python离线包，可在批跑期间只读生成统计，无需重启或修改已签名补丁。
- 同时输出所选LIS批次和工作台全量最新结果；按患者+规则去重，列出VIOLATION/INCONCLUSIVE/CLEAN、涉及患者、规则和类别。
- 金额明确区分未结构化的违规金额与命中患者当前DETAIL结算总费用；退费按共享映射处理，失败和覆盖人数单列。
- 区分规则累计计算耗时、逐患者处理耗时和批次自然时间跨度；运行中报告注明截止时间和未处理状态。
- 输出不含患者标识/病历原文的中文TXT、CSV及JSON，提供本地合成测试和现场教程。

## Capabilities

### New Capabilities
- `hospital-audit-eda`: 249审计批次及工作台存量只读统计。

### Modified Capabilities
无。

## Impact

仅新增delivery/audit_eda独立工具与测试、运维说明；读取既有javert_audit_runs、javert_vio_review和运行摘要，费用复用hub_source，不新建TB映射。无DDL、规则/模型/已安装代码变更或历史重算；无需新依赖。
