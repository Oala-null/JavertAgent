# Javert

国家医保局 2026 年自查自纠问题清单 (0325) 中 163 条「做不了」违规情形的 LLM 审计脚手架.

**当前库存**: 运行 `.venv/bin/javert list` 获取实时口径，不要从历史实测报告反推规则数。
当前 authoring change 已将 RD10-RD37 后退为 `drafting/migration-pending`，
默认执行集仍须以该命令的实时状态为准。

**data-hub (2026-07-03)**: 🟢 **数据中台三链打通** — 对接 `Scriv/Data_Hub` 46 张国标 TB_* 表: **回填** (`scripts/build_data_hub_filled.py`, sy 3309 + szx 全量 4701 患者 → 23 表 631 万记录, 含 通用文书/费用医保分解/手术医保双码 3 张扩展表) → **推送** (`scripts/push_data_hub_filled.py` → 142 `TP_data_hub` 库) → **反向取数** (`scripts/etl_from_data_hub.py`, 流B, zadig_agent 零改动). 双链路对照 J66252 裁决 16/18 一致无 V 级差异. 交接文档 `Scriv/data_hub_filled/_report.md`, 接入指引 `docs/数据接入清单.md` §四.

**2C 对接 v3**：新接入使用 `/api/audit/v3/submit` 与 `/api/audit/v3/results/{SYXH}`；在完整规则卡片上按实际收费明细行返回数量、单价、开单科室编码/名称和开单医生工号/名称。`public_explanation.narrative` 是保留持久化 reasoning 语义的只加字段，严格客户端须允许可选/未知字段。`status=running` 时 cards 只是增量结果，必须轮询到 `done`。详见 `docs/2c对接_javert审计服务_v3.md`。

**确定性 Promise 门禁**：已确认的漂移先沉淀为去标识 `DriftCase`，再提炼为带正例、
相邻反例和显式规则 scope 的版本化 Promise。`decision_pre_llm` Promise 可在模型前给出
终局锁定裁决；普通 verdict gate 和历史漂移防护不得改写合法的 `LOCKED` 结果。提交前运行：

```bash
.venv/bin/javert promise validate
.venv/bin/javert promise run
```

两条命令均离线执行，不访问 LLM、网络、SQL Server 或 hub。Promise 只保护已确认的最小
边界，不替代 Rule YAML、precheck、verdict gate 或肿瘤资格条件树。

**62 当前运行口径（2026-08-05）**：Javert 使用 30000 上的
`Qwen/Qwen3.6-35B-A3B-FP8`；W2 试验服务 30002 与 OCR 30001 已停。62 的 Javert 目录为
`production-62` 稀疏 Git 工作树，提交后用 `scripts/deployment_sync.py check` 核验本地与
62 的 HEAD 相等且远端受控工作树 clean；发布步骤见 `docs/deployment_192_62.md` §10。

**v0.12 (2026-06-04)**: 🟢 **现场演示自动驾驶 (redesign-onboarding-demo-flow)** — `/onboarding` 从工程师映射工具加一层自动驾驶, 面向投资方/合作医院现场演示. **稳**: 服务端进程内会话态单一真相源 (`onb_sid` 索引, 不落盘 JSON) — 删/重传(同名替换)/刷新都稳, 消灭"增删出问题"; **不炸**: `.loaded.env` 只写实际产出表 (缺表客户不再炸) + GUI「清空已载入」; **一词收尾**: `jv-go` 一词跑全量 + `jv-run-all` 逐患者进度行 (`[i/N] 患者号 ✓ xV yI zC`) + 载入面板自动复制剪贴板; **可解释**: 预检红灯可执行诊断 (命中最低表 + 成因) + 日期歧义一次性确认 (绝不静默反转); **丝滑**: 上传即自动认表 (`classifier.py` 最小启发式) + 绿/琥珀结果卡 (synth/asis/bridge 收进「调整▾」) + **我的文件→Javert 表流向图** (真实键标签·可拖节点·点线看明细). **483 测试 + 1 skip 绿**. 详见 `docs/sample_onboarding.md` §八.

**v0.10.1 (2026-06-03)**: 🟢 **onboarding 增强 + 多智能体对抗审查** — 审查发现 16 真问题全修 (含 🔴 斜杠年在前日期月日互换 + 🔴 越权读 `.env` 泄 session secret). UX: 上传秒回 (411MB→12ms; 106MB case_notes 字节估算行数 0% 误差) + **ER 关系图**左栏 (患者hub + 连接键边) + 跨文件拖列自动改绑 + 重置/清空映射 + 删上传文件 + 「开始审计」→「**载入数据**」(诚实 ETL-only) + `jv-*` 终端命令 (`jv-status/jv-run/jv-run-all/jv-clear`, 本地 sqlite-only). **456 测试 + 1 skip 绿**.

