# 试点医院数据对接表单 — 国标 TB_* 版

> 版本 v1.0 | 2026-07-05 | 适用系统: Javert (医保自查自纠 LLM 审计) + zadig_agent (DRG 重确认)
>
> 本表单供院方信息科 / 数据工程团队对照勾选与准备数据。数据按国家医保局数据中台标准
> **TB_\* 表结构**交付 (含我方 2 张扩展表: 文书 + 费用扩展), 我方系统经取数桥零改动接入。
> 表结构 DDL 全文参照: `Scriv/Data_Hub/TB_*.md` (46 张国标表) + `Scriv/data_hub_filled/_ext_tables.sql` (2 张扩展表, v2.2)。
>
> **使用方法**: 每张表右侧「院方情况」栏勾选 `□ 可提供 / □ 部分字段 / □ 无此数据`,
> 并注明源系统 (HIS/EMR/LIS/RIS/病案系统)。字段表中 **●=必填** (审计直接消费, 缺了该类审计跑不了),
> **○=建议** (提升精度), 未列出的国标列按 DDL 建表、无源填 `'-'` 或 NULL 即可。

---

## 一、总览 — 三档优先级

| 档 | 定位 | 表 | 数量 |
|----|------|----|------|
| **第 1 档 · 必备** | 系统跑起来的最低集合 | 文书 + 费用×2 + 诊断 + 手术 + 机构字典 + 就诊 | 7 张 |
|  |  |  |  |
| **第 2 档 · 强烈建议** | 审计精度↑ / DRG 重确认刚需 | 检验×2 + 检查×2 + 病案首页×4 | 8 张 |
| **第 3 档 · 扩容预留** | 未来解锁新审计能力 (医嘱/处方/影像明细/字典) | 医嘱 + 处方 + 影像明细 + 字典×4 | 7 张 |

**最小可跑** = 第 1 档中的 文书 + 费用 两类 (即可出重复收费/串换/虚构类结论);
诊断 + 手术补齐后可进入当前 **118 条 production-ready 规则**主流程（具体可判范围仍取决于
文书、检验、检查和药品数据）；第 2 档补齐后检验指征类 + DRG 链路生效。规则实时口径以
部署版本的 `javert list` 为准。

### 各表消费方速查

| TB 表 | 中文 | 档 | 谁在消费 |
|-------|------|:--:|----------|
| TB_CIS_MEDICAL_DOCUMENT ⁺ | 病历文书 (全文) | 1 | Javert `search_notes`/`note_diagnosis` — **全链路命脉** |
| TB_HIS_ZY_FEE_DETAIL_FS | 住院费用发生明细 | 1 | Javert `search_fees` + Router 预筛 |
| TB_HIS_ZY_FEE_DETAIL_EXT ⁺ | 费用医保分解扩展 | 1 | M4 超标准收费信号 + 开单医生/科室 + 药品通用名 |
| TB_IH_DIAGNOSIS_DETAIL | 诊断明细 | 1 | 诊断指征判断 + `drug_audit_lookup` + verdict_gate |
| TB_OPRATION_DETAIL | 手术明细 | 1 | 手术类规则 + verdict_gate 麻醉/手术判据 |
| TB_DIC_HOSPITAL | 医院信息 | 1 | 院区码↔机构码映射 (取数桥必需) |
| TB_YL_ZY_MEDICAL_RECORD | 住院就诊记录 | 1 | 键桥锚点 (JZLSH↔CISID↔BAH) + 入出院时间 |
| TB_LIS_REPORT / TB_LIS_INDICATORS | 检验报告头 / 指标 | 2 | Javert `search_lab_results` (检查指征类规则) |
| TB_RIS_REPORT / TB_RIS_REPORT2 | 检查报告 放射类 / 非放射类 | 2 | Javert `search_examinations` (含超声/病理/心电/内镜) |
| TB_BA_SYJBK | 病案首页 (233 列) | 2 | zadig_agent DRG 重确认 + 病案概览 + 费用宽表核验 |
| TB_BA_SYZDK / TB_BA_SYSSK | 首页其他诊断 / 首页手术 | 2 | 首页口径 ground truth |
| TB_CIS_DRADVICE_DETAIL | 住院医嘱 (长期/临时) | 3 | 未来 `search_orders`: 药品频次/途径/配伍审计 |
| TB_CIS_PRESCRIPTION_DETAIL | 门诊处方 | 3 | 未来门诊审计扩展 |
| TB_RIS_REPORT_DETAIL | 影像检查项目明细 | 3 | 未来影像报告↔收费项目挂钩核验 |
| TB_DIC_MEDICINES / MATERIALS / PRACTITIONER / DEPARTMENT | 药品/耗材/医护/科室字典 | 3 | 编码消歧 + 未来跨患者统计 |

