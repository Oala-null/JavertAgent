## ADDED Requirements

### Requirement: 文件→目标表自动归类(最小启发式)

系统 SHALL 在文件上传后,用列名对各 tabular spoke 的**必填字段别名覆盖率**给出 Top-1 归属猜测,并自动预填命中字段;归类与结果卡 MUST 只作用于 **tabular spoke(费用/文书/诊断/手术/化验/检查 6 张)**,view spoke(麻醉/病理)MUST NOT 参与自动归类、MUST NOT 出现在可映射的目标表里。本 change 采用最小启发式(必填覆盖率 + 患者键硬门槛),MUST NOT 引入需真实数据标定的复杂打分阈值。每张表的连接键模式 MUST 默认取自 manifest 的 `id_form`/`via_bridge` 声明(确定性,无需探测),用户可在驾驶舱改写。

#### Scenario: 上传即认表并自动映射(尽力,非保证全中)

- **WHEN** 用户上传一个列名含某 tabular spoke 必填字段别名 + 患者键候选的文件
- **THEN** 系统 MUST 把该文件 Top-1 归到该 spoke、自动预填命中字段,并在结果卡显示"已认出:费用明细"

#### Scenario: 自动映射不全时落琥珀态而非阻塞

- **WHEN** 自动归类后某表仍有必填字段未命中(别名未覆盖客户列名)
- **THEN** 该表结果卡 MUST 显示琥珀态并指出缺哪个必填字段,用户 MUST 能展开"调整 ▾"补映射;琥珀态 MUST 是可继续的有效路径(绿=自动全中,琥珀=需补,二者都不崩)

#### Scenario: 归类歧义不猜

- **WHEN** 一个文件对两个 spoke 的必填覆盖相当,或缺患者键候选
- **THEN** 系统 MUST NOT 静默猜测,MUST 在结果卡显示"请确认这是哪张表"单选下拉(而非退回逐字段映射)

#### Scenario: 视图表不进归类流

- **WHEN** 页面加载或文件归类
- **THEN** 麻醉/病理(view spoke)MUST NOT 作为自动归类目标或结果卡的可映射表出现;它们 MUST 仅作为"由文书/化验派生·无需映射"的上下文说明呈现

### Requirement: 服务端接入会话状态

系统 SHALL 以**服务端进程内会话状态**为接入过程的单一真相源(已上传文件、字段映射、归类结果、声明的新表、日期决策、节点位置),所有 mutation(上传/删/映射/拖节点/确认日期)MUST 经服务端更新并返回全量状态供前端整体重渲染。会话状态 MUST NOT 依赖前端内存作为真相源。本 change MUST NOT 引入落盘 JSON 会话文件及其原子写/并发同步复杂度——演示进程生命周期内的内存态足够,冷启动时 MUST 能从 `_uploads/` 现存文件重建自动映射。

#### Scenario: 刷新页面恢复进度

- **WHEN** 用户完成部分映射后在同一服务进程内刷新或重开 `/onboarding`
- **THEN** 系统 MUST 从服务端会话状态恢复全部文件/映射/归类/节点位置,MUST NOT 清空已做的工作

#### Scenario: 删文件级联清映射且不漂移

- **WHEN** 用户删除一个已用于映射的上传文件
- **THEN** 服务端 MUST 从会话移除该文件并级联清掉引用它的全部映射,返回的新状态与磁盘 `_uploads/` MUST 一致(无残留映射、无幽灵字段),前端据返回整体重渲染

#### Scenario: 同名重传替换而非生幽灵文件

- **WHEN** 用户重新上传一个与既有文件同名的文件
- **THEN** 系统 MUST 替换该 logical 文件,MUST NOT 产生 `foo_1.csv` 之类的并存幽灵文件

### Requirement: 已载入数据生命周期

系统 SHALL 提供 GUI 入口清空已载入的产出数据,使"重导一遍"无需离开浏览器去终端。

#### Scenario: GUI 清空已载入

- **WHEN** 用户点"清空已载入数据"并通过二次确认
- **THEN** 系统 MUST 删除 `data_import/` 的产出 CSV、`.loaded.env` 与会话产出态,并反馈已清空,使下一次载入从干净状态开始

### Requirement: 终端开跑一键交接

载入成功后,系统 SHALL 把审核开跑做成一个终端命令(`jv-go`)并在 GUI 自动复制到剪贴板,使操作者无需回忆患者号、手敲 `export`、查 README 即可开跑。GUI MUST NOT 在本页运行 LLM 审核(终端日志是面向技术买家的刻意保留)。`jv-go` MUST 自带健壮性:`.loaded.env` 缺失时 MUST 给出明确指引而非静默失败;逐患者进度 MUST 可见。

#### Scenario: GUI 一词交接到终端

- **WHEN** 载入数据成功
- **THEN** 成功面板 MUST 显示"去终端敲 `jv-go`",该命令 MUST 已在剪贴板,且 MUST 提供"没装 jv-* 快捷命令"的原始命令回退

#### Scenario: jv-go 一词开跑并自检数据

- **WHEN** 操作者在终端敲 `jv-go`
- **THEN** 命令 MUST source `.loaded.env` 并对全部已载入患者跑审核;起始 MUST 打印数据来源摘要(表数/患者数/写入时间)供核对;若 `.loaded.env` 不存在 MUST 提示"请先在网页载入数据"并退出

#### Scenario: 逐患者进度可见、失败不中断

- **WHEN** `jv-run-all` 对 N 个患者顺序跑审核
- **THEN** 每个患者完成 MUST 打印一行进度(`[i/N] 患者号 ✓ xV yI zC`),单患者失败 MUST 标 ✗ 且 MUST NOT 中断后续患者

