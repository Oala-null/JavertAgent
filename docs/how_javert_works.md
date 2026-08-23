# Javert 智能审计系统 — 技术架构说明

> 面向院方管理层与信息科的技术汇报材料

---

## 一、系统定位

Javert 是基于大语言模型 (LLM) 的**医保违规智能审计系统**。

对标国家医保局 2026《医疗机构自查自纠问题清单》(0325 版) 中 163 条违规情形，Javert 逐条审计每位住院患者的费用明细与病历文书，输出**违规 (VIOLATION) / 合规 (CLEAN) / 待核 (INCONCLUSIVE)** 三档裁决，并附带完整的证据链与置信度评分。

**核心价值**: 把人工逐页翻阅费用与病历的工作转成可批跑、可追溯的证据审计。规则执行集复用
8 套模板 M1-M8；实时规则状态以 `.venv/bin/javert list` 为准。单患者耗时随 Router 候选数、
数据完整度和模型负载变化，历史性能快照见 `docs/performance_report.md`。

---

## 二、系统能做什么

### 2.1 审计覆盖范围

| 违规大类 | 典型场景 |
|----------|----------|
| 重复收费 (M1) | PET-CT 重复收报告费、骨科内固定重复计费 |
| 过度检查 (M2) | 无指征 CT/MRI 复查、不必要的肿瘤标志物 |
| 口腔串换 (M3) | 简单拔牙套取种植手术编码 |
| 超标准收费 (M4) | 超出诊疗目录限价、加收不合规 |
| 虚构医药服务 (M5) | 费用有而病历无对应操作记录 |
| 过度诊疗 (M6) | 无适应症的有创操作、超疗程用药 |
| 串换收费 (M7) | 低价项目套高价编码 |
| 药品违规 (M8) | 药品超说明书适应症 / 超医保限定支付（限适应症/超说明书/限二线/禁忌症） |

各类当前规则数和状态以 `.venv/bin/javert list` 为准；本文不复制易漂移库存。

> 药品规则当前以 `RD04/R007/RD01/RD02/RD03` 五条 bulk 为生产入口；`RD10-RD37`
> 为保全精选知识原子已转为 `drafting`，在迁移台账逐条核验前不进入默认批跑。
> 62 的现行 legacy 肿瘤资格 v2 已切换为 `on`：RD04 独占肿瘤医保限定，
> R007 保留非肿瘤范围。

### 2.2 输出物

每位患者 × 每条规则 = 一份结构化裁决:

```
┌─ 裁决 ────────────────────────────────────────────────┐
│ 规则: R191 (肿瘤重复收费)                              │
│ 患者: DEID-PATIENT-001                                 │
│ 结论: VIOLATION (违规)                                  │
│ 置信度: 0.90                                            │
│ 证据:                                                   │
│   [1] 费用: "全身断层显像" ¥3,200 × 1 (2026-03-15)    │
│   [2] 费用: "图文报告" ¥50 × 1 (2026-03-15)           │
│   [3] 文书: 影像报告中确认为同一次 PET-CT 检查         │
│ 推理: 全身断层显像本身已含报告解读费用,                 │
│       另行收取图文报告费属于重复计费。                   │
└────────────────────────────────────────────────────────┘
```

对2C系统的新接入通过 v3 异步契约输出同一批规则卡片。每张 card 表示一条规则裁决，
即使没有实际费用命中也会返回；`matched_items[]` 只表示该卡关联的收费明细，并按源收费行
携带数量、单价、开单科室和开单医生。调用方必须轮询到 `status=done`，不能把 running
阶段的增量 cards 当作完整结果。字段定义见 `docs/2c对接_javert审计服务_v3.md`。
v2/v3 在同一 attempt 内按 run 增量复用卡片投影，并对同 attempt 并发查询做单飞保护；
该有界进程内缓存只优化轮询延迟，不改变审计结果或接口契约。
公开 presenter 还提供默认展示的 `public_explanation.narrative`，完整保留持久化 reasoning 的
中文语义；其他结构化事实不从这段说明反解析，调用方应允许未知的 additive 字段。

---

## 三、工作原理

