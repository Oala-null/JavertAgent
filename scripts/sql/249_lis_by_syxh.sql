-- 249：只改下一行引号内的首页SYXH，然后整份执行（不要只选中最后一段）。
DECLARE @SYXH varchar(64) = '';  -- 在这里填工作台上的SYXH
DECLARE @Hospital varchar(16) = 'AYY8BNRF';

USE [sh_yb_platform];
SET NOCOUNT ON;
SET LOCK_TIMEOUT 10000;

-- 只读业务表；不接小结、不接费用、不改数据、不启动审计。
-- 本脚本先查本院同卡号的全部报告，再标记医疗记录和住院日期；不会隐藏其他次住院候选。
-- 报告按院区+BGDH+BGRQ去重后再接指标，防止重复报告头把指标数乘倍。
-- INDICATORS源表自身的重复行保留，便于核对原始行数。
-- “住院日期一致候选”不等于通过程序全部归属校验：卡类型、报告头冲突、病案号和其他首页归属仍需核对。
-- 多个结果集顺序：1首页 2汇总 3每份报告 4指标明细 5仅报告号相同但日期不同的计数。

IF NULLIF(LTRIM(RTRIM(@SYXH)), '') IS NULL
BEGIN
    SELECT N'请填写第一行的SYXH，然后整份执行。' AS [说明];
    RETURN;
END;
DECLARE @HomeCount bigint;
SELECT @HomeCount=COUNT_BIG(*) FROM dbo.TB_BA_SYJBK
WHERE YLJGYQDM=@Hospital AND SYXH=@SYXH;
IF @HomeCount <> 1
BEGIN
    SELECT @HomeCount AS [本院首页匹配行数],
           N'首页不存在或不唯一，未继续查询；请核对SYXH、院区和所连接的数据库。' AS [说明];
    RETURN;
END;

DECLARE @KH varchar(128), @KLX varchar(32), @Admission date, @Discharge date;
SELECT @KH=KH, @KLX=KLX,
       @Admission=TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),RYRQ,121),'-',''),'/',''),8),112),
       @Discharge=TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),CYRQ,121),'-',''),'/',''),8),112)
FROM dbo.TB_BA_SYJBK WHERE YLJGYQDM=@Hospital AND SYXH=@SYXH;
IF @KH IS NULL OR UPPER(LTRIM(RTRIM(@KH))) IN ('','-','0','NULL','NONE','NAN')
BEGIN
    SELECT N'该首页卡号为空或无效，无法通过卡号找到对应报告。' AS [说明];
    RETURN;
END;

-- 结果1：首页与本次运行所在数据库。
-- BEGIN_HOME
SELECT CONVERT(varchar(128),SERVERPROPERTY('ServerName')) AS [SQL实例], DB_NAME() AS [数据库],
       SYSDATETIME() AS [查询时间], h.YLJGYQDM AS [院区], h.SYXH AS [首页SYXH],
       h.BAH AS [病案号], h.KH AS [卡号], h.KLX AS [卡类型],
       h.RYRQ AS [首页入院时间原值], h.CYRQ AS [首页出院时间原值],
       @Admission AS [入院日期], @Discharge AS [出院日期]
FROM dbo.TB_BA_SYJBK h WHERE h.YLJGYQDM=@Hospital AND h.SYXH=@SYXH;
-- END_HOME

-- 结果2：总览。先看C（同卡所有指标行）和E（住院日期一致候选指标行）。
-- BEGIN_SUMMARY
;WITH Medical AS (
    SELECT m.JZLSH,m.KH,m.KLX,m.XGBZ,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.RYSJ,121),'-',''),'/',''),8),112) AS admission,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.CYSJ,121),'-',''),'/',''),8),112) AS discharge
    FROM dbo.TB_YL_ZY_MEDICAL_RECORD m
    WHERE m.YLJGYQDM=@Hospital AND m.KH=@KH
), ReportRows AS (
    SELECT r.YLJGYQDM,r.BGDH,r.BGRQ,r.JZLSH,r.KLX,r.BBMC,r.BGDLB,
           CASE WHEN EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH) THEN 1 ELSE 0 END AS medical_match,
           CASE WHEN r.KLX=@KLX THEN 1 ELSE 0 END AS card_type_match,
           CASE WHEN r.KLX=@KLX AND @Admission>'19000101' AND @Discharge>=@Admission
                AND EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH
                            AND m.KLX=@KLX AND m.XGBZ='1'
                            AND m.admission=@Admission AND m.discharge=@Discharge)
                THEN 1 ELSE 0 END AS same_stay_candidate
    FROM dbo.TB_LIS_REPORT r
    WHERE r.YLJGYQDM=@Hospital AND r.KH=@KH
), ReportKeys AS (
    SELECT YLJGYQDM,BGDH,BGRQ,COUNT_BIG(*) AS source_report_rows,
           COUNT(DISTINCT JZLSH) AS visit_count,
           MIN(JZLSH) AS first_visit,MAX(JZLSH) AS last_visit,
           MIN(BBMC) AS sample_name,MIN(BGDLB) AS report_category,
           MIN(card_type_match) AS all_card_types_match,
           MAX(medical_match) AS medical_match,MAX(same_stay_candidate) AS same_stay_candidate
    FROM ReportRows GROUP BY YLJGYQDM,BGDH,BGRQ
)

