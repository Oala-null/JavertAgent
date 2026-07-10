#!/usr/bin/env bash
# sync sqlite → 142, 并兜底修复 batch_tag (绕开 write_audit 跳重复的 bug)

set -e

# 1) 重置 szx 已同步标记 (让 pending-only 重新走)
sqlite3 output/audit.sqlite "UPDATE audit_runs SET synced_at = NULL WHERE batch_tag='szx' AND synced_at IS NOT NULL;" >/dev/null

# 2) 走标准 sync
JAVERT_SQL_ENABLED=true uv run javert sync-to-mssql --pending-only 2>&1 | tail -5

# 3) 兜底 UPDATE batch_tag (write_audit 跳重复时不会更新)
JAVERT_SQL_ENABLED=true uv run python -c "
import sqlite3, urllib.parse
from sqlalchemy import create_engine, text
from javert.config import get_config
cfg = get_config()
with sqlite3.connect(cfg.audit_db_path) as conn:
    run_ids = [r[0] for r in conn.execute(\"SELECT run_id FROM audit_runs WHERE batch_tag='szx'\").fetchall()]
pwd = urllib.parse.quote_plus(cfg.sql_password)
url = f'mssql+pyodbc://{cfg.sql_user}:{pwd}@{cfg.sql_host}:{cfg.sql_port}/{cfg.sql_database}?driver={urllib.parse.quote_plus(cfg.sql_driver)}&TrustServerCertificate=yes'
eng = create_engine(url)
with eng.connect() as conn:
    updated = 0
    for i in range(0, len(run_ids), 100):
        chunk = run_ids[i:i+100]
        ph = ','.join([f':r{j}' for j in range(len(chunk))])
        params = {f'r{j}': rid for j, rid in enumerate(chunk)}
        r = conn.execute(text(f\"UPDATE javert_audit_runs SET batch_tag='szx' WHERE run_id IN ({ph})\"), params)
        updated += r.rowcount
    conn.commit()
    print(f'batch_tag 兜底 UPDATE: {updated} 行')

    # 汇总
    rows = conn.execute(text(\"\"\"
        SELECT patient_id, COUNT(*) total,
               SUM(CASE WHEN verdict='VIOLATION' THEN 1 ELSE 0 END) v,
               SUM(CASE WHEN verdict='INCONCLUSIVE' THEN 1 ELSE 0 END) i,
               SUM(CASE WHEN verdict='CLEAN' THEN 1 ELSE 0 END) c
        FROM javert_audit_runs WHERE batch_tag='szx'
        GROUP BY patient_id ORDER BY patient_id
    \"\"\")).fetchall()
    print('')
    print('szx 在 142 实时状态:')
    for r in rows:
        print(f'  {r[0]}: total={r[1]} V={r[2]} I={r[3]} C={r[4]}')
"
