"""查询最新 chat 请求的完整链路"""
import sqlite3, json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

conn = sqlite3.connect("host_brain/data/audit.sqlite3")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

req_id = "req_b0bf774c529c424a8b770796d6c0858f"
print(f"查询请求: {req_id}\n")

all_tables = [
    "intent_log", "governor_log", "error_log", "graph_run_trace",
    "llm_node_call_log", "retrieval_event_log",
]

for table in all_tables:
    try:
        cur.execute(f"SELECT * FROM {table} WHERE request_id=?", (req_id,))
        rows = cur.fetchall()
        if rows:
            print(f"\n{'='*50}")
            print(f"  {table}")
            print(f"{'='*50}")
            for row in rows:
                d = dict(row)
                for k, v in d.items():
                    if v and isinstance(v, str) and len(v) > 100:
                        try:
                            parsed = json.loads(v)
                            print(f"  [{k}]: {json.dumps(parsed, ensure_ascii=False, indent=2)[:400]}")
                        except Exception:
                            print(f"  {k}: {str(v)[:200]}")
                    else:
                        print(f"  {k}: {v}")
                print("  ---")
        else:
            print(f"  [{table}]: 无记录")
    except Exception as e:
        print(f"  [{table}] error: {e}")

conn.close()