SELECT N'A 同卡号LIS_REPORT原始行数' AS [检查项],COUNT_BIG(*) AS [数量] FROM ReportRows
UNION ALL
SELECT N'B 去重报告键数（院区+报告号+报告日期）',COUNT_BIG(*) FROM ReportKeys
UNION ALL
SELECT N'C 同卡号全部报告能关联的指标原始行数',COUNT_BIG(*)
FROM dbo.TB_LIS_INDICATORS i WHERE EXISTS
 (SELECT 1 FROM ReportKeys r WHERE r.YLJGYQDM=i.YLJGYQDM AND r.BGDH=i.BGDH AND r.BGRQ=i.BGRQ)
UNION ALL
SELECT N'D 住院日期一致候选报告数（尚非程序最终接收数）',COUNT_BIG(*) FROM ReportKeys WHERE same_stay_candidate=1
UNION ALL
SELECT N'E 上述日期一致候选报告的指标原始行数',COUNT_BIG(*)
FROM dbo.TB_LIS_INDICATORS i WHERE EXISTS
 (SELECT 1 FROM ReportKeys r WHERE r.same_stay_candidate=1
  AND r.YLJGYQDM=i.YLJGYQDM AND r.BGDH=i.BGDH AND r.BGRQ=i.BGRQ)
UNION ALL
SELECT N'F 同卡号报告中完整键找不到指标的报告数',COUNT_BIG(*)
FROM ReportKeys r WHERE NOT EXISTS
 (SELECT 1 FROM dbo.TB_LIS_INDICATORS i WHERE i.YLJGYQDM=r.YLJGYQDM AND i.BGDH=r.BGDH AND i.BGRQ=r.BGRQ)
UNION ALL
SELECT N'G 同卡号报告中连不上本院医疗记录的报告数',COUNT_BIG(*) FROM ReportKeys WHERE medical_match=0
ORDER BY [检查项];
-- END_SUMMARY

-- 结果3：每份报告有多少指标；完整键为0但仅报告号>0时看结果5。
-- BEGIN_REPORTS
;WITH Medical AS (
    SELECT m.JZLSH,m.KH,m.KLX,m.XGBZ,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.RYSJ,121),'-',''),'/',''),8),112) AS admission,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.CYSJ,121),'-',''),'/',''),8),112) AS discharge
    FROM dbo.TB_YL_ZY_MEDICAL_RECORD m
    WHERE m.YLJGYQDM=@Hospital AND m.KH=@KH
), ReportRows AS (
    SELECT r.YLJGYQDM,r.BGDH,r.BGRQ,r.JZLSH,r.KLX,r.BBMC,r.BGDLB,
           CASE WHEN EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH) THEN 1 ELSE 0 END AS medical_match,
           CASE WHEN r.KLX=@KLX THEN 1 ELSE 0 END AS card_type_match,
           CASE WHEN r.KLX=@KLX AND @Admission>'19000101' AND @Discharge>=@Admission
                AND EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH
                            AND m.KLX=@KLX AND m.XGBZ='1'
                            AND m.admission=@Admission AND m.discharge=@Discharge)
                THEN 1 ELSE 0 END AS same_stay_candidate
    FROM dbo.TB_LIS_REPORT r
    WHERE r.YLJGYQDM=@Hospital AND r.KH=@KH
), ReportKeys AS (
    SELECT YLJGYQDM,BGDH,BGRQ,COUNT_BIG(*) AS source_report_rows,
           COUNT(DISTINCT JZLSH) AS visit_count,
           MIN(JZLSH) AS first_visit,MAX(JZLSH) AS last_visit,
           MIN(BBMC) AS sample_name,MIN(BGDLB) AS report_category,
           MIN(card_type_match) AS all_card_types_match,
           MAX(medical_match) AS medical_match,MAX(same_stay_candidate) AS same_stay_candidate
    FROM ReportRows GROUP BY YLJGYQDM,BGDH,BGRQ
)

