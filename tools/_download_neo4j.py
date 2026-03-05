"""重新下载 Neo4j，带超时重试"""
import urllib.request, pathlib, sys, time

ROOT = pathlib.Path(".").resolve()
TOOLS_DIR = ROOT / "data" / "install"
NEO4J_ZIP = TOOLS_DIR / "neo4j.zip"

# Neo4j 5.26 官方下载地址
URLS = [
    "https://dist.neo4j.org/neo4j-community-5.26.7-windows.zip",
    "https://neo4j.com/artifact.php?name=neo4j-community-5.26.7-windows.zip",
]

NEO4J_EXPECTED_MIN_MB = 100  # 完整包至少 100MB

def check_existing():
    if NEO4J_ZIP.exists():
        size_mb = NEO4J_ZIP.stat().st_size / 1024 / 1024
        print(f"现有文件: {NEO4J_ZIP} ({size_mb:.1f} MB)")
        if size_mb >= NEO4J_EXPECTED_MIN_MB:
            print("文件大小正常，跳过下载")
            return True
        else:
            print(f"文件不完整（{size_mb:.1f} MB < {NEO4J_EXPECTED_MIN_MB} MB），删除重新下载")
            NEO4J_ZIP.unlink()
    return False

if check_existing():
    sys.exit(0)

print("开始下载 Neo4j Community 5.26.7...")
for url in URLS:
    print(f"尝试: {url}")
    try:
        last_print = [time.time()]
        downloaded = [0]

        def reporthook(count, block_size, total_size):
            downloaded[0] = count * block_size
            now = time.time()
            if now - last_print[0] >= 5:
                mb = downloaded[0] / 1024 / 1024
                if total_size > 0:
                    total_mb = total_size / 1024 / 1024
                    pct = min(100, downloaded[0] * 100 // total_size)
                    print(f"  {pct}% ({mb:.1f}/{total_mb:.1f} MB)", flush=True)
                else:
                    print(f"  {mb:.1f} MB", flush=True)
                last_print[0] = now

        urllib.request.urlretrieve(url, str(NEO4J_ZIP), reporthook)
        size_mb = NEO4J_ZIP.stat().st_size / 1024 / 1024
        print(f"\n下载完成: {size_mb:.1f} MB")
        if size_mb >= NEO4J_EXPECTED_MIN_MB:
            print("文件大小正常！")
            break
        else:
            print(f"文件不完整，尝试下一个地址...")
            NEO4J_ZIP.unlink()
    except Exception as e:
        print(f"下载失败: {e}")
        if NEO4J_ZIP.exists():
            NEO4J_ZIP.unlink()
        continue
else:
    print("所有下载地址均失败")
    sys.exit(1)
