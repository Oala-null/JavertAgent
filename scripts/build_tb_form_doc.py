# ponytail: 一次性生成器 — data_hub_schema.json + _ext_tables.sql → 全字段版对接单
import json, re

SCHEMA = json.load(open('/Users/shane/26er/Scriv/data_hub_schema.json'))
EXT_SQL = open('/Users/shane/26er/Scriv/data_hub_filled/_ext_tables.sql').read()
OUT = '/Users/shane/26er/Javert/docs/试点医院数据对接表单_TB_全字段版.md'

# ---- 解析 3 张扩展表 DDL ----
def parse_ext(sql):
    tables = {}
    for m in re.finditer(r"CREATE TABLE \[dbo\]\.\[(\w+)\] \((.*?)\n\);", sql, re.S):
        name, body = m.group(1), m.group(2)
        cols = []
        for line in body.splitlines():
            cm = re.match(r"\s*\[(\w+)\]\s+([\w\(\),]+)\s+(NOT NULL|NULL)[^,]*,?\s*(?:--\s*(.*))?$", line)
            if cm:
                cols.append({'name': cm.group(1), 'type': cm.group(2),
                             'nullable': cm.group(3) == 'NULL',
                             'desc': (cm.group(4) or '').strip()})
        pk = re.search(r"PRIMARY KEY CLUSTERED \(([^)]+)\)", body)
        pk_cols = [c.strip('[] ') for c in pk.group(1).split(',')] if pk else []
        tables[name] = {'columns': cols, 'pk': pk_cols}
    return tables

EXT = parse_ext(EXT_SQL)
EXT['TB_CIS_MEDICAL_DOCUMENT'].setdefault('table_desc', '病历文书 (全文, 按段落拆行)')
EXT['TB_HIS_ZY_FEE_DETAIL_EXT'].setdefault('table_desc', '住院费用明细医保分解扩展')
EXT['TB_BA_SYSSK_EXT'].setdefault('table_desc', '病案首页手术医保双码扩展')

