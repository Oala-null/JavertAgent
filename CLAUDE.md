# CLAUDE.md (Javert)

本文件为 Claude Code (claude.ai/code) 在 Javert 项目内工作时提供导航.

**语言**: 全中文开发 (与 26er/CLAUDE.md 顶层约定一致).



## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.









## 项目目标

把国家医保局 2026《医疗机构自查自纠问题清单》(0325) 中 163 条「做不了」违规情形,
通过 LLM agent + 规则 yaml 的形式做实验性审计.

163 条已按 Javert 当前 4 工具 (`search_fees / search_notes / note_diagnosis / drug_indication`)
的可行性逐条分析:

- **绿区 73 条** — 套 M1/M2/M3 三大模板, 工具直接可审
- **黄区 54 条** — 工具够但每条需单独写 prompt, 信号偏弱
- **红区 36 条** — 当前工具不够 (需诊疗目录 / HR / 跨患者比对等数据), 应标 abandoned 等候后续 change 解锁

详见 `docs/做不了163规则可行性分析.md` 和 `docs/templates/`.

## 三大模板 (M1/M2/M3)

| ID | 名称 | 模式 | 覆盖 | Reference yaml |
|----|------|------|------|---------------|
| M1 | 重复收费 | 主项费用 + 附属费用并存 + 文书无反证 | A 类 28 条 | R191 (已完成) |
| M2 | 过度检查 | 检查 fee 命中 + 诊断无指征 (+ 可选联查药品) | B 类 28 条 | R151 (待补完整) |
| M3 | 口腔串换 | 诊断仅 trivial + fee 见大手术 | G 类 17 条 (口腔) | R245 (待补完整) |

模板设计完整文档在 `docs/templates/模板{1,2,3}.md`, 含 master prompt 骨架 + 全部 73 条
绿区规则的个性化字段填充表.

## 优先级 4 档 (163 条全量分级)

| 档 | 数量 | 含义 |
|----|------|------|
| **P0 高优** | 31 | pilot v0.2 立刻可做 |
| **P1 推荐** | 30 | pilot 跟进 / 口腔强信号待数据切换 |
| **P2 备选** | 63 | 需新工具或需逐条调 prompt |
| **P3 不建议** | 39 | 单病历看不出 / 工具不可见信号 |

专家标注 CSV: `docs/163规则可行性分析表.csv` (5 列: 序号/违规类型/问题/Javert 优先级/要-不要).

## 与同目录其他项目的关系

- `Javert/` 与 `zadig_agent/` 同 Mac 本地共存, 代码完全独立.
- `src/javert/tools/` 内的 4 个工具 + `llm_provider.py` + `tool_executor.py` 拷贝自
  `zadig_agent` v2.10.1 commit. 顶部注释记录 source. 上游升级需手动同步.
- 数据快照: `javert init` 物理拷贝 `../zadig_agent/data/case_notes/case_notes.csv` 与
  `../zadig_agent/data/patients/shi_fee.csv` 到 `data/`. 不软链, 不直读.
- **Ground truth (v0.4 新增)**: 病案首页 ground truth 物理拷贝自 `../shi/db/`:
  - `data/shi_zd.xls` (诊断, 10194 行, maindiag_flag=1 是真主诊)
  - `data/shi_ss.xls` (手术, 6980 行, ICD-9-CM3 临床版 + 医保版双码, main_oprn_flag=1 是主手术)
  - 用途: 病案资料员 HTML 报告 (`scripts/build_clerk_report.py`) 取主诊断 / 主手术替代从 case_notes 派生的不准确字段

- **外部医院数据接入 (v0.7 新增)**: 投资人/合作医院过来现场跑产品的标准接入流程:
  - **接入清单** `docs/数据接入清单.md` — 1 页纸 schema 规范 (4 张表必填字段, 给对方 IT 参考)
  - **列名映射** `configs/column_mapping.yaml` — 左侧 Javert 语义, 右侧外部列名, 对方拿 HIS 原始 CSV 不用改名
  - **ETL 转换** `scripts/etl_import.py` — 外部 CSV/Excel → `data_import/{case_notes,shi_fee,shi_zd,shi_ss}.csv`
    - 必填字段缺失抛错, 可选字段缺失只 warn
    - **文书段落自动拆分**: 整份文书一行 (record_name="入院记录", 内容里嵌 `【主诉】xxx【现病史】yyy`) → 按 `【...】` 标记拆成多行 (子阶段="主诉" 一行, "现病史" 一行)
    - 合成内部复合键 `{hospital_code}-{patient_id} ` 兼容 `bah/ba_id str.contains` 匹配
  - **环境变量切换**: `JAVERT_DATA_DIR=data_import` + `JAVERT_ZD_FILE=shi_zd.csv` + `JAVERT_SS_FILE=shi_ss.csv` 把 audit-patient 切到外部数据
  - **代码侧最小改动**: `config.py` 加 `zd_file/ss_file` 字段; `adapter.py` xlrd → pd.read_excel/read_csv (按扩展名自动选); `patient_overview.py` 硬编码路径改用 `cfg.zd_path/ss_path` 并加 CSV 自动检测
  - **工作台数据合并**: 62 上 `data/case_notes_with_szx.csv` / `shi_fee_with_szx.csv` / `shi_zd_with_szx.csv` / `shi_ss_with_szx.csv` = 现有 106 + szx 5 患者合并, 配 .env 4 个 JAVERT_*_FILE 指向合并文件, 不动原 XLS/CSV (`.env.bak.szx` 备份, 回滚一行 cp 搞定)

## 架构图

```
┌── CLI (cli.py + commands/) ────────────────────────────────────────┐
│ init / list / dry-run / run / mark / report / show /               │
│ audit-patient / web / prompt-fit / template (list/show/validate)   │
└─────────────────────────────────────────────────────────────────────┘
              ↓                              ↓                ↓
┌── audit-engine ──┐  ┌── rule-registry ──┐  ┌── audit-store ──┐
│ runner.py        │  │ rule.py (pydantic)│  │ schema.sql      │
│ prompt_assembler │  │ rule_loader.py    │  │ audit_store.py  │
│ prompts/base.txt │  │ rule_writer.py    │  │ (SqliteStore)   │
│ result.py        │  │ rule_init.py      │  │ result.py       │
│ run_id.py        │  │ state_machine.py  │  └─────────────────┘
└──────────────────┘  └───────────────────┘
              ↓                              ↓
┌── rule-templating ─────────────────────────────────────────────────┐
│ template_model.py (Template + TemplateField pydantic)              │
│ template_loader.py / renderer.py (Jinja2 StrictUndefined)          │
│ vars_validator.py / llm_drafter.py (Qwen 起草兜底)                  │
│ prompt_fit_runner.py (vars / interactive / auto 三模式)            │
└─────────────────────────────────────────────────────────────────────┘
              ↓                              ↓
┌── routing (v0.5 新增, Stage A Router B 单闸) ──────────────────────┐
│ router.py (单闸: status/priority + applicable_* + 弹性 keyword)    │
│ types.py (PatientRecord / FeeItem / RouterDecision)                │
│ adapter.py (CsvLoader + shi_zd → PatientRecord)                    │
│ 数据: data/router/{violation_dict,active_java_rules,               │
│        pruning_rules,javert_rules_index}.json                      │
│ + configs/rule_mapping.json (Java rule 元信息参考, 不参与决策)     │
│ 接入: audit-patient --use-router                                   │
└─────────────────────────────────────────────────────────────────────┘
              ↓                              ↓
┌── tools (10 个; registry 读 manifest 按 status 注册) ┐ ┌── data-access ──┐
│ tool_executor.py (<tool_call> 解析)        │ │ loader.py       │
│ llm_provider.py (Qwen3.5 sglang httpx 直连) │ │ csv_loader.py   │
│ search_notes / search_fees / note_diagnosis │ │ snapshot.py     │
│ drug_indication (52药) / drug_audit_lookup  │ │ sample_pilot.py │
│ search_lab_results / search_examinations    │ │ lab_loader.py   │
│ search_anesthesia / search_pathology (view) │ │ examination_…py │
│ scan_progress_indications / registry.py     │ │                 │
└────────────────────────────────────────────┘ └─────────────────┘
                          ↓                              ↓
                  192.168.31.62:30000             data/*.csv
                  (Qwen3.5-35B sglang)            (3000+ patients)

┌── onboarding (v0.10→v0.12, manifest 驱动数据接入 + 现场自动驾驶) ───┐
│ configs/schema_manifest.yaml  数据模型唯一真相源 (UI/ETL/registry 共读) │
│ configs/field_alias.yaml      国标↔语义别名种子 (自动预填 + 归类覆盖率) │
│ onboarding/manifest_loader.py pydantic 校验 (status/必填/join_key) │
│ onboarding/classifier.py      (v0.12) 文件→表自动归类 最小启发式 (必填覆盖率 + 患者键硬门槛 + 歧义不猜) + default_key_mode │
│ onboarding/etl_engine.py      N 表 ETL (synth/asis/bridge 键归一 + ISO 日期归一 + 会话 date_decisions 覆盖) │
│ onboarding/profiler.py        逐列剖析 + 日期逐列探测 (8 格式族, 无全局 dayfirst, is_ambiguous_dayfirst) │
│ onboarding/join_preflight.py  连接预检 (D4 两 getter 判据 → 🟢🟡🔴) + 时间窗口 │
│ web/onboarding_session.py     (v0.12) 进程内会话态单一真相源 (files/map/classify/stored/date/节点位置, 不落盘 JSON) │
│ web/api/routes_onboarding.py  /session 读写 + /classify + /date-ambiguities + /clear-output + /preflight(路由层打包诊断) + /start(诚实 .loaded.env) │
│ web/templates/onboarding.html + static/onboarding.js  自动驾驶 GUI      │
│   (上传即自动认表 + 绿/琥珀结果卡 + 「调整▾」驾驶舱 + 我的文件→Javert 表流向图 │
│    [真实键标签·可拖节点·点线看明细] + 日期歧义/声明新表 modal + 清空已载入)  │
│ scripts/{javert.zsh,jv_run_all.sh,loaded_status.py}  jv-* 终端命令 (jv-go 一词开跑 + 逐患者进度行) │
└─────────────────────────────────────────────────────────────────────┘
```