⁺ = 我方扩展表 (国标 46 表之外), 建表脚本 `_ext_tables.sql` 我方提供, 幂等可重复执行。

---

## 二、全局键体系与格式约定 (先读, 违者撞库)

| # | 约定 | 说明 |
|---|------|------|
| 1 | **JZLSH 全库唯一患者关联键** | 住院就诊流水号。**所有表同一患者用同一值**, 一次住院一个值。这是我方系统关联所有表的唯一纽带 |
| 2 | **YLJGYQDM 院区代码** | varchar(8)。请用 4 位短码 (如 `0001`); 12 位国标机构码放 TB_DIC_HOSPITAL.YYJC, 勿截断塞入 |
| 3 | **SYXH = JZLSH** | 病案首页四表 (SYJBK/SYZDK/SYSSK/SYYEK) 的首页序号与 JZLSH 取同值 |
| 4 | **SFMXID 必须真唯一** | 费用明细 ID。若 HIS 收费流水号跨患者重复, 请合成 `{JZLSH}-{流水号}-{序号}` (≤32 字符) |
| 5 | **日期格式 ISO** | `YYYY-MM-DD HH:MM:SS`。**禁止** `D/M/Y` 或 `M/D/Y` 混用; BGRQ 类列为 `YYYYMMDD` |
| 6 | **1900-01-01 哨兵语义固定** | = "尚未发生" (未出院 / 医嘱未终止)。**不能**当"时间未知"的默认值乱填 |
| 7 | **退费行** | STFBZ=2 单独成行, 数量/金额存正值 (符号由 STFBZ 表达) |
| 8 | **两套编码不许混** | 临床版 ICD (诊断明细/手术明细) 与医保版编码 (费用 MXXMBMYB; 医保版手术双码走 zadig_agent 重确认请求体) 各归各列 |
| 9 | **NOT NULL 无源兜底** | 字符列填 `'-'`; 时间列按业务回退链取最近真实时间, **不造假时间** |
| 10 | **脱敏** | 患者姓名可填 `'-'`; 身份证不需要; 卡号 KH=`'-'`、卡类型 KLX=`'99'` 即可 |

---

## 三、第 1 档 — 必备表 (7 张)

### 3.1 TB_CIS_MEDICAL_DOCUMENT — 病历文书 ⁺扩展表 【一段文书一行】

> 全系统命脉: LLM 读文书判断"有无反证/有无指征"。整份文书请**按段落拆行**
> (如 入院记录 拆成 主诉/现病史/既往史… 各一行); 无法拆分时整文一行亦可接受 (精度略降)。
> 覆盖文书类型越全越好: 入院记录 / 病程记录 / 手术记录 / 出院小结 / 知情同意 / 会诊记录…

| 字段 | 类型 | 中文 | 必填 | 说明 |
|------|------|------|:----:|------|
| YLJGYQDM | varchar(8) | 院区代码 | ● | |
| JZLSH | varchar(64) | 就诊流水号 | ● | 全库患者键 |
| WSLSH | varchar(64) | 文书流水号 | ● | PK, 院内唯一 |
| WSLB | varchar(2) | 文书类别码 | ● | 码表我方提供 (`_dictionaries/wslb_码表.csv`) |
| WSMC | nvarchar(256) | 文书名称 | ● | 如 `入院记录`、`出院小结` (贵院原始名称即可) |
| DLBT | nvarchar(128) | 段落标题 | ○ | 如 `主诉`/`现病史`; 未拆段可空 |
| DLXH | int | 段落序号 | ○ | |
| JLSJ | datetime | 记录时间 | ○ | |
| ZW | nvarchar(max) | **正文** | ● | 该段落全文 |

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 3.2 TB_HIS_ZY_FEE_DETAIL_FS — 住院费用发生明细 【一笔收费一行】