### 3.1 整体架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Javert 审计引擎                            │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  ① 规则库（执行集以 javert list 为准）② 模板库（8 套 M1-M8）│
│       ↓                         ↓                            │
│  ③ Router 预筛 ──→ 与本患者相关的规则子集                    │
│       ↓                                                      │
│  ④ 确定性 Promise ──→ 已确认最小边界可终局锁定、零 LLM       │
│       ↓ 未命中                                               │
│  ⑤ precheck / LLM Agent / 结构化求值 (按规则能力选择)         │
│       │                                                      │
│       ├── 调用工具 ──→ 费用查询 / 文书查询 / 诊断 / 药品    │
│       ├── 分析证据                                           │
│       └── 输出裁决 (V / C / I + 置信度 + 证据链)             │
│       ↓                                                      │
│  ⑥ verdict gate → 结果存储 (SQLite + SQL Server)             │
│       ↓                                                      │
│  ⑦ 公开 presenter → 专家工作台 / 2C (内部字段兼容保留)       │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 单条规则的审计流程 (以 R191 "肿瘤全身断层显像重复收报告费" 为例)

```
步骤 1: 组装审计指令
  ┌─ 基础 prompt (审计员角色 + 工具使用规范 + 裁决格式)
  ├─ 规则内容 (R191 的违规定义 + 触发词 + 判定标准)
  ├─ 医院配置 (科室开关: 核医学科=开启)
  └─ 经验库 (同类规则历史审计经验)

步骤 2: Promise / precheck 确定性检查
  ├─ 命中 active terminal Promise → LOCKED 裁决，零 LLM
  └─ 未命中 → 继续既有 precheck / Agent 链路

步骤 3: Agent 循环 (LLM 自主决定查什么)
  ┌─ Turn 1: LLM → 调用 search_fees(keyword="全身断层")
  │          → 返回: "全身断层显像 ¥3,200 × 1, 2026-03-15"
  │
  ├─ Turn 2: LLM → 调用 search_fees(keyword="图文报告")
  │          → 返回: "图文报告 ¥50 × 1, 2026-03-15"
  │
  ├─ Turn 3: LLM → 调用 search_notes(keyword="PET-CT")
  │          → 返回: "核医学科影像报告: PET-CT 全身断层…"
  │
  └─ Turn 4: LLM → 输出裁决 JSON
             → VIOLATION, confidence=0.90, 证据 3 条

步骤 4: verdict gate → 存储裁决、可空 Promise trace 与证据链
步骤 5: public presenter 投影医生可读解释；内部追溯字段继续兼容保存
```

**关键设计**: 需要 LLM 的规则不会盲目跑完所有工具，而是根据规则内容**自主决策**先查什么、
再查什么。确定性能力会更早结束：M1 precheck 不满足费用共现时直接 CLEAN；RD04 在 `on`
模式无净正收费候选时也直接 CLEAN，均不调用 LLM。例如：
- 如果费用中根本没有"全身断层"相关条目 → **第 1 步就返回 CLEAN**，不浪费后续查询
- 如果费用命中但文书有合理解释 → 继续查文书再判定

Promise 来自“已确认漂移 → 去标识 DriftCase → 最小 Promise → 正例/相邻反例”的受控流程。
active 版本不得就地改内容；扩大或收窄边界必须新建版本并显式 `supersedes`。提交门禁
`.venv/bin/javert promise validate` 校验 schema、scope、版本链、隐私和行为类别映射，
`.venv/bin/javert promise run` 重复执行全部案例并比较规范化结果。两者均离线运行。

### 3.3 十二个审计工具

Javert 的审计运行时不把原始数据整包塞给模型，而是通过**十二个专用工具**按需查询：

| 工具 | 功能 | 典型查询 | 返回量 |
|------|------|----------|--------|
| **search_fees** | 费用检索 | "全身断层" / "冰袋" / 按类别浏览 | 关键词命中项 (≤20 条) |
| **search_notes** | 文书检索 | "手术记录" / "麻醉" / 按科室浏览 | 关键段落 (≤10 条, ≤500 字/条) |
| **search_orders** | 医嘱检索 | "结膜囊冲洗" / ST / 长期医嘱 | 结构化医嘱；缺失时为明确标注的全文候选 |
| **catalog_lookup** | 版本化诊疗目录 | 项目编码/名称 + 服务日期 | 当日有效计价单位、价格、备注与条款 |
| **note_diagnosis** | 诊断提取 | 自动从入院/出院/术前等 14 个阶段汇总 | 诊断列表 (去重排序) |
| **scan_progress_indications** | 病程指征扫描 | 按症状/疗效/进展词扫描病程 | 结构化指征片段 |
| **drug_indication** | 药品适应症 (52 药内置表, M2 反向洗白) | "奥沙利铂" → ICD-9 适应症 + 禁忌 | 适应症 + 编码 |
| **drug_audit_lookup** (v0.8+) | 患者净正用药 ∩ 928 药监管 KB + 病案首页诊断；RD04 可附条件树/方案证据 | bulk(patient) → 命中药 + 限定/说明书依据 | 命中药 + 诊断 + 可选资格证明 |
| **search_examinations** (v0.8) | 检查报告检索 | "CT" / "超声" → 报告原文 | 结构化检查报告；OCR 缺表时为全文候选 |
| **search_lab_results** (v0.8) | 检验结果检索 | "血小板" → 数值 + 参考区间 | 结构化检验值；OCR 缺表时为全文候选 |
| **search_anesthesia** | 麻醉方式视图 | 查病案首页手术/麻醉记录 | 弱信号或缺失说明 |
| **search_pathology** | 病理报告视图 | 查病理/检验来源文本 | 弱信号或缺失说明 |