> v0.10.1 起「开始审计」按钮更名「载入数据」(诚实: GUI 只做映射+ETL 载入, 不在此跑 LLM;
> 审核走命令行). 载入完写 `data_import/.loaded.env` (JAVERT_SQL_ENABLED=false → 本地 sqlite-only).
> v0.12 (redesign-onboarding-demo-flow): 服务端会话态为单一真相源 (删/重传/刷新都稳, 消灭"增删出问题");
> 上传即自动归类 (绿=就绪/琥珀=需补, 客户不直面 synth/asis/bridge); `.loaded.env` 只写实际产出表
> (缺表客户不炸); 载入面板「去终端敲 `jv-go`」+ 自动复制剪贴板; `jv-go` = source `.loaded.env` + 逐患者进度跑全量.

```
┌── web (v0.6, 部署 192.168.31.62:8090, systemd 纳管) ───────────────┐
│ api/main.py        create_app(with_mssql) + SessionMiddleware      │
│ middleware.py      AuthMiddleware (路径保护 + /workbench 拦截)      │
│ auth.py            bcrypt 12 轮 + session 编解码                    │
│ templating.py      Jinja2 env + verdict color/label filter         │
│ rule_meta.py       111 yaml lazy cache (subtitle)                  │
│ patient_overview.py shi_zd/shi_ss/notes/fees → overview dict       │
│                                                                     │
│ api/routes_auth.py     /login /logout /register(403) /account/pw   │
│ api/routes_workbench.py /workbench, /workbench/{pid}, /review,     │
│                         /api/banner/dismiss, /api/patient/{pid}/raw │
│                         /dashboard, /export                         │
│ api/routes_sse.py      /sse/reviews + EventBus + AuditWatcher       │
│                         (BIGINT id 严格递增追踪, 不用 datetime)     │
│ api/routes_audit.py    /api/audit/run(单规则SSE) /runs;            │
│                         /api/audit/run-batch(批量SSE, ⚠2C平台BFF   │
│                         点菜依赖此契约, 改 SSE 字段先同步 bff)      │
│                                                                     │
│ templates/             base/login/register_closed/password_change/  │
│                        workbench/patient_detail/dashboard/          │
│                        welcome_banner/_sidebar/_patient_overview    │
│ static/                style.css (蓝色商务) + app.js (SSE/Ctrl+F)   │
└─────────────────────────────────────────────────────────────────────┘

┌── workbench 易用性 + evidence-anchoring (v0.9 enhance-workbench-usability) ┐
│ hit_resolver.py    确定性 resolve_hits(run, drug_type) → HitItem[]   │
│   {source,name,code_nat,code_local,restriction,matched_fee_name,    │
│    review_note,anchor} — 编码 join 患者 fee 行 + 限定 join drug_kb   │
│   + 锚点 D3 阶梯 (evidence.anchor→keyword→locator→text→tab); 纯函数  │
│   一组件喂两处: 命中项目块 (#5) + 点证据跳原文 (#3); hits_to/from_json │
│ patient_overview.py +get_fees_sum_map (进程缓存) +get_primary_dx +  │
│   fee_categories 每类挂 items[] (类别就地展开明细 D8)               │
│ routes_workbench    sidebar 富卡片 (fees_sum/primary_dx/updated_at) │
│   + patient_detail 注入 hits_by_run (优先读 anchors_json 缓存现算回退)│
│ _sidebar.html       facet 栏 (tag chip + 费用区间 + 主诊词 + 时间) + │
│   富卡片 data-* (facet 纯前端 show/hide, 叠加 verdict filter)        │
│ patient_detail.html 命中项目块 (code·name + 限定 + data-anchor) +    │
│   评语 title 全文 hover (D9)                                        │
│ app.js   facet 引擎 + 共享高亮模块 + 右侧原文对照面板 openSourcePanel │
│ scripts/backfill_anchors.py  重放确定性逻辑回填 anchors_json (零 LLM)│
│ tools/search_{notes,fees}.py ⟨char/行 locator⟩ + Evidence.anchor 前向 │
└─────────────────────────────────────────────────────────────────────┘
              ↓                                       ↑
┌── persistence (v0.6, 复用 SQLAlchemy + pyodbc + msodbcsql18) ──────┐
│ store/sqlserver_store.py  双写归档 + 工作台 read/write              │
│ scripts/sql/create_javert_tables.sql                                │
│   javert_users / Javert_audit_runs (BIGINT id) /                    │
│   javert_vio_review (含 patient_id/rule_id/username denormalized) / │
│   javert_audit_logs / v_javert_reviews / v_javert_audit_logs       │
│                                                                     │
│ store/audit_store.py (sqlite)      ── source-of-truth              │
│ store/result_persister.py          ── sqlite write → 142 立即推    │
│ web/api/heartbeat.SyncWorker       ── 后台心跳补漏 (失败的 pending) │
└─────────────────────────────────────────────────────────────────────┘
                          ↓
              192.168.31.142:1433 (zadig DB, javert_* 命名空间)
```

## 关键文件

