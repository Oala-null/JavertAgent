# 试点医院数据对接表单 — 国标 TB_* 全字段版

> 版本 v2.1 | 2026-07-06 | 共 23 张表 (国标 46 表中的 20 张 + 我方扩展 3 张, 表名后标 ⁺; 扩展表建表脚本 `_ext_tables.sql` 我方提供)
>
> 字段表标记 — **非空**: 国标 DDL NOT NULL, 无源字符列填 `'-'`; **审计**: `●` 必填 (我方审计直接消费) · `○` 建议 (提升精度) · 空 = 无源填 `'-'` 或 NULL

---

## 一、我们需要哪几个方面的数据

| # | 方面 | 表 |
|---|------|-----|
| 1 | 就诊主索引与机构 | TB_YL_ZY_MEDICAL_RECORD · TB_HIS_ZY_ADM_REG · TB_YL_PATIENT_INFORMATION · TB_DIC_HOSPITAL |
| 2 | 病历文书 | TB_CIS_MEDICAL_DOCUMENT ⁺ · TB_CIS_LEAVEHOSPITAL_SUMMARY |
| 3 | 住院费用 | TB_HIS_ZY_FEE_DETAIL_FS · TB_HIS_ZY_FEE_DETAIL_EXT ⁺ |
| 4 | 诊断与手术 (临床版) | TB_IH_DIAGNOSIS_DETAIL · TB_OPRATION_DETAIL |
| 5 | 检验 (LIS) | TB_LIS_REPORT · TB_LIS_INDICATORS |
| 6 | 检查 (RIS) | TB_RIS_REPORT · TB_RIS_REPORT2 |
| 7 | 病案首页 | TB_BA_SYJBK · TB_BA_SYZDK · TB_BA_SYSSK |
| 8 | 医保结算清单 | TB_YB_JLC_CBRZDXX |
| 9 | 基础字典 | TB_DIC_DEPARTMENT · TB_DIC_PRACTITIONER · TB_DIC_MEDICINES · TB_DIC_MATERIALS |

---

## 二、目录

| 章节 | 表 | 中文 | 粒度 | 列数 |
|------|-----|------|------|:----:|
| 4.1 | TB_YL_ZY_MEDICAL_RECORD | 住院就诊记录 | 一次住院一行 | 22 |
| 4.2 | TB_HIS_ZY_ADM_REG | 住院登记 | 一次住院一行 | 17 |
| 4.3 | TB_YL_PATIENT_INFORMATION | 患者基本信息 | 一名患者一行 | 46 |
| 4.4 | TB_DIC_HOSPITAL | 医院信息 | 一院区一行 (1-2 行) | 14 |
| 5.1 | TB_CIS_MEDICAL_DOCUMENT ⁺ | 病历文书 | 一段文书一行 | 10 |
| 5.2 | TB_CIS_LEAVEHOSPITAL_SUMMARY | 出院小结 | 一次住院一行 | 42 |
| 6.1 | TB_HIS_ZY_FEE_DETAIL_FS | 住院费用发生明细 | 一笔收费一行 | 27 |
| 6.2 | TB_HIS_ZY_FEE_DETAIL_EXT ⁺ | 费用医保分解扩展 | 与 FS 1:1 挂接 | 28 |
| 7.1 | TB_IH_DIAGNOSIS_DETAIL | 诊断明细 | 一个诊断一行 | 22 |
| 7.2 | TB_OPRATION_DETAIL | 手术明细 | 一台手术/操作一行 | 40 |
| 8.1 | TB_LIS_REPORT | 检验报告 | 一份报告一行 | 39 |
| 8.2 | TB_LIS_INDICATORS | 检验指标结果 | 一个指标一行 | 31 |
| 9.1 | TB_RIS_REPORT | 检查报告 (放射/核医学) | 一份报告一行 | 47 |
| 9.2 | TB_RIS_REPORT2 | 检查报告 (超声/病理/心电/内镜等) | 一份报告一行 | 90 |
| 10.1 | TB_BA_SYJBK | 病案首页 | 一次住院一行 (233 列) | 233 |
| 10.2 | TB_BA_SYZDK | 病案首页其他诊断 | 一个诊断一行 | 15 |
| 10.3 | TB_BA_SYSSK | 病案首页手术 | 一台手术一行 | 25 |
| 11.1 | TB_YB_JLC_CBRZDXX | 医保结算清单-诊断信息 | 一个诊断一行 | 5 |
| 12.1 | TB_DIC_DEPARTMENT | 科室字典 | 一科室一行 | 19 |
| 12.2 | TB_DIC_PRACTITIONER | 医护人员字典 | 一人一行 | 29 |
| 12.3 | TB_DIC_MEDICINES | 药品字典 | 一药品一行 | 18 |
| 12.4 | TB_DIC_MATERIALS | 耗材字典 | 一耗材一行 | 19 |

---

## 三、全局约定

| # | 约定 | 说明 |
|---|------|------|
| 1 | **JZLSH 全库唯一患者关联键** | 住院就诊流水号。**所有表同一患者用同一值**, 一次住院一个值 |
| 2 | **YLJGYQDM 院区代码** | varchar(8)。用 4 位短码 (如 `0001`); 12 位国标机构码放 TB_DIC_HOSPITAL.YYJC |
| 3 | **SYXH = JZLSH** | 病案首页三表 (SYJBK/SYZDK/SYSSK) 的首页序号与 JZLSH 取同值; 结算清单 LSH 亦同值 |
| 4 | **流水号必须真唯一** | SFMXID / WSLSH / ZYZDLSH / SSMXLSH / JYZBLSH; 源流水号跨患者重复时合成 `{JZLSH}-{流水号}-{序号}` |
| 5 | **日期格式 ISO** | `YYYY-MM-DD HH:MM:SS`; BGRQ/CSRQ 类列为 `YYYYMMDD` |
| 6 | **1900-01-01 哨兵语义固定** | = "尚未发生" (未出院 / 医嘱未终止), 不能当"时间未知"乱填 |
| 7 | **退费行** | STFBZ=2 单独成行, 数量/金额存正值 (符号由 STFBZ 表达) |
| 8 | **两套编码不许混** | 临床版 ICD (诊断明细/手术明细) 与医保版编码 (费用 MXXMBMYB / 结算清单; 医保版手术双码走 zadig_agent 请求体) 各归各列 |
| 9 | **NOT NULL 无源兜底** | 字符列填 `'-'`; 时间列取最近真实业务时间, 不造假时间 |
| 10 | **脱敏** | 患者姓名可填 `'-'`; 身份证不需要; 卡号 KH=`'-'` 或患者号、卡类型 KLX=`'99'` |
| 11 | **文书按段落拆行** | 整份文书拆成 主诉/现病史/既往史… 一段一行; 拆不了整文一行 |
| 12 | **检查报告分流** | 放射/核医学 → TB_RIS_REPORT; 超声/病理/心电/内镜/电生理 → TB_RIS_REPORT2 |
| 13 | **检验异常提示 YCTS** | 1 正常 / 2 异常 / 3 偏高 / 4 偏低; 定性阴性=1; JYZBLSH 建议 `{BGDH}-{行序}` |

---

## 四、就诊主索引与机构

### 4.1 TB_YL_ZY_MEDICAL_RECORD — 住院就诊记录 【一次住院一行】

主键: `YLJGYQDM + JZLSH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | JZLSH | varchar(64) | 非空 | ● | 住院就诊流水号 |
| 3 | CISID | varchar(32) | 非空 | ● | 住院号 |
| 4 | BAH | varchar(32) | 非空 | ● | 病案号 |
| 5 | KH | varchar(32) | 非空 |  | 卡号 |
| 6 | KLX | varchar(16) | 非空 |  | 卡类型 |
| 7 | HZXM | varchar(64) | 非空 | ○ | 患者姓名 |
| 8 | JZLX | varchar(3) | 非空 |  | 就诊类型 |
| 9 | JZKSBM | varchar(32) | 非空 | ○ | 入院科室编码 |
| 10 | JZKSMC | varchar(128) | 非空 | ○ | 入院科室名称 |
| 11 | CYKSBM | varchar(32) | 非空 | ○ | 出院科室编码 |
| 12 | CYKSMC | varchar(128) | 非空 | ○ | 出院科室名称 |
| 13 | RYSJ | datetime | 非空 | ● | 入院时间 YYYY-MM-DD HH:MM:SS |
| 14 | CYSJ | datetime | 非空 | ● | 出院时间 尚 未 出 院 时 填 写 默 认 值   “1900-01-01”。作为判别  已入院而尚未出院的标志 |
| 15 | MJ | varchar(16) | 非空 |  | 密级 |
| 16 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 17 | YLYL1 | varchar(128) |  |  | 预留一 |
| 18 | YLYL2 | varchar(128) |  |  | 预留二 |
| 19 | YLYL3 | varchar(128) |  |  | 预留三 |
| 20 | YLYL4 | varchar(128) |  |  | 预留四 |
| 21 | YLYL5 | varchar(128) |  |  | 预留五 |
| 22 | YLYL6 | varchar(128) |  |  | 预留六 |

### 4.2 TB_HIS_ZY_ADM_REG — 住院登记 【一次住院一行】

主键: `YLJGYQDM + JZLSH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | JZLSH | varchar(64) | 非空 | ● | 入院登记时产生的代表该次住院的信息系统唯一识别编号 |
| 3 | KH | varchar(32) | 非空 |  | 卡号 |
| 4 | KLX | varchar(16) | 非空 |  | 卡类型 |
| 5 | RYKS | varchar(32) | 非空 | ○ | 入院科室 填医院系统内部定义的 科室编号 |
| 6 | RYSJ | datetime | 非空 | ● | 入院时间 YYYY-MM-DD HH:MM:SS |
| 7 | LGBZ | varchar(1) | 非空 | ○ | 留观标志 编码。0：住院；1：留院观察 |
| 8 | RYCHID | varchar(128) | 非空 |  | 医院系统内部唯一码 |
| 9 | RYCH | varchar(20) | 非空 | ○ | 入院床号(床头卡片上的床号) |
| 10 | RYCWSX | varchar(1) | 非空 | ○ | 入院床位属性：0：核定床位；1：加床；9：其他 |
| 11 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 12 | YLYL1 | varchar(128) |  |  | 预留一 |
| 13 | YLYL2 | varchar(128) |  |  | 预留二 |
| 14 | YLYL3 | varchar(128) |  |  | 预留三 |
| 15 | YLYL4 | varchar(128) |  |  | 预留四 |
| 16 | YLYL5 | varchar(128) |  |  | 预留五 |
| 17 | YLYL6 | varchar(128) |  |  | 预留六 |

### 4.3 TB_YL_PATIENT_INFORMATION — 患者基本信息 【一名患者一行】

