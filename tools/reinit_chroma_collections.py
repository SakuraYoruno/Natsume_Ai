"""
重新初始化 ChromaDB 长期记忆 collection（dim=1024）
删除旧的非长期 collections，只保留 episodic。
"""
from __future__ import annotations
import urllib.request, sys

HOST = "127.0.0.1"
PORT = 18000
ENDPOINT = f"http://{HOST}:{PORT}"
TENANT = "default_tenant"
DATABASE = "default_database"

COLLECTIONS = {
    "episodic": 1024,
}


def is_running() -> bool:
    try:
        urllib.request.urlopen(f"{ENDPOINT}/api/v2/heartbeat", timeout=3)
        return True
    except Exception:
        return False


def reinit_collections() -> None:
    import chromadb
    from chromadb.config import Settings

    if not is_running():
        print(f"[ChromaDB] 服务未运行，请先启动: python tools/start_chroma_bg.py")
        sys.exit(1)

    print(f"[ChromaDB] 连接到 {ENDPOINT}")
    client = chromadb.HttpClient(
        host=HOST, port=PORT,
        tenant=TENANT, database=DATABASE,
        settings=Settings(anonymized_telemetry=False),
    )

    # 确保 tenant/database 存在
    try:
        client.get_or_create_tenant(TENANT)
    except Exception:
        pass
    try:
        client.get_or_create_database(DATABASE, tenant=TENANT)
    except Exception:
        pass

    existing_names = [c.name for c in client.list_collections()]
    for name in sorted(existing_names):
        if name in COLLECTIONS:
            continue
        print(f"  [删除] {name}: 非长期记忆 collection")
        client.delete_collection(name=name)

    print(f"\n[ChromaDB] 重新初始化长期记忆 collection (dim=1024)")
    for name, dim in COLLECTIONS.items():
        # 先尝试删除旧的（如果存在且 dim 不对）
        try:
            existing = client.get_collection(name=name)
            old_meta = existing.metadata or {}
            old_dim = old_meta.get("vector_dim", -1)
            if int(old_dim) != dim:
                print(f"  [重建] {name}: 旧 dim={old_dim} → 新 dim={dim}")
                client.delete_collection(name=name)
            else:
                count = existing.count()
                print(f"  [OK]   {name:15s} dim={dim}  已有条目={count}（无需重建）")
                continue
        except Exception:
            pass  # collection 不存在，直接创建

        # 创建新 collection
        try:
            col = client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine", "vector_dim": dim},
            )
            count = col.count()
            print(f"  [创建] {name:15s} dim={dim}  已有条目={count}")
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")

    print(f"\n[ChromaDB] 长期记忆 collection 初始化完成")


if __name__ == "__main__":
    reinit_collections()
