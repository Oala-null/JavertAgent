## Context

Javert 现有外部接入链路:操作者肉眼读对方 CSV 列名 → 手填 `configs/column_mapping.yaml` 右侧 → 跑 `etl_import.py`(把 4 张表硬编码转成 `shi_fee/case_notes/shi_zd/shi_ss`)→ 切 `JAVERT_*` env → `audit-patient`。问题是不可视、不即时(报错才知道映射错)、且只认 4 表。

本设计的所有关键判断都已用两份真实数据(`data/sy_*`、`data/song/*`)实测过,不是空想:

- **化验/检查工具已就位**:`search_lab_results`/`search_examinations` + `LabLoader`/`ExaminationLoader` 已注册给 agent(`registry.py`),数据 `sy_检验.csv`(392MB)/`sy_patient_examination.csv` 已在;**只是没进 ETL/column_mapping**,外部数据享受不到。
- **病理/麻醉无独立源表**:麻醉散在 `case_notes` 子阶段(38359 行)+ `shi_ss.anst_mtd_name`;病理散在 lab `specimen=病理`(419 行)+ notes 子阶段 + 检查报告自由文本。"真接入" = 视图工具,不是新物理表。
- **song hub 是病案首页 `r_basy`,各表连接键不在同一命名空间**:fee/ss/zd 连 `hsp_account_no`(211xxx),doc 连 `medcasno`(226xxx)。实测随机 5 人五表全连通须经 `r_basy` 桥表;doc 经 `medcasno→psn_no` 归一后 loader 取到 38 段文书 + 554 行费用。fixture 已落 `data/song_fixture/`。
- **日期格式跨表/同列不统一**(sy+song 实测 8 族):`D/M/YYYY` 日月不补零、`YYYY-MM-DD` ISO、`YYYY-MM-DD H:M:S.fff`(小数秒精度 .9~.999999 浮动)、`D/M/YYYY H:M:S.ffffff`、纯日期、无秒、**纯时间无日期**(`09:28:05.794134`,做时间窗口会全废)、**表头泄进数据**(字面 `"reportDate"`)。
- **loader 键匹配策略不一致**:`get_notes` 对 `住院号` 精确相等,`get_fees` 对 `bah` 包含匹配。

约束:不引入新跨机依赖(本地 sqlite 自洽);GUI 复用现有工作台 `create_app`/`AuthMiddleware`/`templating`/静态资源 cache-busting;下游 Javert 仍只认 `bah`/`ba_id`。

## Goals / Non-Goals

**Goals:**
- 让数据接入从"手敲 YAML 跑 CLI"变为"上传→拖列连星图→看绿灯→一键审计"的可视化流程,投资方现场能过。
- 把 ETL 从硬编码 4 表抽象为 manifest 驱动 N 表,新数据类型加一段 manifest 即可,零改 UI/ETL 代码。
- 兜住下限:展示的每个 spoke 都有保证不破的处理路径;两道闸(必填、连接预检)挡住带病运行;新数据来了不崩、不静默丢、不出丑。
- 化验/检查接入外部数据通道;麻醉/病理以视图工具真接入。

**Non-Goals:**
- 不做新审计规则(本 change 只做数据接入与可视化,规则沿用现有 ready 集)。
- 不连 142 SQL Server 跑接入(接入阶段本地 sqlite-only,完后照常 sync)。
- 不做对方 HIS 直连/实时同步(仍是 CSV/Excel 批量文件接入)。
- 不重写下游 loader/工具的 patient_id 语义(桥表归一吸收差异,下游零改)。
- 不在 GUI 里跑 LLM 推理(GUI 只做映射/剖析/预检/触发 ETL;审计仍走既有 `audit-patient`)。

## Decisions

### D1. 单一 manifest 真相源,三处共读
`configs/schema_manifest.yaml` 定义所有 spoke;UI 渲染、ETL 转换、tool registry 注册都从它读,杜绝三处各写一份导致漂移。
- **备选**:在 `etl_import.py` 和前端各维护一份字段表 → 否决,必然漂移,正是现在硬编码的痛点。

### D2. 三档兜底契约(设计铁律)
每个展示出来的 spoke 必须挂一个 `status`,且最坏情况都不破:
- 🟢 `live` — 有专用审计工具消费,正常产裁决(费用/文书/诊断/手术/化验/检查)。
- 🟡 `view` — 有读取视图 + 病案概览展示,规则可暂不依赖;最坏 = 被存下 + 概览可见 + 诚实标注"已接收·暂不参与判定"(麻醉/病理)。
- ⚪ `stored` — 全新声明表,只存不审;最坏 = 存下 + 概览列出原文,绝不静默丢。

registry 据此注册:live→真工具,view→视图工具,stored/无 tool→兜底 stub(返回"已接收暂不参与判定")。**左侧绝不出现最坏情况=崩溃/静默丢的 spoke。**
- **备选**:展示所有理论 spoke 不管有没有处理路径 → 否决,正是"出丑"根源。

### D3. 桥表键归一只在 ETL 阶段消费
manifest 每个 spoke 声明 `join_key` 与可选 `via_bridge`(桥表 + 交叉列对)。ETL 用桥表(如 `r_basy`)构 `源键→canonical key` 交叉表,把每张表的患者键改写成统一 canonical,再落 Javert 格式。下游只认 `bah`/`ba_id`,零改动。
- 实证:`medcasno(226xxx)→psn_no/hsp_account_no` 归一后五表对齐,patient `211482223` 取到 38 notes + 554 fees。
- **canonical 形态由目标 getter 决定**(见 D4):notes 落裸号、fee/zd/ss 落复合键。
- **备选**:把桥表带进审计运行时按需 join → 否决,污染下游所有工具且每次重算。