## MODIFIED Requirements

### Requirement: 双栏可视化字段映射工作室

系统 SHALL 在现有工作台提供 `/onboarding` 页面,复用 `create_app`/`AuthMiddleware`/蓝色商务皮肤/静态资源 cache-busting,并纳入鉴权保护路径。页面 MUST 为双栏布局,默认呈现**自动驾驶视图**:文件投放区 + 每个 tabular 文件一张结果卡(绿=就绪/琥珀=需补)+ 单一主操作("载入数据");同时 MUST 提供 **"我的文件 → Javert 表" 流向图**:左列为客户上传文件节点(真实文件名),右列为 Javert 规范表节点,边为实际映射,**边标签 MUST 为该表实际映射的患者键列名(取自会话状态,非 manifest 默认键),映射改变 MUST 重绘**。原 synth/asis/bridge 连接键下拉、桥表配置、逐字段输入框 MUST 收纳进每张卡的"调整 ▾"驾驶舱,默认不直接呈现给客户。

#### Scenario: 进入需鉴权

- **WHEN** 未登录用户访问 `/onboarding`
- **THEN** 浏览器请求 MUST 302 跳登录,API 请求 MUST 返回 401

#### Scenario: 流向图边标签显示真实映射键

- **WHEN** 用户把某文件映射到某 Javert 表(或自动归类完成)
- **THEN** 该边的标签 MUST 显示该表实际映射的患者键列名(而非 manifest 默认键),映射改变 MUST 重绘标签,点击边 MUST 弹出"源列→目标列"逐字段明细

#### Scenario: 驾驶舱收纳专业控件

- **WHEN** 客户演示默认视图加载
- **THEN** synth/asis/bridge 与桥表配置 MUST NOT 直接可见,MUST 仅在用户展开某卡"调整 ▾"时出现

#### Scenario: 流向图节点可拖动且位置随会话恢复

- **WHEN** 用户拖动流向图中的某个节点(增强项)
- **THEN** 节点 MUST 跟随移动且相连边 MUST 重连,位置 MUST 记入会话状态并在同进程刷新后恢复

### Requirement: 两道结构性兜底闸

系统 MUST 设两道闸,两闸都过才允许"载入数据":必填禁用闸——任一已映射 spoke 的必填字段未集齐时,"载入数据"按钮 MUST 禁用;连接预检闸——连接预检判为🔴(键几乎不交)时 MUST 挡住运行。预检红灯 MUST 给出可执行诊断(哪张表命中最低 + 是否需要桥表),预检结论与 UI 面板 MUST 保持一致:任一使预检失效的 mutation(改字段映射 / 删文件 / 改连接键模式 / 改桥表配置)后 MUST NOT 残留过期的绿灯。

#### Scenario: 必填没齐禁用

- **WHEN** 费用 spoke 的必填字段"日期"未映射
- **THEN** "载入数据"按钮 MUST 禁用,并提示缺哪个必填字段

#### Scenario: 红灯给可执行诊断而非死胡同

- **WHEN** 连接预检返回🔴(键交集覆盖 <10%)
- **THEN** 系统 MUST 阻止载入,MUST 自动展开各表命中率并高亮命中最低的表,对 0% 命中的表 MUST 提示"大概率列映射错或缺桥表",MUST NOT 仅显示单句"键几乎不交"

#### Scenario: 预检过期对所有失效路径一致不残留假绿灯

- **WHEN** 用户在预检通过(绿)后,通过任一路径(改字段映射 / 删文件 / 改连接键模式 / 改桥表配置)改变了输入
- **THEN** 预检面板 MUST 显示"此前预检已过期"并提供"重新预检",MUST NOT 残留旧绿灯;若用户直接点"载入数据"则系统 MUST 先自动重跑预检再判定

### Requirement: 声明新表入口

系统 SHALL 提供"声明新表"入口(表单 modal,非原生 `prompt()`),允许接入 manifest 未预声明的数据表,落为 stored spoke。modal MUST 支持从已上传文件中选取、填中文名与患者键列、并预览该文件前几行。

#### Scenario: 表单化声明新表

- **WHEN** 用户对一张未预声明的表(如"输血记录")点"声明新表"
- **THEN** 系统 MUST 弹出表单 modal(选文件 + 填中文名/患者键列 + 前 5 行预览),提交后该表 MUST 入流向图并标 stored,病案概览 MUST 可见其原文且标注"暂不参与判定"

### Requirement: 一键落映射并触发 ETL

系统 SHALL 在两闸通过后,把映射结果落为 `column_mapping.yaml` 并触发 `etl_import`,产出 Javert 格式数据供后续审计,并写 `.loaded.env` 供 `jv-*` 命令消费。`.loaded.env` MUST 只导出**本次实际产出的表**对应的 `JAVERT_*_FILE` 环境变量,MUST NOT 硬写未产出的表文件名(防缺表客户拿到指向不存在文件的 env)。

#### Scenario: 一键生成可审计数据

- **WHEN** 用户在两闸通过后点"载入数据"
- **THEN** 系统 MUST 落 mapping、跑 ETL 写出实际映射到的表,并反馈每表行数/患者数

#### Scenario: 缺表客户的 env 不指向不存在文件

- **WHEN** 客户只映射了费用与文书(未提供诊断/手术表)
- **THEN** `.loaded.env` MUST 只导出费用/文书对应的 `JAVERT_*_FILE`,MUST NOT 写 `JAVERT_ZD_FILE=shi_zd.csv`/`JAVERT_SS_FILE=shi_ss.csv` 指向未产出文件,使 `audit-patient` MUST NOT 因找不到文件而报错
