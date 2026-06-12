## Context

`add-visual-schema-onboarding`(Complete)交付了 manifest 驱动的 `/onboarding` 拖列映射工作室:`schema_manifest.yaml` 单一真相源、`field_alias.yaml` 别名预填、`onboarding/{manifest_loader,profiler,join_preflight,etl_engine}.py` + `routes_onboarding.py` + 双栏星图 GUI。地基稳、正确性强。

但它的产品定位是"懂数据模型的操作者把列映射对",而本 change 的目标场景是**现场演示**:客户当场给陌生 schema 的脏数据,我要在客户注视下零查阅、零终端拼命令地跑出审核结果。同一套皮肤下其实是两个产品。本 change **不推翻地基**,而是在其上加一层"自动驾驶",把现有工作室降级为"自动失败时才打开的驾驶舱"。

所有判断基于对现有实现的逐行阅读(file:line 见 proposal,经独立复核全部属实),不是空想。两条方向已与产品方确认:**保留终端收尾**(技术买家眼里滚动日志=可信度)、**ER 改双侧"我的文件→Javert 表"流向图**。本设计经一轮多智能体对抗审查,刻意收敛了若干"过度设计"项(会话不落盘、归类用最小启发式、拖拽降级)以贴合 Javert"Simplicity First"。

## Goals / Non-Goals

**Goals:**
- 现场演示**理想路径**压到两个人类动作(拖文件 + 点"载入数据")+ 终端一词 `jv-go`;**现实路径**=自动映射大部分 + 少数"琥珀"卡在驾驶舱补完——二者都不崩、不出丑。
- "增删导入数据"不再出错:服务端状态为单一真相源,删/重传/刷新都稳;载入产出有 GUI 生命周期(清空/重导)。
- 客户从不直面 synth/asis/bridge、桥表列、`prompt()`、原始 `export` 命令。
- 失败可解释、不静默:预检红灯指明哪张表,日期歧义交互确认,缺表不炸。

**Non-Goals:**
- 不在 GUI 跑 LLM 推理(审计仍 `audit-patient`,终端收尾是刻意保留的产品决策,非妥协)。
- 不重写下游 loader/工具/规则、不动 `schema_manifest.yaml` 字段语义、不动桥表归一算法(沿用旧 change D3)。
- 不连 142、不做对方 HIS 直连(仍是 CSV/Excel 批量文件 + 本地 sqlite)。
- 不做完整的"导入历史版本管理 UI"(见 Open Questions,本 change 只做"替换 + 清空")。
- 不引入需真实多院数据标定的复杂归类打分(最小启发式起步,后续按数据迭代)。

## Decisions

### D1. 自动驾驶优先,驾驶舱按需(渐进披露)
默认界面三段:**投放区 → 结果卡(每 tabular 文件一张,绿=自动全中/琥珀=需补)→ 一个主操作("载入数据")**。synth/asis/bridge 下拉、桥表配置、逐字段输入框全部收进每张卡的"调整 ▾"折叠区——这就是现有工作室的全部控件,降级为操作者修正琥珀卡时才展开,客户演示路径不出现。
- **备选**:在现有星图上"减一点控件" → 否决,病根是默认 surface 就是驾驶舱;必须换默认层。

### D2. 文件→目标表自动归类:最小启发式 + manifest 默认键模式
上传后对每个文件,按其列名对各 **tabular spoke 必填字段别名**的覆盖率给 Top-1 归属猜测,命中即自动预填字段(复用现有 `aliasMatch`)。**刻意保持最小**:只用"必填覆盖率 + 患者键硬门槛",不引入需真实数据标定的复杂打分/阈值(对抗审查指出无数据先调阈值是过早抽象)。
- **歧义不猜**:Top-1 不明确(多表覆盖相当)或缺患者键 → 结果卡显"请确认这是哪张表"单选下拉(一次,非逐字段)。
- **键模式不探测、取 manifest 默认**:每 spoke 的连接键模式默认取自 manifest 既有声明——`via_bridge` 非空→bridge、`id_form=bare`→裸号、`compound`→复合。这是**确定性**默认(manifest 是 ground truth),无需 ID 重叠概率探测;模式选错由 D7 预检红灯兜住,操作者在驾驶舱一键改。
- 归类逻辑放 `onboarding/classifier.py`,GUI 与 CLI(`etl_import --auto-classify`)共用。
- **备选 A**(探测 ID 重叠自动选 synth/asis/bridge)→ 否决,manifest 默认 + 预检兜底已够且零标定。
- **备选 B**(让用户先选"这是费用表"再映射)→ 否决,多一步且正是要消除的手动负担。

