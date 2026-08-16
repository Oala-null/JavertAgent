# -*- coding: utf-8 -*-
"""support-desus-hub-source: 表族前缀的安全性与共享 SQL 覆盖。"""

from __future__ import annotations

import pandas as pd
import pytest
from types import SimpleNamespace

import javert.data.hub_source as hs
import javert.web.api.routes_audit as routes_audit


def _empty(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype="string") for column in columns})


def _capturing_q(sqls: list[str]):
    def q(cn, sql, params=()):
        compact = " ".join(sql.split())
        sqls.append(compact)
        if "sys.tables" in compact:
            assert params == ("desus_TB_HIS_ZY_FEE_DETAIL_EXT",)
            return _empty(["x"])
        if "DIC_HOSPITAL" in compact:
            return _empty(["YLJGYQDM", "YYJC"])
        if "FEE_DETAIL_FS" in compact:
            return _empty([
                "YLJGYQDM", "SFMXID", "STFBZ", "JZLSH", "MXFYLB", "FYFSSJ",
                "MXXMBM", "MXXMBMYB", "MXXMMC", "MXXMDJ", "MXXMSL", "MXXMJE",
            ])
        if "MEDICAL_DOCUMENT" in compact:
            return _empty(["JZLSH", "JLSJ", "WSMC", "WSLB", "DLBT", "ZW"])
        if "LEAVEHOSPITAL_SUMMARY" in compact:
            return _empty([
                "JZLSH", "CYSJ", "YYZTBBT1", "YYZTB1", "YYZTBBT2", "YYZTB2",
                *(column for column, _ in hs.SUMMARY_COL2SEC),
            ])
        if "IH_DIAGNOSIS_DETAIL" in compact:
            return _empty(["YLJGYQDM", "JZLSH", "ZDBM", "ZDSM", "CYZDBZ"])
        if "BA_SYJBK" in compact:
            return _empty(["YLJGYQDM", "SYXH", "ZYZD"])
        if "BA_SYZDK" in compact:
            return _empty(["YLJGYQDM", "SYXH", "ZDXH", "ZDDM", "ZDMC"])
        if "BA_SYSSK" in compact:
            return _empty([
                "YLJGYQDM", "SYXH", "SSXH", "SSRQ", "SSDM", "SSMC", "SSJB",
                "MZFS", "SSYS", "MZYS", "SFZYSS", "SSKSSJ",
            ])
        if "OPERATION_DETAIL" in compact:
            return _empty([
                "YLJGYQDM", "JZLSH", "SSCZMC", "SSCZBM", "ZCBZ", "SSKSSJ",
                "SSJB", "MZFS", "SXYHRYXM", "MZYHRYXM",
            ])
        if "LIS_INDICATORS" in compact:
            return _empty([
                "JZLSH", "JYZBMC", "JYZBDM", "JYZBJG", "JLDW", "CKZ", "YCTS",
                "SQKS", "BGSJ", "BBMC", "BGDLB", "BRNL", "BRXB", "BGYHRYXM",
                "SHYHRYXM",
            ])
        if "RIS_REPORT" in compact:
            return _empty([
                "JZLSH", "EXAMTYPE", "JCMC", "concl", "descr", "JCBW", "JCKS",
                "JCSJ", "BGSJ", "diag", "pos", "BRXB", "BGYHRYXM", "SHYHRYXM",
            ])
        raise AssertionError(f"未覆盖 SQL: {compact}")

    return q


def test_table_name_default_prefix_and_validation():
    assert hs.table_name("TB_DIC_HOSPITAL") == "TB_DIC_HOSPITAL"
    assert hs.table_name("TB_DIC_HOSPITAL", "desus_") == "desus_TB_DIC_HOSPITAL"
    with pytest.raises(ValueError):
        hs.table_name("TB_DIC_HOSPITAL", "dbo.")
    with pytest.raises(ValueError):
        hs.table_name("sys.tables", "desus_")


