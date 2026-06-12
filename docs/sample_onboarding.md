# sample_onboarding — 可视化数据接入实测样本

> add-visual-schema-onboarding (v0.10) | 数据源: `data/song/*` (4701 患者真实国标导出) + `data/song_fixture/` (5 患者 e2e 基线)
>
> 本文档记录 `/onboarding` 可视化工作室 + manifest 驱动 ETL 在 **song 真实国标数据**上的接入实测。
> 所有数字均为实跑捕获 (非示意)。

---

## 一、为什么 song 数据会"现场翻车" (本 change 解决的三个真实坑)

song 是某院按 **医保结算清单 / 病案首页字段码** 导出的真实数据，三张表患者键 **不在同一命名空间**：

| 表 | 源文件 | 患者键列 | 命名空间 | key_mode |
|----|--------|---------|----------|----------|
| 费用 | `r_fee.csv` (391 MB) | `bah` | `42506084200-211xxx` (复合) | `asis` |
| 文书 | `szx_doc.csv` (505 MB) | `source_inpat_no` | `226xxx` (medcasno) | **`bridge`** |
| 诊断 | `r_basy_zd.csv` | `ba_id` | `42506084200-211xxx` (复合) | `asis` |
| 手术 | `r_basy_ss.csv` | `ba_id` | `42506084200-211xxx` (复合) | `asis` |

naive join（假设所有表同一列）→ 文书 0 命中。必须经病案首页桥表 `r_basy` 把
`medcasno(226xxx) → psn_no(211xxx)` 归一，文书才落到与费用一致的 canonical 裸号。

日期还跨表/同列不统一：费用 `fee_ocur_time` 是 `D/M/YYYY`（日在前），文书/手术是 ISO。
全局 `dayfirst` 必错一半（`pd.to_datetime('2026-01-05', dayfirst=True)` → `2026-05-01`，月日颠倒）。

---

## 二、接入流程（GUI 五步）

1. **上传** — 拖入 4 个 CSV，右栏自动抽列（费用 36 列 / 文书 7 列 / …）。
2. **映射** — 国标列名 fuzzy 命中 `field_alias.yaml` → 自动预填（`det_item_fee_sumamt → 金额`、
   `medins_list_name → 收费项目名` …），绿框标"自动预填"，人工确认或改。
3. **声明连接键** — 费用/诊断/手术选 `asis`；文书选 `bridge`，填桥表
   `r_basy` + `source_col=medcasno` + `canonical_col=psn_no`。
4. **连接预检**（纯计算，不调 LLM）→ 见下三态。
5. **开始审计**（两闸通过）→ 落 `column_mapping.generated.yaml` + 跑 ETL 写 Javert 四件套。

CLI 等价：`uv run python scripts/etl_import.py --mapping <song_mapping.yaml>`（同源逻辑）。

---

## 三、ETL 实测（manifest 驱动，全量 song）

```
fees:      1,267,018 行, 4680 患者, 桥表失配 0 | 日期归一 ISO, dayfirst=True,  解析率 100%
notes:       623,847 行, 4701 患者, 桥表失配 0 | 日期归一 ISO, dayfirst=False, 解析率 100%   ← bridge medcasno→psn_no
diagnoses:    22,229 行, 4701 患者, 桥表失配 0
surgeries:     7,437 行, 3256 患者, 桥表失配 0 | 日期归一 ISO, dayfirst=False, 解析率 100%
```

- **桥表归一零失配**：4701 个文书患者全部经 `r_basy` 找到 canonical 裸号。
- **逐列日期**：费用列判 `dayfirst=True`（扫到 `30/12/2025` 的 day>12 样本确证），文书/手术列判
  ISO `dayfirst=False`——同次接入两种 dayfirst 并存，正是全局 dayfirst 会翻车之处。

---

## 四、连接预检三态（采样 80 个费用裸号）

```
🟡 键交集覆盖率 72% (参照 fees) — 部分匹配可继续
   · notes:     100%   ← 桥表归一后文书全命中 (D4: get_notes 裸号精确 + get_fees 包含, 两 getter 都解析得出)
   · diagnoses: 100%
   · surgeries:  72%   ← 非映射错: 3256/4701 患者才有手术, 其余无手术天然不交
```

**关键**：预检判据是"两 getter 实际解析非空"而非键集合相等。文书落裸号 `211xxx`、费用落复合键
`42506084200-211xxx`，集合"形态不等"，但 `get_notes("211493468")` 精确命中、
`get_fees("211493468")` 包含命中 `42506084200-211493468` —— D4 判据通过，给 🟢/100%。
若按集合相等判，会被形态差骗成失败。

surgeries 72% 是 **真实信号而非错误**（很多患者本就没手术），故给 🟡 不挡；若是列映射错会跌到
<10% 🔴 挡住。

---

## 五、端到端裁决（song_fixture 5 患者）