# ---- 表级元信息: (中文名, 粒度, 简介行, ●集合, ○集合) ----
# ● = 我方审计/DRG 链路直接消费, 缺了对应能力跑不了; ○ = 建议提供, 提升精度
META = {
 'TB_YL_ZY_MEDICAL_RECORD': dict(zh='住院就诊记录', grain='一次住院一行',
   intro='全库键桥锚点: JZLSH ↔ CISID (住院号) ↔ BAH (病案号) 三键在此对齐; 入出院时间为连接预检时间窗依据。',
   core={'YLJGYQDM','JZLSH','CISID','BAH','RYSJ','CYSJ'},
   sugg={'JZKSMC','CYKSMC','HZXM','JZKSBM','CYKSBM'}),
 'TB_HIS_ZY_ADM_REG': dict(zh='住院登记', grain='一次住院一行',
   intro='入院登记口径 (入院科室/床位/登记时间), 与就诊记录互补; 用于住院轨迹核验。',
   core={'YLJGYQDM','JZLSH','RYSJ'}, sugg={'RYKS','RYCH','LGBZ','RYCWSX'}),
 'TB_YL_PATIENT_INFORMATION': dict(zh='患者基本信息', grain='一名患者一行',
   intro='患者主索引 (性别/出生日期)。KH/KLX 为本表主键: 无真实卡号时 KH 填患者号、KLX 填 `99` (与约定 10 一致); 姓名/证件/联系方式均可脱敏。',
   core={'YLJGYQDM','KH','KLX','XB','CSRQ'}, sugg={'XM','YYDAH'}),
 'TB_DIC_HOSPITAL': dict(zh='医院信息', grain='一院区一行 (1-2 行)',
   intro='院区码 ↔ 机构码映射, 取数桥必需: YLJGYQDM 用 4 位短码, 原 12 位国标机构码放 YYJC。',
   core={'YLJGYQDM','YYMC','YYJC'}, sugg={'JGJB','JGDJ'}),
 'TB_CIS_MEDICAL_DOCUMENT': dict(zh='病历文书 ⁺扩展表', grain='一段文书一行',
   intro=('全系统命脉: LLM 读文书判断"有无反证/有无指征"。整份文书请**按段落拆行** (如 入院记录 拆成 主诉/现病史/既往史… 各一行); '
          '无法拆分时整文一行亦可接受 (精度略降)。覆盖文书类型越全越好: 入院记录 / 病程记录 / 手术记录 / 出院小结 / 知情同意 / 会诊记录…'),
   core={'YLJGYQDM','JZLSH','WSLSH','WSLB','WSMC','ZW'}, sugg={'DLBT','DLXH','JLSJ'}),
 'TB_CIS_LEAVEHOSPITAL_SUMMARY': dict(zh='出院小结', grain='一次住院一行',
   intro='出院小结结构化表 (入院诊断/诊疗过程/出院情况/出院医嘱分列)。若出院小结已随文书表按段落给出, 本表可由我方从文书 pivot 生成; 有现成结构化源则直接给。',
   core={'YLJGYQDM','JZLSH'},
   sugg={'RYZD','CYZD','MZZD','RYZZTZ','JCHZ','ZLGC','CYQK','CYQKMS','CYYZ','RYSJ','CYSJ','ZYTS'}),
 'TB_HIS_ZY_FEE_DETAIL_FS': dict(zh='住院费用发生明细', grain='一笔收费一行',
   intro='费用主战场 (Javert `search_fees` + Router 预筛)。请给**发生口径**明细 (费用发生时间), 不是结算汇总; 退费行按约定 7 处理。',
   core={'YLJGYQDM','JZLSH','SFMXID','STFBZ','FYFSSJ','MXXMMC','MXXMJE','MXFYLB','MXXMBMYB'},
   sugg={'MXXMBM','MXXMSL','MXXMDJ','YZID','MXXMDW'}),
 'TB_HIS_ZY_FEE_DETAIL_EXT': dict(zh='费用医保分解扩展 ⁺', grain='与 FS 1:1 挂接',
   intro='超标准收费 (M4) 审计信号源 + 开单医生/科室维度 + 药品通用名。字段能给多少给多少, 全空不阻塞主链路。',
   core={'YLJGYQDM','SFMXID','JZLSH'}, sugg=None),  # sugg=None → 其余全 ○
 'TB_IH_DIAGNOSIS_DETAIL': dict(zh='诊断明细', grain='一个诊断一行',
   intro='诊断指征判断 + `drug_audit_lookup` + verdict_gate 假阳性闸的诊断依据。编码用 ICD-10 **临床版** (约定 8)。',
   core={'YLJGYQDM','JZLSH','ZYZDLSH','ZDBM','ZDSM','CYZDBZ'}, sugg={'ZDLB','ZDSJ','RYBQ'}),
 'TB_OPRATION_DETAIL': dict(zh='手术明细', grain='一台手术/操作一行',
   intro='手术类规则 + verdict_gate 麻醉/手术判据。编码用 ICD-9-CM3 **临床版**; 医保版双码走 TB_BA_SYSSK_EXT。',
   core={'YLJGYQDM','JZLSH','SSMXLSH','SSCZMC','SSCZBM','ZCBZ'},
   sugg={'SSKSSJ','SSJSSJ','SSJB','MZFS','SXYHRYXM','MZYHRYXM','QKYHDJ'}),
 'TB_LIS_REPORT': dict(zh='检验报告', grain='一份报告一行',
   intro='检验报告头 (Javert `search_lab_results`)。解锁"检查/用药有无指征"类规则的客观证据 (如术前用抗菌药但白细胞正常)。',
   core={'YLJGYQDM','BGDH','BGRQ','JZLSH','BGSJ','BBMC','BGDLB'},
   sugg={'SQKS','BRXB','BRNL','BGYHRYXM','SHYHRYXM'}),
 'TB_LIS_INDICATORS': dict(zh='检验指标结果', grain='一个指标一行',
   intro='JYZBLSH 必须真唯一 (建议 `{BGDH}-{行序}`); YCTS 异常提示: 1 正常 / 2 异常 / 3 偏高 / 4 偏低, **定性阴性=1 正常**。',
   core={'YLJGYQDM','JYZBLSH','BGDH','BGRQ','JYZBMC','JYZBJG','YCTS'},
   sugg={'JYZBDM','JLDW','CKZ'}),
 'TB_RIS_REPORT': dict(zh='检查报告 (放射/核医学)', grain='一份报告一行',
   intro='分流规则: 检查类型 ∈ {放射, 核医学} → 本表; 其余 (超声/病理/心电/内镜/电生理) → TB_RIS_REPORT2。Javert `search_examinations` 消费。',
   core={'YLJGYQDM','INSTANCEUID','JZLSH','EXAMTYPE','JCMC','YXZD','YXBX','JCSJ','BGSJ'},
   sugg={'JCBW','JCKS','BGLCZD','YYS','STUDYUID','PATIENTID','SFYYY'}),
 'TB_RIS_REPORT2': dict(zh='检查报告 (超声/病理/心电/内镜等)', grain='一份报告一行',
   intro='非放射类检查报告。BT1NR/BT2NR 装"所见/描述"与"诊断/结论"; 其余 BTx 标题块按贵院报告结构填。',
   core={'YLJGYQDM','INSTANCEUID','JZLSH','EXAMTYPE','JCMC','JCSJ','BGSJ','JCBGJG','BT1NR','BT2NR'},
   sugg={'JCJGDM','JCBW','JCKS'}),
 'TB_BA_SYJBK': dict(zh='病案首页', grain='一次住院一行 (233 列)',
   intro='zadig_agent DRG/DIP 重确认输入主体; Javert 用作 ground truth 与假阳性闸判据。**按国标模板能填尽填**; 24 项费用宽表分类列 (○) 直接提升 DRG 精度。',
   core={'YLJGYQDM','SYXH','BAH','ZYZD','RYKS','CYKS','ZYTS','ZFY','BRXB','XSNL'},
   sugg={'LYFS','BLZD','BLZDMC','RYRQ','CYRQ','ZRYS','ZZYS','ZYYS'}),
 'TB_BA_SYZDK': dict(zh='病案首页其他诊断', grain='一个诊断一行',
   intro='首页口径其他诊断 (主诊断在 SYJBK, 不进本表); ZDXH 组内序 1..n。',
   core={'YLJGYQDM','SYXH','ZDXH','ZDDM','ZDMC'}, sugg={'RYBQ','ZGQK'}),
 'TB_BA_SYSSK': dict(zh='病案首页手术', grain='一台手术一行',
   intro='首页口径手术 ground truth; verdict_gate 麻醉/术前判据来源之一。',
   core={'YLJGYQDM','SYXH','SSXH','SSDM','SSMC','SFZYSS'},
   sugg={'SSRQ','SSJB','MZFS','SSYS','MZYS','MZKSSJ','MZJSSJ'}),
 'TB_BA_SYSSK_EXT': dict(zh='首页手术医保双码扩展 ⁺', grain='与 SYSSK 1:1 挂接',
   intro='**zadig_agent DRG/DIP 刚需**: 医保版手术编码 (与临床版双码并存, 约定 8)。',
   core={'YLJGYQDM','SYXH','SSXH','HISSDM','HISSMC'}, sugg=None),
 'TB_YB_JLC_CBRZDXX': dict(zh='医保结算清单-诊断信息', grain='一个诊断一行',
   intro='医保结算清单口径诊断 (医保版 ICD), 与临床版诊断明细互补, 用于医保口径核对。本表无院区列: LSH 与 JZLSH 同值, XH 为诊断唯一序号。',
   core={'XH','LSH','ZDNO','ZDMC'}, sugg=set()),
 'TB_DIC_DEPARTMENT': dict(zh='科室字典', grain='一科室一行',
   intro='科室编码消歧 + 未来跨患者/科室维度统计。直接从 HIS 全量导出, 行数小成本低。',
   core={'YLJGYQDM','YYKSDM','YYKSMC'}, sugg={'WSJDM','YBDM','KSXZ'}),
 'TB_DIC_PRACTITIONER': dict(zh='医护人员字典', grain='一人一行',
   intro='开单/计费医生编码消歧 + 未来医生维度画像。姓名可脱敏为工号。',
   core={'YLJGYQDM','YHRYID','XM'}, sugg={'GH','YSDM','LB','SSKS','ZCDM','ZHIC'}),
 'TB_DIC_MEDICINES': dict(zh='药品字典', grain='一药品一行',
   intro='药品审计 (M8) 编码消歧: 院内码 → 国家医保码 → 注册通用名。',
   core={'YLJGYQDM','YYZBDM','GJYBBM','TYMC','BZJX'}, sugg={'YWMC','JXDM','SYBZ','TBSM'}),
 'TB_DIC_MATERIALS': dict(zh='耗材字典', grain='一耗材一行',
   intro='耗材规格类规则 (M7 红区) 解锁前置; 同码多价取最新价。',
   core={'YLJGYQDM','YYZBDM','GJYBBM','XMMC','SFDW','SFDJ'}, sugg={'YBMLBM','SYBZ'}),
}