SELECT r.BGDH AS [报告号],r.BGRQ AS [报告日期],r.first_visit AS [就诊号_最小],r.last_visit AS [就诊号_最大],
       r.visit_count AS [此报告键不同就诊号数],r.source_report_rows AS [REPORT源表行数],
       r.sample_name AS [标本名称_示例],r.report_category AS [报告类别_示例],
       r.medical_match AS [本院医疗记录能关联_1是],r.all_card_types_match AS [报告卡类型全一致_1是],
       r.same_stay_candidate AS [住院日期一致候选_1是],
       (SELECT COUNT_BIG(*) FROM dbo.TB_LIS_INDICATORS i
        WHERE i.YLJGYQDM=r.YLJGYQDM AND i.BGDH=r.BGDH AND i.BGRQ=r.BGRQ) AS [完整键指标行数],
       (SELECT COUNT_BIG(*) FROM dbo.TB_LIS_INDICATORS i
        WHERE i.YLJGYQDM=r.YLJGYQDM AND i.BGDH=r.BGDH) AS [仅同报告号指标行数_仅排查]
FROM ReportKeys r ORDER BY r.same_stay_candidate DESC,r.BGRQ DESC,r.BGDH;
-- END_REPORTS

-- 结果4：真正从INDICATORS查出的源表明细；不做TOP截断、不依赖小结。
-- BEGIN_INDICATORS
;WITH Medical AS (
    SELECT m.JZLSH,m.KH,m.KLX,m.XGBZ,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.RYSJ,121),'-',''),'/',''),8),112) AS admission,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.CYSJ,121),'-',''),'/',''),8),112) AS discharge
    FROM dbo.TB_YL_ZY_MEDICAL_RECORD m
    WHERE m.YLJGYQDM=@Hospital AND m.KH=@KH
), ReportRows AS (
    SELECT r.YLJGYQDM,r.BGDH,r.BGRQ,r.JZLSH,r.KLX,r.BBMC,r.BGDLB,
           CASE WHEN EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH) THEN 1 ELSE 0 END AS medical_match,
           CASE WHEN r.KLX=@KLX THEN 1 ELSE 0 END AS card_type_match,
           CASE WHEN r.KLX=@KLX AND @Admission>'19000101' AND @Discharge>=@Admission
                AND EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH
                            AND m.KLX=@KLX AND m.XGBZ='1'
                            AND m.admission=@Admission AND m.discharge=@Discharge)
                THEN 1 ELSE 0 END AS same_stay_candidate
    FROM dbo.TB_LIS_REPORT r
    WHERE r.YLJGYQDM=@Hospital AND r.KH=@KH
), ReportKeys AS (
    SELECT YLJGYQDM,BGDH,BGRQ,COUNT_BIG(*) AS source_report_rows,
           COUNT(DISTINCT JZLSH) AS visit_count,
           MIN(JZLSH) AS first_visit,MAX(JZLSH) AS last_visit,
           MIN(BBMC) AS sample_name,MIN(BGDLB) AS report_category,
           MIN(card_type_match) AS all_card_types_match,
           MAX(medical_match) AS medical_match,MAX(same_stay_candidate) AS same_stay_candidate
    FROM ReportRows GROUP BY YLJGYQDM,BGDH,BGRQ
)

SELECT @SYXH AS [查询首页SYXH],i.YLJGYQDM AS [院区],i.BGDH AS [报告号],i.BGRQ AS [报告日期],
       r.first_visit AS [报告就诊号_最小],r.last_visit AS [报告就诊号_最大],
       r.same_stay_candidate AS [住院日期一致候选_1是],r.medical_match AS [本院医疗记录能关联_1是],
       r.all_card_types_match AS [报告卡类型全一致_1是],
       CASE WHEN r.visit_count>1 THEN N'报告键涉及多个就诊号，需核对'
            WHEN r.all_card_types_match=0 THEN N'报告卡类型有冲突，需核对'
            WHEN r.medical_match=0 THEN N'同卡报告；本院医疗记录未连上'
            WHEN r.same_stay_candidate=1 THEN N'住院日期一致候选；尚非最终归属结论'
            ELSE N'同卡其他住院，或时间/卡类型/有效标志不符，需核对' END AS [关联说明],
       i.JYZBDM AS [指标编码],i.JYZBMC AS [指标名称],i.JYZBJG AS [结果],
       i.JLDW AS [单位],i.CKZ AS [参考值],i.YCTS AS [异常提示],
       r.source_report_rows AS [对应REPORT源表行数]
