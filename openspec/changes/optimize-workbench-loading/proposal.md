## Why

工作台每次打开患者都会输出完整患者侧栏，历史重跑又读取未展示的大字段；患者数和历史次数共同放大等待时间。使用合成数据复现并优化跨分支共用链路。

## What Changes

- 当前患者首屏与全局侧栏解耦，侧栏异步分页、全范围筛选排序，保留导航。
- 历史记录只取展示需要的字段，当前主卡完整证据及历史批注保留。
- 限制前端 DOM 数量、输入防抖、原文请求去重；核验 SSE 与空状态。
- FP8 测试并合入 main 后，把通用修复移植 gnome-243，构建独立、可校验和回滚的院内补丁。

## Capabilities

### New Capabilities

- `bounded-workbench-loading`: 患者首屏、侧栏与历史查询的资源边界和回归要求。

### Modified Capabilities

无现有公开 API 删除或改名。

## Impact

FastAPI 路由、SQL Server 只读查询、Jinja 模板、原生 JS、合成回归测试、部署补丁工具。无依赖升级、DDL、规则变更或历史重算；院内不带入 FP8 新功能。
