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

后续同输入 A/B 统一使用 Evidence/Evaluation Contract v0.1：主要临床比较单位是 patient-level
legacy A 与同一次 shadow 的 patient-level structured B；drug/policy scope 只作为 evidence/proof
诊断分母，禁止把一个 patient verdict 复制成多个样本。报告必须持久化每个 rate 的 numerator、
denominator 与 95% interval，并分别展示 safety、correct automation、abstention、grounding、
locator/provenance、重复稳定性和盲化专家可理解性，不生成单一总分。当前仓库只完成合成
CONFORMANCE；尚未运行获授权的真实 paired SHADOW/PROMOTION，不能写成临床优效结论。

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

## 完整测试套件

本 change 验收时最初复现的历史基线为：

```text
803 collected
747 passed, 12 skipped, 14 failed, 30 errors
```

随后只清理测试契约与 fixture，不放宽生产安全行为：

- 默认值断言对齐 localhost 与 `sh_yb_platform`，并隔离项目 `.env`，确保测试的是真默认值；
- Web/API fixture 显式注入测试 session secret、临时 SQLite 和合成患者，不绕过
  `create_app()` 的生产 fail-fast；
- backfill、patient overview、workbench raw 和 onboarding 删除测试改用最小合成数据，
  不再依赖本机病例、费用、首页或 szx 快照；
- hub BA SQL stub 改为精确分发并断言 SELECT 列契约；
- SSE 断言纳入向后兼容的可空结构化资格字段；
- 新增 4 个 data-hub 写入安全防回归测试，锁定显式数据库参数、自有库白名单和索引
  SQL 在首条 DDL 前的双重数据库校验。