主键: `YLJGYQDM + KH + KLX`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | KH | varchar(32) | 非空 | ● | 卡号 |
| 3 | KLX | varchar(16) | 非空 | ● | 卡类型 |
| 4 | ZJHM | varchar(32) | 非空 |  | 证件号码 |
| 5 | ZJLX | varchar(2) | 非空 |  | 证件类型 |
| 6 | FKDQ | varchar(6) | 非空 |  | 发卡地区 |
| 7 | XB | varchar(1) | 非空 | ● | 性别 |
| 8 | XM | varchar(64) | 非空 | ○ | 姓名 |
| 9 | HZLY | varchar(1) | 非空 |  | 患者来源 |
| 10 | HYZK | varchar(2) | 非空 |  | 婚姻状况 |
| 11 | CSRQ | varchar(8) | 非空 | ● | 出生日期 格式：YYYYMMDD |
| 12 | CSD | varchar(32) | 非空 |  | 出生地 |
| 13 | MZ | varchar(2) | 非空 |  | 民族 |
| 14 | GJ | varchar(32) | 非空 |  | 国籍：中国，    码值：CHN，则填 CHN。若无，    则填“-” |
| 15 | DHHM | varchar(24) | 非空 |  | 电话号码 |
| 16 | SJHM | varchar(20) | 非空 |  | 手机号码 |
| 17 | GZDWYB | varchar(6) | 非空 |  | 工作单位邮编 |
| 18 | GZDWMC | varchar(128) | 非空 |  | 工作单位名称 |
| 19 | GZDWDZ | varchar(128) | 非空 |  | 工作单位地址 |
| 20 | JZDZ | varchar(128) | 非空 |  | 居住地址 |
| 21 | HKDZ | varchar(128) | 非空 |  | 户口地址 |
| 22 | HKDZYB | varchar(6) | 非空 |  | 户口地址邮编 |
| 23 | HJD_S1 | varchar(4) |  |  | 户籍地址-省 |
| 24 | HJD_S2 | varchar(4) |  |  | 户籍地址-市 |
| 25 | HJD_X1 | varchar(4) |  |  | 户籍地址-县 |
| 26 | HJD_X2 | varchar(4) |  |  | 户籍地址-乡 |
| 27 | HJD_JW | varchar(4) |  |  | 户籍地-居委 |
| 28 | HJD_C | varchar(4) |  |  | 户籍地址-村 |
| 29 | HJD_MPH | varchar(4) |  |  | 户籍地址-门牌号 |
| 30 | HJD_BC | varchar(4) |  |  | 户籍地址-补充信息 |
| 31 | LXRXM | varchar(64) | 非空 |  | 联系人姓名 |
| 32 | LXRGX | varchar(8) | 非空 |  | 联系人关系 |
| 33 | LXRDZ | varchar(128) | 非空 |  | 联系人地址 |
| 34 | LXRYB | varchar(6) | 非空 |  | 联系人邮编 |
| 35 | LXRDH | varchar(24) | 非空 |  | 联系人电话 |
| 36 | YWSCSJ | datetime | 非空 |  | 业务生成时间 业务操作获取该患者信息的时间，YYYY-MM-DD HH:MM:SS |
| 37 | YYDAH | varchar(64) | 非空 | ○ | 医院内部患者唯一索引号 |
| 38 | GJJKKID | varchar(64) | 非空 |  | 国家健康卡 ID |
| 39 | MJ | varchar(16) | 非空 |  | 密级 |
| 40 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 41 | YLYL1 | varchar(128) |  |  | 预留一 |
| 42 | YLYL2 | varchar(128) |  |  | 预留二 |
| 43 | YLYL3 | varchar(128) |  |  | 预留三 |
| 44 | YLYL4 | varchar(128) |  |  | 预留四 |
| 45 | YLYL5 | varchar(128) |  |  | 预留五 |
| 46 | YLYL6 | varchar(128) |  |  | 预留六 |

### 4.4 TB_DIC_HOSPITAL — 医院信息 【一院区一行 (1-2 行)】

主键: `YLJGYQDM`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(18) | 非空 | ● | 医疗机构院区代码 |
| 2 | YYMC | varchar(256) | 非空 | ● | 医疗机构名称 |
| 3 | JGJB | varchar(1) | 非空 | ○ | 1：一级；2：二级；3：三级；9：未定级 |
| 4 | JGDJ | varchar(1) | 非空 | ○ | 1：甲等；2：乙等；9：未定等 |
| 5 | JGLB | varchar(6) | 非空 |  | 机构类别 详见 5.3.11.20.JGLB 卫生 机构类别代码 |
| 6 | YYJC | varchar(128) | 非空 | ● | 机构简称 |
| 7 | CJRQ | datetime | 非空 |  | YYYY-MM-DD HH:MM:SS |
| 8 | XGBZ | varchar(1) | 非空 |  | 修改标志 1：正常；2：撤销 |
| 9 | YLYL1 | varchar(128) |  |  | 预留一 |
| 10 | YLYL2 | varchar(128) |  |  | 预留二 |
| 11 | YLYL3 | varchar(128) |  |  | 预留三 |
| 12 | YLYL4 | varchar(128) |  |  | 预留四 |
| 13 | YLYL5 | varchar(128) |  |  | 预留五 |
| 14 | YLYL6 | varchar(128) |  |  | 预留六 |

## 五、病历文书

### 5.1 TB_CIS_MEDICAL_DOCUMENT ⁺ — 病历文书 【一段文书一行】

