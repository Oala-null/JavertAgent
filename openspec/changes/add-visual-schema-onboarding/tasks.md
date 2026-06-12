## 1. Layer1 地基:Manifest + 别名 + ETL 重构

- [x] 1.1 写 `configs/schema_manifest.yaml`,声明现有 4 表 + 化验/检查(live)+ 麻醉/病理(view),每 spoke 含字段(必填可选)/status/loader/tool/join_key/via_bridge
- [x] 1.2 写 `src/javert/onboarding/manifest_loader.py`(pydantic 校验 manifest schema:status 取值合法、必填字段非空、join_key 存在)
- [x] 1.3 写 `scripts/build_field_alias.py` 从 szx/song 映射反推 `configs/field_alias.yaml`(国标↔Javert 语义词典)
- [x] 1.4 重构 `scripts/etl_import.py`:删硬编码 4 表 dict,改为遍历 manifest spoke 转换(保留旧函数为 fallback)
- [x] 1.5 ETL 回归:对 `data/song_fixture/` 与既有 szx fixture 跑新旧实现,逐列 diff 确认老 4 表输出一致
- [x] 1.6 `uv run pytest tests/` 全绿(含 manifest_loader + ETL 回归新测)

## 2. Layer2 剖析/预检 + 桥表归一 + 数据类型扩容

- [x] 2.1 写 `src/javert/onboarding/profiler.py`:按需逐列剖析(数值范围/计数/空值、键唯一值/重复度),chunk 流式 + 采样近似/全量精确两档
- [x] 2.2 profiler 日期探测:整列采样聚 shape、找 day>12 定 dayfirst、容忍小数秒/缺秒/纯日期、识别纯时间无日期列、少数派异常标红(用 design 8 格式族表当测试用例)
- [x] 2.3 写 `src/javert/onboarding/join_preflight.py`:键交集覆盖率 + 时间窗口重叠 + 三态结论,判据=两 getter 实际解析非空(非集合相等)
- [x] 2.4 ETL 接入桥表键归一:用桥表构 `源键→canonical` 交叉表,notes 落裸号/fee/zd/ss 落复合键,归一失败行计数告警
- [x] 2.5 ETL 接入逐列日期归一(复用 2.2 探测),统一存 ISO
- [x] 2.6 `etl_import` 校验阶段调用 profiler + join_preflight(GUI/CLI 共用同源验证)
- [x] 2.7 扩 `configs/column_mapping.yaml` 加化验/检查映射节 + 桥表/连接键字段
- [x] 2.8 写 `src/javert/tools/search_anesthesia.py`(notes 麻醉子阶段 + shi_ss.anst_mtd_name 聚合,标视图来源)
- [x] 2.9 写 `src/javert/tools/search_pathology.py`(lab specimen=病理 + notes 病理子阶段聚合,标视图来源)
- [x] 2.10 改 `src/javert/tools/registry.py`:读 manifest 按 status 注册(live 真工具/view 视图工具/stored 兜底 stub)
- [x] 2.11 端到端验收:用 `song_fixture` 5 人跑 `audit-patient`,确认化验/检查/麻醉/病理工具可被 agent 调用且不崩
- [x] 2.12 `uv run pytest tests/` 全绿(含 profiler/join_preflight/视图工具新测)

## 3. Layer3 /onboarding 可视化工作室

- [x] 3.1 写 `src/javert/web/api/routes_onboarding.py`:上传抽列 / 取 manifest 渲染数据 / 列剖析 / 连接预检 / 落 column_mapping 触发 ETL
- [x] 3.2 `main.py` 挂 onboarding 路由;`middleware.py` 把 `/onboarding` 纳入鉴权保护路径
- [x] 3.3 写 `templates/onboarding.html`:双栏布局,左 manifest 星图 + 状态灯,右上传文件列区,复用蓝色皮肤
- [x] 3.4 静态 JS:文件拖入抽列、列拖拽映射、手打+联想下拉(读 field_alias)、自动预填标注
- [x] 3.5 逐表连接键声明 UI(支持桥表交叉列对)
- [x] 3.6 两道兜底闸:必填没齐禁用"开始审计" + 连接预检🔴挡住/🟡提示可继续
- [x] 3.7 列剖析 popover(点列触发,展示范围/格式/空值率)+ 连接预检结果面板(三态 + 覆盖率 + 时间窗口)
- [x] 3.8 "声明新表"入口 → 落 stored spoke,概览可见 + 标注"暂不参与判定"
- [x] 3.9 一键"开始审计":落 mapping → 跑 ETL → 反馈每表行数/患者数

## 4. 收尾与文档

- [x] 4.1 部署到 62(tar src → 重启 systemd),冒烟测 `/onboarding` 鉴权 + 上传 + 映射 + 一键审计全链路
- [x] 4.2 更新 `docs/数据接入清单.md`(加可视化流程)+ `CLAUDE.md`(架构图加 onboarding + 修正工具数 4→7+)+ README 变更日志
- [x] 4.3 写 `docs/sample_onboarding.md`:song_fixture 5 人接入实测样本(映射截图/剖析/预检三态/裁决)
- [x] 4.4 `uv run pytest tests/` 全绿收口

## 5. v0.10.1 post-review 增强 (多智能体审查 + UX, 已部署 62)

- [x] 5.1 多智能体对抗审查 (spec/regression/correctness/security/gui 5 维 → 逐条对抗验证): 21 候选 → 16 确认全修
  - [x] 🔴 profiler 斜杠年在前日期 (`2025/01/05`) 月日互换 — 加 `saw_year_first` 标志
  - [x] 🔴 `_safe_path` 越权读 `.env`/configs/sqlite (泄 session secret) — 改 data/data_import/_uploads 白名单
  - [x] stored spoke 概览可见原文 — `patient_overview.get_stored_spokes` + 模板节
  - [x] 时间窗口预检接入 (`preflight_time_windows` → GUI payload + CLI) + dead-ternary 修
  - [x] 上传大小上限 (流式 + 2GB cap) + 错误处理 + 文件名唯一化 + TXT 分隔符嗅探
  - [x] JS 两道闸 stale 修 (state.preflight 失效追踪) + 跨文件 mapping 单源约束
- [x] 5.2 上传秒回: `_read_columns` 删全文件扫行数 → 读 2MB csv 数逻辑行 × filesize 缩放 (411MB→12ms, 多行文书 0% 误差)
- [x] 5.3 ER 关系图左栏 (患者hub + SVG 连接键边 `drawEdges`) 替代竖排卡片列表
- [x] 5.4 映射易用性: 跨文件拖列自动改绑 (不弹窗) + 重置/每表清空 + 自动预填补齐
- [x] 5.5 删上传文件: `/api/onboarding/delete-upload` (仅 _uploads 白名单) + GUI ✕
- [x] 5.6 「开始审计」→「载入数据」(诚实 ETL-only) + 载入完 handoff 出 `jv-run` 命令 + 写 `.loaded.env`
- [x] 5.7 jv-* 终端命令: `scripts/{javert.zsh,jv_run_all.sh,loaded_status.py}` (status/run/run-all/run-bg/watch/clear/web), SQL_ENABLED=false 本地 sqlite-only
- [x] 5.8 文档同步 (CLAUDE.md / README / 数据接入清单 / sample_onboarding) + `uv run pytest tests/` 全绿 (456 + 1 skip)
