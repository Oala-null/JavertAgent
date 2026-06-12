# 分层原则:Must = 会丢数据 / 会让演示崩 / 静默失败;High = 工作流丝滑;Polish = 观感。
# Must 全落即可现场演示;High 是"丝滑/可解释"主体;Polish 最后。

## 1. Must · 稳:服务端进程内会话状态(消灭"增删出问题")

- [x] 1.1 加服务端进程内会话状态(按 session cookie 索引)`{files,map,classify,stored,date_decisions,node_positions}`,**不落盘 JSON**;`GET/POST /api/onboarding/session` 读写,每次 mutation 返回全量状态 → verify: 单测 mutation 后返回态与服务端态一致
- [x] 1.2 前端 `onboarding.js` 改为从会话状态渲染(替换内存 `state` 作真相源),所有 mutation 走服务端再整体重渲染 → verify: 同进程刷新 `/onboarding` 后映射/归类/节点位置全恢复
- [x] 1.3 删文件改走会话级联清映射(服务端返回新状态,前端整体重渲染),替换旧 `onboarding.js:98-125` 双清 → verify: 删一个被 2 字段引用的文件后,返回态与磁盘 `_uploads/` 零残留映射
- [x] 1.4 同名重传改为替换 logical 文件(改 `_unique_upload_path` 语义),不再生 `foo_1.csv` → verify: 同名传两次只剩一个文件、映射指向新内容
- [x] 1.5 冷启动从 `_uploads/` 现存文件重建自动映射(进程重启兜底)→ verify: 重启服务后 `/onboarding` 能从现存上传文件重建结果卡
- [x] 1.6 `uv run pytest tests/` 全绿(含会话新测,不破现 456 基线)

## 2. Must · 不炸:`.loaded.env` 诚实化 + 清空已载入

- [x] 2.1 `routes_onboarding.py:start` 改为按实际产出表写 `JAVERT_*_FILE`(产出 shi_zd 才写 JAVERT_ZD_FILE,余同),删 426-428 死写 → verify: 只映射费用+文书时 `.loaded.env` 不含 ZD/SS 行
- [x] 2.2 端到端验证缺表客户:构费用+文书-only fixture → 载入 → `audit-patient` 不报"文件不存在" → verify: 审核正常出裁决
- [x] 2.3 加 `POST /api/onboarding/clear-output`(清 data_import 产出 CSV + `.loaded.env` + 会话产出态)+ GUI"清空已载入"按钮 + 二次确认 → verify: 点清空后 `jv-status` 显无已载入、再载入从干净态开始
- [x] 2.4 `uv run pytest tests/` 全绿

## 3. Must · 一词终端收尾(消灭现场拼命令)+ 健壮性

- [x] 3.1 `scripts/javert.zsh` 加 `jv-go` = `cd $JAVERT_HOME && source data_import/.loaded.env && jv-run-all`,起始打印 `.loaded.env` 产出摘要(表/患者数/写入时间);`.loaded.env` 不存在则提示"请先在网页载入数据"并退出 → verify: 载入后单敲 `jv-go` 跑全量;无 `.loaded.env` 时给指引不静默
- [x] 3.2 `scripts/jv_run_all.sh` 改输出逐患者进度行 `[i/N] 患者号 ✓ xV yI zC`,单患者失败标 ✗ 不中断,缺某 `JAVERT_*_FILE` 跳过并 WARN → verify: 多患者跑出逐行进度、有失败也跑完
- [x] 3.3 载入成功面板:主文案"去终端敲 `jv-go`" + 自动复制剪贴板 + 保留原始命令 `<details>` 回退 → verify: 点载入后剪贴板内容为 `jv-go`

## 4. Must · 失败可解释:预检诊断 + 过期一致 + 日期歧义确认

- [x] 4.1 路由层把 `join_preflight` 既有 `per_spoke` 打包为诊断 `{lowest_spoke,lowest_rate,message}`(**不改 join_preflight 函数签名**)→ verify: 单测含一表 0% 命中 case,/preflight 响应含 per_spoke + 最低表 + 建议
- [x] 4.2 `onboarding.js` 预检红灯自动展开 per_spoke + 高亮最低表 + 显建议(替换单句红灯)→ verify: 故意错映射一表 → 红灯直接指出是哪张表
- [x] 4.3 修预检过期一致性:**所有失效路径**(改字段映射/删文件/改键模式/改桥表配置)后面板显"已过期 [重新预检]"、不残留绿灯;点"载入数据"若过期先自动重跑预检 → verify: 逐一触发 4 条路径后面板都转过期态,不能带旧绿灯直接载入
- [x] 4.4 `profiler` 对扫完整列仍无 day>12 的列标"待确认歧义"(不静默默认);载入/预检前弹**一次** modal 列出**全部**歧义列 + 各列 D/M vs M/D 样本对比,选定写会话 `date_decisions`,ETL 据此归一 → verify: 多个全 1–12 日期列只弹一次、选定后 ETL 按选择归一
- [x] 4.5 `uv run pytest tests/` 全绿(含 preflight 诊断打包 + profiler 歧义新测)

