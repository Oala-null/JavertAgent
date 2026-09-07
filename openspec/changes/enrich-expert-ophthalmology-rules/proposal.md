## Why

专家反馈了眼科病例审计中的计价、执行举证、联合检查和用药必要性漏检。现有 R319–R322 已覆盖首批五类线索，但收费别名与预检存在不一致；附图还包含未被专门规则覆盖的情形。

## What Changes

- 补强 R319–R322 的别名召回、计价依据和执行/指征边界，保留原规则 ID。
- 新增 R323 手术项目与执行/价格内涵核对、R324 眼内能量精密治疗数量、R325 眼内穿刺与球后/球旁注射互斥、R326 中医治疗执行举证、RD38 抗青光眼滴眼液联合必要性。
- 新规则先 drafting，经人工编写审核后使用 CLI 标为 ready；ready 仅表示可进入后续人工病例验证，不宣称 validated 或生产部署。
- 按用户要求不新增或运行测试、不跑病例、不调用 LLM；只做静态加载、生成资产一致性和 OpenSpec 严格校验。
- 保存去标识规则说明与人工验收清单，不保存截图姓名/病历原文；同切口折价留作待补政策依据，不制定费率。

## Capabilities

### New Capabilities

- `expert-ophthalmology-rule-pack`: 专家线索驱动的眼科与中医治疗规则、条件裁决及 ready 交接。

### Modified Capabilities

无。复用现有规则、Router、预检、工具和三态裁决合同。

## Impact

用户追加授权：验证选定PDF后，按commit→push→deploy发布62并将结果标记“眼科”。真实启动验证发现R323/RD38行为类别未注册，采用既有标准类别；费用CSV纯数字代码会丢前导零，增补共享CsvLoader字符串读取修复与base/overlay回归。其余引擎及接口语义不变。

影响 `configs/rules/`、两份生成的 Router/mapping JSON、README、规则交接文档和 CHANGES。无引擎、数据库、API、部署配置或依赖变更；保留工作树中既有慢病与短标题改动。
