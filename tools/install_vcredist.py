"""
下载并安装 Microsoft Visual C++ 2015-2022 Redistributable (x64)
这是 chromadb、onnxruntime 等包的必要依赖
"""
import urllib.request, subprocess, pathlib, sys, os

VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
DOWNLOAD_PATH = pathlib.Path("data/vc_redist.x64.exe")
DOWNLOAD_PATH.parent.mkdir(parents=True, exist_ok=True)

print("=== 安装 Microsoft Visual C++ 2015-2022 Redistributable (x64) ===")
print(f"下载地址: {VCREDIST_URL}")
print(f"保存路径: {DOWNLOAD_PATH}")
print()

# 检查是否已安装
import winreg
VC_KEYS = [
    r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
    r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
    r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64",
]
already_installed = False
for key_path in VC_KEYS:
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
        installed = winreg.QueryValueEx(key, "Installed")[0]
        version = winreg.QueryValueEx(key, "Version")[0]
        if installed:
            print(f"[已安装] VC++ Redistributable: {version}")
            already_installed = True
            break
    except (FileNotFoundError, OSError):
        continue

if already_installed:
    print("VC++ Redistributable 已安装，无需重新安装")
    sys.exit(0)

print("未检测到 VC++ Redistributable，开始下载...")

def download_with_progress(url, dest):
    def reporthook(count, block_size, total_size):
        if total_size > 0:
            percent = min(100, count * block_size * 100 // total_size)
            print(f"\r  下载进度: {percent}%", end="", flush=True)
    urllib.request.urlretrieve(url, dest, reporthook)
    print()

try:
    download_with_progress(VCREDIST_URL, str(DOWNLOAD_PATH))
    print(f"下载完成: {DOWNLOAD_PATH} ({DOWNLOAD_PATH.stat().st_size // 1024} KB)")
except Exception as e:
    print(f"下载失败: {e}")
    print()
    print("请手动下载并安装：")
    print(f"  {VCREDIST_URL}")
    sys.exit(1)

print()
print("开始安装（需要管理员权限）...")
result = subprocess.run(
    [str(DOWNLOAD_PATH), "/install", "/quiet", "/norestart"],
    capture_output=True, encoding="gbk", errors="replace"
)
print(f"安装退出码: {result.returncode}")
if result.stdout:
    print("stdout:", result.stdout[:500])
if result.stderr:
    print("stderr:", result.stderr[:500])

if result.returncode in (0, 3010):  # 0=成功, 3010=需要重启
    print()
    print("[OK] VC++ Redistributable 安装成功！")
    if result.returncode == 3010:
        print("[注意] 需要重启系统才能完全生效")
else:
    print()
    print(f"[FAIL] 安装失败，退出码: {result.returncode}")
    print("请以管理员身份手动运行:", DOWNLOAD_PATH)