当前完整命令：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/pytest -p no:cacheprovider -q --tb=short -ra
```

结果：

```text
807 collected
795 passed, 12 skipped, 0 failed, 0 errors
```

12 个 skip 均为显式条件：10 个缺可选 szx fixture，1 个缺本机真 CSV 快照，1 个
runner `for-else` 分支在当前结构下不可达。该测试债务闭环子步骤没有修改生产代码。

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
治疗模式。修复后新增去标识回归；当时排除既有环境债务的门禁结果为
`690 passed, 9 skipped`，随后已完成全部失败/错误的 fixture 闭环，当前完整结果见上节。

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

## add-oncology-kb-authoring 本地验收（2026-07-21）

本节是新 change 的**本地离线验收快照**，不改写上文 2026-07-17 现网结论。
截至本快照，未执行 142 知识库 DDL/上传/物化，未收到专家回传或发布批准，
未在 62/生产启用 published release，也未运行任务 10.5 要求的同输入 paired shadow。
2026-07-21 额外运行了 142 `sh_yb_platform` 只读的 DRAFT 候选预览，并把一批明确标为
DRAFT/人工复核的患者级结果写入现有工作台结果库 `zadig`；它不写知识库，也不构成专家批准
或 shadow 性能结论。

已完成并在本地可重复核对的事实：

- 来源快照、肿瘤候选并集、精选知识原子和方案候选均有机器可读产物；
  库存与覆盖以 `docs/oncology/kb_authoring_baseline.json`、
  `docs/oncology/authoring/*.json` 及 `source_coverage_matrix.md` 中的 `jq` 口径为准，
  本节不复制会随来源变动的计数。
- 两份专家工作簿已本地生成，完成页面预览、下拉/公式/保护回读和
  PHI/凭据校验；两份 `.validation.json` 均为 `valid=true` 且 `error_count=0`。
  这只证明模板与当前候选通过离线校验，不代表专家已批准其业务内容。
- 当前候选 checksum 为
  `sha256:79cd6d0c4046cef3a034f4b9ab5f9d5cacc512cb16ac00fdd0544670effdbfaa`：
  225 条 DRAFT 来源 revision 已拆为 469 个待审适应证分支、754 个聚合节点和
  2186 个条件叶子；树错误和 `unsupported` 叶子均为 0。release readiness 仍为
  `REVIEW_REQUIRED`，阻断项包括 1 个未解析联合药概念（地塞米松）、8 个待审药物类别
  和 133 个需专家确认上下文/阈值的病理节点。
- DRAFT 预览编译器已加载全部 469 个分支并复用真实结构化求值器，但无论叶子求值结果如何，
  最终都强制 `REVIEW_REQUIRED`，并带 `DRAFT_RULE_PREVIEW_ONLY`；不得由该路径产生自动
  `CLEAN` 或 `VIOLATION`。
- 142 只读批测以“`TB_BA_SYJBK` 非空主诊锚 + 明确恶性肿瘤诊断 + 净正肿瘤药收费 +
  有病历文本”为硬门禁，并按肺癌优先、同优先层药物多样性优先选人。扫描到的 95 名药物
  候选均有 SY 病案首页主诊锚，其中 94 名有明确肿瘤诊断、22 名为肺癌；最终 10/10 来自
  SY 病案首页、10/10 是肿瘤患者且 10/10 是肺癌，10/10 读取到诊断和病历文本。
  本批覆盖 8 种肿瘤药、14 个药物匹配和 101 个医保分支求值，0 未映射、0 歧义、
  0 求值异常。结果不再出现 `NO_APPROVED_ELIGIBILITY_RULE`；条件状态为
  `SATISFIED 131 / NOT_SATISFIED 62 / CONFLICT 5 / UNKNOWN 400`。UNKNOWN 主要来自病种
  分支不匹配、治疗线次、病理阈值、联合方案、进展状态和组织学证据缺口，不是数据库未连接
  或患者数据未读取。首次只读预览报告位于
  `outputs/add-oncology-kb-authoring/authoring_preview_batch.json`，其中两个数据库写标志均为
  `false`。
- 为满足工作台人工审阅，随后把同一严格选择口径重新运行并按患者聚合为 10 条 RD04 结果，
  写入 `zadig` 的 `kb_test1` 标签；旧的同名 10 条 `NO_APPROVED_ELIGIBILITY_RULE` 历史未删除，
  以单事务改标为 `kb_test1_legacy`。新批次为 10 行/10 人、全部 `INCONCLUSIVE`，包含 14 个
  patient/drug/insurance scope；10/10 `eligibility_json` 可经 Pydantic 回读，10/10 带
  `DRAFT_RULE_PREVIEW_ONLY` 与 `AUTHORING_REVIEW_REQUIRED`，0 条含
  `NO_APPROVED_ELIGIBILITY_RULE`，且 Web 实际 store 读路径核验侧栏和详情均为 10/10。
  去标识写入报告位于
  `outputs/add-oncology-kb-authoring/authoring_preview_workbench_batch.json`，不含患者号或病历原文，
  权限为 0600；其中 `knowledge_database_write=false`、`result_database_write=true`。
- `oncology-kb export/validate/preflight/schema-apply/upload/materialize` 的边界已落代码：
  `validate` 不读数据库配置；所有数据库路径先校验精确库名与
  `JAVERT_OWNED_DBS`，再检查 `DB_NAME()`。`schema-apply` 另校验数据库 ALTER 权限，
  `preflight/upload/materialize` 校验 principal、schema 权限和 version；
  日志/错误不回显密码、连接串或工作簿原文。
- `materialize` 仅接受已通过服务端校验的完整 batch，单事务投影并核对每类实体。
  `approve` 已按 append-only 最新事件集和完整 typed 子图投影 approved 不可变 revision；
  未落库的 `APPROVE_WITH_EDIT`、审核人不一致或引用/日期/药品概念不完整均 fail closed。
- `APPROVE_WITH_EDIT` 不原地覆盖 revision：有效期、条件节点或方案字段修订会克隆完整子图，
  生成 content-addressed superseding DRAFT 并要求重新审核。精选知识映射修订只落为 `MAPPED`，
  必须由后续新的 `APPROVE` 事件核验已 materialize 的同一目标后才能升为 `VERIFIED`；
  VERIFIED 原子和映射由数据库触发器冻结。
- 条件树和方案引用的 `CLASS` 目标来自独立 `kb.drug_class` authority，必须具备非空规范名、
  match terms、批准状态及匹配 checksum 的审核事件；未知或 DRAFT 类别阻断 revision 批准和 release。
- 每条审核事件都保存并校验 `reviewed_content_checksum`：它必须精确等于被审核
  branch/node/regimen/alias/context/component 当前 typed 内容的 checksum。物化插入事件前会锁定
  并复核目标行，内容在审核后改变会以 `REVIEW_CONTENT_CHECKSUM_MISMATCH` 阻断批准，不能让旧
  意见静默批准新内容。已有 `review_event` 历史行若缺 checksum，DDL 会阻断并要求依据当时内容
  人工补齐，禁止自动猜测。
- operational release CLI/store 已覆盖只读 `release-authority`、`release-build`、
  `release-publish` 和 `release-rollback`。authority 把 source、curated、pathology 三个 checksum
  固定为库外审批 pin；build 只在 authoring 库登记确定性 `CANDIDATE`，publish 必须由与
  领域审核人分离且和 build 相同的 operator 用同一组 pin 重建校验。只有 publish 会写四资产
  `PUBLISHED` 不可变 bundle、数据库 pointer 和本地 `active_release.json`。
- publish/rollback 已实现数据库与本地指针的失败补偿及幂等重试校验；回滚只切到已验证的
  历史 published bundle，保留 bundle、revision 和事件，不以删除历史完成恢复。
- published release 运行时合成金标已覆盖双 `policy_scope` 独立状态，以及
  2025 年使用当前 release 并告警、2026–2027 年窗口内正常裁决、2028 年无
  适用新版时 fail-closed 为 `REVIEW_REQUIRED/INCONCLUSIVE`。该策略仅对带
  `release_id` 的 published 资产生效；legacy `configs/` 资产仍由既有生效期开关控制。
- `eligibility_json`、2C 返回和 `new_audit_run` SSE 只追加可空的 release/revision/
  scope/来源/时间 provenance；非肿瘤结果和未曾有结构化资格的旧行保持 `null`，
  已有 legacy RD04 结构化行保留旧字段且新 provenance 为空；旧三态投影不删除、不改名。

本地定向回归命令：

```bash
.venv/bin/pytest -q \
  tests/test_oncology_kb_release.py \
  tests/test_oncology_kb_release_store.py \
  tests/test_oncology_kb_runtime_policy.py \
  tests/test_oncology_integration.py \
  tests/test_event_bus.py
```

2026-07-21 最终本地门禁结果：

- SQL 写安全、staging、materialize、release store：**73 collected / 73 passed /
  0 skipped / 0 failed / 0 errors**；未连接真实 SQL Server。
- oncology 专项（含候选、工作簿、生命周期、release、双 scope、ownership、API/SSE）：
  **277 collected / 277 passed / 0 skipped / 0 failed / 0 errors**。
- 全仓：**998 collected / 997 passed / 1 skipped / 0 failed / 0 errors**；唯一 skip 为
  `tests/test_runner.py:293` 已有不可达 `for-else` 分支契约，不是本 change 新失败。
- 两份最终 `.xlsx` 离线 CLI 校验均为 `valid=True, errors=0`；Router 重建后为
  159 条 YAML（118 ready、41 drafting）；`openspec validate add-oncology-kb-authoring --strict`
  通过。
- 本次全量候选解析、非裁决预览、runtime criteria、workbook、release/store、SQL safety、
  ownership、integration、tools/SSE/result/regimen 定向组合门禁为
  **254 collected / 254 passed / 0 skipped / 0 failed / 0 errors**。

2026-07-22 在 concept authority、来源多锚点合并、curated target 门禁和文档真相收尾后，
重新执行全仓门禁：**1040 collected / 1039 passed / 1 skipped / 0 failed / 0 errors**；唯一
skip 仍为上述既有不可达分支契约。两份最终工作簿再次离线校验为 `valid=True, errors=0`，
Router 重建仍为 159 条（118 ready、41 drafting），严格 OpenSpec 校验与
`git diff --check` 均通过。

外部门禁状态：以下 2026-07-22 实库补充事实不改变专家/发布门禁。OpenSpec 9.3–9.5、
9.7–9.10 仍待最小权限与备份策略留痕、专家回传/审批、受控导入门禁的完整演练、独立发布授权和完整恢复演练；10.5 的获授权
paired shadow 也待执行。不得把 DDL、staging、generated DRAFT 或 release candidate 写成
专家验收或生产发布。

## 142 generated DRAFT 物化补充（2026-07-22）

- `DB_NAME()` 精确核验为 `知识库_work`；最终幂等 DDL 连续执行两次成功，实际建立 3 个 schema、
  25 张表、6 个中文人工审核视图、129 个约束、44 个索引和 23 个必需触发器。账号仍为 `db_owner` 而非
  最小权限，当前备份策略/恢复点未获证明。
- 首个旧方案 batch 因 concept authority namespace 断裂被触发器阻断；首个资格 batch 因
  同文本多锚点未合并被唯一约束阻断。两次 typed 事务均完整回滚为 0，FAILED batch 和去敏
  reconciliation 保留。
- 修复后两份最终工作簿均为 0 validation error、0 PHI，跨工作簿 typed authority 缺口为 0；
  资格 SHA 为 `sha256:58ad7dc5f10fdbdb249347a8d55d105a7b2e0b83707882aee280bc95287f509c`
  （9,511 staging 行），方案 SHA 为
  `sha256:ff4648982b31c486f6bebc383ee0118a348a385a59d104f9c6160e0befd6deb3`
  （339 行）。两份均已通过 preflight/dry-run。
- 用户重新授权后，资格与方案最终 SHA 分别 materialize 为
  `batch_c2e4cf39a6e406aea8308953`（9,511 staging 行、13,353 个投影实体）和
  `batch_e06cc07d61563c5f48479534`（339 staging 行、339 个投影实体）；各自重复上传均
  `reused=true`。只读对账为 221 条 eligibility revision、462 branch、2,900 node、4 条
  regimen revision、130 alias、11 context、16 component，`review_event=0`、release=0。
  完整去敏证据和后续审核顺序见
  `docs/oncology/authoring/142_draft_seed_import_report.md`。

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
