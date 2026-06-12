## Why

外部医院数据接入现在靠手改 `configs/column_mapping.yaml` + 跑 `etl_import.py`:不可视、不即时、易错,投资方现场演示不丝滑。更糟的是 ETL 把 4 张表(费用/文书/诊断/手术)硬编码死了,新数据类型(化验/检查/病理/麻醉)来了没有映射通道——而真实数据(sy / song)已经暴露三个会"现场翻车"的坑:① 各表患者键不在同一命名空间(song 文书走 `medcasno` 226xxx、费用走 `hsp_account_no` 211xxx,naive join 0 命中,必须经病案首页桥表归一);② 日期格式跨表甚至同列内不统一(实测 8 个格式族,含"纯时间无日期"会让时间窗口分析全废);③ loader 两个 getter 键匹配策略不一致(notes 精确相等 vs fees 包含)。我们要让接入"上传文件→拖列连星图→看绿灯→一键审计"取代手敲 YAML,且**下限兜得住——绝不展示处理不了的东西、新数据来了不出丑**。

## What Changes

- 新增 `configs/schema_manifest.yaml` 作为数据模型唯一真相源:每个 spoke 声明中文名 / 内部字段(必填可选)/ 由哪个 loader+tool 消费 / 状态档位(live/view/stored)/ 连接键策略(`join_key` + `via_bridge`)。UI、ETL、工具注册三处都读它。
- 新增 `configs/field_alias.yaml` 国标别名种子(从已有 szx/song 映射反推"国标医保结算清单/病案首页 ↔ Javert"词典),上传列名 fuzzy 命中→自动预填映射。
- 新增 `/onboarding` 双栏可视化字段映射工作室,集成进现有 62:8090 工作台(复用蓝色商务皮肤 + `AuthMiddleware` 鉴权):左=从 manifest 渲染的星型默认结构(spoke 按状态亮灯),右=拖入的 CSV/TXT/XLSX 自动抽列;拖右→左完成映射,逐表声明连接键(支持桥表),手打+联想下拉。
- 新增按需列剖析 + 连接预检(纯计算不调 LLM,GUI/CLI 共用):逐列格式/范围/空值剖析 + 键交集覆盖率 + 时间窗口重叠,给 🟢/🟡/🔴 三态结论。
- **BREAKING(内部实现,非对外 API)**:`scripts/etl_import.py` 从硬编码 4 表 dict 重构为遍历 manifest spokes;老 4 表行为保持不变,但新增 ETL 阶段做桥表键归一(`medcasno→canonical`)+ 连接预检 + 逐列日期格式归一。
- 化验/检查接入 ETL + `column_mapping`(工具 `search_lab_results`/`search_examinations` 已存在,仅缺接入口);麻醉/病理作为 view spoke 补 `search_anesthesia`/`search_pathology` 薄视图工具。
- 修复 loader 键匹配不一致:连接预检以"两个 getter 都能解析同一 patient_id"为通过判据,ETL 保证 notes 落裸号、fee 落复合键。
- 两道结构性兜底闸:**必填禁用闸**(已映射 spoke 必填字段没齐则"开始审计"禁用)+ **连接预检闸**(键几乎不交则挡住),两闸都过才允许上场跑。

## Capabilities

### New Capabilities
- `schema-manifest`: 数据模型唯一真相源——spoke 声明(字段/必填/loader+tool/状态档位/连接键) + 国标别名种子 + 三档兜底契约(live/view/stored) + 桥表连接键模型。UI/ETL/工具注册共读。
- `data-onboarding-studio`: `/onboarding` 双栏拖拽映射 GUI——文件上传抽列、星型 schema 渲染与状态灯、拖拽/手打+联想映射、逐表连接键声明、必填禁用闸 + 连接预检闸、"声明新表"入口。
- `data-profiling`: 按需逐列剖析(数值范围/计数/空值、日期逐列格式探测+min/max+月度分布、键唯一值/重复度)+ 连接预检(键交集覆盖率 + 时间窗口重叠 → 三态结论)。纯计算,GUI 与 `etl_import` 校验阶段共用同一套逻辑。

### Modified Capabilities
- `data-access`: ETL 从硬编码 4 表改为 manifest 驱动 N 表;新增桥表键归一 + 逐列日期格式归一;化验/检查接入;新增 `search_anesthesia`/`search_pathology` 视图工具;tool registry 读 manifest 决定 live 注册真工具 / view 注册视图工具 / 无 tool 的新 spoke 注册兜底 stub。

## Impact

- **新增配置**: `configs/schema_manifest.yaml`、`configs/field_alias.yaml`。
- **新增代码**: `src/javert/web/api/routes_onboarding.py`(上传/映射/剖析/预检/落 column_mapping 并触发 ETL)、`src/javert/onboarding/{profiler,join_preflight,alias_matcher,manifest_loader}.py`、`src/javert/tools/search_anesthesia.py`、`src/javert/tools/search_pathology.py`、`src/javert/web/templates/onboarding.html`、`static` 增星图拖拽 JS。
- **改动代码**: `scripts/etl_import.py`(manifest 驱动 + 桥表归一 + 日期归一 + 复用预检)、`src/javert/tools/registry.py`(读 manifest 注册)、`configs/column_mapping.yaml`(扩 lab/exam 节 + 桥表/连接键字段)、`src/javert/web/api/main.py`(挂 onboarding 路由)、`src/javert/web/middleware.py`(保护 `/onboarding`)。
- **数据**: `data/song_fixture/`(5 病人真实 fixture,已生成)作为 end-to-end 验收基线。
- **依赖**: 不引入新跨机依赖,本地 sqlite 自洽;GUI 复用现有 `create_app`/`AuthMiddleware`/`templating`/静态资源 cache-busting。下游 Javert 仍只认 `bah`/`ba_id`,桥表归一只在 ETL 阶段消费。
