-- =============================================================================
-- Javert 审计结果 + 专家审核工作台 142 归档表 DDL
-- 目标库: 192.168.31.142:1433, db=zadig (与 zadig_agent 共用同库)
-- 表前缀: Javert_  (与 zadig 现有表 drg_reconfirm_audit / agent_sessions 区分)
-- 幂等: 通过 sys.tables / sys.indexes 检查保护, 可重复执行不报错
-- 用途:
--   javert_audit_runs    — Web/CLI 双写归档审计运行结果 (本地 SQLite 仍 SoT)
--   javert_users         — 审核工作台账号 (bcrypt + last_login)
--   javert_vio_review    — 专家三态决策 (V/I/C + 评语), insert-only + is_latest
--   javert_audit_logs    — 全用户行为 trail (register/login/review_submit/...)
-- 注: 不建视图 (2026-07-12 DE 意见: 大数据量下视图慢, 关联查询在程序层做; 代码本就零视图依赖)
-- =============================================================================

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

-- ============================================================
-- javert_audit_runs: 单 (rule, patient) 审计结果
--   id: BIGINT auto-increment, 主键
--   run_id: aud_<12 char nanoid>, 业务唯一键 (与本地 SQLite 同源)
--   verdict: VIOLATION / CLEAN / INCONCLUSIVE
--   evidence_json / tool_calls_json: 完整 dump (NVARCHAR MAX)
--   rule_yaml_snapshot: 当次审计使用的 rule yaml 副本 (规则演进可追溯)
--   rule_status: 当次审计时的规则 status (drafting/ready/validated/abandoned)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'javert_audit_runs')
BEGIN
    CREATE TABLE javert_audit_runs (
        id                  BIGINT IDENTITY(1,1) PRIMARY KEY,
        run_id              NVARCHAR(50)   NOT NULL,
        rule_id             NVARCHAR(20)   NOT NULL,
        patient_id          NVARCHAR(50)   NOT NULL,
        verdict             NVARCHAR(20)   NOT NULL,
        confidence          FLOAT          NULL,
        headline            NVARCHAR(120)  NULL,
        reasoning           NVARCHAR(MAX)  NULL,
        evidence_json       NVARCHAR(MAX)  NULL,
        tool_calls_json     NVARCHAR(MAX)  NULL,
        eligibility_json    NVARCHAR(MAX)  NULL,
        promise_trace_json  NVARCHAR(MAX)  NULL,
        duration_ms         INT            NULL,
        model               NVARCHAR(200)  NULL,
        rule_yaml_snapshot  NVARCHAR(MAX)  NULL,
        rule_status         NVARCHAR(20)   NULL,
        triggered_by        NVARCHAR(50)   NULL,   -- web / cli-dry-run / cli-run
        started_at          DATETIME2      NULL,
        created_at          DATETIME2      NOT NULL DEFAULT GETDATE(),
        CONSTRAINT UQ_javert_audit_runs_run_id UNIQUE (run_id),
        CONSTRAINT CK_Javert_audit_verdict CHECK (verdict IN (N'VIOLATION', N'CLEAN', N'INCONCLUSIVE'))
    );
    PRINT 'Created table javert_audit_runs';
END
ELSE
    PRINT 'Table javert_audit_runs already exists, skip CREATE';
GO

-- add-public-audit-headline-contract: 可空公开短标题；旧行不回填
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'headline'
      AND Object_ID = Object_ID(N'javert_audit_runs')
)
BEGIN
    ALTER TABLE javert_audit_runs ADD headline NVARCHAR(120) NULL;
    PRINT 'Added column headline to javert_audit_runs';
END
ELSE
    PRINT 'Column headline already exists on javert_audit_runs';
GO

-- add-evolving-promise-harness: 可空、去标识终局 Promise trace (幂等迁移)
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'promise_trace_json'
      AND Object_ID = Object_ID(N'javert_audit_runs')
)
BEGIN
    ALTER TABLE javert_audit_runs ADD promise_trace_json NVARCHAR(MAX) NULL;
    PRINT 'Added column promise_trace_json to javert_audit_runs';
END
ELSE
    PRINT 'Column promise_trace_json already exists on javert_audit_runs';
GO

-- strengthen-oncology-drug-eligibility: 单一可空结构化资格 JSON (幂等迁移)
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'eligibility_json'
      AND Object_ID = Object_ID(N'javert_audit_runs')
)
BEGIN
    ALTER TABLE javert_audit_runs ADD eligibility_json NVARCHAR(MAX) NULL;
    PRINT 'Added column eligibility_json to javert_audit_runs';
