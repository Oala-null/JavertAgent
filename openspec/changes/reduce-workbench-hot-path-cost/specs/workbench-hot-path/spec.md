## ADDED Requirements

### Requirement: 响应与事件传输
系统 SHALL 压缩支持 gzip 的大页面响应，且不压缩或缓冲 SSE。

#### Scenario: 大页面与事件
- **WHEN** 请求声明 gzip 并读取大HTML和SSE
- **THEN** HTML 为gzip，事件流保持原内容类型和未压缩事件

### Requirement: 详情来源查询复用
系统 SHALL 在短缓存周期内复用同患者的已知来源查询，并限制缓存规模。

#### Scenario: A B A切换
- **WHEN** 配置了Hub profiles且患者有已知来源标签，在30秒内依次打开A、B、A
- **THEN** 来源查询共2次，同次详情的来源选择一致

### Requirement: 一份费用切片只预处理一次
系统 SHALL 在一个请求的多规则和多收费证据间复用惰性费用预处理，保持原输出。

#### Scenario: 多条收费证据
- **WHEN** 同患者10条规则消费同一费用切片
- **THEN** 净额计算最多一次，命中内容、编码、退费剔除与原实现逐项相同

#### Scenario: 另一请求或只有文书证据
- **WHEN** 另一患者或下一请求的费用不同，或当前只有文书证据
- **THEN** 新请求重新计算自己的费用；纯文书无需费用预处理