`song_fixture/` 是经上述 ETL 对齐后的 5 患者 Javert 四件套。用它跑 `audit-patient`
验证 manifest 驱动 registry（含化验/检查/麻醉/病理工具）可被 agent 调用且不崩：

```
JAVERT_DATA_DIR=data/song_fixture JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv \
JAVERT_SQL_ENABLED=false uv run javert audit-patient 211549440 --rules R191,R151

[1/2] R151 → C conf=0.95 88.8s tc=3
[2/2] R191 → C conf=0.95 96.3s tc=2
Verdicts: V=0 / C=2 / I=0   Tool calls: 5 total
```

直接工具冒烟（10 工具全注册，含 2 个 view 工具，均不崩）：

```
search_anesthesia : OK 【视图来源·暂不参与判定】文书麻醉相关子阶段 9 条
search_pathology  : OK 【视图来源·暂不参与判定】文书病理相关子阶段 5 条
search_lab_results: OK 该患者无检验记录 (song_fixture 无 sy_检验)
search_examinations:OK 该患者无检查记录
search_fees       : OK 费用分类目录 (共241项, 总额¥18734.47)
search_notes      : OK 文书目录 (31 条记录)
```

化验/检查在 song_fixture 无源 → 诚实报"无记录"；麻醉/病理 view 工具聚合文书子阶段并标
"暂不参与判定"（不冒充 live）。三档兜底契约（live/view/stored）下，左侧 ER 关系图每个 spoke
最坏情况都不崩、不静默丢。

---

## 六、v0.10.1 增强（多智能体审查 + UX）

多智能体对抗审查（5 维度 → 逐条对抗验证）发现 **16 个真问题全修**，其中 2 个 🔴：
斜杠年在前日期 `2025/01/05` 被全局 dayfirst 月日互换；`_safe_path` 仅夹 PROJECT_ROOT →
登录用户可读 `.env` 泄 `JAVERT_SESSION_SECRET`（→ 改数据目录白名单）。

UX/性能：
- **上传秒回** — `_read_columns` 删全文件扫行数，改读前 2MB 用 csv 数逻辑行 × filesize 缩放。
  实测 411MB `sy_检验.csv` **12ms**（旧版全扫数秒）；106MB `case_notes.csv` 行数估算 **0% 误差**。
  （朴素物理行计数对含换行的多行文书字段会大幅虚高 —— 例 `song_fixture/case_notes.csv` 195 逻辑行被算成约 1.7 万物理行 ≈ 88×；改用 csv 逻辑行计数后归 0。）数值分布仍点列才算。
- **ER 关系图**左栏 — 患者 hub + SVG 连接键边；跨文件拖列自动改绑；重置/清空映射；删上传文件 ✕。
- **「载入数据」handoff** — 不再叫"开始审计"（诚实：GUI 只 ETL 载入），载入完出 `jv-run` 命令 + `.loaded.env`。

---

## 七、复现命令

```bash
# 1. 重建别名种子 (改 _SEED / column_mapping 后)
uv run python scripts/build_field_alias.py

# 2. CLI 接入 song (manifest 驱动 + 桥表 + 日期归一)
#    song_mapping.yaml 见本文第一、三节 (fees/diag/surg=asis, notes=bridge r_basy)
uv run python scripts/etl_import.py --mapping song_mapping.yaml --output data_import

# 3. GUI 接入 (本地 jv-web 或 62:8090)
#    浏览器登录 → /onboarding → 拖文件 → ER 图映射 → 连接预检 → 载入数据

# 4. 用接入后数据审计 (jv-* 终端命令, 本地 sqlite-only)
jv-status                  # 看可审核患者
jv-run <裸号>              # 单患者; jv-run-all 全部
# 等价手敲:
export JAVERT_DATA_DIR=data_import JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv JAVERT_SQL_ENABLED=false
uv run javert audit-patient <裸号> --priority all --use-router --concurrency 5
```

---

## 八、redesign-onboarding-demo-flow (v0.12) — 现场演示自动驾驶

> 定位转变: v0.10 是"懂数据模型的操作者把列映射对"的工程师工具; 本次在其上加一层**自动驾驶**,
> 面向**投资方/合作医院现场演示** — 客户当场递 4 个他们 HIS 导出的脏 CSV, 当着客户面零查 README、
> 零终端拼命令、不手动逐字段改映射地把数据跑出来。**两个动作是理想路径** (拖文件 + 点「载入数据」),
> **现实路径** = 自动映射大部分 + 少数「琥珀」卡在驾驶舱补完 — 二者都不崩、不出丑。

### 新交互 (相对 v0.10 的变化)

