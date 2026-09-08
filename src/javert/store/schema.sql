-- Javert audit store schema (v10: 加 nullable 慢病结构化结果)
-- 一张主表 + 一张元数据表 + 索引
-- v2 升级: synced_at / sync_attempts / sync_last_error 三列, migration 由 init_schema 兼容处理

CREATE TABLE IF NOT EXISTS audit_runs (
    run_id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('VIOLATION', 'CLEAN', 'INCONCLUSIVE')),
    confidence REAL,
    headline TEXT,
    reasoning TEXT,
    evidence_json TEXT,
    tool_calls_json TEXT,
    duration_ms INTEGER,
    model TEXT,
    started_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- v2: 142 同步状态
    synced_at TEXT,                    -- NULL = 未同步; 非 NULL = 142 同步成功时间戳
    sync_attempts INTEGER DEFAULT 0,   -- 累计尝试次数 (含失败)
    sync_last_error TEXT,              -- 最后一次失败的 error 文本
    -- v3 (v0.7): batch tag (v1.0 baseline / v1.2 新数据集...). NULL = baseline 无标
    batch_tag TEXT,
    -- v4 (v0.9): 命中项目/锚点 (hit_resolver) 确定性缓存; backfill_anchors.py 回填
    anchors_json TEXT,
    -- v5 (add-verdict-gate-layer): gate 降级标签 (缺文书 / 单次放过 / 低置信降级 / '')
    gate_tag TEXT,
    -- v6 (strengthen-oncology-drug-eligibility): 可空结构化资格结果
    eligibility_json TEXT,
    clinical_criteria_json TEXT,
    -- v7 (add-evolving-promise-harness): 可空、去标识终局 Promise trace
    promise_trace_json TEXT,
    -- v8 (OCR pipeline): caseRef/version 派生安全重放键
    replay_key TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_rule_patient ON audit_runs(rule_id, patient_id);
CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_runs(created_at);
-- idx_audit_unsynced (synced_at, created_at) 由 audit_store._ensure_v2_columns 创建,
-- 因为老库可能还没有 synced_at 列, 需 ALTER 后再 CREATE INDEX

CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO _meta(key, value) VALUES ('schema_version', '10');