> 费用主战场。请给**发生口径**明细 (费用发生时间), 不是结算汇总。

| 字段 | 类型 | 中文 | 必填 | 说明 |
|------|------|------|:----:|------|
| YLJGYQDM / JZLSH | | 院区码 / 患者键 | ● | |
| SFMXID | varchar(32) | 收费明细 ID | ● | PK, 见约定 4 |
| STFBZ | varchar(1) | 收/退费标志 | ● | 1 收费 / 2 退费 |
| FYFSSJ | datetime | 费用发生时间 | ● | |
| MXXMMC | varchar(1024) | 明细项目名称 | ● | 项目/药品/耗材名 |
| MXXMJE | decimal(15,3) | 明细金额 | ● | 正值 |
| MXFYLB | varchar(2) | 费用类别 2 位码 | ● | 01床位…10西药…20病理 (码表我方提供) |
| MXXMBMYB | varchar(64) | 医保项目编码 | ● | 国家医保码; 无则 `'-'` |
| MXXMBM | varchar(32) | 院内项目编码 | ○ | |
| MXXMSL / MXXMDJ | decimal | 数量 / 单价 | ○ | |
| YZID | varchar(32) | 相关医嘱 ID | ○ | 有则填, 为未来医嘱联审留钩子 |

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 3.3 TB_HIS_ZY_FEE_DETAIL_EXT — 费用医保分解扩展 ⁺ 【与 FS 1:1 挂接】

> 超标准收费 (M4) 审计信号源 + 开单医生/科室维度 + 药品通用名。字段能给多少给多少, 全空也不阻塞第 1 档跑通。

| 字段 | 中文 | 必填 | 字段 | 中文 | 必填 |
|------|------|:----:|------|------|:----:|
| SFMXID / JZLSH | 挂接键 | ● | CHRGITM_LV | 收费等级 (甲乙丙) | ○ |
| PRODNAME | 药品通用名 | ○ | MED_LIST_CODG | 国家医保编码 (完整) | ○ |
| SPEC / DOSFORM_NAME | 规格 / 剂型 | ○ | MEDINS_CHRGITM_TYPE | 源费用类别 (中文) | ○ |
| BILG_DEPT_NAME / BILG_DR_NAME | 计费科室 / 医生 | ○ | ORDERS_DR_NAME | 开单医生 | ○ |
| PRIC_UPLMT_AMT | 限价 | ○ | SELFPAY_PROP | 自付比例 | ○ |
| FULAMT_OWNPAY_AMT | 全自费金额 | ○ | OVERLMT_AMT | 超限价金额 | ○ |
| PRESELFPAY_AMT | 先行自付金额 | ○ | INSCP_SCP_AMT | 医保范围内金额 | ○ |
| DSCG_TKDRUG_FLAG | 出院带药标志 | ○ | HOSP_APPR_FLAG | 医院审批标志 | ○ |

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 3.4 TB_IH_DIAGNOSIS_DETAIL — 诊断明细 【一个诊断一行】

| 字段 | 类型 | 中文 | 必填 | 说明 |
|------|------|------|:----:|------|
| YLJGYQDM / JZLSH | | 院区码 / 患者键 | ● | |
| ZYZDLSH | varchar(32) | 诊断流水号 | ● | PK, 机构内唯一 |
| ZDBM | varchar(256) | 诊断编码 | ● | ICD-10 **临床版** |
| ZDSM | varchar(512) | 诊断名称 | ● | |
| CYZDBZ | varchar(1) | 主要诊断标志 | ● | 1 主诊断 / 2 非主诊断 |
| ZDLB / ZDSJ / RYBQ | | 诊断类别 / 时间 / 入院病情 | ○ | |

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 3.5 TB_OPRATION_DETAIL — 手术明细 【一台手术/操作一行】

