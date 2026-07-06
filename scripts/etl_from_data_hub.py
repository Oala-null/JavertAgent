#!/usr/bin/env python3
"""反向取数桥: 142 TP_data_hub (TB_* 国标表) → Javert 内部 6 文件 (流 B).

用法:
    uv run python scripts/etl_from_data_hub.py --patients 211530148,J66252 --output data_import_hub
    uv run python scripts/etl_from_data_hub.py --all --output data_import_hub

产出列契约 = configs/schema_manifest.yaml 各 spoke 的 output_schema (v0.7 外部数据同一契约),
跑审计:
    export JAVERT_DATA_DIR=data_import_hub JAVERT_ZD_FILE=shi_zd.csv JAVERT_SS_FILE=shi_ss.csv \\
           JAVERT_LABS_FILE=lab_results.csv JAVERT_EXAMINATIONS_FILE=examinations.csv \\
           JAVERT_SQL_ENABLED=false
    uv run javert audit-patient 211530148 --use-router --concurrency 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
import pyodbc

CS = ("DRIVER={ODBC Driver 18 for SQL Server};SERVER=192.168.31.142,1433;DATABASE=TP_data_hub;"
      "UID=machendong;PWD=Jyn_Machendong;TrustServerCertificate=yes;Encrypt=no;LoginTimeout=60")

# MXFYLB 统一 2 位码 → 中文 (与 build_data_hub_filled.MXFYLB_DICT 一致; szx 侧 EXT 无中文类别时回填)
MXFYLB2CN = {
    "01": "床位", "02": "诊查", "03": "检查", "04": "化验", "05": "治疗", "06": "手术",
    "07": "麻醉", "08": "护理", "09": "材料", "10": "西药", "11": "中成药", "12": "草药",
    "13": "输血", "14": "输氧", "15": "饮食", "16": "专护", "17": "CT", "18": "拍片",
    "19": "透视", "20": "病理", "99": "其他",
}
YCTS2FLAG = {"1": "正常", "2": "异常", "3": "偏高", "4": "偏低"}
SENT = "1900-01-01 00:00:00"


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patients", help="逗号分隔 JZLSH 列表")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--output", default="data_import_hub")
    args = ap.parse_args()
    if not args.all and not args.patients:
        ap.error("--patients 或 --all 二选一")
    pids = None if args.all else [p.strip() for p in args.patients.split(",")]
    out = Path(args.output)
    out.mkdir(exist_ok=True)

    cn = pyodbc.connect(CS, timeout=60)
    hosp = q(cn, "SELECT YLJGYQDM, YYJC FROM TB_DIC_HOSPITAL")
    yq2org = dict(zip(hosp["YLJGYQDM"], hosp["YYJC"]))  # 院区码 → 原机构码 (建库时逆映射存 YYJC)

    # ── 费用: FS ⋈ EXT → shi_fee.csv (36 列契约) ──
    fee = q(cn, f"""
        SELECT f.YLJGYQDM, f.SFMXID, f.STFBZ, f.JZLSH, f.MXFYLB, f.FYFSSJ, f.MXXMBM, f.MXXMBMYB,
               f.MXXMMC, f.MXXMDJ, f.MXXMSL, f.MXXMJE,
               e.CHRGITM_LV, e.LIST_TYPE, e.MED_LIST_CODG, e.MEDINS_LIST_CODG, e.PRODNAME, e.SPEC,
               e.DOSFORM_NAME, e.BILG_DEPT_CODG, e.BILG_DEPT_NAME, e.BILG_DR_CODG, e.BILG_DR_NAME,
               e.ACORD_DEPT_CODG, e.ACORD_DEPT_NAME, e.ORDERS_DR_CODE, e.ORDERS_DR_NAME,
               e.DSCG_TKDRUG_FLAG, e.FEE_TYPE, e.MEDINS_CHRGITM_TYPE, e.HOSP_APPR_FLAG,
               e.PRIC_UPLMT_AMT, e.SELFPAY_PROP, e.FULAMT_OWNPAY_AMT, e.OVERLMT_AMT,
               e.PRESELFPAY_AMT, e.INSCP_SCP_AMT
        FROM TB_HIS_ZY_FEE_DETAIL_FS f
        LEFT JOIN TB_HIS_ZY_FEE_DETAIL_EXT e ON f.YLJGYQDM=e.YLJGYQDM AND f.SFMXID=e.SFMXID
        WHERE {in_clause(pids, 'f.JZLSH')}""")
    sign = fee["STFBZ"].map(lambda v: -1 if v == "2" else 1)
    cat = fee["MEDINS_CHRGITM_TYPE"].where(fee["MEDINS_CHRGITM_TYPE"] != "",
                                           fee["MXFYLB"].map(MXFYLB2CN).fillna("其他"))
    shi_fee = pd.DataFrame({
        "bah": fee["YLJGYQDM"].map(yq2org).fillna("") + "-" + fee["JZLSH"],
        "feedetl_sn": fee["SFMXID"],
        "fee_ocur_time": fee["FYFSSJ"],
        "cnt": pd.to_numeric(fee["MXXMSL"], errors="coerce").fillna(0) * sign,
        "pric": fee["MXXMDJ"],
        "det_item_fee_sumamt": pd.to_numeric(fee["MXXMJE"], errors="coerce").fillna(0) * sign,
        "pric_uplmt_amt": fee["PRIC_UPLMT_AMT"], "selfpay_prop": fee["SELFPAY_PROP"],
        "fulamt_ownpay_amt": fee["FULAMT_OWNPAY_AMT"], "overlmt_amt": fee["OVERLMT_AMT"],
        "preselfpay_amt": fee["PRESELFPAY_AMT"], "inscp_scp_amt": fee["INSCP_SCP_AMT"],
        "chrgitm_lv": fee["CHRGITM_LV"], "list_type": fee["LIST_TYPE"],
        "med_list_codg": fee["MED_LIST_CODG"].where(fee["MED_LIST_CODG"] != "", fee["MXXMBMYB"]),
        "medins_list_codg": fee["MEDINS_LIST_CODG"].where(fee["MEDINS_LIST_CODG"] != "", fee["MXXMBM"]),
        "medins_list_name": fee["MXXMMC"],
        "med_chrgitm_type": "", "prodname": fee["PRODNAME"], "spec": fee["SPEC"],
        "dosform_name": fee["DOSFORM_NAME"],
        "bilg_dept_codg": fee["BILG_DEPT_CODG"], "bilg_dept_name": fee["BILG_DEPT_NAME"],
        "bilg_dr_codg": fee["BILG_DR_CODG"], "bilg_dr_name": fee["BILG_DR_NAME"],
        "acord_dept_codg": fee["ACORD_DEPT_CODG"], "acord_dept_name": fee["ACORD_DEPT_NAME"],
        "orders_dr_code": fee["ORDERS_DR_CODE"], "orders_dr_name": fee["ORDERS_DR_NAME"],
        "dscg_tkdrug_flag": fee["DSCG_TKDRUG_FLAG"], "fee_type": fee["FEE_TYPE"],
        "medins_chrgitm_type": cat, "hosp_appr_flag": fee["HOSP_APPR_FLAG"],
        "hospital": fee["YLJGYQDM"].map(yq2org).fillna(""),
        "create_time": "", "id": fee["SFMXID"],
    })
    shi_fee.to_csv(out / "shi_fee.csv", index=False, encoding="utf-8-sig")

    # ── 文书: MEDICAL_DOCUMENT → case_notes.csv (6 列契约) ──
    doc = q(cn, f"""
        SELECT JZLSH, JLSJ, WSMC, DLBT, ZW FROM TB_CIS_MEDICAL_DOCUMENT
        WHERE {in_clause(pids, 'JZLSH')} ORDER BY JZLSH, WSLSH""")
    notes = pd.DataFrame({
        "住院号": doc["JZLSH"],
        "事件时间": clean_dt(doc["JLSJ"]),
        "阶段": doc["WSMC"],
        "子阶段": doc["DLBT"],
        "内容": doc["ZW"],
        "来源文件": "data_hub",
    })
    notes.to_csv(out / "case_notes.csv", index=False, encoding="utf-8-sig")

    # ── 诊断: IH_DIAGNOSIS_DETAIL → shi_zd.csv (7 列契约) ──
    zd = q(cn, f"""
        SELECT YLJGYQDM, JZLSH, ZDBM, ZDSM, CYZDBZ FROM TB_IH_DIAGNOSIS_DETAIL
        WHERE {in_clause(pids, 'JZLSH')} ORDER BY JZLSH, ZYZDLSH""")
    shi_zd = pd.DataFrame({
        "ba_id": zd["YLJGYQDM"].map(yq2org).fillna("") + "-" + zd["JZLSH"],
        "maindiag_flag": zd["CYZDBZ"].map(lambda v: 1 if v == "1" else 0),
        "inhosp_diag_name": zd["ZDSM"].replace("-", ""),
        "inhosp_diag_code": zd["ZDBM"].replace("-", ""),
        "diag_name": zd["ZDSM"].replace("-", ""),
        "diag_code": zd["ZDBM"].replace("-", ""),
        "ipt_medcas_hmpg_sn": zd.groupby("JZLSH").cumcount() + 1,
    })
    shi_zd.to_csv(out / "shi_zd.csv", index=False, encoding="utf-8-sig")

    # ── 手术: OPRATION_DETAIL → shi_ss.csv (9 列契约) ──
    ss = q(cn, f"""
        SELECT YLJGYQDM, JZLSH, SSCZMC, SSCZBM, ZCBZ, SSKSSJ, SSJB, MZFS, SXYHRYXM, MZYHRYXM
        FROM TB_OPRATION_DETAIL WHERE {in_clause(pids, 'JZLSH')} ORDER BY JZLSH, SSMXLSH""")
    shi_ss = pd.DataFrame({
        "ba_id": ss["YLJGYQDM"].map(yq2org).fillna("") + "-" + ss["JZLSH"],
        "oprn_oprt_name": ss["SSCZMC"].replace("-", ""),
        "oprn_oprt_code": ss["SSCZBM"].replace("-", ""),
        "main_oprn_flag": ss["ZCBZ"].map(lambda v: 1 if v == "1" else 0),
        "oprn_oprt_date": clean_dt(ss["SSKSSJ"]).str[:10],
        "oprn_lv_name": ss["SSJB"].replace("-", ""),
        "anst_mtd_name": ss["MZFS"].replace("-", ""),
        "oper_dr_name": ss["SXYHRYXM"].replace("-", ""),
        "anst_dr_name": ss["MZYHRYXM"].replace("-", ""),
    })
    shi_ss.to_csv(out / "shi_ss.csv", index=False, encoding="utf-8-sig")

    # ── 检验: INDICATORS ⋈ REPORT → lab_results.csv (16 列契约) ──
    lab = q(cn, f"""
        SELECT r.JZLSH, i.JYZBMC, i.JYZBDM, i.JYZBJG, i.JLDW, i.CKZ, i.YCTS,
               r.SQKS, r.BGSJ, r.BBMC, r.BGDLB, r.BRNL, r.BRXB, r.BGYHRYXM, r.SHYHRYXM
        FROM TB_LIS_INDICATORS i
        JOIN TB_LIS_REPORT r ON i.YLJGYQDM=r.YLJGYQDM AND i.BGDH=r.BGDH AND i.BGRQ=r.BGRQ
        WHERE {in_clause(pids, 'r.JZLSH')}""")
    labs = pd.DataFrame({
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
    labs.to_csv(out / "lab_results.csv", index=False, encoding="utf-8-sig")

    # ── 检查: RIS_REPORT ∪ RIS_REPORT2 → examinations.csv (15 列契约) ──
    r1 = q(cn, f"""
        SELECT JZLSH, EXAMTYPE, JCMC, YXZD AS concl, YXBX AS descr, JCBW, JCKS, JCSJ, BGSJ,
               BGLCZD AS diag, YYS AS pos, BRXB, BGYHRYXM, SHYHRYXM
        FROM TB_RIS_REPORT WHERE {in_clause(pids, 'JZLSH')}""")
    r2 = q(cn, f"""
        SELECT JZLSH, EXAMTYPE, JCMC, JCBGJG AS concl, BT1NR AS descr, JCBW, JCKS, JCSJ, BGSJ,
               BT2NR AS diag, JCJGDM AS pos, BRXB, BGYHRYXM, SHYHRYXM
        FROM TB_RIS_REPORT2 WHERE {in_clause(pids, 'JZLSH')}""")
    r = pd.concat([r1, r2], ignore_index=True)
    exams = pd.DataFrame({
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
    exams.to_csv(out / "examinations.csv", index=False, encoding="utf-8-sig")

    print(f"→ {out}/")
    for name, df, pidcol in [("shi_fee", shi_fee, "bah"), ("case_notes", notes, "住院号"),
                             ("shi_zd", shi_zd, "ba_id"), ("shi_ss", shi_ss, "ba_id"),
                             ("lab_results", labs, "zyh"), ("examinations", exams, "zyh")]:
        print(f"  {name:14s} {len(df):>8,d} 行  {df[pidcol].nunique()} 患者")


if __name__ == "__main__":
    sys.exit(main())