### D4. 连接预检判据 = "两 getter 都解析得出同一 id",非集合相等
loader `get_notes`(精确相等 `住院号==pid`)与 `get_fees`(包含匹配 `bah.contains(pid)`)策略不同。预检对每个候选 patient_id 实际调两个 getter 验证都非空,而不是简单比较两表键集合是否相等(后者会被复合键 vs 裸号的形态差骗过)。ETL 据此保证 notes 落裸号、fee 落复合键。
- 实证教训:第一版 fixture notes 落复合键,集合"相等"但 `get_notes` 精确匹配返回 0;改落裸号才通。

### D5. 日期格式逐列探测,不设全局 dayfirst
对每个日期列:采样整列(非头几行)→ 按 shape(数字归一为 9)聚类 → 找到 day>12 的样本判定 dayfirst → 弹性解析(容忍小数秒精度浮动、缺秒、纯日期)→ 识别"纯时间无日期"列(标记不可做时间窗口)→ 少数派异常 shape 标红(抓表头泄漏)。归一后统一存 ISO。
- **备选**:`pd.to_datetime(dayfirst=True)` 全局 → 否决,实测同文件内 fee 是 D/M、doc 是 ISO,全局必错一半;且 `11/11` 这类样本骗过头几行探测。

### D6. 剖析与预检按需触发、纯计算、GUI/CLI 共用
列剖析点哪列算哪列(大文件先采样秒出近似、再可"全量精确扫描"),绝不导入即全列硬算(392MB)。预检/剖析逻辑放 `src/javert/onboarding/{profiler,join_preflight}.py`,`etl_import.py` 校验阶段直接 import 复用,GUI 路由也调它——同一套逻辑两个入口。
- **备选**:GUI 用 JS 在浏览器算、CLI 用 Python 各算一份 → 否决,双实现易漂移且浏览器扛不动大文件。

### D7. GUI 集成进现有工作台,不另起服务
`/onboarding` 复用 `create_app`/`AuthMiddleware`(加保护路径)/`templating`/蓝色皮肤;上传文件存临时目录,映射结果落 `column_mapping.yaml` 后调 ETL。
- **备选**:独立单页 HTML 工具 → 探索期评估过,与审计链路断开、且大文件浏览器解析吃力 → 否决(用户已选集成)。

### D8. 国标别名种子驱动自动预填
`configs/field_alias.yaml` 从已有 szx/song 映射反推"国标(医保结算清单/病案首页字段码)↔ Javert 语义"词典(如 `fees.amount ← [det_item_fee_sumamt, 金额, ...]`)。上传列名 fuzzy 命中别名→自动预填映射,用户确认+补非标。公立医院多按国标导出,命中率高,这是"丝滑"主力。

## Risks / Trade-offs

- **桥表本身脏/缺行** → 部分患者键归一失败 → 预检报"未命中"并在 UI 列出,不静默吞;ETL 对归一失败行可选跳过+计数告警。
- **大文件全量扫描慢**(391/505MB) → 默认采样近似,全量为显式按钮;月度分布/范围用 chunk 流式算,不全载内存。
- **manifest 抽象引入回归风险**(动了稳定的 4 表 ETL) → 老 4 表 spoke 行为用 `data/song_fixture/` + 现有 szx fixture 做回归基线,转换输出逐列对比旧实现。
- **view 工具信号弱被误读为"已覆盖"** → UI 与工具输出都强制标注"暂不参与判定/弱信号",concept 上不让 view spoke 冒充 live。
- **纯时间无日期列**(D5)若漏判 → 时间窗口预检产垃圾结论 → 探测器显式识别并在该列禁用窗口分析、UI 标"无日期分量"。
- **新 spoke 滥用 stored 档** → 大量只存不审数据制造"看着接了实则没用"错觉 → dashboard/概览明确区分 live/view/stored 计数。

## Migration Plan

1. 先落 `schema_manifest.yaml` + `field_alias.yaml`,把现有 4 表 + lab/exam 声明进去(Layer1)。
2. 重构 `etl_import.py` 为 manifest 驱动,用 `song_fixture` + szx 回归,确认老 4 表输出逐列一致。
3. 加 `onboarding/{profiler,join_preflight,alias_matcher,manifest_loader}.py`,CLI 校验阶段先用上(Layer2 一半)。
4. 补 lab/exam ETL 节 + `search_anesthesia`/`search_pathology` 视图工具 + registry manifest 注册(Layer2)。
5. 加 `/onboarding` 路由 + 模板 + 星图拖拽 JS,集成工作台(Layer3)。
6. **回滚**:manifest/onboarding 为纯增量;`etl_import` 重构保留旧函数为 fallback,出问题切回硬编码路径;GUI 路由可摘除不影响 CLI 接入。
- **演示紧时**:Layer1+3 先出能映射 4+2 表的丝滑界面,麻醉/病理星点先亮 🟡 占位,Layer2 视图工具随后补。

## Open Questions

- 上传文件临时存储位置与清理策略(进程临时目录 vs `data_import/_uploads/`,演示后是否保留)?
- "声明新表"产出的 stored spoke 是否要落回 `schema_manifest.yaml`(持久化)还是仅本次会话内存态?
- 桥表交叉列对在 UI 上如何让用户声明最省心(自动探测同时含两套号的表 vs 手选)?