**v0.10 (2026-06-03)**: 🟢 **可视化数据接入工作室** — `/onboarding` 双栏星图拖拽 GUI 取代手敲 YAML：上传抽列 + 国标别名自动预填 + 连接键声明 (synth/asis/**bridge 桥表归一**) + 列剖析 + 连接预检 🟢🟡🔴 + 必填/预检两道闸 + 一键审计. 底层 `schema_manifest.yaml` 数据模型唯一真相源 (UI/ETL/registry 共读), ETL 重构为 manifest 驱动 N 表 + 逐列日期 ISO 归一 (无全局 dayfirst). 化验/检查接入 + 麻醉/病理 view 工具 (工具 4→10). **421 测试 + 1 skip 绿**. 详见 `docs/sample_onboarding.md`.

**v0.9 (2026-06-01)**: 🟢 **工作台可用性增强 + evidence-anchoring** — 违规卡「命中项目」块 (编码·名称·医保限定) + 点证据 **右侧 parallel 滑出原文对照面板** (非弹窗, 左右比对) + 病人列表 facet (tag/费用区间/主诊/时间) + 富卡片 + 费用类别就地展开 + 长评语 hover. 确定性 `hit_resolver` 对全部 106 病人即时生效 (零 LLM 重跑/零批注扰动). 详见 [审核工作台](#审核工作台-v06) + `docs/review_workbench_user_guide.md`.

**v0.8 (2026-05-29)**: **药品违规审计上线** — M8 模板 + `drug_audit_lookup` 工具 + 928 通用名监管 KB, 32 条规则 (R007 + RD01-03 + RD10-37), on-label 误报闸. 详见 `docs/sample_drug_audit.md`.

**肿瘤医保资格 v2（62 已验收启用）**: M8 当前生产入口收敛为
`RD04/R007/RD01/RD02/RD03` 五条 bulk；`RD04` 已 ready 并在 `on` 模式独占肿瘤医保限定臂；
`RD10-RD37` 在当前 authoring change 中为 `drafting/migration-pending`，不进入
默认执行集。知识 authoring、专家 Excel 和 142 门禁见 `docs/oncology/operations.md`。

**v0.7 (2026-05-27)**: **外部医院数据接入** — ETL (`scripts/etl_import.py`) + 列名映射 + `【段落】` 自动拆分, 首次跑外部院真数据验证不依赖 shi 数据结构. 详见 `docs/数据接入清单.md`.

**v0.6 (2026-05-21)**: 🟢 **审核工作台上线** — 实装专家审核 web 工作台 (FastAPI + Jinja2 + SSE), 部署到 192.168.31.62:8090. 注册关闭走运维分配账号 + 自助改密. 106 病人 / 5016 audit_runs / 529 V 待审. 详见 [审核工作台](#审核工作台-v06).

**v0.5**: 引入 **Router B** (Stage A prefilter) — 病案进来时自动收缩规则集合, 把 LLM 调用砍掉 ~68%-80%, 数据质量保持不变 (跟 v0.4 baseline V 数 ±2). 详见 [Router B](#router-b-stage-a-prefilter) 章节.

163 条已按 Javert 工具可行性做完逐条分级:

| 档 | 含义 | 数量 |
|----|------|------|
| **P0 高优** | pilot v0.2 立刻可做 (绿区强信号, 套模板 M1/M2 直接出 yaml) | 31 |
| **P1 推荐** | pilot 跟进 (绿区中信号 / 口腔强信号待数据切换) | 30 |
| **P2 备选** | 需新工具 / 需逐条调 prompt 才能做 (黄区 + 红区 E) | 63 |
| **P3 不建议** | 单病历看不出 / 工具不可见信号 (红区 F + 弱信号 C/D) | 39 |

## 工作模式

每条规则一个 `configs/rules/Rxxx.yaml`. 主流落地路径:

1. **模板复用** (~73 条绿区): 套 M1/M2/M3 三大模板填字段, 一份 yaml 半小时内成型
2. **逐条编写** (~50 条黄区): 操作者参考 0325 question + example 手写 prompt_addon
3. **N/A 入库**: 红区 ~39 条 status=abandoned + notes 标明原因, 不进批跑

dry-run → 看 trace → 改 yaml → 再 dry-run; 成熟后 `mark ready` / `validated`, 不成熟 `mark abandoned`.

## 八大模板

下表说明模板语义和维护入口，不作为当前 ready 库存。执行集与各模板实时数量统一以
`.venv/bin/javert list` 和 `.venv/bin/javert template list` 为准。

| 模板 | 模式 | 原型/维护入口 | 设计文档 |
|------|------|---------------|----------|
| **M1 重复收费** | A+B 两笔费用并存 + 文书反证 | R191 | `docs/templates/模板1_重复收费.md` |
| **M2 过度检查** | fee 命中 + 诊断无指征 | R151 | `docs/templates/模板2_过度检查.md` |
| **M3 口腔串换** | 诊断仅 trivial + fee 见大手术 | R245 | `docs/templates/模板3_口腔串换.md` |
| **M4 超标准收费** | 实际计价方式 ≠ 诊疗目录条款 | R193 | `docs/templates/模板4_超标准收费.md` |
| **M5 虚构服务** | fee 见 X / 文书无 X | R203 / R317 / R318 | `docs/templates/模板5_虚构医药服务.md` |
| **M6 过度诊疗** | treatment + 排除指征 / 限制超 | R310 | `docs/templates/模板6_过度诊疗.md` |
| **M7 项目身份串换** | 做 X (低价) 收 Y (高价) | R083 | `docs/templates/模板7_串换收费.md` |
| **M8 药品适应症/限定** | 药品 fee 命中 KB + 诊断∉依据（禁忌反向） | `scripts/init_drug_rules.py`；RD04 独立维护 | 见肿瘤运维文档 |

每份模板文档含: master prompt 骨架 + 一条 reference yaml + 所有规则的 personalized 字段填充表.

历史 v0.4 装载快照为 **101/109 = 92.7% ready**，仅用于说明当时的 rollout，详见
`docs/y_rules_status_v0_4.md`。药品类当前仍由 `RD04/R007/RD01-03` 分工维护；是否进入
执行集以及 `RD10-RD37` 的实时状态统一查 `.venv/bin/javert list`，不要从本文推断库存。
v0.8 历史验收见 `docs/sample_drug_audit.md`，当前所有权、专家维护与启用流程见
`docs/oncology/operations.md`。

## 分析产出 (docs/)

| 文件 | 用途 |
|------|------|
| `docs/how_javert_works.md` | 技术架构说明（面向院方管理层/信息科/投资方；工具、Router 与 8 模板工作原理；规则库存以 `javert list` 为准） |
| `docs/2c对接_javert审计服务_v3.md` | 2C 新接入契约：异步轮询、完整 cards、收费明细行量价及开单科室/医生字段 |
| `docs/做不了163规则可行性分析.md` | 163 条完整分析报告 (含 4 工具能力深潜 + 7 tier 分类 + pilot 选单) |
| `docs/163规则可行性分析表.csv` | 给医保领域专家做 Y/N 标注 (4 优先级 + 留空 Y/N 列) |
| `docs/templates/模板{1-7}.md` | 7 大模板的完整设计 + 全 109 条 Y personalization 数据 |
| `docs/sample_drug_audit.md` | (v0.8) M8 药品类规则两批对照实测 + on-label 误报闸验收 + 抽样 ground-truth |
| `docs/oncology/operations.md` | 肿瘤医保资格 v2 知识维护、运行验证、RD04/R007 所有权与回滚 |
| `docs/oncology/kb_authoring_guide.md` | 肿瘤条件树/方案专家 Excel 一页式填写、校验与安全交回说明 |
| `docs/oncology/authoring/source_coverage_matrix.md` | 从生成 JSON 动态查询医院/国家/指南/资产/规则/方案覆盖，不固化漂移计数 |
| `docs/rule_design_guide.md` | yaml 字段含义与设计 checklist |
| `docs/y_rules_status_v0_4.md` | **v0.4 历史快照**：当时 Y 装载 92.7% + 4 档质量分类 + 8 条缺口归因 |
| `docs/y_rules_analysis.md` | (v0.3 旧版) 已被 v0.4 supersede |
| `docs/sample_audit_patient.md` | audit-patient 多组实测 (组 A-M), 含 v0.4 组 L 10 病人全量 + v0.5 组 M 50 病人 router |
| `docs/sample_run_R191.md` | R191 的历史 dry-run 样本（当前已 ready + precheck） |

## Router B (Stage A prefilter)

v0.5 引入: 病案进来时, 先用毫秒级的 Python prefilter 决定哪些 yaml 规则要送 LLM. 工作原理:

1. **每条 yaml 自带 `trigger_keywords`** (e.g. R045 = 肩锁关节+韧带重建, R141 = 肌红蛋白)
2. **跟病人 fee_name + 诊断做弹性匹配**: keyword ≥3 字用 60% prefix (e.g. "病理检查" → "病理" 命中 "病理切片诊断"); ≤2 字精确防泛滥
3. **`applicable_*` 结构化字段** (可选, 灵感来自公司 Java engine `ImsRuleCatch` + `RuleItemBase.checkRuleValid`): visit_type / gender / age_range / diag_codes / departments — yaml 缺省即不限制

命中才送 LLM, 不命中直接 CLEAN. 不替 LLM 做判断, 只决定 "要不要请 LLM 看".

```bash
# 用法 (audit-patient 加 --use-router flag)
uv run javert audit-patient J66252 --priority all --use-router --concurrency 5
```

**实测节省** (10 v0.4 病人 / ready 全集 111 条, 不调 LLM 的 selection 对比):

| 模式 | 不加 router | 加 router | 省 |
|---|---|---|---|
| P0 only (57 条) | 570 LLM 调用 | 150 | **73.7%** |
| 全 ready (111 条) | 1110 LLM 调用 | 206 | **81.4%** |

**50 病人 router 全跑实测** (含 v0.4 10 + 40 分层采样, ready 全集):

- 跑了 9 小时 (实测), vs 不加 router 估算 22 小时, **省 ~13 小时 / 60%**
- 1764 条裁决 = **275 V + 120 I + 1369 C** (49/50 ok, K60258 22/23 部分完成)
- v0.4 baseline 对照 10 病人 V 差异 ±2 (LLM 抖动, 非 router 漏检)
- 抽样 9 条 V ground truth 评估: 8 真 V + 1 边缘 (LLM 字面严)

详细报告:
```bash
open output/router_v2_50patients.html        # 50 病人审计 (1.07 MB)
open output/router_compare_J66252.html       # J66252 三轮对比 (v0.4 / off / on v2)
```

### Router 数据维护

```bash
# yaml 改完后重建 router 数据 (router 依赖 yaml.trigger_keywords + applicable_*)
uv run python scripts/build_rule_mapping.py  # 扫全部规则 yaml → javert_rules_index.json + rule_mapping.json

# 从规则引擎代码 xls 提取 14372 字典 + 11 active rules (一次性, Phase 2 Java engine 也用)
uv run python scripts/extract_router_data.py

# Router 效果对比 (不调 LLM, 验证 prefilter 命中率)
uv run python scripts/compare_router_off_on.py
uv run python scripts/test_router_smoke.py
```

详见 `src/javert/routing/` 模块 + `docs/sample_audit_patient.md` 组 M.

## 审核工作台 (v0.6)

线上地址: **http://192.168.31.62:8090/** (内网, systemd 纳管)

### 部署初始数据基线（2026-05-21）

| 项 | 数 |
|----|----|
| 病人 | 106 (50 老 + 50 新 + 6 测试增量) |
| audit_runs 行 | 5016 (含历史重跑, workbench 走 latest dedup) |
| 待审 V (违规) | 529 |
| 待审 I (不明) | 238 |
| 干净 C | 3287 |

### 核心 capability

| 路径 | 功能 |
|------|------|
| `/login` `/logout` | session cookie (itsdangerous 签名, 30 天), bcrypt 12 轮密码 |
| `/register` | **403 关闭** (走 `javert mssql-user create`) |
| `/account/password` | 自助改密 (旧 + 新 + 确认), 改完强制重登 |
| `/workbench` | sidebar 100+ 病人 (V/I/C 计数 + 已审 N/M 进度 + **v0.9 主诊/¥金额/更新时间富卡片**), filter 4 档, **v0.9 facet (tag/费用区间/主诊关键词/更新时间, 纯前端叠加)**, welcome banner |
| `/workbench/{pid}` | 病案概览 (主诊/手术/费用结构/科室/医师) + 违规卡 + 三态决策 form + 其他专家行 (**v0.9 长评语 hover 全文**) + 原始病历 modal |
| **公开解释与命中项目** | 默认展示完整中文化“审核说明”（保留持久化 reasoning 语义）以及结论、核查项目、收费事实、依据、临床证据和复核事项；结构化事实不从说明反解析，费用/药品命中必须关联患者实际净正收费行，不展示内部规则号或原始 evidence JSON |
| **原文对照面板** | 点命中项目后先按锚点懒加载文书/费用/检验目标页签，再定位高亮；源暂不可用可重试，真实无数据与定位失效分别提示 |
| **v0.9 费用类别展开** | 费用类别表手风琴, 点类别就地展开明细 (编码·名称·次数·金额) |
| 原始病历 modal | 文书 / 费用 / 检验记录按页签加载，支持 Ctrl+F 搜索与高亮跳转 |
| `/review` | POST 提交批复 (V/I/C + 评语), insert-only + is_latest, 事务 |
| `/sse/reviews` | EventSource 长连接, 推 `review_submitted` (同事提交) + `new_audit_run` (新 audit 实时) |
| `/dashboard` | 总进度 + Javert/专家 一致率 + reviewer leaderboard + 规则维度表 |
| `/export?format=xlsx` | 顶栏全量导出 (4 sheet); 病人详情页有 "导出此病人" 单 patient 版 |

### 142 schema (SQL Server)

```
javert_users             — 账号 (bcrypt + last_login)
javert_audit_runs        — 镜像 sqlite audit_runs (BIGINT id 主键, 自增)
javert_vio_review        — 专家批复 insert-only, 含 denormalized patient_id/rule_id/username
javert_audit_logs        — 全用户行为 trail
v_javert_reviews         — review JOIN 视图 (含 agree/disagree flag)
v_javert_audit_logs      — log JOIN 视图 (含 username)
```

### 运维 CLI

```bash
# 142 schema 一次性建 (幂等)
uv run javert ensure-mssql-schema

# 一次性 sqlite → 142 import (3345 行 ~3 min)
uv run javert sync-to-mssql --batch-size 200

# 账号管理 (替自助注册)
uv run javert mssql-user list
uv run javert mssql-user create dr_zhang --display-name "张医生(放疗科)"
uv run javert mssql-user reset-password dr_zhang
uv run javert mssql-user delete dr_zhang --confirm

# 起 web (本地 dev; 生产走 systemd)
uv run javert web --with-mssql --host 0.0.0.0 --port 8090
uv run javert web --no-mssql  # Mac dev, 工作台路径 503
```

详见 `docs/deployment_192_62.md` (runbook) + `docs/review_workbench_user_guide.md` (专家手册).

---

## v0.4 病案资料员视角输出

```bash
uv run python scripts/build_clerk_report.py
open output/clerk_report_v0_4.html
```

10-tab HTML 报告, 每个 tab 一个病人, 含:
- 病案基本信息 (入/出院日期, 住院天数, 收治科室, 经治医师, 主诉, 现病史, 既往史)
- **主诊断** ← `data/shi_zd.xls` 病案首页 ground truth (含 ICD-10)
- **手术列表** ← `data/shi_ss.xls` 病案首页 ground truth (含 ICD-9-CM3 临床版 + 医保版双码, 主刀, 麻醉医师, 等级)
- 费用结构: 按 `medins_chrgitm_type` 中文类别拆分 + Top 15 费用项目 (同名不同规格分行) + 药品/耗材 Top 8
- 审计裁决: V / I / C 三色分组 + reasoning + evidence_json 折叠

## 安装 (uv)

```bash
cd /Users/shane/26er/Javert
uv sync
uv pip install pytest
```

## 首次初始化

```bash
uv run javert init
```

自动完成 5 步:
1. 从 `../zadig_agent/data/` 物理拷贝 `case_notes.csv` + `shi_fee.csv` → `data/`
2. 自动采样 50 患者写入 `data/pilot_patients.txt` (规则: 文书命中甲状腺 + 费用≥10 + 信号覆盖)
3. 从 `2026年医疗机构自查自纠问题清单0325.csv` 生成 34 条 pilot yaml → `configs/rules/`
4. 创建 `output/audit.sqlite`
5. 探活 sglang LLM (`http://192.168.31.62:30000/v1`)

**v0.4 额外手动拷贝** (病案资料员 HTML 报告需要):
```bash
cp /Users/shane/26er/shi/db/shi_zd.xls data/   # 病案首页诊断
cp /Users/shane/26er/shi/db/shi_ss.xls data/   # 病案首页手术
```

```bash
# 已有数据时强制刷新
uv run javert init --refresh-data --rebuild-store
```

## 顶层子命令（实时以 `--help` 为准）

```bash
.venv/bin/javert --help                                   # 动态查看当前命令库存
uv run javert list                                        # 列规则及统计 (含 priority 列)
uv run javert dry-run R191 --patient J66252               # 单跑 + 打印 trace
uv run javert run R191 --patient J66252                   # 单跑 (不打印 trace)
uv run javert run R191 --pilot                            # 批跑 50 患者 (规则维度)
uv run javert audit-patient <去标识测试号>                 # 患者维度: 跑当前 P0 非 abandoned 规则
uv run javert audit-patient J66252 --share-tool-cache     # 跨规则共享 ToolExecutor 缓存
uv run javert audit-patient J66252 --share-tool-cache --concurrency 5  # 并发 5 (15 条 M1 → ~5 min)
uv run javert audit-patient J66252 --rules R045,R191      # 显式列表 (绕过 priority + abandoned)
uv run javert audit-patient J66252 --priority P1          # 按 priority 过滤
uv run javert audit-patient <去标识测试号> --priority all  # ready 全集（实时数量见 javert list）
uv run javert audit-patient J66252 --use-router           # (v0.5) Stage A prefilter, 砍 ~70% LLM
uv run javert audit-patient J66252 --priority all --use-router --concurrency 5  # (v0.5) 推荐配置
uv run javert mark R191 --status ready                    # 状态前进
uv run javert mark R191 --status drafting --force         # 后退需 force
uv run javert report                                      # 全规则汇总
uv run javert report --rule R191 --since 2026-05-01       # 限定汇总
uv run javert show aud_a1b2c3d4e5f6                       # run_id 反查
uv run javert show R191 --patient J66252                  # 取最新一次跑的 trace

# 确定性 Promise 提交门禁（离线、零 LLM/网络/生产数据库）
.venv/bin/javert promise validate
.venv/bin/javert promise run

# 模板套填 (rule-templating capability)
uv run javert template list                                                     # 看 M1-M8 状态
uv run javert template show M1                                                  # 看模板内容
uv run javert template validate M1                                              # empty / partial / ready / error
uv run javert prompt-fit R191 --template M1 --vars docs/m1_r191_vars.json --dry-run --output -  # 验证渲染
uv run javert prompt-fit R045 --template M1 --vars docs/m1_r045_vars.json       # 写盘 + 标 derived_from_template
uv run javert prompt-fit R047 --template M1 --interactive                       # 一字段一字段问
uv run javert prompt-fit R047 --template M1 --auto                              # Qwen 起草 → 人审 [y/N]

# 肿瘤知识专家维护（本地 export/validate；142 已有待审 DRAFT，仅供人工审阅）
.venv/bin/javert oncology-kb --help
.venv/bin/javert oncology-kb validate <workbook.xlsx> --kind eligibility
.venv/bin/javert oncology-kb validate <workbook.xlsx> --kind regimen

# 获授权后的命令参数以各自 --help 为准；执行顺序见 docs/oncology/operations.md
.venv/bin/javert oncology-kb approve --help
.venv/bin/javert oncology-kb release-authority --help
.venv/bin/javert oncology-kb release-build --help
.venv/bin/javert oncology-kb release-publish --help
.venv/bin/javert oncology-kb release-rollback --help
```

release 链路已是 operational 实现：`approve` 只投影 append-only 最新审核事件，并要求
`reviewed_content_checksum` 精确匹配当前 typed 内容；只读
`release-authority` 固定 source、curated、pathology 三个外部 checksum pin；
`release-build` 只登记数据库 `CANDIDATE`，只有 `release-publish` 会用相同 pin 重建校验、
写不可变 `PUBLISHED` 本地 bundle 并切 active pointer。领域审核人与 release operator 必须
分离；publish/rollback 跨数据库与本地指针失败时执行补偿，使用原参数重试仍会重新校验。
截至 2026-07-22，142 `知识库_work` 已实际执行幂等 DDL 并建立 6 个中文审核视图；两次
历史 generated DRAFT 物化探针均由单事务完整回滚；用户重新授权后，最终修正版已通过离线校验、
preflight、服务端校验并完成物化，23 个必需触发器均已启用。当前数据仍是待审 DRAFT，
`review_event=0`、release=0。详见
`docs/oncology/authoring/142_draft_seed_import_report.md`。这不代表专家批准或发布；生产
publish/rollback、62 published bundle 启用和 paired shadow 仍未执行。

## 一次性脚本

```bash
# 把 docs/163规则可行性分析表.csv 的 priority 灌进所有 yaml + 为 P0 缺失项建骨架
uv run python scripts/sync_priority_csv.py --dry-run      # 先看 plan
uv run python scripts/sync_priority_csv.py --write        # 实写盘
```

## 配置

默认 `configs/llm.yaml`. 任意字段可被环境变量 `JAVERT_<UPPER>` 覆盖, 例如:

```bash
JAVERT_MAX_TOOL_CALLS=15 uv run javert dry-run R191 --patient J66252
JAVERT_ONCOLOGY_ELIGIBILITY_V2=shadow uv run javert audit-patient <去标识测试号> --rules RD04
```

`JAVERT_ONCOLOGY_ELIGIBILITY_V2` 仅允许 `off|shadow|on`，仓库默认 `off`；62 的生产值
为 `on`。所有权与回滚见 `docs/oncology/operations.md`。

## 状态机

```
drafting ──> ready ──> validated
   │         │           │
   ▼         ▼           ▼
abandoned (任意状态可达, 无需 force)
```

后退方向 (`validated→ready`, `ready→drafting`) 必须 `--force`.

## 规则字段 (yaml schema)

详见 `docs/rule_design_guide.md` 与 `configs/rules/R191.yaml` 示例.

## Change 索引（已交付历史 + 当前候选）

带 ✅ 的旧数量是该 change 完成时的历史口径，不应相加推导当前库存；当前状态看本页顶部和
`javert list`。未带 ✅ 的条目才是候选。

| Change | 范围 | 解锁 |
|--------|------|------|
| `bootstrap-javert-mvp` | 搭通管道 + R191 跑通 | 1 条 |
| `add-patient-centric-audit` | 患者维度入口 + Rule.priority + P0 baseline | 30 条 P0 |
| `add-rule-template-fitter` ✅ | rule-templating capability + prompt-fit CLI + M1 完成 + 14 N 清场 | 41 条 yaml 全 Y |
| `m1-rollout` ✅ | 14 条 M1-类规则装 prompt_addon + R191 加 derived_from_template, 15 条 M1-set 全 ready | 15 条 M1 |
| `fix-tool-patient-id-default` ✅ | ToolExecutor 加 patient_id 自动注入, Runner 边界 set/clear, 砍 LLM 漏传重试 | 全量提速 |
| `add-parallel-audit` ✅ | `audit-patient --concurrency N` (ThreadPoolExecutor + 锁保护), 15 条 M1 跑到 ~5 min | 全量提速 |
| `m2-rollout` ✅ | M2.yaml 模板装满 (14 fields + jinja2 drug_check/single_count/special_notes/pilot_caveat 条件块) + 18 条 B 类规则装 prompt_addon, ready:33/41 | 18 条 M2 |
| `m3-rollout` ✅ | M3.yaml 模板装满 (15 fields + jinja2 dental_dept_check/self_pay_bonus/total_amount_threshold/pilot_caveat/subclass 条件块) + 17 条 G 类 init + 装 prompt_addon, priority=P1, ready:50/58 | 17 条 M3 |
| `m5-rollout` ✅ | M5.yaml 模板装满 (12 fields + jinja2 dept_check/supporting_dx/special_notes 条件块) + 设计 doc 写就 + 10 条 H 类 init + 8 条主审装 prompt_addon (R015/R037/R080/R103/R105/R134/R203/R224), 2 条 R003/R004 留 drafting (跨患者/采购数据), ready:58/68 | 8 条 M5 主审 |
| `m6-rollout` ✅ | M6.yaml 模板装满 (13 fields + jinja2 exclusion_dx 硬证据/notes_evidence 条件块) + 设计 doc 写就 + 14 条 init (7 主审 R218/R221/R222/R225/R310/R311/R312 + 7 特殊 R280-R286 留 P2 drafting), **首次组 J 出 V=2**, ready:65/82 | 7 条 M6 主审 |
| `m4-rollout` ✅ | M4.yaml 装满 (14 fields + 量化计算引导) + 设计 doc + 12 条 E 类装诊疗目录条款 + ready | 12 条 M4 |
| `m7-rollout` ✅ (v0.4) | M7.yaml + 20 条 P2 Y 串换规则装 prompt_addon, **七模板闭环 ready:111**, Y 覆盖率 74.3% → 92.7% | 20 条 M7 |
| `add-ground-truth-integration` ✅ (v0.4) | 接入 shi_zd.xls (诊断) + shi_ss.xls (手术 ICD-9), `scripts/build_clerk_report.py` 出 10-tab 病案资料员 HTML | 报告层 |
| `router-b-v1` ✅ (v0.5, **废弃**) | 双闸 prefilter (Java 字典 AND yaml keyword), J66252 P0 省 87% 但漏检 3 V (M5/M6 fee-notes 模式跟 Java 字典语义不 overlap) | 设计教训 |
| `router-b-v2` ✅ (v0.5) | 单闸 + 弹性 keyword (60% prefix) + applicable_* schema. J66252 P0 重测 0 漏检. cli `--use-router` flag + `--priority all` | 全量提速 ~68-80% |
| `add-50patient-batch` ✅ (v0.5) | 50 病人 (含 v0.4 10 + 40 分层) × ready 全集 × router v2 跑了 9 小时, 1764 裁决 = 275V/120I/1369C, V 数跟 v0.4 baseline 数量级吻合 | 验证层 |
| `add-review-workbench` ✅ (v0.6) | 142 4 张表 + sqlite 3345 行 import + FastAPI 工作台 (login/register-closed/workbench/sidebar/violation card/3-态 review/SSE/dashboard/export/raw-modal+Ctrl+F+tab 切换/single-patient export) + systemd 部署 62 + bcrypt + 改密 + 病案概览 (shi_zd ground truth) + 规则副标题 (`domain.violation_type.priority.模板Mx`) + dedup latest-per-(rule_id, patient_id) + NVARCHAR hook 类型感知 + audit_watcher BIGINT id 追踪 (不死循环) | 交付层 |
| `add-batch-new-50` ✅ (v0.6) | 第二批 50 病人 (J/K + 50-400 段文书) 全 router 跑 8.7 h, 48 ok + 2 部分失败, 累计 106 患者 / 5016 行 / 529 V / 238 I / 3287 C | 验证 + 体量 |
| `add-external-data-import` ✅ (v0.7) | 外部医院数据接入: ETL (`etl_import.py`) + 列名映射 + `【段落】` 自动拆分 + 合成复合键, 首跑外部院真数据 (szx 5 患者) | 接入层 |
| `add-drug-audit-rules` ✅ (v0.8) | 药品违规审计: M8 模板 + `drug_audit_lookup` + 928 药监管 KB + 32 条规则 (R007 + RD01-03 + RD10-37) + on-label 误报闸, R007 红区 E 解锁 | 32 条 M8 |
| `strengthen-oncology-drug-eligibility` ✅（62 on） | M8 生产入口收敛为 RD04/R007/RD01-03 bulk；RD04 接结构化肿瘤资格并独占肿瘤医保限定臂；RD10-RD37 abandoned | 肿瘤医保限定 v2 |
| `boost-llm-efficiency` ✅ | prompt 公共前缀前置 + 单轮多 tool call + per-key 工具缓存；历史实测见 `docs/boost_llm_efficiency_实测.md` | 已交付原 `prompt-cache-optimize` / `tool-call-merge` |
| `enhance-workbench-usability` ✅ (v0.9) | 工作台 5 项易用性 + evidence-anchoring: `hit_resolver` 命中项目/锚点 + facet 富卡片 + 费用类别展开 + parallel 原文对照面板 + 评语 hover + `anchors_json` 缓存回填 | 工作台 + 锚点 |
| `add-cross-patient-stats` (候选, 最高) | 跨患者算每条规则 V 率, ≥50% 提报医院级整改 (利用 100+ 病人 ~5000 裁决基线) | 解锁 R003/R280/R281/R286 + 系统性违规量化 |
| `add-java-engine-port` Phase 2 (候选) | Python 复现 11 valid=1 Java 规则 + LLM 润色 warn_msg, 独立 Track A 输出 java_violations[] | 补"做得了"覆盖 (不省 GPU 但拓宽监管面) |
| `evidence-source-extend` (候选) | base.txt evidence source 加 hospital_config | R212/R220 设计语义准确化 |
| `add-material-registry` (候选) | 引入耗材规格/采购数据 | R013/R033 P3 Y + M7 红色难 6 条 |
| `add-catalog-loader` (候选) | 引入医保药品/诊疗目录 yaml (R007 已由 v0.8 M8 解锁) | 剩余 E 类 drafting |
| `reasoning-precision-tune` (候选) | base.txt 加 "区分子项不要合并主项" | 修 J19333 R146 这类细节漂移 |

## 测试

```bash
uv run pytest tests/ -v
```

2026-08-05 记录的完整套件为 `1104 collected / 1103 passed / 1 skipped /
0 failed / 0 errors`。唯一 skip 是已有不可达 `for-else` 分支契约；本轮公开说明与 Promise
门禁见 `docs/CHANGES.md` 和 `openspec/changes/add-evolving-promise-harness/verification.md`，
oncology 历史分组计数与外部门禁见 `docs/oncology/qa_report.md`。

## 与 zadig_agent 的关系

代码层完全独立. `src/javert/tools/` 下的 search/note/drug 工具 + `llm_provider.py` + `tool_executor.py`
最初拷贝自 zadig_agent v2.10.1 (顶部注释记录 source); `drug_audit_lookup` (v0.8) 与 `hit_resolver` (v0.9) 为本项目原创. 上游 bugfix 需要手动 cherry-pick.

## 架构 / 设计取舍

详见 `CLAUDE.md` 与 `openspec/changes/bootstrap-javert-mvp/design.md`.
