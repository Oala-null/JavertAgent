## Context
已上线08cfc40用现有HTML路由局部导航；每次点击仍fetch。真实点击A→B→A回归实际2次请求，期望1次。侧栏缓存和费用请求内复用不能复用详情HTML。

## Goals / Non-Goals
目标：短期重复查看直接复用已验证详情，不串身份、不保存草稿。非目标：离线工作台、跨标签页缓存、永久病历缓存或新后端协议。

## Decisions
- Map保存详情HTML字符串快照及标题，初始化副作用前seed首次详情；后续只从验证成功的服务器响应构造快照，不从离开时的live DOM构造。
- 键为用户名、同源URL及排序后的完整query，忽略hash。只读取显式filter的缓存；缺省filter依赖cookie，初始页面按已渲染data-filter存显式filter入口。无filter历史请求仍走服务器，避免cookie变化后串筛选。
- 固定TTL60秒，命中只调整LRU，不延长TTL；最多5个页面入口、总量2097152个JS字符（UTF-16约4MiB），单页超限不缓存。只用内存，不用local/sessionStorage；工作台空页不缓存。
- 审核发起即失效当前患者；review_submitted/new_audit_run在慢病早退前失效相应患者全部变体；MAX签名变化、登录/用户不匹配、窗口focus/visibility恢复清全。
- SSE error清全并禁用读写，重连open清全后恢复；首次正常open保留seed。没有SSE能力时仍有TTL/签名兜底。
- 缓存独立代数在每次失效时递增；旧请求可按现有导航规则显示，但不能回填缓存。导航世代继续负责旧原文和乱序请求隔离。

## Risks / Trade-offs
非SSE来源更改可能在60秒内仍显示旧详情，固定TTL限制窗口；焦点恢复/手动刷新立即放弃缓存。缺filter历史入口、大页、断线期间会重新加载。内存条数与字符双上限避免病历过大耗尽浏览器内存。

## Migration Plan
真实回归先红→实现和浏览器重复导航计数验证→推送→核对62基线并增量安装app.js→重启刷新资源版本→SHA/配置/服务/合成验收。回滚仅恢复旧运行提交并重启，无数据迁移。
