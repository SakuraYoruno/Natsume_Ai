"""
启动 ChromaDB HTTP 服务并初始化长期记忆 collection
用法：python tools/start_chroma.py
"""
from __future__ import annotations
import subprocess, sys, time, pathlib, os, urllib.request, json

ROOT = pathlib.Path(".").resolve()
CHROMA_DATA = ROOT / "runtime" / "chroma" / "data"
CHROMA_DATA.mkdir(parents=True, exist_ok=True)

CHROMA_EXE = ROOT / ".venv" / "Scripts" / "chroma.exe"
HOST = "127.0.0.1"
PORT = 18000
ENDPOINT = f"http://{HOST}:{PORT}"

# 当前主链只保留长期记忆向量库；Style/Canon 不再混入 Chroma 召回。
# 与 .env 中 CHROMA_COLLECTION_PROFILES_JSON={"episodic":1024} 保持一致。
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


def start_server() -> subprocess.Popen:
    print(f"[ChromaDB] 启动服务: {CHROMA_EXE}")
    print(f"[ChromaDB] 数据目录: {CHROMA_DATA}")
    print(f"[ChromaDB] 监听地址: {HOST}:{PORT}")
    proc = subprocess.Popen(
        [
            str(CHROMA_EXE), "run",
            "--host", HOST,
            "--port", str(PORT),
            "--path", str(CHROMA_DATA),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc


def wait_ready(proc: subprocess.Popen, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"[ChromaDB] 进程意外退出，退出码: {proc.returncode}")
            return False
        if is_running():
            return True
        time.sleep(0.5)
    return False


def ensure_tenant_and_database(client) -> None:
    """确保 tenant 和 database 存在"""
    try:
        client.get_or_create_tenant(TENANT)
    except Exception:
        pass
    try:
        client.get_or_create_database(DATABASE, tenant=TENANT)
    except Exception:
        pass


def init_collections() -> None:
    """初始化长期记忆 collection。"""
    import chromadb
    from chromadb.config import Settings

    print(f"\n[ChromaDB] 初始化长期记忆 collection (tenant={TENANT}, database={DATABASE})")
    client = chromadb.HttpClient(
        host=HOST,
        port=PORT,
        tenant=TENANT,
        database=DATABASE,
        settings=Settings(anonymized_telemetry=False),
    )

    # 确保 tenant/database 存在
    ensure_tenant_and_database(client)

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


def main() -> None:
    if is_running():
        print(f"[ChromaDB] 服务已在运行: {ENDPOINT}")
        init_collections()
        return

    proc = start_server()

    print("[ChromaDB] 等待服务就绪...", end="", flush=True)
    ready = wait_ready(proc, timeout=30)

    if not ready:
        print(" 超时！")
        # 打印进程输出
        try:
            out, _ = proc.communicate(timeout=2)
            print("[ChromaDB] 进程输出:")
            print(out[:2000])
        except Exception:
            pass
        sys.exit(1)

    print(f" 就绪！({ENDPOINT})")
    init_collections()

    print(f"\n[ChromaDB] 服务运行中，PID={proc.pid}")
    print("[ChromaDB] 按 Ctrl+C 停止服务\n")

    try:
        while True:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if line:
                print(f"[chroma] {line.rstrip()}")
    except KeyboardInterrupt:
        print("\n[ChromaDB] 正在停止...")
        proc.terminate()
        proc.wait(timeout=5)
        print("[ChromaDB] 已停止")


if __name__ == "__main__":
    main()
