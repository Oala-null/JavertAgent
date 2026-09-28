# prompt-efficiency Specification

## Purpose
TBD - created by syncing change boost-llm-efficiency. Update Purpose after archive.
## Requirements
### Requirement: 静态段前置形成跨规则公共前缀

assembled system prompt 的段序 MUST 为: base → experience → hospital → tools → 规则个性化段; 同一部署下任意两条规则的 assembled prompt MUST 共享覆盖到 tools 段末尾的公共前缀 (规则个性化段之后才分叉). 各段内容 MUST 与本 change 之前逐字一致 (只挪位置不改文本).

#### Scenario: 两规则公共前缀覆盖工具段

- **WHEN** 分别为 R191 与 R151 组装 system prompt
- **THEN** 两串的最长公共前缀长度 ≥ 从开头到 tools 段末尾的长度

#### Scenario: 段内容零漂移

- **WHEN** 对同一规则比较挪位前后的 assembled prompt
- **THEN** 两者包含的段集合与各段文本逐字相同, 仅顺序不同

### Requirement: 一轮多 tool_call 对话契约

base prompt MUST 明确告知模型可在同一轮回复中并列发出多个 `<tool_call>` (含示例), 且保留分轮追问的自由度; runner MUST 逐个执行同轮内全部 tool_call 并将各结果分别返回给模型 (既有能力, 契约化锁定).

#### Scenario: 一轮三调用全执行

- **WHEN** 模型在一轮回复中并列发出 3 个合法 `<tool_call>`
- **THEN** 3 个工具全部执行, 3 份结果在下一轮全部可见

#### Scenario: 轮数下降可观测

- **WHEN** 对 5 患者对照集以新 base prompt 重跑 dry-run
- **THEN** 每规则平均 LLM 轮数较改前基线 (8-9 轮) 下降, 且 verdict 分布无 V 级意外差异