| 字段 | 类型 | 中文 | 必填 | 说明 |
|------|------|------|:----:|------|
| YLJGYQDM / JZLSH | | 院区码 / 患者键 | ● | |
| SSMXLSH | varchar(32) | 手术明细流水号 | ● | PK |
| SSCZMC | varchar(256) | 手术操作名称 | ● | |
| SSCZBM | varchar(32) | 手术操作编码 | ● | ICD-9-CM3 **临床版** |
| ZCBZ | varchar(2) | 主次标志 | ● | 1 主手术 / 2 非主手术 |
| SSKSSJ / SSJSSJ | datetime | 手术起止时间 | ○ | |
| SSJB | varchar(1) | 手术级别 | ○ | 一~四级 |
| MZFS | varchar(2) | 麻醉方式 | ○ | verdict_gate 麻醉判据用 |
| SXYHRYXM / MZYHRYXM | varchar(64) | 术者 / 麻醉医师姓名 | ○ | |
| QKYHDJ | varchar(3) | 切口愈合等级 | ○ | |

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 3.6 TB_DIC_HOSPITAL — 医院信息 【1-2 行】

● YLJGYQDM (院区短码) · YYMC (机构名称) · YYJC (机构简称/原 12 位机构码) · JGJB/JGDJ (等级/等次)

### 3.7 TB_YL_ZY_MEDICAL_RECORD — 住院就诊记录 【一次住院一行】

> 全库键桥锚点。JZLSH ↔ CISID (住院号) ↔ BAH (病案号) 三键在此对齐。

| 字段 | 中文 | 必填 | 说明 |
|------|------|:----:|------|
| YLJGYQDM / JZLSH | 院区码 / 就诊流水号 | ● | |
| CISID / BAH | 住院号 / 病案号 | ● | 与 JZLSH 同值亦可 |
| JZKSMC / CYKSMC | 入院科室 / 出院科室名称 | ○ | |
| RYSJ / CYSJ | 入院 / 出院时间 | ● | 未出院 CYSJ=`1900-01-01` |
| HZXM | 患者姓名 | ○ | 可 `'-'` 脱敏 |

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

---

## 四、第 2 档 — 强烈建议 (8 张)

### 4.1 检验: TB_LIS_REPORT (报告头) + TB_LIS_INDICATORS (指标) 【一份报告一行 + 一个指标一行】

> 解锁"检查/用药有无指征"类规则的客观证据 (如术前用抗菌药但白细胞正常)。

**LIS_REPORT** ●: YLJGYQDM · BGDH (报告单号) · BGRQ (报告日期 YYYYMMDD) · JZLSH · BGSJ (报告时间) · BBMC (标本名称) · BGDLB (报告单类别, 如"血常规")
○: SQKS (申请科室) · BRXB/BRNL (性别/年龄) · BGYHRYXM/SHYHRYXM (报告/审核人)

**LIS_INDICATORS** ●: YLJGYQDM · JYZBLSH (指标流水号, 真唯一, 建议 `{BGDH}-{行序}`) · BGDH/BGRQ (挂报告头) · JYZBMC (指标名称) · JYZBJG (结果) · YCTS (异常提示: 1正常/2异常/3偏高/4偏低; **定性阴性=1 正常**)
○: JYZBDM (指标代码如 RBC) · JLDW (单位) · CKZ (参考值)

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 4.2 检查: TB_RIS_REPORT (放射/核医学) + TB_RIS_REPORT2 (超声/病理/心电/内镜等) 【一份报告一行】

> 分流规则: 检查类型 ∈ {放射, 核医学} → REPORT; 其余 (超声/心超/病理/心电/内镜/电生理) → REPORT2。

**RIS_REPORT** ●: YLJGYQDM · INSTANCEUID (报告流水号) · JZLSH · EXAMTYPE (检查类型) · JCMC (检查名称) · YXZD (影像诊断) · YXBX (影像表现) · JCSJ/BGSJ (检查/报告时间)
○: JCBW (部位) · JCKS (检查科室) · BGLCZD (临床诊断) · YYS (阴阳性) · STUDYUID (对接 PACS 用, 见 5.3)

