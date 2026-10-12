## Why
用户反馈患者切换会白屏。合成浏览器复现及真实app.js点击回归表明每次患者卡点击仍执行整文档导航，顶栏和侧栏随之重建。

## What Changes
- 患者普通点击使用现有HTML路由异步加载，完整验证后一次替换详情；等待/失败保留原画面。
- 显式同步患者身份、有效筛选、标题、地址与侧栏active；支持前进后退、连续切换和错误重试。
- 保留未提交批注，切换期间锁定旧详情交互；拦截上一患者原文的迟到响应。

## Capabilities
### New Capabilities
- `patient-navigation`: 患者无闪屏导航与跨患者状态隔离。
### Modified Capabilities
无API字段改名或新增后端接口。

## Impact
app.js、患者/工作台HTML模板及合成测试。复用现有fetch/DOMParser/History，不引入框架或依赖，不改变SQL/审计/规则。
