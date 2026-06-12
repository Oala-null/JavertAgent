## ADDED Requirements

### Requirement: 双栏可视化字段映射工作室

系统 SHALL 在现有工作台提供 `/onboarding` 页面,复用 `create_app`/`AuthMiddleware`/蓝色商务皮肤/静态资源 cache-busting,并纳入鉴权保护路径。页面 MUST 为双栏布局:左栏从 `schema_manifest.yaml` 渲染 Javert 默认结构的星型 schema,右栏展示用户导入文件抽取出的列。

#### Scenario: 进入需鉴权

- **WHEN** 未登录用户访问 `/onboarding`
- **THEN** 浏览器请求 MUST 302 跳登录,API 请求 MUST 返回 401

#### Scenario: 左栏渲染星图与状态灯

- **WHEN** 页面加载
- **THEN** 左栏 MUST 以患者为 hub、各 spoke 为分支渲染星型结构,每个 spoke MUST 按 manifest `status` 显示状态灯(🟢live/🟡view/⚪stored 或未映射)

### Requirement: 文件上传与自动抽列

系统 SHALL 支持拖入或选择上传 CSV / TXT / XLSX 文件,并自动抽取每个文件的列名展示在右栏。

#### Scenario: 拖入文件抽列

- **WHEN** 用户拖入一个含 47 列的 CSV
- **THEN** 右栏 MUST 列出该文件名与全部 47 个列名,供拖拽映射

### Requirement: 拖拽 / 手打 + 联想 映射

系统 SHALL 支持把右栏列拖到左栏某 spoke 字段完成映射;也 SHALL 支持在字段上手打列名并给出联想下拉(基于 `field_alias.yaml` 与已上传列名)。命中国标别名的字段 MUST 自动预填映射。

#### Scenario: 自动预填后人工确认

- **WHEN** 上传的国标列被别名库自动预填到对应字段
- **THEN** UI MUST 以可区分样式标出"自动预填",用户 MUST 能改或确认

#### Scenario: 手打联想

- **WHEN** 用户在某字段输入部分列名
- **THEN** 系统 MUST 弹出匹配的已上传列名/别名候选下拉

### Requirement: 逐表连接键声明(支持桥表)

系统 SHALL 让用户为每张导入表声明它连到患者 hub 的连接键,并支持声明经桥表的连接(桥表 + 源键⇄canonical 键对)。

#### Scenario: 声明经桥表连接

- **WHEN** 文书表的患者键(medcasno)与费用表(hsp_account_no)不同名
- **THEN** 用户 MUST 能指定文书表经病案首页桥表归一连接,系统据此构交叉表

### Requirement: 两道结构性兜底闸

系统 MUST 设两道闸,两闸都过才允许"开始审计":必填禁用闸——任一已映射 spoke 的必填字段未集齐时,"开始审计"按钮 MUST 禁用;连接预检闸——连接预检判为🔴(键几乎不交)时 MUST 挡住运行。

#### Scenario: 必填没齐禁用

- **WHEN** 费用 spoke 的必填字段"日期"未映射
- **THEN** "开始审计"按钮 MUST 禁用,并提示缺哪个必填字段

#### Scenario: 键不交挡住

- **WHEN** 连接预检返回键交集覆盖 <10%(🔴)
- **THEN** 系统 MUST 阻止开始审计并提示"大概率列映射错了",MUST NOT 带病运行

#### Scenario: 部分匹配可继续

- **WHEN** 连接预检返回🟡(部分匹配/时间错位)
- **THEN** 系统 SHALL 允许继续但弹出提示(并可选加住院期日期窗口过滤)

### Requirement: 声明新表入口

系统 SHALL 提供"声明新表"入口,允许接入 manifest 未预声明的数据表,落为 stored spoke。

#### Scenario: 声明新表落兜底

- **WHEN** 用户对一张未预声明的表(如"输血记录")声明新表并映射患者键
- **THEN** 系统 MUST 接受并存下,病案概览 MUST 可见其原文,标注"暂不参与判定"

### Requirement: 一键落映射并触发 ETL

系统 SHALL 在两闸通过后,把映射结果落为 `column_mapping.yaml` 并触发 `etl_import`,产出 Javert 格式数据供后续审计。

#### Scenario: 一键生成可审计数据

- **WHEN** 用户在两闸通过后点"开始审计"
- **THEN** 系统 MUST 落 mapping、跑 ETL 写出 Javert 四件套(+ 化验/检查可选表),并反馈每表行数/患者数