# 国标源 DDL 注释错位的列 (照录原文 + 追加更正)
DESC_FIX = {
 ('TB_DIC_PRACTITIONER','YHRYID'): '医护人员 ID (院内唯一)',
 ('TB_DIC_PRACTITIONER','GH'): '工号',
 ('TB_DIC_MATERIALS','WJSFDM'): '物价收费代码',
 ('TB_DIC_MATERIALS','YBMLBM'): '医保目录编码',
}

# 域分组 (对接单章节顺序)
GROUPS = [
 ('就诊主索引与机构', ['TB_YL_ZY_MEDICAL_RECORD','TB_HIS_ZY_ADM_REG','TB_YL_PATIENT_INFORMATION','TB_DIC_HOSPITAL']),
 ('病历文书', ['TB_CIS_MEDICAL_DOCUMENT','TB_CIS_LEAVEHOSPITAL_SUMMARY']),
 ('住院费用', ['TB_HIS_ZY_FEE_DETAIL_FS','TB_HIS_ZY_FEE_DETAIL_EXT']),
 ('诊断与手术 (临床版)', ['TB_IH_DIAGNOSIS_DETAIL','TB_OPRATION_DETAIL']),
 ('检验 (LIS)', ['TB_LIS_REPORT','TB_LIS_INDICATORS']),
 ('检查 (RIS)', ['TB_RIS_REPORT','TB_RIS_REPORT2']),
 ('病案首页', ['TB_BA_SYJBK','TB_BA_SYZDK','TB_BA_SYSSK','TB_BA_SYSSK_EXT']),
 ('医保结算清单', ['TB_YB_JLC_CBRZDXX']),
 ('基础字典', ['TB_DIC_DEPARTMENT','TB_DIC_PRACTITIONER','TB_DIC_MEDICINES','TB_DIC_MATERIALS']),
]
ALL_TABLES = [t for _, ts in GROUPS for t in ts]
EXT_NAMES = set(EXT)

