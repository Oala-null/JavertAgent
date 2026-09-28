## 1. 回归测试与 Hub 止血

- [x] 1.1 补充 Hub 手术 SQL 表名防漂移断言，验证两处查询只使用 `TB_OPERATION_DETAIL`
- [x] 1.2 补充 2C 重跑 attempt 复用/更新、患者级失败、partial 和 Router 零候选测试
- [x] 1.3 补充 Runner `finish_reason=length` 有界恢复、截断正文不回灌及恢复失败测试

## 2. 2C attempt 与失败语义

- [x] 2.1 为实际入队任务生成 `attempt_id`，运行中幂等提交复用、完成后重跑更新
- [x] 2.2 在 worker 中按 Hub/Router/audit 阶段记录稳定错误码，并计算 succeeded/partial/failed outcome
- [x] 2.3 在查询响应只追加 attempt、outcome、retryable、progress 和规则诊断字段，保持旧字段兼容

## 3. LLM 截断恢复

- [x] 3.1 Provider 显式返回 `finish_reason`
- [x] 3.2 Runner 对 length 截断使用不携带截断正文、`max_tokens<=512` 的短恢复路径
- [x] 3.3 length 恢复失败继续安全落 INCONCLUSIVE，并向 2C 映射中文理由和稳定诊断码

## 4. 文档与验证

- [x] 4.1 更新 `docs/2c对接_javert审计服务.md` 与 `docs/CHANGES.md` 的兼容字段、轮询和失败处理口径
- [x] 4.2 运行相关单测、受影响模块组合测试和一个 TestClient 端到端 2C 流程
- [x] 4.3 运行 `openspec validate fix-2c-rerun-malformed-results --strict` 并核对 tasks 与真实结果一致
