# 慢病发布交接（2026-09-08）

已推送：`codex/chronic-expert-pilot`，功能提交`baf16bc5468295c7a00e2bbdd33fe408a2a182dc`。
发布工作树基于62既有`ef0f625`；原项目工作树的未提交工作保留。

## 核心代码改动与必须成组合并

| 必须成组的文件 | 行为 |
|---|---|
| `src/javert/chronic/{contracts,knowledge,facts,runtime}.py`、`src/javert/clinical_criteria/{contracts,evaluator}.py`、`configs/chronic_disease_criteria.json`、`configs/chronic_disease_sources.json`、`configs/chronic_disease_expert_feedback.json`、两份draft-r1快照、`configs/schemas/chronic_disease_{criteria,sources}.schema.json`、`scripts/build_chronic_disease_criteria.py` | 专家解释revision/checksum、候选事实引用校验和确定性shadow证明，未审批不认定 |
| `src/javert/audit/{rule,rule_loader,rule_writer,result,runner}.py`、`src/javert/commands/audit_patient.py`、`src/javert/config.py`、`configs/llm.yaml`、`configs/rules/CD01.yaml`至`CD20.yaml`、`src/javert/routing/router.py`、`scripts/build_rule_mapping.py`、`data/router/javert_rules_index.json`、`configs/rule_mapping.json` | CD注册、默认排除、显式专用运行；规则源码与生成索引不可拆 |
| `src/javert/audit/result.py`、`src/javert/store/{audit_store,sqlserver_store,result_persister,models}.py`、`src/javert/store/schema.sql`、`scripts/sql/create_javert_tables.sql` | 独立nullable慢病JSON双写/回读；迁移必须先于服务重启 |
| `src/javert/web/api/{routes_audit,routes_sse,routes_workbench,schemas}.py`、`src/javert/web/templates/{_clinical_criteria,patient_detail,_sidebar}.html`、`src/javert/web/{templating,public_presenter,reasoning_zh,rule_meta}.py`、`src/javert/web/static/app.js` | 慢病候选标记、中文条件证据、精确页定位、tag入口；与存储字段成组，CD不计普通违规 |
| `scripts/run_chronic_pdf_pilot.py`及上述运行时/存储接口 | 私有单病例临床页隔离、清单恢复、发布目标绑定、幂等追加和精确run同步 |

`src/javert/audit/headline.py`、`src/javert/promises/models.py`、`src/javert/web/api/routes_rules.py`
也包含CD ID兼容调整，不能遗失。建议合并完整功能提交，不逐文件摘取。

## 验证配套

- `tests/test_chronic_{criteria_knowledge,rule_registry,runtime,result_delivery,pdf_pilot}.py`、
  `tests/test_clinical_criteria_evaluator.py`及既有Runner/存储/Workbench/2C回归。
- 源政策PDF、专家解释JSON、知识构建`--check`、Router重建和OpenSpec strict。
- 三份2C对接契约仅新增字段；下游忽略未知字段兼容性已静态核查，本次不发布2C。
- 代码门禁1314 passed / 12 skipped，另精确排除50个在旧版本同环境复现的测试资产债务。
  后续脚本新增保护测试34 passed、存储UI/API组合271 passed；详见主QA。
- 62部署顺序、备份、进程配置、SQL/Hub、v3与HEAD/clean见部署手册。

## 风险与未完成

临床候选不是完整资格认定，OCR未人工复核，资产仍needs_review，复杂时间/重复测量等策略
尚未完整实现，CD19功能障碍分级仍待明确。5+45严格QUALIFIED样本阶段未完成；
本次仅授权单PDF的shadow发布，CD10关闭，不扩展到其他患者或243。未归档change。
62真实验收：20条已同步、21临床页、7候选规则、29证据锚点、tag=慢病，普通V/I/C均0；临时隐私材料已清理。完整记录见`chronic_disease_criteria_qa.md`，不在本文件复制患者资料。