def get_table(name):
    if name in EXT_NAMES:
        t = EXT[name]
        return t['columns'], t['pk'], t.get('table_desc', '')
    t = SCHEMA[name]
    return t['columns'], t.get('pk') or [], t.get('table_desc', '')

def esc(s):
    return (s or '').replace('|', '\\|').replace('\n', ' ').strip()

def audit_mark(tbl, col, meta):
    if col in meta['core']:
        return '●'
    sugg = meta['sugg']
    if sugg is None:  # EXT 表: 非 ● 全 ○
        return '○'
    if col in sugg:
        return '○'
    # SYJBK 费用宽表列启发: decimal 且注释以"费"结尾 → ○
    return ''

def syjbk_fee_sugg(cols):
    return {c['name'] for c in cols
            if c['type'].startswith('decimal') and (c.get('desc') or '').rstrip().endswith(('费', '费用'))}

NUM = ["零","一","二","三","四","五","六","七","八","九","十","十一","十二"]

def zh_name(t):
    return META[t]['zh'].replace(' ⁺扩展表', '').replace(' ⁺', '')

def tb_label(t):
    return f"{t} ⁺" if t in EXT_NAMES else t

lines = []
w = lines.append
w('# 试点医院数据对接表单 — 国标 TB_* 全字段版')
w('')
w('> 版本 v2.1 | 2026-07-06 | 共 23 张表 (国标 46 表中的 20 张 + 我方扩展 3 张, 表名后标 ⁺; 扩展表建表脚本 `_ext_tables.sql` 我方提供)')
w('>')
w("> 字段表标记 — **非空**: 国标 DDL NOT NULL, 无源字符列填 `'-'`; **审计**: `●` 必填 (我方审计直接消费) · `○` 建议 (提升精度) · 空 = 无源填 `'-'` 或 NULL")
w('')
w('---')
w('')
w('## 一、我们需要哪几个方面的数据')
w('')
w('| # | 方面 | 表 |')
w('|---|------|-----|')
for gi, (gname, tbls) in enumerate(GROUPS, 1):
    ts = ' · '.join(tb_label(t) for t in tbls)
    w(f'| {gi} | {gname} | {ts} |')
w('')
w('---')
w('')
w('## 二、目录')
w('')
w('| 章节 | 表 | 中文 | 粒度 | 列数 |')
w('|------|-----|------|------|:----:|')
for gi, (gname, tbls) in enumerate(GROUPS, 4):
    for j, t in enumerate(tbls, 1):
        w(f'| {gi}.{j} | {tb_label(t)} | {zh_name(t)} | {META[t]["grain"]} | {len(get_table(t)[0])} |')