| 环节 | v0.10 (工程师工具) | v0.12 (现场自动驾驶) |
|------|-------------------|---------------------|
| **认表** | 左栏固定星图 8 节点, 操作者自己判断哪个文件映哪张表 | 上传即自动归类 (列名对必填别名覆盖率 → Top-1), 结果卡显「已认出: 费用明细」 |
| **映射状态** | 字段绿框「自动预填」 | 结果卡 **绿=全中就绪 / 琥珀=缺必填需补**, 指明缺项 + 「调整 ▾」展开补 |
| **专业控件** | synth/asis/bridge 下拉 + 桥表框常驻 | 收进每卡「调整 ▾」驾驶舱, 客户演示路径不出现 |
| **ER 图** | Javert 自己的固定 schema 星图 (不可拖, 线上字段是 manifest 默认键) | **我的文件 → Javert 表** 双侧流向图, 连线标签 = **实际映射的患者键列**, 可拖节点, 点线看「源列→目标列」 |
| **增删数据** | 前端内存态 + 磁盘 + 产出三处手工对齐, 误刷新全丢, 同名生 `foo_1.csv` 幽灵 | **服务端进程内会话态单一真相源** (设计 D4), 删/重传/刷新都稳, 同名替换, 冷启动从 `_uploads/` 重建 |
| **预检红灯** | 单句"键几乎不交" | **可执行诊断**: 自动展开各表命中率 + 高亮命中最低的表 + 给成因建议 (列映射错 / 缺桥表) |
| **日期歧义** | 整列 1–12 时静默默认 dayfirst (可能反转数据) | **一次性弹确认 modal** 列出全部歧义列 + D/M vs M/D 样本对比, 选定写会话, ETL 据此归一 |
| **声明新表** | 3 个连续 `prompt()` | 表单 modal (选文件 + 中文名/患者键列 + 前 5 行预览) |
| **终端收尾** | alt-tab 回忆 `jv-run <患者号>` / 粘 `export …` | 载入面板「去终端敲 `jv-go`」+ 自动复制剪贴板; `jv-go` = source `.loaded.env` + 逐患者进度跑全量 |
| **重导** | 离开浏览器去终端 `jv-clear` | GUI「清空已载入」按钮 (二次确认) |
| **`.loaded.env`** | 硬写 `JAVERT_ZD_FILE=shi_zd.csv` / `SS` — 缺表客户拿到指向不存在文件的 env 直接炸 | **只写实际产出表** 对应的 `JAVERT_*_FILE` (设计 D5 诚实化) |

### 自动归类判据 (最小启发式, 设计 D2)

`src/javert/onboarding/classifier.py`:列名对各 **tabular spoke 必填字段别名**覆盖率 + **患者键硬门槛**
→ Top-1 归属; **刻意不引需真实数据标定的复杂打分阈值**。Top-1 不明确 (多表覆盖相当 / 无患者键)
→ 结果卡显「请确认这是哪张表」单选下拉, **不猜**。键模式默认取 manifest (`via_bridge`→bridge / 否则
synth 据 `id_form` 落 compound/bare), 确定性无需探测, 选错由预检红灯兜住、驾驶舱一键改。

仅 **6 张 tabular spoke** 参与归类; view spoke (麻醉/病理) 无 `output_file` → 不进归类、不作可映射目标,
仅作「由文书/化验派生·无需映射」上下文呈现 (设计 D9, 消灭"页面 8 个节点客户困惑要映几张")。

### 终端一词开跑 (设计 D6, 保留终端收尾)

GUI **不跑 LLM** (技术买家眼里滚动的真实审计日志 = 可信度资产)。载入成功 → 终端单敲:

```bash
jv-go          # = cd $JAVERT_HOME && source data_import/.loaded.env && jv-run-all
               # 起始打印产出摘要 (表/患者数/写入时间) 供核对; .loaded.env 缺失则给指引不静默
```

`jv-run-all` 逐患者打印进度行 `[i/N] 患者号 ✓ xV yI zC`, 单患者失败标 ✗ 不中断, 缺某 `JAVERT_*_FILE`
跳过并 WARN。`data_import/.loaded.env` 只含本次实际产出表 — 缺诊断/手术表的客户 `audit-patient` 不再报"文件不存在"。

### 代码落点

- 新增 `src/javert/onboarding/classifier.py` (归类最小启发式 + `default_key_mode`)
- 新增 `src/javert/web/onboarding_session.py` (进程内会话态单一真相源, 不落盘 JSON)
- `routes_onboarding.py` 加 `/session` (读写) `/classify` `/date-ambiguities` `/clear-output`;
  `start` 写诚实 `.loaded.env`; preflight 路由层打包可执行诊断 (不改 `join_preflight` 签名)
- `profiler.is_ambiguous_dayfirst` + `normalize_date_series(dayfirst_override=)` + `etl_engine` 据会话 `date_decisions` 归一
- `onboarding.{html,js}` + `style.css` 重写为流向图 + 结果卡 + 渐进披露 + 日期/声明 modal
- `scripts/javert.zsh` 加 `jv-go`; `scripts/jv_run_all.sh` 逐患者进度行 + 缺表跳过

> 部署 62 现场冒烟 (拖 4 文件自动认表 → 流向图 → 载入 → `jv-go` 全链路) 见 `docs/deployment_192_62.md`。
