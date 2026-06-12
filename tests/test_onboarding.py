# -*- coding: utf-8 -*-
"""onboarding 剖析/预检/视图工具测试 (add-visual-schema-onboarding Layer2).

日期探测用 design D5 的 8 格式族当用例; 预检验 D4 判据 (两 getter 解析非空).
"""

from __future__ import annotations

import pandas as pd
import pytest

from javert.onboarding.profiler import (
    detect_date_format,
    normalize_date_series,
    profile_column,
)


# ────────────── 日期逐列探测 (design D5 八格式族) ──────────────

def test_dmy_dayfirst_unpadded():
    # 一致的 DMY-带时间列 (主 shape 含时间, 归一保留时间)
    p = detect_date_format(["30/12/2025 11:05:44", "1/5/2025 08:00:00", "24/12/2025 09:30:00"])
    assert p.has_date and p.has_time and not p.is_time_only
    assert p.dayfirst is True and p.dayfirst_confident
    assert normalize_date_series(pd.Series(["30/12/2025 11:05:44"]), p).iloc[0] == "2025-12-30 11:05:44"


def test_iso_not_dayfirst():
    p = detect_date_format(["2026-01-05", "2025-12-31"])
    assert p.dayfirst is False
    # 关键: 全局 dayfirst=True 会把 2026-01-05 错读成 2026-05-01; 逐列探测必须避免
    assert normalize_date_series(pd.Series(["2026-01-05"]), p).iloc[0] == "2026-01-05"


def test_iso_fractional_seconds_keeps_time():
    p = detect_date_format(["2026-01-17 10:51:28.474", "2026-01-18 09:00:00.999999"])
    assert p.has_time and not p.is_time_only
    assert normalize_date_series(pd.Series(["2026-01-17 10:51:28.474"]), p).iloc[0] == "2026-01-17 10:51:28"


def test_time_only_no_date_flagged():
    p = detect_date_format(["09:28:05.794134", "10:15:00.5", "23:59:59"])
    assert p.is_time_only is True
    assert p.can_time_window is False
    # 纯时间列不归一 (避免乱补今天日期)
    out = normalize_date_series(pd.Series(["09:28:05.794134"]), p)
    assert out.iloc[0] == "09:28:05.794134"


def test_header_leak_flagged_red():
    p = detect_date_format(["2026-01-05", "2026-01-06", "reportDate", "2026-01-07", "2026-01-08"])
    assert "reportDate" in p.anomalies
    assert 0 < p.parse_rate < 1.0


def test_day_eq_month_scans_whole_column():
    # 头部 11/11 无法区分 D/M; 末尾 30/1 出现 day>12 → 必须扫整列才判 dayfirst
    p = detect_date_format(["11/11/2025", "5/6/2025", "30/1/2025"])
    assert p.dayfirst is True and p.dayfirst_confident
    assert normalize_date_series(pd.Series(["30/1/2025"]), p).iloc[0] == "2025-01-30"


def test_mdy_month_first():
    p = detect_date_format(["12/30/2025", "1/5/2025", "6/15/2025"])
    assert p.dayfirst is False and p.dayfirst_confident
    assert normalize_date_series(pd.Series(["12/30/2025"]), p).iloc[0] == "2025-12-30"


def test_slash_year_first_not_dayfirst():
    # 回归 (review finding #1): 斜杠年在前 YYYY/MM/DD 不可用 dayfirst=True, 否则月日互换
    p = detect_date_format(["2025/12/30", "2025/01/05", "2025/06/03"])
    assert p.dayfirst is False and p.dayfirst_confident
    out = normalize_date_series(pd.Series(["2025/01/05", "2025/06/03"]), p)
    assert list(out) == ["2025-01-05", "2025-06-03"]  # 不是 2025-05-01 / 2025-03-06
    # 带时间变体 (HIS 常见)
    p2 = detect_date_format(["2025/01/05 09:30:00", "2025/02/06 10:00:00"])
    assert p2.dayfirst is False
    assert normalize_date_series(pd.Series(["2025/01/05 09:30:00"]), p2).iloc[0] == "2025-01-05 09:30:00"


# ────────────── 列剖析 ──────────────

def test_profile_numeric():
    p = profile_column(["10", "20", "30", ""], name="amount")
    assert p.kind == "numeric"
    assert p.min == 10.0 and p.max == 30.0
    assert p.null_count == 1


def test_profile_key_uniqueness():
    p = profile_column(["a", "b", "c", "a"], name="pid", as_key=True)
    assert p.kind == "key"
    assert p.unique == 3
    assert p.dup_rate == pytest.approx(0.25)


def test_profile_date_kind():
    p = profile_column(["2026-01-05", "2025-12-31", "2026-01-17"], name="dt")
    assert p.kind == "date"
    assert p.date is not None and p.date.has_date


# ────────────── 连接预检 (设计 D4) ──────────────

def test_preflight_both_getters_resolve_not_set_equality():
    from javert.onboarding.etl_engine import SpokeResult
    from javert.onboarding.join_preflight import preflight_keys
    from javert.onboarding.manifest_loader import load_manifest

    m = load_manifest()
    # notes 落裸号, fees 落复合键 — 集合"形态不等"但 D4 判据应通过
    notes = pd.DataFrame({c: [""] * 2 for c in m.spoke("notes").output_schema})
    notes["住院号"] = ["211001", "211002"]
    fees = pd.DataFrame({c: [""] * 2 for c in m.spoke("fees").output_schema})
    fees["bah"] = ["42506084200-211001 ", "42506084200-211002 "]

    spokes = [
        SpokeResult("fees", "费用", "shi_fee.csv", fees, 2),
        SpokeResult("notes", "文书", "case_notes.csv", notes, 2),
    ]
    pf = preflight_keys(spokes, {"fees": m.spoke("fees"), "notes": m.spoke("notes")})
    assert pf is not None
    assert pf.coverage == 1.0
    assert pf.verdict == "green"


