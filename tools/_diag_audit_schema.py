"""查询 audit 数据库表结构和最新 llm_node_call_log"""
import sqlite3, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

conn = sqlite3.connect("host_brain/data/audit.sqlite3")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("Tables:", tables)

for t in ["graph_run_trace", "llm_node_call_log", "model_control_log"]:
    if t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = [r[1] for r in cur.fetchall()]
        print(f"\n{t} columns: {cols}")
        cur.execute(f"SELECT * FROM {t} ORDER BY rowid DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            print(f"  latest row: {json.dumps(dict(row), ensure_ascii=False, default=str)[:600]}")

conn.close()
