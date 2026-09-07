"""执行真实取数SQL的离线关联测试；仅将TOP/dbo/sys.tables适配为SQLite。

不替代现场SQL Server/ODBC验收；重点验证多次住院和不同院区的数据不混合。
"""
import re
import sqlite3

from javert.data import hub_source as hs


SCHEMAS = {
    'TB_BA_SYJBK': 'YLJGYQDM SYXH BAH KH KLX RYRQ CYRQ ZYZD',
    'TB_CIS_LEAVEHOSPITAL_SUMMARY': 'YLJGYQDM JZLSH BAH KH KLX RYSJ CYSJ YYZTBBT1 YYZTB1 YYZTBBT2 YYZTB2 ' + ' '.join(c for c, _ in hs.SUMMARY_COL2SEC),
    'TB_HIS_ZY_ADM_REG': 'YLJGYQDM JZLSH KH KLX RYSJ',
    'TB_HIS_ZY_FEE_DETAIL_FS': 'YLJGYQDM JZLSH KH KLX SFMXID STFBZ MXFYLB FYFSSJ MXXMBM MXXMBMYB MXXMMC MXXMDJ MXXMSL MXXMJE',
    'TB_CIS_MEDICAL_DOCUMENT': 'YLJGYQDM JZLSH BAH JLSJ WSMC WSLB DLBT ZW WSLSH',
    'TB_IH_DIAGNOSIS_DETAIL': 'YLJGYQDM JZLSH ZDBM ZDSM CYZDBZ ZYZDLSH',
    'TB_BA_SYZDK': 'YLJGYQDM SYXH ZDXH ZDDM ZDMC',
    'TB_OPERATION_DETAIL': 'YLJGYQDM JZLSH SSCZMC SSCZBM ZCBZ SSKSSJ SSJB MZFS SXYHRYXM MZYHRYXM SSMXLSH SSXH',
    'TB_BA_SYSSK': 'YLJGYQDM SYXH SSXH SSRQ SSDM SSMC SSJB MZFS SSYS MZYS SFZYSS',
    'TB_LIS_INDICATORS': 'YLJGYQDM BGDH BGRQ JYZBMC JYZBDM JYZBJG JLDW CKZ YCTS',
    'TB_LIS_REPORT': 'YLJGYQDM JZLSH BGDH BGRQ SQKS BGSJ BBMC BGDLB BRNL BRXB BGYHRYXM SHYHRYXM',
    'TB_RIS_REPORT': 'YLJGYQDM JZLSH EXAMTYPE JCMC YXZD YXBX JCBW JCKS JCSJ BGSJ BGLCZD YYS BRXB BGYHRYXM SHYHRYXM',
    'TB_RIS_REPORT2': 'YLJGYQDM JZLSH EXAMTYPE JCMC JCBGJG BT1NR JCBW JCKS JCSJ BGSJ BT2NR JCJGDM BRXB BGYHRYXM SHYHRYXM',
}


class Cursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()

    def execute(self, sql, params=()):
        sql = sql.replace('dbo.', '')
        sql = sql.replace('sys.tables', 'sqlite_master')
        limit = re.search(r'SELECT\s+TOP\s*\((\d+)\)', sql, flags=re.I)
        if limit:
            sql = re.sub(r'SELECT\s+TOP\s*\(\d+\)', 'SELECT', sql, flags=re.I)
            sql += ' LIMIT ' + limit.group(1)
        self.cursor.execute(sql, params)
        return self

    @property
    def description(self):
        return self.cursor.description

    def fetchall(self):
        return self.cursor.fetchall()