**不是全量扫描，而是关键字定向查询** — 一个患者可能有 700 条费用、150 条文书，但每次工具调用只返回与当前规则相关的少量匹配结果。

### 3.4 Router 智能预筛 (70% 规则直接跳过)

审 1 位患者不需要跑全部规则。Router 在 LLM 介入之前完成三层过滤；规则状态与候选数量
随工作树变化，以下只说明过程，实时库存统一以 `.venv/bin/javert list` 为准：

```
第一层: 规则状态过滤
  全部 YAML → 去掉 abandoned/drafting → 当前 ready 执行集

第二层: 患者属性过滤 (applicable_* 字段)
  ├─ 科室: 该患者非口腔科 → 跳过 17 条口腔规则 (M3)
  ├─ 性别: 该患者为男性 → 跳过妇科规则
  ├─ 年龄: 该患者 45 岁 → 跳过儿科/老年规则
  └─ 诊断: 该患者 ICD C73 甲状腺癌 → 跳过无关瘤种

第三层: 关键字匹配 (trigger_keywords)
  ├─ 该患者费用+诊断中有 "全身断层" → R191 保留
  ├─ 该患者费用中无 "种植体" → R233-R249 全跳过
  └─ 弹性匹配: ≥3 字关键词取 60% 前缀 ("病理检查" → 匹配 "病理")

结果: ready 执行集 → 与当前患者相关的候选子集
```

### 3.5 规则模板体系

当前规则不是逐条手写 — 可复用部分由 **8 套共享模板 (M1-M8)** 批量生成：

```
模板 M1 (重复收费):
  master_prompt = "检查该患者是否同时存在 {main_fee} 和 {附属 fee}..."
  ×
  22 条规则各自填入:
    R191: main_fee="全身断层显像", sub_fee="图文报告"
    R045: main_fee="骨科内固定", sub_fee="固定材料费"
    ...
  =
  22 条完整审计 prompt (结构一致, 内容个性化)
```

**好处**: 模板经过充分调优，保证审计质量一致；新增同类规则只需填字段，不需要重新写 prompt。

### 3.6 肿瘤知识从专家维护到运行时

肿瘤知识维护与患者审计分层隔离，并不让工作簿或草稿直接进入裁决：

```text
受控来源并集 → 两份专家工作簿 → 离线校验 → staging/审核
             → approve 投影不可变 revision → 只读 authority 三 pin
             → 数据库 CANDIDATE → PUBLISHED release
             → 本地四资产 bundle + active_release.json → RD04 离线加载
```

- 医保支付限定与指南适应证是两个独立 `policy_scope`，共用药品概念和
  release，但不互相冒充来源或合并资格状态。
- `release-authority` 从当前 authoring 全集和病理 bootstrap 计算 source、curated、
  pathology 三个需在库外审批单固定的 checksum；build/publish 任一 pin 不一致即拒绝。
- `release-build` 只在 authoring 库登记可重建的 `CANDIDATE`，不把 candidate 写入本地目录；
  只有 `release-publish` 会重建校验并写不可变 `PUBLISHED` bundle、数据库指针和本地原子指针。
  领域审核人与 release operator 分离，publish 必须由 build 的同一 operator 完成。
- published release 路径只接受四份 JSON 齐全、schema/checksum/审核状态通过且由
  `active_release.json` 指向的 `PUBLISHED` bundle；指针或资产校验失败时 fail-closed，
  不回退到草稿或 candidate。运行中不连专家维护库。
