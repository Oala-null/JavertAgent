# -*- coding: utf-8 -*-
"""单患者概览 — 复用 CsvLoader 已加载的 notes/fees DataFrame, 现场算 basics.

参考 scripts/build_clerk_report.py 的 render_patient_tab, 但精简成 per-patient
on-demand 调用 (不是全患者一次扫). 用 functools.lru_cache 缓存按 patient_id 命中.

数据来源:
  - 病案首页主诊/其他诊断:  data/shi_zd.xls (lazy 全表加载 + 进程缓存)
  - 病案首页手术:           data/shi_ss.xls (同上)
  - 主诉/现病史/既往史/麻醉等: case_notes.csv 子阶段 (CsvLoader 已加载)
  - 费用 / 科室 / 医师 / 项目类别: shi_fee.csv (同上)
"""

from __future__ import annotations

import copy
import logging
import re
from collections import defaultdict
from datetime import datetime
from functools import lru_cache
from typing import Any


# 中文病例特点开头常见模式 (覆盖):
#   "石凤玲，女，45岁，因..."   (姓名+逗号+性别+逗号)
#   "患者 女，36岁"             (患者+空格+性别+逗号)
#   "X女，45 岁因..."            (姓名直接接性别)
# 隔符放宽到 [，, \t]+ , 性别和年龄之间也允许空白
_SEX_AGE_PATTERN = re.compile(r"[，, \t]\s*([男女])\s*[，, \t]\s*(\d{1,3})\s*岁")
# szx 等外部文书形态: "性别男性，年龄57岁"
_SEX_AGE_PATTERN2 = re.compile(r"性别\s*([男女])性?\s*[，,].{0,8}?年龄\s*(\d{1,3})\s*岁")

from javert.config import get_config
from javert.data.csv_loader import CsvLoader
from javert.data.fee_netting import fee_group_key, net_fee_items

logger = logging.getLogger("javert.web.patient_overview")


_KEY_NOTES = {
    "主诉": "chief_complaint",
    "现病史": "history",
    "既往史": "past_history",
    "性别": "gender",
    "年龄": "age",
    "婚姻": "marital",
    "拟施手术名称": "planned_surgery",
    "拟定手术名称": "planned_surgery2",
    "手术名称": "actual_surgery",
    "术后诊断": "post_dx",
    "病理诊断": "pathology",
    "麻醉方法": "anesthesia_method",
    "麻醉分级（ASA分级）": "asa",
}

_DX_SUBS = ("出院诊断", "入院诊断", "临床诊断", "主要诊断", "其他诊断", "术后诊断")


# =========================================================
# shi_zd.xls / shi_ss.xls lazy 全表 → dict[pid, ...]
# =========================================================
_ZD_CACHE: dict[str, dict] | None = None
_SS_CACHE: dict[str, list[dict]] | None = None
# v0.9 (enhance-workbench-usability, D6): sidebar 卡片 ¥fees_sum 进程级一次性 groupby 缓存,
# 避免百卡逐个对十万行 shi_fee 切片. 与 _ZD_CACHE / _SS_CACHE 同生命周期, 走 reset_caches().
_FEES_SUM_CACHE: dict[str, float] | None = None
# add-visual-schema-onboarding: 声明的 stored 新表 (data_import/_stored_spokes.json) 进程缓存.
_STORED_CACHE: dict | None = None