### D3. ER 星图 → "我的文件 → Javert 表" 双侧流向图(标签为主,拖拽为辅)
左列 = 客户上传文件节点(真实文件名 + 列数),右列 = Javert 规范表节点;边 = 一条实际映射,**边标签 = 该表实际映射的患者键列名**(如 `就诊流水号`),取自会话状态,改映射即重绘;点边弹"源列→目标列"明细。
- **强制部分(High)**:双侧布局 + 真实键标签 + 点边明细——直接解决用户三诉求里的"显示不清晰"和"线上字段是自带的"。
- **增强部分(Polish)**:节点可拖 + 位置随会话恢复——用户提了"想要 plotly 那种拖动",保留但**排在最后做**,因为它是观感增强,不该挤占稳定性 Must;拖拽用绝对定位 + `transform` + SVG 重连即可,**不引 d3-force/cytoscape 物理引擎**(双列规则布局不需要)。
- **备选**(全 d3-force)→ 否决,包重、布局不可预测、演示反而乱。

### D4. 服务端进程内会话状态 = 单一真相源(不落盘 JSON)
接入过程状态 `{files, map, classify, stored, date_decisions, node_positions}` 存**服务端进程内**(按 session cookie 索引),每次 mutation 服务端更新并返回全量状态,前端整体重渲染。
- **删文件**:服务端移除文件 + 级联清其映射,返回新状态 → 消灭 JS-vs-磁盘双清不一致(旧 `onboarding.js:98-125`)。
- **同名重传**:替换 logical 文件而非生 `foo_1.csv`(改旧 `_unique_upload_path` 语义)。
- **刷新**:同进程内从会话状态恢复 → 消灭"误刷新当场全丢";冷启动(进程重启,演示期不会发生)从 `_uploads/` 现存文件重建自动映射。
- **刻意不落 JSON 文件**(对抗审查):落盘 JSON 会引入原子写/并发同步/磁盘漂移等新复杂度与 bug 面,演示进程生命周期内的内存态已足够,省 ~150 LOC。代价=进程重启丢临时态(可接受)。
- **备选**(前端内存态为真相源)→ 否决,正是增删出错与刷新丢失的病根。
- **备选**(落盘 `_session.json` + 原子写)→ 否决,过度设计,见上。

### D5. 已载入数据生命周期 + `.loaded.env` 诚实化
- 新增 `POST /api/onboarding/clear-output` + GUI"清空已载入"按钮(二次确认),清 `data_import/` 产出 CSV + `.loaded.env` + 会话产出态——重导走 GUI,不必去终端 `jv-clear`。
- `start` 写 `.loaded.env` 时**只导出实际产出表对应的 `JAVERT_*_FILE`**:产出 `shi_zd.csv` 才写 `JAVERT_ZD_FILE`,余同——修 `routes_onboarding.py:426-428` 死写,缺表客户 `audit-patient` 不再找不到文件。
- **备选**(完整版本史 v1/v2/激活/diff)→ 本 change 只做"替换 + 清空",版本史留 Open Question。

### D6. 保留终端收尾:`jv-go` 一词 + 逐患者进度日志 + 健壮性
GUI 仍**不跑 LLM**;载入成功面板给一条已自动 copy 的命令 `jv-go`,显式写"去终端敲 `jv-go`"。
- `jv-go`(`scripts/javert.zsh`)= `cd $JAVERT_HOME && source data_import/.loaded.env && jv-run-all`;起始打印 `.loaded.env` 产出摘要(表/患者数/写入时间)供核对;**`.loaded.env` 不存在则提示"请先在网页载入数据"并退出**(不静默失败)。
- `jv-run-all`(`scripts/jv_run_all.sh`)输出**逐患者进度行** `[i/N] 患者号 ✓ xV yI zC`;单患者失败标 ✗ 不中断;缺某 `JAVERT_*_FILE` 跳过并 WARN(不 ERROR)。
- **备选**(浏览器内一键跑 + SSE 进度回灌)→ 技术可行(已有 EventBus/SSE),但产品方明确要终端日志显专业 → 否决浏览器内跑;SSE 留作未来"非技术买家"模式候选。

### D7. 预检红灯 = 可执行诊断,不是死胡同
`join_preflight` 已算 `per_spoke` 命中率,**MUST NOT 改其函数签名**;诊断打包在**路由层**:red 时面板自动展开各表命中率、高亮最低表、对低命中表给一句建议(0% 多半列映射错或缺桥表 → "是否需要桥表?")。同时修预检过期不一致:任一失效路径(改字段映射/删文件/改键模式/改桥表)后面板显"已过期 [重新预检]",不残留假绿灯(旧 `onboarding.js:199,287-290`);点"载入数据"若已过期则自动先重跑预检再判。
- **备选**(维持单句红灯)→ 否决,正是现场 10 分钟试错的病根。

### D8. 日期 dayfirst 歧义 → 一次性交互确认,绝不静默反转
`profiler` 探测某日期列扫完整列仍无 day>12(`dayfirst_confident=False`)时,不再默认猜 D/M。载入/预检前若存在未决歧义列,弹**一次**确认(列出**全部**歧义列 + 各列 D/M vs M/D 样本对比),用户选定后记入会话 `date_decisions{file:col → dayfirst}`,ETL 据此归一。**一次性、非逐列弹窗**。
- **备选**(默认 + popover 警告)→ 否决,警告易忽视且后果是静默数据反转,事后甩锅最伤。