def test_preflight_red_when_keys_disjoint():
    from javert.onboarding.etl_engine import SpokeResult
    from javert.onboarding.join_preflight import preflight_keys
    from javert.onboarding.manifest_loader import load_manifest

    m = load_manifest()
    notes = pd.DataFrame({c: [""] * 2 for c in m.spoke("notes").output_schema})
    notes["住院号"] = ["999111", "999222"]  # 完全不交
    fees = pd.DataFrame({c: [""] * 2 for c in m.spoke("fees").output_schema})
    fees["bah"] = ["42506084200-211001 ", "42506084200-211002 "]
    spokes = [
        SpokeResult("fees", "费用", "shi_fee.csv", fees, 2),
        SpokeResult("notes", "文书", "case_notes.csv", notes, 2),
    ]
    pf = preflight_keys(spokes, {"fees": m.spoke("fees"), "notes": m.spoke("notes")})
    assert pf.verdict == "red"


# ────────────── 视图工具 (设计 D2) ──────────────

class _StubLoader:
    def __init__(self, notes):
        self._notes = notes

    def all_notes(self):
        return self._notes


class _StubLab:
    def __init__(self, rows):
        self._rows = rows

    def get_lab_results(self, patient_id, **_kw):
        return self._rows


def test_search_anesthesia_view_tag(tmp_path):
    from javert.tools import search_anesthesia
    notes = pd.DataFrame({
        "住院号": ["211001", "211001"],
        "子阶段": ["麻醉记录", "入院诊断"],
        "内容": ["全身麻醉, 诱导平稳", "甲状腺癌"],
    })
    ss = tmp_path / "shi_ss.csv"
    pd.DataFrame({
        "ba_id": ["szx-211001"], "oprn_oprt_name": ["甲状腺切除术"],
        "anst_mtd_name": ["全身麻醉"], "anst_dr_name": ["张医生"], "main_oprn_flag": ["1"],
    }).to_csv(ss, index=False)
    search_anesthesia.reset_cache()
    fn = search_anesthesia.create_executor(_StubLoader(notes), ss)
    out = fn("211001")
    # v3: 病案首页手术表有麻醉医师签名 → 权威断言麻醉真实 (不再标"暂不参与判定")
    assert "权威记录" in out
    assert "麻醉服务真实开展" in out
    assert "全身麻醉" in out


def test_search_pathology_view_tag():
    from javert.tools import search_pathology
    notes = pd.DataFrame({
        "住院号": ["211001"],
        "子阶段": ["病理诊断"],
        "内容": ["甲状腺乳头状癌, 病理确诊"],
    })
    lab = _StubLab([
        {"specimen": "病理", "rpt_itemname": "甲状腺穿刺", "result": "恶性",
         "report_dt": "2026-01-10", "diagnosisOpinion": "乳头状癌"},
    ])
    fn = search_pathology.create_executor(_StubLoader(notes), lab)
    out = fn("211001")
    assert "暂不参与判定" in out
    assert "病理" in out


def test_search_anesthesia_empty():
    from javert.tools import search_anesthesia
    notes = pd.DataFrame({"住院号": ["211001"], "子阶段": ["入院诊断"], "内容": ["甲状腺癌"]})
    ss = "/nonexistent/shi_ss.csv"
    search_anesthesia.reset_cache()
    from pathlib import Path
    fn = search_anesthesia.create_executor(_StubLoader(notes), Path(ss))
    out = fn("211001")
    assert "无麻醉记录" in out


# ────────────── 时间窗口预检 (review finding #4) ──────────────

def test_preflight_time_window_detects_out_of_window():
    from javert.onboarding.join_preflight import preflight_time_window
    window = pd.Series(["2026-01-01", "2026-01-31"])  # 住院期
    events = pd.Series(["2026-01-10", "2020-05-05", "2020-06-06"])  # 2/3 落窗口外
    tw = preflight_time_window(window, events, "住院期", "化验")
    assert tw.checked == 3 and tw.out_window == 2
    assert tw.out_rate > 0.5 and tw.verdict == "yellow"
    assert "落在" in tw.note


def test_preflight_time_window_skips_time_only():
    from javert.onboarding.join_preflight import preflight_time_window
    tw = preflight_time_window(pd.Series(["09:00:00"]), pd.Series(["10:00:00"]))
    assert tw.checked == 0 and "无日期分量" in tw.note


def test_preflight_time_windows_driver_labs_vs_fees():
    from javert.onboarding.etl_engine import SpokeResult
    from javert.onboarding.join_preflight import preflight_time_windows
    from javert.onboarding.manifest_loader import load_manifest
    m = load_manifest()
    fees = pd.DataFrame({c: [""] * 2 for c in m.spoke("fees").output_schema})
    fees["fee_ocur_time"] = ["2026-01-01", "2026-01-31"]
    labs = pd.DataFrame({c: [""] * 2 for c in m.spoke("labs").output_schema})
    labs["report_dt"] = ["2020-05-05", "2020-06-06"]  # 全落住院期外
    spokes = [SpokeResult("fees", "费用", "shi_fee.csv", fees, 2),
              SpokeResult("labs", "化验", "lab_results.csv", labs, 2)]
    meta = {"fees": m.spoke("fees"), "labs": m.spoke("labs")}
    tws = preflight_time_windows(spokes, meta)
    assert len(tws) == 1 and tws[0]["spoke"] == "labs"
    assert tws[0]["out_rate"] == 1.0 and tws[0]["verdict"] == "yellow"
