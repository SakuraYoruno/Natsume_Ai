"""通过进程和内存查找微信数据目录。"""
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import subprocess, os, glob

# 1. 查微信进程路径
print("=== 微信进程信息 ===")
r = subprocess.run(
    ["wmic", "process", "where", "name='Weixin.exe'", "get", "ProcessId,ExecutablePath"],
    capture_output=True, text=True, timeout=10, encoding="gbk", errors="replace"
)
print(r.stdout or "(未找到微信进程)")

# 2. 查微信安装目录（从进程路径推断数据目录）
wx_exe_path = None
for line in r.stdout.splitlines():
    line = line.strip()
    if "Weixin.exe" in line and os.path.exists(line.split()[0] if line.split() else ""):
        wx_exe_path = line.split()[0]
        break

# 3. 搜索 D 盘下的 WeChat Files
print("\n=== 搜索 D 盘 WeChat Files ===")
r2 = subprocess.run(
    ["cmd", "/c", "dir /s /b D:\\ 2>nul | findstr /i /c:\"WeChat Files\""],
    capture_output=True, text=True, timeout=30, encoding="gbk", errors="replace",
    shell=False
)
if r2.stdout.strip():
    print(r2.stdout[:3000])
else:
    print("D 盘未找到")

# 4. 直接检查 C 盘用户目录下的常见位置（不递归）
print("\n=== 检查 C 盘用户目录 ===")
username = os.environ.get("USERNAME", "")
user_root = f"C:/Users/{username}"
print(f"用户目录: {user_root}")
if os.path.exists(user_root):
    for item in os.listdir(user_root):
        full = os.path.join(user_root, item)
        if os.path.isdir(full) and "wechat" in item.lower():
            print(f"  [FOUND] {full}")

    # 检查 Documents
    docs = os.path.join(user_root, "Documents")
    if os.path.exists(docs):
        for item in os.listdir(docs):
            if "wechat" in item.lower() or "weixin" in item.lower() or "tencent" in item.lower():
                print(f"  [FOUND in Documents] {os.path.join(docs, item)}")

    # 检查 AppData
    appdata = os.path.join(user_root, "AppData")
    for sub in ["Roaming", "Local", "LocalLow"]:
        tencent = os.path.join(appdata, sub, "Tencent")
        if os.path.exists(tencent):
            print(f"  [Tencent dir] {tencent}")
            for item in os.listdir(tencent):
                print(f"    - {item}")

# 5. 通过 PowerShell 查找（更快）
print("\n=== PowerShell 搜索 MSG3.db ===")
ps_cmd = "Get-ChildItem -Path C:\\ -Filter MSG3.db -Recurse -ErrorAction SilentlyContinue | Select-Object -First 5 FullName"
r3 = subprocess.run(
    ["powershell", "-NoProfile", "-Command", ps_cmd],
    capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace"
)
if r3.stdout.strip():
    print(r3.stdout[:3000])
else:
    print("PowerShell 未找到 MSG3.db")
    if r3.stderr:
        print("stderr:", r3.stderr[:500])

print("\n完成。")
