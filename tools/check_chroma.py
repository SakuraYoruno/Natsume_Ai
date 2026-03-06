import sqlite3, pathlib

chroma_db = pathlib.Path("D:/nastume/Natsume_Ai/data/chroma_db/chroma.sqlite3")
conn = sqlite3.connect(str(chroma_db))
cur = conn.cursor()

# check embeddings table schema
cur.execute("PRAGMA table_info(embeddings)")
cols = cur.fetchall()
print("embeddings columns:", [c[1] for c in cols])

# count total embeddings
cur.execute("SELECT COUNT(*) FROM embeddings")
print("total embeddings:", cur.fetchone()[0])

# check segments (each segment belongs to a collection)
cur.execute("PRAGMA table_info(segments)")
seg_cols = cur.fetchall()
print("segments columns:", [c[1] for c in seg_cols])

cur.execute("SELECT id, scope, type, collection FROM segments")
segs = cur.fetchall()
print("segments:", segs)

# count embeddings per segment
for seg in segs:
    seg_id = seg[0]
    cur.execute("SELECT COUNT(*) FROM embeddings WHERE segment_id=?", (seg_id,))
    cnt = cur.fetchone()[0]
    print(f"  segment {seg_id[:8]}... ({seg[1]}/{seg[2]}): {cnt} embeddings")

conn.close()

# find all db files
import os
print("\nAll DB files in project:")
for root, dirs, files in os.walk("D:/nastume/Natsume_Ai"):
    dirs[:] = [d for d in dirs if d not in ['.venv', '__pycache__', '.git', 'node_modules', 'jdk17', 'install']]
    for f in files:
        if f.endswith(('.db', '.sqlite', '.sqlite3')):
            p = pathlib.Path(root) / f
            print(f"  {p}  ({p.stat().st_size:,} bytes)")