class Hospital:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        for table, columns in SCHEMAS.items():
            self.db.execute(f'CREATE TABLE {table} (' + ','.join(c + ' TEXT' for c in columns.split()) + ')')

    def cursor(self):
        return Cursor(self.db)

    def row(self, table, **values):
        columns = SCHEMAS[table].split()
        self.db.execute(f'INSERT INTO {table} VALUES (' + ','.join('?' for _ in columns) + ')',
                        [values.get(c, '') for c in columns])

    def admission(self, hospital, suffix, marker):
        common = dict(YLJGYQDM=hospital, KH='CARD_SHARED', KLX='0')
        syxh, bah, visit = 'CASE_' + suffix, 'CHART_' + suffix, 'VISIT_' + suffix
        self.row('TB_BA_SYJBK', **common, SYXH=syxh, BAH=bah, ZYZD='DX_SHARED',
                 RYRQ='2026080308:07:53', CYRQ='2026080807:33:10')
        self.row('TB_CIS_LEAVEHOSPITAL_SUMMARY', **common, JZLSH=visit, BAH=bah,
                 RYSJ='2026-08-03 08:07:49', CYSJ='2026-08-08 07:33:10', RYZD=marker)
        self.row('TB_HIS_ZY_ADM_REG', **common, JZLSH=visit, RYSJ='2026-08-03 08:07:49')
        self.row('TB_HIS_ZY_FEE_DETAIL_FS', **common, JZLSH=visit, SFMXID='FEE_' + suffix,
                 STFBZ='1', MXFYLB='09', FYFSSJ='2026-08-08 07:33:17', MXXMMC=marker,
                 MXXMDJ='2', MXXMSL='3', MXXMJE='6')
        self.row('TB_CIS_MEDICAL_DOCUMENT', YLJGYQDM=hospital, JZLSH=bah, BAH=bah,
                 JLSJ='1900-01-01 00:00:00', WSMC='病程', WSLB='03', DLBT='病程', ZW=marker)
        self.row('TB_BA_SYZDK', YLJGYQDM=hospital, SYXH=syxh, ZDXH='1',
                 ZDDM='DX_SHARED', ZDMC=marker)
        self.row('TB_BA_SYSSK', YLJGYQDM=hospital, SYXH=syxh, SSXH='1',
                 SSRQ='', SSDM='OP_' + suffix, SSMC=marker, SFZYSS='1')
        self.row('TB_OPERATION_DETAIL', YLJGYQDM=hospital, JZLSH=visit, SSXH='1',
                 SSKSSJ='2026-08-03 09:00:00', SSCZMC=marker, SSCZBM='OP_' + suffix, ZCBZ='1')
        self.row('TB_LIS_REPORT', YLJGYQDM=hospital, JZLSH=visit, BGDH='REPORT_' + suffix,
                 BGRQ='2026-08-04', BGSJ='2026-08-04 09:00:00')
        self.row('TB_LIS_INDICATORS', YLJGYQDM=hospital, BGDH='REPORT_' + suffix,
                 BGRQ='2026-08-04', JYZBMC=marker, JYZBDM='LAB_TEST', JYZBJG='123', YCTS='1')
        self.row('TB_RIS_REPORT', YLJGYQDM=hospital, JZLSH=visit, JCMC=marker, YXZD=marker)
        self.row('TB_RIS_REPORT2', YLJGYQDM=hospital, JZLSH=visit, JCMC=marker, JCBGJG=marker)


def test_real_queries_keep_other_hospital_and_admissions_out():
    hospital = Hospital()
    try:
        # 故意先插入相同诊断码的另一院区：全局名称查找会暴露串院问题。
        hospital.admission('OTHERHOSP', 'A', 'FOREIGN_HOSPITAL_MARKER')
        hospital.admission('TESTHOSP', 'A', 'LOCAL_DIAGNOSIS_MARKER')
        hospital.admission('TESTHOSP', 'B', 'LOCAL_DIAGNOSIS_MARKER')
        for suffix in ('A', 'B'):
            result = hs.fetch_hospital_bundle(hospital, 'CASE_' + suffix, 'TESTHOSP')
            assert len(result['fees']) == 1
            assert result['fees'].iloc[0]['bah'] == 'TESTHOSP-CASE_' + suffix
            assert set(result['notes']['住院号']) == {'CASE_' + suffix}
            assert result['zd'].iloc[0]['inhosp_diag_name'] == 'LOCAL_DIAGNOSIS_MARKER'
            assert result['ss'].iloc[0]['oprn_oprt_date'] == '2026-08-03'
            assert len(result['labs']) == 1 and len(result['exams']) == 2
            assert set(result['labs']['zyh']) == {'CASE_' + suffix}
            assert set(result['exams']['zyh']) == {'CASE_' + suffix}
            for key in ('fees', 'notes', 'zd', 'ss', 'labs', 'exams'):
                assert 'FOREIGN_HOSPITAL_MARKER' not in result[key].to_csv(index=False)
    finally:
        hospital.db.close()