## 5. High · 丝滑:文件→表自动归类(最小)+ 结果卡(绿/琥珀)

- [x] 5.1 写 `src/javert/onboarding/classifier.py`:列名对各 **tabular spoke 必填字段别名**覆盖率 → Top-1 归属 + 患者键硬门槛;**仅最小启发式,不引阈值打分**;Top-1 不明确 → 标歧义 → verify: 单测 song/szx 列名命中正确 spoke + 歧义 case 不猜
- [x] 5.2 键模式默认取 manifest `id_form`/`via_bridge`(via_bridge→bridge / bare→裸号 / compound→复合),不探测;用户可在驾驶舱改 → verify: notes 默认 bridge、fees 默认复合、labs 默认裸号(对 manifest)
- [x] 5.3 加 `POST /api/onboarding/classify`,上传后自动归类 + 自动映射 + 写会话;**仅 6 张 tabular spoke 参与**,view spoke(麻醉/病理)不进归类/不作可映射目标 → verify: 上传 4 fixture 文件后 4 表自动认出、麻醉/病理不出现在可映射表
- [x] 5.4 结果卡视图(每文件一张:认出的表 + N/N 必填 + 患者数 + **绿=全中/琥珀=需补**),synth/asis/bridge + 桥表 + 逐字段输入收进"调整 ▾"折叠 → verify: 默认不见专业控件;某表缺必填时卡显琥珀 + 指明缺项 + 可展开补、不阻塞
- [x] 5.5 歧义文件结果卡显"请确认这是哪张表"单选下拉(非逐字段);手动改表归属后更新会话 + 重渲染卡(不静默清空)→ verify: 歧义 fixture 一次下拉定表;auto-classify 后手动改绑会话同步、卡显新绑定
- [x] 5.6 `uv run pytest tests/` 全绿(含 classifier 新测)

## 6. High · "我的文件 → Javert 表" 流向图(标签为主)

- [x] 6.1 重写 `onboarding.js` `drawEdges` 为双侧布局:左=上传文件节点(真实文件名)、右=Javert 表节点、边=实际映射 → verify: 4 文件 4 表连线正确
- [x] 6.2 边标签 = 该表实际映射的患者键列名(取会话),改映射即重绘;点边弹"源列→目标列"明细 → verify: 改某表键映射后线上标签实时变为新列名
- [x] 6.3 替换 `onboarding.html` 星图 DOM 为流向图容器 + 结果卡区;`style.css` 加流向图/卡/折叠样式(蓝色商务皮肤一致)→ verify: 视觉过审

## 7. Polish · 观感增强 + 收尾文档

- [x] 7.1 流向图节点可拖(绝对定位 + `transform` + SVG 重连),位置写会话、同进程刷新恢复;resize 去抖重绘 → verify: 拖动节点后刷新位置保留
- [x] 7.2 "声明新表"改表单 modal(选已上传文件 + 中文名/患者键列 + 前 5 行预览),替 `onboarding.js:517-525` 三个 `prompt()` → verify: modal 流程可声明 stored 表、概览可见原文
- [ ] 7.3 部署 62 冒烟:`/onboarding` 鉴权 + 拖 4 文件自动认表 + 流向图 + 载入 + `jv-go` 全链路 → verify: 现场流程两动作 + 一词跑通(理想路径)/ 琥珀补完(现实路径) — **待人工部署执行** (需 62 服务器 + 浏览器; 本地 TestClient 已验证 page/session/upload/preflight/start/clear-output + jv-* zsh 语法)
- [x] 7.4 更新 `docs/sample_onboarding.md`(自动归类/绿琥珀/流向图/诊断/日期确认/jv-go 实测)+ `CLAUDE.md` onboarding 段 + README 变更日志(v0.11)→ verify: 5 文件清单一致性(按 26er/CLAUDE.md 文档协议)
- [x] 7.5 `uv run pytest tests/` 全绿收口