END
ELSE
    PRINT 'Column eligibility_json already exists on javert_audit_runs';
GO

-- 索引 1: 按 rule + patient 反查最新结果
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_Javert_audit_rule_patient'
      AND object_id = OBJECT_ID(N'dbo.javert_audit_runs')
)
BEGIN
    CREATE INDEX ix_Javert_audit_rule_patient
        ON javert_audit_runs (rule_id, patient_id, created_at DESC);
    PRINT 'Created index ix_Javert_audit_rule_patient';
END
ELSE
    PRINT 'Index ix_Javert_audit_rule_patient already exists';
GO

-- 索引 2: 按时间倒序浏览
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_Javert_audit_created_at'
      AND object_id = OBJECT_ID(N'dbo.javert_audit_runs')
)
BEGIN
    CREATE INDEX ix_Javert_audit_created_at
        ON javert_audit_runs (created_at DESC);
    PRINT 'Created index ix_Javert_audit_created_at';
END
ELSE
    PRINT 'Index ix_Javert_audit_created_at already exists';
GO

-- 索引 3: 按 verdict + rule 聚合统计
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_Javert_audit_verdict_rule'
      AND object_id = OBJECT_ID(N'dbo.javert_audit_runs')
)
BEGIN
    CREATE INDEX ix_Javert_audit_verdict_rule
        ON javert_audit_runs (verdict, rule_id);
    PRINT 'Created index ix_Javert_audit_verdict_rule';
END
ELSE
    PRINT 'Index ix_Javert_audit_verdict_rule already exists';
GO

-- v0.7 (Javert) migration: 补 batch_tag 列 (老库 ALTER, 新库 CREATE 时已含)
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'batch_tag'
      AND Object_ID = Object_ID(N'javert_audit_runs')
)
BEGIN
    ALTER TABLE javert_audit_runs ADD batch_tag NVARCHAR(20) NULL;
    PRINT 'Added column batch_tag to javert_audit_runs (v0.7 migration)';
END
ELSE
    PRINT 'Column batch_tag already exists on javert_audit_runs';
GO

-- OCR pipeline: caseRef/version 派生安全重放键（不含原始患者标识）
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'replay_key'
      AND Object_ID = Object_ID(N'javert_audit_runs')
)
BEGIN
    ALTER TABLE javert_audit_runs ADD replay_key NVARCHAR(128) NULL;
    PRINT 'Added column replay_key to javert_audit_runs (OCR pipeline)';
END
ELSE
    PRINT 'Column replay_key already exists on javert_audit_runs';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = N'ux_Javert_audit_replay_rule'
      AND object_id = OBJECT_ID(N'dbo.javert_audit_runs')
)
BEGIN
    CREATE UNIQUE INDEX ux_Javert_audit_replay_rule
        ON javert_audit_runs (replay_key, rule_id)
        WHERE replay_key IS NOT NULL;
    PRINT 'Created index ux_Javert_audit_replay_rule';
END
ELSE
    PRINT 'Index ux_Javert_audit_replay_rule already exists';
GO

-- v0.9 (enhance-workbench-usability) migration: anchors_json 派生缓存
--   命中项目 / 锚点 (hit_resolver) 的确定性结果缓存. 渲染优先读此列, miss 则现算.
--   由 scripts/backfill_anchors.py 重放确定性逻辑回填 (不调 LLM, 同 run_id, 不增删行).
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'anchors_json'
      AND Object_ID = Object_ID(N'javert_audit_runs')
)
BEGIN
    ALTER TABLE javert_audit_runs ADD anchors_json NVARCHAR(MAX) NULL;
    PRINT 'Added column anchors_json to javert_audit_runs (v0.9 migration)';
END
ELSE
    PRINT 'Column anchors_json already exists on javert_audit_runs';
GO

-- add-verdict-gate-layer migration: gate_tag (裁决后确定性 gate 降级标签)
--   取值 ∈ {缺文书, 单次放过, 低置信降级, ''}. 工作台 facet 默认隐藏「缺文书」.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'gate_tag'
      AND Object_ID = Object_ID(N'javert_audit_runs')
)
BEGIN
    ALTER TABLE javert_audit_runs ADD gate_tag NVARCHAR(20) NULL;
    PRINT 'Added column gate_tag to javert_audit_runs (add-verdict-gate-layer migration)';
END
ELSE
    PRINT 'Column gate_tag already exists on javert_audit_runs';
