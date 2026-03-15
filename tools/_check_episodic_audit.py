"""Quick check: recent requests episodic write status from audit."""
import json
import os
import sqlite3

db = os.environ.get("Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3")
if not os.path.exists(db):
    db = "runtime/host/audit.sqlite3"
if not os.path.exists(db):
    print("DB not found:", db)
    raise SystemExit(1)

conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT request_id, detail, ts_ms FROM error_log "
    "WHERE stage = 'AUDIT_SNAPSHOT' AND detail IS NOT NULL "
    "ORDER BY ts_ms DESC LIMIT 5"
).fetchall()
for r in rows:
    try:
        detail = json.loads(r["detail"]) if isinstance(r["detail"], str) else (r["detail"] or {})
    except Exception:
        detail = {}
    tags = detail.get("audit_tags") or []
    tags_str = ",".join(tags) if isinstance(tags, list) else str(tags)
    episodic_skip = any("EPISODIC_SKIPPED" in str(t) or "MEMORY_GUARD_REJECTED" in str(t) for t in tags)
    episodic_ok = "EPISODIC_WRITTEN" in tags_str
    print(r["request_id"], "episodic_skip=", episodic_skip, "episodic_written=", episodic_ok, "|", tags_str[:180])
conn.close()
