"""
以 Windows 后台进程方式启动 ChromaDB，并初始化长期记忆 collection
用法：python tools/start_chroma_bg.py
"""
from __future__ import annotations
import subprocess, sys, time, pathlib, urllib.request

ROOT = pathlib.Path(".").resolve()
CHROMA_DATA = ROOT / "runtime" / "chroma" / "data"
CHROMA_DATA.mkdir(parents=True, exist_ok=True)

# 自动查找 chroma.exe：优先 .venv，其次系统 Python Scripts，最后 PATH
def _find_chroma_exe() -> pathlib.Path:
    py_dir = pathlib.Path(sys.executable).parent
    candidates = [
        ROOT / ".venv" / "Scripts" / "chroma.exe",
        py_dir / "Scripts" / "chroma.exe",   # Python311/Scripts/chroma.exe
        py_dir / "chroma.exe",               # 直接在 Python 目录下（少见）
    ]
    for c in candidates:
        if c.exists():
            return c
    # 回退到 PATH 中的 chroma
    import shutil
    found = shutil.which("chroma")
    if found:
        return pathlib.Path(found)
    raise FileNotFoundError("找不到 chroma.exe，请确认 chromadb 已正确安装")

CHROMA_EXE = _find_chroma_exe()
HOST = "127.0.0.1"
PORT = 18000
ENDPOINT = f"http://{HOST}:{PORT}"
LOG_FILE = CHROMA_DATA / "chroma.log"

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


def start_bg() -> None:
    """用 Windows DETACHED_PROCESS 方式启动 ChromaDB"""
    import subprocess as _sp
    ver = _sp.run([str(CHROMA_EXE), "--version"], capture_output=True, text=True)
    print(f"[ChromaDB] 使用: {CHROMA_EXE}")
    print(f"[ChromaDB] CLI 版本: {ver.stdout.strip() or ver.stderr.strip()}")
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    log_fd = open(str(LOG_FILE), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(CHROMA_EXE), "run",
         "--host", HOST,
         "--port", str(PORT),
         "--path", str(CHROMA_DATA)],
        stdout=log_fd,
        stderr=log_fd,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    print(f"[ChromaDB] 后台进程已启动，PID={proc.pid}")
    print(f"[ChromaDB] 日志文件: {LOG_FILE}")


def wait_ready(timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_running():
            return True
        time.sleep(0.8)
    return False


def init_collections() -> None:
    import chromadb
    from chromadb.config import Settings

    print(f"\n[ChromaDB] 初始化长期记忆 collection")
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

    print(f"[ChromaDB] 启动后台服务...")
    start_bg()

    print("[ChromaDB] 等待服务就绪...", end="", flush=True)
    ready = wait_ready(timeout=30)
    if not ready:
        print(" 超时！请检查日志:", LOG_FILE)
        # 打印日志内容
        if LOG_FILE.exists():
            print(LOG_FILE.read_text(encoding="utf-8-sig", errors="replace")[:2000])
        sys.exit(1)

    print(f" 就绪！")
    init_collections()

    print(f"\n[ChromaDB] 服务运行中: {ENDPOINT}")
    print(f"[ChromaDB] 日志: {LOG_FILE}")
    print("[ChromaDB] 停止服务: 找到 PID 后 taskkill /F /PID <pid>")


if __name__ == "__main__":
    main()