主键: `YLJGYQDM + WSLSH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码                       [必填] |
| 2 | JZLSH | varchar(64) | 非空 | ● | 住院就诊流水号                         [必填] |
| 3 | WSLSH | varchar(64) | 非空 | ● | 文书流水号 (PK)                        [必填] |
| 4 | WSLB | varchar(2) |  | ● | 文书类别码 (可空; 接入按 WSMC 派生, 码表见 _dictionaries/wslb_码表.csv) |
| 5 | WSMC | nvarchar(256) | 非空 | ● | 文书名称 (原始, 如 入院记录/首次病程记录) [必填] |
| 6 | DLBT | nvarchar(128) |  | ○ | 段落标题 (可空; 医院整篇一行即可, 正文含【段落】标记时接入自动拆) |
| 7 | DLXH | int |  | ○ | 段落序号 (可空) |
| 8 | JLSJ | datetime |  | ○ | 记录时间 (可空) |
| 9 | ZW | nvarchar(max) |  | ● | 正文                                   [必填(业务上)] |
| 10 | XGBZ | varchar(1) | 非空 |  |  |

### 5.2 TB_CIS_LEAVEHOSPITAL_SUMMARY — 出院小结 【一次住院一行】

主键: `YLJGYQDM + JZLSH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | JZLSH | varchar(64) | 非空 | ● | 住院就诊流水号 |
| 3 | KH | varchar(32) | 非空 |  | 卡号 |
| 4 | KLX | varchar(32) | 非空 |  | 卡类型 |
| 5 | KS | varchar(32) | 非空 |  | 科室 |
| 6 | BQ | varchar(32) | 非空 |  | 病区 |
| 7 | BAH | varchar(32) | 非空 |  | 病案号 |
| 8 | CYCHID | varchar(128) | 非空 |  | 出院床号 ID 系统内部唯一码 |
| 9 | CH | varchar(20) | 非空 |  | 床号 |
| 10 | XM | varchar(64) | 非空 |  | 姓名 |
| 11 | BRXB | varchar(1) | 非空 |  | 病人性别 |
| 12 | BRNL | decimal(5,2) | 非空 |  | 年龄 |
| 13 | RYSJ | datetime | 非空 | ○ | 入院时间 YYYY-MM-DD HH:MM:SS |
| 14 | CYSJ | datetime | 非空 | ○ | 出院时间 YYYY-MM-DD HH:MM:SS |
| 15 | ZYTS | decimal(6,1) | 非空 | ○ | 住院天数 |
| 16 | MZZD | varchar(1024) | 非空 | ○ | 门诊诊断 西医：疾病分类与代码国家 临床版。中医填写格式：中 医主诊断(证型+……+证型 +证型,治法)。注意“(”“,” 均为半角字符 |
| 17 | RYZD | varchar(1024) | 非空 | ○ | 入院诊断 西医：疾病分类与代码国家 临床版。中医填写格式：中 医主诊断(证型+……+证型 +证型,治法)。注意“(”“,” 均为半角字符 |
| 18 | CYZD | varchar(1024) | 非空 | ○ | 出院诊断 西医：疾病分类与代码国家 临床版。中医填写格式：中 医主诊断(证型+……+证型 +证型,治法)。注意“(”“,” 均为半角字符 |
| 19 | RYZZTZ | varchar(4000) | 非空 | ○ | 入院时主要症状及体征 该数据项在某些医院的出 院小结中还包括入院时主 要重要检查结果 |
| 20 | JCHZ | varchar(8000) | 非空 | ○ | 实验室检查及主要会诊 该数据项在某些医院的出 院小结中称为“住院期间主 要检查结果” |
| 21 | TSJC | varchar(1024) | 非空 |  | 住院期间特殊检查 |
| 22 | ZLGC | varchar(8000) | 非空 | ○ | 诊疗过程 该数据项在某些医院的出 院小结中称为“住院期间病 程与诊疗结果” |
| 23 | HBZ | varchar(1024) | 非空 |  | 合并症 |
| 24 | CYQK | varchar(1) | 非空 | ○ | 出院情况代码 |
| 25 | CYQKMS | varchar(4000) | 非空 | ○ | 出院情况描述 该数据项在某些医院的出 院小结中称为“出院时情况 （症状、体征）” |
| 26 | CYYZ | varchar(4000) | 非空 | ○ | 出院医嘱 该数据项在某些医院的出 院小结中称为“出院后用药 及建议”。如患者死亡，则 直接填写“死亡” |
| 27 | ZZYSYHRYID | varchar(32) | 非空 |  | 主治医师医护人员 ID |
| 28 | ZZYSRYXM | varchar(64) | 非空 |  | 主治医师医护人员姓名 |
| 29 | ZYYSYHRYID | varchar(32) | 非空 |  | 住院医师医护人员 ID |
| 30 | ZYYSYHRYXM | varchar(64) | 非空 |  | 住院医师医护人员姓名 |
| 31 | YYZTBBT1 | varchar(32) |  |  | 医院自填报内容1 标题 由于出院小结在各医院具 有灵活性，可根据各自样式 自行填写认为重要的内容， 供展示(不用于计算分析处 理) |
| 32 | YYZTB1 | varchar(512) |  |  | 医院自填报内容1 |
| 33 | YYZTBBT2 | varchar(32) |  |  | 医院自填报内容2 标题 |
| 34 | YYZTB2 | varchar(512) |  |  | 医院自填报内容2 |
| 35 | ZLJGSM | varchar(1024) | 非空 |  | 治疗结果 |
| 36 | XGBZ | varchar(1) | 非空 |  | 编码。1：正常；2：撤销 |
| 37 | YLYL1 | varchar(128) |  |  | 预留一 |
| 38 | YLYL2 | varchar(128) |  |  | 预留二 |
| 39 | YLYL3 | varchar(128) |  |  | 预留三 |
| 40 | YLYL4 | varchar(128) |  |  | 预留四 |
| 41 | YLYL5 | varchar(128) |  |  | 预留五 |
| 42 | YLYL6 | varchar(128) |  |  | 预留六 |

## 六、住院费用

### 6.1 TB_HIS_ZY_FEE_DETAIL_FS — 住院费用发生明细 【一笔收费一行】

主键: `YLJGYQDM + SFMXID + STFBZ`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | SFMXID | varchar(32) | 非空 | ● | 收费明细 ID |
| 3 | STFBZ | varchar(1) | 非空 | ● | 收/退费标志 1：收费；2：退费 |
| 4 | JZLSH | varchar(64) | 非空 | ● | 住院就诊流水号 用于与住院就诊记录表关联 |
| 5 | KH | varchar(32) | 非空 |  | 卡号 |
| 6 | KLX | varchar(16) | 非空 |  | 卡类型 |
| 7 | YZID | varchar(32) | 非空 | ○ | 相关医嘱 ID |
| 8 | MXFYLB | varchar(2) | 非空 | ● | 明细费用类别 |
| 9 | FYFSSJ | datetime | 非空 | ● | YYYY-MM-DD HH:MM:SS 费用发生时间 |
| 10 | MXXMBM | varchar(32) |  | ○ | 项目明细编码(院内) |
| 11 | MXXMBMYB | varchar(64) | 非空 | ● | 项目明细编码(医保) |
| 12 | MXXMMC | varchar(1024) | 非空 | ● | 明细项目名称 |
| 13 | MXXMDJ | decimal(15,3) |  | ○ | 明细项目单价 |
| 14 | MXXMSL | decimal(15,3) | 非空 | ○ | 项目明细数量 |
| 15 | MXXMDW | varchar(64) | 非空 | ○ | 项目明细单位 |
| 16 | MXXMJE | decimal(15,3) | 非空 | ● | 项目明细金额 |
| 17 | BZDJ | decimal(15,3) |  |  | 包装单价 |
| 18 | BZDW | varchar(64) |  |  | 包装单位 |
| 19 | BZS | decimal(15,3) |  |  | 包装数 |
| 20 | CLDW | varchar(64) |  |  | 拆零单位 |
| 21 | ZXCLS | decimal(15,3) |  |  | 最小拆零数 |
| 22 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 23 | YLYL1 | varchar(128) |  |  | 预留一 |
| 24 | YLYL2 | varchar(128) |  |  | 预留二 |
| 25 | YLYL3 | varchar(128) |  |  | 预留三 |
| 26 | YLYL4 | varchar(128) |  |  | 预留四 |
| 27 | YLYL6 | varchar(128) |  |  | 预留六 |

### 6.2 TB_HIS_ZY_FEE_DETAIL_EXT ⁺ — 费用医保分解扩展 【与 FS 1:1 挂接】

主键: `YLJGYQDM + SFMXID`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | SFMXID | varchar(32) | 非空 | ● | 1:1 挂 TB_HIS_ZY_FEE_DETAIL_FS.SFMXID |
| 3 | JZLSH | varchar(64) | 非空 | ● | 住院就诊流水号(患者号) |
| 4 | INSCP_SCP_AMT | decimal(15,3) |  | ○ | 医保范围内金额        [性能: Router 预筛精度] |
| 5 | PRODNAME | nvarchar(256) |  | ○ | 药品通用名            [性能: LLM 证据/展示, FS 只有项目名称] |
| 6 | SPEC | nvarchar(128) |  | ○ | 规格                  [性能: 同上] |
| 7 | FEE_TYPE | varchar(8) |  | ○ | 费用类型(源枚举)      [性能: zadig fee_signal 治疗类降权] |
| 8 | MEDINS_CHRGITM_TYPE | nvarchar(16) |  | ○ | 源费用类别(中文)      [性能: 类别主源, MXFYLB 码表为回退] |
| 9 | BILG_DEPT_CODG | varchar(32) |  | ○ | 计费科室编码          [上下文] |
| 10 | BILG_DEPT_NAME | nvarchar(128) |  | ○ | 计费科室名称          [性能: 审计证据上下文] |
| 11 | BILG_DR_CODG | varchar(32) |  | ○ | 计费医生编码          [上下文] |
| 12 | BILG_DR_NAME | nvarchar(64) |  | ○ | 计费医生姓名          [上下文] |
| 13 | ACORD_DEPT_CODG | varchar(32) |  | ○ | 开单(受单)科室编码    [上下文] |
| 14 | ACORD_DEPT_NAME | nvarchar(128) |  | ○ | 开单(受单)科室名称    [上下文] |
| 15 | ORDERS_DR_CODE | varchar(32) |  | ○ | 开单医生编码          [上下文] |
| 16 | ORDERS_DR_NAME | nvarchar(64) |  | ○ | 开单医生姓名          [上下文] |
| 17 | CHRGITM_LV | varchar(8) |  | ○ | 收费项目等级(甲乙丙)  [M4] |
| 18 | PRIC_UPLMT_AMT | decimal(15,3) |  | ○ | 限价                  [M4] |
| 19 | SELFPAY_PROP | decimal(6,4) |  | ○ | 自付比例              [M4] |
| 20 | FULAMT_OWNPAY_AMT | decimal(15,3) |  | ○ | 全自费金额            [M4] |
| 21 | OVERLMT_AMT | decimal(15,3) |  | ○ | 超限价金额            [M4] |
| 22 | PRESELFPAY_AMT | decimal(15,3) |  | ○ | 先行自付金额          [M4] |
| 23 | MED_LIST_CODG | varchar(64) |  | ○ | 国家医保编码          [冗余兜底: FS.MXXMBMYB 为主源] |
| 24 | MEDINS_LIST_CODG | varchar(64) |  | ○ | 院内项目编码完整值    [冗余兜底: FS.MXXMBM varchar(32) 截断时] |
| 25 | LIST_TYPE | varchar(32) |  | ○ | 目录类别              [可选] |
| 26 | DOSFORM_NAME | nvarchar(64) |  | ○ | 剂型                  [可选] |
| 27 | DSCG_TKDRUG_FLAG | varchar(2) |  | ○ | 出院带药标志          [可选] |
| 28 | HOSP_APPR_FLAG | varchar(2) |  | ○ | 医院审批标志          [可选] |

## 七、诊断与手术 (临床版)

### 7.1 TB_IH_DIAGNOSIS_DETAIL — 诊断明细 【一个诊断一行】

主键: `YLJGYQDM + ZYZDLSH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | ZYZDLSH | varchar(32) | 非空 | ● | 诊断流水号 |
| 3 | JZLSH | varchar(64) |  | ● | 就诊流水号 用于与住院就诊记录表或 门诊就诊记录表关联 |
| 4 | MZZYBZ | varchar(2) |  |  | 门诊/住院标志 1：门急诊；2：住院 |
| 5 | KH | varchar(32) |  |  | 卡号 |
| 6 | KLX | varchar(16) |  |  | 卡类型 |
| 7 | ZDLXQF | varchar(1) |  |  | 诊断类型区分 编码。1：西医；2：中医 |
| 8 | ZDLB | varchar(2) |  | ○ | 诊断类别代码 |
| 9 | ZDSJ | datetime |  | ○ | 诊断时间 YYYY-MM-DD HH:MM:SS |
| 10 | ZDBM | varchar(256) |  | ● | 诊断编码 |
| 11 | ZDSM | varchar(512) |  | ● | 诊断说明 |
| 12 | CYZDBZ | varchar(1) |  | ● | 主要诊断标志 编码。1：主要诊断；2： 非主要诊断 |
| 13 | YZDBZ | varchar(1) |  |  | 疑似诊断标志 按 CV05.01.002 诊断状态 代码表 |
| 14 | CYQKBM | varchar(1) |  |  | 出院情况编码 出院情况编码。1：治愈； 2：好转；3：未愈；4：死 亡；5：其它（出院时必填） 门急诊业务场景中填报 “-” |
| 15 | RYBQ | varchar(1) |  | ○ | 入院病情 |
| 16 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 17 | YLYL1 | varchar(128) |  |  | 预留一 |
| 18 | YLYL2 | varchar(128) |  |  | 预留二 |
| 19 | YLYL3 | varchar(128) |  |  | 预留三 |
| 20 | YLYL4 | varchar(128) |  |  | 预留四 |
| 21 | YLYL5 | varchar(128) |  |  | 预留五 |
| 22 | YLYL6 | varchar(128) |  |  | 预留六 |

### 7.2 TB_OPRATION_DETAIL — 手术明细 【一台手术/操作一行】

主键: `YLJGYQDM + SSMXLSH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | SSMXLSH | varchar(32) | 非空 | ● | 手术明细流水号 |
| 3 | JZLSH | varchar(64) |  | ● | 就诊流水号 |
| 4 | MZZYBZ | varchar(2) |  |  | 门诊/住院标志 1：门急诊；2：住院 |
| 5 | KH | varchar(32) |  |  | 卡号 |
| 6 | KLX | varchar(16) |  |  | 卡类型 |
| 7 | RJSSBZ | varchar(2) |  |  | 日间手术标志 编码。1：日间手术；0：非日 间手术。见说明（2） |
| 8 | ZQSSBZ | varchar(1) |  |  | 择期手术标志 编码。1：择期手术；2：非择 期手术；9：不明确 |
| 9 | SSJB | varchar(1) |  | ○ | 手术级别 |
| 10 | SSLX | varchar(2) |  |  | 手术类型 编码：1：一般；2：抢救；9： 其他 |
| 11 | SSCZBM | varchar(32) |  | ● | 手术操作编码 按规定的手术操作分类代码国 家临床版字典执行 |
| 12 | SSCZMC | varchar(256) |  | ● | 手术操作名称 对应于 SSCZBM 的中文名称 |
| 13 | SSQZD | varchar(32) |  |  | 手术前诊断 |
| 14 | SSHZD | varchar(32) |  |  | 手术后诊断 |
| 15 | SSKSSJ | datetime |  | ○ | 手术起始时间 YYYY-MM-DD HH:MM:SS |
| 16 | SSJSSJ | datetime |  | ○ | 手术结束时间 YYYY-MM-DD HH:MM:SS |
| 17 | SXYHRYID | varchar(32) |  |  | 手术医护人员 ID |
| 18 | SXYHRYXM | varchar(64) |  | ○ | 手术医护人员姓名 |
| 19 | SXZ1YHRYID | varchar(32) |  |  | 手术 I 助医护人员 ID |
| 20 | SXZ1YHRYXM | varchar(64) |  |  | 手术 I 助医护人员姓名 |
| 21 | SXZ2YHRYID | varchar(32) |  |  | 手术 II助医护人员 ID |
| 22 | SXZ2YHRYXM | varchar(64) |  |  | 手术 II助医护人员姓名 |
| 23 | MZYHRYID | varchar(32) |  |  | 麻醉医护人员 ID |
| 24 | MZYHRYXM | varchar(64) |  | ○ | 麻醉医护人员姓名 |
| 25 | MZFS | varchar(2) |  | ○ | 麻醉方式 |
| 26 | QKYHDJ | varchar(3) |  | ○ | 切口愈合等级 |
| 27 | SSXH | varchar(6) |  |  | 手术序号 指本条记录描述的是当日的第 几个手术 |
| 28 | YYXSU | varchar(1) |  |  | 医源性手术 指由于医院原因导致该手术。 1：是；2：否 |
| 29 | SSLY | varchar(1) |  |  | 手术来源 指是否源于本院的手术。1：是； 2：否 |
| 30 | MZFY | varchar(1) |  |  | 麻醉反应 1：无麻醉；2：有反应；3：无 反应 |
| 31 | SSBFZ | varchar(32) |  |  | 手术并发症 |
| 32 | SSZH | varchar(6) |  |  | 手术组号 |
| 33 | ZCBZ | varchar(2) |  | ● | 主次标志 若有主次手术的情况下。1：主 手术；2：非主手术 |
| 34 | XGBZ | varchar(1) | 非空 |  | 编码。1：正常；2：撤销 修改标志 |
| 35 | YLYL1 | varchar(128) |  |  | 预留一 |
| 36 | YLYL2 | varchar(128) |  |  | 预留二 |
| 37 | YLYL3 | varchar(128) |  |  | 预留三 |
| 38 | YLYL4 | varchar(128) |  |  | 预留四 |
| 39 | YLYL5 | varchar(128) |  |  | 预留五 |
| 40 | YLYL6 | varchar(128) |  |  | 预留六 |

## 八、检验 (LIS)

### 8.1 TB_LIS_REPORT — 检验报告 【一份报告一行】

主键: `YLJGYQDM + BGRQ + BGDH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | BGRQ | varchar(8) | 非空 | ● | 报告日期 复合主键；YYYYMMDD |
| 3 | BGDH | varchar(64) | 非空 | ● | 检验报告单号 |
| 4 | JZLSH | varchar(64) | 非空 | ● | 就诊流水号 |
| 5 | MZZYBZ | varchar(2) | 非空 |  | 1：门急诊；2：住院；6：体检 门诊/住院标志 |
| 6 | KH | varchar(32) | 非空 |  | 卡号 |
| 7 | KLX | varchar(16) | 非空 |  | 卡类型 |
| 8 | HZXM | varchar(64) | 非空 |  | 患者姓名 |
| 9 | BRXB | varchar(1) | 非空 | ○ | 病人性别 |
| 10 | BRNL | decimal(5,2) | 非空 | ○ | 年龄 |
| 11 | SQYHRYID | varchar(32) | 非空 |  | 申请医护人员ID |
| 12 | SQYHRYXM | varchar(64) | 非空 |  | 申请 医护人员姓名 |
| 13 | BGYHRYID | varchar(32) | 非空 |  | 报告医护人员ID |
| 14 | BGYHRYXM | varchar(64) | 非空 | ○ | 报告医护人员姓名 |
| 15 | SHYHRYID | varchar(32) | 非空 |  | 审核医护人员ID |
| 16 | SHYHRYXM | varchar(64) | 非空 | ○ | 审核医护人员姓名 |
| 17 | SQKS | varchar(32) | 非空 | ○ | 申请科室编码 |
| 18 | BQ | varchar(32) |  |  | 病区 |
| 19 | CH | varchar(20) |  |  | 床号 |
| 20 | BGSJ | datetime | 非空 | ● | 报告时间 YYYY-MM-DD HH:MM:SS |
| 21 | DYSJ | datetime |  |  | 打印时间 YYYY-MM-DD HH:MM:SS |
| 22 | SQSJ | datetime | 非空 |  | 申请时间 YYYY-MM-DD HH:MM:SS |
| 23 | CJSJ | datetime | 非空 |  | 采集时间 YYYY-MM-DD HH:MM:SS |
| 24 | JYSJ | datetime | 非空 |  | 检验时间 YYYY-MM-DD HH:MM:SS |
| 25 | SHSJ | datetime | 非空 |  | 审核时间 YYYY-MM-DD HH:MM:SS |
| 26 | YYSJ | datetime | 非空 |  | 预约时间 YYYY-MM-DD HH:MM:SS 若无，则填“-” |
| 27 | BGBZ | varchar(1024) | 非空 |  | 报告备注 没有报告备注填报‘-’ |
| 28 | BBDM | varchar(4) | 非空 |  | 标本代码 |
| 29 | BBMC | varchar(64) | 非空 | ● | 标本名称 |
| 30 | BGDLBBM | varchar(4) | 非空 |  | 报告单类别编码 编码。1：一般临床检验； 2：血液学检查；3：临床 化学检查；4：临床免疫学 检查；5：临床微生物学检 查；6：临床寄生虫学检查； 7：分子生物学检查；9999： 其它 |
| 31 | BGDLB | varchar(256) | 非空 | ● | 报告单类别名称 填写中文。如“血常规”、 “尿常规”等行业常识的 名称 |
| 32 | WJLJ | varchar(8000) | 非空 |  | 文件链接 |
| 33 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 34 | YLYL1 | varchar(128) |  |  | 预留一 |
| 35 | YLYL2 | varchar(128) |  |  | 预留二 |
| 36 | YLYL3 | varchar(128) |  |  | 预留三 |
| 37 | YLYL4 | varchar(128) |  |  | 预留四 |
| 38 | YLYL5 | varchar(128) |  |  | 预留五 |
| 39 | YLYL6 | varchar(128) |  |  | 预留六 |

### 8.2 TB_LIS_INDICATORS — 检验指标结果 【一个指标一行】

主键: `YLJGYQDM + JYZBLSH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | JYZBLSH | varchar(64) | 非空 | ● | 检验指标流水号 |
| 3 | BGDH | varchar(64) | 非空 | ● | 检验报告单号 |
| 4 | BGRQ | varchar(8) | 非空 | ● | 报告日期 YYYYMMDD |
| 5 | SHSJ | datetime | 非空 |  | 审核时间 YYYY-MM-DD HH:MM:SS |
| 6 | MXXMBM | varchar(32) | 非空 |  | 项目明细编码(院内) |
| 7 | MXXMBMYB | varchar(64) | 非空 |  | 项目明细编码(医保) |
| 8 | MXXMMC | varchar(1024) | 非空 |  | 明细项目名称 |
| 9 | JYZBDM | varchar(32) | 非空 | ○ | 检验指标代码  医院可按实际情况填写，可 填写通用的英文简称（如： “红细胞计数”的英文缩写 为 RBC） |
| 10 | JYFF | varchar(32) | 非空 |  | 检验方法 如：“化学法” |
| 11 | JYZBMC | varchar(100) | 非空 | ● | 检验指标名称 如：“红细胞压积” |
| 12 | JYZBJG | varchar(128) | 非空 | ● | 检验指标结果 量化结果或定性结果；例 如：“阴性”或“+”，以 及描述性文字 |
| 13 | SBBM | varchar(64) | 非空 |  | 设备编码 |
| 14 | YQBH | varchar(20) | 非空 |  | 仪器编号 |
| 15 | YQMC | varchar(100) | 非空 |  | 仪器名称 |
| 16 | CKZ | varchar(128) | 非空 | ○ | 参考值 |
| 17 | JLDW | varchar(20) | 非空 | ○ | 计量单位 |
| 18 | YCTS | varchar(3) | 非空 | ● | 异常提示 编码。1：正常；2：无法识 别的异常；3：异常偏高；4： 异常偏低 |
| 19 | DYXH | int | 非空 |  | 打印序号 |
| 20 | JCYHRYID | varchar(32) | 非空 |  | 检测医护人员 ID |
| 21 | JCYHRYXM | varchar(64) | 非空 |  | 检测医护人员姓名 |
| 22 | SHYHRYID | varchar(32) | 非空 |  | 审核医护人员 ID |
| 23 | SHYHRYXM | varchar(64) | 非空 |  | 审核医护人员姓名 |
| 24 | YZID | varchar(32) | 非空 |  | 相关医嘱 ID/处方项目明细编号 |
| 25 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 26 | YLYL1 | varchar(128) |  |  | 预留一 |
| 27 | YLYL2 | varchar(128) |  |  | 预留二 |
| 28 | YLYL3 | varchar(128) |  |  | 预留三 |
| 29 | YLYL4 | varchar(128) |  |  | 预留四 |
| 30 | YLYL5 | varchar(128) |  |  | 预留五 |
| 31 | YLYL6 | varchar(128) |  |  | 预留六 |

## 九、检查 (RIS)

### 9.1 TB_RIS_REPORT — 检查报告 (放射/核医学) 【一份报告一行】

主键: `YLJGYQDM + INSTANCEUID`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | INSTANCEUID | varchar(128) | 非空 | ● | 报告流水号 复合主键；唯一标识了一份 检查报告 |
| 3 | STUDYUID | varchar(512) | 非空 | ○ | 检查实例唯一号 复合主键；对应检查的 Study Instance UID |
| 4 | JZLSH | varchar(64) | 非空 | ● | 就诊流水号 用于与门诊就诊记录表、住 院就诊记录表关联 |
| 5 | MZZYBZ | varchar(2) | 非空 |  | 门诊/住院标志 1：门急诊；2：住院；6：体检 |
| 6 | KH | varchar(32) | 非空 |  | 卡号 |
| 7 | KLX | varchar(16) | 非空 |  | 卡类型 |
| 8 | HZXM | varchar(64) | 非空 |  | 患者姓名 |
| 9 | BRXB | varchar(1) | 非空 |  | 病人性别 |
| 10 | PATIENTID | varchar(64) | 非空 | ○ | 影像号 |
| 11 | SQDH | varchar(64) |  |  | 申请单号  该检查在 HIS 或 RIS 中的申   请单编号 |
| 12 | KDSJ | datetime | 非空 |  | YYYY-MM-DD HH:MM:SS 开单时间 |
| 13 | JCSJ | datetime | 非空 | ● | YYYY-MM-DD HH:MM:SS 检查时间 |
| 14 | BGSJ | datetime | 非空 | ● | YYYY-MM-DD HH:MM:SS 报告时间 |
| 15 | SHSJ | datetime | 非空 |  | YYYY-MM-DD HH:MM:SS 审核时间 |
| 16 | YYSJ | datetime | 非空 |  | YYYY-MM-DD HH:MM:SS  若无，则填“-”  日期时间 |
| 17 | EXAMTYPE | varchar(16) | 非空 | ● | 检查类型 |
| 18 | SBBM | varchar(64) | 非空 |  | 检查设备仪器型号 |
| 19 | YQBM | varchar(64) | 非空 |  | 检查仪器号 |
| 20 | SQKS | varchar(32) | 非空 |  | 申请科室编码 |
| 21 | SQYHRYID | varchar(32) | 非空 |  | 申请医护人员ID |
| 22 | SQYHRYXM | varchar(64) | 非空 |  | 申请医护人员姓名 |
| 23 | JCKS | varchar(32) | 非空 | ○ | 检查科室编码 |
| 24 | JCYHRYXM | varchar(64) | 非空 |  | 检查医护人员姓名 |
| 25 | JCYHRYID | varchar(32) | 非空 |  | 检查医护人员ID |
| 26 | BGRQ | varchar(8) | 非空 |  | YYYYMMDD 报告日期 |
| 27 | BGYHRYID | varchar(32) | 非空 |  | 报告医护人员ID |
| 28 | BGYHRYXM | varchar(64) | 非空 |  | 报告医护人员姓名 |
| 29 | SHYHRYID | varchar(32) | 非空 |  | 审核医护人员ID |
| 30 | SHYHRYXM | varchar(64) | 非空 |  | 审核医护人员姓名 |
| 31 | JCBW | varchar(256) | 非空 | ○ | 检查部位 |
| 32 | JCFF | varchar(512) | 非空 |  | 检查方法 |
| 33 | BWACR | varchar(256) | 非空 |  | 检查部位 ACR 编码 |
| 34 | JCMC | varchar(1024) | 非空 | ● | 检查名称 |
| 35 | YYS | varchar(1) | 非空 | ○ | 阴阳性 |
| 36 | BGLCZD | varchar(2048) | 非空 | ○ | 报告临床诊断 |
| 37 | YXBX | varchar(4000) | 非空 | ● | 影像表现 |
| 38 | YXZD | varchar(2048) | 非空 | ● | 影像诊断 |
| 39 | BZHJY | varchar(1024) | 非空 |  | 备注或建议 |
| 40 | SFYYY | varchar(1) | 非空 | ○ | 是否有影像 1：有；2：无；3：未定 |
| 41 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 42 | YLYL1 | varchar(128) |  |  | 预留一 |
| 43 | YLYL2 | varchar(128) |  |  | 预留二 |
| 44 | YLYL3 | varchar(128) |  |  | 预留三 |
| 45 | YLYL4 | varchar(128) |  |  | 预留四 |
| 46 | YLYL5 | varchar(128) |  |  | 预留五 |
| 47 | YLYL6 | varchar(128) |  |  | 预留六 |

### 9.2 TB_RIS_REPORT2 — 检查报告 (超声/病理/心电/内镜等) 【一份报告一行】

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | INSTANCEUID | varchar(128) | 非空 | ● | 报告流水号 |
| 3 | STUDYUID | varchar(512) | 非空 |  | 检查实例唯一号 |
| 4 | JZLSH | varchar(64) | 非空 | ● | 就诊流水号 |
| 5 | MZZYBZ | varchar(2) | 非空 |  | 门 诊 / 住 院 标志 1：门急诊；2：住院；6：体检 |
| 6 | KH | varchar(32) | 非空 |  | 卡号 |
| 7 | KLX | varchar(16) | 非空 |  | 卡类型 |
| 8 | HZXM | varchar(64) | 非空 |  | 患者姓名 |
| 9 | BRXB | varchar(1) | 非空 |  | 病人性别 |
| 10 | PATIENTID | varchar(64) | 非空 |  | 影像号 被检查的患者在医院内部 的影像号码，即影像图像 DICOM 文件中对应 Dicom 中 位置(0010,0020)的值 |
| 11 | SQDH | varchar(64) | 非空 |  | 申请单号 该检查在 HIS 或 RIS 中的申    请单编号 |
| 12 | KDSJ | datetime | 非空 |  | YYYY-MM-DD HH:MM:SS 开单时间 |
| 13 | JCSJ | datetime | 非空 | ● | YYYY-MM-DD HH:MM:SS 检查时间 |
| 14 | BGSJ | datetime | 非空 | ● | YYYY-MM-DD HH:MM:SS 报告时间 |
| 15 | SHSJ | datetime | 非空 |  | YYYY-MM-DD HH:MM:SS 审核时间 |
| 16 | YYSJ | datetime | 非空 |  | YYYY-MM-DD HH:MM:SS 预约时间 若无，则填“-” |
| 17 | EXAMTYPE | varchar(16) | 非空 | ● | 检查类型 |
| 18 | SBBM | varchar(64) | 非空 |  | 检查设备仪器型号 |
| 19 | YQBM | varchar(64) | 非空 |  | 检查仪器号 |
| 20 | SQKS | varchar(32) | 非空 |  | 申请科室编码 |
| 21 | SQYHRYID | varchar(32) | 非空 |  | 申请医护人员ID |
| 22 | SQYHRYXM | varchar(64) | 非空 |  | 申请医护人员姓名 |
| 23 | JCKS | varchar(32) | 非空 | ○ | 检查科室编码 |
| 24 | JCYHRYXM | varchar(64) | 非空 |  | 检查医护人员姓名 |
| 25 | JCYHRYID | varchar(32) | 非空 |  | 检查医护人员ID |
| 26 | BGRQ | varchar(8) | 非空 |  | 报告日期 YYYYMMDD |
| 27 | BGYHRYID | varchar(32) | 非空 |  | 报告医护人员ID |
| 28 | BGYHRYXM | varchar(64) | 非空 |  | 报告医护人员姓名 |
| 29 | SHYHRYID | varchar(32) | 非空 |  | 审核医护人员ID |
| 30 | SHYHRYXM | varchar(64) | 非空 |  | 审核医护人员姓名 |
| 31 | JCBW | varchar(256) | 非空 | ○ | 检查部位 |
| 32 | JCFF | varchar(512) | 非空 |  | 检查方法 |
| 33 | BWACR | varchar(256) | 非空 |  | 检查部位 ACR 编码 |
| 34 | JCMC | varchar(1024) | 非空 | ● | 检查名称 |
| 35 | JCJGDM | varchar(1) | 非空 | ○ | 检查结果代码 1：正常；2：异常；3：不 确定 |
| 36 | JCBGJG | varchar(1024) | 非空 | ● | 检查报告结果 |
| 37 | JCBGBZ | varchar(1024) | 非空 |  | 检查报告备注 |
| 38 | SFYYY | varchar(1) | 非空 |  | 是否有影像 1：有；2：无；3：未定 |
| 39 | BT1BM | varchar(4) |  |  | 标题一编码 |
| 40 | BT1MC | varchar(32) |  |  | 标题一名称 |
| 41 | BT1NR | varchar(4000) |  | ● | 标题一内容 |
| 42 | BT2BM | varchar(4) |  |  | 标题二编码 |
| 43 | BT2MC | varchar(32) |  |  | 标题二名称 |
| 44 | BT2NR | varchar(4000) |  | ● | 标题二内容 |
| 45 | BT3BM | varchar(4) |  |  | 标题三编码 |
| 46 | BT3MC | varchar(32) |  |  | 标题三名称 |
| 47 | BT3NR | varchar(4000) |  |  | 标题三内容 |
| 48 | BT4BM | varchar(4) |  |  | 标题四编码 |
| 49 | BT4MC | varchar(32) |  |  | 标题四名称 |
| 50 | BT4NR | varchar(4000) |  |  | 标题四内容 |
| 51 | BT5BM | varchar(4) |  |  | 标题五编码 |
| 52 | BT5MC | varchar(32) |  |  | 标题五名称 |
| 53 | BT5NR | varchar(4000) |  |  | 标题五内容 |
| 54 | BT6BM | varchar(4) |  |  | 标题六编码 |
| 55 | BT6MC | varchar(32) |  |  | 标题六名称 |
| 56 | BT6NR | varchar(4000) |  |  | 标题六内容 |
| 57 | BT7BM | varchar(4) |  |  | 标题七编码 |
| 58 | BT7MC | varchar(32) |  |  | 标题七名称 |
| 59 | BT7NR | varchar(4000) |  |  | 标题七内容 |
| 60 | BT8BM | varchar(4) |  |  | 标题八编码 |
| 61 | BT8MC | varchar(32) |  |  | 标题八名称 |
| 62 | BT8NR | varchar(4000) |  |  | 标题八内容 |
| 63 | BT9BM | varchar(4) |  |  | 标题九编码 |
| 64 | BT9MC | varchar(32) |  |  | 标题九名称 |
| 65 | BT9NR | varchar(4000) |  |  | 标题九内容 |
| 66 | BT10BM | varchar(4) |  |  | 标题十编码 |
| 67 | BT10MC | varchar(32) |  |  | 标题十名称 |
| 68 | BT10NR | varchar(4000) |  |  | 标题十内容 |
| 69 | BT11BM | varchar(4) |  |  | 标题十一编码 |
| 70 | BT11MC | varchar(32) |  |  | 标题十一名称 |
| 71 | BT11NR | varchar(4000) |  |  | 标题十一内容 |
| 72 | BT12BM | varchar(4) |  |  | 标题十二编码 |
| 73 | BT12MC | varchar(32) |  |  | 标题十二名称 |
| 74 | BT1NR2 | varchar(4000) |  |  | 标题十二内容 |
| 75 | BT13BM | varchar(4) |  |  | 标题十三编码 |
| 76 | BT13MC | varchar(32) |  |  | 标题十三名称 |
| 77 | BT13NR | varchar(4000) |  |  | 标题十三内容 |
| 78 | BT14BM | varchar(4) |  |  | 标题十四编码 |
| 79 | BT14MC | varchar(32) |  |  | 标题十四名称 |
| 80 | BT14NR | varchar(4000) |  |  | 标题十四内容 |
| 81 | BT15BM | varchar(4) |  |  | 标题十五编码 |
| 82 | BT15MC | varchar(32) |  |  | 标题十五名称 |
| 83 | BT15NR | varchar(4000) |  |  | 标题十五内容 |
| 84 | XGBZ | varchar(1) | 非空 |  | 修改标志 编码。1：正常；2：撤销 |
| 85 | YLYL1 | varchar(128) |  |  | 预留一 |
| 86 | YLYL2 | varchar(128) |  |  | 预留二 |
| 87 | YLYL3 | varchar(128) |  |  | 预留三 |
| 88 | YLYL4 | varchar(128) |  |  | 预留四 |
| 89 | YLYL5 | varchar(128) |  |  | 预留五 |
| 90 | YLYL6 | varchar(128) |  |  | 预留六 |

## 十、病案首页

### 10.1 TB_BA_SYJBK — 病案首页 【一次住院一行 (233 列)】

主键: `YLJGYQDM + SYXH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | SYXH | varchar(50) | 非空 | ● | (唯一标示符，与 TB_BA_SYYEK，TB_BA_SYSSK，TB_BA_SYZDK 三张表的 SYXH 一致) |
| 3 | HISSYXH | varchar(64) |  |  | 就诊流水号 |
| 4 | YLZZJGDM | varchar(32) | 非空 |  | 医疗组织机构代码 |
| 5 | YLJGMC | varchar(80) | 非空 |  | 医疗机构名称 |
| 6 | BRXZ | varchar(4) |  |  | 病人性质 |
| 7 | RYCS | int |  |  | 入院次数 |
| 8 | JGLB | varchar(6) |  |  | 机构类别 |
| 9 | BAH | varchar(32) | 非空 | ● | 病案号 |
| 10 | JKKH | varchar(64) |  |  | 健康卡号 |
| 11 | BRXM | varchar(64) | 非空 |  | 病人姓名 |
| 12 | BRXB | varchar(4) |  | ● | 病人性别 |
| 13 | CSNY | varchar(8) |  |  | 出生年月日 |
| 14 | XSNL | decimal(5,2) |  | ● | 显示年龄 |
| 15 | GJDM | varchar(4) |  |  | 国籍代码 |
| 16 | NL_BABY | decimal(5,2) |  |  | 不足 1 周岁年龄 |
| 17 | XSECSTZ | varchar(64) |  |  | 新生儿出生体重 |
| 18 | XSERYTZ | varchar(64) |  |  | 新生儿入院体重 |
| 19 | SFDM | varchar(64) |  |  | 出生地 |
| 20 | JGSSDM | varchar(64) |  |  | 籍贯地址 |
| 21 | MZDM | varchar(2) |  |  | 民族代码 |
| 22 | SFZLBDM | varchar(4) |  |  | 身份证类别代码 |
| 23 | SFZH | varchar(20) |  |  | 身份证号 |
| 24 | ZYDM | varchar(4) |  |  | 职业代码 |
| 25 | HYZK | varchar(2) |  |  | 婚姻状况 |
| 26 | XZZ | varchar(128) |  |  | 现住址 |
| 27 | XZZDH | varchar(16) |  |  | 现住址电话 |
| 28 | XZZYB | varchar(16) |  |  | 现住址邮编 |
| 29 | HKDZ | varchar(128) |  |  | 户口地址 |
| 30 | HKDH | varchar(16) |  |  | 户口电话 |
| 31 | HKYB | varchar(16) |  |  | 户口邮编 |
| 32 | GZDW | varchar(64) |  |  | 单位名称 |
| 33 | DWDZ | varchar(128) |  |  | 单位地址 |
| 34 | DWDH | varchar(16) |  |  | 单位电话 |
| 35 | DWYB | varchar(16) |  |  | 单位邮编 |
| 36 | LXRM | varchar(64) |  |  | 联系人名 |
| 37 | LXGX | varchar(4) |  |  | 联系关系 |
| 38 | LXRDZ | varchar(128) |  |  | 联系人地址 |
| 39 | LXDH | varchar(16) |  |  | 联系电话 |
| 40 | RYTJ | varchar(4) |  |  | 入院途径 |
| 41 | ZLLB | varchar(4) |  |  | 治疗类别 |
| 42 | RYRQ | varchar(16) | 非空 | ○ | 格式类似于:2012121200:00:01 |
| 43 | QZRQ | varchar(8) |  |  | 格式类似于：20121212 |
| 44 | RYKS | varchar(8) |  | ● | 入院科室 |
| 45 | RYBQDM | varchar(8) |  |  | 入院病区代码 |
| 46 | RYBQMC | varchar(40) |  |  | 入院病区名称 |
| 47 | ZKKS | varchar(256) |  |  | 转科科室 |
| 48 | CYRQ | varchar(16) | 非空 | ○ | 格式类似于:2012121200:00:01 |
| 49 | CYKS | varchar(8) |  | ● | 出院科室 |
| 50 | CYBQDM | varchar(8) |  |  | 出院病区代码 |
| 51 | CYBQMC | varchar(40) |  |  | 出院病区名称 |
| 52 | ZYTS | decimal(6,1) | 非空 | ● | 住院天数 |
| 53 | MZZD_ZY | varchar(20) |  |  | 中医门(急)诊诊断 |
| 54 | MJZZDMC | varchar(256) |  |  | 门诊诊断名称 |
| 55 | MZZDBM | varchar(256) |  |  | 门诊诊断编码 |
| 56 | SSLCLJ | varchar(4) |  |  | 实施临床路径 |
| 57 | ZZZYZJ | varchar(4) |  |  | 自制中医制剂 |
| 58 | ZYZLSB | varchar(4) |  |  | 使用中医诊疗设备 |
| 59 | ZYZLJS | varchar(4) |  |  | 使用中医诊疗技术 |
| 60 | BZSH | varchar(4) |  |  | 辨证施护 |
| 61 | ZYZD_ZY | varchar(20) |  |  | 中医主病编码 |
| 62 | ZYZZ_ZY | varchar(21) |  |  | 中医主证编码 |
| 63 | ZYRYBQ | varchar(4) |  |  | 中医入院病情 |
| 64 | ZGQK_ZY | varchar(4) |  |  | 中医出院情况 |
| 65 | ZYZD | varchar(256) |  | ● | 主要诊断疾病编码 |
| 66 | ZLBM | varchar(20) |  |  | 肿瘤编码 |
| 67 | ZLMC | varchar(256) |  |  | 肿瘤名称 |
| 68 | ZLJG | varchar(4) |  |  | 主诊断治疗结果 |
| 69 | RYSBQ | varchar(4) |  |  | 入院时病情 |
| 70 | SSWYMC | varchar(256) |  |  | 损伤中毒外部原因 |
| 71 | SSZD | varchar(20) |  |  | 损伤中毒 |
| 72 | BLZD | varchar(256) |  | ○ | 病理诊断 |
| 73 | BLZDMC | varchar(256) |  | ○ | 病理诊断名称 |
| 74 | BLBH | varchar(12) |  |  | 病理编号 |
| 75 | BLZD_1 | varchar(256) |  |  | 病理诊断 1 |
| 76 | BLZDMC_1 | varchar(256) |  |  | 病理诊断名称1 |
| 77 | BLBH_1 | varchar(12) |  |  | 病理编号 1 |
| 78 | BLZD_2 | varchar(256) |  |  | 病理诊断 2 |
| 79 | BLZDMC_2 | varchar(256) |  |  | 病理诊断名称2 |
| 80 | BLBH_2 | varchar(12) |  |  | 病理编号 2 |
| 81 | GMYW | varchar(64) |  |  | 过敏药物 |
| 82 | YWGM | varchar(4) |  |  | 药物过敏 |
| 83 | SJQK | varchar(4) |  |  | 尸检情况 |
| 84 | QJCS | varchar(15) |  |  | 抢救次数 |
| 85 | CGCS | varchar(15) |  |  | 成功次数 |
| 86 | SXJL1 | varchar(3) |  |  | 输血记录 1 |
| 87 | SXJL2 | varchar(3) |  |  | 输血记录 2 |
| 88 | SXJL3 | varchar(3) |  |  | 输血记录 3 |
| 89 | SXJL4 | varchar(3) |  |  | 输血记录 4 |
| 90 | SXJL5 | varchar(3) |  |  | 输血记录 5 |
| 91 | SXJL6 | varchar(3) |  |  | 输血记录 6 |
| 92 | SXL_ZLL1 | int |  |  | 输血量 1(治疗量) |
| 93 | SXL_ZLL2 | int |  |  | 输血量2(治疗量) |
| 94 | SXL_ZLL3 | int |  |  | 输血量3(治疗量) |
| 95 | SXL_ZLL4 | int |  |  | 输血量 4(治疗量) |
| 96 | SXL_ZLL5 | int |  |  | 输血量 5(治疗量) |
| 97 | SXL_ZLL6 | int |  |  | 输血量 6(治疗量) |
| 98 | SXL_U1 | decimal(12,1) |  |  | 输血量 1(U/) |
| 99 | SXL_U2 | decimal(12,1) |  |  | 输血量 2(U/) |
| 100 | SXL_U3 | decimal(12,1) |  |  | 输血量 3(U/) |
| 101 | SXL_U4 | decimal(12,1) |  |  | 输血量 4(U/) |
| 102 | SXL_U5 | decimal(12,1) |  |  | 输血量5(U/) |
| 103 | SXL_U6 | decimal(12,1) |  |  | 输血量 6(U/) |
| 104 | SXL_ML1 | int |  |  | 输血量 1(ml) |
| 105 | SXL_ML2 | int |  |  | 输血量 2(ml) |
| 106 | SXL_ML3 | int |  |  | 输血量 3(ml) |
| 107 | SXL_ML4 | int |  |  | 输血量 4(ml) |
| 108 | SXL_ML5 | int |  |  | 输血量 5(ml) |
| 109 | SXL_ML6 | int |  |  | 输血量 6(ml) |
| 110 | SXFA1 | varchar(1) |  |  | 输血方法 1 |
| 111 | SXFA2 | varchar(1) |  |  | 输血方法 2 |
| 112 | SXFA3 | varchar(1) |  |  | 输血方法 3 |
| 113 | SXFA4 | varchar(1) |  |  | 输血方法 4 |
| 114 | SXFA5 | varchar(1) |  |  | 输血方法 5 |
| 115 | SXFA6 | varchar(1) |  |  | 输血方法 6 |
| 116 | XXDM | varchar(4) |  |  | 血型代码 |
| 117 | RHJY | varchar(4) |  |  | Rh 检验 |
| 118 | HBsAg | varchar(10) |  |  | HBsAg |
| 119 | HCV_Ab | varchar(10) |  |  | HCV-Ab |
| 120 | HIV_Ab | varchar(10) |  |  | HIV-Ab |
| 121 | HXB | decimal(4,1) | 非空 |  | 红细胞 |
| 122 | XXB | decimal(4,1) | 非空 |  | 血小板 |
| 123 | XJ | decimal(4,1) | 非空 |  | 血浆 |
| 124 | QX | decimal(4,1) | 非空 |  | 全血 |
| 125 | ZTXHS | decimal(4,1) | 非空 |  | 自体血回输 |
| 126 | SXFY | varchar(4) |  |  | 输血反应 |
| 127 | RSMDSC | varchar(4) |  |  | 妊娠梅毒筛查 |
| 128 | CHCXSF | varchar(4) |  |  | 产后出血是否 |
| 129 | KZR | varchar(64) |  |  | 科主任 |
| 130 | KZRBM | varchar(30) | 非空 |  | 科主任编码 |
| 131 | ZYYS | varchar(64) |  | ○ | 住院医师 |
| 132 | ZYYSBM | varchar(30) | 非空 |  | 住院医师编码 |
| 133 | ZZYS | varchar(64) |  | ○ | 主治医师 |
| 134 | ZZYSBM | varchar(30) | 非空 |  | 主治医师编码 |
| 135 | ZRYS | varchar(64) |  | ○ | 主任医师 |
| 136 | Z_FZ_RYSBM | varchar(30) | 非空 |  | 主(副主)任医师编码 |
| 137 | Z_FZ_RYS | varchar(40) | 非空 |  | 主(副主)任医师 |
| 138 | ZRHS | varchar(64) |  |  | 责任护士 |
| 139 | ZRHSBM | varchar(30) |  |  | 责任护士编码 |
| 140 | JXYS | varchar(64) |  |  | 进修医师 |
| 141 | SXYS2 | varchar(64) |  |  | 实习医师 |
| 142 | BMY | varchar(64) |  |  | 编码员 |
| 143 | BAZL | varchar(4) |  |  | 病案质量 |
| 144 | ZKYS | varchar(64) |  |  | 质控医师 |
| 145 | ZKHS | varchar(64) |  |  | 质控护士 |
| 146 | BARQ | varchar(8) |  |  | 格式类似于 20121212 |
| 147 | LYFS | varchar(4) |  | ○ | 离院方式 |
| 148 | JSYLJG1 | varchar(256) |  |  | 拟接收医疗机构(医嘱转院) |
| 149 | JSYLJG2 | varchar(256) |  |  | 医嘱转社区卫生院/乡镇卫生院 |
| 150 | ZZYJH | varchar(4) |  |  | 是否有出 院31 天内再住院计划 |
| 151 | ZZYMD | varchar(256) |  |  | 再住院目的 |
| 152 | HMDAY1 | varchar(16) |  |  | 入院前昏迷天数 |
| 153 | HMHOUR1 | varchar(16) |  |  | 入院前昏迷小时数 |
| 154 | HMMIN1 | varchar(16) |  |  | 入院前昏迷分钟数 |
| 155 | HMDAY2 | varchar(16) |  |  | 入院后昏迷天数 |
| 156 | HMHOUR2 | varchar(16) |  |  | 入院后昏迷小时数 |
| 157 | HMMIN2 | varchar(16) |  |  | 入院后昏迷分钟数 |
| 158 | YCHXJSYSJ | int |  |  | 大于等于 0 的整数，单位(小时)，    指患者住院期间有创呼吸机累计    使用时间，全麻期间使用有创呼    吸机的时间除外，不足 1 小时按 1    小时计算 |
| 159 | ZZJHSMC1 | varchar(10) |  |  | 重症监护室名称 1 |
| 160 | JRSJ1 | datetime |  |  | 指进入重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss，进入时间    不能晚于退出时间 |
| 161 | TCSJ1 | datetime |  |  | 指退出重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss |
| 162 | ZZJHSMC2 | varchar(10) |  |  | 重症监护室名称 2 |
| 163 | JRSJ2 | datetime |  |  | 指进入重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss，进入时间    不能晚于退出时间 |
| 164 | TCSJ2 | datetime |  |  | 指退出重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss |
| 165 | ZZJHSMC3 | varchar(10) |  |  | 重症监护室名称 3 |
| 166 | JRSJ3 | datetime |  |  | 指进入重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss，进入时间    不能晚于退出时间 |
| 167 | TCSJ3 | datetime |  |  | 指退出重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss |
| 168 | ZZJHSMC4 | varchar(10) |  |  | 重症监护室名称 4 |
| 169 | JRSJ4 | datetime |  |  | 指进入重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss，进入时间    不能晚于退出时间 |
| 170 | TCSJ4 | datetime |  |  | 指退出重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss |
| 171 | ZZJHSMC5 | varchar(10) |  |  | 重症监护室名称 5 |
| 172 | JRSJ5 | datetime |  |  | 指进入重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss，进入时间    不能晚于退出时间 |
| 173 | TCSJ5 | datetime |  |  | 指退出重症监护室的时间，格式    yyyy-MM-dd HH:mm:ss |
| 174 | ZFY | decimal(15,3) |  | ● | 住院总费用 |
| 175 | ZFJE | decimal(15,3) |  |  | 自付金额 |
| 176 | ZFEIJE | decimal(15,3) |  |  | 自费金额 |
| 177 | QTZF | decimal(15,3) |  |  | 其他支付 |
| 178 | YBYLFWF | decimal(15,3) |  | ○ | 一般医疗服务费 |
| 179 | ZYBZLZF | decimal(15,3) |  | ○ | 中医辨证论治费 |
| 180 | ZYBZLZHZF | decimal(15,3) |  | ○ | 中医辨证论治会诊费 |
| 181 | YBZLCZF | decimal(15,3) |  | ○ | 一般治疗操作费 |
| 182 | HLF | decimal(15,3) |  | ○ | 护理费 |
| 183 | QTF1 | decimal(15,3) |  | ○ | 其他费用 |
| 184 | BLZDF | decimal(15,3) |  | ○ | 病理诊断费 |
| 185 | SYSZDF | decimal(15,3) |  | ○ | 实验室诊断费 |
| 186 | YXXZDF | decimal(15,3) |  | ○ | 影像学诊断费 |
| 187 | LCZDXMF | decimal(15,3) |  | ○ | 临床诊断项目费 |
| 188 | FSSZLXMF | decimal(15,3) |  | ○ | 非手术治疗项目费用 |
| 189 | LCWLZLF | decimal(15,3) |  | ○ | 临床物理治疗费 |
| 190 | SSZLF | decimal(15,3) |  | ○ | 手术治疗费 |
| 191 | SSF | decimal(15,3) |  | ○ | 手术费 |
| 192 | MZF | decimal(15,3) |  | ○ | 麻醉费 |
| 193 | KFF | decimal(15,3) |  | ○ | 康复费 |
| 194 | ZYZDF | decimal(15,3) |  | ○ | 中医诊断费 |
| 195 | ZYZLF | decimal(15,3) |  | ○ | 中医治疗费 |
| 196 | ZYWZF | decimal(15,3) |  | ○ | 中医外治费 |
| 197 | ZYGSF | decimal(15,3) |  | ○ | 中医骨伤费 |
| 198 | ZCYJF | decimal(15,3) |  |  | 针刺与灸法 |
| 199 | ZYTNZL | decimal(15,3) |  |  | 中医推拿治疗 |
| 200 | ZYGCZL | decimal(15,3) |  |  | 中医肛肠治疗 |
| 201 | ZYTSZL | decimal(15,3) |  |  | 中医特殊治疗 |
| 202 | ZYQTF | decimal(15,3) |  | ○ | 中医其他费用 |
| 203 | ZYTSTPJG | decimal(15,3) |  |  | 中药特殊调配加工 |
| 204 | BZSS | decimal(15,3) |  |  | 辨证施膳 |
| 205 | XYF | decimal(15,3) |  | ○ | 西药费 |
| 206 | KJYWF | decimal(15,3) |  | ○ | 抗菌药物费用 |
| 207 | CHENGYF | decimal(15,3) |  | ○ | 中成药费 |
| 208 | ZYZJF | decimal(15,3) |  | ○ | 医疗机构中药制剂费 |
| 209 | CAOYF | decimal(15,3) |  | ○ | 中草药费 |
| 210 | SXF | decimal(15,3) |  | ○ | 血费 |
| 211 | BDBLZPF | decimal(15,3) |  | ○ | 白蛋白类制品费 |
| 212 | QDBLZPF | decimal(15,3) |  | ○ | 球蛋白类制品费 |
| 213 | NXYZLZPF | decimal(15,3) |  | ○ | 凝血因子类制品费 |
| 214 | XBYZLZPF | decimal(15,3) |  | ○ | 细胞因子类制品费 |
| 215 | JCYYCXCLF | decimal(15,3) |  | ○ | 检查用一次性医用材料费 |
| 216 | ZLYYCXCLF | decimal(15,3) |  | ○ | 治疗用一次性医用材料费 |
| 217 | SSYYCXCLF | decimal(15,3) |  | ○ | 手术用一次性医用材料费 |
| 218 | QTF2 | decimal(15,3) |  | ○ | 其他费 |
| 219 | KLX | varchar(16) |  |  | 卡类型 |
| 220 | KH | varchar(32) |  |  | 卡号 |
| 221 | BRZSY | varchar(36) |  |  | 病人主索引号 |
| 222 | JLZT | varchar(2) | 非空 |  | 插入时值为 0，平台处理后更新成1 |
| 223 | GXRQ | varchar(16) | 非空 |  | 更 新 记 录 时 间 。 格 式    [2012121200:00:01] |
| 224 | FYDM | varchar(2) | 非空 |  | 院区代码 |
| 225 | FYMC | varchar(120) |  |  | 院区名称 |
| 226 | BASYXH | int |  |  | 病案序号 |
| 227 | SCSJ | varchar(16) | 非空 |  | yyyymmddhh:mm:ss |
| 228 | GDRQ | datetime | 非空 |  | 格式 YYYY-MM-DD HH:MM:SS |
| 229 | GDBBH | varchar(16) |  |  | 归档版本号 |
| 230 | TJHLTS | decimal(15,3) |  |  | 特级护理天数 |
| 231 | YJHLTS | decimal(15,3) |  |  | 一级护理天数 |
| 232 | EJHLTS | decimal(15,3) |  |  | 二级护理天数 |
| 233 | SJHLTS | decimal(15,3) |  |  | 三级护理天数 |

### 10.2 TB_BA_SYZDK — 病案首页其他诊断 【一个诊断一行】

主键: `YLJGYQDM + SYXH + ZDXH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | SYXH | varchar(50) | 非空 | ● | 首页序号 （唯一标示符，与 TB_BA_SYJBK 中的 SYXH 一致） |
| 3 | ZDXH | varchar(3) | 非空 | ● | 诊断序号 其他诊断的排序序号，类似于 1,2,3 排列 |
| 4 | YLZZJGDM | varchar(32) | 非空 |  | 医疗组织机构代码 组织机构代码目前按照 WS218-2002 卫生机构（组织）分 类与代码标准填写，代码由 8 位 本体代码、连字符和 1 位检验码 组成 |
| 5 | ZDDM | varchar(20) | 非空 | ● | 诊断代码 西医：疾病分类与代码国家临床 版。中医填写格式：中医主诊断 (证型+……+证型+证型,治法)。 注意“(”“,”均为半角字符 |
| 6 | ZDMC | varchar(256) | 非空 | ● | 诊断名称 |
| 7 | ZLDM | varchar(20) |  |  | 肿瘤代码 |
| 8 | ZLMC | varchar(256) |  |  | 肿瘤名称 |
| 9 | RYBQ | varchar(1) |  | ○ | 入院病情 |
| 10 | ZGQK | varchar(4) | 非空 | ○ | 出院情况 |
| 11 | FYDM | varchar(2) | 非空 |  | 院区代码 |
| 12 | GXRQ | varchar(16) | 非空 |  | 更新记录时间。格式[2012121200:00:01] |
| 13 | BASYXH | int |  |  | 默认 null |
| 14 | GDRQ | datetime | 非空 |  | 归档日期 格式 YYYY-MM-DD HH:MM:SS |
| 15 | GDBBH | varchar(16) | 非空 |  | 归档版本号 |

### 10.3 TB_BA_SYSSK — 病案首页手术 【一台手术一行】

主键: `YLJGYQDM + SYXH + SSXH`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医疗机构院区代码 |
| 2 | SYXH | varchar(50) | 非空 | ● | 首页序号 |
| 3 | SSXH | varchar(6) | 非空 | ● | 手术序号 |
| 4 | YLZZJGDM | varchar(32) | 非空 |  | 医 疗 组 织机构代码 |
| 5 | SSRQ | varchar(16) | 非空 | ○ | 格式类似于:2012121200:00:01 |
| 6 | SSDM | varchar(20) | 非空 | ● | 手术代码 |
| 7 | SSJB | varchar(1) |  | ○ | 手术级别 |
| 8 | SSMC | varchar(64) |  | ● | 手术名称 |
| 9 | SSMZFJ | varchar(10) |  |  | 手术麻 醉分级 |
| 10 | SSYS | varchar(64) |  | ○ | 主刀医生 |
| 11 | SSYZ | varchar(64) |  |  | 手术一助 |
| 12 | SSEZ | varchar(64) |  |  | 手术二助 |
| 13 | YHLB | varchar(4) |  |  | 切口愈合等级 |
| 14 | MZFS | varchar(4) |  | ○ | 麻醉方式 |
| 15 | MZYS | varchar(64) |  | ○ | 麻醉医生 |
| 16 | MZKSSJ | datetime |  | ○ | 格式 YYYY-MM-DD HH:MM:SS |
| 17 | MZJSSJ | datetime |  | ○ | 格式 YYYY-MM-DD HH:MM:SS |
| 18 | FYDM | varchar(2) |  |  | 院区代码 |
| 19 | GXRQ | varchar(16) |  |  | 更 新 记 录 时 间 。 格 式    [2012121200:00:01] |
| 20 | BASYXH | int |  |  | 病案序号 默认 null |
| 21 | SSCXSJ | decimal(7,2) |  |  | 单位(小时) |
| 22 | SFZYSS | varchar(1) |  | ● | 是 否 主 要手术1：是；0：否 |
| 23 | SFJHSS | varchar(1) |  |  | 是否“非计划 再 次 手术” 1：是；0：否 |
| 24 | GDRQ | datetime | 非空 |  | 归档日期 格式 YYYY-MM-DD HH:MM:SS |
| 25 | GDBBH | varchar(16) | 非空 |  | 归档版本号 |

## 十一、医保结算清单

### 11.1 TB_YB_JLC_CBRZDXX — 医保结算清单-诊断信息 【一个诊断一行】

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | XH | varchar(50) | 非空 | ● | 序号 主键，诊断唯一标志 |
| 2 | LSH | varchar(16) |  | ● | 流水号 就诊记录唯一标识 |
| 3 | ZDNO | varchar(60) |  | ● | 诊断代码 |
| 4 | ZDMC | varchar(100) |  | ● | 诊断名称 |
| 5 | XGBZ | varchar(1) |  |  | 修改标志 编码。1：正常；2：撤销 |

## 十二、基础字典

### 12.1 TB_DIC_DEPARTMENT — 科室字典 【一科室一行】

主键: `YLJGYQDM + YYKSDM`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(16) | 非空 | ● | 医 疗 机 构 院 区代码 |
| 2 | YYKSDM | varchar(32) | 非空 | ● | 科室代码 |
| 3 | YYKSMC | varchar(128) | 非空 | ● | 医院科室名称或病区科室名称的全称 |
| 4 | WSJDM | varchar(32) | 非空 | ○ | 该科室对应“医疗卫生机构业务科    室分类与代码”(卫统)标准的代    码 |
| 5 | YBDM | varchar(32) | 非空 | ○ | 医保局代码 |
| 6 | KSXZ | varchar(10) | 非空 | ○ | 科室性质 编码。01：临床；02：医技；03： 行政；04：专技；05：后保；06： 研究所；99：其他 |
| 7 | KSFLBZ | varchar(4) | 非空 |  | 分类标志 |
| 8 | KSJJ | varchar(500) | 非空 |  | 科室简介 科室名称简称，长度限于 10 个汉 字以内 |
| 9 | KSJC | varchar(20) | 非空 |  | 科室简称 |
| 10 | KSJB | varchar(1) | 非空 |  | 科室级别 |
| 11 | SJKE | varchar(20) | 非空 |  | 上级科室代码 |
| 12 | KSZT | varchar(1) | 非空 |  | 科室状态 |
| 13 | XGBZ | varchar(1) | 非空 |  | 修改标志 1：正常；2：撤销 |
| 14 | YLYL1 | varchar(128) |  |  | 预留一 |
| 15 | YLYL2 | varchar(128) |  |  | 预留二 |
| 16 | YLYL3 | varchar(128) |  |  | 预留三 |
| 17 | YLYL4 | varchar(128) |  |  | 预留四 |
| 18 | YLYL5 | varchar(128) |  |  | 预留五 |
| 19 | YLYL6 | varchar(128) |  |  | 预留六 |

### 12.2 TB_DIC_PRACTITIONER — 医护人员字典 【一人一行】

主键: `YLJGYQDM + YHRYID`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医 疗 机 构 院 区代码 |
| 2 | YHRYID | varchar(32) | 非空 | ● | 医院科室名称或病区科室名称的全称 ⚠国标源 DDL 注释错位, 实义: 医护人员 ID (院内唯一) |
| 3 | GH | varchar(16) | 非空 | ○ | 该科室对应“医疗卫生机构业务科    室分类与代码”(卫统)标准的代    码 ⚠国标源 DDL 注释错位, 实义: 工号 |
| 4 | CFYSH | varchar(32) | 非空 |  | 处方医生号(上海市) |
| 5 | YSDM | varchar(32) | 非空 | ○ | 医生编码(国家医保) |
| 6 | YSZYZSBM | varchar(27) | 非空 |  | 医生填写医师资格证书中的 15 位执 业证书代码，详见核发医师资格证 书的通知（卫医发［2000］447 号） 附件 6；军医填写 24（27）位资格 证书代码，详见核发医师资格证书 的通知（卫医发［2000］447 号）附 件 5 |
| 7 | RYZTBZ | varchar(2) | 非空 |  | 状态 1：在职；2：离职 |
| 8 | ZCM | varchar(16) | 非空 |  | 注册名称 |
| 9 | XM | varchar(64) | 非空 | ● | 姓名 |
| 10 | ZJLX | varchar(2) | 非空 |  | 证件类型 |
| 11 | ZJHM | varchar(32) | 非空 |  | 证件号码 |
| 12 | SSKS | varchar(32) | 非空 | ○ | 所属科室 |
| 13 | ZWDM | varchar(32) | 非空 |  | 职务代码 |
| 14 | ZHIW | varchar(32) | 非空 |  | 职务名称 |
| 15 | ZCDM | varchar(32) |  | ○ | 职称代码 |
| 16 | ZHIC | varchar(32) |  | ○ | 职称名称 |
| 17 | CSRQ | varchar(8) | 非空 |  | 出生日期 YYYYMMDD |
| 18 | LB | varchar(2) | 非空 | ○ | 人员类别 编码。01：医生；02：护士；03： 医技人员；04：行政人员；99：其 他 |
| 19 | ZHUANY | varchar(32) | 非空 |  | 专业 |
| 20 | YSJJ | varchar(500) | 非空 |  | 医生简介 |
| 21 | YSTC | varchar(200) | 非空 |  | 医生特长 |
| 22 | SFKZYY | varchar(1) | 非空 |  | 是否开展预约 0：否；1：是 |
| 23 | XGBZ | varchar(1) | 非空 |  | 1：正常；2：撤销 |
| 24 | YLYL1 | varchar(128) |  |  | 预留一 |
| 25 | YLYL2 | varchar(128) |  |  | 预留二 |
| 26 | YLYL3 | varchar(128) |  |  | 预留三 |
| 27 | YLYL4 | varchar(128) |  |  | 预留四 |
| 28 | YLYL5 | varchar(128) |  |  | 预留五 |
| 29 | YLYL6 | varchar(128) |  |  | 预留六 |

### 12.3 TB_DIC_MEDICINES — 药品字典 【一药品一行】

主键: `YLJGYQDM + YYZBDM`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医 疗 机 构 院 区代码 |
| 2 | YYZBDM | varchar(32) | 非空 | ● | 医院自编代码 |
| 3 | GJYBBM | varchar(64) | 非空 | ● | 填写对应国家医保代码，若不 存在，可为‘-’ |
| 4 | SYBZ | varchar(2) | 非空 | ○ | 使用标志 1：停用；0：使用中 |
| 5 | TYMC | varchar(100) | 非空 | ● | 药品注册通用名 国家食药监批文的注册通用 名，或者是本院自行界定的通 用名 |
| 6 | YWMC | varchar(100) | 非空 | ○ | 英文名称 |
| 7 | JXDM | varchar(4) | 非空 | ○ | 剂型代码 详见说明（1）。注：优先填报 二级药品剂型代码，即值域中 四位代码 |
| 8 | BZJX | varchar(100) | 非空 | ● | 剂型名称 |
| 9 | YNZJBZ | varchar(1) | 非空 |  | 院内制剂标志 0：非自制药品；1：自制药品 |
| 10 | TBSM | varchar(100) | 非空 | ○ | 药品分类上或使用上的特别说明 例如：属于毒麻药、属于抗菌 素控制药等 |
| 11 | BZSM | varchar(100) | 非空 |  | 备注说明 |
| 12 | XGBZ | varchar(1) | 非空 |  | 1：正常；2：撤销 |
| 13 | YLYL1 | varchar(128) |  |  | 预留一 |
| 14 | YLYL2 | varchar(128) |  |  | 预留二 |
| 15 | YLYL3 | varchar(128) |  |  | 预留三 |
| 16 | YLYL4 | varchar(128) |  |  | 预留四 |
| 17 | YLYL5 | varchar(128) |  |  | 预留五 |
| 18 | YLYL6 | varchar(128) |  |  | 预留六 |

### 12.4 TB_DIC_MATERIALS — 耗材字典 【一耗材一行】

主键: `YLJGYQDM + YYZBDM`

| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |
|---|------|------|:----:|:----:|------|
| 1 | YLJGYQDM | varchar(8) | 非空 | ● | 医 疗 机 构 院 区代码 |
| 2 | YYZBDM | varchar(32) | 非空 | ● | 医院自编代码 |
| 3 | WJSFDM | varchar(64) | 非空 |  | 医院科室名称或病区科室名称的全称 ⚠国标源 DDL 注释错位, 实义: 物价收费代码 |
| 4 | YBMLBM | varchar(64) | 非空 | ○ | 该科室对应“医疗卫生机构业务科    室分类与代码”(卫统)标准的代    码 ⚠国标源 DDL 注释错位, 实义: 医保目录编码 |
| 5 | GJYBBM | varchar(64) | 非空 | ● | 国家医保代码 |
| 6 | XMMC | varchar(100) | 非空 | ● | 医院对该收费项目界定的 名称 |
| 7 | SFDW | varchar(30) | 非空 | ● | 用文字表述。计价单位 |
| 8 | SFDJ | decimal(15,3) | 非空 | ● | 收费单价 标准的收费单价 |
| 9 | SYBZ | varchar(2) | 非空 | ○ | 使用标志 1：停用；0：使用中 |
| 10 | YNZJBZ | varchar(1) | 非空 |  | 院内自制标志 0：非自制；1：自制 |
| 11 | TBSM | varchar(100) | 非空 |  | 分类上或使用上的特别说明 |
| 12 | BZSM | varchar(100) | 非空 |  | 备注说明 |
| 13 | XGBZ | varchar(1) | 非空 |  | 修改标志 1：正常；2：撤销 |
| 14 | YLYL1 | varchar(128) |  |  | 预留一 |
| 15 | YLYL2 | varchar(128) |  |  | 预留二 |
| 16 | YLYL3 | varchar(128) |  |  | 预留三 |
| 17 | YLYL4 | varchar(128) |  |  | 预留四 |
| 18 | YLYL5 | varchar(128) |  |  | 预留五 |
| 19 | YLYL6 | varchar(128) |  |  | 预留六 |