### 代码
| 路径 | 角色 |
|------|------|
| `src/javert/cli.py` | Click 入口, 10 个 subcommand 注册 (含 prompt-fit / template list/show/validate) |
| `src/javert/config.py` | yaml + JAVERT_* env 配置 |
| `src/javert/audit/runner.py` | 核心 agent loop |
| `src/javert/audit/verdict_gate.py` | (add-verdict-gate-layer) 裁决后确定性闸 `apply_gate`: runner 落库前调, 只对 V 生效、只降不升 (V→I/C) + 打标签; 闸集 `configs/verdict_gate.yaml` |
| `src/javert/audit/precheck.py` | (pilot-deterministic-precheck) M1 LLM **之前**的确定性预检 `run_precheck(spec, fee_df)`: A/B 费用并存缺失→短路 CLEAN 零 LLM; 并存→注入窄问题事实块 + 判 V 合并费用行锚点. `Rule.precheck{a_items,b_items}` 声明, `config.precheck` 开关. runner 接线, `scripts/init_m1_precheck.py` 迁移 |
| `src/javert/data/clinical_context.py` | 病案首页硬 ground truth 封装 (shi_ss 手术/麻醉 + shi_zd 诊断), 供 verdict_gate 临床判据 |
| `src/javert/audit/prompts/base.txt` | 基础 system prompt (≥1 工具调用 + fenced JSON) |
| `src/javert/audit/rule.py` | Rule pydantic 模型 (11 字段含 `derived_from_template`) |
| `src/javert/templating/template_model.py` | Template + TemplateField pydantic 模型 |
| `src/javert/templating/{template_loader,renderer,vars_validator,llm_drafter,prompt_fit_runner}.py` | rule-templating capability 全套 |
| `src/javert/store/schema.sql` | audit_runs 表 + 2 索引 |
| `configs/llm.yaml` | 默认配置 (LLM endpoint, paths) |
| `configs/rules/Rxxx.yaml` | 单条规则 (由 javert init 生成) |
| `configs/templates/Mx.yaml` | M1-M8 共享模板 (M1-M7 骗保类 + M8 药品适应症/限定 全 rollout 完成) |
| `src/javert/tools/drug_audit_lookup.py` | (v0.8) 药品违规工具: bulk(患者用药∩KB+病案首页诊断) / single(单药). 与 drug_indication 并存不互扰 |
| `configs/drug_audit_kb.json` | (v0.8) 928 通用名药品监管 KB (限适应症/超说明书/限二线/禁忌症 4 类聚合), 由 build_drug_kb.py 从 4 xlsx 生成, sort_keys 确定性 |
| `scripts/build_drug_kb.py` | (v0.8) 4 份药品 xlsx → drug_audit_kb.json + 命中频次表 (扫『药品通用名』行作表头, 鲁棒于偏移) |
| `scripts/init_drug_rules.py` | (v0.8) 建/装 33 条 M8 药品规则 (R007+RD01-03 类型级 + RD10-37 精选), 每条校验在 KB+命中表 |
| `configs/rule_mapping.json` | (v0.5) Java rule 元信息参考表 + javert→java mapping reference, 当前不参与 router 决策 (单闸下保留) |
| `2026年医疗机构自查自纠问题清单0325.csv` | 实为 .xls (xlrd 可读), 0325 源表 |
| `src/javert/routing/{router,types,adapter,__init__}.py` | (v0.5) Router B 单闸 prefilter — 80% LLM 调用砍掉 |
| `data/router/{violation_dict,active_java_rules,pruning_rules,javert_rules_index}.json` | (v0.5) router 数据 — 14372 字典 + 11 active java rules + 125 yaml 元数据 |
| `规则引擎代码/` | (v0.5) 公司既有 Java 监管引擎源码 (`RuleServiceImpl.doRule` 入口 + `ImsAnalyzer` + 38 Rule/RuleItem 子类) — Phase 2 Python port 参考 |
| `scripts/etl_import.py` | (v0.7→v0.10) 外部医院 CSV/Excel → Javert N 文件 ETL; v0.10 起默认 manifest 驱动 (etl_engine), `--legacy` 走旧硬编码路径 (回归 oracle); 含 `【】` 段落自动拆分 + 必填校验 + 连接预检 |
| `scripts/etl_from_sql.py` | (add-aidb-sql-fallback) 142 `aidb` 6 表 → `data_import/*.csv` 快照桥; SQL 接入兜底. 读 aidb→临时 CSV→复用 `run_etl`/`match_fields`/`join_preflight`. 可测 seam `run_bridge(tables,...)`. CLI `--hospital-code/--output/--dry-run` |
| `scripts/run_aidb_audit.sh` | (add-aidb-sql-fallback) aidb 兜底**一键** (纯 bash, 62 无 zsh 也能跑): `bash scripts/run_aidb_audit.sh <投资方名>` = 拉数+预检 → 跑全部可审核患者(`loaded_status.py --ids`)→ `SQL_ENABLED=true` 实时上 62, 批次标签区分. Mac 别名 `jv-aidb`(javert.zsh 委托本脚本). 须在 62 跑(产出落 62 data_import overlay, 原文 tab 才有数据) |
| `scripts/sql/create_aidb_tables.sql` | (add-aidb-sql-fallback) aidb 6 张 `intake_*` 源表 DDL (列名=`docs/schema` 友好表头, 全 NVARCHAR, 幂等; 只建表不建库). 工程师手填兜底数据 |
| `configs/column_mapping.yaml` | (v0.7→v0.10) 外部列名映射模板 (左 Javert 语义 / 右 外部列名); v0.10 加 labs/exam 节 + `key_mode`(synth/asis/bridge) + `normalize_dates` |
| `scripts/build_field_alias.py` | (v0.10) `_SEED` + column_mapping 反推 → `configs/field_alias.yaml` (国标↔语义别名, 自动预填) |
| `scripts/loaded_status.py` | (v0.10.1→v0.12) 看 data_import 已载入的表/行数/可审核患者 (费用∩文书 裸号) + `.loaded.env` 写入时间; `--ids` 给 jv-run-all 遍历 |
| `scripts/javert.zsh` + `scripts/jv_run_all.sh` | (v0.10.1→v0.12) `jv-*` 终端命令: web/status/**go**/run/run-all/run-bg/watch/clear; `jv-go`=一词开跑 (source `.loaded.env`+摘要+全量), `jv-run-all` 逐患者进度行 `[i/N] 患者号 ✓ xV yI zC` + 失败不中断 + 缺 `JAVERT_*_FILE` WARN 跳过 |
| `src/javert/onboarding/classifier.py` | (v0.12) 文件→表自动归类 (最小启发式: 必填别名覆盖率 + 患者键硬门槛 + 歧义不猜) + `default_key_mode` (via_bridge→bridge/否则synth); GUI/CLI 共用, 别名匹配与前端 aliasMatch 同语义 |
| `src/javert/web/onboarding_session.py` | (v0.12) 进程内会话态单一真相源 (按 session cookie `onb_sid` 索引, 不落盘 JSON): files/map/classify/stored/date_decisions/node_positions + 删文件级联清映射 + 跨文件改绑 + 预检过期追踪 |
| `scripts/build_data_hub_filled.py` | (data-hub) sy(3309患者)+szx全量(song 4701患者) → `Scriv/data_hub_filled/` 23 张 TB_*.csv (631万记录, 含 3 扩展表+码表字典+自检报告); 映射依据 `Scriv/data_hub_关系映射.md` + `syjbk_rbasy_mapping.json`; ⚠日期三坑全踩过: 年在前 ISO 行绝不吃 dayfirst (月日互换) / 纯时间值 ('00:00:00') 会被捏造成"今天"必须置空 / decimal 聚合浮点残差出科学计数法按 scale 归一 |
| `scripts/push_data_hub_filled.py` | (data-hub) data_hub_filled → 142 `TP_data_hub` 库 (Chinese_PRC_CI_AS, 23 表 631万行已推完)。连 142 要 `Encrypt=no` (老 TLS, Driver18 默认强制加密会 Login timeout); CI 排序规则下患者号需大写归一防撞键; pandas3 str-dtype 的 NaN 过 tolist 变 float NaN 绑 datetime 必炸 (显式转 Python None); 幂等: 已有行的表跳过, `--recreate` 重灌, `--only 表名` 单表 |
| `src/javert/data/hub_source.py` | (add-workbench-sql-raw-source) TB_*→内部列契约映射**唯一来源**: 6 个 `fetch_xxx(cn, pids)` 纯函数 + 连接串构造 (复用 config sql_* 凭据, 密码不入源码); `etl_from_data_hub.py` 与工作台 `HubRawSource` 共用 |
| `src/javert/web/hub_raw_source.py` | (add-workbench-sql-raw-source) 工作台 hub 原文源: CSV 双 miss 按患者号实时查 `TP_data_hub` (逐患者 LRU 32 + SQL 异常降级为 miss 不 500); routes 链式回退接线 (raw/labs/exams/主诊断), 开关 `JAVERT_HUB_RAW_ENABLED` 默认关 |
| `scripts/sql/create_data_hub_indexes.sql` | (add-workbench-sql-raw-source) TP_data_hub 逐患者查询 9 索引 (幂等; 5 表 JZLSH + fee⋈EXT + LIS join + 2 RIS), 首查 2.44s→0.31s |
| `scripts/etl_from_data_hub.py` | (data-hub 流B) 142 `TP_data_hub` → 内部 6 文件反向取数桥 (`--patients a,b` / `--all`, 默认出 `data_import_hub/`); 列契约=schema_manifest output_schema, 类别经 MXFYLB 2位码回中文, YCTS 回 result_flag。跑审计: `JAVERT_DATA_DIR=data_import_hub JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv JAVERT_LABS_FILE=lab_results.csv JAVERT_EXAMINATIONS_FILE=examinations.csv JAVERT_SQL_ENABLED=false` + `javert audit-patient <裸号> --use-router`。冒烟已过: szx 周金妹 211530148 router 57→14 条, 13C/1I 零失败 48.8s, trace 读到真数据; J66252 双链路对照 (batch_tag=data-hub-test, 已实时双写 142 工作台) 18 条 16 一致 + 2 条 C↔I 摇摆 (R212/R165, 边界证据模型摇摆, 无 V 级差异)。store 连接串已加 `Encrypt=no` (Mac 直连 142 必需)。⚠ szx(0003) 取数三坑已修 (2026-07-06, hub_source.py `BA_HOSPS` 分支): ①主诊断锚=SYJBK.ZYZD (IH 的 CYZDBZ 和 SYZDK 序号1 都不是主诊语义, 名字建全局码→名字典解析, 次诊=SYZDK 列表) ②文书 DLBT 全空→正文按【段落】拆行 (否则 note_diagnosis 全瞎, "诊断失明"产假阳性 V) ③sy 首页库回填不全, 按院区分支勿全局切 BA 源 |
| `scripts/run_szx_batch.sh` | (v0.7) 5 患者 szx 批跑脚本 (串行, 每患者 router 全 ready + concurrency 5) |
| `scripts/sync_szx_with_tag.sh` | (v0.7) sync-to-mssql + 兜底 UPDATE batch_tag (绕开 `write_audit` 重复跳过 bug) |

