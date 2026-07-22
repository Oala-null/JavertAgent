# -*- coding: utf-8 -*-
"""harden-onsite-redlines Task 5.2: hub_source BA 分支纯逻辑单测 (stub q, 不连 142).

覆盖: per-patient 源选择 (D5) / ZYZD 前缀5码→名解析 / 主次诊去重 / sy 院区不受影响.
"""

from __future__ import annotations

import pandas as pd
import pytest

import javert.data.hub_source as hs

YQ2ORG = {"0003": "H03", "0001": "H01"}


def _df(cols: list[str], rows: list[list[str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=cols).astype(str)


def _stub_q(tables: dict[str, pd.DataFrame]):
    """按 SQL 关键字分发的 q() 替身. tables 键:
    ih / jbk / zdk / zdk_dict / ih_dict / zdk_like / ih_like / op / syssk"""

    def q(cn, sql, params=()):
        s = " ".join(sql.split())
        if "SELECT DISTINCT ZDDM, ZDMC" in s:
            key = "zdk_like" if "LIKE" in s else "zdk_dict"
            return tables.get(key, _df(["ZDDM", "ZDMC"], []))
        if "SELECT DISTINCT ZDBM, ZDSM" in s:
            key = "ih_like" if "LIKE" in s else "ih_dict"
            return tables.get(key, _df(["ZDBM", "ZDSM"], []))
        if "FROM TB_IH_DIAGNOSIS_DETAIL" in s:
            return tables.get("ih", _df(["YLJGYQDM", "JZLSH", "ZDBM", "ZDSM", "CYZDBZ"], []))
        if "FROM TB_BA_SYJBK" in s:
            return tables.get("jbk", _df(["YLJGYQDM", "SYXH", "ZYZD"], []))
        if "FROM TB_BA_SYZDK" in s:
            return tables.get("zdk", _df(["YLJGYQDM", "SYXH", "ZDXH", "ZDDM", "ZDMC"], []))
        # BA 手术查询内嵌 OPERATION_DETAIL 时间回退子查询，必须先精确分发 BA。
        if "FROM TB_BA_SYSSK s" in s:
            assert "TB_OPRATION_DETAIL" not in s
            assert (
                "SELECT s.YLJGYQDM, s.SYXH, s.SSXH, s.SSRQ, s.SSDM, s.SSMC, "
                "s.SSJB, s.MZFS, s.SSYS, s.MZYS, s.SFZYSS, o.SSKSSJ "
                "FROM TB_BA_SYSSK s"
            ) in s
            return tables.get("syssk", _df(
                ["YLJGYQDM", "SYXH", "SSXH", "SSRQ", "SSDM", "SSMC", "SSJB",
                 "MZFS", "SSYS", "MZYS", "SFZYSS", "SSKSSJ"], []))
        if "FROM TB_OPERATION_DETAIL WHERE" in s:
            assert "TB_OPRATION_DETAIL" not in s
            assert (
                "SELECT YLJGYQDM, JZLSH, SSCZMC, SSCZBM, ZCBZ, SSKSSJ, SSJB, "
                "MZFS, SXYHRYXM, MZYHRYXM FROM TB_OPERATION_DETAIL WHERE"
            ) in s
            return tables.get("op", _df(
                ["YLJGYQDM", "JZLSH", "SSCZMC", "SSCZBM", "ZCBZ", "SSKSSJ",
                 "SSJB", "MZFS", "SXYHRYXM", "MZYHRYXM"], []))
        raise AssertionError(f"stub 未覆盖的 SQL: {s[:120]}")

    return q


# =========================================================
# fetch_zd — per-patient 源选择
# =========================================================
def test_zd_per_patient_source_selection(monkeypatch):
    """A 有 SYJBK 首页行 → BA 源; B 没有 → 保留 IH 行 (不再双清零)."""
    tables = {
        "ih": _df(["YLJGYQDM", "JZLSH", "ZDBM", "ZDSM", "CYZDBZ"], [
            ["0003", "A", "I10.x00", "IH高血压(应被剔)", "1"],
            ["0003", "B", "C73.x00", "甲状腺恶性肿瘤", "1"],
            ["0003", "B", "E04.101", "甲状腺结节", "0"],
        ]),
        "jbk": _df(["YLJGYQDM", "SYXH", "ZYZD"], [["0003", "A", "C73.x01"]]),
        "zdk": _df(["YLJGYQDM", "SYXH", "ZDXH", "ZDDM", "ZDMC"], [
            ["0003", "A", "2", "E04.101", "甲状腺结节"],
        ]),
        "zdk_dict": _df(["ZDDM", "ZDMC"], [["C73.x01", "甲状腺乳头状癌"]]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_zd(None, ["A", "B"], YQ2ORG)

    a = out[out["ba_id"] == "H03-A"]
    b = out[out["ba_id"] == "H03-B"]
    # A: BA 源 (主诊锚 ZYZD + SYZDK 次诊), IH 行被剔
    assert list(a[a["maindiag_flag"] == 1]["inhosp_diag_code"]) == ["C73.x01"]
    assert list(a[a["maindiag_flag"] == 1]["inhosp_diag_name"]) == ["甲状腺乳头状癌"]
    assert "IH高血压(应被剔)" not in set(a["inhosp_diag_name"])
    assert list(a[a["maindiag_flag"] == 0]["inhosp_diag_code"]) == ["E04.101"]
    # B: 缺首页行 → IH 源保留, 主/次诊都在
    assert len(b) == 2
    assert list(b[b["maindiag_flag"] == 1]["inhosp_diag_code"]) == ["C73.x00"]


def test_zd_sy_hospital_unaffected(monkeypatch):
    """sy (0001) 患者始终走 IH, 即使批内 0003 患者命中首页."""
    tables = {
        "ih": _df(["YLJGYQDM", "JZLSH", "ZDBM", "ZDSM", "CYZDBZ"], [
            ["0001", "S1", "J18.x00", "肺炎", "1"],
            ["0003", "A", "I10.x00", "IH行(应被剔)", "1"],
        ]),
        "jbk": _df(["YLJGYQDM", "SYXH", "ZYZD"], [["0003", "A", "C73.x01"]]),
        "zdk_dict": _df(["ZDDM", "ZDMC"], [["C73.x01", "癌"]]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_zd(None, None, YQ2ORG)
    s1 = out[out["ba_id"] == "H01-S1"]
    assert list(s1["inhosp_diag_name"]) == ["肺炎"]
    assert int(s1["maindiag_flag"].iloc[0]) == 1


def test_zd_prefix5_code_resolution(monkeypatch):
    """ZYZD 精确无命中 → 前缀5 LIKE 字典解析出名字."""
    tables = {
        "jbk": _df(["YLJGYQDM", "SYXH", "ZYZD"], [["0003", "A", "D34.9x9"]]),
        # 精确字典无此码; LIKE 字典按前缀 D34.9 命中
        "zdk_like": _df(["ZDDM", "ZDMC"], [["D34.901", "甲状腺良性肿瘤"]]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_zd(None, ["A"], YQ2ORG)
    main = out[out["maindiag_flag"] == 1]
    assert list(main["inhosp_diag_name"]) == ["甲状腺良性肿瘤"]
    assert list(main["inhosp_diag_code"]) == ["D34.9x9"]


def test_zd_secondary_dedup_against_main(monkeypatch):
    """SYZDK 与主诊同码的行剔除 (主次诊去重)."""
    tables = {
        "jbk": _df(["YLJGYQDM", "SYXH", "ZYZD"], [["0003", "A", "C73.x01"]]),
        "zdk": _df(["YLJGYQDM", "SYXH", "ZDXH", "ZDDM", "ZDMC"], [
            ["0003", "A", "1", "C73.x01", "与主诊同码(应被剔)"],
            ["0003", "A", "2", "E04.101", "甲状腺结节"],
        ]),
        "zdk_dict": _df(["ZDDM", "ZDMC"], [["C73.x01", "癌"]]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_zd(None, ["A"], YQ2ORG)
    assert len(out[out["maindiag_flag"] == 1]) == 1
    sec = out[out["maindiag_flag"] == 0]
    assert list(sec["inhosp_diag_code"]) == ["E04.101"]


def test_zd_empty_zyzd_falls_back_to_ih(monkeypatch):
    """fix-scan-residuals: SYJBK 有行但 ZYZD 空串 → 回退 IH, 不生成空主诊.

    A: ZYZD 非空 → BA 源. B: SYJBK 有行但 ZYZD='' → 保留 IH, 无空主诊行.
    """
    tables = {
        "ih": _df(["YLJGYQDM", "JZLSH", "ZDBM", "ZDSM", "CYZDBZ"], [
            ["0003", "A", "I10.x00", "IH高血压(应被剔)", "1"],
            ["0003", "B", "C73.x00", "甲状腺恶性肿瘤", "1"],
        ]),
        "jbk": _df(["YLJGYQDM", "SYXH", "ZYZD"], [
            ["0003", "A", "C73.x01"],
            ["0003", "B", "   "],  # 空白 ZYZD (strip 后为空) → 不构成 BA 覆盖
        ]),
        "zdk_dict": _df(["ZDDM", "ZDMC"], [["C73.x01", "甲状腺乳头状癌"]]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_zd(None, ["A", "B"], YQ2ORG)

    b = out[out["ba_id"] == "H03-B"]
    # B 回退 IH: 主诊非空, 无 code/name 双空的伪主诊行
    assert list(b["inhosp_diag_name"]) == ["甲状腺恶性肿瘤"]
    assert list(b["inhosp_diag_code"]) == ["C73.x00"]
    assert not ((out["inhosp_diag_code"] == "") & (out["inhosp_diag_name"] == "")).any()


def test_zd_jbk_main_but_no_zdk_only_main(monkeypatch):
    """fix-scan-residuals 留档: SYJBK 有主诊但 SYZDK 零行 → 只剩一条主诊."""
    tables = {
        "jbk": _df(["YLJGYQDM", "SYXH", "ZYZD"], [["0003", "A", "C73.x01"]]),
        "zdk_dict": _df(["ZDDM", "ZDMC"], [["C73.x01", "甲状腺乳头状癌"]]),
        # zdk 缺省 → 零行
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_zd(None, ["A"], YQ2ORG)
    assert len(out) == 1
    assert int(out["maindiag_flag"].iloc[0]) == 1
    assert list(out["inhosp_diag_name"]) == ["甲状腺乳头状癌"]


def test_zd_no_ba_rows_at_all_keeps_ih(monkeypatch):
    """整批都没首页行 (len(jbk)==0) → 全部保留 IH (含 0003 患者)."""
    tables = {
        "ih": _df(["YLJGYQDM", "JZLSH", "ZDBM", "ZDSM", "CYZDBZ"], [
            ["0003", "B", "C73.x00", "甲状腺恶性肿瘤", "1"],
        ]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_zd(None, ["B"], YQ2ORG)
    assert list(out["ba_id"]) == ["H03-B"]
    assert list(out["inhosp_diag_name"]) == ["甲状腺恶性肿瘤"]


# =========================================================
# fetch_ss — per-patient 源选择
# =========================================================
def test_ss_per_patient_source_selection(monkeypatch):
    """A 有 SYSSK 行 → 首页手术源 (SFZYSS 主手术); B 没有 → 保留 OPERATION 行."""
    tables = {
        "op": _df(["YLJGYQDM", "JZLSH", "SSCZMC", "SSCZBM", "ZCBZ", "SSKSSJ",
                   "SSJB", "MZFS", "SXYHRYXM", "MZYHRYXM"], [
            ["0003", "A", "IH手术(应被剔)", "06.2x01", "1", "2026-01-02 09:00:00",
             "二级", "全麻", "张三", "李四"],
            ["0003", "B", "阑尾切除术", "47.0x01", "1", "2026-01-03 08:00:00",
             "二级", "全麻", "王五", "赵六"],
        ]),
        "syssk": _df(["YLJGYQDM", "SYXH", "SSXH", "SSRQ", "SSDM", "SSMC", "SSJB",
                      "MZFS", "SSYS", "MZYS", "SFZYSS", "SSKSSJ"], [
            ["0003", "A", "1", "20260101", "06.2x02", "甲状腺部分切除术", "3",
             "全身麻醉", "张三", "李四", "1", ""],
        ]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_ss(None, ["A", "B"], YQ2ORG)

    a = out[out["ba_id"] == "H03-A"]
    b = out[out["ba_id"] == "H03-B"]
    # A: 首页源, SFZYSS=1 → 主手术; SSRQ 8 位数字 → ISO; SSJB 码 → 中文
    assert list(a["oprn_oprt_name"]) == ["甲状腺部分切除术"]
    assert int(a["main_oprn_flag"].iloc[0]) == 1
    assert list(a["oprn_oprt_date"]) == ["2026-01-01"]
    assert list(a["oprn_lv_name"]) == ["三级"]
    # B: 缺首页手术行 → OPERATION 保留 (不再清零)
    assert list(b["oprn_oprt_name"]) == ["阑尾切除术"]
    assert list(b["oprn_oprt_date"]) == ["2026-01-03"]


def test_ss_sy_hospital_unaffected(monkeypatch):
    tables = {
        "op": _df(["YLJGYQDM", "JZLSH", "SSCZMC", "SSCZBM", "ZCBZ", "SSKSSJ",
                   "SSJB", "MZFS", "SXYHRYXM", "MZYHRYXM"], [
            ["0001", "S1", "胆囊切除术", "51.2x01", "1", "2026-01-05 10:00:00",
             "三级", "全麻", "张三", "李四"],
        ]),
        "syssk": _df(["YLJGYQDM", "SYXH", "SSXH", "SSRQ", "SSDM", "SSMC", "SSJB",
                      "MZFS", "SSYS", "MZYS", "SFZYSS", "SSKSSJ"], [
            ["0003", "A", "1", "20260101", "06.2x02", "甲状腺切除", "3",
             "全麻", "x", "y", "1", ""],
        ]),
    }
    monkeypatch.setattr(hs, "q", _stub_q(tables))
    out = hs.fetch_ss(None, None, YQ2ORG)
    s1 = out[out["ba_id"] == "H01-S1"]
    assert list(s1["oprn_oprt_name"]) == ["胆囊切除术"]
