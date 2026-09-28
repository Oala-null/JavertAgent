## 1. 缺口基线与数据契约

- [x] 1.1 为费用单位/医嘱关联丢失、OCR 医嘱表为空、检查报告只在文书的现状补失败回归测试
- [x] 1.2 扩展 schema manifest 与 Hub fee 映射，保留可选 `unit`、`order_id` 并验证老 CSV 兼容
- [x] 1.3 将结构化医嘱安全合流到 notes 契约，验证表缺失/空表/有行三种路径

## 2. 通用临床证据工具

- [x] 2.1 实现并注册 `search_orders`，结构化医嘱优先、OCR 医嘱全文保守兜底
- [x] 2.2 实现并注册按编码/名称/服务日期查询的 `catalog_lookup`
- [x] 2.3 把两版诊疗目录纳入受控生产制品并补部署范围测试
- [x] 2.4 为 `search_examinations`、`search_lab_results` 增加结构化为空时的报告全文弱兜底
- [x] 2.5 更新工具清单、prompt 文档和公开中文化映射，确保新增工具不泄漏内部英文名

## 3. 专家眼科规则与 Router

- [x] 3.1 新增 R319 眼科计价数量/单位/侧别对账规则与 presence precheck
- [x] 3.2 新增 R320 睑板腺治疗执行举证规则与 presence precheck
- [x] 3.3 新增 R321 床头心电图设备现场核查规则与零 LLM presence_review precheck
- [x] 3.4 新增 R322 A/B 超联合指征规则与中性 coexist_review precheck
- [x] 3.5 重建 Router 映射并验证名称后缀、国家医保编码和 A/B 共存召回

## 4. 回归、回放与文档

- [x] 4.1 增加不含真实患者信息的合成工具/规则回归，验证费用不存在时零 LLM
- [x] 4.2 运行定向测试、Promise 门禁、受影响组合测试和全量 pytest，记录既有债务
- [x] 4.3 对目标 OCR 病例做只读工具回放和四规则 precheck dry-run，确认五个问题均进入证据链（完整 LLM dry-run 因敏感数据外发门禁未执行）
- [x] 4.4 更新 README/架构/规则设计/部署 runbook/CHANGES 并运行 OpenSpec 严格校验

## 5. 发布

- [x] 5.1 审查最终 diff 与工作树，只暂存本任务文件并提交
- [ ] 5.2 推送当前分支并确认远端提交
- [ ] 5.3 从已提交 HEAD 构建并安装 production-62 受控制品，保持环境值不变
- [ ] 5.4 重启后验证 HEAD/clean、systemd、登录、SQL/Hub、工具注册、v3 API 和合成规则冒烟
- [ ] 5.5 对目标 OCR 病例执行获授权的四规则生产重跑并核对工作台结果