### D9. tabular-only 归类 + 琥珀两态诚实(防"假装零手动")
- 自动归类与结果卡 MUST 只作用于 6 张 tabular spoke;view spoke(麻醉/病理)不进归类、不作可映射目标,仅作"由文书/化验派生·无需映射"上下文呈现——消灭"页面 8 个节点、客户困惑要映几张"。
- "两个动作"是**理想路径**(客户数据贴国标、别名全中→全绿)。现实里 `field_alias` 对陌生院列名有命中缺口,**琥珀态(自动没补全)是一等公民**:结果卡标琥珀 + 指明缺项 + "调整 ▾"补完即可继续,**不阻塞、不假装全自动**。文案与 spec 不overpromise"零手动"。
- 提升命中率(如 jaro-winkler 模糊匹配、让客户上传本院别名库)留作后续增强,非本 change 必须。

## Risks / Trade-offs

- **自动归类误判**(把诊断当手术)→ 必填覆盖 + 患者键硬门槛 + 歧义不猜(让人一次选);误判可在结果卡一键改表归属。宁可标"请确认"也不静默猜错。
- **会话内存态进程重启丢失** → 演示期(数小时)单进程不重启,可接受;冷启动从 `_uploads/` 重建自动映射兜底。
- **流向图拖拽边界 case** → 降级为 Polish,双列规则布局天然少重叠;拖拽只改 `transform` + 重连 SVG,resize 去抖重绘(沿用旧机制)。
- **`jv-go` 误用旧 `.loaded.env`** → 起始 banner 打印产出摘要(表/患者数/写入时间)供一眼核对;清空已载入会删 `.loaded.env`。
- **琥珀态被误读为"系统没认出"** → 文案明确"已认出表、缺 N 个必填,点调整补完";绿/琥珀色彩语义清晰。
- **改动触及稳定的 `routes_onboarding`/`onboarding.js`** → 会话化、归类、流向图为增量端点/模块;旧硬编码 `start` 落盘路径保留为回退;每步 `uv run pytest tests/` 全绿(现 456 + 1 skip 基线)。

## Migration Plan

按"先稳、再不炸、再丝滑、最后观感"分层,各层可独立测、独立回滚:

1. **服务端会话状态(Must)**:加进程内会话 + `/api/onboarding/session`,前端改为从会话渲染(删/重传/刷新走会话)——先消灭增删 bug。
2. **`.loaded.env` 诚实化 + 清空已载入(Must)**:`start` 按实际产出写 env + `/clear-output` + GUI 按钮。
3. **终端收尾(Must)**:`jv-go` + `jv-run-all` 进度行 + 健壮性 + 载入面板交接/剪贴板。
4. **预检诊断 + 日期确认(Must,失败可解释)**:红灯展开诊断(路由层打包)、过期一致;日期歧义一次性 modal。
5. **自动归类最小版 + 结果卡(High)**:`classifier.py` 必填覆盖归类 + manifest 默认键模式;绿/琥珀结果卡;"调整 ▾"收纳旧控件;tabular-only。
6. **流向图标签(High)**:重写 `drawEdges` 为双侧 file→spoke + 真实键标签 + 点边明细;替换星图 DOM。
7. **观感增强(Polish)**:节点拖拽 + 位置恢复;声明新表表单 modal。
- **回滚**:每层增量;会话/归类/流向图可单独摘除回现状;`jv-go` 是纯新增 alias。
- **演示紧时**:1+2+3 先出(稳的增删 + 不炸的 env + 一词收尾)即显著提升观感;4+5+6 是"丝滑/可解释"主体随后补;7 最后。

## 规模估计(对抗审查补)

约 800–950 行新增/改动、7 个文件:`onboarding.js` ~+250、`routes_onboarding.py` ~+120、`onboarding.html` ~+100、`style.css` ~+150、新 `classifier.py` ~100、会话态 ~30–50(内存,不落盘)、`javert.zsh`/`jv_run_all.sh` 小改;预计 +50–70 条新测。单人 Must+High 约 2–3 周,Polish 另计。tasks 分层即据此——Must 先落即可演示。

## Open Questions

- 自动归类的"必填覆盖率"判定阈值(几成算认出)需用 sy/song + 真实外院多份 fixture 标定(先经验值)。
- 导入历史**版本管理**(v1/v2/激活/diff)是否要做、还是"替换+清空"长期够用?(本 change 不做)。
- 是否值得加模糊匹配(jaro-winkler)或"客户上传本院别名库"以压低琥珀率?(留作增强)。
- `jv-run-all` 是否需写机读 `_progress.jsonl`(已有 batch runner 先例)供工作台/对接团队消费?