**RIS_REPORT2** ●: 同上键位 + JCBGJG (检查报告结果) · BT1NR (所见/描述) · BT2NR (诊断/结论)
○: JCJGDM (结果代码 1正常/2异常) · 其余 BTx 标题块按贵院报告结构填

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 4.3 病案首页三表: TB_BA_SYJBK + TB_BA_SYZDK + TB_BA_SYSSK

> zadig_agent DRG/DIP 重确认的输入主体; Javert 用作 ground truth 与假阳性闸判据。
> SYJBK 233 列**按国标模板能填尽填**, 最低集合如下:

**SYJBK 最低集合** ●: YLJGYQDM · SYXH (=JZLSH) · BAH (病案号) · ZYZD (主要诊断疾病编码) · RYKS/CYKS (入/出院科室) · ZYTS (住院天数) · ZFY (住院总费用) · BRXB (性别) · XSNL (年龄)
○ (DRG 精度↑): LYFS (离院方式) · 24 项费用宽表分类列 (XYF 西药费 / SSF 手术费 / MZF 麻醉费 / HLF 护理费 / KJYWF 抗菌药物费…) · BLZD (病理诊断)

**SYZDK** (首页其他诊断, 主诊断不进本表) ●: SYXH · ZDXH (组内序 1..n) · ZDDM/ZDMC (诊断代码/名称)

**SYSSK** (首页手术) ●: SYXH · SSXH · SSDM/SSMC (手术代码/名称) · SFZYSS (是否主手术)
○: SSRQ · SSJB (级别) · MZFS (麻醉方式) · SSYS/MZYS (主刀/麻醉医生) · MZKSSJ/MZJSSJ (麻醉起止)

> 注 (v2.2): 原 SYSSK_EXT 扩展表已取消——术者/麻醉医师编码与手术时间在国标 TB_OPRATION_DETAIL 原生列
> (SXYHRYID/MZYHRYID/SSKSSJ, **SSXH 请与 SYSSK.SSXH 对齐**); 医保版手术双码经 zadig_agent 重确认请求体提交, 不入中台。

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

---

## 五、第 3 档 — 扩容预留 (现在能给就给, 未来解锁新审计)

### 5.1 TB_CIS_DRADVICE_DETAIL — 住院医嘱明细 (长期/临时医嘱) 🔥 优先级最高的扩容项

> 解锁药品审计 (M8) 的"怎么用的"视角: 频次违规 (bid 开成 qid)、给药途径违规、配伍禁忌、
> 长期医嘱未停、医嘱与收费交叉核验 (有费无嘱=虚构信号)。国标表已有 53 列定义, 映射我方已备好。

| 字段 | 中文 | 必填 | 字段 | 中文 | 必填 |
|------|------|:----:|------|------|:----:|
| YZID | 医嘱 ID (PK) | ● | JZLSH | 患者键 | ● |
| **YZLB** | **医嘱类别 1长期/2临时/3出院带药** | ● | YZXDSJ | 医嘱下达时间 | ● |
| YZZZSJ | 医嘱终止时间 (未停=1900-01-01) | ● | MXXMMC | 项目/药品名称 | ● |
| MXXMBMYB | 医保编码 | ○ | YZLX | 医嘱项目类型 | ○ |
| YF / YPYF | 用药途径代码 / 用法 | ○ | YYPCDM / YYPD | 用药频次代码 / 频度 | ○ |
| JL / DW | 每次剂量 / 单位 | ○ | MCSL / MCDW | 每次数量 / 单位 | ○ |
| YPGG | 药品规格 | ○ | YYTS | 用药天数 (出院带药) | ○ |
| XDKSBM / YZXDYHRYXM | 下达科室 / 医生 | ○ | ZXKSBM / YZZXSJ | 执行科室 / 执行时间 | ○ |

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 5.2 TB_CIS_PRESCRIPTION_DETAIL — 门诊处方明细

