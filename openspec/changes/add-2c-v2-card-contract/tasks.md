## 1. 行为名称与公共契约

- [x] 1.1 更新集中行为映射：虚构类映射为 T380206/提供不必要的医药服务，串换保持独立
- [x] 1.2 为 v2 两个精确路径补充免鉴权白名单并验证其他审计路径仍受保护

## 2. v2 结果实现

- [x] 2.1 抽取 v1/v2 共用提交逻辑，新增 v2 submit 且保持 attempt 幂等
- [x] 2.2 实现 v2 命中项目费用时间关联和 matched_items 稳定去重
- [x] 2.3 实现 v2 card 序列化、code/name/time 等长投影、CLEAN 命中和占位文本清理
- [x] 2.4 完整透传肿瘤 eligibility_evaluation，并保持历史空值兼容

## 3. 测试与文档

- [x] 3.1 增加 v2 入参、免鉴权、v1 兼容和幂等回归测试
- [x] 3.2 增加多命中、多日期、CLEAN 药品、肿瘤条件与占位文本测试
- [x] 3.3 新增独立 v2 对接文档并在现有 2C 文档中注明版本入口
- [x] 3.4 运行相关 API/hit resolver/SSE 测试、OpenSpec 严格校验并记录结果（全量 1044 passed / 1 skipped；严格校验通过）

## 4. 70782 联调回归修复

- [x] 4.1 增加 name-only 假命中、时间格式、不适用标签和 39 卡完整性回归测试
- [x] 4.2 统一 v2 occurrence_time 为 yyyy-MM-dd HH:mm:ss，并过滤无法关联实际费用行的 matched_items
- [x] 4.3 在不改变底层 CLEAN 三态的前提下增加 applicability，并将规则不适用展示为“不适用”
- [x] 4.4 同步 2C v2 对接文档，运行定向测试（20 passed）、组合测试（68 passed）与 OpenSpec strict 校验（通过）

## 5. 62 生产部署

- [x] 5.1 无回显固化旧进程实际环境并备份当前源码与 mode 0600 `.env`（回滚点 `/home/admin2/backup/javert-2c-v2-20260727-dYx4sX`）
- [x] 5.2 仅部署 2C v2 路由源码，运行幂等 schema 检查并重拉服务（远端 SHA-256 `4a856e50...c4a59`）
- [x] 5.3 验证 systemd、HTTP、进程开关、SQL/Hub 健康和 J70782 v2 契约（39/39、0 failed、0 格式/关联错误）
- [x] 5.4 记录部署与回滚点，运行 2C 定向测试（20 passed）及 OpenSpec strict 校验（通过）

## 6. 2C 出口计数诊断

- [x] 6.1 增加不含患者标识和业务原文的 v2 出口结构化计数日志及回归测试（20 passed）
- [x] 6.2 单文件部署诊断日志并请求一次 J70782 v2（PID `498738`，HTTP 200）
- [x] 6.3 从 journal 读取出口计数：Javert 发出 39 cards；13 cards 含 14 matched_items，C 端 14 条与 matched_items 数量相等
