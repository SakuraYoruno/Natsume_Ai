"""
下载并安装 OpenJDK 17 (Microsoft Build) 和 Neo4j Community Server 5.x
用法：python tools/install_neo4j.py
"""
from __future__ import annotations
import urllib.request, subprocess, pathlib, sys, os, zipfile, json

ROOT = pathlib.Path(".").resolve()
TOOLS_DIR = ROOT / "data" / "install"
TOOLS_DIR.mkdir(parents=True, exist_ok=True)

# ── JDK 17 (Microsoft Build of OpenJDK) ──────────────────────
JDK_URL = "https://aka.ms/download-jdk/microsoft-jdk-17.0.14-windows-x64.zip"
JDK_ZIP = TOOLS_DIR / "jdk17.zip"
JDK_DIR = ROOT / "data" / "jdk17"

# ── Neo4j Community 5.26 ──────────────────────────────────────
NEO4J_URL = "https://dist.neo4j.org/neo4j-community-5.26.7-windows.zip"
NEO4J_ZIP = TOOLS_DIR / "neo4j.zip"
NEO4J_DIR = ROOT / "data" / "neo4j"

NEO4J_HOST = "127.0.0.1"
NEO4J_BOLT_PORT = 7687
NEO4J_HTTP_PORT = 7474


def download(url: str, dest: pathlib.Path, label: str) -> None:
    if dest.exists():
        print(f"  [跳过] {label} 已存在: {dest}")
        return
    print(f"  [下载] {label}")
    print(f"         {url}")

    def reporthook(count, block_size, total_size):
        if total_size > 0:
            mb_done = count * block_size / 1024 / 1024
            mb_total = total_size / 1024 / 1024
            pct = min(100, count * block_size * 100 // total_size)
            print(f"\r         {pct}% ({mb_done:.1f}/{mb_total:.1f} MB)", end="", flush=True)

    urllib.request.urlretrieve(url, str(dest), reporthook)
    print(f"\r         完成 ({dest.stat().st_size // 1024 // 1024} MB)    ")


def extract_zip(zip_path: pathlib.Path, dest_dir: pathlib.Path, label: str) -> pathlib.Path:
    if dest_dir.exists() and any(dest_dir.iterdir()):
        print(f"  [跳过] {label} 已解压: {dest_dir}")
        # 返回第一个子目录
        subdirs = [d for d in dest_dir.iterdir() if d.is_dir()]
        return subdirs[0] if subdirs else dest_dir

    print(f"  [解压] {label} -> {dest_dir}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        zf.extractall(str(dest_dir))

    subdirs = [d for d in dest_dir.iterdir() if d.is_dir()]
    result = subdirs[0] if subdirs else dest_dir
    print(f"         解压完成: {result}")
    return result


def find_java_exe(jdk_root: pathlib.Path) -> pathlib.Path | None:
    candidates = list(jdk_root.rglob("java.exe"))
    for c in candidates:
        if "bin" in c.parts:
            return c
    return candidates[0] if candidates else None


def find_neo4j_bat(neo4j_root: pathlib.Path) -> pathlib.Path | None:
    candidates = list(neo4j_root.rglob("neo4j.bat"))
    for c in candidates:
        if "bin" in c.parts:
            return c
    return candidates[0] if candidates else None


def configure_neo4j(neo4j_home: pathlib.Path, java_home: pathlib.Path) -> None:
    """配置 Neo4j：设置监听地址、禁用认证（dev 模式）"""
    conf_file = neo4j_home / "conf" / "neo4j.conf"
    if not conf_file.exists():
        print(f"  [警告] 找不到配置文件: {conf_file}")
        return

    conf_text = conf_file.read_text(encoding="utf-8", errors="replace")
    changes = {
        # 监听地址
        "#server.default_listen_address=0.0.0.0": "server.default_listen_address=127.0.0.1",
        "server.default_listen_address=0.0.0.0": "server.default_listen_address=127.0.0.1",
        # Bolt 端口
        "#server.bolt.listen_address=:7687": f"server.bolt.listen_address=:{NEO4J_BOLT_PORT}",
        # HTTP 端口
        "#server.http.listen_address=:7474": f"server.http.listen_address=:{NEO4J_HTTP_PORT}",
        # 初始密码（dev 模式禁用认证）
        "#dbms.security.auth_enabled=false": "dbms.security.auth_enabled=false",
        "dbms.security.auth_enabled=true": "dbms.security.auth_enabled=false",
    }
    for old, new in changes.items():
        conf_text = conf_text.replace(old, new)

    conf_file.write_text(conf_text, encoding="utf-8")
    print(f"  [OK] Neo4j 配置已更新: {conf_file}")


def main() -> None:
    print("=" * 60)
    print("  Neo4j 安装向导")
    print("=" * 60)

    # ── Step 1: 下载 JDK ──────────────────────────────────────
    print("\n【1】下载 OpenJDK 17")
    download(JDK_URL, JDK_ZIP, "OpenJDK 17")

    print("\n【2】解压 JDK")
    jdk_root = extract_zip(JDK_ZIP, JDK_DIR, "JDK 17")
    java_exe = find_java_exe(jdk_root)
    if not java_exe:
        print(f"  [ERROR] 找不到 java.exe，请检查: {jdk_root}")
        sys.exit(1)
    java_home = java_exe.parent.parent
    print(f"  JAVA_HOME: {java_home}")
    print(f"  java.exe:  {java_exe}")

    # 验证 Java
    r = subprocess.run([str(java_exe), "-version"], capture_output=True,
                       encoding="utf-8", errors="replace")
    java_ver = (r.stdout + r.stderr).strip().split("\n")[0]
    print(f"  版本: {java_ver}")

    # ── Step 2: 下载 Neo4j ────────────────────────────────────
    print("\n【3】下载 Neo4j Community 5.26")
    download(NEO4J_URL, NEO4J_ZIP, "Neo4j Community 5.26")

    print("\n【4】解压 Neo4j")
    neo4j_root = extract_zip(NEO4J_ZIP, NEO4J_DIR, "Neo4j")
    neo4j_bat = find_neo4j_bat(neo4j_root)
    if not neo4j_bat:
        print(f"  [ERROR] 找不到 neo4j.bat，请检查: {neo4j_root}")
        sys.exit(1)
    neo4j_home = neo4j_bat.parent.parent
    print(f"  NEO4J_HOME: {neo4j_home}")

    # ── Step 3: 配置 Neo4j ────────────────────────────────────
    print("\n【5】配置 Neo4j")
    configure_neo4j(neo4j_home, java_home)

    # ── Step 4: 输出启动命令 ──────────────────────────────────
    print("\n" + "=" * 60)
    print("  安装完成！")
    print("=" * 60)
    print()
    print("启动 Neo4j 服务：")
    print(f"  set JAVA_HOME={java_home}")
    print(f"  {neo4j_bat} console")
    print()
    print("或使用以下脚本启动：")
    print("  python tools/start_neo4j.py")
    print()

    # 保存路径信息供 start_neo4j.py 使用
    info = {
        "java_home": str(java_home),
        "java_exe": str(java_exe),
        "neo4j_home": str(neo4j_home),
        "neo4j_bat": str(neo4j_bat),
        "bolt_port": NEO4J_BOLT_PORT,
        "http_port": NEO4J_HTTP_PORT,
    }
    info_file = ROOT / "data" / "neo4j_install_info.json"
    info_file.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"安装信息已保存: {info_file}")


if __name__ == "__main__":
    main()
