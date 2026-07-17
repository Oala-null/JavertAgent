# 肿瘤药医保资格 v2 QA（2026-07-17）

## 结论

本 change 的实现、知识资产、三例合成金标和 142 全候选 shadow 已重新验证。严格报告为
`status=complete`：419 名确认候选患者、444 个候选求值，患者与候选覆盖率均为 100%，
结构化错误、校验错误和重复 ownership 均为 0。

这只证明本次测量完整，不代表所有知识分支都已自动化。156 条来源限定拆成 158 个分支后，目前只有
3 个 approved，155 个仍为 `needs_review`；新患者级结果为 `CLEAN 2 /
INCONCLUSIVE 417`。未批准分支继续 fail-closed 为人工复核，不会被自动判成违规。

用户已于 2026-07-17 接受上述 shadow/golden 结果，并另行明确授权把 RD04 转为
`ready`、将 62 的 `JAVERT_ONCOLOGY_ELIGIBILITY_V2` 切换为 `on` 后部署。代码默认
仍保留 `off` 作为新环境安全基线和回滚入口；生产开关只写入 62 的受控 `.env`。

## 直接受影响测试

核心合同、资格树、病理、方案、结果、实际 RD04/Runner/store/API 路径、shadow
报告器、药品 lookup、store 和 runner：

```text
171 passed, 1 skipped
```

配置中的 oncology feature flag 定向测试：

```text
5 passed, 9 deselected
```

三例金标均使用语义化合成 fixture：

- `urothelial_her2_low`：`CerbB2(1+)` 被规范化为 HER2 IHC 1+，资格不满足；
  既往含铂仍为 UNKNOWN，mandatory branch 不得 CLEAN。
- `pola_cycle_conflict`：解析完整 Pola-R-GemOx 和 `cycle_no=4`，保留年份冲突，
  治疗线数、复发和难治保持未知。
- `pola_transplant_gap`：既往多线治疗和进展有依据，移植不适合仍为文书缺项，
  生成聚焦建议且不伪造证据。

## 知识资产门槛

两个构建器连续运行两次，以下输出 SHA-256 前后逐字节一致：

| 资产 | SHA-256 |
|---|---|
| `oncology_eligibility_rules.json` | `9c91fc5af84010746d37af8b6ff7365f2734b0d6f997805f9385c1f9218b5420` |
| `pathology_biomarker_kb.json` | `7de02aa02333100e62ff3d33d8ebed25d7c5a14fad458df6a8b053d8d5735bd1` |
| `oncology_regimen_kb.json` | `174146374ba00371dcef970336864e7bf5caa63826379e528e1039401898f820` |
| `oncology_eligibility_needs_review.json` | `08a227f6e15e01320b27f667acda764109156747ed3931a1e7f0c109d5c26252` |
| `pathology_biomarker_needs_review.json` | `e4c92d0f44be3d38c25890c8a8fd0b073ec64fb42e62d8a7516a9c43cf7a306a` |

覆盖守恒：

- 156 个来源限定全部有处置；
- 158 个分支 = 3 approved + 155 needs_review；
- 来源级 = 1 fully approved + 1 partially approved + 154 needs-review-only；
- 病理资产 = 1 approved + 131 needs_review；
- 维迪西妥有效期为 2026-01-01 至 2027-12-31；
- 维泊妥珠有效期为 2025-01-01 至 2026-12-31。

维迪西妥胃癌分支因“至少 2 个系统化疗”尚无可靠计数器，明确进入
`needs_review`，不再与尿路上皮癌分支一起被误批准。两个手工编译来源均以规范化全文
指纹和完整 branch manifest fail-closed；来源全文或分支的新增、删除、修改会拒绝生成
approved 资产。

## 142 全候选 shadow

正式驱动从 `192.168.31.142 / TP_data_hub` 只读查询候选，从 `zadig` 只读查询历史
RD04/专家复核。应用调用链只有 `SELECT`；当前账号本身未被脚本强制为数据库只读角色，
生产长期运行仍应使用只读账号。

数据读取窗口为 2026-07-17 11:42:17Z 至 11:43:05Z，使用多个
`READ COMMITTED` 查询，`snapshot_isolation=false`，因此它是可审计的查询时间窗口，
不是可重放的一致性数据库快照。

严格结果：

- SQL 粗筛 439 人；经运行时同口径的退费净额、国家码和无码完整通用名精筛后确认
  419 人；
- 444 个候选全部产生合法结构化求值，患者覆盖率和候选覆盖率均为 100%；
- `evaluation_error_count=0`、`validation_error_count=0`、
  `duplicate_count=0`；
- 419 人中 214 人有历史 RD04，205 人没有历史旧裁决；
- 214 人的历史方向性矩阵为 `C→C 1`、`C→I 183`、`I→C 1`、
  `I→I 7`、`V→I 22`；8 人不变、206 人变化；
- 新患者级裁决为 `CLEAN 2 / INCONCLUSIVE 417`；
- 候选资格状态为 `DOCUMENTATION_GAP 442 / CONFLICT 2`；
- 仅 8 人有历史专家复核：当前结构化结果一致 3/8，历史运行一致 5/8。

比较设计明确标为 `historical_unpaired`：历史旧裁决与当前结构化求值可能使用不同数据
窗口、规则和候选集合。矩阵与专家一致率只能作探索性方向参考，不能宣称 paired shadow
性能。

公开产物：

| 产物 | SHA-256 |
|---|---|
| `shadow_comparison.json` | `791568d0209e18980521372032fa4147e82b822e549e28cd7049fb95bf0d4f61` |
| `shadow_run_manifest.json` | `4288edc83679d9123dc41e29c20242d9248cbe5c0de6aa22f94e98ef742565e8` |