GO

-- ============================================================
-- javert_users: 审核工作台账号
--   id 自增主键, username UNIQUE
--   pw_hash: bcrypt hash (passlib + bcrypt)
--   display_name: 可空, NULL → 渲染时回退 username
--   last_login: 登录路由更新; 用于"自上次登录的增量"
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'javert_users')
BEGIN
    CREATE TABLE javert_users (
        id              INT IDENTITY(1,1) PRIMARY KEY,
        username        NVARCHAR(64)   NOT NULL,
        pw_hash         NVARCHAR(255)  NOT NULL,
        display_name    NVARCHAR(128)  NULL,
        created_at      DATETIME2      NOT NULL DEFAULT SYSUTCDATETIME(),
        last_login      DATETIME2      NULL,
        CONSTRAINT UQ_javert_users_username UNIQUE (username)
    );
    PRINT 'Created table javert_users';
END
ELSE
    PRINT 'Table javert_users already exists, skip CREATE';
GO

-- ============================================================
-- javert_vio_review: 专家批复 (insert-only + is_latest)
--   每次 review_submit/update 都 INSERT 新行
--   先把同 (run_id, user_id) 老行 UPDATE is_latest=0, 再 INSERT 新行 is_latest=1
--   查询最新: WHERE is_latest=1
--   查询历史: WHERE run_id=? ORDER BY created_at
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'javert_vio_review')
BEGIN
    CREATE TABLE javert_vio_review (
        id              INT IDENTITY(1,1) PRIMARY KEY,
        run_id          NVARCHAR(50)   NOT NULL,
        user_id         INT            NOT NULL,
        -- 写入时从 JOIN 复制进来 (denormalized snapshot), 让 SSMS / Excel 直接看
        patient_id      NVARCHAR(50)   NULL,
        rule_id         NVARCHAR(20)   NULL,
        username        NVARCHAR(64)   NULL,
        review_verdict  NVARCHAR(16)   NOT NULL,
        comment         NVARCHAR(MAX)  NULL,
        created_at      DATETIME2      NOT NULL DEFAULT SYSUTCDATETIME(),
        is_latest       BIT            NOT NULL DEFAULT 1,
        CONSTRAINT CK_javert_review_verdict CHECK (review_verdict IN (N'V', N'I', N'C')),
        CONSTRAINT FK_javert_review_user FOREIGN KEY (user_id) REFERENCES javert_users(id)
    );
    PRINT 'Created table javert_vio_review (with denormalized patient_id/rule_id/username)';
END
ELSE
BEGIN
    -- 老库 migration: 补 3 列 (幂等, 用 sys.columns 检查存在性)
    IF NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID(N'dbo.javert_vio_review')
                     AND name = 'patient_id')
    BEGIN
        ALTER TABLE javert_vio_review ADD patient_id NVARCHAR(50) NULL;
        PRINT 'Added column javert_vio_review.patient_id';
    END;
    IF NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID(N'dbo.javert_vio_review')
                     AND name = 'rule_id')
    BEGIN
        ALTER TABLE javert_vio_review ADD rule_id NVARCHAR(20) NULL;
        PRINT 'Added column javert_vio_review.rule_id';
    END;
    IF NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID(N'dbo.javert_vio_review')
                     AND name = 'username')
    BEGIN
        ALTER TABLE javert_vio_review ADD username NVARCHAR(64) NULL;
        PRINT 'Added column javert_vio_review.username';
    END;
    PRINT 'Table javert_vio_review schema confirmed (with denormalized cols)';
END
GO

-- Backfill: 给老行补 patient_id / rule_id / username (新插入由 app 写, 这里只补历史)
UPDATE rv
   SET rv.patient_id = r.patient_id,
       rv.rule_id    = r.rule_id
  FROM javert_vio_review rv
  INNER JOIN javert_audit_runs r ON r.run_id = rv.run_id
 WHERE rv.patient_id IS NULL OR rv.rule_id IS NULL;
GO

UPDATE rv
   SET rv.username = u.username
  FROM javert_vio_review rv
  INNER JOIN javert_users u ON u.id = rv.user_id
 WHERE rv.username IS NULL;
GO

PRINT 'Backfilled javert_vio_review denormalized columns';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_javert_review_latest'
      AND object_id = OBJECT_ID(N'dbo.javert_vio_review')
)
BEGIN
    CREATE INDEX ix_javert_review_latest
        ON javert_vio_review (run_id, user_id, is_latest);
    PRINT 'Created index ix_javert_review_latest';