FROM dbo.TB_LIS_INDICATORS i
JOIN ReportKeys r ON r.YLJGYQDM=i.YLJGYQDM AND r.BGDH=i.BGDH AND r.BGRQ=i.BGRQ
ORDER BY r.same_stay_candidate DESC,i.BGRQ DESC,i.BGDH,i.JYZBDM,i.JYZBMC;
-- END_INDICATORS

-- 结果5：仅按报告号能找到、但日期对不上的条数；不展示可能属于别次检验的结果值。
-- BEGIN_DATE_MISMATCH
;WITH Medical AS (
    SELECT m.JZLSH,m.KH,m.KLX,m.XGBZ,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.RYSJ,121),'-',''),'/',''),8),112) AS admission,
           TRY_CONVERT(date,LEFT(REPLACE(REPLACE(CONVERT(varchar(40),m.CYSJ,121),'-',''),'/',''),8),112) AS discharge
    FROM dbo.TB_YL_ZY_MEDICAL_RECORD m
    WHERE m.YLJGYQDM=@Hospital AND m.KH=@KH
), ReportRows AS (
    SELECT r.YLJGYQDM,r.BGDH,r.BGRQ,r.JZLSH,r.KLX,r.BBMC,r.BGDLB,
           CASE WHEN EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH) THEN 1 ELSE 0 END AS medical_match,
           CASE WHEN r.KLX=@KLX THEN 1 ELSE 0 END AS card_type_match,
           CASE WHEN r.KLX=@KLX AND @Admission>'19000101' AND @Discharge>=@Admission
                AND EXISTS (SELECT 1 FROM Medical m WHERE m.JZLSH=r.JZLSH
                            AND m.KLX=@KLX AND m.XGBZ='1'
                            AND m.admission=@Admission AND m.discharge=@Discharge)
                THEN 1 ELSE 0 END AS same_stay_candidate
    FROM dbo.TB_LIS_REPORT r
    WHERE r.YLJGYQDM=@Hospital AND r.KH=@KH
), ReportKeys AS (
    SELECT YLJGYQDM,BGDH,BGRQ,COUNT_BIG(*) AS source_report_rows,
           COUNT(DISTINCT JZLSH) AS visit_count,
           MIN(JZLSH) AS first_visit,MAX(JZLSH) AS last_visit,
           MIN(BBMC) AS sample_name,MIN(BGDLB) AS report_category,
           MIN(card_type_match) AS all_card_types_match,
           MAX(medical_match) AS medical_match,MAX(same_stay_candidate) AS same_stay_candidate
    FROM ReportRows GROUP BY YLJGYQDM,BGDH,BGRQ
)

SELECT i.BGDH AS [仅报告号相同],i.BGRQ AS [指标表中的另一个日期],COUNT_BIG(*) AS [指标行数_不能直接认作本次],
       N'可能是报告日期口径不一致，也可能是报告号跨日期复用；只作排查，不纳入结果4' AS [说明]
FROM dbo.TB_LIS_INDICATORS i
WHERE i.YLJGYQDM=@Hospital
  AND EXISTS (SELECT 1 FROM ReportKeys r WHERE r.BGDH=i.BGDH)
  AND NOT EXISTS (SELECT 1 FROM ReportKeys r WHERE r.BGDH=i.BGDH AND r.BGRQ=i.BGRQ)
GROUP BY i.BGDH,i.BGRQ ORDER BY i.BGDH,i.BGRQ;
-- END_DATE_MISMATCH

-- 判断方式：
-- 1. 结果4只有8行：当前库中，通过该首页卡号和完整报告键能连到的指标就是这8行。
--    这不证明医院总共只做了8项；缺失REPORT、错误卡号/院区/报告键、同步范围都可能造成漏关联。
-- 2. 结果4很多，但E少：先看其他住院、无效记录、日期/卡类型及报告归属；不能全部算成本次。
-- 3. E比页面多：需对照具体报告号，进一步核对程序归属闸和本次审计快照/页面读取时间。
-- 4. F>0：有报告头但完整键没有指标；结果3和5能区分日期错配候选与完全没有同号指标。
-- 5. 收费项目与检验指标不是一对一：一个检验套餐可能含多项指标，不能直接拿两边行数相减。
-- 页面和本脚本不是同一时点的事务快照。此文件只做源数据查询，不改变审计取数规则。
