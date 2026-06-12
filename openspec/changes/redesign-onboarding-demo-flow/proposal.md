## Why

`add-visual-schema-onboarding`(已 Complete)把接入从"手敲 YAML 跑 CLI"变成了 `/onboarding` 拖列映射,这一步是对的。但它是**工程师视角的"映射正确性工具"**——默认操作者懂数据模型(synth/asis/bridge、桥表 canonical 列、复合键 vs 裸号都直接摆在脸上)。我们真实的使用场景是**投资方/合作医院现场演示**:客户当场递来 4 个他们自己 HIS 导出的 CSV/Excel(列名是他们的、日期格式乱、可能缺表),我要**当着客户面、不查 README、不在终端临时拼命令、不手动逐字段改映射**,丝滑不出错地把数据跑出来。当前页面在这个场景下"很重、易出丑",已暴露多处现场会翻车的点(均有 file:line 实证):

- **增删导入数据经常出问题**:前端 JS state / `_uploads/` 磁盘 / `data_import/` 产出三处真相靠手工对齐(`onboarding.js:98-125` 删文件单独清映射);映射全在内存(`onboarding.js:13`),**误刷新一次当场全丢**;同名重传产生 `foo_1.csv` 幽灵文件(`routes_onboarding.py:49-58`);最关键——**载入产出无 GUI 清理入口**,想重导一遍只能离开浏览器去终端敲 `jv-clear`。
- **ER 图不清晰、不能拖、线上字段是"自带的"**:左栏画的是 Javert 自己的固定 schema 星图(CSS 流式布局 + `getBoundingClientRect` 推边,`onboarding.js:229-257`),**结构上无法拖动**;连接线标签 = `card.dataset.join` 直接取 manifest 默认键(`onboarding.js:252`、`onboarding.html:37`),**不是客户实际映射的列、不可编辑、改映射不更新**。
- **现场查 README / 临时拼命令的"狼狈"收尾**:载入成功后必须 alt-tab 到终端、回忆 `jv-run <患者号>` 或粘贴 `export JAVERT_DATA_DIR=… && uv run …`(`onboarding.js:501-505`),依赖 `jv-*` 已 source。
- **预检红灯无诊断**:`per_spoke` 命中率算出来了却埋着,红灯只说"键几乎不交"(`onboarding.js:287-290`),客户无法定位是哪张表 → 盲目试错改映射再预检,现场 10 分钟没了。
- **日期 dayfirst 歧义会静默反转数据**:整列全是 1–12 时默认猜 D/M(`profiler.py` dayfirst 不确定→默认 true),美式 M/D 文件被**静默颠倒**,审计算时间窗口时才暴露,事后被指"你们把我日期搞反了"。
- **专业术语与原始交互直怼客户**:每表的 synth/asis/bridge 下拉 + 三个桥表输入框常驻可见;"声明新表"是 3 个连续 `prompt()`(`onboarding.js:517-525`)。
- **隐性稳定性 bug**:载入只产出客户实际映射的表,但 `.loaded.env` 永远硬写 `JAVERT_ZD_FILE=shi_zd.csv` / `JAVERT_SS_FILE=shi_ss.csv`(`routes_onboarding.py:426-428`)——只给了费用+文书的客户拿到指向不存在文件的 env,`audit-patient` 直接报错。

**两条已定方向(本 change 据此设计,非待议)**:① **保留终端收尾**——对技术买家,滚动的真实审计日志是"真在跑"的可信度资产,不藏 CLI,而是把 GUI→终端的交接做到一个词、把日志本身做得专业;② ER 图改为 **"我的文件 → Javert 表" 的流向图**,连线标签显示客户实际映射的键列。

## What Changes

- **自动驾驶优先 / 驾驶舱按需(渐进披露)**:默认界面 = 一个投放区 + 结果卡 + 一个主操作;synth/asis/bridge、桥表配置、逐字段输入框收进每张卡的"调整 ▾"(操作者修正用,客户看不到)。
- **文件→目标表自动归类(最小启发式)**:上传后按列名对各 **tabular spoke 必填字段别名**的覆盖率,给每个文件 Top-1 归属猜测 + 自动预填命中字段;只作用于 6 张 tabular 表,view spoke(麻醉/病理)不进归类、不作可映射目标(消灭"8 个节点要映几张"的困惑)。连接键模式**默认取自 manifest 的 `id_form`/`via_bridge` 声明**(确定性,无需探测),选错由预检红灯兜住、驾驶舱一键改。**诚实定位**:"两个动作"是理想路径(数据贴国标、别名全中→全绿);现实里别名对陌生院列名有缺口,**琥珀卡(没补全)是一等公民**——指明缺项、"调整 ▾"补完即可继续,不阻塞、不假装零手动。
- **服务端进程内会话状态作为单一真相源**:`{上传文件 + 映射 + 归类 + 日期决策 + 节点位置}` 存服务端进程内(按 session 索引),每次 mutation 返回全量状态、前端整体重渲染;删/重传走服务端(同名**替换**不再生 `_1` 幽灵);同进程**刷新页面恢复**而非清空,冷启动从 `_uploads/` 重建。**刻意不落盘 JSON**(避免原子写/并发同步/磁盘漂移新 bug 面)。一次性消灭"增删出问题"整类 bug。
- **已载入数据生命周期 GUI**:新增"清空已载入"按钮(`POST /api/onboarding/clear-output` + 二次确认),重导走 GUI 不必去终端;`.loaded.env` 只写**实际产出**的表(修 `shi_zd`/`shi_ss` 硬编码),缺表客户不再炸。
- **ER 星图 → "我的文件 → Javert 表" 双侧流向图**:左=客户文件(真实文件名),右=Javert 规范表,边=真实映射,**连线标签 = 实际映射的键列**(改映射即重绘);点边看"源列→目标列"明细。这是 High;**节点拖拽 + 位置恢复保留但排为 Polish**(观感增强,不挤占稳定性 Must)。
- **预检红灯给可执行诊断**:red 时自动展开各表命中率、高亮最低的表、对低命中表提示"是否需要桥表?";预检过期时面板与闸状态一致(不再残留假绿灯)。
- **日期歧义交互确认**:dayfirst 不确定时弹"D/M 还是 M/D"对比(展示样本两种解析),用户确认后记录,绝不静默反转。
- **终端收尾做到一个词 + 专业进度日志**:载入成功面板给出已在剪贴板的 `jv-go` 一词命令;新增 `jv-go` = `source .loaded.env && jv-run-all`;`jv-run-all` 输出**逐患者进度行**(`[i/N] 患者号 ✓ xV yI zC ETA`)——对接团队看着真在跑。GUI 仍**不跑 LLM**。
- **"声明新表"改表单 modal**(替 3 个 `prompt()`):选已上传文件 + 填中文名/患者键列 + 前 5 行预览。