END
ELSE
    PRINT 'Index ix_javert_review_latest already exists';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_javert_review_run'
      AND object_id = OBJECT_ID(N'dbo.javert_vio_review')
)
BEGIN
    CREATE INDEX ix_javert_review_run
        ON javert_vio_review (run_id);
    PRINT 'Created index ix_javert_review_run';
END
ELSE
    PRINT 'Index ix_javert_review_run already exists';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_javert_review_user'
      AND object_id = OBJECT_ID(N'dbo.javert_vio_review')
)
BEGIN
    CREATE INDEX ix_javert_review_user
        ON javert_vio_review (user_id);
    PRINT 'Created index ix_javert_review_user';
END
ELSE
    PRINT 'Index ix_javert_review_user already exists';
GO

-- ============================================================
-- javert_audit_logs: 全用户行为 trail (insert-only, 永不删)
--   action: register/login/login_fail/logout/review_submit/review_update/export/...
--   target_id: run_id 或 username (action 决定语义)
--   payload_json: 携带具体内容 (verdict + comment + previous_verdict 等)
--   user_id 可空 (未登录的 register/login_fail 也记)
-- ============================================================
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'javert_audit_logs')
BEGIN
    CREATE TABLE javert_audit_logs (
        id              BIGINT IDENTITY(1,1) PRIMARY KEY,
        user_id         INT            NULL,
        action          NVARCHAR(32)   NOT NULL,
        target_id       NVARCHAR(64)   NULL,
        payload_json    NVARCHAR(MAX)  NULL,
        ip              NVARCHAR(45)   NULL,
        user_agent      NVARCHAR(255)  NULL,
        ts              DATETIME2      NOT NULL DEFAULT SYSUTCDATETIME()
    );
    PRINT 'Created table javert_audit_logs';
END
ELSE
    PRINT 'Table javert_audit_logs already exists, skip CREATE';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_javert_logs_user'
      AND object_id = OBJECT_ID(N'dbo.javert_audit_logs')
)
BEGIN
    CREATE INDEX ix_javert_logs_user
        ON javert_audit_logs (user_id);
    PRINT 'Created index ix_javert_logs_user';
END
ELSE
    PRINT 'Index ix_javert_logs_user already exists';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_javert_logs_action'
      AND object_id = OBJECT_ID(N'dbo.javert_audit_logs')
)
BEGIN
    CREATE INDEX ix_javert_logs_action
        ON javert_audit_logs (action);
    PRINT 'Created index ix_javert_logs_action';
END
ELSE
    PRINT 'Index ix_javert_logs_action already exists';
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'ix_javert_logs_ts'
      AND object_id = OBJECT_ID(N'dbo.javert_audit_logs')
)
BEGIN
    CREATE INDEX ix_javert_logs_ts
        ON javert_audit_logs (ts);
    PRINT 'Created index ix_javert_logs_ts';
END
ELSE
    PRINT 'Index ix_javert_logs_ts already exists';
GO

-- ============================================================
-- 视图 v_javert_reviews: 给运营 / SSMS 看的友好版
--   把 javert_vio_review 跟 javert_audit_runs + javert_users JOIN, 显式带出
--   患者住院号 (patient_id) + 规则 ID (rule_id) + 用户名 (username)
--   原表 javert_vio_review 保持窄设计 (insert-only + is_latest), 视图做拼接
-- ============================================================
IF OBJECT_ID(N'dbo.v_javert_reviews', N'V') IS NOT NULL
    DROP VIEW v_javert_reviews;
GO


PRINT 'Created view v_javert_reviews (review + patient_id + rule_id + username)';
GO

-- ============================================================
-- 视图 v_javert_audit_logs: 行为 trail 友好版 (带用户名)
-- ============================================================
IF OBJECT_ID(N'dbo.v_javert_audit_logs', N'V') IS NOT NULL
    DROP VIEW v_javert_audit_logs;
GO


PRINT 'Created view v_javert_audit_logs (action + username + target_id)';
GO

-- 验证
SELECT
    COUNT(*) AS table_count,
    STRING_AGG(name, ', ') AS tables
FROM sys.tables
WHERE name IN ('javert_audit_runs', 'javert_users', 'javert_vio_review', 'javert_audit_logs');
GO

SELECT
    COUNT(*) AS view_count,
    STRING_AGG(name, ', ') AS views
FROM sys.views
WHERE name IN ('v_javert_reviews', 'v_javert_audit_logs');
GO
