#!/usr/bin/env python3
"""data_hub_filled 回填器: sy(主院) + szx/song(外部院) → Data_Hub TB_* CSV.

用法:
    uv run python scripts/build_data_hub_filled.py            # 全量
    uv run python scripts/build_data_hub_filled.py --only fee # 只跑某域 (fee/diag/surg/face/visit/doc/lab/exam/patient/dic/yb)

产出 (默认 /Users/shane/26er/Scriv/data_hub_filled/):
    TB_*.csv (utf-8-sig)  + EXT_*.csv (扩展表)
    _report.md            逐表行数 / PK 唯一性 / NOT NULL 兜底计数 / 截断计数
    _ext_tables.sql       3 张扩展表 DDL (回传 142 前先建)
    _dictionaries/        MXFYLB 费用类别码表 + 院区码映射 + WSLB 文书类别码表

设计依据: /Users/shane/26er/Scriv/data_hub_关系映射.md (v2026-07-02)
键规则 (映射总纲 §三): YLJGYQDM 走院区码字典; JZLSH=裸患者号;
SFMXID/ZYZDLSH/SSMXLSH/JYZBLSH 均按 "{JZLSH}-{源号}[-{序}]" 合成保唯一。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

JAVERT = Path("/Users/shane/26er/Javert")
SCRIV = Path("/Users/shane/26er/Scriv")
OUT_DIR = SCRIV / "data_hub_filled"
SCHEMA_JSON = SCRIV / "data_hub_schema.json"
SYJBK_MAP_JSON = SCRIV / "syjbk_rbasy_mapping.json"  # r_basy 233列→SYJBK 映射 (agent 产出)

# 院区码映射: YLJGYQDM varchar(8) 装不下 12 位机构码 (映射总纲 风险③)
YQDM_MAP = {
    "H31010600042": "0001",  # sy 主院
    "H424101075": "0002",    # 黑龙江 OCR 病例 (195150)
    "42506084200": "0003",   # szx/song 外部院
}
SENT_DT = "1900-01-01 00:00:00"

# 统一明细费用类别 2 位码 (自定码表, 输出到 _dictionaries)
MXFYLB_DICT = {
    "01": "床位", "02": "诊查", "03": "检查", "04": "化验", "05": "治疗",
    "06": "手术(含麻醉操作)", "07": "麻醉", "08": "护理", "09": "卫生材料",
    "10": "西药", "11": "中成药", "12": "中药饮片/草药", "13": "输血/血制品",
    "14": "输氧", "15": "饮食", "16": "专护", "17": "CT", "18": "拍片",
    "19": "透视", "20": "病理", "99": "其他",
}
SY_CAT2CODE = {
    "床位": "01", "诊查": "02", "检查": "03", "化验": "04", "治疗": "05",
    "手术": "06", "麻醉": "07", "护理": "08", "材料": "09", "西药": "10",
    "中药": "11", "草药": "12", "输血": "13", "输氧": "14", "饮食": "15",
    "专护": "16", "CT": "17", "拍片": "18", "透视": "19", "病理": "20", "其他": "99",
}
# szx 数值码含义由各类别 top 项目名反推 (2026-07-02 实测)
SZX_CODE2CODE = {
    "1": "05", "2": "06", "3": "03", "4": "04", "5": "17", "6": "19",
    "7": "01", "8": "02", "9": "08", "10": "13", "11": "14", "12": "10",
    "13": "11", "14": "12", "19": "99", "20": "15", "21": "09",
}

# 文书类别码 (扩展表 TB_CIS_MEDICAL_DOCUMENT.WSLB, 输出到 _dictionaries)
WSLB_DICT = {
    "01": "病案首页", "02": "入院记录", "03": "病程记录", "04": "手术文书",
    "05": "出院记录/小结", "06": "知情同意/告知", "07": "评估/筛查表",
    "08": "核查/清点/交接表", "09": "麻醉文书", "10": "医嘱/申请/会诊",
    "11": "检查检验报告", "12": "护理文书", "99": "其他",
}
# 顺序敏感: 前面的规则先命中
WSLB_RULES = [
    ("07", r"评估|筛查|Braden|NRS|跌倒|压疮|疼痛|护理分级"),
    ("08", r"核查|清点|交接"),
    ("09", r"麻醉"),
    ("06", r"知情|同意书|告知|委托|授权"),
    ("05", r"出院小结|出院记录"),
    ("04", r"手术记录|术前讨论|术前小结|手术经过|多学科讨论"),
    ("02", r"入院记录"),
    ("01", r"病案首页"),
    ("10", r"医嘱|申请单|答复单|会诊"),
    ("12", r"护理记录|护理单"),
    ("11", r"报告单|检验报告|检查报告"),
    ("03", r"病程|查房|小结"),
]

SCHEMA = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))


# ────────────────────────── 基础设施 ──────────────────────────

def parse_dt(s: pd.Series, dayfirst: bool = True) -> pd.Series:
    """任意格式日期列 → 'YYYY-MM-DD HH:MM:SS' 字符串, 解析失败 → ''.

    年在前 (ISO YYYY-MM-DD) 的行无歧义, 绝不能吃 dayfirst (否则月日互换 —
    v0.10.1 斜杠年在前 bug 的镜像); 仅剩余行按 dayfirst 解析.
    """
    s2 = s.fillna("").astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s2.index, dtype="datetime64[ns]")
    # 纯时间无日期 (shi_ss 手术时间全是 '00:00:00', szx create_time '09:28:05.8') —
    # to_datetime 会捏造"今天"的日期, 必须当无值处理
    s2 = s2.where(~s2.str.match(r"^\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?$"), "")
    iso = s2.str.match(r"^\d{4}[-/年]")
    if iso.any():
        out.loc[iso] = pd.to_datetime(s2[iso], errors="coerce", format="mixed")
    rem = ~iso & (s2 != "")
    if rem.any():
        out.loc[rem] = pd.to_datetime(s2[rem], dayfirst=dayfirst, errors="coerce", format="mixed")
    res = out.dt.strftime("%Y-%m-%d %H:%M:%S")
    return res.fillna("")


def ymd(iso: pd.Series) -> pd.Series:
    """ISO datetime 字符串列 → YYYYMMDD ('' 保持 '')"""
    return iso.str.replace("-", "", regex=False).str[:8]


def ryrq16(iso: pd.Series) -> pd.Series:
    """ISO → SYJBK RYRQ/CYRQ 的 varchar(16) 'YYYYMMDDHH:MM:SS'"""
    return (iso.str[:10].str.replace("-", "", regex=False) + iso.str[11:19]).where(iso != "", "")


_VC_RE = re.compile(r"varchar\((\d+)\)")


class Emitter:
    """按 DDL 生成完整列 + NOT NULL 兜底 + varchar 字节截断 + PK 自检, 统计进报告."""

    def __init__(self) -> None:
        self.report: list[str] = []
        self.tables: dict[str, pd.DataFrame] = {}

    def emit(self, table: str, data: dict[str, pd.Series], n: int, source_note: str = "") -> None:
        meta = SCHEMA[table]
        cols, fills, truncs = {}, [], []
        for c in meta["columns"]:
            name, typ, nullable = c["name"], c["type"], c["nullable"]
            if name in data:
                ser = data[name].astype(str).replace({"nan": "", "None": "", "NaT": ""}).fillna("")
            else:
                ser = pd.Series([""] * n, dtype=str)
            dm = re.match(r"decimal\((\d+),(\d+)\)", typ)
            if dm and name in data:
                # 浮点聚合残差会出科学计数法 ('-4.44e-16'), decimal 列按声明 scale 归一
                scale = int(dm.group(2))
                num = pd.to_numeric(ser.where(ser != "", None), errors="coerce").round(scale)
                ser = num.map(lambda x: "" if pd.isna(x) else f"{x:.{scale}f}")
            if not nullable:
                if typ.startswith("datetime"):
                    default = SENT_DT
                elif typ.startswith(("decimal", "numeric", "int", "bigint", "float")):
                    default = "0"
                else:
                    default = "-"
                empty = ser == ""
                if empty.any():
                    fills.append(f"{name}({int(empty.sum())})")
                    ser = ser.where(~empty, default)
            m = _VC_RE.match(typ)
            if m:
                lim = int(m.group(1))
                maxchars = int(ser.str.len().max() or 0)
                if maxchars * 2 > lim:  # 快路径: 全列字符数×2 不超限则必不超字节限
                    over = ser.str.encode("gbk", errors="replace").str.len() > lim
                    if over.any():
                        truncs.append(f"{name}({int(over.sum())})")
                        ser = ser.copy()
                        ser.loc[over] = ser.loc[over].map(lambda v: _trunc_gbk(v, lim))
            cols[name] = ser.values
        df = pd.DataFrame(cols)
        pk = meta["pk"]
        pk_ok = "无PK"
        if pk:
            dup = int(df[pk].apply(lambda c: c.str.upper()).duplicated().sum())
            pk_ok = "✓唯一" if dup == 0 else f"✗重复{dup}行"
        if table in self.tables:
            df = pd.concat([self.tables[table], df], ignore_index=True)
            if pk:
                dup = int(df[pk].apply(lambda c: c.str.upper()).duplicated().sum())
                pk_ok = "✓唯一" if dup == 0 else f"✗重复{dup}行(合并后)"
        self.tables[table] = df
        self.report.append(
            f"| {table} | {source_note} | {n} | {pk_ok} | "
            f"{'; '.join(fills[:8]) or '—'} | {'; '.join(truncs[:5]) or '—'} |"
        )

    def emit_ext(self, table: str, df: pd.DataFrame, source_note: str = "") -> None:
        """扩展表 (无 DDL 约束), 原样落盘."""
        if table in self.tables:
            df = pd.concat([self.tables[table], df], ignore_index=True)
        self.tables[table] = df
        self.report.append(f"| {table} (EXT) | {source_note} | {len(df)} | — | — | — |")


def _trunc_gbk(v: str, nbytes: int) -> str:
    b = v.encode("gbk", errors="replace")[:nbytes]
    return b.decode("gbk", errors="ignore")


def dedup_suffix(keys: pd.Series) -> pd.Series:
    """同值第 2 次起加 -1/-2 后缀保唯一."""
    cc = keys.groupby(keys).cumcount()
    return keys.where(cc == 0, keys + "-" + cc.astype(str))


def classify_wslb(names: pd.Series) -> pd.Series:
    out = pd.Series(["99"] * len(names), index=names.index)
    remaining = names.fillna("")
    done = pd.Series(False, index=names.index)
    for code, pat in WSLB_RULES:
        hit = ~done & remaining.str.contains(pat, regex=True, na=False)
        out[hit] = code
        done |= hit
    return out


# ────────────────────────── 数据装载 ──────────────────────────

def load_sy():
    fee = pd.read_csv(JAVERT / "data/shi_fee.csv", dtype=str, low_memory=False)
    fee = fee[fee["bah"].notna()].copy()  # 1 行全空 junk (风险⑪)
    fee["pid"] = fee["bah"].str.split("-").str[-1].str.strip().str.upper()
    fee["yq"] = fee["hospital"].map(YQDM_MAP).fillna("0009")
    # 日期: 主院 dayfirst, H424101075 (OCR 病例 73 行) 是 M/D/Y (风险⑩)
    main = fee["hospital"] != "H424101075"
    fee.loc[main, "dt"] = parse_dt(fee.loc[main, "fee_ocur_time"], dayfirst=True)
    fee.loc[~main, "dt"] = parse_dt(fee.loc[~main, "fee_ocur_time"], dayfirst=False)

    zd = pd.read_excel(JAVERT / "data/shi_zd.xls", dtype=str)
    zd = zd.apply(lambda col: col.str.replace(r"\.0$", "", regex=True))  # xlrd 数字列尾 .0
    zd["pid"] = zd["ba_id"].str.split("-").str[-1].str.strip().str.upper()
    zd["yq"] = zd["ba_id"].str.split("-").str[0].map(YQDM_MAP).fillna("0001")
    ss = pd.read_excel(JAVERT / "data/shi_ss.xls", dtype=str)
    ss = ss.apply(lambda col: col.str.replace(r"\.0$", "", regex=True))
    ss["pid"] = ss["ba_id"].str.split("-").str[-1].str.strip().str.upper()
    ss["yq"] = ss["ba_id"].str.split("-").str[0].map(YQDM_MAP).fillna("0001")

    notes = pd.read_csv(JAVERT / "data/case_notes.csv", dtype=str, low_memory=False)
    notes["pid"] = notes["住院号"].str.strip().str.upper()

    lab = pd.read_csv(JAVERT / "data/sy_检验.csv", dtype=str, low_memory=False)
    lab["pid"] = lab["zyh"].fillna("").str.strip().str.upper()
    lab = lab[(lab["pid"] != "") & (lab["rpt_itemname"].fillna("").str.strip().str.replace("　", "") != "")].copy()

    exam = pd.read_csv(JAVERT / "data/sy_patient_examination.csv", dtype=str, low_memory=False)
    exam = exam[exam["checkType"].fillna("") != "checkType"].copy()  # 内嵌表头行
    exam["pid"] = exam["zyh"].fillna("").str.strip().str.upper()
    exam = exam[exam["pid"] != ""].copy()

    # 患者三要素 (姓名/性别/年龄) 从检验表反推 — 覆盖 2639/3063 (风险④)
    pinfo = (
        lab[lab["patientName"].notna()]
        .groupby("pid")
        .agg(name=("patientName", "first"), sex=("sex", "first"), age=("age", "first"),
             yydah=("patientId", "first"), cert=("certificateNo", "first"))
    )
    # 住院时间近似: 费用跨度 (映射总纲: regex 覆盖率不稳, 费用跨度 100% 覆盖费用患者)
    span = fee[fee["dt"] != ""].groupby("pid")["dt"].agg(["min", "max"])
    return dict(fee=fee, zd=zd, ss=ss, notes=notes, lab=lab, exam=exam, pinfo=pinfo, span=span)


def load_szx():
    fee = pd.read_csv(JAVERT / "data/song/r_fee.csv", dtype=str, low_memory=False)
    fee = fee[fee["bah"].notna()].copy()
    fee["pid"] = fee["bah"].str.split("-").str[-1].str.strip().str.upper()
    fee["yq"] = fee["hospital"].map(YQDM_MAP).fillna("0003")
    fee["dt"] = parse_dt(fee["fee_ocur_time"], dayfirst=True)

    zd = pd.read_csv(JAVERT / "data/song/r_basy_zd.csv", dtype=str, low_memory=False)
    zd["pid"] = zd["ba_id"].str.split("-").str[-1].str.strip().str.upper()
    zd["yq"] = "0003"
    ss = pd.read_csv(JAVERT / "data/song/r_basy_ss.csv", dtype=str, low_memory=False)
    ss["pid"] = ss["ba_id"].str.split("-").str[-1].str.strip().str.upper()
    ss["yq"] = "0003"

    basy = pd.read_csv(JAVERT / "data/song/r_basy.csv", dtype=str, low_memory=False)
    basy["pid"] = basy["psn_no"].str.strip()
    bridge = dict(zip(basy["medcasno"].str.strip(), basy["pid"]))  # medcasno → psn_no

    doc = pd.read_csv(JAVERT / "data/song/szx_doc.csv", dtype=str, low_memory=False)
    doc["pid"] = doc["source_inpat_no"].fillna("").str.strip().map(bridge)

    lab = pd.read_csv(JAVERT / "data/song/lab_results.csv", dtype=str, low_memory=False)
    lab["pid"] = lab["zyh"].fillna("").str.strip().str.upper().map(bridge)
    return dict(fee=fee, zd=zd, ss=ss, basy=basy, doc=doc, lab=lab, bridge=bridge)


# ────────────────────────── 各域 builder ──────────────────────────

def build_fee(em: Emitter, src, tag: str, cat_mapper) -> None:
    fee = src["fee"]
    n = len(fee)
    amt = pd.to_numeric(fee["det_item_fee_sumamt"], errors="coerce").fillna(0)
    cnt = pd.to_numeric(fee["cnt"], errors="coerce").fillna(0)
    stfbz = pd.Series("1", index=fee.index).where(~((amt < 0) | (cnt < 0)), "2")
    sfmxid = dedup_suffix(fee["pid"] + "-" + fee["feedetl_sn"].fillna("0"))
    em.emit("TB_HIS_ZY_FEE_DETAIL_FS", {
        "YLJGYQDM": fee["yq"],
        "SFMXID": sfmxid,
        "STFBZ": stfbz,
        "JZLSH": fee["pid"],
        "MXFYLB": cat_mapper(fee),
        "FYFSSJ": fee["dt"],
        "MXXMBM": fee["medins_list_codg"],
        "MXXMBMYB": fee["med_list_codg"],
        "MXXMMC": fee["medins_list_name"],
        "MXXMDJ": fee["pric"],
        "MXXMSL": cnt.abs().astype(str),
        "MXXMJE": amt.abs().astype(str),
        "XGBZ": pd.Series("1", index=fee.index),
    }, n, tag)
    # 扩展表: 医保分解 + 科室/医生 + 药品属性 (映射总纲 扩容③, Javert 审计刚需字段 1:1 挂 SFMXID)
    ext_cols = ["chrgitm_lv", "list_type", "med_list_codg", "medins_list_codg", "prodname", "spec", "dosform_name",
                "bilg_dept_codg", "bilg_dept_name", "bilg_dr_codg", "bilg_dr_name",
                "acord_dept_codg", "acord_dept_name", "orders_dr_code", "orders_dr_name",
                "dscg_tkdrug_flag", "fee_type", "medins_chrgitm_type", "hosp_appr_flag",
                "pric_uplmt_amt", "selfpay_prop", "fulamt_ownpay_amt", "overlmt_amt",
                "preselfpay_amt", "inscp_scp_amt"]
    ext = pd.DataFrame({"YLJGYQDM": fee["yq"].values, "SFMXID": sfmxid.values, "JZLSH": fee["pid"].values})
    for c in ext_cols:
        ext[c.upper()] = fee[c].fillna("").values if c in fee.columns else ""
    em.emit_ext("TB_HIS_ZY_FEE_DETAIL_EXT", ext, tag)


def build_diag(em: Emitter, src, tag: str) -> None:
    zd = src["zd"]
    zd = zd[zd["pid"].notna() & (zd["pid"] != "")].copy()
    n = len(zd)
    seq = (zd.groupby("pid").cumcount() + 1).astype(str)
    dt_iso = parse_dt(zd["create_time"], dayfirst=True)
    # 临床版编码优先 (风险⑥: 两套编码体系不混写), 空回退医保版
    zdbm = zd["inhosp_diag_code"].fillna("").where(zd["inhosp_diag_code"].fillna("") != "", zd["diag_code"].fillna(""))
    zdsm = zd["inhosp_diag_name"].fillna("").where(zd["inhosp_diag_name"].fillna("") != "", zd["diag_name"].fillna(""))
    em.emit("TB_IH_DIAGNOSIS_DETAIL", {
        "YLJGYQDM": zd["yq"],
        "ZYZDLSH": dedup_suffix(zd["pid"] + "-D" + seq),
        "JZLSH": zd["pid"],
        "MZZYBZ": pd.Series("2", index=zd.index),
        "ZDLXQF": zd["tcd_flag"].fillna("0").map(lambda v: "2" if v in ("1", "1.0") else "1"),
        "ZDLB": zd["diag_type"],
        "ZDSJ": dt_iso,
        "ZDBM": zdbm,
        "ZDSM": zdsm,
        "CYZDBZ": zd["maindiag_flag"].map(lambda v: "1" if v in ("1", "1.0") else "2"),
        "CYQKBM": zd["dscg_trt_rslt_code"],
        "RYBQ": zd["adm_dise_cond_name"] if "adm_dise_cond_name" in zd.columns else None,
        "XGBZ": pd.Series("1", index=zd.index),  # vali_flag≠撤销语义, 不映 XGBZ (风险⑧)
    }, n, tag)
    # 首页其他诊断投影: diag_type∈{1,2} 且非主诊断
    other = zd[(zd["diag_type"].isin(["1", "2"])) & (~zd["maindiag_flag"].isin(["1", "1.0"]))].copy()
    zdxh = (other.groupby("pid").cumcount() + 1).astype(str)
    em.emit("TB_BA_SYZDK", {
        "YLJGYQDM": other["yq"],
        "SYXH": other["pid"],
        "ZDXH": zdxh,
        "YLZZJGDM": other["ba_id"].str.split("-").str[0],
        "ZDDM": other["inhosp_diag_code"].fillna("").where(other["inhosp_diag_code"].fillna("") != "", other["diag_code"].fillna("")),
        "ZDMC": other["inhosp_diag_name"].fillna("").where(other["inhosp_diag_name"].fillna("") != "", other["diag_name"].fillna("")),
        "RYBQ": other["adm_cond"] if "adm_cond" in other.columns else None,
        "ZGQK": other["dscg_trt_rslt_code"],
        "FYDM": other["yq"],
        "GXRQ": parse_dt(other["create_time"], dayfirst=True),
        "GDRQ": parse_dt(other["create_time"], dayfirst=True).str[:10],
        "GDBBH": pd.Series("1", index=other.index),
    }, len(other), tag)
    # 医保侧参保人诊断 (医保版编码, 空码撤销行剔除)
    yb = zd[zd["diag_code"].fillna("") != ""].copy()
    ybseq = (yb.groupby("pid").cumcount() + 1).astype(str)
    em.emit("TB_YB_JLC_CBRZDXX", {
        "XH": dedup_suffix(yb["pid"] + "-Y" + ybseq),
        "LSH": yb["pid"],
        "ZDNO": yb["diag_code"],
        "ZDMC": yb["diag_name"],
        "XGBZ": pd.Series("1", index=yb.index),
    }, len(yb), tag)


def build_surg(em: Emitter, src, tag: str) -> None:
    ss = src["ss"]
    ss = ss[ss["pid"].notna() & (ss["pid"] != "")].copy()
    n = len(ss)
    seq = ss["oprn_oprt_sn"].fillna("")
    fallback = (ss.groupby("pid").cumcount() + 1).astype(str)
    ssxh = seq.where(seq != "", fallback)
    dup = (ss["pid"] + "|" + ssxh).to_frame(0).groupby(0).cumcount()
    ssxh = ssxh.where(dup == 0, ssxh + "-" + dup.astype(str))  # 同患者 sn 重复 → 加后缀保 PK
    date_iso = parse_dt(ss["oprn_oprt_date"], dayfirst=True)
    beg_iso = parse_dt(ss["oprn_oprt_begntime"], dayfirst=True)
    end_iso = parse_dt(ss["oprn_oprt_endtime"], dayfirst=True)
    anst_beg = parse_dt(ss["anst_begntime"], dayfirst=True)
    anst_end = parse_dt(ss["anst_endtime"], dayfirst=True)
    em.emit("TB_BA_SYSSK", {
        "YLJGYQDM": ss["yq"],
        "SYXH": ss["pid"],
        "SSXH": ssxh,
        "YLZZJGDM": ss["ba_id"].str.split("-").str[0],
        "SSRQ": ymd(date_iso),
        "SSDM": ss["oprn_oprt_code"],
        "SSJB": ss["oprn_lv_code"].fillna("").where(ss["oprn_lv_code"].fillna("") != "", ss["oprn_lv_name"].fillna("")),
        "SSMC": ss["oprn_oprt_name"],
        "SSYS": ss["oper_dr_name"],
        "SSYZ": ss["asit_1_name"],
        "SSEZ": ss["asit_name2"],
        "YHLB": ss["sinc_heal_lv"],
        "MZFS": ss["anst_mtd_name"].fillna("").where(ss["anst_mtd_name"].fillna("") != "", ss["anst_way"].fillna("")),
        "MZYS": ss["anst_dr_name"],  # sy 侧与术者同名脏数据, 见报告
        "MZKSSJ": anst_beg,
        "MZJSSJ": anst_end,
        "FYDM": ss["yq"],
        "GXRQ": parse_dt(ss["create_time"], dayfirst=True),
        "SSCXSJ": ss["oprn_con_time"],
        "SFZYSS": ss["main_oprn_flag"].map(lambda v: "1" if v in ("1", "1.0") else "0"),
        "GDRQ": parse_dt(ss["create_time"], dayfirst=True).str[:10],
        "GDBBH": pd.Series("1", index=ss.index),
    }, n, tag)
    # 扩展: 医保版手术双码 + 医师编码 + 部位 + 取消标志 (映射总纲 扩容①, DRG/DIP 刚需)
    ext = pd.DataFrame({
        "YLJGYQDM": ss["yq"].values, "SYXH": ss["pid"].values, "SSXH": ssxh.values,
        "HISSDM": ss["hi_oprn_oprt_code"].fillna("").values,
        "HISSMC": ss["hi_oprn_oprt_name"].fillna("").values,
        "SSBW": ss["oprn_oper_part"].fillna("").values,
        "SSBWDM": ss["oprn_oper_part_code"].fillna("").values,
        "SSYSBM": ss["oper_dr_code"].fillna("").values,
        "MZYSBM": ss["anst_dr_code"].fillna("").values,
        "QXSSBZ": ss["canc_oprn_flag"].fillna("").values,
        "SSKSSJ": beg_iso.values, "SSJSSJ": end_iso.values,
        "MZKSSJ": anst_beg.values, "MZJSSJ": anst_end.values,
    })
    em.emit_ext("TB_BA_SYSSK_EXT", ext, tag)
    em.emit("TB_OPRATION_DETAIL", {
        "YLJGYQDM": ss["yq"],
        "SSMXLSH": dedup_suffix(ss["pid"] + "-S" + ssxh),
        "JZLSH": ss["pid"],
        "MZZYBZ": pd.Series("2", index=ss.index),
        "SSJB": ss["oprn_lv_code"],
        "SSCZBM": ss["oprn_oprt_code"],
        "SSCZMC": ss["oprn_oprt_name"],
        "SSKSSJ": beg_iso.where(beg_iso != "", date_iso),
        "SSJSSJ": end_iso,
        "SXYHRYID": ss["oper_dr_code"],
        "SXYHRYXM": ss["oper_dr_name"],
        "SXZ1YHRYXM": ss["asit_1_name"],
        "SXZ2YHRYXM": ss["asit_name2"],
        "MZYHRYID": ss["anst_dr_code"],
        "MZYHRYXM": ss["anst_dr_name"],
        "MZFS": ss["anst_mtd_name"],
        "QKYHDJ": ss["sinc_heal_lv"],
        "SSXH": ssxh,
        "ZCBZ": ss["main_oprn_flag"].map(lambda v: "1" if v in ("1", "1.0") else "2"),
        "XGBZ": pd.Series("1", index=ss.index),
    }, n, tag)


def build_face_sy(em: Emitter, sy) -> None:
    """sy 侧 SYJBK 最小行: 键 + 主诊断/病理/损伤中毒 + 费用聚合 + 计数 + 住院跨度."""
    zd, ss, fee, span, pinfo = sy["zd"], sy["ss"], sy["fee"], sy["span"], sy["pinfo"]
    pids = sorted(set(zd["pid"].dropna()) | set(ss["pid"].dropna()))
    base = pd.DataFrame({"pid": pids})
    base["yq"] = base["pid"].map(dict(zip(zd["pid"], zd["yq"]))).fillna("0001")
    base["org"] = base["pid"].map(dict(zip(zd["pid"], zd["ba_id"].str.split("-").str[0]))).fillna("H31010600042")

    main = zd[zd["maindiag_flag"].isin(["1", "1.0"])].drop_duplicates("pid").set_index("pid")
    path = zd[zd["diag_type"] == "6"].drop_duplicates("pid").set_index("pid")   # 病理
    injury = zd[zd["diag_type"] == "5"].drop_duplicates("pid").set_index("pid")  # 损伤中毒
    mss = ss[ss["main_oprn_flag"].isin(["1", "1.0"])].drop_duplicates("pid").set_index("pid")

    fee_ok = fee[fee["dt"] != ""].copy()
    fee_ok["amt"] = pd.to_numeric(fee_ok["det_item_fee_sumamt"], errors="coerce").fillna(0)
    total = fee_ok.groupby("pid")["amt"].sum()
    bycat = fee_ok.groupby(["pid", "medins_chrgitm_type"])["amt"].sum().unstack(fill_value=0)
    CAT2COL = {"化验": "SYSZDF", "病理": "BLZDF", "西药": "XYF", "中药": "CHENGYF",
               "草药": "CAOYF", "输血": "SXF", "手术": "SSF", "麻醉": "MZF",
               "护理": "HLF", "材料": "ZLYYCXCLF", "治疗": "FSSZLXMF"}
    img = bycat.reindex(columns=["CT", "拍片", "透视", "检查"], fill_value=0).sum(axis=1)
    rest = bycat.reindex(columns=["床位", "诊查", "饮食", "专护", "输氧", "其他"], fill_value=0).sum(axis=1)

    ry = base["pid"].map(span["min"]).fillna("")
    cy = base["pid"].map(span["max"]).fillna("")
    days = (pd.to_datetime(cy, errors="coerce") - pd.to_datetime(ry, errors="coerce")).dt.days.fillna(0).astype(int) + 1

    data = {
        "YLJGYQDM": base["yq"],
        "SYXH": base["pid"],
        "BAH": base["pid"],
        "YLZZJGDM": base["org"],
        "BRXM": base["pid"].map(pinfo["name"]),
        "BRXB": base["pid"].map(pinfo["sex"]),
        "XSNL": base["pid"].map(pd.to_numeric(pinfo["age"], errors="coerce")),
        "RYCS": pd.Series(1, index=base.index),
        "RYRQ": ryrq16(ry),
        "CYRQ": ryrq16(cy),
        "ZYTS": days.astype(str),
        "ZYZD": base["pid"].map(main["diag_code"]),
        "RYSBQ": base["pid"].map(main["adm_cond_type"]) if "adm_cond_type" in main.columns else None,
        "ZLJG": base["pid"].map(main["dscg_trt_rslt_code"]),
        "BLZD": base["pid"].map(path["inhosp_diag_code"]),
        "BLZDMC": base["pid"].map(path["inhosp_diag_name"]),
        "SSZD": base["pid"].map(injury["inhosp_diag_code"]),
        "SSWYMC": base["pid"].map(injury["inhosp_diag_name"]),
        "ZFY": base["pid"].map(total),
    }
    for cat, col in CAT2COL.items():
        if cat in bycat.columns:
            data[col] = base["pid"].map(bycat[cat])
    data["YXXZDF"] = base["pid"].map(img)
    data["QTF2"] = base["pid"].map(rest)
    data = {k: v for k, v in data.items() if v is not None}
    em.emit("TB_BA_SYJBK", data, len(base), "sy(shi_zd/ss/fee 拼装)")


def build_face_szx(em: Emitter, szx) -> None:
    """szx 侧 SYJBK: r_basy 233 列经 agent 映射直灌."""
    if not SYJBK_MAP_JSON.exists():
        em.report.append("| TB_BA_SYJBK | szx(r_basy) | 0 | ⚠跳过 | 缺 syjbk_rbasy_mapping.json | — |")
        return
    mapping = json.loads(SYJBK_MAP_JSON.read_text(encoding="utf-8"))
    basy = szx["basy"]
    n = len(basy)
    date_cols_ryrq = {"RYRQ", "CYRQ"}

    def numeric_pick(a: str, b: str) -> pd.Series:
        """两列疑互换 (naty/naty_name, medcas_qlt_*): 取值为纯数字的那列."""
        sa, sb = basy[a].fillna(""), basy[b].fillna("")
        return sa.where(sa.str.match(r"^\d+$"), sb.where(sb.str.match(r"^\d+$"), ""))

    # r_basy 无主诊断列 (unmapped_syjbk 含 ZYZD), 从 r_basy_zd maindiag 补
    zd = szx["zd"]
    main = zd[zd["maindiag_flag"].isin(["1", "1.0"])].drop_duplicates("pid").set_index("pid")
    data: dict[str, pd.Series] = {
        "YLJGYQDM": pd.Series("0003", index=basy.index),
        "SYXH": basy["pid"],
        "BAH": basy["medcasno"],  # szx 真病案号; SYXH 保持 psn_no 与 SYZDK/SYSSK 一致
        "ZYZD": basy["pid"].map(main["diag_code"]),
        "MZDM": numeric_pick("naty", "naty_name"),
        "BAZL": numeric_pick("medcas_qlt_code", "medcas_qlt_name"),
    }
    for m in mapping.get("mappings", []):
        tgt, src_col = m["syjbk_col"], m.get("r_basy_col")
        if not src_col or src_col not in basy.columns or m.get("confidence") == "low":
            continue
        if tgt in data:
            continue
        ser = basy[src_col].fillna("")
        tr = (m.get("transform") or "")
        if tgt in date_cols_ryrq:
            ser = ryrq16(parse_dt(ser, dayfirst=True))
        elif "YYYYMMDD" in tr or tgt in {"CSNY"}:
            ser = ymd(parse_dt(ser, dayfirst=True))
        elif re.search(r"日期|时间|datetime", tr) or tgt.endswith(("RQ", "SJ")):
            meta_cols = {c["name"]: c for c in SCHEMA["TB_BA_SYJBK"]["columns"]}
            if tgt in meta_cols and meta_cols[tgt]["type"].startswith("datetime"):
                ser = parse_dt(ser, dayfirst=True)
        data[tgt] = ser
    em.emit("TB_BA_SYJBK", data, n, f"szx(r_basy 映射 {len(data)}列)")


def build_visit(em: Emitter, sy, szx) -> None:
    # sy: 键桥 + 费用跨度近似入出院时间
    pids = sorted(set(sy["notes"]["pid"].dropna()) | set(sy["fee"]["pid"].dropna()))
    base = pd.DataFrame({"pid": pids})
    yq_map = dict(zip(sy["fee"]["pid"], sy["fee"]["yq"]))
    ry = base["pid"].map(sy["span"]["min"]).fillna("")
    cy = base["pid"].map(sy["span"]["max"]).fillna("")
    em.emit("TB_YL_ZY_MEDICAL_RECORD", {
        "YLJGYQDM": base["pid"].map(yq_map).fillna("0001"),
        "JZLSH": base["pid"],
        "CISID": base["pid"],
        "BAH": base["pid"],
        "HZXM": base["pid"].map(sy["pinfo"]["name"]),
        "JZLX": pd.Series("2", index=base.index),
        "RYSJ": ry,
        "CYSJ": cy,
        "XGBZ": pd.Series("1", index=base.index),
    }, len(base), "sy(费用跨度近似)")
    em.emit("TB_HIS_ZY_ADM_REG", {
        "YLJGYQDM": base["pid"].map(yq_map).fillna("0001"),
        "JZLSH": base["pid"],
        "RYSJ": ry,
        "LGBZ": pd.Series("0", index=base.index),
        "XGBZ": pd.Series("1", index=base.index),
    }, len(base), "sy")
    # szx: r_basy 真实入出院
    basy = szx["basy"]
    ry2 = parse_dt(basy["adm_date"], dayfirst=True)
    cy2 = parse_dt(basy["dscg_date"], dayfirst=True)
    em.emit("TB_YL_ZY_MEDICAL_RECORD", {
        "YLJGYQDM": pd.Series("0003", index=basy.index),
        "JZLSH": basy["pid"],
        "CISID": basy["medcasno"],
        "BAH": basy["pid"],
        "HZXM": basy["psn_name"],
        "JZLX": pd.Series("2", index=basy.index),
        "JZKSBM": basy["hos_adm_caty_code"],
        "JZKSMC": basy["hos_adm_caty_name"],
        "CYKSBM": basy["hos_dscg_caty_code"],
        "CYKSMC": basy["hos_dscg_caty_name"],
        "RYSJ": ry2,
        "CYSJ": cy2,
        "XGBZ": pd.Series("1", index=basy.index),
    }, len(basy), "szx(r_basy)")
    em.emit("TB_HIS_ZY_ADM_REG", {
        "YLJGYQDM": pd.Series("0003", index=basy.index),
        "JZLSH": basy["pid"],
        "RYKS": basy["hos_adm_caty_name"],
        "RYSJ": ry2,
        "LGBZ": pd.Series("0", index=basy.index),
        "XGBZ": pd.Series("1", index=basy.index),
    }, len(basy), "szx")


DISCHARGE_SECTION2COL = {
    "入院诊断": "RYZD", "出院诊断": "CYZD", "入院时主要症状和体征": "RYZZTZ",
    "出院时症状和体征": "CYQKMS", "出院医嘱": "CYYZ", "治疗结果": "ZLJGSM",
}


def build_discharge(em: Emitter, sy) -> None:
    notes = sy["notes"]
    dis = notes[notes["阶段"] == "出院小结"].copy()
    if dis.empty:
        return
    dis["内容"] = dis["内容"].fillna("")
    piv = dis.pivot_table(index="pid", columns="子阶段", values="内容",
                          aggfunc=lambda s: "\n".join(x for x in s if x)).fillna("")
    piv = piv.reset_index()

    def col(name: str) -> pd.Series:
        return piv[name] if name in piv.columns else pd.Series("", index=piv.index)

    jchz = (col("主要实验室检查和特殊检查") + "\n" + col("住院时主要实验室检查和特殊检查")).str.strip()
    zlgc = (col("治疗经过") + "\n" + col("手术情况")).str.strip()
    ry = piv["pid"].map(sy["span"]["min"]).fillna("")
    cy = piv["pid"].map(sy["span"]["max"]).fillna("")
    days = (pd.to_datetime(cy, errors="coerce") - pd.to_datetime(ry, errors="coerce")).dt.days.fillna(0) + 1
    yq_map = dict(zip(sy["fee"]["pid"], sy["fee"]["yq"]))
    em.emit("TB_CIS_LEAVEHOSPITAL_SUMMARY", {
        "YLJGYQDM": piv["pid"].map(yq_map).fillna("0001"),
        "JZLSH": piv["pid"],
        "BAH": piv["pid"],
        "XM": piv["pid"].map(sy["pinfo"]["name"]),
        "BRXB": piv["pid"].map(sy["pinfo"]["sex"]).map({"男": "1", "女": "2"}),
        "BRNL": piv["pid"].map(pd.to_numeric(sy["pinfo"]["age"], errors="coerce")),
        "RYSJ": ry,
        "CYSJ": cy,
        "ZYTS": days.astype(str),
        "RYZD": col("入院诊断"),
        "CYZD": col("出院诊断"),
        "RYZZTZ": col("入院时主要症状和体征"),
        "JCHZ": jchz,
        "ZLGC": zlgc,
        "CYQKMS": col("出院时症状和体征"),
        "CYYZ": col("出院医嘱"),
        "ZLJGSM": col("治疗结果"),
        "YYZTBBT1": pd.Series("健康教育", index=piv.index).where(col("健康教育") != "", ""),
        "YYZTB1": col("健康教育"),
        "YYZTBBT2": pd.Series("病理报告", index=piv.index).where(col("病理报告") != "", ""),
        "YYZTB2": col("病理报告"),
        "XGBZ": pd.Series("1", index=piv.index),
    }, len(piv), "sy(出院小结 pivot)")


def build_docs(em: Emitter, sy, szx) -> None:
    """扩展表 TB_CIS_MEDICAL_DOCUMENT: 通用文书 (映射总纲 扩容② — search_notes 命脉)."""
    notes = sy["notes"]
    n1 = notes.copy()
    n1["JLSJ"] = parse_dt(n1["事件时间"], dayfirst=True)
    seq = (n1.groupby("pid").cumcount() + 1).astype(str)
    yq_map = dict(zip(sy["fee"]["pid"], sy["fee"]["yq"]))
    d1 = pd.DataFrame({
        "YLJGYQDM": n1["pid"].map(yq_map).fillna("0001").values,
        "JZLSH": n1["pid"].values,
        "WSLSH": (n1["pid"] + "-W" + seq).values,
        "WSLB": classify_wslb(n1["阶段"]).values,
        "WSMC": n1["阶段"].fillna("").values,
        "DLBT": n1["子阶段"].fillna("").values,
        "DLXH": n1.groupby(["pid", "阶段"]).cumcount().add(1).values,
        "JLSJ": n1["JLSJ"].values,
        "ZW": n1["内容"].fillna("").values,
        "XGBZ": "1",
    })
    em.emit_ext("TB_CIS_MEDICAL_DOCUMENT", d1, "sy(case_notes)")

    doc = szx["doc"]
    d = doc[doc["pid"].notna()].copy()
    unbridged = len(doc) - len(d)
    seq2 = (d.groupby("pid").cumcount() + 1).astype(str)
    d2 = pd.DataFrame({
        "YLJGYQDM": "0003",
        "JZLSH": d["pid"].values,
        "WSLSH": (d["pid"] + "-W" + seq2).values,
        "WSLB": classify_wslb(d["record_name"]).values,
        "WSMC": d["record_name"].fillna("").values,
        "DLBT": "",
        "DLXH": 1,
        "JLSJ": parse_dt(d["record_date"], dayfirst=True).values,
        "ZW": d["replace"].fillna("").values,
        "XGBZ": "1",
    })
    em.emit_ext("TB_CIS_MEDICAL_DOCUMENT", d2, f"szx(szx_doc, 桥失配剔除{unbridged})")


YCTS_MAP = {"正常": "1", "N": "1", "阴性": "1", "↑": "3", "H": "3", "偏高": "3",
            "↓": "4", "L": "4", "偏低": "4"}


def _lab_frames(lab: pd.DataFrame, yq: str, bgdh: pd.Series, tag: str, em: Emitter,
                extra: dict[str, pd.Series] | None = None) -> None:
    """扁平检验行 → REPORT(按报告聚合) + INDICATORS(逐行)."""
    lab = lab.copy()
    lab["BGDH"] = bgdh
    rpt_iso = parse_dt(lab["report_dt"], dayfirst=True)
    lab["BGSJ"] = rpt_iso
    lab["BGRQ"] = ymd(rpt_iso)
    extra = extra or {}

    head = lab.groupby(["pid", "BGDH"], as_index=False).first()
    n = len(head)

    def hx(col: str) -> pd.Series | None:
        return head[col] if col in head.columns else None

    cj = parse_dt(head["samplingTime"], dayfirst=True) if "samplingTime" in head.columns else pd.Series("", index=head.index)
    jy = parse_dt(head["TestDate"], dayfirst=True) if "TestDate" in head.columns else pd.Series("", index=head.index)
    data = {
        "YLJGYQDM": pd.Series(yq, index=head.index),
        "BGRQ": head["BGRQ"],
        "BGDH": head["BGDH"],
        "JZLSH": head["pid"],
        "MZZYBZ": pd.Series("2", index=head.index),
        "HZXM": hx("patientName"),
        "BRXB": hx("sex").map({"男": "1", "女": "2", "1": "1", "2": "2"}) if hx("sex") is not None else None,  # varchar(1)
        "BRNL": hx("age"),
        "SQYHRYXM": hx("inspectionDoctor"),
        "BGYHRYXM": hx("report_user") if "report_user" in head.columns else hx("trier"),
        "SHYHRYXM": hx("auditor"),
        "SQKS": hx("department"),
        "BGSJ": head["BGSJ"],
        "SQSJ": cj,
        "CJSJ": cj.where(cj != "", head["BGSJ"]),
        "JYSJ": jy.where(jy != "", cj.where(cj != "", head["BGSJ"])),  # 回退链 (风险④)
        "SHSJ": head["BGSJ"],
        "BBMC": hx("specimen"),
        "BGDLB": hx("inspectionName"),
        "XGBZ": pd.Series("1", index=head.index),
    }
    em.emit("TB_LIS_REPORT", {k: v for k, v in data.items() if v is not None}, n, tag)

    seq = (lab.groupby(["pid", "BGDH"]).cumcount() + 1).astype(str)
    flag = lab["result_flag"].fillna("") if "result_flag" in lab.columns else pd.Series("", index=lab.index)
    ycts = flag.map(YCTS_MAP).fillna("").where(flag != "", "1")  # 空 flag 视为正常
    ycts = ycts.where(ycts != "", "2")
    ind = {
        "YLJGYQDM": pd.Series(yq, index=lab.index),
        "JYZBLSH": dedup_suffix(lab["BGDH"] + "-" + seq),
        "BGDH": lab["BGDH"],
        "BGRQ": lab["BGRQ"],
        "SHSJ": lab["BGSJ"],
        "MXXMBM": lab["inspectionCode"] if "inspectionCode" in lab.columns else None,
        "MXXMMC": lab["inspectionName"] if "inspectionName" in lab.columns else None,
        "JYZBDM": lab["rpt_itemcode"],
        "JYZBMC": lab["rpt_itemname"],
        "JYZBJG": lab["result"],
        "CKZ": lab["result_ref"],
        "JLDW": lab["result_unit"],
        "YCTS": ycts,
        "DYXH": seq,
        "SHYHRYXM": lab["auditor"] if "auditor" in lab.columns else None,
        "XGBZ": pd.Series("1", index=lab.index),
    }
    em.emit("TB_LIS_INDICATORS", {k: v for k, v in ind.items() if v is not None}, len(lab), tag)


def build_lab(em: Emitter, sy, szx) -> None:
    lab = sy["lab"]
    bgdh = lab["inspectionRecordId"].fillna("")
    synth = "R" + (lab["pid"] + "|" + lab["report_dt"].fillna("") + "|" + lab["inspectionName"].fillna("")).map(
        lambda s: hashlib.md5(s.encode()).hexdigest()[:15])
    # 前缀患者号: 同一报告号会被复制到同患者的两次住院 (两个 zyh) → PK 撞 (映射总纲 风险②)
    bgdh = lab["pid"] + "-" + bgdh.where(bgdh != "", synth)
    _lab_frames(lab, "0001", bgdh, "sy(sy_检验)", em)

    slab = szx["lab"]
    s = slab[slab["pid"].notna()].copy()
    unbridged = len(slab) - len(s)
    s = s[s["rpt_itemname"].fillna("").str.strip() != ""]
    basy = szx["basy"].drop_duplicates("pid").set_index("pid")
    s["patientName"] = s["pid"].map(basy["psn_name"])  # 检验表无患者三要素, 从 r_basy 富化
    s["sex"] = s["pid"].map(basy["gend"])
    s["age"] = s["pid"].map(basy["age"])
    bgdh2 = "R" + (s["pid"] + "|" + s["report_dt"].fillna("") + "|" + s["inspectionName"].fillna("")).map(
        lambda x: hashlib.md5(x.encode()).hexdigest()[:15])
    _lab_frames(s, "0003", bgdh2, f"szx(song lab, 桥失配剔除{unbridged})", em)


def build_exam(em: Emitter, sy) -> None:
    exam = sy["exam"].copy()
    exam["uid"] = dedup_suffix(exam["id"].fillna("").where(exam["id"].fillna("") != "",
                                                           "E" + exam.groupby("pid").cumcount().astype(str)))
    jc_iso = parse_dt(exam["checkDate"], dayfirst=True)
    bg_iso = parse_dt(exam["reportDate"], dayfirst=True)
    bg_iso = bg_iso.where(bg_iso != "", jc_iso)
    name_map, sex_map = sy["pinfo"]["name"], sy["pinfo"]["sex"]
    hz = exam["patientName"].fillna("").where(exam["patientName"].fillna("") != "", exam["pid"].map(name_map))
    xb = exam["Sex"].fillna("").where(exam["Sex"].fillna("") != "", exam["pid"].map(sex_map))
    xb = xb.map({"男": "1", "女": "2"}).fillna("")  # BRXB varchar(1)
    common = {
        "YLJGYQDM": pd.Series("0001", index=exam.index),
        "INSTANCEUID": exam["uid"],
        "STUDYUID": exam["StudyUID"].fillna("").where(exam["StudyUID"].fillna("") != "", exam["uid"]),
        "JZLSH": exam["pid"],
        "MZZYBZ": pd.Series("2", index=exam.index),
        "HZXM": hz,
        "BRXB": xb,
        "PATIENTID": exam["patientId"],
        "SQDH": exam["sampleNo"],
        "KDSJ": jc_iso,
        "JCSJ": jc_iso,
        "BGSJ": bg_iso,
        "SHSJ": bg_iso,
        "EXAMTYPE": exam["checkType"],
        "JCKS": exam["department"],
        "BGRQ": ymd(bg_iso),
        "BGYHRYXM": exam["reporter"],
        "SHYHRYXM": exam["auditor"].fillna("").where(exam["auditor"].fillna("") != "", exam["reporter"].fillna("")),
        "JCBW": exam["checkPosition"],
        "JCMC": exam["checkItemName"],
        "SFYYY": pd.Series("2", index=exam.index),
        "XGBZ": pd.Series("1", index=exam.index),
    }
    is_ris = exam["checkType"].isin(["放射", "核医学"])
    ris = exam[is_ris]
    em.emit("TB_RIS_REPORT", {
        **{k: v[is_ris] for k, v in common.items()},
        "YXBX": exam.loc[is_ris, "checkDescribe"],
        "YXZD": exam.loc[is_ris, "checkConclusion"].fillna("").where(
            exam.loc[is_ris, "checkConclusion"].fillna("") != "", exam.loc[is_ris, "diagnosis"].fillna("")),
        "BGLCZD": exam.loc[is_ris, "diagnosis"],
        "YYS": exam.loc[is_ris, "isPos"].map({"是": "1", "阳性": "1", "否": "2", "阴性": "2"}),
    }, len(ris), "sy(放射/核医学)")
    r2 = exam[~is_ris]
    em.emit("TB_RIS_REPORT2", {
        **{k: v[~is_ris] for k, v in common.items()},
        "JCBGJG": exam.loc[~is_ris, "checkConclusion"].fillna("").where(
            exam.loc[~is_ris, "checkConclusion"].fillna("") != "", exam.loc[~is_ris, "checkDescribe"].fillna("")),
        "JCJGDM": exam.loc[~is_ris, "isPos"].map({"是": "2", "阳性": "2", "否": "1", "阴性": "1"}),
        "BT1MC": pd.Series("检查所见", index=r2.index),
        "BT1NR": exam.loc[~is_ris, "checkDescribe"],
        "BT2MC": pd.Series("诊断意见", index=r2.index).where(exam.loc[~is_ris, "diagnosis"].fillna("") != "", ""),
        "BT2NR": exam.loc[~is_ris, "diagnosis"],
    }, len(r2), "sy(超声/病理/心电等)")


def build_patient(em: Emitter, sy, szx) -> None:
    p = sy["pinfo"].reset_index()
    em.emit("TB_YL_PATIENT_INFORMATION", {
        "YLJGYQDM": pd.Series("0001", index=p.index),
        "KH": p["pid"],
        "KLX": pd.Series("99", index=p.index),
        "ZJHM": p["cert"],
        "XB": p["sex"].map({"男": "1", "女": "2"}),
        "XM": p["name"],
        "YYDAH": p["yydah"],
        "XGBZ": pd.Series("1", index=p.index),
    }, len(p), "sy(检验表反推)")
    b = szx["basy"]
    em.emit("TB_YL_PATIENT_INFORMATION", {
        "YLJGYQDM": pd.Series("0003", index=b.index),
        "KH": b["pid"],
        "KLX": pd.Series("99", index=b.index),
        "ZJHM": b["certno"],
        "ZJLX": pd.Series("01", index=b.index),
        "XB": b["gend"],
        "XM": b["psn_name"],
        "HYZK": b["mrg_stas"],
        "CSRQ": ymd(parse_dt(b["brdy"], dayfirst=True)),
        "CSD": b["birplc"],
        "GJ": b["ntly"],
        "MZ": b["naty"],
        "DHHM": b["psn_tel"],
        "GZDWMC": b["emp_name"],
        "GZDWDZ": b["empr_addr"].fillna("").where(b["empr_addr"].fillna("") != "", b["emp_addr"].fillna("")),
        "JZDZ": b["curr_addr"],
        "HKDZ": b["resd_addr"],
        "LXRXM": b["coner_name"],
        "LXRGX": b["patn_rlts"],
        "LXRDH": b["coner_tel"],
        "YWSCSJ": parse_dt(b["create_time"], dayfirst=True),
        "YYDAH": b["medcasno"],
        "XGBZ": pd.Series("1", index=b.index),
    }, len(b), "szx(r_basy)")


def build_dic(em: Emitter, sy, szx) -> None:
    # 医院
    hosp = pd.DataFrame([
        {"YLJGYQDM": "0001", "YYMC": "H31010600042(主院)", "org": "H31010600042"},
        {"YLJGYQDM": "0002", "YYMC": "哈尔滨市道里区人民医院", "org": "H424101075"},
        {"YLJGYQDM": "0003", "YYMC": "42506084200(szx外部院)", "org": "42506084200"},
    ])
    em.emit("TB_DIC_HOSPITAL", {
        "YLJGYQDM": hosp["YLJGYQDM"], "YYMC": hosp["YYMC"],
        "YYJC": hosp["org"],  # 机构简称位存原 12 位机构码 (院区码字典的逆映射)
        "XGBZ": pd.Series("1", index=hosp.index),
    }, len(hosp), "院区码字典逆映射")

    frames = []
    for tag, fee, yq in [("sy", sy["fee"], None), ("szx", szx["fee"], "0003")]:
        for cod, nam in [("bilg_dept_codg", "bilg_dept_name"), ("acord_dept_codg", "acord_dept_name")]:
            if cod in fee.columns:
                sub = fee[[cod, nam, "yq"]].dropna(subset=[nam]).rename(columns={cod: "code", nam: "name"})
                frames.append(sub)
    dept = pd.concat(frames, ignore_index=True)
    dept["code"] = dept["code"].fillna("").where(dept["code"].fillna("") != "",
                                                 dept["name"].map(lambda s: hashlib.md5(s.encode()).hexdigest()[:8]))
    dept = dept.drop_duplicates(subset=["yq", "code"])
    em.emit("TB_DIC_DEPARTMENT", {
        "YLJGYQDM": dept["yq"], "YYKSDM": dept["code"], "YYKSMC": dept["name"],
        "KSXZ": pd.Series("01", index=dept.index), "XGBZ": pd.Series("1", index=dept.index),
    }, len(dept), "fee 科室去重")

    drs = []
    for fee in [sy["fee"], szx["fee"]]:
        for cod, nam in [("bilg_dr_codg", "bilg_dr_name"), ("orders_dr_code", "orders_dr_name")]:
            if cod in fee.columns:
                drs.append(fee[[cod, nam, "yq", "bilg_dept_name"]].dropna(subset=[nam]).rename(
                    columns={cod: "code", nam: "name"}))
    dr = pd.concat(drs, ignore_index=True)
    dr["code"] = dr["code"].fillna("").where(dr["code"].fillna("") != "",
                                             dr["name"].map(lambda s: hashlib.md5(s.encode()).hexdigest()[:8]))
    dr = dr.drop_duplicates(subset=["yq", "code"])
    em.emit("TB_DIC_PRACTITIONER", {
        "YLJGYQDM": dr["yq"], "YHRYID": dr["code"], "GH": dr["code"], "XM": dr["name"],
        "SSKS": dr["bilg_dept_name"], "LB": pd.Series("01", index=dr.index),
        "XGBZ": pd.Series("1", index=dr.index),
    }, len(dr), "fee 医生去重")

    # 药品/非药品目录: 仅 sy (中文类别可靠); szx 数值码不进字典 (报告注明)
    fee = sy["fee"]
    drug_mask = fee["medins_chrgitm_type"].isin(["西药", "中药", "草药"])
    drug = fee[drug_mask].drop_duplicates(subset=["medins_list_codg"]).copy()
    drug = drug[drug["medins_list_codg"].notna()]
    em.emit("TB_DIC_MEDICINES", {
        "YLJGYQDM": drug["yq"], "YYZBDM": drug["medins_list_codg"],
        "GJYBBM": drug["med_list_codg"],
        "SYBZ": pd.Series("0", index=drug.index),
        "TYMC": drug["prodname"].fillna("").where(drug["prodname"].fillna("") != "", drug["medins_list_name"]),
        "BZJX": drug["dosform_name"],
        "YNZJBZ": pd.Series("0", index=drug.index),
        "XGBZ": pd.Series("1", index=drug.index),
    }, len(drug), "sy 药品行去重")
    mat = fee[~drug_mask & fee["medins_list_codg"].notna()].sort_values("dt").drop_duplicates(
        subset=["medins_list_codg"], keep="last")
    em.emit("TB_DIC_MATERIALS", {
        "YLJGYQDM": mat["yq"], "YYZBDM": mat["medins_list_codg"],
        "GJYBBM": mat["med_list_codg"], "XMMC": mat["medins_list_name"],
        "SFDJ": mat["pric"], "SYBZ": pd.Series("0", index=mat.index),
        "YNZJBZ": pd.Series("0", index=mat.index), "XGBZ": pd.Series("1", index=mat.index),
    }, len(mat), "sy 非药品行去重(同码取最新价)")


# ── 交接文档: 逐表说明 (写入 _report.md; 改表逻辑时同步维护此处) ──
# 每项: (表名, 中文名, 来源, 键与合成规则, 注意事项)
TABLE_DOCS: list[tuple[str, str, str, str, str]] = [
    # ---- 费用域 ----
    ("TB_HIS_ZY_FEE_DETAIL_FS", "住院费用发生明细 (费用主表)",
     "sy: shi_fee.csv 全列 · szx: song/r_fee.csv 全列",
     "PK=(YLJGYQDM,SFMXID,STFBZ)。SFMXID=`{患者号}-{feedetl_sn}[-序]` 合成 (源 feedetl_sn 跨患者重复 3 万组, 不能直用); "
     "STFBZ 由金额/数量符号派生 (负数→'2'退费, 金额数量落库取绝对值)",
     "选发生表(_FS)不选结算表: 内部 fee_ocur_time 是发生口径。MXFYLB 是自定 2 位码 (见 _dictionaries/mxfylb_码表.csv, "
     "sy 中文类别与 szx 数值码两套源字典已归一)。医保分解/科室/医生/药品属性 在 EXT 表"),
    ("TB_HIS_ZY_FEE_DETAIL_EXT", "费用明细扩展 (⚠新增表)",
     "与 FS 同源, 装 FS 国标 DDL 放不下的审计刚需列",
     "PK=(YLJGYQDM,SFMXID) 1:1 挂 FS",
     "字段来源规则: 列名 = 内部 shi_fee 同名列的大写 (如 CHRGITM_LV←chrgitm_lv)。逐字段见下方字段表。"
     "Javert 超标准收费(M4)/药品(M8) 审计信号都在这张表"),
    # ---- 病案首页域 ----
    ("TB_BA_SYJBK", "病案首页 (233 列宽表)",
     "sy(3300行): shi_zd 主诊断/病理/损伤中毒 + shi_ss 主手术 + shi_fee 费用聚合 + 检验表患者三要素, 拼装≈30列 · "
     "szx(4701行): r_basy 233 列国标英文直映 130+ 列 (映射表 Scriv/syjbk_rbasy_mapping.json)",
     "PK=(YLJGYQDM,SYXH)。SYXH=患者号(与 SYZDK/SYSSK 一致); BAH: sy=患者号, szx=medcasno(真病案号)",
     "RYRQ/CYRQ 是 varchar(16) 怪格式 `YYYYMMDDHH:MM:SS` (照 DDL desc)。sy 侧入出院时间=费用跨度近似; "
     "szx 侧含完整 84 号文费用宽表 24 分类列 (与费用明细独立两套口径, 勿混算)"),
    ("TB_BA_SYZDK", "首页其他诊断 (归档投影)",
     "sy: shi_zd · szx: r_basy_zd (diag_type∈{1,2} 且非主诊断的行)",
     "PK=(YLJGYQDM,SYXH,ZDXH), ZDXH=患者内 1..n",
     "主诊断不进本表 (在 SYJBK.ZYZD); 编码用临床版 (inhosp_diag_code), 空则回退医保版"),
    ("TB_IH_DIAGNOSIS_DETAIL", "诊断明细 (诊断主表)",
     "sy: shi_zd 全行(10193) · szx: r_basy_zd 全行(22229), 含病理/损伤中毒诊断行",
     "PK=(YLJGYQDM,ZYZDLSH), ZYZDLSH=`{患者号}-D{序}`",
     "ZDBM=临床版 ICD-10 (医保版编码在 TB_YB_JLC_CBRZDXX, 两套编码 16.5% 不同码, 严禁混用)。"
     "CYZDBZ 1主/2非主; 源 vali_flag=0 的病理/损伤中毒行也保留 (不是撤销语义)"),
    # ---- 手术域 ----
    ("TB_BA_SYSSK", "首页手术 (84号文口径)",
     "sy: shi_ss(6795) · szx: r_basy_ss(7437)",
     "PK=(YLJGYQDM,SYXH,SSXH), SSXH=oprn_oprt_sn (同患者重复 sn 加 `-序` 后缀)",
     "⚠sy 侧 SSRQ='-': 源 xls 手术日期列全是 '00:00:00' 纯时间无日期。⚠sy 侧 MZYS(麻醉医生姓名)与术者 100% 同名(源脏数据), "
     "可靠麻醉医师编码用 EXT 表 MZYSBM"),
    ("TB_BA_SYSSK_EXT", "首页手术扩展 (⚠新增表)",
     "与 SYSSK 同源",
     "PK=(YLJGYQDM,SYXH,SSXH) 1:1 挂 SYSSK",
     "装国标 SYSSK 放不下的: 医保版手术双码(DRG/DIP 刚需)、医师编码、手术部位、取消标志、手术/麻醉起止时间。逐字段见下方字段表"),
    ("TB_OPRATION_DETAIL", "手术明细 (HIS 口径手术主表)",
     "同 SYSSK 两源",
     "PK=(YLJGYQDM,SSMXLSH), SSMXLSH=`{患者号}-S{序}`",
     "与 IH_DIAGNOSIS_DETAIL 对称的手术侧明细; ZCBZ 1主/2次; 含术者/麻醉师编码+姓名"),
    # ---- 文书域 ----
    ("TB_CIS_MEDICAL_DOCUMENT", "通用病历文书 (⚠新增表, 全库唯一文书全文表)",
     "sy: case_notes.csv 35.8万段落行 (一行一段落, DLBT=子阶段) · szx: szx_doc.csv 18万行 (一行一整篇, DLBT 空)",
     "PK=(YLJGYQDM,WSLSH), WSLSH=`{患者号}-W{序}`",
     "Javert search_notes / zadig_agent 文书链路的命脉——没有这张表 data_hub 装不下叙述文书。"
     "WSLB 文书类别 2 位码见 _dictionaries/wslb_码表.csv (13 类, 按 WSMC 正则归类)。ZW=正文 nvarchar(max)。"
     "sy 事件时间 92% 空 (源如此), JLSJ 可空。逐字段见下方字段表"),
    ("TB_CIS_LEAVEHOSPITAL_SUMMARY", "出院小结 (结构化 42 列)",
     "仅 sy(2412 份): case_notes 阶段=出院小结 的段落 pivot 到结构化列 (入院诊断→RYZD, 治疗经过+手术情况→ZLGC, 出院医嘱→CYYZ, "
     "健康教育→YYZTB1, 病理报告→YYZTB2)",
     "PK=(YLJGYQDM,JZLSH)",
     "szx 出院小结不分段, 整篇在 MEDICAL_DOCUMENT (WSLB=05), 不进本表。缺段落的 NOT NULL 列填 '-'"),
    # ---- 就诊/患者域 ----
    ("TB_YL_ZY_MEDICAL_RECORD", "住院就诊记录 (键桥主表)",
     "sy: 患者全集(费用∪文书) + 费用跨度时间 · szx: r_basy (真实入出院时间/科室)",
     "PK=(YLJGYQDM,JZLSH)。三键桥: JZLSH=患者号 ↔ CISID(住院号: sy=患者号, szx=medcasno) ↔ BAH",
     "全库表间关联的锚点——查任何患者先从这张表拿键。sy 侧入出院时间是费用跨度近似"),
    ("TB_HIS_ZY_ADM_REG", "入院登记",
     "sy: 近似 (费用首日) · szx: r_basy adm_date + 入院科室 (真值)",
     "PK=(YLJGYQDM,JZLSH)",
     "薄表; 科室/床号 sy 侧无源为 '-'"),
    ("TB_YL_PATIENT_INFORMATION", "患者信息 (主索引)",
     "sy(2838): 检验表反推 姓名/性别/年龄/patientId · szx(4701): r_basy 人口学全套 (证件号/出生/婚姻/民族/地址/单位/联系人)",
     "PK=(YLJGYQDM,KH,KLX)。KH=患者号(无真实卡号), KLX='99'",
     "⚠sy 有 424 名患者检验表无姓名源 → XM='-'。szx 民族/病案质量源列疑互换已做数值甄别"),
    # ---- 检验域 ----
    ("TB_LIS_REPORT", "检验报告头",
     "sy: sy_检验.csv 按报告聚合(6.6万) · szx: song/lab_results.csv 合成聚合(5.8万, zyh=medcasno 经 r_basy 桥回 psn_no)",
     "PK=(YLJGYQDM,BGRQ,BGDH)。BGDH: sy=`{患者号}-{inspectionRecordId}` (⚠必须前缀患者号: 同一报告会复制到同人两次住院, 裸报告号撞 PK); "
     "szx=`{患者号}-R{md5(患者|报告时间|检验类别)}` 合成",
     "时间回退链: JYSJ←TestDate(38%空)←采样时间←报告时间; szx 患者三要素从 r_basy 富化"),
    ("TB_LIS_INDICATORS", "检验结果指标 (一行一指标)",
     "同 LIS_REPORT 两源, 151.5 万行",
     "PK=(YLJGYQDM,JYZBLSH), JYZBLSH=`{BGDH}-{序}` (源 id 假唯一 85.6万行仅51.8万 distinct, 不能直用)",
     "YCTS 异常提示: 1正常/2异常无法识别/3偏高/4偏低; 定性阴性(N) 归 1 正常"),
    # ---- 检查域 ----
    ("TB_RIS_REPORT", "检查报告-放射类",
     "仅 sy: sy_patient_examination.csv 中 checkType∈{放射,核医学} (7697)",
     "PK=(YLJGYQDM,INSTANCEUID), INSTANCEUID=源 id (重复加后缀)",
     "影像所见→YXBX, 结论→YXZD (空回退 diagnosis)。szx 无检查数据源"),
    ("TB_RIS_REPORT2", "检查报告-非放射类",
     "仅 sy: 超声/病理/心电图/心超/电生理/内镜 (33333)",
     "无声明 PK (DDL 原样); INSTANCEUID 同上保唯一",
     "结论→JCBGJG; 所见走标题块 BT1MC='检查所见'/BT1NR, 诊断意见 BT2MC/BT2NR。病理报告在本表 (10512 份)"),
    # ---- 医保域 ----
    ("TB_YB_JLC_CBRZDXX", "参保人诊断信息 (医保侧)",
     "sy+szx 诊断表中医保版编码非空的行 (3.0万)",
     "XH=`{患者号}-Y{序}` (DDL 无声明 PK), LSH=患者号",
     "ZDNO=医保版编码 (与 IH 表临床版是两套码); 源医保码为空的撤销行已剔除"),
    # ---- 字典域 ----
    ("TB_DIC_HOSPITAL", "医院字典 (院区码逆映射)",
     "手工 3 行",
     "PK=YLJGYQDM",
     "⚠关键: YYJC(机构简称) 存的是原 12 位机构码 (H31010600042/H424101075/42506084200)——院区码→原码的唯一逆映射, "
     "反向取数 (etl_from_data_hub.py) 靠它重建复合患者键。0001=sy主院 0002=黑龙江OCR病例 0003=szx"),
    ("TB_DIC_DEPARTMENT", "科室字典", "sy+szx 费用表 开单/受单科室去重 (158)", "PK=(YLJGYQDM,YYKSDM); 无码科室用名称 md5 前 8 位",
     "最小可用版; 卫统码/医保码无源为 '-'"),
    ("TB_DIC_PRACTITIONER", "医护人员字典", "sy+szx 费用表 开单医生去重 (1694)", "PK=(YLJGYQDM,YHRYID)=医生编码; GH 同值",
     "SSKS=高频开单科室; 执业证书/国家医保医生码无源"),
    ("TB_DIC_MEDICINES", "药品目录", "仅 sy 费用表 西药/中药/草药行按院内码去重 (920)", "PK=(YLJGYQDM,YYZBDM)=院内码",
     "szx 不进 (数值类别码划不清药品边界)。TYMC 用 prodname 回退项目名; GJYBBM 空填 '-'"),
    ("TB_DIC_MATERIALS", "非药品目录 (诊疗项目+耗材)", "仅 sy 费用表非药品行去重 (2206), 同码多价取最新", "PK=(YLJGYQDM,YYZBDM)",
     "SFDJ=最新单价"),
]


EXT_DDL = """-- data_hub_filled 扩展表 DDL (回传 142 前先执行; 幂等)
-- 依据: data_hub_关系映射.md §五 扩容清单 ①②③

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'TB_CIS_MEDICAL_DOCUMENT')
CREATE TABLE [dbo].[TB_CIS_MEDICAL_DOCUMENT] (
  [YLJGYQDM] varchar(8) NOT NULL,            -- 医疗机构院区代码
  [JZLSH] varchar(64) NOT NULL,              -- 住院就诊流水号
  [WSLSH] varchar(64) NOT NULL,              -- 文书流水号 (PK)
  [WSLB] varchar(2) NOT NULL,                -- 文书类别码 (见 _dictionaries/wslb_码表.csv)
  [WSMC] nvarchar(256) NOT NULL,             -- 文书名称 (原始)
  [DLBT] nvarchar(128) NULL,                 -- 段落标题 (如 主诉/现病史)
  [DLXH] int NULL,                           -- 段落序号
  [JLSJ] datetime NULL,                      -- 记录时间
  [ZW] nvarchar(max) NULL,                   -- 正文
  [XGBZ] varchar(1) NOT NULL DEFAULT '1',
  CONSTRAINT [PK_TB_CIS_MEDICAL_DOCUMENT] PRIMARY KEY CLUSTERED ([YLJGYQDM],[WSLSH])
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'TB_HIS_ZY_FEE_DETAIL_EXT')
CREATE TABLE [dbo].[TB_HIS_ZY_FEE_DETAIL_EXT] (
  [YLJGYQDM] varchar(8) NOT NULL,            -- 医疗机构院区代码
  [SFMXID] varchar(32) NOT NULL,             -- 1:1 挂 TB_HIS_ZY_FEE_DETAIL_FS.SFMXID
  [JZLSH] varchar(64) NOT NULL,              -- 住院就诊流水号(患者号)
  [CHRGITM_LV] varchar(8) NULL,              -- 收费项目等级(甲乙丙)
  [LIST_TYPE] varchar(32) NULL,              -- 目录类别
  [MED_LIST_CODG] varchar(64) NULL,          -- 国家医保编码
  [MEDINS_LIST_CODG] varchar(64) NULL,       -- 院内项目编码 (完整值; FS.MXXMBM varchar(32) 截断兜底)
  [PRODNAME] nvarchar(256) NULL,             -- 药品通用名
  [SPEC] nvarchar(128) NULL,                 -- 规格
  [DOSFORM_NAME] nvarchar(64) NULL,          -- 剂型
  [BILG_DEPT_CODG] varchar(32) NULL,         -- 计费科室编码
  [BILG_DEPT_NAME] nvarchar(128) NULL,       -- 计费科室名称
  [BILG_DR_CODG] varchar(32) NULL,           -- 计费医生编码
  [BILG_DR_NAME] nvarchar(64) NULL,          -- 计费医生姓名
  [ACORD_DEPT_CODG] varchar(32) NULL,        -- 开单(受单)科室编码
  [ACORD_DEPT_NAME] nvarchar(128) NULL,      -- 开单(受单)科室名称
  [ORDERS_DR_CODE] varchar(32) NULL,         -- 开单医生编码
  [ORDERS_DR_NAME] nvarchar(64) NULL,        -- 开单医生姓名
  [DSCG_TKDRUG_FLAG] varchar(2) NULL,        -- 出院带药标志
  [FEE_TYPE] varchar(8) NULL,                -- 费用类型(源枚举)
  [MEDINS_CHRGITM_TYPE] nvarchar(16) NULL,   -- 源费用类别(中文)
  [HOSP_APPR_FLAG] varchar(2) NULL,          -- 医院审批标志
  [PRIC_UPLMT_AMT] decimal(15,3) NULL,       -- 限价
  [SELFPAY_PROP] decimal(6,4) NULL,          -- 自付比例
  [FULAMT_OWNPAY_AMT] decimal(15,3) NULL,    -- 全自费金额
  [OVERLMT_AMT] decimal(15,3) NULL,          -- 超限价金额
  [PRESELFPAY_AMT] decimal(15,3) NULL,       -- 先行自付金额
  [INSCP_SCP_AMT] decimal(15,3) NULL,        -- 医保范围内金额
  CONSTRAINT [PK_TB_HIS_ZY_FEE_DETAIL_EXT] PRIMARY KEY CLUSTERED ([YLJGYQDM],[SFMXID])
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'TB_BA_SYSSK_EXT')
CREATE TABLE [dbo].[TB_BA_SYSSK_EXT] (
  [YLJGYQDM] varchar(8) NOT NULL,            -- 医疗机构院区代码
  [SYXH] varchar(32) NOT NULL,               -- 同 TB_BA_SYSSK.SYXH (患者号)
  [SSXH] varchar(8) NOT NULL,                -- 同 TB_BA_SYSSK.SSXH
  [HISSDM] varchar(64) NULL,                 -- 医保版手术编码 (DRG/DIP)
  [HISSMC] nvarchar(256) NULL,               -- 医保版手术名称
  [SSBW] nvarchar(128) NULL,                 -- 手术部位
  [SSBWDM] varchar(32) NULL,                 -- 手术部位编码
  [SSYSBM] varchar(32) NULL,                 -- 术者编码
  [MZYSBM] varchar(32) NULL,                 -- 麻醉医师编码 (比姓名可靠)
  [QXSSBZ] varchar(2) NULL,                  -- 取消手术标志
  [SSKSSJ] datetime NULL,                    -- 手术开始时间
  [SSJSSJ] datetime NULL,                    -- 手术结束时间
  [MZKSSJ] datetime NULL,                    -- 麻醉开始时间
  [MZJSSJ] datetime NULL,                    -- 麻醉结束时间
  CONSTRAINT [PK_TB_BA_SYSSK_EXT] PRIMARY KEY CLUSTERED ([YLJGYQDM],[SYXH],[SSXH])
);
"""


# ────────────────────────── 主流程 ──────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只跑某域: fee/diag/surg/face/visit/doc/lab/exam/patient/dic")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "_dictionaries").mkdir(exist_ok=True)
    em = Emitter()

    print("装载 sy ...", flush=True)
    sy = load_sy()
    print("装载 szx(song) ...", flush=True)
    szx = load_szx()

    def want(k: str) -> bool:
        return args.only is None or args.only == k

    if want("fee"):
        print("费用 ...", flush=True)
        build_fee(em, sy, "sy(shi_fee)", lambda f: f["medins_chrgitm_type"].map(SY_CAT2CODE).fillna("99"))
        build_fee(em, szx, "szx(r_fee)", lambda f: f["med_chrgitm_type"].map(SZX_CODE2CODE).fillna("99"))
    if want("diag"):
        print("诊断 ...", flush=True)
        build_diag(em, sy, "sy(shi_zd)")
        build_diag(em, szx, "szx(r_basy_zd)")
    if want("surg"):
        print("手术 ...", flush=True)
        build_surg(em, sy, "sy(shi_ss)")
        build_surg(em, szx, "szx(r_basy_ss)")
    if want("face"):
        print("病案首页 ...", flush=True)
        build_face_sy(em, sy)
        build_face_szx(em, szx)
    if want("visit"):
        print("就诊登记 ...", flush=True)
        build_visit(em, sy, szx)
    if want("doc"):
        print("文书 ...", flush=True)
        build_discharge(em, sy)
        build_docs(em, sy, szx)
    if want("lab"):
        print("检验 ...", flush=True)
        build_lab(em, sy, szx)
    if want("exam"):
        print("检查 ...", flush=True)
        build_exam(em, sy)
    if want("patient"):
        print("患者 ...", flush=True)
        build_patient(em, sy, szx)
    if want("dic"):
        print("字典 ...", flush=True)
        build_dic(em, sy, szx)

    print("落盘 ...", flush=True)
    for name, df in em.tables.items():
        df.to_csv(OUT_DIR / f"{name}.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame([{"code": k, "name": v} for k, v in MXFYLB_DICT.items()]).to_csv(
        OUT_DIR / "_dictionaries/mxfylb_码表.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"code": k, "name": v} for k, v in WSLB_DICT.items()]).to_csv(
        OUT_DIR / "_dictionaries/wslb_码表.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"YLJGYQDM": v, "原机构代码": k} for k, v in YQDM_MAP.items()]).to_csv(
        OUT_DIR / "_dictionaries/院区码映射.csv", index=False, encoding="utf-8-sig")
    (OUT_DIR / "_ext_tables.sql").write_text(EXT_DDL, encoding="utf-8")

    # 扩展表字段文档: 从 EXT_DDL 的行内注释自动抽取 (改 DDL 注释即改文档, 不漂移)
    ext_fields: dict[str, list[tuple[str, str, str, str]]] = {}
    for block in re.finditer(r"CREATE TABLE \[dbo\]\.\[(\w+)\](.*?)\);", EXT_DDL, re.S):
        tname, body = block.group(1), block.group(2)
        rows = []
        for ln in body.splitlines():
            comment = ln.split("--", 1)[1].strip() if "--" in ln else ""
            defs = re.findall(r"\[(\w+)\]\s+(n?varchar\(\w+\)|decimal\(\d+,\d+\)|datetime|int)\s+(NOT NULL|NULL)", ln)
            for c, t, nl in defs:
                rows.append((c, t, nl, comment if len(defs) == 1 else ""))
        ext_fields[tname] = rows

    row_count = {}
    for line in em.report:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) > 3 and parts[3].isdigit():
            t, n = parts[1].replace(" (EXT)", ""), int(parts[3])
            if "(EXT)" in parts[1]:  # emit_ext 报累计值 → 取 max
                row_count[t] = max(row_count.get(t, 0), n)
            else:
                row_count[t] = row_count.get(t, 0) + n

    lines = [
        "# data_hub_filled 交接报告", "",
        f"> 生成: scripts/build_data_hub_filled.py · 数据源 sy({len(sy['fee'])}费用行) + szx({len(szx['fee'])}费用行)",
        "> 设计依据: Scriv/data_hub_关系映射.md (总纲) + data_hub_关系映射_字段级明细.md (330条映射) + syjbk_rbasy_mapping.json",
        "> 回传 142 顺序: ① 先跑 _ext_tables.sql 建 3 张扩展表 → ② 灌 23 张 CSV (scripts/push_data_hub_filled.py, 幂等)",
        "> 反向取数 (Javert/zadig_agent 消费): scripts/etl_from_data_hub.py --patients a,b / --all", "",
        "## 全局键体系 (读任何表前先看这段)",
        "- **YLJGYQDM 院区代码**: `0001`=sy主院(H31010600042) `0002`=黑龙江OCR病例(H424101075) `0003`=szx(42506084200)。",
        "  varchar(8) 装不下原 12 位机构码 → 原码存 TB_DIC_HOSPITAL.YYJC (逆映射)",
        "- **JZLSH/SYXH 患者键**: 裸患者号 (sy=J66252 形态, szx=psn_no 211530148 形态), **已大写归一**",
        "  (源数据有 j34532 等小写变体, 142 CI 排序规则下同键)",
        "- **szx 双号体系**: psn_no(211*)=患者主键; medcasno(8位)=病案号 (文书/检验源表用它, 建库时已经 r_basy 桥转回 psn_no)",
        "- **合成键**: 所有明细流水号都是 `{患者号}-{源号或序}` 形态 (源 ID 假唯一, 详见各表说明)", "",
        "## 自检结果 (逐 emit 批次)", "",
        "| 表 | 来源 | 行数 | PK | NOT NULL 兜底列(行数) | 截断列(行数) |",
        "|---|---|---|---|---|---|",
        *em.report, "",
        "## 逐表说明", "",
    ]
    for tname, cn_name, src, key, note in TABLE_DOCS:
        n = row_count.get(tname, 0)
        lines += [f"### {tname} — {cn_name}", "",
                  f"- **行数**: {n:,}",
                  f"- **来源**: {src}",
                  f"- **键**: {key}",
                  f"- **说明**: {note}", ""]
        if tname in ext_fields:
            lines += ["| 字段 | 类型 | 可空 | 含义/来源 |", "|---|---|---|---|"]
            lines += [f"| {c} | {t} | {'' if nl == 'NOT NULL' else '✓'} | {cm} |" for c, t, nl, cm in ext_fields[tname]]
            lines += [""]
    lines += [
        "## 已知近似与坑 (与映射总纲一致)",
        "- sy 侧入出院时间 (SYJBK.RYRQ/CYRQ, 就诊记录, 入院登记) 用费用时间跨度近似 (无费用患者为哨兵/'-')",
        "- sy 侧手术日期/起止时间无源 (源 xls 全是纯时间 '00:00:00'), SSRQ='-', 时间列 NULL",
        "- SYSSK.MZYS: sy 侧 anst_dr_name 与术者同名 (源脏数据), 可靠麻醉医师编码在 TB_BA_SYSSK_EXT.MZYSBM",
        "- sy 424 名患者无姓名源 (检验表覆盖 2838/3063), 患者表 XM='-'",
        "- szx 药品/耗材不进 DIC_MEDICINES/MATERIALS (数值类别码不定药品边界)",
        "- MXFYLB 为自定 2 位码 (官方码表到位后按 _dictionaries/mxfylb_码表.csv 一键替换)",
        "- NOT NULL 兜底约定: varchar→'-' · datetime→1900-01-01 哨兵 · decimal→0 (逐列计数见上方自检表)",
        "- 日期三坑 (已处理, 二次开发别再踩): 年在前 ISO 不吃 dayfirst / 纯时间值不造日期 / decimal 聚合残差按 scale 归一",
    ]
    (OUT_DIR / "_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"完成 → {OUT_DIR}")
    print("\n".join(em.report))


if __name__ == "__main__":
    sys.exit(main())
