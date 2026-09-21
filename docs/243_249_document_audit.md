# gnome-243文档影响盘点（2026-09-20）

范围：根目录说明、docs递归Markdown、Info_Sync说明、交付维护说明。已逐文件读取并按分支/使用场景核对；历史数据和实验结论不以新状态重写。用户未跟踪文件保留原件，旧中文重复部署手册不再作为受控入口。

| 文件 | 行数 | 处理 |
|---|---:|---|
| `AGENTS.md` | 81 | 已更新：当前维护线口径/导航 |
| `CLAUDE.md` | 4 | 已更新：当前维护线口径/导航 |
| `Info_Sync/01_Zadig与Javert_产品与数据对接说明.md` | 398 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `Info_Sync/02_Javert数据范围清单.md` | 242 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `Info_Sync/技术深潜_Zadig与Javert.md` | 416 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `Javert问题汇总.md` | 90 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `README.md` | 365 | 已更新：当前维护线口径/导航 |
| `delivery/audit_eda/README_zh.md` | 137 | 已回收并核对：已交付只读EDA，行为不变 |
| `delivery/audit_eda/VALIDATION.md` | 12 | 已回收并核对：已交付只读EDA，行为不变 |
| `delivery/lis_hotfix/MAINTENANCE.md` | 10 | 已更新：当前维护线口径/导航 |
| `delivery/lis_hotfix/README_zh.md` | 263 | 已核对：冻结原v1交付说明，现场状态查当前运维入口 |
| `delivery/lis_hotfix/VALIDATION.md` | 37 | 已核对：冻结原v1交付说明，现场状态查当前运维入口 |
| `docs/243_249_hotfix_qa.md` | 69 | 已更新：当前维护线口径/导航 |
| `docs/243_linkage_qa.md` | 97 | 已更新：当前维护线口径/导航 |
| `docs/243_release_cookbook.md` | 82 | 已更新：当前维护线口径/导航 |
| `docs/243开机故障归因_20260715.md` | 132 | 已更新：当前维护线口径/导航 |
| `docs/243部署手册.md` | 217 | 已评估：用户既有未跟踪资料，保留原件；非本分支操作入口 |
| `docs/CHANGES.md` | 48 | 已更新：当前维护线口径/导航 |
| `docs/ai_compute_two_phase_budget.md` | 85 | 已评估：用户既有未跟踪资料，保留原件；非本分支操作入口 |
| `docs/boost_llm_efficiency_实测.md` | 102 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/companion_术式配套表.md` | 37 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `docs/company_ai_compute_plan.md` | 93 | 已评估：用户既有未跟踪资料，保留原件；非本分支操作入口 |
| `docs/deployment_192_62.md` | 436 | 已更新：当前维护线口径/导航 |
| `docs/deployment_243_gnome.md` | 102 | 已更新：当前维护线口径/导航 |
| `docs/deployment_249_lis_medical.md` | 265 | 已更新：当前维护线口径/导航 |
| `docs/dgx_spark_post_training_evaluation.md` | 82 | 已评估：用户既有未跟踪资料，保留原件；非本分支操作入口 |
| `docs/drug_fix_v2_2_sample.md` | 106 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/fn_baseline.md` | 8 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/how_javert_works.md` | 253 | 已更新：当前维护线口径/导航 |
| `docs/performance_report.md` | 372 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/precheck_compare_实测.md` | 63 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/qwen36_post_training_sizing_research.md` | 117 | 已评估：用户既有未跟踪资料，保留原件；非本分支操作入口 |
| `docs/review_workbench_user_guide.md` | 324 | 已更新：当前维护线口径/导航 |
| `docs/rule_design_guide.md` | 129 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `docs/sample_audit_patient.md` | 1112 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/sample_drug_audit.md` | 121 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/sample_onboarding.md` | 201 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/sample_run_R191.md` | 58 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/schema/00_数据接入说明.md` | 82 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `docs/template_design_guide.md` | 217 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `docs/templates/模板1_重复收费.md` | 392 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/templates/模板2_过度检查.md` | 381 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/templates/模板3_口腔串换.md` | 339 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/templates/模板4_超标准收费.md` | 191 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/templates/模板5_虚构医药服务.md` | 151 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/templates/模板6_过度诊疗.md` | 76 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/templates/模板7_串换收费.md` | 262 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/training_local_vs_cloud.md` | 91 | 已评估：用户既有未跟踪资料，保留原件；非本分支操作入口 |
| `docs/v2_1_gate_drug_analysis.md` | 93 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `docs/y_rules_analysis.md` | 282 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `docs/y_rules_status_v0_4.md` | 182 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/做不了163规则可行性分析.md` | 922 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/数据接入清单.md` | 184 | 已更新：当前维护线口径/导航 |
| `docs/系统扫描与优化方案_2026-07.md` | 203 | 已核对：历史实验/规则设计资料，保留日期与样本口径 |
| `docs/药品限制/README.md` | 34 | 已核对：通用或其他环境说明，本次运行接口未改变 |
| `docs/试点医院数据对接表单_TB.md` | 296 | 已更新：当前维护线口径/导航 |
| `docs/试点医院数据对接表单_TB_全字段版.md` | 1082 | 已更新：当前维护线口径/导航 |

当前受控入口：`docs/deployment_243_gnome.md`。LIS细节、EDA、单SYXH查询、现场检查包均由入口导航。
API/SSE未改变，因此未把FP8/62的v2/v3对接文档移植或声明为院内已部署；规则/模板/模型/知识库也未更新。
Agent手册由原AGENTS/CLAUDE两份重复正文收敛为AGENTS硬边界+CLAUDE指针；历史机制继续保留在架构、设计和CHANGES。
