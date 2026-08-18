-- =============================================================================
-- Javert 源数据兜底接入表 (aidb 6 表)
-- 目标库: 192.168.31.142:1433, db=aidb (已存在, 本脚本只建表, 不建库)
-- 用途: 现场对接兜底 —— 当 /onboarding 拖 CSV 不顺时, 贵院工程师把数据
--       **按 docs/schema 的 6 张 Excel 模板 1:1 填进这 6 张表**, Javert 用
--       scripts/etl_from_sql.py 从 aidb 取数 → 跑审计 → 工作台实时显示.
--
-- ★ 三条铁律 (同 docs/schema/00_数据接入说明.md) ★
--   ① 「住院号」在 6 张表用同一个值 (关联同一患者的唯一钥匙).
--   ② 日期用 ISO 格式 2026-01-05 (或 2026-01-05 09:30:00), 避免 05/01/2026 日/月歧义.
--   ③ 列名保持本表原样 (= docs/schema Excel 模板表头), 别改.
--
-- 列名 = docs/schema 友好中文表头 (build_intake_templates.py 的 META, 唯一真相源);
--   tests/test_etl_from_sql.py 断言本表列清单与 META 一致, 防 DDL / 模板 / manifest 漂移.
-- 全列 NVARCHAR (中文 + "原样填"鲁棒; ETL 读 dtype=str 自处理类型, 退费金额写负数即可).
-- 幂等: sys.tables 检查保护, 可重复执行不报错.
-- 执行: SSMS 连 aidb 库直接跑本脚本即可.
-- =============================================================================

USE [aidb];
GO

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- ============================================================
-- intake_fees: 费用明细 (必给) — 一行一条收费明细, 退费写负数
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'intake_fees')
BEGIN
    CREATE TABLE intake_fees (
        id            BIGINT IDENTITY(1,1) PRIMARY KEY,
        [住院号]      NVARCHAR(64)   NULL,   -- ★必须与其它所有表用同一个值★
        [收费项目名称] NVARCHAR(512)  NULL,
        [金额]        NVARCHAR(64)   NULL,   -- 元; 退费写负数
        [收费日期]    NVARCHAR(64)   NULL,   -- 推荐 ISO 2026-01-05
        [费用类别]    NVARCHAR(64)   NULL,   -- 西药/中成药/检查/化验/治疗/手术/护理/材料/其他
        [数量]        NVARCHAR(64)   NULL,
        [计价单位]    NVARCHAR(64)   NULL,
        [医嘱编号]    NVARCHAR(128)  NULL,
        [单价]        NVARCHAR(64)   NULL,
        [规格]        NVARCHAR(255)  NULL,
        [项目编码]    NVARCHAR(128)  NULL,
        [开单科室]    NVARCHAR(128)  NULL,
        [开单医生]    NVARCHAR(128)  NULL,
        [计费科室]    NVARCHAR(128)  NULL,
        [计费医生]    NVARCHAR(128)  NULL,
        [药品通用名]  NVARCHAR(255)  NULL
    );
    PRINT 'Created table intake_fees';
END
ELSE
    PRINT 'Table intake_fees already exists, skip CREATE';
GO

IF COL_LENGTH('dbo.intake_fees', '计价单位') IS NULL
    ALTER TABLE dbo.intake_fees ADD [计价单位] NVARCHAR(64) NULL;
IF COL_LENGTH('dbo.intake_fees', '医嘱编号') IS NULL
    ALTER TABLE dbo.intake_fees ADD [医嘱编号] NVARCHAR(128) NULL;
GO

-- ============================================================
-- intake_notes: 病历文书 (必给) — 一行一段文书, 或整份一行 (内容含【】标记系统自动拆段)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'intake_notes')
BEGIN
    CREATE TABLE intake_notes (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        [住院号]    NVARCHAR(64)   NULL,   -- ★与其它表保持一致★
        [文书名称]  NVARCHAR(255)  NULL,   -- 主诉/现病史/入院诊断/手术记录/病程记录 ...
        [内容]      NVARCHAR(MAX)  NULL,   -- 文书正文 (可整份放一行, 用【主诉】【现病史】拆段)
        [文书标题]  NVARCHAR(255)  NULL,
        [文书时间]  NVARCHAR(64)   NULL    -- 推荐 ISO
    );
    PRINT 'Created table intake_notes';
END
ELSE
    PRINT 'Table intake_notes already exists, skip CREATE';
GO

