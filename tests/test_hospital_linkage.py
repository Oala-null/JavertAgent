"""人工去标识样例：同卡多次住院，三种编号必须分开。无真实库连接。"""
import pandas as pd
import pytest

from javert.data import hub_source as hs


def frames():
    return {
        "home": pd.DataFrame([dict(SYXH="CASE_A", BAH="CHART_A", KH="CARD_TEST",
                                  KLX="0", RYRQ="2026080308:07:53", CYRQ="2026080807:33:10")]),
        "summary": pd.DataFrame([dict(JZLSH="VISIT_A", BAH="CHART_A", KH="CARD_TEST",
                                     KLX="0", RYSJ="2026-08-03 08:07:49", CYSJ="2026-08-08 07:33:10")]),
        "home_bah": pd.DataFrame([dict(SYXH="CASE_A")]),
        "summary_visit": pd.DataFrame([dict(BAH="CHART_A")]),
        "admission": pd.DataFrame([dict(KH="CARD_TEST", KLX="0", RYSJ="2026-08-03 08:07:49")]),
    }


def resolver_stub(monkeypatch, tables):
    def query(cn, sql, params=()):
        assert params[0] == "TESTHOSP"
        # 源查询只许参数绑定，患者号和卡号不得拼进SQL文本。
        assert not any(x in sql for x in ("CASE_A", "CHART_A", "CARD_TEST", "VISIT_A"))
        if "TB_BA_SYJBK" in sql:
            name = "home" if "SYXH=?" in sql else "home_bah"
        elif "TB_HIS_ZY_ADM_REG" in sql:
            name = "admission"
        else:
            name = "summary_visit" if "JZLSH=?" in sql else "summary"
        return tables[name].copy()
    monkeypatch.setattr(hs, "q", query)


def test_resolve_distinct_identifiers(monkeypatch):
    resolver_stub(monkeypatch, frames())
    patient = hs.resolve_patient(None, "CASE_A", "TESTHOSP")
    assert (patient.syxh, patient.bah, patient.visit) == ("CASE_A", "CHART_A", "VISIT_A")
    assert "CARD_TEST" not in repr(patient)


@pytest.mark.parametrize("table", ["home", "summary", "home_bah", "summary_visit", "admission"])
@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_duplicate_fails_closed(monkeypatch, table, count):
    tables = frames()
    tables[table] = pd.concat([tables[table]] * count) if count else tables[table].iloc[:0]
    resolver_stub(monkeypatch, tables)
    with pytest.raises(hs.HospitalLinkageError):
        hs.resolve_patient(None, "CASE_A", "TESTHOSP")


@pytest.mark.parametrize("table,col,value", [
    ("summary", "KH", "OTHER_CARD"), ("summary", "KLX", "1"),
    ("summary", "RYSJ", "1900-01-01 00:00:00"),
    ("summary", "RYSJ", "2026-08-18 08:07:49"),
    ("home", "BAH", "-"), ("admission", "KH", "OTHER_CARD"),
    ("admission", "RYSJ", "2026-08-02 08:07:49"),
])
def test_identity_and_time_conflicts(monkeypatch, table, col, value):
    tables = frames()
    tables[table].loc[0, col] = value
    resolver_stub(monkeypatch, tables)
    with pytest.raises(hs.HospitalLinkageError) as error:
        hs.resolve_patient(None, "CASE_A", "TESTHOSP")
    assert "CARD_TEST" not in str(error.value)


@pytest.mark.parametrize("hospital", ["", "0001", "0003"])
def test_real_mode_refuses_test_hospital(hospital):
    with pytest.raises(hs.HospitalLinkageError):
        hs.resolve_patient(None, "CASE_A", hospital)


def test_notes_use_chart_key_and_keep_unknown_time(monkeypatch):
    patient = hs.HospitalPatient("TESTHOSP", "CASE_A", "CHART_A", "VISIT_A", "CARD_TEST", "0")
    def query(cn, sql, params=()):
        if "TB_CIS_MEDICAL_DOCUMENT" in sql:
            assert "BAH=?" in sql and params == ("TESTHOSP", "CHART_A")
            return pd.DataFrame([
                ["CHART_A", "1900-01-01 00:00:00", "病程", "03", "病程", "人工测试正文"],
                ["CHART_A", "1900-01-01 00:00:00", "出院小结", "05", "", "-"],
            ], columns=["JZLSH", "JLSJ", "WSMC", "WSLB", "DLBT", "ZW"])
        assert params == ("TESTHOSP", "VISIT_A")
        row = {c: "" for c, _ in hs.SUMMARY_COL2SEC}
        row.update(JZLSH="VISIT_A", CYSJ="2026-08-08 07:33:10", RYZD="人工诊断",
                   YYZTBBT1="", YYZTB1="", YYZTBBT2="", YYZTB2="")
        return pd.DataFrame([row])
    monkeypatch.setattr(hs, "q", query)
    result = hs.fetch_notes(None, ["CASE_A"], patient=patient)
    assert set(result["住院号"]) == {"CASE_A"}
    assert len(result) == 2
    assert result.loc[result["阶段"] == "病程", "事件时间"].iloc[0] == ""
    assert "出院小结" in set(result["阶段"])


