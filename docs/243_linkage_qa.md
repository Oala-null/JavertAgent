# 243 最小关联修复 QA（2026-09-07）

## 范围与状态

- 基线：gnome-243 / 3da4d80。未合并 fp8，未包含其未提交内容。
- 未修改 pyproject.toml、uv.lock、规则 YAML、模板或知识库资产；没有新增SQL schema迁移。
- 院内只读截图提供关联依据；本地验证全部使用去标识人工夹具，不连接医院，不调真实LLM。
- 真实患者执行、机上版本确认与工作台验收仍需现场完成。不能将本报告理解为“已部署/已跑通”。
- 核心代码提交53adb4a已推送gnome-243；首次正式运行包374文件通过摘要验证。发布记录更新后最终包继续从最新已推送HEAD生成，核心代码不变。

## 验证结果

同一Python环境：/Users/shane/26er/Javert/.venv/bin/python，独立工作目录，无现场.env和患者文件。

| 检查 | collected | pass | skip | fail | error | deselected |
|---|---:|---:|---:|---:|---:|---:|
| 未修改基线3da4d80，pytest -q -m 'not slow' | 674 | 625 | 12 | 14 | 23 | 0 |
| 修复后，同命令原始统计 | 717 | 670 | 12 | 12 | 23 | 0 |
| 修复后，显式排除下面35项既有环境/夹具债务 | 717 | 670 | 12 | 0 | 0 | 35 |
| 本次相关组合测试 | 66 | 66 | 0 | 0 | 0 | 0 |

相关组合：test_hospital_linkage.py、test_gnome_release.py、test_hub_raw_source.py、
test_hub_source_ba.py、test_hub_summary_notes.py、test_hospital_linkage_sql.py。

另通过：Bash语法、Python compileall、CLI list、CLI --help、OpenSpec严格验证、git diff --check。
原有两个手术测试替身把JOIN子查询误当成主查询，已限定匹配条件；这是测试替身修正，不是删除测试。
本报告不声称“全量全绿”。尚存的失败/error集合均在未修改基线上复现。

## 本次覆盖的失败边界

- 三种ID不相同、多次住院、医院代码拒绝测试0001/0003、KH/KLX身份冲突。
- 首页/小结/登记为0或多行、BAH重复、占位时间、时间冲突。
- 费用走visit、正文走bah、诊断/手术走syxh；参数绑定并限定院区。
- 文书空正文不遮蔽标准小结；1900时间不伪造；真实标准费用类别和连接扩行保护。
- TB_OPERATION_DETAIL正确拼写；手术关联使用visit而不是SYXH硬等JZLSH；紧凑手术时间解析。
- 共享bundle上下文、费用空则停止、ETL快照可被现有CsvLoader精确加载、目录0700/文件0600。
- Web真实模式绕过旧CSV/overlay；SQL schema检查仅SELECT、不执行DDL。
- 发布保护env/LLM配置/venv/数据/SQLite；损坏包/非白名单/目标软链拒绝，安装失败还原；回滚保留业务数据。
- 未推送或脏工作目录禁止正式打包。

## 显式排除清单

环境债务主要为：缺少本地演示患者文件、旧默认值断言、Web测试缺少安全session配置、既有onboarding/回填夹具。下面是本次门禁实际排除项，不是泛化跳过整个模块。

- `tests/test_backfill_anchors.py::test_backfill_sqlite_end_to_end`
- `tests/test_config.py::test_missing_file_uses_defaults`
- `tests/test_config.py::test_hub_raw_defaults`
- `tests/test_onboarding_routes.py::test_delete_upload_removes_file_and_blocks_data_dir`
- `tests/test_patient_overview.py::test_get_fees_sum_map_known_patient`
- `tests/test_patient_overview.py::test_get_primary_dx_maindiag_flag`
- `tests/test_patient_overview.py::test_build_overview_fee_category_items_consistency`
- `tests/test_patient_overview.py::test_build_overview_cached_but_isolated_copies`
- `tests/test_patient_overview.py::test_build_overview_cache_cleared_by_reset`
- `tests/test_workbench_routes.py::test_raw_endpoint_notes_bucketed_and_sorted`
- `tests/test_workbench_routes.py::test_raw_endpoint_fees_have_formatted_date`
- `tests/test_workbench_routes.py::test_raw_endpoint_includes_labs_and_exams`
- `tests/test_web_api.py::test_phi_endpoints_require_login`
- `tests/test_web_api.py::test_health`
- `tests/test_web_api.py::test_list_rules`
- `tests/test_web_api.py::test_get_rule_existing`
- `tests/test_web_api.py::test_get_rule_404`
- `tests/test_web_api.py::test_get_rule_yaml`
- `tests/test_web_api.py::test_sample_pilot`
- `tests/test_web_api.py::test_sample_pool_invalid`
- `tests/test_web_api.py::test_pools_endpoint`
- `tests/test_web_api.py::test_audit_runs_query`
- `tests/test_web_api.py::test_audit_run_detail_404`
- `tests/test_web_api.py::test_index_html_served`
- `tests/test_web_app.py::test_with_mssql_login_page`
- `tests/test_web_app.py::test_with_mssql_register_page_closed_by_default`
- `tests/test_web_app.py::test_with_mssql_workbench_redirects_to_login`
- `tests/test_web_app.py::test_with_mssql_workbench_patient_redirects`
- `tests/test_web_app.py::test_with_mssql_dashboard_redirects`
- `tests/test_web_app.py::test_with_mssql_export_redirects`
- `tests/test_web_app.py::test_with_mssql_sse_api_returns_401`
- `tests/test_web_app.py::test_with_mssql_review_api_unauth_401`
- `tests/test_web_app.py::test_with_mssql_banner_dismiss_api_unauth_401`
- `tests/test_web_app.py::test_with_mssql_raw_data_api_unauth_401`
- `tests/test_web_app.py::test_workbench_routes_registered_in_with_mssql_mode`

## 现场待验收

完成审查补测：执行真实取数SQL的SQLite兼容替身（只转换TOP/dbo/sys.tables语法），
复现并修复“另一院区同诊断码的名称被全局字典查询带入”。多院区/同卡多住院的六类取数整体通过。
该测试不替代SQL Server/ODBC现场验收。

正式包d455978已完成离线完整演练：374个运行文件安装并验证；826个原文件内容/权限在回滚后完全恢复，
包含人工模拟的现场手工代码修改；env、LLM配置、venv标记、患者文件标记和数据库标记五类保留项不变。
没有连接医院或执行真实审计。最简操作指南见243_overlay_rollback.txt。

1. 确认DEPLOY_COMMIT/manifest、原启动方式、SQL源与结果库、模型别名和session配置。
2. 验包、停旧Web/批跑、备份安装；env和业务数据不变。
3. 一个实际八月住院患者通过预检；费用/有效正文可读；未拿七月首页拼八月数据。
4. 审计failed=0，有实际候选执行；启用双写时无本次pending，结果出现在正确库。
5. 工作台原文、首页诊断/手术与该次快照一致，未知文书时间如实保留。
