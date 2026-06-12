"""
生成「医保监管规则引擎说明（医学专家版）」docx 文档。

简化版:
- 不讲 Java 代码 / 数据表 / 表达式 DSL
- 重点讲每条规则做什么、什么场景触发、临床实例
- 视觉风格参照 Javert 既有 HTML 报告 (蓝紫渐变 + 蓝色商务)
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor, Cm


# --------------------------------------------------------------------------
# 配色 (参照 Javert HTML 报告蓝紫色调)
# --------------------------------------------------------------------------
COLOR_PRIMARY = RGBColor(0x1E, 0x40, 0xAF)      # 主标题深蓝
COLOR_ACCENT = RGBColor(0x6D, 0x28, 0xD9)       # 强调紫
COLOR_SUBTLE = RGBColor(0x6B, 0x72, 0x80)       # 副文字灰
COLOR_VIOLATION = RGBColor(0xB9, 0x1C, 0x1C)    # 违规红
COLOR_WARN = RGBColor(0xB4, 0x53, 0x09)         # 提示橙
COLOR_OK = RGBColor(0x04, 0x78, 0x57)           # 合规绿
COLOR_HEADER_BG = "1E40AF"                      # 表头蓝底 (hex)


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def shade_cell(cell, hex_color: str) -> None:
    """单元格底色"""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def set_cell_border(cell, color="DDDDDD", sz=4):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_borders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        elem = OxmlElement(f"w:{edge}")
        elem.set(qn("w:val"), "single")
        elem.set(qn("w:sz"), str(sz))
        elem.set(qn("w:color"), color)
        tc_borders.append(elem)
    tc_pr.append(tc_borders)


def add_heading(doc: Document, text: str, level: int = 1, color: RGBColor = COLOR_PRIMARY) -> None:
    h = doc.add_heading("", level=level)
    run = h.add_run(text)
    run.font.color.rgb = color
    run.font.name = "微软雅黑"
    rPr = run._element.get_or_add_rPr()
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:eastAsia"), "微软雅黑")
    rPr.append(rFonts)


def add_para(doc: Document, text: str, bold=False, color: RGBColor | None = None, size: int = 11) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = "微软雅黑"
    if color is not None:
        run.font.color.rgb = color
    rPr = run._element.get_or_add_rPr()
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:eastAsia"), "微软雅黑")
    rPr.append(rFonts)


def add_rule_card(doc: Document, code: str, name: str, level: str,
                  what: str, trigger: str, example: str,
                  count: int | None = None) -> None:
    """给一条规则画一张卡片 (标题行 + 内容表)。"""

    # ---- 标题行: 编号 + 名称 + 等级标签 ----
    title_table = doc.add_table(rows=1, cols=3)
    title_table.autofit = False
    title_table.columns[0].width = Cm(2.0)
    title_table.columns[1].width = Cm(11.5)
    title_table.columns[2].width = Cm(3.0)

    cells = title_table.rows[0].cells

    # 编号
    shade_cell(cells[0], "1E40AF")
    set_cell_border(cells[0], "1E40AF")
    p = cells[0].paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(f"#{code}")
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    run.bold = True
    run.font.size = Pt(13)
    run.font.name = "微软雅黑"
    cells[0].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # 名称
    shade_cell(cells[1], "EFF6FF")
    set_cell_border(cells[1], "1E40AF")
    p = cells[1].paragraphs[0]
    run = p.add_run(f"  {name}")
    if count is not None:
        run2 = p.add_run(f"   （生产数据触发 {count:,} 条）")
        run2.font.color.rgb = COLOR_SUBTLE
        run2.font.size = Pt(10)
        run2.font.name = "微软雅黑"
    run.font.color.rgb = COLOR_PRIMARY
    run.bold = True
    run.font.size = Pt(12)
    run.font.name = "微软雅黑"
    cells[1].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # 等级
    lvl_color_map = {
        "违规": (COLOR_VIOLATION, "FEE2E2"),
        "提示": (COLOR_WARN, "FEF3C7"),
        "可疑": (COLOR_WARN, "FEF3C7"),
    }
    lvl_color, lvl_bg = lvl_color_map.get(level, (COLOR_SUBTLE, "F3F4F6"))
    shade_cell(cells[2], lvl_bg)
    set_cell_border(cells[2], "1E40AF")
    p = cells[2].paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(level)
    run.font.color.rgb = lvl_color
    run.bold = True
    run.font.size = Pt(11)
    run.font.name = "微软雅黑"
    cells[2].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # ---- 内容表 ----
    body_table = doc.add_table(rows=3, cols=2)
    body_table.autofit = False
    body_table.columns[0].width = Cm(2.5)
    body_table.columns[1].width = Cm(14.0)

    rows_data = [
        ("做什么", what),
        ("触发条件", trigger),
        ("临床示例", example),
    ]
    for i, (label, val) in enumerate(rows_data):
        c = body_table.rows[i].cells
        shade_cell(c[0], "F8FAFC")
        set_cell_border(c[0])
        set_cell_border(c[1])
        p0 = c[0].paragraphs[0]
        r0 = p0.add_run(label)
        r0.bold = True
        r0.font.size = Pt(10.5)
        r0.font.color.rgb = COLOR_PRIMARY
        r0.font.name = "微软雅黑"
        c[0].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        p1 = c[1].paragraphs[0]
        r1 = p1.add_run(val)
        r1.font.size = Pt(10.5)
        r1.font.name = "微软雅黑"

    # 空段落留点呼吸感
    doc.add_paragraph()


# --------------------------------------------------------------------------
# 内容 (医学专家视角，不讲代码)
# --------------------------------------------------------------------------
RULES_ACTIVE = [
    # (code, name, level, what, trigger, example, prod_count)
    ("5", "限定医院等级",
     "违规",
     "某些药品/项目只允许在指定等级及以上医院使用 (如三级医院专用药)。",
     "费用项目命中规则编码，且本次就诊医院等级低于该项目限定等级。",
     "某靶向药限定三级医院使用，本次就诊为二级综合医院 → 触发。",
     4467),

    ("4", "限定儿童年龄",
     "违规",
     "儿童剂型/儿科专用药仅限指定年龄段 (按天数或岁数判定)。",
     "费用项目命中儿童限定编码，患者实际年龄超出 (或低于) 限定范围，且病案无相应适应症诊断豁免。",
     "小儿氨酚黄那敏颗粒用于 40 岁成人；新生儿专用配方用于 5 岁患儿 → 触发。",
     2775),

    ("36", "医保不予报销项目",
     "违规",
     "医保目录外项目原则上应全自费，不得走医保统筹/个人账户报销。",
     "费用项目命中目录外编码 (整容/保健/工伤类) 且实际仍走了医保内金额 (inscp_scp_amt > 0)。",
     "美容相关注射、保健类按摩等按医保内金额结算 → 触发。",
     2489),

    ("19", "重复收费",
     "违规",
     "已包含的子项目不能再单独收费；同一项目不能重复计费；某些组合不可同时存在。",
     "本次费用中同时出现 A (主项) 与 B (相伴项)，按规则配置在同一处方/同一天/本次就诊范围内同框；高级用法可再叠加 C 形成三元约束 (ABC 同时存在才违规)。",
     "心电图检查 + 心电图测量 + 心电图报告 三项同时收费；治疗费 + 已包含该治疗的套餐费 → 触发。",
     2251),

    ("3", "限定性别",
     "违规",
     "性别专用项目不能开给异性 (妇科药/前列腺药等)。",
     "费用项目命中限定性别编码，且患者性别与项目限定性别冲突。",
     "前列腺增生用药开给女性；妇科外用药开给男性 → 触发。",
     1027),

    ("17", "超限定频次/数量",
     "违规",
     "某些项目有单次/单日/累计 (按天数窗口) 的数量或费用上限。",
     "在指定患者维度 (身份证/医保号) 和时间窗口内 (本次就诊/同一处方/同一天/N 天/全年) 累计 cnt 或费用，超过 limit_times；住院模式可按住院天数动态放大上限。",
     "某检验项目每月限 4 次，本次为本月第 5 次 → 触发；住院每日限 1 次 PICC 维护，本日已收 2 次 → 触发。",
     612),

    ("35", "超限定支付疗程",
     "违规",
     "某些项目只允许在固定疗程天数内收费 (如 X 天内某康复治疗)。",
     "费用项目命中规则编码，且当前发生日距本次住院首次使用该项目的日期超过 limit_days。",
     "某高压氧治疗限定 5 天疗程，第 1 天首次收费后第 6 天仍在收 → 触发。",
     84),

    ("2", "限定就医方式",
     "违规",
     "某些项目仅限门诊或仅限住院使用，不允许跨类型开立。",
     "费用项目命中规则编码即直接触发 (规则在装载时已按门诊/住院分别启用)。",
     "门诊不可单独收住院专用监护类项目；住院不可收某门诊专用快检 → 触发。",
     81),

    ("7", "违反限定适应症用药",
     "违规",
     "限定适应症的药品/项目必须有对应诊断才能使用。",
     "费用项目命中规则编码 (非全自费)，且病案诊断中没有任何一个命中该项目限定的诊断包 (支持全码 startsWith 及 I20-I25 这种 ICD 区间码)。",
     "某 PD-1 单抗仅限肺癌/黑色素瘤等指定诊断，病案诊断为 “高血压” → 触发。",
     20),

    ("15", "材料-项目搭配不符",
     "违规",
     "某些医用材料必须搭配对应的手术/治疗项目使用，反之亦然。",
     "A 项 (材料) 在指定范围 (本次就诊/同一处方/同一天) 内出现，但 B 项 (对应手术/操作) 在同一范围内未出现。",
     "一次性穿刺包已收费，但本次就诊未收对应穿刺术费 → 触发。",
     12),

    ("9", "中药饮片单味不予支付",
     "违规",
     "某些单味中药饮片 (例如部分滋补类) 不在医保支付范围；当整张处方仅有该一味时不予报销。",
     "本次处方中所有中药饮片 (yb_code 以 YP 开头) 经去重后只有 1 味，且该味在 “单味不予支付” 清单中。",
     "处方只开了一味 “冬虫夏草” → 触发。",
     1),
]


# 实装但当前生产数据触发为 0 (灰度/待启用) — 用表格汇总即可
RULES_LATENT = [
    ("10", "超量取药", "考虑日处方上限、包装数、诊断组例外，跨整张处方累计判断超量。"),
    ("12", "限制使用 (首发/适应症/治疗包)", "三道闸: 适应症诊断/治疗包匹配/历史首发，全不通过才触发。"),
    ("18", "同药品组总数量上限", "按药品组+时间窗口查全部明细，不同药品种数超上限报后续项。"),
    ("22", "提前开药", "在前若干天内已经开过同种药且药量未用完，又开一笔。"),
    ("23", "诊断缺失或编码不规范", "整单粒度: 未上传诊断、或诊断编码不在标准字典内。"),
    ("24", "药品费用 + 诊断不规范", "至少一条药品类费用，且诊断空或编码不规范。"),
    ("26", "收费跨期超 180 天", "费用发生时间距结算时间超 180 天 (积压补登)。"),
    ("28", "冠脉支架 / 24 小时无住院", "门诊收冠脉支架但同身份证 24 小时内无住院记录。"),
    ("29", "支架后无抗血小板药", "冠脉/脑血管支架手术后 366 天内无阿司匹林/氯吡格雷/替格瑞洛记录。"),
    ("30", "限参保人 (人群限制)", "项目命中规则编码且参保类型为 310 或为外伤情形 (out_flag=1)。"),
    ("31", "门急诊就诊频次异常", "月累计 ≥20 次 / 单日 ≥4 次 / 年度 ≥100 次。"),
    ("33", "配伍禁忌 (十八反/十九畏)", "同一处方内 A 药与 B 药同时存在 (相反/相畏)。"),
    ("34", "首日不可开药", "明细日为住院首日且开了规则限定的项目。"),
    ("37", "药品超量开药 (新版)", "门诊按主单顺序累计、住院按当日累计，超 limit × 天数 上限按比例扣金额。"),
    ("38", "中药饮片超量", "按每日上限 × 阈值 × 天数 折算允许克数，超出按比例扣金额。"),
    ("1", "限定险种 (生育/工伤)", "命中编码 + 险种不匹配 (1=生育, 2=工伤)。"),
    ("6", "限定开单科室", "项目命中编码 + 开单科室不在限定列表内。"),
]


# --------------------------------------------------------------------------
# 主体: 组装 docx
# --------------------------------------------------------------------------
def build():
    doc = Document()

    # 全局默认字体
    style = doc.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(11)
    rPr = style.element.get_or_add_rPr()
    rFonts = OxmlElement("w:rFonts")
    rFonts.set(qn("w:eastAsia"), "微软雅黑")
    rPr.append(rFonts)

    # 页面边距
    for section in doc.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)

    # =============== 封面标题 ===============
    title_table = doc.add_table(rows=1, cols=1)
    title_cell = title_table.rows[0].cells[0]
    shade_cell(title_cell, "1E40AF")
    set_cell_border(title_cell, "1E40AF", sz=12)
    p = title_cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("\n医保监管规则引擎说明\n")
    run.font.size = Pt(22)
    run.bold = True
    run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    run.font.name = "微软雅黑"
    run = p.add_run("（医学专家版）\n")
    run.font.size = Pt(13)
    run.font.color.rgb = RGBColor(0xDB, 0xEA, 0xFE)
    run.font.name = "微软雅黑"

    add_para(doc, "")
    add_para(doc, "本文档面向临床/医保专家，简要介绍当前规则引擎中各条审核规则的临床含义、触发条件与典型示例，便于专家在审核工作中快速对应到具体的医保监管口径。",
             color=COLOR_SUBTLE)
    add_para(doc, "")

    # =============== 一、规则引擎是什么 ===============
    add_heading(doc, "一、规则引擎在做什么", level=1)
    add_para(doc,
             "规则引擎对每一份医保结算单据 (门诊/住院) 进行自动审查，目标是在结算前后发现"
             "「不合规收费」或「不合理诊疗」，输出 “违规 / 提示 / 可疑” 三档结果交给医保审核人员复核。")
    add_para(doc,
             "审查的对象包括: 费用明细 (单条费用项目)、主单 (一次结算)、诊断列表、参保信息、"
             "院内开单科室与医师等。审查方式是把每条费用与「规则」一一比对，规则由数据库表统一配置，"
             "无需改代码即可上下线。")
    add_para(doc,
             "目前共有 38 个规则编号，启用 32 条，其中在生产数据中真正常规触发的约 11 条 (见下表)；"
             "其余规则为灰度/低频/待启用，配置准备就绪后可立即上线。")

    # =============== 二、告警等级 ===============
    add_heading(doc, "二、告警等级", level=1)
    lvl_table = doc.add_table(rows=4, cols=2)
    lvl_table.autofit = False
    lvl_table.columns[0].width = Cm(3.0)
    lvl_table.columns[1].width = Cm(13.5)
    hdrs = ["等级", "含义"]
    rows = [
        ("违规", "明确违反医保规定，建议拒付或追回。"),
        ("提示", "可能存在问题，需要审核人员关注 (例如材料-项目搭配缺失)。"),
        ("可疑", "存在异常迹象但需要更多证据，需结合病历进一步判断 (例如频次稍高、跨期长等)。"),
    ]
    for i, (a, b) in enumerate([hdrs] + rows):
        cells = lvl_table.rows[i].cells
        if i == 0:
            shade_cell(cells[0], COLOR_HEADER_BG)
            shade_cell(cells[1], COLOR_HEADER_BG)
            color = RGBColor(0xFF, 0xFF, 0xFF)
            bold = True
        else:
            shade_cell(cells[0], "F8FAFC" if i % 2 else "FFFFFF")
            shade_cell(cells[1], "F8FAFC" if i % 2 else "FFFFFF")
            color = None
            bold = False
        set_cell_border(cells[0])
        set_cell_border(cells[1])
        for cell, txt in zip(cells, (a, b)):
            p = cell.paragraphs[0]
            r = p.add_run(txt)
            r.bold = bold
            r.font.size = Pt(10.5)
            r.font.name = "微软雅黑"
            if color is not None:
                r.font.color.rgb = color
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    add_para(doc, "")

    # =============== 三、生产数据触发分布 ===============
    add_heading(doc, "三、当前规则触发分布", level=1)
    add_para(doc, "下表为生产数据中各规则的触发条数，反映规则的实际使用强度。", color=COLOR_SUBTLE)

    dist_table = doc.add_table(rows=12, cols=4)
    dist_table.autofit = False
    dist_table.columns[0].width = Cm(2.5)
    dist_table.columns[1].width = Cm(8.0)
    dist_table.columns[2].width = Cm(3.0)
    dist_table.columns[3].width = Cm(3.0)
    dist_rows = [
        ("规则号", "名称", "触发条数", "占比"),
        ("5", "限定医院等级", "4,467", "32.3%"),
        ("4", "限定儿童年龄", "2,775", "20.1%"),
        ("36", "医保不予报销项目", "2,489", "18.0%"),
        ("19", "重复收费", "2,251", "16.3%"),
        ("3", "限定性别", "1,027", "7.4%"),
        ("17", "超限定频次", "612", "4.4%"),
        ("35", "超限定支付疗程", "84", "0.6%"),
        ("2", "限定就医方式", "81", "0.6%"),
        ("7", "违反限定适应症用药", "20", "0.1%"),
        ("15", "材料-项目搭配不符", "12", "<0.1%"),
        ("9", "中药饮片单味不予支付", "1", "<0.1%"),
    ]
    for i, row in enumerate(dist_rows):
        cells = dist_table.rows[i].cells
        for j, txt in enumerate(row):
            if i == 0:
                shade_cell(cells[j], COLOR_HEADER_BG)
                color = RGBColor(0xFF, 0xFF, 0xFF)
                bold = True
            else:
                shade_cell(cells[j], "F8FAFC" if i % 2 else "FFFFFF")
                color = None
                bold = False
            set_cell_border(cells[j])
            p = cells[j].paragraphs[0]
            if j in (0, 2, 3):
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(txt)
            r.bold = bold
            r.font.size = Pt(10.5)
            r.font.name = "微软雅黑"
            if color is not None:
                r.font.color.rgb = color
            cells[j].vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    add_para(doc, "")

    # =============== 四、活跃规则详解 ===============
    add_heading(doc, "四、活跃规则详解", level=1)
    add_para(doc, "以下是当前生产数据中实际触发、且对临床有直接监管意义的 11 条核心规则。",
             color=COLOR_SUBTLE)
    add_para(doc, "")

    for code, name, level, what, trigger, example, count in RULES_ACTIVE:
        add_rule_card(doc, code, name, level, what, trigger, example, count)

    # =============== 五、其他实装规则 ===============
    add_heading(doc, "五、其他已实装规则 (待启用 / 低频)", level=1)
    add_para(doc, "下列规则在引擎中已实装代码，目前处于灰度或专项启用状态，配置就绪后可立即生效。",
             color=COLOR_SUBTLE)

    latent_table = doc.add_table(rows=len(RULES_LATENT) + 1, cols=3)
    latent_table.autofit = False
    latent_table.columns[0].width = Cm(2.0)
    latent_table.columns[1].width = Cm(5.5)
    latent_table.columns[2].width = Cm(9.0)
    hdrs = ("规则号", "名称", "核心思路")
    for j, txt in enumerate(hdrs):
        c = latent_table.rows[0].cells[j]
        shade_cell(c, COLOR_HEADER_BG)
        set_cell_border(c)
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(txt)
        r.bold = True
        r.font.size = Pt(10.5)
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        r.font.name = "微软雅黑"
        c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    for i, (code, name, desc) in enumerate(RULES_LATENT):
        cells = latent_table.rows[i + 1].cells
        bg = "F8FAFC" if (i + 1) % 2 else "FFFFFF"
        for j, txt in enumerate((code, name, desc)):
            c = cells[j]
            shade_cell(c, bg)
            set_cell_border(c)
            p = c.paragraphs[0]
            if j == 0:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            r = p.add_run(txt)
            r.font.size = Pt(10)
            r.font.name = "微软雅黑"
            if j == 1:
                r.font.color.rgb = COLOR_PRIMARY
                r.bold = True
            c.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    add_para(doc, "")

    # =============== 六、设计思路 ===============
    add_heading(doc, "六、设计思路概要", level=1)
    points = [
        ("配置驱动", "代码只负责算法骨架，具体限值、编码、诊断包全部在数据库表中配置，调整规则上下线不需改代码、不需重新发版。"),
        ("统一模型", "约 80% 的规则套用 “A 主项 + B 相伴项 (+ C 三元约束) + 时间窗 + 限值” 这套结构，覆盖了重复收费、超量、超频、配伍等多类违规。"),
        ("事前与事中分流", "规则可配置为 “医嘱开立前实时拦截” 或 “夜间批量复核”，平衡前端体验与覆盖深度。"),
        ("适用性预筛", "执行前先按 “有效期 / 门诊或住院 / 科室 / 人群类型” 等条件筛掉无关规则，避免无效计算。"),
        ("可追溯", "每条违规都记录到 IMS_VIOLATION 表 (规则号、明细 ID、等级、提示信息、医保金额)，供后续人工复核和申诉。"),
    ]
    for title, body in points:
        p = doc.add_paragraph(style="List Bullet")
        r = p.add_run(f"{title}: ")
        r.bold = True
        r.font.color.rgb = COLOR_PRIMARY
        r.font.size = Pt(11)
        r.font.name = "微软雅黑"
        r2 = p.add_run(body)
        r2.font.size = Pt(11)
        r2.font.name = "微软雅黑"

    # =============== 结尾 ===============
    add_para(doc, "")
    add_para(doc, "—— 文档结束 ——", color=COLOR_SUBTLE)

    # 输出
    out_dir = Path(__file__).resolve().parent.parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "规则引擎说明_医学专家版.docx"
    doc.save(out_path)
    print(f"[ok] saved: {out_path}")


if __name__ == "__main__":
    build()