@pytest.mark.parametrize("duplicate", [False, True])
def test_fees_use_visit_key_standard_category_and_detect_join_expansion(monkeypatch, duplicate):
    p = hs.HospitalPatient("TESTHOSP", "CASE_A", "CHART_A", "VISIT_A", "CARD_TEST", "0")
    def query(cn, sql, params=()):
        if "sys.tables" in sql:
            return pd.DataFrame(columns=["x"])
        assert "f.YLJGYQDM=? AND f.JZLSH=?" in sql
        assert params == ("TESTHOSP", "VISIT_A")
        row = dict(YLJGYQDM="TESTHOSP", SFMXID="FEE_A", STFBZ="1", JZLSH="VISIT_A",
                   MXFYLB="09", FYFSSJ="2026-08-08 07:33:17", MXXMBM="LOCAL_TEST",
                   MXXMBMYB="NATIONAL_TEST", MXXMMC="人工药品", MXXMDJ="2",
                   MXXMSL="3", MXXMJE="6", KH="CARD_TEST", KLX="0")
        return pd.DataFrame([row] * (2 if duplicate else 1))
    monkeypatch.setattr(hs, "q", query)
    if duplicate:
        with pytest.raises(hs.HospitalLinkageError, match="FEE_DUPLICATED"):
            hs.fetch_fees(None, ["CASE_A"], {}, patient=p)
    else:
        result = hs.fetch_fees(None, ["CASE_A"], {"TESTHOSP": "错误机构简称"}, patient=p)
        assert result.iloc[0]["bah"] == "TESTHOSP-CASE_A"
        assert result.iloc[0]["hospital"] == "TESTHOSP"
        assert result.iloc[0]["medins_chrgitm_type"] == "西药"
        assert result.iloc[0]["det_item_fee_sumamt"] == 6


def test_bundle_stops_before_audit_when_fees_missing(monkeypatch):
    p = hs.HospitalPatient("TESTHOSP", "CASE_A", "CHART_A", "VISIT_A", "CARD_TEST", "0")
    monkeypatch.setattr(hs, "resolve_patient", lambda *a: p)
    monkeypatch.setattr(hs, "fetch_fees", lambda *a, **kw: pd.DataFrame())
    monkeypatch.setattr(hs, "fetch_notes", lambda *a, **kw: pytest.fail("费用空时不能继续"))
    with pytest.raises(hs.HospitalLinkageError, match="SOURCE_FEES_EMPTY"):
        hs.fetch_hospital_bundle(None, "CASE_A", "TESTHOSP")


def test_operations_use_real_table_and_distinct_visit_join(monkeypatch):
    p = hs.HospitalPatient("TESTHOSP", "CASE_A", "CHART_A", "VISIT_A", "CARD_TEST", "0")
    def query(cn, sql, params=()):
        assert "TB_OPRATION_DETAIL" not in sql
        if "FROM TB_BA_SYSSK" in sql:
            assert "o.JZLSH=?" in sql and "s.SYXH=o.JZLSH" not in sql
            assert params == ("VISIT_A", "TESTHOSP", "CASE_A")
            return pd.DataFrame([dict(YLJGYQDM="TESTHOSP", SYXH="CASE_A", SSXH="1",
                SSRQ="2026080309:00:00", SSDM="OP_TEST", SSMC="人工操作", SSJB="1",
                MZFS="", SSYS="", MZYS="", SFZYSS="1", SSKSSJ="")])
        assert "TB_OPERATION_DETAIL" in sql and params == ("TESTHOSP", "VISIT_A")
        return pd.DataFrame(columns=["YLJGYQDM", "JZLSH", "SSCZMC", "SSCZBM", "ZCBZ",
                                     "SSKSSJ", "SSJB", "MZFS", "SXYHRYXM", "MZYHRYXM"])
    monkeypatch.setattr(hs, "q", query)
    result = hs.fetch_ss(None, ["CASE_A"], {}, patient=p)
    assert result.iloc[0]["ba_id"] == "TESTHOSP-CASE_A"
    assert result.iloc[0]["oprn_oprt_date"] == "2026-08-03"