manifest 记录运行引用 `shadow-8e290dce6aa2`、HEAD
`68a40f8d708cb87ebac855cd2fbd7a105bdf70b9`、dirty-tree 内容指纹、四个知识资产哈希、
源/历史数据库、候选查询版本、读取窗口和计数。生成的 report/manifest 被明确排除出
dirty-tree 指纹，避免输出自引用。

## PHI 与临时数据

- 三个金标 fixture 不含患者号、姓名、住院号、联系方式、地址或病历原文导出；
- 当前工作树对三项旧患者标识逐项扫描均为 0 命中（本文不复述原值）；
- shadow 公开报告只含 salted `patient_ref/run_ref/ownership_ref/review-run ref`；
- 驱动在写公开产物前逐一扫描本次粗筛患者号、历史 run ID、原始 ownership key 和 salt，
  发现任一原值即失败；
- 419 个 patient ref 和 444 个 ownership ref 均格式合法且唯一；
- 含原始标识的临时目录强制 0700、文件强制 0600，成功和异常退出均清理；本次运行后
  `/private/tmp` 与 `/tmp` 无 `javert-oncology-shadow-*` 或旧
  `javert-rd04-shadow-*` 残留。

注意：旧标识仍存在于 integration 本地历史 commit。生产交付只把共同基线到最终树的
净差异 squash 到原项目分支，不合并或推送 integration 分支，因此这些中间 commit
不会成为原项目分支祖先，也不会随本次交付传播。

## 全量非慢测试

原始全量：

```text
803 collected
747 passed, 12 skipped, 14 failed, 30 errors
```

14 个 failure 和 30 个 error 与修复前基线完全同形，本 change 没有新增失败：

- `test_backfill_anchors.py`：1 个既有 review-note 文案断言；
- `test_config.py`：2 个旧默认值断言仍期待 `.62 / TP_data_hub`；
- `test_hub_source_ba.py`：2 个既有 mock 缺 `SYXH`；
- `test_onboarding_routes.py`：缺 `data/szx/random_5pts_fee.csv`；
- `test_patient_overview.py`、`test_workbench_routes.py`：缺本地病例/费用/首页快照；
- `test_routes_2c.py`、`test_run_batch_integrity.py`、`test_web_api.py`、
  `test_web_app.py`：测试未注入非默认 session secret，触发既有生产安全 fail-fast。

排除上述 10 个既有环境/fixture 债务文件后的全量非慢结果：

```text
699 collected
690 passed, 9 skipped
```

## 62 生产激活与三例复跑

2026-07-17 已把运行时 commit `260a4d2` 部署到 62。覆盖源码前先将旧版实际生效的
SQL/Hub 连接值无回显固化进 mode 0600 的 `.env` 并完成可回滚备份；部署后验证：

- `JAVERT_ONCOLOGY_ELIGIBILITY_V2=on` 由 systemd 新进程实际读取，非仅检查配置文件；
- RD04 YAML 与 router index 均为 `ready`；
- 142 `javert_audit_runs.eligibility_json` 幂等迁移完成；
- 远端专项为 `20 passed`，工作台登录页 HTTP 200。

请求批 `med_rst1.2` 的三条 RD04 均成功运行并在 142 写入非空结构化结果，去标识顺序下
旧→新裁决为 `C→I / V→I / C→I`。复核第三条时发现一个真实抽取缺陷：鉴别诊断中描述
其他疾病的“治疗有效”模板句被误当成当前肿瘤反证，同时“已行多次…化疗”未命中既往
治疗模式。修复后新增去标识回归，排除既有环境债务的全量门禁提升为上面的
`690 passed, 9 skipped`。

补充批 `med_rst1.2-fix1` 复跑同三条：第一条因结构化结果字节等价触发确定性 run-id
去重，继续复用请求批结果；第二、三条产生新归档行。最终有效结果为：

| 去标识目标 | 旧裁决 | 最终裁决 | 资格状态 | 说明 |
|---|---:|---:|---|---|
| target_1 | CLEAN | INCONCLUSIVE | DOCUMENTATION_GAP | 服务日期没有生效且 approved 的条件树，fail-closed 人工复核 |
| target_2 | VIOLATION | CLEAN | DOCUMENTATION_GAP | 复发/难治臂仅缺移植适合性评估；仍保留 `CANCER_CONTEXT_CONFLICT` 数据质量标志 |
| target_3 | CLEAN | CLEAN | DOCUMENTATION_GAP | 既往多次化疗与疾病进展被正确识别，仅缺移植适合性评估 |

补充批结束后再次确认 62 服务 active、登录页 HTTP 200、进程实读 feature flag 为 `on`。
运行日志仅以 target 编号存放在 0700 私有目录，未写入 Git；本文不记录患者号、run_id、
推理原文或证据原文。

## 复现命令

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_oncology_eligibility_assets.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_oncology_regimen_kb.py

export JAVERT_SHADOW_DEID_SALT="<从受控随机源读取>"
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/run_oncology_shadow_batch.py \
  --env-file /secure/path/javert-142.env \
  --hub-database TP_data_hub \
  --history-database zadig
```

严格 driver 对缺少独立分母、非 shadow 模式、畸形嵌套 evaluation、缺失/空白
ownership、缺失候选身份、重复患者、重复 ownership 和计数不一致均 fail-closed：
先写 `status=blocked` 的去标识报告与 manifest、清理私密临时目录，再以退出码 2 结束。