-- ============================================================
-- intake_diagnoses: 病案首页-诊断 (建议给) — 主诊断那行「主诊断标志」=1, 其余=0
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'intake_diagnoses')
BEGIN
    CREATE TABLE intake_diagnoses (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        [住院号]    NVARCHAR(64)   NULL,   -- ★与其它表保持一致★
        [主诊断标志] NVARCHAR(8)    NULL,   -- 1=主诊断, 0=其它
        [诊断名称]  NVARCHAR(512)  NULL,   -- ICD-10 中文名称
        [诊断编码]  NVARCHAR(64)   NULL    -- ICD-10 编码
    );
    PRINT 'Created table intake_diagnoses';
END
ELSE
    PRINT 'Table intake_diagnoses already exists, skip CREATE';
GO

-- ============================================================
-- intake_surgeries: 病案首页-手术 (建议给) — 主手术那行「主手术标志」=1, 其余=0
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'intake_surgeries')
BEGIN
    CREATE TABLE intake_surgeries (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        [住院号]    NVARCHAR(64)   NULL,   -- ★与其它表保持一致★
        [手术名称]  NVARCHAR(512)  NULL,
        [主手术标志] NVARCHAR(8)    NULL,   -- 1=主手术, 0=其它
        [手术编码]  NVARCHAR(64)   NULL,   -- ICD-9-CM3, 可留空
        [手术日期]  NVARCHAR(64)   NULL,   -- 推荐 ISO
        [手术级别]  NVARCHAR(32)   NULL,   -- 一/二/三/四级
        [麻醉方式]  NVARCHAR(128)  NULL,
        [手术医师]  NVARCHAR(128)  NULL,
        [麻醉医师]  NVARCHAR(128)  NULL
    );
    PRINT 'Created table intake_surgeries';
END
ELSE
    PRINT 'Table intake_surgeries already exists, skip CREATE';
GO

-- ============================================================
-- intake_labs: 化验/检验 (可选) — 一行一个结果项 (一次抽血多指标 → 多行)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'intake_labs')
BEGIN
    CREATE TABLE intake_labs (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        [住院号]    NVARCHAR(64)   NULL,   -- ★与其它表保持一致★
        [检验项目]  NVARCHAR(255)  NULL,
        [检验项代码] NVARCHAR(64)   NULL,   -- 英文缩写如 FT4/TSH/AFP, 可留空
        [检验结果]  NVARCHAR(255)  NULL,
        [单位]      NVARCHAR(64)   NULL,
        [参考范围]  NVARCHAR(128)  NULL,
        [异常标志]  NVARCHAR(16)   NULL,   -- H高/L低/↑↓/阳性 ...
        [报告时间]  NVARCHAR(64)   NULL,   -- 推荐 ISO
        [样本]      NVARCHAR(64)   NULL,
        [检验类别]  NVARCHAR(128)  NULL,
        [送检科室]  NVARCHAR(128)  NULL
    );
    PRINT 'Created table intake_labs';
END
ELSE
    PRINT 'Table intake_labs already exists, skip CREATE';
GO

-- ============================================================
-- intake_examinations: 检查/影像 (可选) — 一行一个检查报告
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'intake_examinations')
BEGIN
    CREATE TABLE intake_examinations (
        id          BIGINT IDENTITY(1,1) PRIMARY KEY,
        [住院号]    NVARCHAR(64)   NULL,   -- ★与其它表保持一致★
        [检查类型]  NVARCHAR(64)   NULL,   -- CT/MRI/超声/X线 ..., 可留空
        [检查项目]  NVARCHAR(255)  NULL,
        [检查结论]  NVARCHAR(MAX)  NULL,
        [检查所见]  NVARCHAR(MAX)  NULL,
        [检查部位]  NVARCHAR(128)  NULL,
        [检查科室]  NVARCHAR(128)  NULL,
        [检查日期]  NVARCHAR(64)   NULL,   -- 推荐 ISO
        [报告日期]  NVARCHAR(64)   NULL    -- 推荐 ISO
    );
    PRINT 'Created table intake_examinations';
END
ELSE
    PRINT 'Table intake_examinations already exists, skip CREATE';
GO

-- 验证: 6 张表都在
SELECT COUNT(*) AS table_count, STRING_AGG(name, ', ') AS tables
FROM sys.tables
WHERE name IN ('intake_fees', 'intake_notes', 'intake_diagnoses',
               'intake_surgeries', 'intake_labs', 'intake_examinations');
GO
