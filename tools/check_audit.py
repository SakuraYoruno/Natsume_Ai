import sqlite3, pathlib

# audit.sqlite3
audit_db = pathlib.Path("D:/nastume/Natsume_Ai/host_brain/data/audit.sqlite3")
conn = sqlite3.connect(str(audit_db))
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
print("audit.sqlite3 tables:", tables)
for t in tables:
    cur.execute(f"SELECT COUNT(*) FROM [{t}]")
    cnt = cur.fetchone()[0]
    print(f"  {t}: {cnt} rows")
    if cnt > 0 and cnt <= 3:
        cur.execute(f"SELECT * FROM [{t}] LIMIT 3")
        rows = cur.fetchall()
        cur.execute(f"PRAGMA table_info([{t}])")
        cols = [c[1] for c in cur.fetchall()]
        print(f"    cols: {cols}")
        for r in rows:
            print(f"    {r}")
conn.close()
