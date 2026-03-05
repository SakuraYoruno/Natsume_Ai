"""
启动 Neo4j Community Server（使用本地安装的 JDK 17）
用法：python tools/start_neo4j.py
"""
from __future__ import annotations
import subprocess, sys, time, pathlib, json, urllib.request, os

ROOT = pathlib.Path(".").resolve()
INFO_FILE = ROOT / "data" / "neo4j_install_info.json"

NEO4J_BOLT_PORT = 7687
NEO4J_HTTP_PORT = 7474
ENDPOINT = f"http://127.0.0.1:{NEO4J_HTTP_PORT}"


def load_install_info() -> dict:
    if not INFO_FILE.exists():
        print(f"[Neo4j] 找不到安装信息文件: {INFO_FILE}")
        print("[Neo4j] 请先运行: python tools/install_neo4j.py")
        sys.exit(1)
    return json.loads(INFO_FILE.read_text(encoding="utf-8"))


def is_running() -> bool:
    try:
        urllib.request.urlopen(f"{ENDPOINT}", timeout=3)
        return True
    except Exception:
        return False


def is_bolt_running() -> bool:
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", NEO4J_BOLT_PORT), timeout=3)
        s.close()
        return True
    except Exception:
        return False


def start_neo4j(info: dict) -> subprocess.Popen:
    java_home = pathlib.Path(info["java_home"])
    neo4j_bat = pathlib.Path(info["neo4j_bat"])
    neo4j_home = pathlib.Path(info["neo4j_home"])

    env = os.environ.copy()
    env["JAVA_HOME"] = str(java_home)
    env["NEO4J_HOME"] = str(neo4j_home)

    print(f"[Neo4j] JAVA_HOME: {java_home}")
    print(f"[Neo4j] NEO4J_HOME: {neo4j_home}")
    print(f"[Neo4j] 启动命令: {neo4j_bat} console")

    log_file = ROOT / "data" / "neo4j_server.log"
    log_fd = open(str(log_file), "w", encoding="utf-8")

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    proc = subprocess.Popen(
        [str(neo4j_bat), "console"],
        stdout=log_fd,
        stderr=log_fd,
        env=env,
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    print(f"[Neo4j] 后台进程已启动，PID={proc.pid}")
    print(f"[Neo4j] 日志文件: {log_file}")
    return proc


def wait_ready(timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_bolt_running():
            return True
        time.sleep(1.0)
    return False


def main() -> None:
    if is_bolt_running():
        print(f"[Neo4j] 服务已在运行: bolt://127.0.0.1:{NEO4J_BOLT_PORT}")
        return

    info = load_install_info()
    start_neo4j(info)

    print("[Neo4j] 等待服务就绪（最多 60 秒）...", end="", flush=True)
    ready = wait_ready(timeout=60)

    if not ready:
        print(" 超时！")
        log_file = ROOT / "data" / "neo4j_server.log"
        if log_file.exists():
            print("[Neo4j] 最近日志:")
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-20:]:
                print(f"  {line}")
        sys.exit(1)

    print(f" 就绪！")
    print(f"[Neo4j] Bolt: bolt://127.0.0.1:{NEO4J_BOLT_PORT}")
    print(f"[Neo4j] HTTP: http://127.0.0.1:{NEO4J_HTTP_PORT}")
    print(f"[Neo4j] 浏览器: http://127.0.0.1:{NEO4J_HTTP_PORT}/browser/")
    print(f"[Neo4j] 默认账号: neo4j / (无密码，已禁用认证)")


if __name__ == "__main__":
    main()