- publish 或 rollback 跨数据库与本地指针失败时恢复调用前状态；bundle、revision 和回滚
  事件不靠删除“修复”，同参数重试会重新校验后再复用。
- published release 对服务日期采用非对称策略：早于声明窗口的历史病例使用
  当前 release 并显式告警；窗口内正常裁决；超过窗口且无新版时
  fail-closed 为人工复核。该策略不改写 legacy `configs/` 资产的现有开关语义。
- 新结果在旧 `eligibility_json` 上只追加可空的 release、revision、来源、
  scope 和时间 provenance；历史旧行不回填，旧三态投影保持兼容。

维护入口及当前子命令以 `.venv/bin/javert oncology-kb --help` 为准；来源与候选计数
从 `docs/oncology/kb_authoring_baseline.json` 和 `docs/oncology/authoring/` 内的生成报告读取，
不在长期架构文档复制易漂移库存。

截至 2026-07-22，142 `知识库_work` 已实际建立 authoring schema、23 个必需触发器与 6 个
中文审核视图；两次历史 generated DRAFT 物化探针均已全量回滚，最终修正版则已完成
validate/preflight/服务端校验和 DRAFT 物化。专家批准、生产 publish/rollback、备份恢复演练
和新方案 paired shadow 仍未执行；以 `docs/oncology/authoring/142_draft_seed_import_report.md`
为当前实库事实记录。

### 3.7 Evidence Contract 与可复用 A/B 验收

Javert 把“医学命题”和“谁依据什么证据作出声明”分开：`Fact` 只表示规范化 proposition，
`Assertion` 保存 TRUE/FALSE/UNKNOWN、来源类型、双时间、Evidence、Provenance 与 Ontology 版本。
任何自动结论都必须能回到 immutable SourceArtifact 的 row/span/checksum；上游 HIS 主键只在
确实保留时作为额外 locator，不能猜测。

架构 A/B 使用同一公共 Evaluation Contract：冻结 cohort/source snapshot、候选查询、A/B
代码/配置/模型/知识版本和重复次数，持久化 plan、case、arm observation、专家裁定、report 与
manifest 的 canonical JSON/checksum。oncology 的 A 是 legacy patient verdict，B 是同一次
`shadow` 运行的 structured patient eligibility projection；历史旧 verdict 不共享输入，仍只能作
`historical_unpaired` 背景。

验收采用不可补偿的多门禁，不生成总分：配对与技术有效性 → false V/C 和 unsafe auto-decision
→ correct automation/abstention → evidence grounding/locator/proof/provenance → 重复运行稳定性 →
盲化专家定位成功率、解释评分与复核耗时。样本不足返回 `INSUFFICIENT_EVIDENCE`；SHADOW/PROMOTION
通过也只获得进入下一次人工决策的资格，不自动改 flag、规则状态或生产部署。

首个运行切片是默认关闭的 Diagnosis shadow：现有 `shi_zd` 与 `note_diagnosis` 保持原样，新纯
extractor 旁路生成 row/span locator、Fact/Assertion 和 provenance，经本地 ontology validator
后写独立 append-only ledger。projection 可从 cursor 0 重建，多来源同义诊断共用 Fact 但保留
Assertion；同一命题正反并列为 Conflict；未映射与技术失败分开。该路径没有被 Runner/Web/API
导入，运行和 retention 见 `docs/diagnosis_evidence_shadow.md`。

数据库输入采用独立 Hub Evidence Snapshot：正式默认源是 `sh_yb_platform-readonly`，连接前逐表
验证 SELECT-only；原始表按稳定 PK 排序成为 SourceArtifact，现有 `shi_fee/case_notes/shi_zd`
作为兼容 projection，lineage sidecar 保留原表/主键/字段。Manifest 固定每表和 projection digest
及 composite snapshot ID，A/B 两臂只读同一目录。当前库无 snapshot isolation/CDC/rowversion，
故只能诚实标记 `atomic_snapshot=false`；详情见 `docs/hub_evidence_snapshot.md`。

---

## 四、数据流转

### 4.1 数据来源

