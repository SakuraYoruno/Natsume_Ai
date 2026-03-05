"""检查 chromadb 1.x 的可用 API"""
import chromadb, inspect, sys

print("chromadb version:", chromadb.__version__)
print("chromadb location:", chromadb.__file__)
print()

# 检查顶层 API
attrs = [a for a in dir(chromadb) if not a.startswith("_")]
print("顶层 API:", attrs)
print()

# 检查是否有 server 模块
try:
    import chromadb.server
    print("chromadb.server:", dir(chromadb.server))
except ImportError as e:
    print("chromadb.server 不可用:", e)

# 检查是否有 app 模块
try:
    import chromadb.app
    print("chromadb.app:", dir(chromadb.app))
except ImportError as e:
    print("chromadb.app 不可用:", e)

# 检查 PersistentClient
try:
    from chromadb.config import Settings
    client = chromadb.PersistentClient(
        path="data/chroma_db",
        settings=Settings(anonymized_telemetry=False),
    )
    print("\nPersistentClient 创建成功:", type(client))
    print("PersistentClient 方法:", [m for m in dir(client) if not m.startswith("_")])
except Exception as e:
    print("PersistentClient 失败:", e)