### 分析与设计文档
| 路径 | 角色 |
|------|------|
| `docs/how_javert_works.md` | 技术架构说明 (面向院方管理层/信息科/投资方的非技术汇报材料; 系统定位 + 143 条规则 + 工具/Router/8 模板工作原理) |
| `docs/做不了163规则可行性分析.md` | 163 条完整分析 + 工具能力深潜 + pilot 选单 |
| `docs/163规则可行性分析表.csv` | 给专家做 Y/N 标注的 5 列 CSV |
| `docs/templates/模板1_重复收费.md` | M1 模板 markdown 形态 + R045 reference + A 类 28 条填充表 (人类阅读用; 机器可读形态在 `configs/templates/M1.yaml`) |
| `docs/templates/模板2_过度检查.md` | M2 模板 + R151 reference + B 类 28 条填充表 |
| `docs/templates/模板3_口腔串换.md` | M3 模板 + R245 reference + G 类 17 条填充表 |
| `docs/templates/模板4_超标准收费.md` | M4 模板 + R193 reference + E 类 12 条填充表 |
| `docs/templates/模板5_虚构医药服务.md` | M5 模板 + H 类 8 条填充表 |
| `docs/templates/模板6_过度诊疗.md` | M6 模板 + R310 reference + 9 条填充表 |
| `docs/templates/模板7_串换收费.md` | M7 模板 + R083 reference + C 类 20 条填充表 (待 m7-rollout) |
| `docs/y_rules_analysis.md` | (v0.3 旧版, 已被 `y_rules_status_v0_4.md` supersede) 专家 Y 标注 (109 条) vs Javert 装载状态 + 22 条缺口归因分析 |
| `docs/y_rules_status_v0_4.md` | **(v0.4 当前版)** Y 装载现状 92.7% + 质量分档 + 8 条受限条目 |
| `docs/sample_audit_patient.md` | audit-patient 多组实测样本 (组 A-M), 含 v0.4 组 L 10 病人 + v0.5 组 M 50 病人 router 全跑 |
| `docs/v2_1_gate_drug_analysis.md` | **(2026-06-03)** v2.1 批次实测: verdict-gate (v1.7→v2.1, 单次放过 78) + drug-code-match (v2.0→v2.1, 药品 V 104→63) 效果 + 合并精准度双峰 (非药品 76.7%/97.7% 达标 ↔ 药品 38.7% 短板) + 假阳性集中点 (RD20/R007) |
| `scripts/build_clerk_report.py` | 10-tab 病案资料员 HTML 报告生成器 (使用 shi_zd + shi_ss + shi_fee 三件套) |
| `scripts/extract_router_data.py` | (v0.5) 提取规则引擎代码 xls → data/router/*.json (14372 字典 + 11 active rules + pruning hints) |
| `scripts/build_rule_mapping.py` | (v0.5) 扫 125 yaml → javert_rules_index.json + configs/rule_mapping.json (含 applicable_* 字段读出) |
| `scripts/compare_router_off_on.py` | (v0.5) 10 v0.4 病人 router 加/不加 selection 对比 (P0 only + 全 ready), 不调 LLM |
| `scripts/diff_router_runs.py` | (v0.5) 按 created_at cutoff diff 两次 audit_runs (off vs on verdict diff + 漏检检测) |
| `scripts/run_batch_50patients.py` | (v0.5) 50 病人 batch runner, nohup detached, 单 patient fail 不中断, _progress.jsonl 写盘 |
| `scripts/build_50_html.py` | (v0.5) 50 病人审计报告 HTML — 左侧 sidebar list + 右侧详情 (复用 clerk_report 模板) + 质量评估 bar |
| `scripts/quality_eval.py` | (v0.5) 启发式打标 high_conf/questionable/malformed/consistent_c/weak_c (LLM verdict 质量分档) |
| `scripts/build_router_compare_html.py` | (v0.5) J66252 三轮对比 3-tab HTML (v0.4 / off / on v2) |
| `output/clerk_report_v0_4.html` | v0.4 病案资料员视角 10 病人审计报告 (1110 裁决 = 76V/41I/993C) |
| `output/router_v2_50patients.html` | (v0.5) 50 病人 router v2 全 ready 审计报告 (1.07 MB, 1764 裁决 = 275V/120I/1369C) |
| `output/router_compare_J66252.html` | (v0.5) J66252 三轮对比报告 (v0.4 vs off vs on v2, 验证 router 不漏检) |
| `docs/rule_design_guide.md` | yaml 字段含义 + 设计 checklist |
| `docs/template_design_guide.md` | 模板 yaml schema + Jinja2 字段 + 三模式选用指南 (操作者实用手册) |
| `docs/m1_r191_vars.json` | M1 模板 R191 personalization 数据 (round-trip verification 来源) |
| `docs/m1_r045_vars.json` | M1 模板 R045 骨科重复收费 personalization 数据 |
| `docs/sample_run_R191.md` | R191 dry-run 样本 (TODO) |
| `docs/deployment_192_62.md` | (v0.6) 工作台 62 部署 runbook (systemd + .env + 升级流程 + 故障排查) |
| `docs/review_workbench_user_guide.md` | (v0.6) 专家使用手册 (11 节, 从拿账号到改密到 Ctrl+F) |
| `docs/数据接入清单.md` | (v0.7) 给对方医院 IT 的 1 页纸数据规格 (4 张表必填字段 + 时间预期 + 安全承诺) |

### 工作台代码 (v0.6)
| 路径 | 角色 |
|------|------|
| `src/javert/web/api/main.py` | `create_app(with_mssql)` 工厂, lifespan 启 SyncWorker + AuditWatcher |
| `src/javert/web/middleware.py` | `AuthMiddleware` 保护 `/workbench /review /dashboard /export /sse/* /api/banner /api/patient/*`, API 401 + 浏览器 302 |
| `src/javert/web/auth.py` | bcrypt hash/verify (4.x 直接调, 不走 passlib) + session helpers |
| `src/javert/web/templating.py` | Jinja2 env + 自定义 filter (`humanize_delta` / `verdict_color` / `verdict_label` / `format_dt`) |
| `src/javert/web/rule_meta.py` | 111 yaml lazy cache → `{domain}.{violation_type}.P0.模板Mx` subtitle (v0.9 加 `drug_rule_type`) |
| `src/javert/web/patient_overview.py` | shi_zd (`inhosp_diag_name` / `inhosp_diag_code` / `ba_id`) + shi_ss (`oprn_oprt_name` / `main_oprn_flag`) + notes regex (性别/年龄从"病例特点") + fees 聚合; v0.9 加 `get_fees_sum_map` 进程缓存 + `get_primary_dx` + fee_categories 挂 items[] (类别就地展开) |
| `src/javert/web/hit_resolver.py` | (v0.9) 确定性命中项目/锚点解析器: `resolve_hits` → `HitItem[]` (编码 join 患者 fee 行 + 限定 join drug_audit_kb + 锚点 D3 阶梯). 纯函数, `#3 跳转`与`#5 命中项目展示`共用. `hits_to_json`/`hits_from_json` 供 anchors_json 缓存 |
| `scripts/backfill_anchors.py` | (v0.9) 回放确定性逻辑回填 `anchors_json` 缓存 (sqlite/mssql 目标, 不调 LLM, 幂等, 不增删行/不动批注) |
| `src/javert/web/utils/humanize_zh.py` | 中文相对时间 (刚刚 / 5 分钟前 / 昨天 / 上周 / 2 个月前) |
| `src/javert/web/api/routes_auth.py` | `/login` `/logout` `/register` (403 关闭) `/account/password` (改密) + slowapi 限流 5/min |
| `src/javert/web/api/routes_workbench.py` | `/workbench` `/workbench/{pid}` `/review` `/dashboard` `/export` `/api/banner/dismiss` `/api/patient/{pid}/raw` + 单病人导出 |
| `src/javert/web/api/routes_sse.py` | `/sse/reviews` + `EventBus` (asyncio Queue fan-out) + `AuditWatcher` (BIGINT id 严格递增 poll 1Hz) |
| `src/javert/web/templates/*.html` | base / login / register_closed / password_change / workbench / patient_detail / dashboard / welcome_banner / _sidebar / _patient_overview |
| `src/javert/web/static/style.css` | 蓝色商务风 (`--primary #1e40af`) + verdict 语义色 (V/I/C #b91c1c/#b45309/#047857) + tabs / search box / mark.search-hit |
| `src/javert/web/static/app.js` | vanilla JS ~430 行: review form 提交 / SSE 订阅 / banner dismiss / 原始病历 modal + Ctrl+F + 2 tab |
| `src/javert/web/static/favicon.svg` | 32x32 `#1e40af` 底白 "J" |
| `src/javert/store/sqlserver_store.py` | SQLAlchemy + pyodbc + msodbcsql18, NVARCHAR hook **只 coerce string** (datetime/int 让 driver auto-detect, 防死循环), 工作台 read/write 全方法 |
| `src/javert/store/models.py` | pydantic `User` / `ReviewRecord` / `AuditLogRecord` / `SinceLastLoginStats` / `PatientSidebarItem` / `RunWithReviews` / `DashboardStats` |
| `src/javert/commands/ensure_schema.py` | CLI `javert ensure-mssql-schema` (4 表 + 2 视图, 幂等) |
| `src/javert/commands/sync_to_mssql.py` | CLI `javert sync-to-mssql --dry-run/--pending-only --batch-size N` |
| `src/javert/commands/mssql_user.py` | CLI `javert mssql-user list/create/delete/reset-password` (注册关闭, 走 CLI 分配) |
| `scripts/sql/create_javert_tables.sql` | 4 张 javert_* 表 DDL (含 ALTER 老库 migration) + 2 个 v_javert_* JOIN 视图 |
| `scripts/run_batch_new.py` | (v0.6) 第二批 50 病人 (data/batch_50_new.txt) router 全跑 8.7 h |
| `deploy/javert-web.service` | systemd unit (User=admin2, EnvironmentFile=/home/admin2/javert/.env) |
| `deploy/javert-web.env.example` | env 模板 (JAVERT_SQL_* / JAVERT_SESSION_SECRET / JAVERT_ALLOW_REGISTER=false) |

## 常用命令

```bash
# 启动初始化 (首次)
uv run javert init

# 一次性把 docs/163规则可行性分析表.csv 的 priority 灌进所有 yaml + 为 P0 缺失项建骨架
uv run python scripts/sync_priority_csv.py --dry-run   # 看 plan
uv run python scripts/sync_priority_csv.py --write     # 实写

# 单条规则迭代闭环 (规则维度)
uv run javert dry-run R191 --patient J66252        # 看 trace
# (编辑 configs/rules/R191.yaml prompt_addon)
uv run javert dry-run R191 --patient J66252        # 再看
uv run javert mark R191 --status ready             # 满意了往前推

# 批跑 (规则维度: 一条规则 × N 患者)
uv run javert run R191 --pilot

# 患者维度: 一个患者 × 一组规则 (默认 P0, 跳 abandoned)
uv run javert audit-patient J66252                       # 冷启动 baseline
uv run javert audit-patient J66252 --share-tool-cache    # 跨规则共享 ToolExecutor 缓存
uv run javert audit-patient J66252 --share-tool-cache --concurrency 5    # 并发 5 (M1 15 条 → ~4-6 min)
uv run javert audit-patient J66252 --rules R045,R191     # 显式列表 (绕开 priority + abandoned 过滤)
uv run javert audit-patient J66252 --priority P1         # 跑 P1 规则
uv run javert audit-patient J66252 --priority all        # (v0.5) 跑 status=ready 全集 (143 条)

# (v0.5) Router B prefilter: 砍掉 ~70-80% LLM 调用
uv run javert audit-patient J66252 --use-router          # P0 + router, ~7 条 final (vs 57 全跑)
uv run javert audit-patient J66252 --priority all --use-router --concurrency 5  # ready 全集 + router

# (v0.5) Router 数据维护 (yaml 改完后重建)
uv run python scripts/extract_router_data.py    # 重建 data/router/*.json (从规则引擎代码 xls)
uv run python scripts/build_rule_mapping.py     # 扫 125 yaml → javert_rules_index.json + rule_mapping.json
uv run python scripts/test_router_smoke.py      # 5 病人 smoke test 看 final 集合 + 耗时

# (v0.5) Router 效果对比 (不调 LLM)
uv run python scripts/compare_router_off_on.py        # 10 v0.4 病人 P0 / 全 ready 两档对比
uv run python scripts/test_audit_patient_router_dryrun.py  # 3 病人完整 audit-patient 链路 dryrun

# (v0.5) 50 病人 batch + 报告
nohup uv run python scripts/run_batch_50patients.py > output/batch_50/_main.log 2>&1 &  # ~9h 跑
uv run python scripts/build_50_html.py --cutoff-start "<batch-start>" --cutoff-end "2099-01-01"

# (v0.5) 单 patient 三轮对比 (v0.4 / off / on v2)
uv run python scripts/build_router_compare_html.py    # 3-tab 同 patient 不同规则集合

# (v0.8) 药品类规则 (M8 药品适应症/限定审计)
uv run python scripts/build_drug_kb.py                   # 4 xlsx → drug_audit_kb.json + 命中频次表 (改 KB 后重跑)
uv run python scripts/init_drug_rules.py                 # 重建/重渲染 33 条 M8 药品规则 (改 M8.yaml 后必跑)
uv run python scripts/build_rule_mapping.py              # 重建 router index (init_drug_rules 后必跑, router 读 index)
uv run javert audit-patient J90508 --priority all --use-router --concurrency 5  # 综合科药品丰富 (出 V 信号)
uv run javert audit-patient J66252 --priority P1 --use-router --concurrency 5   # 甲状腺 (on-label 闸: 甲状腺片/钙 应 CLEAN)

# (v0.9) evidence-anchoring 命中项目锚点缓存回填 (可选加速; 渲染默认现算, 老数据即生效)
uv run python scripts/backfill_anchors.py --target sqlite --dry-run   # 本地只算不写 (核对)
uv run python scripts/backfill_anchors.py --target mssql              # 142 回填 anchors_json (工作台读此库; 先 ensure-mssql-schema 加列)

# 汇总 / 反查
uv run javert list                                       # 表头加 priority 列 + 末行汇总
uv run javert report --rule R191
uv run javert show <run_id>

# 模板套填 (rule-templating capability)
uv run javert template list                                                # 看 M1-M8 装配状态
uv run javert template show M1                                             # 看 M1 master_prompt + fields
uv run javert template validate M1                                         # empty / partial / ready / error
uv run javert prompt-fit R191 --template M1 --vars docs/m1_r191_vars.json --dry-run --output -   # round-trip 验证
uv run javert prompt-fit R045 --template M1 --vars docs/m1_r045_vars.json  # 写盘, 含 derived_from_template
uv run javert prompt-fit R047 --template M1 --interactive                  # 一字段一字段问
uv run javert prompt-fit R047 --template M1 --auto                         # Qwen 起草后人审确认

# 测试
uv run pytest tests/ -v
```

### 外部医院数据接入

**可视化 (v0.12 现场自动驾驶, `/onboarding`)**: 登录工作台 → **拖 4 个 CSV (系统自动认表 + 预填映射)** →
绿卡=就绪/琥珀卡=「调整▾」补缺必填 → (可选) 连接预检 🟢🟡🔴 (红灯给可执行诊断) → 点「载入数据」(落映射 + 跑 ETL
+ 写诚实 `.loaded.env`) → 面板出「去终端敲 `jv-go`」(已复制剪贴板) → 终端 `jv-go` 一词跑全量.
理想路径 = 两个动作 (拖文件 + 点载入) + 终端一词; 详见 `docs/数据接入清单.md` + `docs/sample_onboarding.md` §八.

```bash
# v0.12 jv-* 终端命令 (source ~/26er/Javert/scripts/javert.zsh 进 ~/.zshrc 后)
jv-web              # 起本地工作台 → 浏览器 /onboarding 拖文件载入
jv-status           # 看 data_import 已载入的表 + 可审核患者 + .loaded.env 写入时间
jv-go               # 一词开跑: 载入后单敲此命令 (source .loaded.env + 摘要 + 逐患者进度跑全量)
jv-run <患者号>     # 跑单患者审核 (实时输出, 本地 sqlite-only, 不入 142/工作台)
jv-run-all          # 跑全部 (前台逐患者进度行); jv-run-bg + jv-watch 后台+tail
jv-clear            # 清空 data_import (或走 GUI「清空已载入」按钮)
```

**CLI (v0.7 等价路径, 手敲 yaml)**:
```bash
# 1. 改 configs/column_mapping.yaml (右侧填对方列名 + hospital_code; v0.10 可加 key_mode/bridge/normalize_dates)
# 2. ETL (dry-run 校验必填+连接预检 → 实跑写 data_import/); 默认 manifest 驱动, --legacy 走旧路径
uv run python scripts/etl_import.py --mapping configs/column_mapping.yaml --dry-run
uv run python scripts/etl_import.py --mapping configs/column_mapping.yaml --output data_import
# 3. 切 env 跑审计 (本地 sqlite-only, 完后批量 sync)
export JAVERT_DATA_DIR=data_import JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv \
       JAVERT_SQL_ENABLED=false JAVERT_BATCH_TAG=<tag>
uv run javert audit-patient <ID> --priority all --use-router --concurrency 5   # 或 bash scripts/run_szx_batch.sh
# 4. sync 到 142 (batch_tag 兜底): bash scripts/sync_szx_with_tag.sh
#    62 工作台合并现有+外部数据: /tmp/merge_szx.py → *_with_szx.csv, .env 加 4 个 JAVERT_*_FILE
```

### 工作台 (v0.6) 运维命令

```bash
# 一次性 schema 建 (142 上幂等; 改 scripts/sql/create_javert_tables.sql 后重跑)
set -a && source .env && set +a
uv run javert ensure-mssql-schema

# sqlite → 142 一次性同步 (或补漏)
uv run javert sync-to-mssql --dry-run               # 看 plan
uv run javert sync-to-mssql --batch-size 200        # 实跑 (3345 行 ~3 min)
uv run javert sync-to-mssql --pending-only          # 双写失败行的补漏

# 账号管理 (注册关闭, 走 CLI)
uv run javert mssql-user list
uv run javert mssql-user create dr_zhang --display-name "张医生(放疗科)"  # 交互式输 2 遍默认密码
uv run javert mssql-user reset-password dr_zhang     # 忘密码兜底
uv run javert mssql-user delete dr_zhang --confirm   # latest review 自动降级 is_latest=0, log 保留

# 起 web (本地 dev; 生产走 systemd)
uv run javert web --with-mssql --host 127.0.0.1 --port 8090
uv run javert web --no-mssql                         # Mac dev, 工作台路径 503

# 第二批 50 病人 batch (在 62 后台跑)
ssh admin2@192.168.31.62
cd ~/javert && set -a && source .env && set +a
nohup ~/.local/bin/uv run python scripts/run_batch_new.py > output/batch_new/_main.log 2>&1 &
disown

# 62 systemd 管理 (需 sudo 密码)
ssh -t admin2@192.168.31.62 'sudo systemctl restart javert-web'
ssh admin2@192.168.31.62 'sudo journalctl -u javert-web -f'

# 升级 src 流程 (Mac 改完 → 62 跑)
tar -czf /tmp/javert-src.tgz --exclude='__pycache__' -C . src
scp /tmp/javert-src.tgz admin2@192.168.31.62:/tmp/
ssh admin2@192.168.31.62 'cd ~/javert && tar xzf /tmp/javert-src.tgz \
    && rm /tmp/javert-src.tgz && find . -name "._*" -delete 2>/dev/null'
ssh -t admin2@192.168.31.62 'sudo systemctl restart javert-web'
```

## 设计原则

1. **规则即文件**: 每条规则一个 yaml, 用 git diff 看演进, 不进 SQL.
2. **模板复用而非逐条手抄**: 绿区 + Y 标注 109 条用 M1-M7 共 7 个模板填字段, 不重复造 prompt; v0.8 加 M8 (药品适应症/限定) 第 8 模板.
3. **数据接入抽象**: 业务代码只调 `DataLoader.get_notes / get_fees`, csv 是初版实现, SQL 留口.
4. **复用 = 拷贝粘贴**: zadig_agent 不是 PyPI 包, 拷代码避免互相干扰; 代价是手动 cherry-pick.
5. **dry-run 是默认**: pilot 阶段所有改动都先 dry-run 5 患者看 trace, 再 mark ready 后批跑.
6. **结构化 verdict**: LLM 必须输出 fenced JSON, 不是自由文本; 一次 repair 兜底, 二次失败转 INCONCLUSIVE. 裁决落库前再过 `verdict_gate` 确定性闸 (`src/javert/audit/verdict_gate.py` 的 `apply_gate`, runner 调用): 只对 VIOLATION 生效、只降不升 (V→I/C) 并打可解释标签, 消假阳性不伤已对判断; 闸集见 `configs/verdict_gate.yaml`, 临床判据来自病案首页 (`clinical_context.py`).
7. **零跨机依赖**: pilot 阶段不连 142 SQL Server, 本地 SQLite 自洽.
8. **N/A 优于乱跑**: 工具不够的规则 (P3) 标 abandoned + notes 说明原因, 不强行写 prompt.

## 与 26er/CLAUDE.md 中 Bug 修复协议的衔接

- 改完代码 → `uv run pytest tests/ -v` 全绿 → 跑 `dry-run` 看 trace 端到端验证 → 写新 yaml/prompt 验证 verdict 实际能产出.
- 不要相信"代码看起来对了". Pilot 阶段每次改 prompt 都 5 患者 dry-run 看 trace.

## 与 openspec workflow 的衔接

```
openspec/
└── changes/
    ├── archive/
    ├── bootstrap-javert-mvp/        # 搭通管道, 已 complete
    └── add-patient-centric-audit/   # 患者维度入口 + P0 baseline 测耗时
        ├── proposal.md
        ├── design.md
        ├── specs/                   # rule-registry/audit-engine/cli 三个 capability 的 ADDED
        └── tasks.md
```

## 路线图 (候选 changes)

已交付见下方变更日志. 未做候选:
- `add-cross-patient-stats` 🔥 — 跨患者算每条规则 V 率, ≥50% 提报系统性违规 (用 106 病人 ~5000 裁决基线); 解锁 R003/R280/R281/R286
- `prompt-cache-optimize` / `tool-call-merge` 🔥 — 提速 (system prompt 顺序前置 hit_rate 36%→60%+ / 复合工具一次拉全 tool calls 8-9→2-3)
- `add-java-engine-port` Phase 2 🟡 — Python 复现 11 valid=1 Java 规则, 独立产 java_violations[] (补"做得了"覆盖, 不省 GPU)
- `add-catalog-loader` 🟡 — 医保药品/诊疗目录工具 (剩余 E 类 drafting; R007 已 v0.8 解锁)
- `add-material-registry` 🟡 — 耗材规格/采购数据 (解锁 R013/R033 + M7 红区难 6 条)
- `add-identity-verification` 🟢 — 解锁 R284 (虚假住院/挂床/冒名); `add-sql-loader` — csv → SQL Server (SqlLoader)

## 变更日志 (按版本; 实测/设计详情见各 docs/ + openspec/changes/)

- **分析阶段**: 163 条逐条可行性分析 (绿/黄/红 + P0-P3) + 3 大模板设计 + 专家 Y/N 标注 CSV. → `docs/做不了163规则可行性分析.md` + `docs/163规则可行性分析表.csv`
- **add-patient-centric-audit**: `javert audit-patient` 患者维度入口 + Rule schema 加 priority + R312 abandoned. P0 baseline 组 A: J66252 冷启动 41.5 min (慢规则全是空骨架 yaml)
- **rule-templating + m1~m7-rollout (→v0.4)**: jinja2 模板地基 + 三模式 prompt-fit CLI; 七模板全 rollout (M1 重复收费 / M2 过度检查 / M3 口腔串换 / M4 超标准 / M5 虚构 / M6 过度诊疗 / M7 串换), **111 条 ready, 专家 Y 覆盖 92.7%**. 14 条 N 清场. 组 E-K 实测 → `docs/sample_audit_patient.md`
- **v0.3 底层修复**: search_notes 边界 (噪音段过滤 + 否认/选项框标注 + ETL 交叉引用探测) + etl-missing-marker + conf 区间校准 + `hospital_config.yaml` 25 科室开关 + runner deadline retry (tc 触顶强制收敛). 有效裁决率 88%→100%
- **v0.4 ground-truth**: 接入 `shi_zd` 诊断 + `shi_ss` 手术 (ICD-9-CM3 双码) + `scripts/build_clerk_report.py` 10-tab HTML. 10 病人全量 = 1110 裁决 (76V/41I/993C). → 组 L + `output/clerk_report_v0_4.html`
- **v0.5 Router B**: 单闸 prefilter (yaml `trigger_keywords` 弹性命中 + `applicable_*` schema), 砍 ~68-80% LLM 调用且 0 漏检; `audit-patient --use-router`. 50 病人全跑 1764 裁决 (275V/120I/1369C). → 组 M + `output/router_v2_50patients.html`. (`规则引擎代码/` 公司 Java 引擎 = Phase 2 port 参考)
- **v0.6 审核工作台**: 142 4 表 + FastAPI/Jinja2/SSE 工作台部署 62:8090 (systemd). 病案概览 (shi_zd/ss/notes) + 三态 review (insert-only is_latest) + dashboard + 导出 + 原始病历 modal (Ctrl+F). 第二批 50 病人 → **累计 106 病人 / 5016 行 / 529V/238I/3287C**. → `docs/deployment_192_62.md` + `docs/review_workbench_user_guide.md`
- **v0.7 外部数据接入**: `scripts/etl_import.py` (列名映射 + `【段落】` 自动拆分 + 合成复合键) + `docs/数据接入清单.md`. 首跑外部院真数据 (szx 5 患者 V=14). config/adapter/patient_overview 支持外部 schema (xls→csv auto)
- **v0.8 药品审计**: M8 模板 + `drug_audit_lookup` 工具 + `drug_audit_kb.json` (928 通用名 KB) + 33 规则 (R007 + RD01-37) + on-label 误报闸. R007 红区 E 解锁. rule_id pattern 放宽 `R\d{3}|RD\d{2,3}`. → `docs/sample_drug_audit.md`
- **v0.9 工作台易用性 + evidence-anchoring** (2026-06-01, 已部署 62): `hit_resolver.py` 确定性命中项目/锚点 (编码 join fee + 限定 join drug_kb + D3 锚点阶梯) + 病人列表 facet/富卡片 + 费用类别就地展开 + parallel 原文对照面板 + 评语 hover + `backfill_anchors.py` 缓存回填 (anchors_json) + search 工具 ⟨locator⟩/`Evidence.anchor` 前向. 静态资源自动 cache-busting (?v=mtime). **333 测试 + 1 skip 绿**. → `openspec/changes/enhance-workbench-usability/`
- **v0.10 可视化数据接入 (add-visual-schema-onboarding)** (2026-06-03): `schema_manifest.yaml` 数据模型唯一真相源 (UI/ETL/registry 三处共读) + `field_alias.yaml` 国标别名种子. ETL 从硬编码 4 表重构为 manifest 驱动 N 表 (老 4 表逐列回归一致, 旧路径留 `--legacy` oracle), 加桥表键归一 (`medcasno→psn_no`, song 实测 0 失配) + 逐列日期 ISO 归一 (8 格式族, 无全局 dayfirst). `/onboarding` 双栏星图拖拽 GUI (上传抽列 + 别名自动预填 + 连接键 synth/asis/bridge + 列剖析 popover + 连接预检 🟢🟡🔴 + 必填/预检两道闸 + 声明新表 stored + 一键审计). 化验/检查接入通道 + `search_anesthesia`/`search_pathology` view 工具 (三档兜底契约 live/view/stored). 工具 4→10. **421 测试 + 1 skip 绿**. → `openspec/changes/add-visual-schema-onboarding/` + `docs/sample_onboarding.md`
- **v0.10.1 onboarding 增强 + 多智能体对抗审查修复** (2026-06-03, 已部署 62): 多智能体审查发现 16 真问题全修 (含 🔴 斜杠年在前日期月日互换 `2025/01/05` + 🔴 `_safe_path` 越权读 `.env` 泄 session secret → 改数据目录白名单; stored spoke 概览可见原文; 时间窗口预检接入; 上传无大小上限 DoS; JS 两道闸 stale + 跨文件 mapping). UX: 上传秒回 (411MB→12ms, `_read_columns` 删全扫改 2MB csv 字节估算行数, 多行文书 0% 误差) + ER 关系图左栏 (患者hub + SVG 连接键边) + 跨文件拖列自动改绑(不再弹窗拦截) + 重置/清空映射 + 删上传文件✕ (仅 _uploads 白名单) + 「开始审计」→「载入数据」(诚实 ETL-only) + 载入完出 `jv-run` handoff + `.loaded.env`. CLI: `scripts/{javert.zsh,jv_run_all.sh,loaded_status.py}` → `jv-status/jv-run/jv-run-all/jv-run-bg/jv-watch/jv-clear/jv-web` (SQL_ENABLED=false 本地 sqlite-only, 不入 142/工作台). **456 测试 + 1 skip 绿**.
- **v0.12 现场演示自动驾驶 (redesign-onboarding-demo-flow)** (2026-06-04): `/onboarding` 从"工程师映射工具"加一层自动驾驶, 面向投资方/合作医院现场演示 (客户当场给陌生脏 CSV, 零查 README/零终端拼命令地跑出审核). **稳 (Must)**: 新 `web/onboarding_session.py` 进程内会话态单一真相源 (按 cookie `onb_sid`, 不落盘 JSON) — 删/重传(同名替换不再生 `_1` 幽灵)/刷新都稳, 冷启动从 `_uploads/` 重建, 消灭"增删出问题". **不炸**: `.loaded.env` 只写实际产出表 (修 `shi_zd`/`shi_ss` 死写, 缺表客户 `audit-patient` 不再找不到文件) + GUI「清空已载入」按钮. **一词收尾**: `jv-go` = source `.loaded.env` + 逐患者进度跑全量, `jv-run-all` 输出 `[i/N] 患者号 ✓ xV yI zC` + 失败不中断 + 缺 `JAVERT_*_FILE` WARN 跳过, 载入面板自动复制 `jv-go` 到剪贴板. **可解释**: 预检红灯路由层打包可执行诊断 (命中最低表 + 成因建议, 不改 `join_preflight` 签名) + 所有失效路径预检过期一致 + 日期歧义 (整列 1–12) 一次性 modal 确认 D/M vs M/D (写会话 `date_decisions`, ETL 据此归一, 绝不静默反转). **丝滑 (High)**: 新 `onboarding/classifier.py` 文件→表自动归类 (最小启发式: 必填别名覆盖率 + 患者键硬门槛 + 歧义不猜, 键模式取 manifest 默认) + 绿/琥珀结果卡 (synth/asis/bridge 收进「调整▾」驾驶舱) + tabular-only (麻醉/病理 view 不进归类). **流向图**: ER 星图 → 我的文件→Javert 表双侧流向图 (连线标签=实际映射的患者键列, 可拖节点, 点线看源列→目标). **Polish**: 节点拖拽位置存会话 + 声明新表表单 modal (替 3 个 `prompt()`). 工具/下游零改. **483 测试 + 1 skip 绿** (含 +19 redesign 测试). → `openspec/changes/redesign-onboarding-demo-flow/` + `docs/sample_onboarding.md` §八. (62 现场冒烟待部署执行)
- **aidb SQL 兜底取数桥 (add-aidb-sql-fallback)** (2026-06-04): 现场对接 fallback —— `/onboarding` 拖 CSV 不顺时, 工程师把数据**按 `docs/schema` 6 模板 1:1 填进 142 `aidb` 库 6 张 `intake_*` 表**, Javert 从 SQL 取数跑. **快照桥** `scripts/etl_from_sql.py`: 读 aidb 6 表 → 落临时 CSV → 复用 `run_etl`/`match_fields`(自动认表, 零硬编码映射)/`join_preflight`(🟢🟡🔴) → 写 `data_import/*.csv` → 正常 `audit-patient`. 建表 `scripts/sql/create_aidb_tables.sql` (列名=`build_intake_templates.META` 友好表头, 全 NVARCHAR, 幂等; 测试断言 DDL 列==META 防漂移). `config.py` 加 `sql_source_database`(默认 `aidb`, env `JAVERT_SQL_SOURCE_DATABASE`) 与结果库 `zadig` 区分. **实时显示 + 新旧区分全复用**: `JAVERT_SQL_ENABLED=true` 双写 142 + SSE 实时刷; `JAVERT_BATCH_TAG=投资方名` → sidebar 批次 chip + facet 一键只看这批 (零新表/零 UI 改动). 一键 `scripts/run_aidb_audit.sh`(纯 bash, 62 无 zsh 也能跑; Mac 别名 `jv-aidb` + bash `scripts/javert.bash` 入 ~/.bashrc): 拉数→预检→跑全部患者→实时上 62, 批次标签区分新旧. **lab/exam overlay 修复**: `LabLoader`/`ExaminationLoader` 加 `overlay_dir` 叠加 `data_import/{lab_results,examinations}.csv` (对齐 `CsvLoader.overlay`), `routes_workbench` lab/exam getter 传 overlay + `reset_loader` 一并重置 — 修 onboarding 导入患者「检验记录」tab 空 (之前只读 base 文件读不到导入患者). 净改动: loaders/工具/runner/router/工作台**一行不改**. **501 测试 + 1 skip 绿** (含 +11 桥测试). **142 aidb 6 表已建 + 端到端真连冒烟通过 (中文表→桥转内部→LLM 审核→实时双写 142→62, 跑完即清) + 已部署 62**. → `scripts/etl_from_sql.py` + `scripts/run_aidb_audit.sh` + `docs/数据接入清单.md` §三. (兜底路; 主路仍 onboarding GUI)
- **v0.11 工作台 UI 增量 (workbench-lab-tab-and-sort)** (2026-06-04, 已部署 62): 4 项前端改动. (1) 病人列表按金额/生成时间顺逆序排序 (`_sidebar.html` 下拉 + `app.js` applySort 纯前端重排, 复用 data-fees/data-updated, 与 facet 筛选正交叠加, 偏好存 sessionStorage); (2) 文书/费用右侧新增「检验记录」tab (检验·化验 + 检查·影像 合并一个 tab, 异常值红底; `/raw` 端点加 labs/exams, LabLoader/ExaminationLoader 单例 + 缺文件优雅降级; 命中项可 trace 跳转, `hit_resolver` lab/exam 锚点统一到 labs tab + 老缓存 exam 锚点前端兜底归并); (3) 命中名修复 — LLM 搜索未命中时占位 locator (`费用明细检索`/`药品类明细检索`/`search_lab_results(...)`) 从 text/locator 按 `keyword=`/引号/括号/**tool_call 实搜词**四级提取真实药品/项目名, 替代按钮上无意义的"费用明细" (62 全量回填实测: 按钮名占位 283→4, 仅剩纯类别级检索无单一项名的死角); (4) 违规卡 `AI 推理+证据` 块默认展开 (details `open`). **464 测试 + 1 skip 绿**. 升级附加步骤 (回填 anchors 刷新已缓存命中名/锚点 + 确认 2 检验 CSV 在 62) 见 `docs/deployment_192_62.md` §10.1. 部署经验: `backfill_anchors.py` 原单一大事务 (commit 在末尾) 会长占 `Javert_audit_runs` 表锁 (~万行/十几分钟), 在线跑会把工作台 list_patients 挡死 (整页变慢 + 病人列表空); 已改为**每 batch_size(默认500) 行分批提交** (`--batch-size`), 锁只短暂持有, 工作台读批间穿插, 可在线安全跑.
- **data-hub 三链打通** (2026-07-02~03): 对接 `Scriv/Data_Hub` 46 张国标 TB_* 表 — 8 域多智能体映射 (330 条字段映射+67 校验修正, `Scriv/data_hub_关系映射.md`); **回填** `build_data_hub_filled.py` (sy 3309 + szx/song 全量 4701 患者 → 23 表 631 万记录, 含 3 张扩展表: 通用文书/费用医保分解/手术医保双码) → **推送** `push_data_hub_filled.py` (142 `TP_data_hub` 库全量已灌) → **反向取数** `etl_from_data_hub.py` (流B, 内部 6 文件契约, zadig_agent 零改动)。双链路对照 J66252 18 条 16 一致无 V 级差异 (工作台 batch_tag=data-hub-test)。交接文档 `Scriv/data_hub_filled/_report.md` (逐表说明+EXT 逐字段, 每次重建自动再生)。`sqlserver_store` 连接串加 `Encrypt=no`。接入路径见 `docs/数据接入清单.md` §四
- **工作台原文 hub SQL 源 (add-workbench-sql-raw-source)** (2026-07-06): 工作台原文/费用/检验/主诊断对 CSV 双 miss 患者按患者号**实时查 142 `TP_data_hub`** — 消灭"批次患者原文要拷 1GB CSV 到 62 + 重启"的人肉同步链路 (szx2.0 4680 患者 404 实发驱动). 链式回退 CSV 先 hub 后 (老患者零行为变化) + 开关 `JAVERT_HUB_RAW_ENABLED` 默认关 + SQL 异常降级 miss 不 500 + 逐患者 LRU. TB_*→内部映射提取为 `data/hub_source.py` 唯一来源 (流B 脚本薄壳化, 排序后逐字节回归; 顺手消掉脚本硬编码密码), `web/hub_raw_source.py` 适配器 + routes 四处接线. 142 建 9 索引 (`create_data_hub_indexes.sql`), 单患者首查 0.31s. **513 测试 + 1 skip 绿** (+16). **已部署 62 + 端到端验证三路径 (hub 患者 200 / J66252 一致 / 双 miss 404)**; 62 overlay 已恢复追加前原状 (szx2 患者走 hub, 1GB 冗余文件消除). → `openspec/changes/add-workbench-sql-raw-source/`
- **进院红线加固 (harden-onsite-redlines)** (2026-07-07, 已部署 62): 2fd3cdc 后仍在的 7 项红线/丢数窗口清零. **PHI**: `/api/patient/{pid}/raw` 留痕 (`javert_audit_logs` action=raw_access, source ∈ csv|hub|rate_limited, 429 也留痕) + 每会话限流 (默认 30/min, env `JAVERT_RAW_RATE_LIMIT` 可调; ⚠ slowapi 默认 `key_style="url"` 每个 pid 独立 bucket 枚举限不住, 已改 `key_style="endpoint"`); session secret fail-fast 挪进 `create_app()` (uvicorn 直起同拦, cli 检查删除 + 设 env 后 `reset_config_cache()` 修 `--no-mssql` 读到陈旧缓存). **run-batch 完整性**: persist 成功才发 `result`, 失败发 `fail`; 未知 rule_id 发 `fail`; fail 事件加 `stage` ∈ audit/persist/unknown_rule (SSE 契约只加不改, ⚠ 2C bff 升级见 `docs/deployment_192_62.md` §10.3 ⑤). **失败隔离**: `llm_provider` 4xx (非429) 抛新 `LlmClientError` 不重试 (R103 类 400 可诊断); `audit_patient` 串行单条失败标 failed 继续 (与并发对齐, summary 不再有 "LLM failed at"). **fees 精确匹配**: `csv_loader` bah 索引子串语义→两级精确 (全键/复合键末段, strip), 短号不再吃长号; `scripts/diff_fee_match.py` 新旧 diff — Mac 3309 键 + 62 3315 键实测**完全一致** (零行为变化, 纯堵洞). **szx hub 兜底**: `hub_source.py` fetch_zd/fetch_ss BA 分支 per-patient 源选择 (IH 剔除集 = SYJBK/SYSSK 真有行的患者, 缺首页行者保留 IH 不再双清零; ⚠ 实测当前 TP_data_hub szx 缺首页行患者数=0, 此闸护的是未来批次); 142 幂等加 BA 四表 5 索引, hub 患者 raw 首查 0.71s (基线 0.31s, 含 BA 名解析仍亚秒). **548 测试 + 1 skip 绿** (+35). **62 冒烟全过** (留痕落行 / R999 unknown_rule 回执 / R191 真跑 persist→result / szx 详情页 / hub 首查). → `openspec/changes/harden-onsite-redlines/` + `docs/deployment_192_62.md` §10.3
- **药品审计精度修复 (fix-drug-audit-precision)** (2026-07-07, 已部署 62): 系统扫描定位的三个恰好都砸在药品规则上的工程缺陷 (v2.1 药品精准 38.7% 短板), 三处小改各自可独立验证. **① 分段截断保全 ground truth**: `runner._truncate` 加必留标记 `RETAIN_HEAD_MARKER` (头部整段保全、只截明细段、尾附"明细已截断 N 字符"; 无标记维持旧尾截断), `drug_audit_lookup` bulk 把病案首页诊断挪进标记前必留段 + `search_notes` 反向语义告警 (`[否认段]/[选项框]`) 挪进必留头部; 上限提 `tool_result_max_chars` (config.py+llm.yaml, 默认 2000). 修复前命中药一多、判"有无适应症"的唯一硬证据 (ground truth) 被尾截 → 模型只能凭 KB 条文倾向判 V. **② conf 底线闸补洞**: `verdict_gate` ③闸 `[floor,ceiling)` → `conf < ceiling` (含 confidence 缺失归 0), 消灭 "VIOLATION conf 0.00" 自相矛盾落库行; `conf_floor` 标弃用 (保留仅 schema 兼容). **③ R205 出⑥存在性闸**: R205 (1 台手术按次超收全麻) 是计数类规则 — 麻醉真实存在恰是违规前提, 存在性证据不构成反证, 移出 `anesthesia_reality_rules` 防造假阴性. **④ M8 精选 28 条收敛**: RD10-RD37 判定逻辑与 R007/RD01-03 bulk 逐字相同 (同患者同药重复计 V), 全部 `status: abandoned` + notes, 药品审计由 4 条 bulk 类型级独占 (R007=限适应症/RD01=超说明书/RD02=限二线/RD03=禁忌症), 覆盖不缩水, 重建 router index; `--rules RDxx` 显式仍可单跑. **548 测试 + 1 skip 绿** (+11 截断/config/gate 测试). **62 已部署 + drug-fix-v2.2 抽样对照** (6 药品活跃患者, 药品 V 24→10 全来自消重复, RD20 首要假阳性源移出可执行集, bulk 覆盖持平; J90508 实证 24 条诊断幸存截断). → `openspec/changes/fix-drug-audit-precision/` + `docs/drug_fix_v2_2_sample.md`
- **LLM 效率提升 (boost-llm-efficiency)** (2026-07-07, src 已部署 62, ⚠ javert-web systemd 重启待手动): 吃掉路线图 `prompt-cache-optimize` + `tool-call-merge` 两候选. **① prompt 静态段前置**: `prompt_assembler.py` tools 段挪到规则个性化段之前 (公共前缀 = base+experience+hospital+tools, 段内容逐字不动); **② 一轮多 tool_call**: `base.txt` 明示可并列 + 双调用示例 (runner 本就支持, 纯 prompting 缺口) — **每规则 LLM 轮数 8-9 → 3.3-4.6 实测约减半**; **③ 工具缓存真并发**: `tool_executor.py` 全局锁改两层 per-key compute-once 锁 (不同 key 不互斥, LabLoader 首建不再挡全部线程); **④ fee 明细信号解锁**: `search_fees` 行输出追加 `单价×数量` + `[开单:科室/医师]` (缺列整体省略, 行锚不动) — M4 超标准/分解/串换科室类从盲跑变可审; **⑤ 工作台顺手项**: `build_overview` 补真 lru_cache (失效接 `reset_caches()`) + detail 页 sidebar 10s TTL 缓存 (不复跑两遍全表 ROW_NUMBER CTE; 设计原"按 pid 窄查询"会砍侧栏导航, 实施改 TTL). **562 测试 + 1 skip 绿** (+14). 实测 (11 患者/499 条, `scripts/compare_llm_efficiency.py` 不落库对照): 同代码基线 V 级翻转 4/384 (1.0%, 双向边界噪声, R130/R225/R134 待专家抽查); cache hit 73%→76-79% (proposal 的 36% 系过时口径); **批吞吐 +12% (0.216→0.242 规则/s), 未如预期减半** — GPU token 吞吐是硬约束, 省的是往返次数非 token 量, 串行/现场 `jv-run` 场景时延收益最大. → `docs/boost_llm_efficiency_实测.md` + `openspec/changes/boost-llm-efficiency/`
- **M1 确定性预检 (pilot-deterministic-precheck)** (2026-07-08, 已部署 62, ⚠ javert-web systemd 重启待手动): 把 M1「重复收费」可确定性判定的事实 (主项 A ∩ 附属 B 是否并存) 从 LLM 自由探索前移到确定性 `audit/precheck.py`. **① 短路**: A 或 B 费用缺失 → 直接 CLEAN 零 LLM 调用 (带 `precheck_tag`); **② 窄问题**: A∩B 并存 → 注入费用事实块, LLM 只用 `search_notes` 核实反证 (不再自己搜费用); **③ 机器锚点**: 判 V 时确定性并入 A/B 命中费用行 evidence (`source=search_fees`), `hit_resolver` 出码+锚点, 与 LLM 引用解耦. `Rule.precheck{a_items,b_items}` 结构化字段 + `config.precheck` 开关 (env `JAVERT_PRECHECK=off` 回滚); 迁移 `scripts/init_m1_precheck.py` 从 prompt_addon 抽取 → 21/22 条获 precheck (R112 bespoke 跨日期比对跳过, 走原路径). `verdict_gate` 零改 (②M2-scoped 不碰 M1, ③对两路同等). **590 测试 + 1 skip 绿** (+12). **实测 100 患者 ON vs OFF** (`scripts/compare_precheck.py`): **LLM 调用 4 vs 6993 = 降 99.9%** · 短路 2099/2100 · **0 真漏检** (唯一告警 = OFF 侧 LLM 噪声, 重跑翻回 CLEAN) · facts V 机器锚点 100%. **关键发现**: 全 3309 患者 A∩B 并存仅 4 例全在 R069 (血液透析); R069 项目集过粗 (a_items 子串命中耗材名 / b_items「滤器」命中呼吸耗材 → K01731 假阳性) → 已给 `_build_fact_block` 加两步语义护栏 (先确认 B 确为 A 附属再核反证), K01731 V→CLEAN 真并存仍 V; R069 项目集精化交 `make-rules-code-portable`. → `docs/precheck_compare_实测.md` + `openspec/changes/archive/2026-07-08-pilot-deterministic-precheck/`
- 🚧 R191 dry-run 样本待补 (`docs/sample_run_R191.md`)