## Capabilities

### Modified Capabilities

- `data-onboarding-studio`(承载本 change 主体)。新增 Requirements:**文件→目标表自动归类(最小启发式)**(tabular-only + 绿/琥珀两态 + manifest 默认键模式 + 歧义单选)、**服务端接入会话状态**(进程内单一真相源,不落盘)、**已载入数据生命周期**(清空已载入)、**终端开跑一键交接**(`jv-go` + 剪贴板 + 逐患者进度 + 健壮性)。MODIFIED Requirements:**双栏可视化字段映射工作室**(默认自动驾驶视图 + "我的文件→Javert 表"流向图 + 真实键标签;synth/asis/bridge 收进驾驶舱;拖拽为增强)、**两道结构性兜底闸**(红灯可执行诊断 + 所有失效路径不残留假绿灯)、**声明新表入口**(表单 modal 替 `prompt()`)、**一键落映射并触发 ETL**(`.loaded.env` 只写实际产出表,修 `shi_zd`/`shi_ss` 死写)。
- `data-profiling`。MODIFIED Requirements:**连接预检**(输出从"单句三态"升级为含 `per_spoke` 命中率 + 最低表定位 + 成因建议的可执行诊断,诊断在路由层打包、不改核心函数签名)、**日期列逐列格式探测**(整列仍无 day>12 时不静默套默认 dayfirst,改一次性交互确认 D/M vs M/D)。

> 注:`jv-go` / `.loaded.env` 诚实化属"接入流的终端交接",归在 `data-onboarding-studio`(其既有 Requirement"一键落映射并触发 ETL"即写 `.loaded.env`),不另设 `data-access` delta。

## Impact

- **改动代码**:
  - `src/javert/web/static/onboarding.js`(自动归类 + 会话渲染 + 流向图重写 `drawEdges` + 渐进披露 + 诊断面板 + 日期确认 + 声明新表 modal)
  - `src/javert/web/templates/onboarding.html`(双侧流向图 DOM + 结果卡 + 调整折叠 + 清空已载入按钮 + declare-modal)
  - `src/javert/web/api/routes_onboarding.py`(`/session` 读写进程内态、`/classify` 文件归类、`/clear-output` 清产出、`/confirm-date` 日期确认、`/preflight` 诊断打包;`start` 的 `.loaded.env` 改为按实际产出写)
  - `src/javert/web/static/style.css`(流向图/结果卡/折叠/modal 样式)
  - `scripts/javert.zsh`(加 `jv-go`)、`scripts/jv_run_all.sh`(逐患者进度行 + 缺表跳过)
- **新增代码**:`src/javert/onboarding/classifier.py`(列名对 tabular spoke 必填别名覆盖 → Top-1 归类,**最小启发式、键模式取 manifest 默认**,复用 `field_alias`/`manifest_loader`);会话状态用进程内态(轻量模块或 app.state,**不落盘 JSON**)。
- **新增配置/数据**:无新增跨机依赖;产出仍落 `data_import/`,本地 sqlite 自洽。
- **规模**:约 800–950 行 / 7 文件;单人 Must+High 约 2–3 周,Polish 另计(详见 design.md 规模估计)。
- **依赖既有**:复用 `add-visual-schema-onboarding` 的 `manifest_loader`/`profiler`/`join_preflight`/`etl_engine`/`field_alias.yaml`、`schema_manifest.yaml`,以及工作台 `create_app`/`AuthMiddleware`/蓝色皮肤/cache-busting。下游 `audit-patient`/工具零改。
- **非目标兜底**:GUI 仍不跑 LLM 推理(审计走 `audit-patient`/`jv-*`);不连 142,接入阶段 `JAVERT_SQL_ENABLED=false` 本地 sqlite-only。
