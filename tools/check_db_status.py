import sqlite3, os, pathlib, sys

ROOT = pathlib.Path("D:/nastume/Natsume_Ai")

print("=" * 60)
print("Six-Store Data Diagnosis")
print("=" * 60)

# 1. SQLite Ledger
print("\n[1] SQLite Ledger (audit tables)")
db_paths = (
    list((ROOT / "data").rglob("*.db"))
    + list((ROOT / "data").rglob("*.sqlite"))
    + list((ROOT / "host_brain").rglob("*.db"))
    + list((ROOT / "host_brain").rglob("*.sqlite"))
)
if not db_paths:
    print("  EMPTY - no .db/.sqlite files found")
for p in db_paths:
    try:
        rel = p.relative_to(ROOT)
    except Exception:
        rel = p
    print(f"  file: {rel}  ({p.stat().st_size:,} bytes)")
    try:
        conn = sqlite3.connect(str(p))
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cur.fetchall()]
        if not tables:
            print("    (no tables)")
        for t in tables:
            cur.execute(f"SELECT COUNT(*) FROM [{t}]")
            cnt = cur.fetchone()[0]
            print(f"    table {t}: {cnt} rows")
        conn.close()
    except Exception as e:
        print(f"    read error: {e}")

# 2. ChromaDB / Episodic Vector
print("\n[2] ChromaDB / Episodic Vector Store")
chroma_dirs = [ROOT / "data" / "chroma_db", ROOT / "chroma_db", ROOT / "data" / "chroma"]
found_chroma = False
for chroma in chroma_dirs:
    if chroma.exists():
        found_chroma = True
        total = sum(f.stat().st_size for f in chroma.rglob("*") if f.is_file())
        print(f"  dir: {chroma.name}  ({total:,} bytes total)")
        for item in sorted(chroma.iterdir()):
            if item.is_dir():
                sub_total = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                print(f"    collection [{item.name}]: {sub_total:,} bytes")
            else:
                print(f"    file: {item.name}  ({item.stat().st_size:,} bytes)")
if not found_chroma:
    print("  EMPTY - ChromaDB dir not found")

# 3. Neo4j Bond SSOT
print("\n[3] Neo4j Bond SSOT")
neo4j_dirs = [ROOT / "data" / "neo4j", ROOT / "neo4j", ROOT / "data" / "graph"]
found_neo4j = False
for neo4j in neo4j_dirs:
    if neo4j.exists():
        found_neo4j = True
        total = sum(f.stat().st_size for f in neo4j.rglob("*") if f.is_file())
        print(f"  dir: {neo4j.name}  ({total:,} bytes total)")
if not found_neo4j:
    print("  EMPTY - Neo4j data dir not found")

# 4. Style Canon Store
print("\n[4] Style Canon Store")
style_dirs = [ROOT / "data" / "canon_style", ROOT / "data" / "style_canon", ROOT / "data" / "style"]
found_style = False
for d in style_dirs:
    if d.exists():
        found_style = True
        files = [f for f in d.rglob("*") if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        print(f"  dir: {d.name}  ({len(files)} files, {total:,} bytes)")
        for f in sorted(files)[:10]:
            print(f"    {f.name}  ({f.stat().st_size:,} bytes)")
if not found_style:
    print("  EMPTY - Style Canon dir not found")

# 5. World Canon Store
print("\n[5] World Canon Store")
world_dirs = [ROOT / "data" / "canon_world", ROOT / "data" / "world_canon", ROOT / "data" / "world"]
found_world = False
for d in world_dirs:
    if d.exists():
        found_world = True
        files = [f for f in d.rglob("*") if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        print(f"  dir: {d.name}  ({len(files)} files, {total:,} bytes)")
        for f in sorted(files)[:10]:
            print(f"    {f.name}  ({f.stat().st_size:,} bytes)")
if not found_world:
    print("  EMPTY - World Canon dir not found")

# 6. Artifact Vault
print("\n[6] Artifact Vault")
vault_dirs = [ROOT / "data" / "artifact_vault", ROOT / "data" / "vault", ROOT / "host_brain" / "lsp" / "vault"]
found_vault = False
for d in vault_dirs:
    if d.exists():
        found_vault = True
        files = [f for f in d.rglob("*") if f.is_file()]
        total = sum(f.stat().st_size for f in files)
        print(f"  dir: {d.name}  ({len(files)} files, {total:,} bytes)")
if not found_vault:
    print("  EMPTY - Artifact Vault dir not found")

# 7. Full data/ tree
print("\n[7] data/ directory overview")
data_dir = ROOT / "data"
if data_dir.exists():
    for item in sorted(data_dir.iterdir()):
        if item.is_dir():
            sub_files = [f for f in item.rglob("*") if f.is_file()]
            sub_total = sum(f.stat().st_size for f in sub_files)
            print(f"  {item.name}/  ({len(sub_files)} files, {sub_total:,} bytes)")
        else:
            print(f"  {item.name}  ({item.stat().st_size:,} bytes)")
else:
    print("  data/ dir not found")

print("\n" + "=" * 60)