| 数据 | 来源 | 条目数 | 用途 |
|------|------|--------|------|
| 住院费用明细 | HIS 费用导出 (shi_fee) | 695,608 条 | search_fees 工具数据源 |
| 病历文书 | EMR 文书导出 (case_notes) | 357,418 条 | search_notes 工具数据源 |
| 病案首页诊断 | 病案首页 (shi_zd) | 10,194 条 | 主诊断 / ICD 编码 / Router 过滤 |
| 病案首页手术 | 病案首页 (shi_ss) | 6,980 条 | 主手术 / ICD-9-CM3 编码 |
| 检查报告 | 检查系统导出 | 270,743 条 | search_examinations 工具数据源 |
| 检验结果 | LIS 导出 | 856,647 条 | search_lab_results 工具数据源 (化验异常判定) |
| 药品适应症 | 内置映射表 (drug_indication) | 52 种药物 | M2 反向洗白匹配 |
| 药品监管 KB (v0.8) | `drug_audit_kb.json` (4 监管 xlsx 归一化) | 928 通用名 | M8 药品违规审计 (限适应症/超说明书/限二线/禁忌症) |
| 肿瘤资格知识 | legacy 三资产；可选 published release 四资产 bundle | 仅 approved/published 条目可裁决，其余显式待核 | RD04 双 scope 资格证明、provenance 与病历完善建议 |

### 4.2 数据在审计中怎么用

**一位患者的数据规模** (以 J66252 为例):

| 数据类型 | 该患者条目数 | 审计时实际读取量 |
|----------|-------------|-----------------|
| 费用明细 | 117 条 | 每次工具调用返回 ≤20 条匹配项 |
| 病历文书 | 152 条 | 每次工具调用返回 ≤10 条匹配段落 |
| 诊断 | 8 个 | 全量读取 (数据量小) |

**关键点**: 系统不会一次性把 117 条费用全喂给 LLM。而是由 LLM 根据规则需要，**用关键字定向检索**，每次只取少量相关数据。这既控制了 LLM 输入成本，也提高了准确率 (减少干扰信息)。

### 4.3 数据安全

- 患者数据来自院内 142 数据中台或受控本地快照，审计结果写本地 SQLite 并归档到 142；
  数据不出内网
- LLM 模型部署在内网 62 GPU 服务器，无需调用外部 API
- 审计结果双写: 本地 SQLite (审计引擎) + SQL Server (工作台归档)
- 肿瘤专家维护库与 `zadig` 结果库、`TP_data_hub`/`sh_yb_platform` 数据源分离；
  写入前必须同时通过精确库名、owned 白名单、连接库和权限预检
- 工作台访问需账号密码，注册通道关闭，由管理员 CLI 分配

---

## 五、结果复核机制 (专家工作台)

审计结果不是终审 — 系统产出后进入**专家复核**环节:

```
┌── 专家工作台 (Web, http://192.168.31.62:8090) ────────────────┐
│                                                                 │
│  左侧: 待审患者列表 (按违规数排序)                              │
│  右侧: 患者详情                                                 │
│    ├─ 病案概览 (主诊断 + 主手术 + 科室 + 住院天数)              │
│    ├─ 医生可读公开解释 (默认审核说明 + 结论/核查项/收费事实等) │
│    ├─ 真实命中项目 (只关联患者实际净正收费行)                  │
│    ├─ 原始资料按页签加载 (文书/费用/检验，失败可安全重试)      │
│    └─ 专家标注 (同意/不同意 + 备注)                             │
│                                                                 │
│  实时同步: 新审计完成 → SSE 推送 → 工作台自动刷新               │
│  Dashboard: 全局统计 (V/I/C 分布 + 按规则/科室聚合)             │
│  导出: Excel 全量导出 / 单患者导出                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 六、部署架构

```
┌── 医院内网 ──────────────────────────────────────────────────┐
│                                                              │
│  GPU 服务器 (192.168.31.62, RTX 5880 Ada 48GB)              │
│    ├─ Qwen3.6-35B-A3B-FP8 (sglang, 端口 30000)             │
│    ├─ Javert 审计引擎 (稀疏 Git production-62, systemd)     │
│    └─ 专家工作台 (FastAPI, 端口 8090)                        │
│                                                              │
│  数据库服务器 (192.168.31.142)                               │
│    └─ SQL Server (审计结果归档 + 工作台数据)                  │
│                                                              │
│  数据源: HIS/EMR/LIS 导出 → CSV/XLS → 审计引擎读取          │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

**全链路内网部署，无外网依赖。** 62 的 W2 试验端口 30002 与 OCR 端口 30001 当前均已停；
代码发布以本地和 62 的 Git HEAD 相等、远端受控工作树 clean 为完成条件，操作细节见
`docs/deployment_192_62.md`。
