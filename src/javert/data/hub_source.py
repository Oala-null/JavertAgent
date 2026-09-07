# -*- coding: utf-8 -*-
"""TB_* 国标表 → Javert 内部列契约 的唯一映射源 (add-workbench-sql-raw-source D2).

`scripts/etl_from_data_hub.py` (流B 批量快照) 与工作台 HubRawSource (逐患者按需)
共用本模块的 fetch_* 函数 — 映射逻辑只此一份, 改列契约只改这里.

产出列契约 = configs/schema_manifest.yaml 各 spoke 的 output_schema (v0.7 外部数据同一契约).
连接凭据复用 config sql_* (同台 142), 库名走 cfg.hub_database (默认 sh_yb_platform).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

# MXFYLB 统一 2 位码 → 中文 (与 build_data_hub_filled.MXFYLB_DICT 一致; szx 侧 EXT 无中文类别时回填)
MXFYLB2CN = {
    "01": "床位", "02": "诊查", "03": "检查", "04": "化验", "05": "治疗", "06": "手术",
    "07": "麻醉", "08": "护理", "09": "材料", "10": "西药", "11": "中成药", "12": "草药",
    "13": "输血", "14": "输氧", "15": "饮食", "16": "专护", "17": "CT", "18": "拍片",
    "19": "透视", "20": "病理", "99": "其他",
}
YCTS2FLAG = {"1": "正常", "2": "异常", "3": "偏高", "4": "偏低"}
SENT = "1900-01-01 00:00:00"

# 病案首页三表 (TB_BA_SYJBK/SYZDK/SYSSK) 为 ground truth 的院区.
# szx(0003): IH_DIAGNOSIS_DETAIL 的 CYZDBZ 不是主诊语义 (与首页主诊几乎零一致, 2026-07-06 实measured),
# 主诊断锚 = SYJBK.ZYZD (与 IH 主诊 83% 同码), 诊断列表 = SYZDK, 手术 = SYSSK⋈OPRATION_DETAIL (v2.2).
# sy(0001): 首页库回填不全 (J66252 仅 1 行且主诊错), 维持 IH/OPRATION 现状.
BA_HOSPS = ("0003",)

# 上海接口1.3.1费用类别；旧测试表的自造类别仅用于legacy。
SHANGHAI_FEE_CATEGORIES = {
    "01": "床位", "02": "诊察", "03": "检查", "04": "化验",
    "05": "治疗", "06": "手术", "07": "护理", "08": "卫生材料",
    "09": "西药", "10": "中成药", "11": "中草药", "12": "其他",
}


class HospitalLinkageError(ValueError):
    """稳定的预检错误码；不包含患者号、卡号、SQL或凭据。"""


@dataclass(frozen=True, repr=False)
class HospitalPatient:
    hospital: str
    syxh: str
    bah: str
    visit: str
    kh: str
    klx: str
    admission: str = ""
    discharge: str = ""


def _valid(value) -> bool:
    return value is not None and str(value).strip().upper() not in {"", "-", "NONE", "NULL", "NAN"}


def _time(value) -> datetime:
    text = str(value).strip()
    for fmt in ("%Y%m%d%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            date = datetime.strptime(text, fmt)
            if date.year > 1900:
                return date
        except ValueError:
            pass
    raise HospitalLinkageError("LINK_TIME_MISSING")


def _check_time(left, right):
    # 容忍已观察到的4秒源系统差异，不以时间近似寻找关联。
    if abs((_time(left) - _time(right)).total_seconds()) > 60:
        raise HospitalLinkageError("LINK_TIME_CONFLICT")


def _optional_time(value):
    try:
        return _time(value).isoformat(sep=" ")
    except HospitalLinkageError:
        return ""


def _one(frame, stage):
    if len(frame) != 1:
        raise HospitalLinkageError(f"LINK_{stage}_NOT_UNIQUE")
    return frame.iloc[0]


def resolve_patient(cn, syxh: str, hospital: str) -> HospitalPatient:
    """仅按明确的SYXH→BAH→JZLSH解析本次住院；任何歧义停止。"""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", hospital or "") or hospital in {"0001", "0003"}:
        raise HospitalLinkageError("LINK_HOSPITAL_REQUIRED")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", syxh or ""):
        raise HospitalLinkageError("LINK_SYXH_INVALID")
    home = _one(q(cn, """
        SELECT TOP (2) SYXH, BAH, KH, KLX, RYRQ, CYRQ FROM dbo.TB_BA_SYJBK
        WHERE YLJGYQDM=? AND SYXH=?
    """, (hospital, syxh)), "HOME")
    if not all(_valid(home[c]) for c in ("BAH", "KH", "KLX")) or home.BAH == "0":
        raise HospitalLinkageError("LINK_IDENTITY_MISSING")
    home_bah = _one(q(cn, """
        SELECT TOP (2) SYXH FROM dbo.TB_BA_SYJBK WHERE YLJGYQDM=? AND BAH=?
    """, (hospital, home.BAH)), "HOME_BAH")
    if home_bah.SYXH != home.SYXH:
        raise HospitalLinkageError("LINK_HOME_CONFLICT")
    summary = _one(q(cn, """
        SELECT TOP (2) JZLSH, BAH, KH, KLX, RYSJ, CYSJ
        FROM dbo.TB_CIS_LEAVEHOSPITAL_SUMMARY WHERE YLJGYQDM=? AND BAH=?
    """, (hospital, home.BAH)), "SUMMARY")
    if (not _valid(summary.JZLSH) or summary.JZLSH == "0"
            or (summary.KH, summary.KLX, summary.BAH) != (home.KH, home.KLX, home.BAH)):
        raise HospitalLinkageError("LINK_IDENTITY_CONFLICT")
    visit_row = _one(q(cn, """
        SELECT TOP (2) BAH FROM dbo.TB_CIS_LEAVEHOSPITAL_SUMMARY
        WHERE YLJGYQDM=? AND JZLSH=?
    """, (hospital, summary.JZLSH)), "SUMMARY_VISIT")
    if visit_row.BAH != home.BAH:
        raise HospitalLinkageError("LINK_VISIT_CONFLICT")
    _check_time(home.RYRQ, summary.RYSJ)
    _check_time(home.CYRQ, summary.CYSJ)
    if _time(home.RYRQ) > _time(home.CYRQ) or _time(summary.RYSJ) > _time(summary.CYSJ):
        raise HospitalLinkageError("LINK_TIME_REVERSED")
    admission = _one(q(cn, """
        SELECT TOP (2) KH, KLX, RYSJ FROM dbo.TB_HIS_ZY_ADM_REG
        WHERE YLJGYQDM=? AND JZLSH=?
    """, (hospital, summary.JZLSH)), "ADMISSION")
    if (admission.KH, admission.KLX) != (home.KH, home.KLX):
        raise HospitalLinkageError("LINK_ADMISSION_IDENTITY_CONFLICT")
    _check_time(admission.RYSJ, summary.RYSJ)
    return HospitalPatient(hospital, str(home.SYXH), str(home.BAH),
                           str(summary.JZLSH), str(home.KH), str(home.KLX),
                           _time(summary.RYSJ).isoformat(sep=" "),
                           _time(summary.CYSJ).isoformat(sep=" "))


def _scope(pids, column, patient, key="visit", hospital_column="YLJGYQDM"):
    if patient is None:
        return in_clause(pids, column), ()
    return f"{hospital_column}=? AND {column}=?", (patient.hospital, getattr(patient, key))


def _internal_id(frame, column, patient):
    if patient is not None:
        frame[column] = patient.syxh
    return frame


def fetch_hospital_bundle(cn, syxh: str, hospital: str) -> dict:
    """ETL和工作台唯一的新模式入口；按阶段返回无敏感信息的失败码。"""
    patient = resolve_patient(cn, syxh, hospital)
    pids, mapping = [patient.syxh], {hospital: hospital}
    bundle = {}
    for name, fetch, with_map in (
        ("fees", fetch_fees, True), ("notes", fetch_notes, False),
        ("zd", fetch_zd, True), ("ss", fetch_ss, True),
        ("labs", fetch_labs, False), ("exams", fetch_exams, False),
    ):
        try:
            args = (cn, pids, mapping) if with_map else (cn, pids)
            bundle[name] = fetch(*args, patient=patient)
        except HospitalLinkageError:
            raise
        except Exception:
            raise HospitalLinkageError(f"SOURCE_{name.upper()}_QUERY_FAILED") from None
        if name in ("fees", "notes") and bundle[name].empty:
            raise HospitalLinkageError(f"SOURCE_{name.upper()}_EMPTY")
    bundle["basics"] = {"admission": patient.admission, "discharge": patient.discharge}
    bundle["warnings"] = {
        "NOTE_TIME_MISSING": int(bundle["notes"]["事件时间"].map(lambda v: not _valid(v)).sum()),
        "LABS_EMPTY": int(bundle["labs"].empty),
        "EXAMS_EMPTY": int(bundle["exams"].empty),
    }
    return bundle


def build_conn_str(cfg, database: str | None = None, login_timeout: int = 60) -> str:
    """hub 连接串: 复用 config sql_* 凭据; Encrypt=no 为 142 老 TLS 必需 (Mac/62 直连同)."""
    if not cfg.sql_password:
        raise RuntimeError(
            "JAVERT_SQL_PASSWORD 未配置 (凭证不入源码) — source .env 或 export 后重试")
    return (
        f"DRIVER={{{cfg.sql_driver}}};SERVER={cfg.sql_host},{cfg.sql_port};"
        f"DATABASE={database or cfg.hub_database};UID={cfg.sql_user};PWD={cfg.sql_password};"
        f"TrustServerCertificate=yes;Encrypt=no;LoginTimeout={login_timeout}"
    )


def connect(cfg, database: str | None = None, timeout: int = 60):
    import pyodbc
    return pyodbc.connect(build_conn_str(cfg, database), timeout=timeout)


def q(cn, sql: str, params=()) -> pd.DataFrame:
    cur = cn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return pd.DataFrame.from_records(cur.fetchall(), columns=cols).astype(str).replace({"None": ""})


def in_clause(pids: list[str] | None, col: str) -> str:
    if pids is None:
        return "1=1"
    vals = ",".join("'" + p.replace("'", "") + "'" for p in pids)
    return f"{col} IN ({vals})"


def clean_dt(s: pd.Series) -> pd.Series:
    """哨兵 1900 → 空; 去掉全零时间尾巴保持内部数据习惯."""
    return s.where(~s.str.startswith("1900-01-01"), "")


def fetch_hospital_map(cn) -> dict[str, str]:
    """院区码 YLJGYQDM → 原机构码 (建库时逆映射存 YYJC)."""
    hosp = q(cn, "SELECT YLJGYQDM, YYJC FROM TB_DIC_HOSPITAL")
    return dict(zip(hosp["YLJGYQDM"], hosp["YYJC"]))


# v3 (2026-07-10): EXT 只留"值与国标 FS 列不重复"的原始字段。
# 已剔除: MED/MEDINS_LIST_CODG(与 FS 逐值重复 99.6%/100%) + 3 全空占位 + 5 死列(源头全零, 实测)
_FEE_EXT_COLS = [
    "CHRGITM_LV", "LIST_TYPE", "PRODNAME", "SPEC",
    "BILG_DEPT_CODG", "BILG_DEPT_NAME", "BILG_DR_CODG", "BILG_DR_NAME",
    "ACORD_DEPT_CODG", "ACORD_DEPT_NAME", "ORDERS_DR_CODE", "ORDERS_DR_NAME",
    "FEE_TYPE", "MEDINS_CHRGITM_TYPE", "SELFPAY_PROP",
]


def fetch_fees(cn, pids: list[str] | None, yq2org: dict[str, str], *, patient=None) -> pd.DataFrame:
    """费用: FS (⋈ EXT 若存在) → shi_fee (36 列契约).

    v3: 编码两列直取 FS 原生列 (MXXMBMYB 国家码 / MXXMBM 院内码); EXT 只装原始补充字段
    (通用名/规格/科室医生/原始类别/自付比例)。EXT 缺表容忍 (医院数据未就绪时 FS 单表可跑);
    已剔除列 (医保分解死列/剂型等) 契约位置保留、恒空。"""
    has_ext = len(q(cn, "SELECT 1 x FROM sys.tables WHERE name='TB_HIS_ZY_FEE_DETAIL_EXT'")) > 0
    ext_sel = ", ".join(f"e.{c}" for c in _FEE_EXT_COLS)
    where, params = _scope(pids, "f.JZLSH", patient, hospital_column="f.YLJGYQDM")
    fee = q(cn, f"""
        SELECT f.YLJGYQDM, f.SFMXID, f.STFBZ, f.JZLSH, f.MXFYLB, f.FYFSSJ, f.MXXMBM, f.MXXMBMYB,
               f.MXXMMC, f.MXXMDJ, f.MXXMSL, f.MXXMJE{', f.KH, f.KLX' if patient else ''}{', ' + ext_sel if has_ext else ''}
        FROM TB_HIS_ZY_FEE_DETAIL_FS f
        {'LEFT JOIN TB_HIS_ZY_FEE_DETAIL_EXT e ON f.YLJGYQDM=e.YLJGYQDM AND f.SFMXID=e.SFMXID' if has_ext else ''}
        WHERE {where}""", params)
    if patient is not None:
        if not ((fee["KH"] == patient.kh) & (fee["KLX"] == patient.klx)).all():
            raise HospitalLinkageError("LINK_FEE_CARD_CONFLICT")
        if fee.duplicated(["YLJGYQDM", "SFMXID", "STFBZ"]).any():
            raise HospitalLinkageError("LINK_FEE_DUPLICATED")
        yq2org = {patient.hospital: patient.hospital}
        _internal_id(fee, "JZLSH", patient)
    if not has_ext:
        for c in _FEE_EXT_COLS:
            fee[c] = ""
    sign = fee["STFBZ"].map(lambda v: -1 if v == "2" else 1)
    empty = pd.Series("", index=fee.index)
    cat = fee["MEDINS_CHRGITM_TYPE"].where(fee["MEDINS_CHRGITM_TYPE"] != "",
                                           fee["MXFYLB"].map(MXFYLB2CN).fillna("其他"))
    if patient is not None:
        cat = fee["MXFYLB"].str.zfill(2).map(SHANGHAI_FEE_CATEGORIES).fillna("其他")
    return pd.DataFrame({
        "bah": fee["YLJGYQDM"].map(yq2org).fillna("") + "-" + fee["JZLSH"],
        "feedetl_sn": fee["SFMXID"],
        "fee_ocur_time": fee["FYFSSJ"],
        "cnt": pd.to_numeric(fee["MXXMSL"], errors="coerce").fillna(0) * sign,
        "pric": fee["MXXMDJ"],
        "det_item_fee_sumamt": pd.to_numeric(fee["MXXMJE"], errors="coerce").fillna(0) * sign,
        "pric_uplmt_amt": empty, "selfpay_prop": fee["SELFPAY_PROP"],
        "fulamt_ownpay_amt": empty, "overlmt_amt": empty,
        "preselfpay_amt": empty, "inscp_scp_amt": empty,
        "chrgitm_lv": fee["CHRGITM_LV"], "list_type": fee["LIST_TYPE"],
        "med_list_codg": fee["MXXMBMYB"],
        "medins_list_codg": fee["MXXMBM"],
        "medins_list_name": fee["MXXMMC"],
        "med_chrgitm_type": "", "prodname": fee["PRODNAME"], "spec": fee["SPEC"],
        "dosform_name": empty,
        "bilg_dept_codg": fee["BILG_DEPT_CODG"], "bilg_dept_name": fee["BILG_DEPT_NAME"],
        "bilg_dr_codg": fee["BILG_DR_CODG"], "bilg_dr_name": fee["BILG_DR_NAME"],
        "acord_dept_codg": fee["ACORD_DEPT_CODG"], "acord_dept_name": fee["ACORD_DEPT_NAME"],
        "orders_dr_code": fee["ORDERS_DR_CODE"], "orders_dr_name": fee["ORDERS_DR_NAME"],
        "dscg_tkdrug_flag": empty, "fee_type": fee["FEE_TYPE"],
        "medins_chrgitm_type": cat, "hosp_appr_flag": empty,
        "hospital": fee["YLJGYQDM"].map(yq2org).fillna(""),
        "create_time": "", "id": fee["SFMXID"],
    })


# LEAVEHOSPITAL_SUMMARY 列 → 出院小结 子阶段 (build_data_hub_filled SY_SEC2COL 的逆向, 名称保持 sy canonical)
SUMMARY_COL2SEC = [
    ("RYZD", "入院诊断"), ("CYZD", "出院诊断"), ("RYZZTZ", "入院时主要症状和体征"),
    ("JCHZ", "主要实验室检查和特殊检查"), ("ZLGC", "治疗经过"), ("HBZ", "合并症"),
    ("CYQKMS", "出院时症状和体征"), ("CYYZ", "出院医嘱"), ("ZLJGSM", "治疗结果"),
]


def _summary_to_notes(summ: pd.DataFrame) -> list[dict]:
    """标准表出院小结行 → case_notes 行 (阶段=出院小结, 一列一子阶段; '-'/'' 兜底值跳过)."""
    rows: list[dict] = []
    for r in summ.itertuples(index=False):
        ts = "" if str(r.CYSJ).startswith("1900-01-01") else r.CYSJ
        pairs = [(sec, getattr(r, col)) for col, sec in SUMMARY_COL2SEC]
        pairs += [(r.YYZTBBT1, r.YYZTB1), (r.YYZTBBT2, r.YYZTB2)]  # 动态标题块 (健康教育/病理报告)
        for sec, body in pairs:
            if sec and sec != "-" and body and body != "-":
                rows.append({"住院号": r.JZLSH, "事件时间": ts, "阶段": "出院小结",
                             "子阶段": sec, "内容": body, "来源文件": "data_hub"})
    return rows


def fetch_notes(cn, pids: list[str] | None, *, patient=None) -> pd.DataFrame:
    """文书: 标准表 LEAVEHOSPITAL_SUMMARY (出院小结) + 扩展表 MEDICAL_DOCUMENT → case_notes (6 列契约).

    46表标准化: 出院小结的标准承载 = LEAVEHOSPITAL_SUMMARY。患者在扩展表有 WSLB=05 行
    (自建全文, 信息最全) → 用扩展表; 只有标准表行 (医院按国标 DDL 灌库的形态) → 列反拆重建.
    医院只给标准表不给扩展表 05 也能跑.

    szx 侧整篇文书一行且 DLBT(段落标题) 全空 → note_diagnosis 等按子阶段匹配的工具全瞎.
    修复: DLBT 空且正文含【段落】标记时按标记拆行 (复用 v0.7/v0.10 已验证拆分);
    DLBT 有值 (sy 侧已拆) 或无标记 → 原样, 不伤现状.
    """
    from javert.onboarding.etl_engine import split_sections

    doc_where, doc_params = _scope(pids, "BAH" if patient else "JZLSH", patient, key="bah")
    summary_where, summary_params = _scope(pids, "JZLSH", patient)
    doc = q(cn, f"""
        SELECT JZLSH, JLSJ, WSMC, WSLB, DLBT, ZW FROM TB_CIS_MEDICAL_DOCUMENT
        WHERE {doc_where} ORDER BY JZLSH, WSLSH""", doc_params)
    summ = q(cn, f"""
        SELECT JZLSH, CYSJ, YYZTBBT1, YYZTB1, YYZTBBT2, YYZTB2,
               {', '.join(c for c, _ in SUMMARY_COL2SEC)}
        FROM TB_CIS_LEAVEHOSPITAL_SUMMARY WHERE {summary_where}""", summary_params)
    if patient is not None:
        doc = doc[doc["ZW"].map(_valid)].copy()
        doc["JLSJ"] = doc["JLSJ"].map(_optional_time)
        _internal_id(doc, "JZLSH", patient)
        _internal_id(summ, "JZLSH", patient)
    # 05 判定: WSLB 或 WSMC 正则 (v2 医院侧 WSLB 可空, 按文书名称派生)
    is05 = (doc["WSLB"] == "05") | doc["WSMC"].str.contains("出院小结|出院记录", regex=True, na=False)
    ext05_pids = set(doc.loc[is05, "JZLSH"])
    rows: list[dict] = _summary_to_notes(summ[~summ["JZLSH"].isin(ext05_pids)])
    for r in doc.itertuples(index=False):
        ts = "" if str(r.JLSJ).startswith("1900-01-01") else r.JLSJ
        sections = split_sections(str(r.ZW)) if (not r.DLBT and "【" in str(r.ZW)) else []
        if not sections:
            rows.append({"住院号": r.JZLSH, "事件时间": ts, "阶段": r.WSMC,
                         "子阶段": r.DLBT, "内容": r.ZW, "来源文件": "data_hub"})
            continue
        # 标记前的导语 (含主诉/入院时间等) 保留为无子阶段行
        preamble = str(r.ZW)[: str(r.ZW).index("【")].strip()
        if preamble:
            rows.append({"住院号": r.JZLSH, "事件时间": ts, "阶段": r.WSMC,
                         "子阶段": "", "内容": preamble, "来源文件": "data_hub"})
        for title, body in sections:
            rows.append({"住院号": r.JZLSH, "事件时间": ts, "阶段": r.WSMC,
                         "子阶段": title, "内容": body, "来源文件": "data_hub"})
    return pd.DataFrame(rows, columns=["住院号", "事件时间", "阶段", "子阶段", "内容", "来源文件"])


def _zd_frame(ba_id, mainflag, name, code, sn) -> pd.DataFrame:
    return pd.DataFrame({
        "ba_id": ba_id, "maindiag_flag": mainflag,
        "inhosp_diag_name": name, "inhosp_diag_code": code,
        "diag_name": name, "diag_code": code,
        "ipt_medcas_hmpg_sn": sn,
    })


def fetch_zd(cn, pids: list[str] | None, yq2org: dict[str, str], *, patient=None) -> pd.DataFrame:
    """诊断 → shi_zd (7 列契约). BA_HOSPS 院区走病案首页 (SYJBK 主诊锚 + SYZDK 列表),
    其他院区维持 IH_DIAGNOSIS_DETAIL 现状.

    harden-onsite-redlines D5: 源选择按患者粒度 — 只有 SYJBK 真有行的患者剔除 IH 行,
    缺首页行的 szx 患者保留 IH 诊断 (此前整院区剔除 → 诊断/手术双清零)."""
    ba_in = ",".join(f"'{h}'" for h in BA_HOSPS)
    ih_where, ih_params = _scope(pids, "JZLSH", patient)
    ba_where, ba_params = _scope(pids, "SYXH", patient, key="syxh")
    if patient is not None:
        yq2org = {patient.hospital: patient.hospital}
        ba_filter = ba_where
    else:
        ba_filter = f"YLJGYQDM IN ({ba_in}) AND {ba_where}"

    # ── IH 全院区取 (BA 院区行的去留按患者定, 见下) ──
    zd = q(cn, f"""
        SELECT YLJGYQDM, JZLSH, ZDBM, ZDSM, CYZDBZ FROM TB_IH_DIAGNOSIS_DETAIL
        WHERE {ih_where}
        ORDER BY JZLSH, ZYZDLSH""", ih_params)
    _internal_id(zd, "JZLSH", patient)

    # ── BA 院区: 主诊断 = SYJBK.ZYZD; 列表 = SYZDK (ZDXH 排序) ──
    jbk = q(cn, f"""
        SELECT YLJGYQDM, SYXH, ZYZD FROM TB_BA_SYJBK
        WHERE {ba_filter}""", ba_params)
    # fix-scan-residuals: 主诊 ZYZD 空串不构成 BA 覆盖 — 剔空后 ba_keys/main/zyzd_of 一致,
    # SYJBK 有行但 ZYZD 空的患者回退 IH (否则 IH 被剔 + 生成一条空主诊, 比 per-patient 回退前更糟).
    if len(jbk):
        jbk = jbk[jbk["ZYZD"].fillna("").astype(str).str.strip() != ""]
    zdk = q(cn, f"""
        SELECT YLJGYQDM, SYXH, ZDXH, ZDDM, ZDMC FROM TB_BA_SYZDK
        WHERE {ba_filter}""", ba_params)

    # BA 源患者 = SYJBK 真有行的 (院区, 患者); 其 IH 行剔除, 其余患者保留 IH.
    # SYZDK 也只取这些患者 (主诊锚不存在时整个患者回退 IH, 不半拉混源).
    ba_keys = set(zip(jbk["YLJGYQDM"], jbk["SYXH"])) if len(jbk) else set()
    if ba_keys and len(zd):
        zd = zd[[(yq, jz) not in ba_keys
                 for yq, jz in zip(zd["YLJGYQDM"], zd["JZLSH"])]]
    if ba_keys and len(zdk):
        zdk = zdk[[(yq, jz) in ba_keys
                   for yq, jz in zip(zdk["YLJGYQDM"], zdk["SYXH"])]]

    ih = _zd_frame(
        zd["YLJGYQDM"].map(yq2org).fillna("") + "-" + zd["JZLSH"],
        zd["CYZDBZ"].map(lambda v: 1 if v == "1" else 0),
        zd["ZDSM"].replace("-", ""), zd["ZDBM"].replace("-", ""),
        zd.groupby("JZLSH").cumcount() + 1)

    if len(jbk) == 0:
        return ih

    # ZYZD 码→名: 先全局 SYZDK, 再 IH, 再前缀5 (SYZDK 与 ZYZD 编码体系不同, 精确锚定率仅 3/4701;
    # 前缀5 再救 ~300, 剩 ~4% 字典无此码保留裸码)
    codes = [c for c in jbk["ZYZD"].unique().tolist() if c]
    name_map: dict[str, str] = {}
    prefix_map: dict[str, str] = {}
    if codes:
        dict_scope = "YLJGYQDM=? AND " if patient is not None else ""
        dict_params = (patient.hospital,) if patient is not None else ()
        cin = ",".join("'" + c.replace("'", "") + "'" for c in codes)
        pin = ",".join("'" + c[:5].replace("'", "") + "%'" for c in set(c[:5] for c in codes))
        for sql, cc, nc in [
            (f"SELECT DISTINCT ZDDM, ZDMC FROM TB_BA_SYZDK WHERE {dict_scope}ZDDM IN ({cin})", "ZDDM", "ZDMC"),
            (f"SELECT DISTINCT ZDBM, ZDSM FROM TB_IH_DIAGNOSIS_DETAIL WHERE {dict_scope}ZDBM IN ({cin})", "ZDBM", "ZDSM"),
        ]:
            d = q(cn, sql, dict_params)
            for code, name in zip(d[cc], d[nc]):
                name_map.setdefault(code, name)
        like = " OR ".join(f"ZDDM LIKE {p}" for p in pin.split(","))
        like_ih = " OR ".join(f"ZDBM LIKE {p}" for p in pin.split(","))
        for sql, cc, nc in [
            (f"SELECT DISTINCT ZDDM, ZDMC FROM TB_BA_SYZDK WHERE {dict_scope}({like})", "ZDDM", "ZDMC"),
            (f"SELECT DISTINCT ZDBM, ZDSM FROM TB_IH_DIAGNOSIS_DETAIL WHERE {dict_scope}({like_ih})", "ZDBM", "ZDSM"),
        ]:
            d = q(cn, sql, dict_params)
            for code, name in zip(d[cc], d[nc]):
                prefix_map.setdefault(str(code)[:5], name)

    def _resolve(code: str) -> str:
        return name_map.get(code) or prefix_map.get(str(code)[:5], "")

    jbk_ba = jbk["YLJGYQDM"].map(yq2org).fillna("") + "-" + jbk["SYXH"]
    main = _zd_frame(jbk_ba, 1,
                     jbk["ZYZD"].map(_resolve).fillna(""), jbk["ZYZD"], 1)

    if len(zdk):
        zdk = zdk.assign(_sn=pd.to_numeric(zdk["ZDXH"], errors="coerce").fillna(999)) \
                 .sort_values(["SYXH", "_sn"])
        # 与主诊同码的行剔除 (避免重复; 实测极少)
        zyzd_of = dict(zip(jbk["SYXH"], jbk["ZYZD"]))
        zdk = zdk[zdk.apply(lambda r: r["ZDDM"] != zyzd_of.get(r["SYXH"], ""), axis=1)]
        sec = _zd_frame(
            zdk["YLJGYQDM"].map(yq2org).fillna("") + "-" + zdk["SYXH"], 0,
            zdk["ZDMC"].replace("-", ""), zdk["ZDDM"].replace("-", ""),
            zdk.groupby("SYXH").cumcount() + 2)
    else:
        sec = _zd_frame(pd.Series(dtype=str), pd.Series(dtype=int),
                        pd.Series(dtype=str), pd.Series(dtype=str), pd.Series(dtype=int))

    return pd.concat([ih, main, sec], ignore_index=True).sort_values(
        ["ba_id", "ipt_medcas_hmpg_sn"]).reset_index(drop=True)


def _ss_frame(ba_id, name, code, mainflag, date, lv, anst, dr, anst_dr) -> pd.DataFrame:
    return pd.DataFrame({
        "ba_id": ba_id, "oprn_oprt_name": name, "oprn_oprt_code": code,
        "main_oprn_flag": mainflag, "oprn_oprt_date": date, "oprn_lv_name": lv,
        "anst_mtd_name": anst, "oper_dr_name": dr, "anst_dr_name": anst_dr,
    })


def fetch_ss(cn, pids: list[str] | None, yq2org: dict[str, str], *, patient=None) -> pd.DataFrame:
    """手术 → shi_ss (9 列契约). BA_HOSPS 走病案首页 SYSSK⋈OPRATION_DETAIL (SFZYSS 主手术标志),
    其他院区维持 OPRATION_DETAIL 现状.

    v2 (46表标准化): SSKSSJ 日期回退改标准表 OPRATION_DETAIL (旧 SYSSK_EXT join 实测 0 行生效,
    且术者/麻醉/时间标准表已承载) — 医院无需提供 SYSSK_EXT.

    harden-onsite-redlines D5: per-patient 源选择 — 只有 SYSSK 真有行的患者剔除
    OPRATION 行, 缺首页手术行的 szx 患者保留 IH 侧手术."""
    ba_in = ",".join(f"'{h}'" for h in BA_HOSPS)
    operation_table = "TB_OPERATION_DETAIL" if patient is not None else "TB_OPRATION_DETAIL"
    op_where, op_params = _scope(pids, "JZLSH", patient)
    ba_where, ba_params = _scope(pids, "s.SYXH", patient, key="syxh", hospital_column="s.YLJGYQDM")
    if patient is not None:
        yq2org = {patient.hospital: patient.hospital}
        # 首页与operation不再用SYXH=JZLSH错误连接；上下文给出真实就诊号。
        op_join = "s.YLJGYQDM=o.YLJGYQDM AND o.JZLSH=? AND s.SSXH=o.SSXH"
        ba_filter = ba_where
        ba_params = (patient.visit,) + ba_params
    else:
        op_join = "s.YLJGYQDM=o.YLJGYQDM AND s.SYXH=o.JZLSH AND s.SSXH=o.SSXH"
        ba_filter = f"s.YLJGYQDM IN ({ba_in}) AND {ba_where}"

    ss = q(cn, f"""
        SELECT YLJGYQDM, JZLSH, SSCZMC, SSCZBM, ZCBZ, SSKSSJ, SSJB, MZFS, SXYHRYXM, MZYHRYXM
        FROM {operation_table}
        WHERE {op_where}
        ORDER BY JZLSH, SSMXLSH""", op_params)
    _internal_id(ss, "JZLSH", patient)

    ba = q(cn, f"""
        SELECT s.YLJGYQDM, s.SYXH, s.SSXH, s.SSRQ, s.SSDM, s.SSMC, s.SSJB, s.MZFS,
               s.SSYS, s.MZYS, s.SFZYSS, o.SSKSSJ
        FROM TB_BA_SYSSK s
        LEFT JOIN (SELECT YLJGYQDM, JZLSH, SSXH, MIN(SSKSSJ) AS SSKSSJ
                   FROM {operation_table} GROUP BY YLJGYQDM, JZLSH, SSXH) o
          ON {op_join}
        WHERE {ba_filter}
        ORDER BY s.SYXH, s.SSXH""", ba_params)

    ba_keys = set(zip(ba["YLJGYQDM"], ba["SYXH"])) if len(ba) else set()
    if ba_keys and len(ss):
        ss = ss[[(yq, jz) not in ba_keys
                 for yq, jz in zip(ss["YLJGYQDM"], ss["JZLSH"])]]

    op = _ss_frame(
        ss["YLJGYQDM"].map(yq2org).fillna("") + "-" + ss["JZLSH"],
        ss["SSCZMC"].replace("-", ""), ss["SSCZBM"].replace("-", ""),
        ss["ZCBZ"].map(lambda v: 1 if v == "1" else 0),
        clean_dt(ss["SSKSSJ"]).str[:10],
        ss["SSJB"].replace("-", ""), ss["MZFS"].replace("-", ""),
        ss["SXYHRYXM"].replace("-", ""), ss["MZYHRYXM"].replace("-", ""))

    if len(ba) == 0:
        return op

    def _iso(d: str) -> str:
        d = (d or "").strip()
        if patient is not None and len(d) > 8:
            return _optional_time(d)[:10]
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return d[:10] if not d.startswith("1900-01-01") else ""

    date = ba["SSRQ"].map(_iso)
    date = date.where(date != "", clean_dt(ba["SSKSSJ"].fillna("")).str[:10])
    syssk = _ss_frame(
        ba["YLJGYQDM"].map(yq2org).fillna("") + "-" + ba["SYXH"],
        ba["SSMC"].replace("-", ""), ba["SSDM"].replace("-", ""),
        ba["SFZYSS"].map(lambda v: 1 if v == "1" else 0),
        date,
        ba["SSJB"].map({"1": "一级", "2": "二级", "3": "三级", "4": "四级"}).fillna(ba["SSJB"]),
        ba["MZFS"].replace("-", ""),
        ba["SSYS"].replace("-", ""), ba["MZYS"].replace("-", ""))

    return pd.concat([op, syssk], ignore_index=True).reset_index(drop=True)


def fetch_labs(cn, pids: list[str] | None, *, patient=None) -> pd.DataFrame:
    """检验: INDICATORS ⋈ REPORT → lab_results (16 列契约)."""
    where, params = _scope(pids, "r.JZLSH", patient, hospital_column="r.YLJGYQDM")
    lab = q(cn, f"""
        SELECT r.JZLSH, i.JYZBMC, i.JYZBDM, i.JYZBJG, i.JLDW, i.CKZ, i.YCTS,
               r.SQKS, r.BGSJ, r.BBMC, r.BGDLB, r.BRNL, r.BRXB, r.BGYHRYXM, r.SHYHRYXM
        FROM TB_LIS_INDICATORS i
        JOIN TB_LIS_REPORT r ON i.YLJGYQDM=r.YLJGYQDM AND i.BGDH=r.BGDH AND i.BGRQ=r.BGRQ
        WHERE {where}""", params)
    _internal_id(lab, "JZLSH", patient)
    return pd.DataFrame({
        "zyh": lab["JZLSH"],
        "rpt_itemname": lab["JYZBMC"],
        "rpt_itemcode": lab["JYZBDM"].replace("-", ""),
        "result": lab["JYZBJG"],
        "result_unit": lab["JLDW"].replace("-", ""),
        "result_ref": lab["CKZ"].replace("-", ""),
        "result_flag": lab["YCTS"].map(YCTS2FLAG).fillna(""),
        "diagnosisOpinion": "",
        "department": lab["SQKS"].replace("-", ""),
        "report_dt": clean_dt(lab["BGSJ"]),
        "specimen": lab["BBMC"].replace("-", ""),
        "inspectionName": lab["BGDLB"].replace("-", ""),
        "age": lab["BRNL"],
        "sex": lab["BRXB"].map({"1": "男", "2": "女"}).fillna(""),
        "trier": lab["BGYHRYXM"].replace("-", ""),
        "auditor": lab["SHYHRYXM"].replace("-", ""),
    })


def fetch_exams(cn, pids: list[str] | None, *, patient=None) -> pd.DataFrame:
    """检查: RIS_REPORT ∪ RIS_REPORT2 → examinations (15 列契约)."""
    where, params = _scope(pids, "JZLSH", patient)
    r1 = q(cn, f"""
        SELECT JZLSH, EXAMTYPE, JCMC, YXZD AS concl, YXBX AS descr, JCBW, JCKS, JCSJ, BGSJ,
               BGLCZD AS diag, YYS AS pos, BRXB, BGYHRYXM, SHYHRYXM
        FROM TB_RIS_REPORT WHERE {where}""", params)
    r2 = q(cn, f"""
        SELECT JZLSH, EXAMTYPE, JCMC, JCBGJG AS concl, BT1NR AS descr, JCBW, JCKS, JCSJ, BGSJ,
               BT2NR AS diag, JCJGDM AS pos, BRXB, BGYHRYXM, SHYHRYXM
        FROM TB_RIS_REPORT2 WHERE {where}""", params)
    r = pd.concat([r1, r2], ignore_index=True)
    _internal_id(r, "JZLSH", patient)
    return pd.DataFrame({
        "zyh": r["JZLSH"],
        "checkType": r["EXAMTYPE"].replace("-", ""),
        "checkItemName": r["JCMC"].replace("-", ""),
        "checkConclusion": r["concl"].replace("-", ""),
        "checkDescribe": r["descr"].replace("-", ""),
        "checkPosition": r["JCBW"].replace("-", ""),
        "department": r["JCKS"].replace("-", ""),
        "checkDate": clean_dt(r["JCSJ"]),
        "reportDate": clean_dt(r["BGSJ"]),
        "diagnosis": r["diag"].replace("-", ""),
        "isPos": "",
        "age": "",
        "Sex": r["BRXB"].map({"1": "男", "2": "女"}).fillna(""),
        "reporter": r["BGYHRYXM"].replace("-", ""),
        "auditor": r["SHYHRYXM"].replace("-", ""),
    })