def test_shared_bundle_uses_same_context_for_every_source(monkeypatch):
    p = hs.HospitalPatient("TESTHOSP", "CASE_A", "CHART_A", "VISIT_A", "CARD_TEST", "0")
    monkeypatch.setattr(hs, "resolve_patient", lambda *a: p)
    called = []
    for name in ("fees", "notes", "zd", "ss", "labs", "exams"):
        def fetch(*args, patient, name=name):
            assert patient is p and args[1] == ["CASE_A"]
            called.append(name)
            return pd.DataFrame([{"事件时间": ""}])
        monkeypatch.setattr(hs, "fetch_" + name, fetch)
    bundle = hs.fetch_hospital_bundle(None, "CASE_A", "TESTHOSP")
    assert called == ["fees", "notes", "zd", "ss", "labs", "exams"]
    assert bundle["warnings"]["NOTE_TIME_MISSING"] == 1


def test_workbench_real_mode_never_uses_legacy_csv(monkeypatch):
    from javert.config import JavertConfig
    import javert.web.api.routes_workbench as routes
    cfg = JavertConfig(_env_file=None, hub_linkage_mode="shanghai", hub_hospital_code="TESTHOSP")
    sentinel = object()
    monkeypatch.setattr(routes, "get_config", lambda: cfg)
    monkeypatch.setattr(routes, "_get_hub_source", lambda: sentinel)
    monkeypatch.setattr(routes, "CsvLoader", lambda *a, **kw: pytest.fail("不得读取测试CSV"))
    assert routes._get_loader() is sentinel


def test_sql_schema_check_in_real_mode_never_executes_ddl(monkeypatch):
    from javert.config import JavertConfig
    from javert.store.sqlserver_store import SqlServerStore
    statements = []
    class Connection:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql):
            statements.append(str(sql))
    class Engine:
        def connect(self):
            return Connection()
    store = SqlServerStore(JavertConfig(_env_file=None, hub_linkage_mode="shanghai"))
    monkeypatch.setattr(store, "get_engine", lambda: Engine())
    assert store.init_schema()
    assert len(statements) == 4 and all(s.startswith("SELECT TOP (0)") for s in statements)


def test_etl_snapshot_can_be_loaded_by_audit_loader(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    from javert.data.csv_loader import CsvLoader
    spec = importlib.util.spec_from_file_location("hospital_etl_cli",
           Path(__file__).parents[1] / "scripts/etl_from_data_hub.py")
    etl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(etl)
    class Conn:
        timeout = 0
        def close(self):
            pass
    monkeypatch.setattr(hs, "connect", lambda *a, **kw: Conn())
    bundle = {name: pd.DataFrame([{"zyh": "CASE_A"}]) for name in ("zd", "ss", "labs", "exams")}
    bundle["fees"] = pd.DataFrame([{"bah": "TESTHOSP-CASE_A", "cnt": 1}])
    bundle["notes"] = pd.DataFrame([{"住院号": "CASE_A", "内容": "人工测试正文", "事件时间": ""}])
    bundle["warnings"] = {"NOTE_TIME_MISSING": 1}
    monkeypatch.setattr(hs, "fetch_hospital_bundle", lambda *a: bundle)
    out = tmp_path / "snapshot"
    etl.hospital_etl(SimpleNamespace(patients="CASE_A", all=False, check_only=False, output=str(out)),
                     SimpleNamespace(hub_hospital_code="TESTHOSP"))
    loader = CsvLoader(out / "case_notes.csv", out / "shi_fee.csv")
    assert len(loader.get_fees("CASE_A")) == 1
    assert len(loader.get_notes("CASE_A")) == 1
    assert loader.get_fees("CASE_OTHER_ADMISSION").empty
    assert (out.stat().st_mode & 0o777) == 0o700
    assert all((f.stat().st_mode & 0o777) == 0o600 for f in out.iterdir())
    with pytest.raises(hs.HospitalLinkageError, match="OUTPUT_ALREADY_EXISTS"):
        etl.hospital_etl(SimpleNamespace(patients="CASE_A", all=False, check_only=False, output=str(out)),
                         SimpleNamespace(hub_hospital_code="TESTHOSP"))