def test_all_shared_fetchers_use_desus_table_family(monkeypatch):
    sqls: list[str] = []
    monkeypatch.setattr(hs, "q", _capturing_q(sqls))
    prefix = "desus_"
    hs.fetch_hospital_map(None, table_prefix=prefix)
    mapping = {"0001": "H01", "0003": "H03"}
    hs.fetch_fees(None, ["P"], mapping, table_prefix=prefix)
    hs.fetch_notes(None, ["P"], table_prefix=prefix)
    hs.fetch_zd(None, ["P"], mapping, table_prefix=prefix)
    hs.fetch_ss(None, ["P"], mapping, table_prefix=prefix)
    hs.fetch_labs(None, ["P"], table_prefix=prefix)
    hs.fetch_exams(None, ["P"], table_prefix=prefix)

    rendered = "\n".join(sqls)
    for base in (
        "TB_DIC_HOSPITAL", "TB_HIS_ZY_FEE_DETAIL_FS", "TB_CIS_MEDICAL_DOCUMENT",
        "TB_CIS_LEAVEHOSPITAL_SUMMARY", "TB_IH_DIAGNOSIS_DETAIL", "TB_BA_SYJBK",
        "TB_BA_SYZDK", "TB_OPERATION_DETAIL", "TB_BA_SYSSK", "TB_LIS_INDICATORS",
        "TB_LIS_REPORT", "TB_RIS_REPORT", "TB_RIS_REPORT2",
    ):
        assert f"desus_{base}" in rendered
    assert " FROM TB_" not in rendered
    assert " JOIN TB_" not in rendered


def test_2c_probe_falls_back_to_configured_profile(monkeypatch):
    seen: list[str] = []

    class _Connection:
        def close(self):
            pass

    class _Cfg:
        hub_database = "TP_data_hub"
        hub_table_prefix = ""
        hub_raw_profiles = {
            "ocr1.0": SimpleNamespace(database="TP_data_hub", table_prefix="desus_")
        }

        def model_copy(self, *, update):
            clone = _Cfg()
            clone.hub_database = update["hub_database"]
            clone.hub_table_prefix = update["hub_table_prefix"]
            clone.hub_raw_profiles = {}
            return clone

    monkeypatch.setattr(routes_audit, "get_config", _Cfg)
    monkeypatch.setattr(hs, "connect", lambda config, timeout=10: _Connection())
    monkeypatch.setattr(hs, "fetch_hospital_map", lambda cn, **kw: {})

    def _fees(cn, pids, mapping, **kw):
        seen.append(kw["table_prefix"])
        return pd.DataFrame([{"JZLSH": pids[0]}]) if kw["table_prefix"] == "desus_" else pd.DataFrame()

    monkeypatch.setattr(hs, "fetch_fees", _fees)

    assert routes_audit._hub_probe("P") is True
    assert seen == ["", "desus_"]


def test_2c_hub_snapshot_propagates_desus_prefix(monkeypatch, tmp_path):
    seen: list[tuple[str, str]] = []

    class _Connection:
        def close(self):
            pass

    cfg = type("Cfg", (), {"hub_table_prefix": "desus_"})()
    monkeypatch.setattr(routes_audit, "get_config", lambda: cfg)
    monkeypatch.setattr(hs, "connect", lambda config: _Connection())
    monkeypatch.setattr(
        hs,
        "fetch_hospital_map",
        lambda cn, **kw: (seen.append(("hospital", kw["table_prefix"])), {})[1],
    )
    for name in ("fees", "zd", "ss"):
        monkeypatch.setattr(
            hs,
            f"fetch_{name}",
            lambda cn, pids, mapping, _name=name, **kw: (
                seen.append((_name, kw["table_prefix"])), pd.DataFrame()
            )[1],
        )
    for name in ("notes", "labs", "exams"):
        monkeypatch.setattr(
            hs,
            f"fetch_{name}",
            lambda cn, pids, _name=name, **kw: (
                seen.append((_name, kw["table_prefix"])), pd.DataFrame()
            )[1],
        )

    routes_audit._hub_fetch_patient("P", tmp_path)

    assert {name for name, _ in seen} == {
        "hospital", "fees", "notes", "zd", "ss", "labs", "exams"
    }
    assert {prefix for _, prefix in seen} == {"desus_"}