def _overlay_csv(df, filename: str):
    """web coexist: 把 data_import/<filename> 追加到 base df (让接入病人与 shi 病人并存).

    不改 base 文件; data_import 缺失或读失败 → 原样返回. 仅工作台渲染侧用 (审计侧读 data_import 自身).
    """
    from javert.onboarding.etl_engine import PROJECT_ROOT
    p = PROJECT_ROOT / "data_import" / filename
    if not p.exists():
        return df
    try:
        import pandas as pd
        ext = pd.read_csv(p, dtype=str, low_memory=False)
        return pd.concat([df, ext], ignore_index=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("overlay %s 叠加失败: %s", filename, e)
        return df


def _load_zd() -> dict[str, dict]:
    """shi_zd.xls 列名 (确认自实际文件):
      ba_id (形如 'H31010600042-J61556 '), maindiag_flag (0/1),
      inhosp_diag_name (入院诊断名), inhosp_diag_code (ICD-10),
      diag_name / diag_code (出院诊断, 部分行才有),
      ipt_medcas_hmpg_sn (诊断顺序), vali_flag, ...
    """
    global _ZD_CACHE
    if _ZD_CACHE is not None:
        return _ZD_CACHE
    out: dict[str, dict] = {}
    cfg = get_config()
    zd_path = cfg.zd_path
    if not zd_path.exists():
        _ZD_CACHE = out
        return out
    try:
        import pandas as pd
        if zd_path.suffix.lower() == ".csv":
            df = pd.read_csv(zd_path, dtype=str, low_memory=False)
        else:
            df = pd.read_excel(zd_path)
        df = _overlay_csv(df, "shi_zd.csv")  # coexist: 叠加 data_import 诊断
        # 兼容: id 列优先 ba_id (实际), fallback bah / 住院号
        id_col = next((c for c in ("ba_id", "bah", "住院号", "patient_id") if c in df.columns), None)
        flag_col = next((c for c in ("maindiag_flag", "main_flag") if c in df.columns), None)
        # 名称优先 inhosp_diag_name (入院诊断 ground truth), 后退 diag_name (出院诊断)
        name_col = next(
            (c for c in ("inhosp_diag_name", "diag_name", "dx_name", "诊断名称") if c in df.columns),
            None,
        )
        code_col = next(
            (c for c in ("inhosp_diag_code", "diag_code", "dx_code", "icd_code", "诊断编码") if c in df.columns),
            None,
        )
        order_col = next(
            (c for c in ("ipt_medcas_hmpg_sn", "dx_order", "order_no", "顺序") if c in df.columns),
            None,
        )
        if id_col is None or name_col is None:
            logger.warning(
                "shi_zd.xls 缺关键列 (id=%s name=%s flag=%s code=%s)",
                id_col, name_col, flag_col, code_col,
            )
            _ZD_CACHE = out
            return out
        for _, row in df.iterrows():
            raw_id = str(row[id_col] or "").strip()
            if not raw_id or raw_id == "nan":
                continue
            # ba_id 形如 "H31010600042-J61556 "; 提取末段 + 去空格
            pid = raw_id.split("-")[-1].strip()
            if not pid:
                continue
            entry = out.setdefault(pid, {"main": [], "others": []})
            name_v = row[name_col]
            if name_v is None or (isinstance(name_v, float) and pd.isna(name_v)):
                continue
            name = str(name_v).strip()
            if not name:
                continue
            code = ""
            if code_col and code_col in row.index:
                code_v = row[code_col]
                if code_v is not None and not (isinstance(code_v, float) and pd.isna(code_v)):
                    code = str(code_v).strip()
            order = 0
            if order_col and order_col in row.index:
                try:
                    order = int(float(row[order_col]))
                except (ValueError, TypeError):
                    order = 0
            # maindiag_flag 可能是 int / float (1.0); 用 float(...)==1 判定
            is_main = False
            if flag_col is not None:
                try:
                    is_main = float(row[flag_col]) == 1.0
                except (ValueError, TypeError):
                    is_main = str(row[flag_col]).strip() == "1"
            d = {"name": name, "code": code, "order": order}
            if is_main:
                entry["main"].append(d)
            else:
                entry["others"].append(d)
        # 主诊按 order 排, others 也排
        for pid_entry in out.values():
            pid_entry["main"].sort(key=lambda x: x["order"])
            pid_entry["others"].sort(key=lambda x: x["order"])
    except Exception as e:
        logger.warning("shi_zd.xls 加载失败: %s", e)
    _ZD_CACHE = out
    logger.info("shi_zd cache: %d patients", len(out))
    return out


def _load_ss() -> dict[str, list[dict]]:
    """shi_ss.xls 列名 (确认自实际文件):
      ba_id, oprn_oprt_name (临床版), oprn_oprt_code (ICD-9-CM3 临床),
      hi_oprn_oprt_name (医保版), hi_oprn_oprt_code (ICD-9-CM3 医保),
      main_oprn_flag (0/1), oprn_lv_name, anst_mtd_name,
      anst_dr_name, oper_dr_name, oprn_oprt_begntime (开始时间) /
      oprn_oprt_date (日期)
    """
    global _SS_CACHE
    if _SS_CACHE is not None:
        return _SS_CACHE
    out: dict[str, list[dict]] = defaultdict(list)
    cfg = get_config()
    ss_path = cfg.ss_path
    if not ss_path.exists():
        _SS_CACHE = dict(out)
        return _SS_CACHE
    try:
        import pandas as pd
        if ss_path.suffix.lower() == ".csv":
            df = pd.read_csv(ss_path, dtype=str, low_memory=False)
        else:
            df = pd.read_excel(ss_path)
        df = _overlay_csv(df, "shi_ss.csv")  # coexist: 叠加 data_import 手术
        id_col = next(
            (c for c in ("ba_id", "bah", "住院号", "patient_id") if c in df.columns),
            None,
        )
        if id_col is None:
            logger.warning("shi_ss.xls 缺 id 列, 跳过")
            _SS_CACHE = dict(out)
            return _SS_CACHE

        def _get(row, *names) -> str:
            for n in names:
                if n in df.columns:
                    v = row[n]
                    if v is None or (isinstance(v, float) and pd.isna(v)):
                        return ""
                    s = str(v).strip()
                    return "" if s.lower() in ("nan", "none") else s
            return ""

        from datetime import date, datetime, time

        def _date_str(row) -> str:
            """优先 oprn_oprt_date; 跳过 pure-time(00:00:00) / NaT / 缺失."""
            for col in ("oprn_oprt_date", "oprn_oprt_begntime"):
                if col not in df.columns:
                    continue
                v = row[col]
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    continue
                if isinstance(v, pd.Timestamp):
                    return v.strftime("%Y-%m-%d")
                if isinstance(v, datetime):
                    return v.strftime("%Y-%m-%d")
                if isinstance(v, date):
                    return v.strftime("%Y-%m-%d")
                # datetime.time without date 等于缺失日期 → 跳过这列, 试下一列
                if isinstance(v, time):
                    continue
                s = str(v).strip()
                # 字符串只有时间 (00:00:00 / 11:23:45) 也算缺失
                if not s or s in ("nan", "NaT"):
                    continue
                # 如果是纯 HH:MM:SS, 跳
                if len(s) <= 8 and ":" in s and "-" not in s and "/" not in s:
                    continue
                return s[:19]
            return ""

        for _, row in df.iterrows():
            raw_id = str(row[id_col] or "").strip()
            if not raw_id or raw_id == "nan":
                continue
            pid = raw_id.split("-")[-1].strip()
            if not pid:
                continue
            main_flag_v = row.get("main_oprn_flag")
            try:
                main_flag = 1 if float(main_flag_v) == 1.0 else 0
            except (ValueError, TypeError):
                main_flag = 1 if str(main_flag_v).strip() in ("1", "1.0") else 0
            entry = {
                "name": _get(row, "oprn_oprt_name"),
                "code": _get(row, "oprn_oprt_code"),
                "hi_name": _get(row, "hi_oprn_oprt_name"),
                "hi_code": _get(row, "hi_oprn_oprt_code"),
                "date": _date_str(row),
                "main": main_flag,
                "level": _get(row, "oprn_lv_name"),
                "anesthesia": _get(row, "anst_mtd_name"),
                "oper_dr": _get(row, "oper_dr_name"),
                "anst_dr": _get(row, "anst_dr_name"),
                "part": _get(row, "oprn_oper_part"),
            }
            if entry["name"]:
                out[pid].append(entry)
        # 主手术优先, 然后按日期
        for pid in out:
            out[pid].sort(key=lambda x: (-x["main"], x["date"]))
    except Exception as e:
        logger.warning("shi_ss.xls 加载失败: %s", e)
    _SS_CACHE = dict(out)
    logger.info("shi_ss cache: %d patients with surgeries", len(out))
    return _SS_CACHE


def _load_stored() -> dict:
    """声明的 stored 新表 (来自 /onboarding『声明新表』) → {meta, frames}. 进程缓存."""
    global _STORED_CACHE
    if _STORED_CACHE is not None:
        return _STORED_CACHE
    import json
    import pandas as pd
    from javert.config import PROJECT_ROOT
    base = PROJECT_ROOT / "data_import"
    out: dict = {"meta": [], "frames": {}}
    meta_path = base / "_stored_spokes.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            out["meta"] = meta if isinstance(meta, list) else []
            for e in out["meta"]:
                fp = base / e.get("file", "")
                if fp.exists():
                    out["frames"][e["file"]] = pd.read_csv(
                        fp, dtype=str, low_memory=False).fillna("")
        except Exception as ex:  # noqa: BLE001
            logger.warning("stored spokes 加载失败: %s", ex)
    _STORED_CACHE = out
    return _STORED_CACHE


def get_stored_spokes(patient_id: str, max_rows: int = 20) -> list[dict]:
    """该患者在 stored 新表里的原文行 (设计 D2: 存下且概览可见原文, 标暂不参与判定)."""
    data = _load_stored()
    result: list[dict] = []
    pid = str(patient_id).strip()
    for e in data["meta"]:
        df = data["frames"].get(e.get("file"))
        if df is None:
            continue
        pcol = e.get("patient_col")
        if pcol and pcol in df.columns:
            sub = df[df[pcol].astype(str).str.contains(pid, na=False, regex=False)]
        else:
            sub = df.iloc[0:0]
        result.append({
            "name": e.get("name"),
            "note": e.get("note", "已接收·暂不参与判定"),
            "columns": list(df.columns),
            "rows": sub.head(max_rows).values.tolist(),
            "total": int(len(sub)),
        })
    return result


# =========================================================
# 卡片摘要 (sidebar 富卡片用) — D6 进程级缓存
# =========================================================
def get_fees_sum_map(loader: CsvLoader | None = None) -> dict[str, float]:
    """对 shi_fee 一次 groupby(bah 末段 pid) 求 det_item_fee_sumamt 之和 → {pid: fees_sum}.

    进程级缓存一次 (D6). 缓存命中后 O(1) 查; sidebar 百卡逐个查不再扫全表.
    loader 提供时复用其已加载的 all_fees() (省一次读盘); 否则自读 cfg.fees_path.
    """
    global _FEES_SUM_CACHE
    if _FEES_SUM_CACHE is not None:
        return _FEES_SUM_CACHE
    out: dict[str, float] = {}
    try:
        import pandas as pd
        if loader is not None:
            df = loader.all_fees()
        else:
            cfg = get_config()
            fees_path = cfg.fees_path
            if not fees_path.exists():
                _FEES_SUM_CACHE = out
                return out
            df = pd.read_csv(fees_path, dtype={"bah": str}, low_memory=False)
            df = _overlay_csv(df, "shi_fee.csv")  # coexist (loader 缺省时也叠加)
        if df is None or "bah" not in df.columns:
            _FEES_SUM_CACHE = out
            return out
        amt = pd.to_numeric(df.get("det_item_fee_sumamt"), errors="coerce").fillna(0.0)
        # bah 形如 "H31010600042-J90508 " → pid = 末段去空格 (兼容外部 {hospital_code}-{pid})
        pid_series = df["bah"].astype(str).str.split("-").str[-1].str.strip()
        tmp = pd.DataFrame({"pid": pid_series, "amt": amt})
        grouped = tmp.groupby("pid")["amt"].sum()
        out = {
            str(k): float(v)
            for k, v in grouped.items()
            if k and str(k).lower() != "nan"
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("get_fees_sum_map 失败: %s", e)
    _FEES_SUM_CACHE = out
    logger.info("fees_sum cache: %d patients", len(out))
    return out


def get_primary_dx(patient_id: str, loader: CsvLoader | None = None) -> str:
    """主诊断 — 病案首页 maindiag_flag=1 优先 (复用 _load_zd 进程缓存),
    无首页则 note 派生 (出院/主要/入院/临床诊断), 再无则 "".
    """
    zd = _load_zd().get(patient_id)
    if zd and zd.get("main"):
        m = zd["main"][0]
        return f"{m['name']} ({m['code']})" if m.get("code") else m["name"]
    if loader is not None:
        try:
            notes_df = loader.get_notes(patient_id)
            nb = _extract_from_notes_df(notes_df)
            diags = nb["diagnoses"]
            cand = (
                diags.get("出院诊断") or diags.get("主要诊断")
                or diags.get("入院诊断") or diags.get("临床诊断") or []
            )
            if cand:
                return cand[0]
        except Exception:  # noqa: BLE001
            pass
    return ""


# =========================================================
# 单患者 basics 提取 (从 CsvLoader 已加载的 df)
# =========================================================
def _extract_from_notes_df(df) -> dict[str, Any]:
    """notes_df → diagnoses / fields / stages / notes_total.

    fields[sex|age] 优先走 _KEY_NOTES 子阶段直取 (基本不命中); fallback 在
    "病例特点" 内容 regex 抽 "X，男/女，N 岁" 模式 (临床文书通用开头).
    """
    out: dict[str, Any] = {
        "diagnoses": defaultdict(list),
        "fields": {},
        "notes_stages": defaultdict(int),
        "notes_total": 0,
    }
    if df is None or len(df) == 0:
        return out
    for _, r in df.iterrows():
        out["notes_total"] += 1
        stage = str(r.get("阶段", "") or "")
        sub = str(r.get("子阶段", "") or "")
        content = str(r.get("内容", "") or "").strip()
        out["notes_stages"][stage] += 1
        if sub in _DX_SUBS:
            out["diagnoses"][sub].append(content[:400])
        if sub in _KEY_NOTES:
            fk = _KEY_NOTES[sub]
            if fk not in out["fields"] or len(content) > len(out["fields"].get(fk, "")):
                out["fields"][fk] = content[:800]
        # 性别 / 年龄 regex fallback: 任意 note 内容前 300 字符 搜 "X，女，45岁" 模式;
        # 跳过 "姓名:某某" 这种已脱敏块, 它们的 patient demographics 无从恢复
        if ("gender" not in out["fields"] or "age" not in out["fields"]) and content:
            head = content[:300]
            if "某某" not in head:  # 脱敏文书没法救
                m = _SEX_AGE_PATTERN.search(head) or _SEX_AGE_PATTERN2.search(head)
                if m:
                    out["fields"].setdefault("gender", m.group(1))
                    out["fields"].setdefault("age", m.group(2) + "岁")
    out["diagnoses"] = dict(out["diagnoses"])
    out["notes_stages"] = dict(out["notes_stages"])
    return out


def _safe_float(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _extract_from_fees_df(df) -> dict[str, Any]:
    """fees_df → fees_count / fees_sum / dates / departments / doctors /
                  fee_items / fee_categories / drugs_materials"""
    out: dict[str, Any] = {
        "fees_count": 0,
        "fees_sum": 0.0,
        "dates": [],
        "departments": defaultdict(int),
        "doctors": defaultdict(int),
        "notes_stages": {},  # placeholder
        "fee_items": defaultdict(lambda: {"cnt": 0.0, "sum": 0.0, "unit_prices": set()}),
        "drugs_materials": defaultdict(lambda: {"cnt": 0.0, "sum": 0.0, "spec": ""}),
        # v0.9 (D8): 类别行挂明细 — items 按 (项目名, 国家码) 聚合 (编码/名称/次数/金额)
        "fee_categories": defaultdict(
            lambda: {
                "cnt": 0,
                "sum": 0.0,
                "items": defaultdict(
                    lambda: {
                        "name": "", "code_nat": "", "code_local": "",
                        "cnt": 0.0, "sum": 0.0, "refund_count": 0,
                    }
                ),
            }
        ),
    }
    if df is None or len(df) == 0:
        return out

    # fix-fee-refund-netting (4.1/4.3): 完全充退项不进明细/计数; 部分退显示净量 + 脚注
    net_items = net_fee_items(df)
    full_refunded = {k for k, it in net_items.items() if it.is_full_refund}

    for _, r in df.iterrows():
        out["fees_count"] += 1
        amt = _safe_float(r.get("det_item_fee_sumamt"))
        cnt = _safe_float(r.get("cnt"))
        pric = _safe_float(r.get("pric"))
        out["fees_sum"] += amt

        # date 解析: shi_fee 时间格式 dd/mm/yyyy 或 dd/mm/yyyy hh:mm:ss
        ts = str(r.get("fee_ocur_time", "") or "").split()[0] if r.get("fee_ocur_time") else ""
        if ts:
            for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
                try:
                    out["dates"].append(datetime.strptime(ts, fmt))
                    break
                except ValueError:
                    continue

        # 科室 / 医师 — 优先用 acord_ (开单), bilg_ (计费) 为 fallback;
        # pandas read_csv NaN → str(nan) == "nan", 显式过滤
        def _clean(v) -> str:
            s = "" if v is None else str(v).strip()
            return "" if s.lower() in ("nan", "none", "null", "") else s
        dn = _clean(r.get("acord_dept_name")) or _clean(r.get("bilg_dept_name"))
        if dn:
            out["departments"][dn] += 1
        dr = _clean(r.get("orders_dr_name")) or _clean(r.get("bilg_dr_name"))
        if dr:
            out["doctors"][dr] += 1

        item_name = str(r.get("medins_list_name", "") or "").strip()
        prod_name = str(r.get("prodname", "") or "").strip()
        spec = str(r.get("spec", "") or "").strip()
        cat = str(r.get("medins_chrgitm_type", "") or "").strip() or "(未分类)"

        # 完全充退项 (净≤0) 不进明细/计数 (4.1); fees_sum/dates/dept 等粗指标保留 (上方已累加)
        gkey = fee_group_key(str(r.get("med_list_codg", "") or "").strip(), item_name)
        if gkey in full_refunded:
            continue

        key = (item_name or prod_name or "(未命名)", spec or "-")
        out["fee_items"][key]["cnt"] += cnt
        out["fee_items"][key]["sum"] += amt
        if pric > 0:
            out["fee_items"][key]["unit_prices"].add(pric)

        if prod_name and prod_name != item_name:
            dkey = (prod_name, spec)
            out["drugs_materials"][dkey]["cnt"] += cnt
            out["drugs_materials"][dkey]["sum"] += amt
            out["drugs_materials"][dkey]["spec"] = spec

        catobj = out["fee_categories"][cat]
        catobj["cnt"] += 1
        catobj["sum"] += amt
        # 同行数据收集该类别明细 (不新增扫描, D8)
        code_nat = str(r.get("med_list_codg", "") or "").strip()
        code_local = str(r.get("medins_list_codg", "") or "").strip()
        disp_name = item_name or prod_name or "(未命名)"
        it = catobj["items"][(disp_name, code_nat)]
        it["name"] = disp_name
        it["code_nat"] = "" if code_nat.lower() == "nan" else code_nat
        it["code_local"] = "" if code_local.lower() == "nan" else code_local
        it["cnt"] += cnt
        it["sum"] += amt
        # 部分退脚注 (4.3): net helper 该组退费次数 (cnt 已是净量, 不展开退费行)
        netit = net_items.get(gkey)
        if netit is not None:
            it["refund_count"] = netit.refund_count

    return out


# =========================================================
# 主入口 — 拼装单患者概览数据
# =========================================================
def build_overview(patient_id: str, loader: CsvLoader) -> dict[str, Any]:
    """单患者概览数据 dict, 可直接传 Jinja2 模板.

    boost-llm-efficiency: lru_cache 按 (patient_id, loader 实例) 命中 — detail 页重开同患者
    不再重算. loader 换新实例 (reset_loader) 自然换 key; onboarding 载入新数据走
    reset_caches() 统一失效. fix-scan-residuals: 返回缓存 dict 的深拷贝, 调用方可安全修改
    (如就地补字段) 不污染缓存, 消跨请求串数据回归面.
    """
    return copy.deepcopy(_build_overview_cached(patient_id, loader))


@lru_cache(maxsize=256)
def _build_overview_cached(patient_id: str, loader: CsvLoader) -> dict[str, Any]:
    notes_df = loader.get_notes(patient_id)
    fees_df = loader.get_fees(patient_id)

    note_b = _extract_from_notes_df(notes_df)
    fee_b = _extract_from_fees_df(fees_df)

    dates = sorted(fee_b["dates"])
    admit_date = dates[0].strftime("%Y-%m-%d") if dates else "—"
    discharge_date = dates[-1].strftime("%Y-%m-%d") if dates else "—"
    los_days = (dates[-1] - dates[0]).days + 1 if dates else 0

    zd = _load_zd().get(patient_id, {"main": [], "others": []})
    ss = _load_ss().get(patient_id, [])
    diags = note_b["diagnoses"]
    fields = note_b["fields"]

    if zd["main"]:
        m = zd["main"][0]
        primary_dx = f"{m['name']} ({m['code']})" if m["code"] else m["name"]
        primary_source = "病案首页"
    else:
        cand = (diags.get("出院诊断") or diags.get("主要诊断")
                or diags.get("入院诊断") or diags.get("临床诊断") or [])
        primary_dx = cand[0] if cand else "(无)"
        primary_source = "病历文书 (病案首页无主诊断)"

    other_dx = (
        [f"{o['name']} ({o['code']})" if o["code"] else o["name"] for o in zd["others"][:10]]
        if zd["others"] else diags.get("其他诊断", [])[:10]
    )
    post_dx = diags.get("术后诊断", [])[:5]

    # top-N 排序
    top_depts = sorted(fee_b["departments"].items(), key=lambda kv: -kv[1])[:5]
    top_drs = sorted(fee_b["doctors"].items(), key=lambda kv: -kv[1])[:5]
    top_stages = sorted(note_b["notes_stages"].items(), key=lambda kv: -kv[1])[:8]

    fee_items_sorted = sorted(
        fee_b["fee_items"].items(),
        key=lambda kv: -kv[1]["sum"],
    )[:15]
    # 把 unit_prices 集合算成 min/max
    fee_items_display = []
    for (name, spec), v in fee_items_sorted:
        prices = sorted(v["unit_prices"]) if v["unit_prices"] else [0.0]
        price_range = (
            f"¥{prices[0]:,.2f}"
            if len(prices) == 1 else f"¥{prices[0]:,.2f}—{prices[-1]:,.2f}"
        )
        pct = (v["sum"] / fee_b["fees_sum"] * 100.0) if fee_b["fees_sum"] else 0.0
        fee_items_display.append({
            "name": name,
            "spec": spec,
            "cnt": v["cnt"],
            "price_range": price_range,
            "sum": v["sum"],
            "pct": pct,
        })

    drugs_sorted = sorted(
        fee_b["drugs_materials"].items(),
        key=lambda kv: -kv[1]["sum"],
    )[:8]
    drugs_display = [
        {"name": name, "spec": spec, "cnt": v["cnt"], "sum": v["sum"]}
        for (name, spec), v in drugs_sorted
    ]

    fee_cats_sorted = sorted(fee_b["fee_categories"].items(), key=lambda kv: -kv[1]["sum"])
    cat_max = max((v["sum"] for _, v in fee_cats_sorted), default=1.0)
    _CAT_ITEMS_TOP_N = 50  # 防超长 (实数据单类别明细通常 < 50)
    fee_cats_display = []
    for label, v in fee_cats_sorted:
        items_sorted = sorted(v["items"].values(), key=lambda x: -x["sum"])
        items_display = [
            {
                "name": it["name"],
                "code_nat": it["code_nat"],
                "code_local": it["code_local"],
                "cnt": it["cnt"],
                "sum": it["sum"],
                "refund_count": it.get("refund_count", 0),
            }
            for it in items_sorted[:_CAT_ITEMS_TOP_N]
        ]
        fee_cats_display.append({
            "label": label,
            "cnt": v["cnt"],
            "sum": v["sum"],
            "pct": (v["sum"] / fee_b["fees_sum"] * 100.0) if fee_b["fees_sum"] else 0.0,
            "bar_pct": (v["sum"] / cat_max * 100.0) if cat_max else 0.0,
            "items": items_display,
            "items_total": len(items_sorted),
            "items_truncated": len(items_sorted) > _CAT_ITEMS_TOP_N,
        })

    return {
        "patient_id": patient_id,
        "admit_date": admit_date,
        "discharge_date": discharge_date,
        "los_days": los_days,
        "notes_total": note_b["notes_total"],
        "fees_count": fee_b["fees_count"],
        "fees_sum": fee_b["fees_sum"],
        "gender": fields.get("gender", ""),
        "age": fields.get("age", ""),
        "anesthesia": fields.get("anesthesia_method", ""),
        "asa": fields.get("asa", ""),
        "chief_complaint": fields.get("chief_complaint", ""),
        "history": fields.get("history", ""),
        "past_history": fields.get("past_history", ""),
        "pathology": fields.get("pathology", ""),
        "actual_surgery": fields.get("actual_surgery", "")
                          or fields.get("planned_surgery", "")
                          or fields.get("planned_surgery2", ""),
        "top_depts": top_depts,
        "top_drs": top_drs,
        "top_stages": top_stages,
        "primary_dx": primary_dx,
        "primary_source": primary_source,
        "other_dx": other_dx,
        "post_dx": post_dx,
        "surgeries": ss,
        "fee_items": fee_items_display,
        "drugs": drugs_display,
        "fee_categories": fee_cats_display,
        "stored_spokes": get_stored_spokes(patient_id),
    }


def reset_caches() -> None:
    global _ZD_CACHE, _SS_CACHE, _FEES_SUM_CACHE, _STORED_CACHE
    _ZD_CACHE = None
    _SS_CACHE = None
    _FEES_SUM_CACHE = None
    _STORED_CACHE = None
    _build_overview_cached.cache_clear()
