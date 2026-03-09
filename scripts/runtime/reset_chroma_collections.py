"""
重置 Chroma 向量集合：删除旧 dim=384 集合，重建 episodic + canon（dim=1024）
"""
import urllib.request
import json
import sys

BASE = "http://localhost:8000/api/v2/tenants/default_tenant/databases/default_database"

OLD_COLLECTIONS = ["canon_world", "episodic", "bond", "canon_style", "distilled", "long_term"]
NEW_COLLECTIONS = [
    {"name": "episodic", "metadata": {"hnsw:space": "cosine", "vector_dim": 1024}},
    {"name": "canon",    "metadata": {"hnsw:space": "cosine", "vector_dim": 1024}},
]

def delete_collection(name: str):
    req = urllib.request.Request(
        f"{BASE}/collections/{name}", method="DELETE"
    )
    try:
        with urllib.request.urlopen(req) as r:
            print(f"  [DELETE] {name} -> {r.status} OK")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  [DELETE] {name} -> 404 不存在，跳过")
        else:
            print(f"  [DELETE] {name} -> 错误 {e.code}: {e.read().decode()}")
            sys.exit(1)

def create_collection(name: str, metadata: dict):
    payload = json.dumps({"name": name, "metadata": metadata}).encode()
    req = urllib.request.Request(
        f"{BASE}/collections",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            result = json.loads(r.read())
            print(f"  [CREATE] {name} -> {r.status} OK, id={result.get('id')}, dim={metadata.get('vector_dim')}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 409:
            print(f"  [CREATE] {name} -> 409 已存在，跳过")
        else:
            print(f"  [CREATE] {name} -> 错误 {e.code}: {body}")
            sys.exit(1)

def list_collections():
    req = urllib.request.Request(f"{BASE}/collections", method="GET")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

if __name__ == "__main__":
    print("=== 步骤 1：删除旧集合 ===")
    for name in OLD_COLLECTIONS:
        delete_collection(name)

    print("\n=== 步骤 2：创建新集合（dim=1024）===")
    for col in NEW_COLLECTIONS:
        create_collection(col["name"], col["metadata"])

    print("\n=== 步骤 3：验证当前集合 ===")
    cols = list_collections()
    for c in cols:
        dim = c.get("metadata", {}).get("vector_dim", "未知")
        print(f"  {c['name']}  dim={dim}  id={c['id']}")

    print("\n完成！")