> 当前审计范围为住院; 门诊数据到位后解锁门诊审计扩展。核心字段与住院医嘱对称:
> ● CYH/CFMXH (处方号/明细号) · JZLSH · KFRQ (开方时间) · MXXMMC · XMMXSL/XMMXJE (数量/金额)
> ○ YPGG/SYPC/JL/YF (规格/频次/剂量/途径) · JZKSDM/YHRYXM (科室/医生)

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 5.3 影像扩容: TB_RIS_REPORT_DETAIL + PACS 关联

> 两层扩容: ① **REPORT_DETAIL** 把检查报告与收费项目挂钩 (报告↔医保编码), 解锁"收了 CT 费但无 CT 报告"
> 类虚构核验: ● INSTANCEUID/STUDYUID (挂报告) · SFFSL (是否放射) · MXXMBMYB/MXXMMC (收费项目医保码/名称);
> ② **PACS 影像本体**暂不需要传输 DICOM 文件, 只要 RIS_REPORT 的 STUDYUID/PATIENTID (影像号)/SFYYY
> (是否有影像) 真实可回查, 未来抽查时能按 StudyUID 调阅即可。

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

### 5.4 字典四表: TB_DIC_MEDICINES / TB_DIC_MATERIALS / TB_DIC_PRACTITIONER / TB_DIC_DEPARTMENT

> 药品字典 (通用名/剂型/国家医保码) 用于药品审计编码消歧; 耗材字典解锁耗材规格类规则;
> 医护/科室字典用于跨患者统计与医生维度画像。直接从 HIS 导出全量即可, 行数小、成本低。
>
> **MEDICINES** ●: YYZBDM (院内码) · GJYBBM (国家医保码, 无则 `'-'`) · TYMC (注册通用名) · BZJX (剂型)
> **MATERIALS** ●: YYZBDM · GJYBBM · XMMC (项目名称) · SFDW/SFDJ (计价单位/单价)

**院方情况**: □ 可提供 □ 部分 □ 无 源系统: ________ 备注: ________

---

## 六、数据范围建议与交付方式

| 项 | 建议 |
|----|------|
| **首批患者量** | 20-50 位已出院患者 (冒烟 2-3 位先行); 覆盖 ≥3 个科室、含手术与非手术病例 |
| **时间窗** | 同一批患者的费用/文书/检验/检查时间窗必须重叠 (我方接入时跑连接预检核验) |
| **交付形式** | 三选一: ① 由 DE 走受控流程进入 142 `sh_yb_platform` 数据中台（Javert 只读）; ② 院内建同构库并提供只读账号; ③ 按上述表结构导出 CSV (UTF-8) 交付 |
| **建表脚本** | 国标表 DDL 按医保数据中台标准; 3 张 ⁺扩展表执行我方提供的 `_ext_tables.sql` (幂等) |
| **验收流程** | 我方三步: 连接预检 (患者键交集覆盖率 + 时间窗 🟢🟡🔴) → 冒烟 2-3 患者出审计报告 → 批量跑全量, 结果进审核工作台 |
| **安全承诺** | 姓名/身份证/卡号均可脱敏 (见约定 10); 数据仅用于审计试点, 不出我方环境 |

### 落表核对总清单 (对接会议逐项过)

- [ ] 第 1 档 7 张表字段核对完毕, JZLSH 口径确认 (全表同值)
- [ ] SFMXID / WSLSH / ZYZDLSH / SSMXLSH 四个流水号唯一性确认
- [ ] 日期格式 ISO 确认; 1900-01-01 哨兵语义确认
- [ ] 文书能否按段落拆分 (不能则整文一行, 提前告知)
- [ ] 费用类别码 MXFYLB 对照表拿到 (院内类别 → 2 位国标码)
- [ ] 第 2 档: 检验/检查/病案首页三表可提供范围确认; 医保版手术双码 (zadig_agent 请求体口径) 有无
- [ ] 第 3 档: 医嘱表 (长期/临时) 有无、何时可给 — 影响药品审计深度
- [ ] 交付形式三选一敲定 + 首批患者名单圈定