w('')
w('---')
w('')
w('## 三、全局约定')
w('')
w('| # | 约定 | 说明 |')
w('|---|------|------|')
w('| 1 | **JZLSH 全库唯一患者关联键** | 住院就诊流水号。**所有表同一患者用同一值**, 一次住院一个值 |')
w('| 2 | **YLJGYQDM 院区代码** | varchar(8)。用 4 位短码 (如 `0001`); 12 位国标机构码放 TB_DIC_HOSPITAL.YYJC |')
w('| 3 | **SYXH = JZLSH** | 病案首页四表 (SYJBK/SYZDK/SYSSK/SYSSK_EXT) 的首页序号与 JZLSH 取同值; 结算清单 LSH 亦同值 |')
w('| 4 | **流水号必须真唯一** | SFMXID / WSLSH / ZYZDLSH / SSMXLSH / JYZBLSH; 源流水号跨患者重复时合成 `{JZLSH}-{流水号}-{序号}` |')
w('| 5 | **日期格式 ISO** | `YYYY-MM-DD HH:MM:SS`; BGRQ/CSRQ 类列为 `YYYYMMDD` |')
w('| 6 | **1900-01-01 哨兵语义固定** | = "尚未发生" (未出院 / 医嘱未终止), 不能当"时间未知"乱填 |')
w('| 7 | **退费行** | STFBZ=2 单独成行, 数量/金额存正值 (符号由 STFBZ 表达) |')
w('| 8 | **两套编码不许混** | 临床版 ICD (诊断明细/手术明细) 与医保版编码 (SYSSK_EXT / 费用 MXXMBMYB / 结算清单) 各归各列 |')
w("| 9 | **NOT NULL 无源兜底** | 字符列填 `'-'`; 时间列取最近真实业务时间, 不造假时间 |")
w("| 10 | **脱敏** | 患者姓名可填 `'-'`; 身份证不需要; 卡号 KH=`'-'` 或患者号、卡类型 KLX=`'99'` |")
w('| 11 | **文书按段落拆行** | 整份文书拆成 主诉/现病史/既往史… 一段一行; 拆不了整文一行 |')
w('| 12 | **检查报告分流** | 放射/核医学 → TB_RIS_REPORT; 超声/病理/心电/内镜/电生理 → TB_RIS_REPORT2 |')
w('| 13 | **检验异常提示 YCTS** | 1 正常 / 2 异常 / 3 偏高 / 4 偏低; 定性阴性=1; JYZBLSH 建议 `{BGDH}-{行序}` |')
w('')
w('---')

sec = 3
for gname, tbls in GROUPS:
    sec += 1
    w('')
    w(f'## {NUM[sec]}、{gname}')
    for j, t in enumerate(tbls, 1):
        cols, pk, _ = get_table(t)
        w('')
        w(f'### {sec}.{j} {tb_label(t)} — {zh_name(t)} 【{META[t]["grain"]}】')
        w('')
        if pk:
            w(f"主键: `{' + '.join(pk)}`")
            w('')
        w('| # | 字段 | 类型 | 非空 | 审计 | 说明 (DDL 注释) |')
        w('|---|------|------|:----:|:----:|------|')
        fee_sugg = syjbk_fee_sugg(cols) if t == 'TB_BA_SYJBK' else set()
        meta = META[t]
        for k, c in enumerate(cols, 1):
            nn = '非空' if not c['nullable'] else ''
            m = audit_mark(t, c['name'], meta)
            if not m and c['name'] in fee_sugg:
                m = '○'
            desc = esc(c.get('desc'))
            fix = DESC_FIX.get((t, c['name']))
            if fix:
                desc = f"{desc} ⚠国标源 DDL 注释错位, 实义: {fix}"
            w(f"| {k} | {c['name']} | {c['type']} | {nn} | {m} | {desc} |")

w('')
open(OUT, 'w').write('\n'.join(lines))
n_fields = sum(len(get_table(t)[0]) for t in ALL_TABLES)
print(f'写出 {OUT}: {len(ALL_TABLES)} 表 / {n_fields} 字段 / {len(lines)} 行')

# ---- pandoc → docx + 全网格线注入 ----
import subprocess, zipfile, shutil
DOCX = OUT[:-3] + '.docx'
subprocess.run(['pandoc', OUT, '-o', DOCX], check=True)
borders = ''.join(f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
                  for s in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
zin = zipfile.ZipFile(DOCX)
styles = zin.read('word/styles.xml').decode('utf8')
block = re.search(r'<w:style w:default="1" w:styleId="Table" w:type="table">.*?</w:style>', styles, re.S).group(0)
styles = styles.replace(block, block.replace('<w:tblPr>', f'<w:tblPr><w:tblBorders>{borders}</w:tblBorders>', 1))
with zipfile.ZipFile(DOCX + '.tmp', 'w', zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = styles.encode('utf8') if item.filename == 'word/styles.xml' else zin.read(item.filename)
        zout.writestr(item, data)
zin.close()
shutil.move(DOCX + '.tmp', DOCX)
print(f'写出 {DOCX} (全网格线)')
