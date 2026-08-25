"""
用 Python API 直接启动 ChromaDB HTTP 服务（不依赖 CLI 的 Rust 绑定）
chromadb 1.x 支持通过 chromadb.server.fastapi 直接启动
"""
from __future__ import annotations
import pathlib, urllib.request

ROOT = pathlib.Path(".").resolve()
CHROMA_DATA = ROOT / "runtime" / "chroma" / "data"
CHROMA_DATA.mkdir(parents=True, exist_ok=True)

HOST = "127.0.0.1"
PORT = 18000
ENDPOINT = f"http://{HOST}:{PORT}"

COLLECTIONS = {
    "episodic": 1024,
}
TENANT = "default_tenant"
DATABASE = "default_database"


def is_running() -> bool:
    try:
        urllib.request.urlopen(f"{ENDPOINT}/api/v2/heartbeat", timeout=3)
        return True
    except Exception:
        return False


def start_server_api() -> None:
    """通过 chromadb Python API 启动 HTTP 服务"""
    import chromadb
    from chromadb.config import Settings

    print(f"[ChromaDB] 版本: {chromadb.__version__}")
    print(f"[ChromaDB] 数据目录: {CHROMA_DATA}")
    print(f"[ChromaDB] 监听: {HOST}:{PORT}")

    # chromadb 1.x 使用 chromadb.Server 或 uvicorn 方式
    try:
        # 方式1: 尝试 chromadb.Server (1.x API)
        import uvicorn
        from chromadb.app import create_app

        settings = Settings(
            chroma_server_host=HOST,
            chroma_server_http_port=PORT,
            is_persistent=True,
            persist_directory=str(CHROMA_DATA),
            anonymized_telemetry=False,
        )
        app = create_app(settings)
        print(f"[ChromaDB] 使用 uvicorn 启动...")
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
    except (ImportError, AttributeError) as e:
        print(f"[ChromaDB] create_app 方式失败: {e}")
        # 方式2: 直接用 PersistentClient（仅本地，不提供 HTTP）
        print("[ChromaDB] 回退到 PersistentClient 模式（本地嵌入式）")
        start_persistent_client()


def start_persistent_client() -> None:
    """使用 PersistentClient 模式（嵌入式，不需要 HTTP 服务）"""
    import chromadb
    from chromadb.config import Settings

    client = chromadb.PersistentClient(
        path=str(CHROMA_DATA),
        settings=Settings(anonymized_telemetry=False),
    )
    print(f"[ChromaDB] PersistentClient 已启动，数据目录: {CHROMA_DATA}")
    init_collections_with_client(client)
    print("\n[ChromaDB] 注意：PersistentClient 为嵌入式模式，不提供 HTTP 服务")
    print("[ChromaDB] host_brain 需要配置为使用本地嵌入式模式")


def init_collections_with_client(client) -> None:
    print(f"\n[ChromaDB] 初始化长期记忆 collection")
    try:
        client.get_or_create_tenant(TENANT)
    except Exception:
        pass
    try:
        client.get_or_create_database(DATABASE, tenant=TENANT)
    except Exception:
        pass

    for name, dim in COLLECTIONS.items():
        try:
            col = client.get_or_create_collection(
                name=name,
                metadata={"hnsw:space": "cosine", "vector_dim": dim},
            )
            count = col.count()
            print(f"  [OK] {name:15s} dim={dim}  已有条目={count}")
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")


if __name__ == "__main__":
    if is_running():
        print(f"[ChromaDB] 服务已在运行: {ENDPOINT}")
        # 直接初始化 collections
        import chromadb
        from chromadb.config import Settings
        client = chromadb.HttpClient(
            host=HOST, port=PORT,
            tenant=TENANT, database=DATABASE,
            settings=Settings(anonymized_telemetry=False),
        )
        init_collections_with_client(client)
    else:
        start_server_api()
