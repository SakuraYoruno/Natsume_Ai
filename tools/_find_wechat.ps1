# 查找微信数据目录
$ErrorActionPreference = 'SilentlyContinue'

# 1. 查注册表
Write-Host "=== 注册表 ==="
$regPaths = @(
    "HKCU:\Software\Tencent\WeChat",
    "HKCU:\Software\Tencent\MicroMsg",
    "HKLM:\Software\Tencent\WeChat",
    "HKLM:\Software\WOW6432Node\Tencent\WeChat"
)
foreach ($rp in $regPaths) {
    if (Test-Path $rp) {
        Write-Host "Found: $rp"
        Get-ItemProperty $rp | Format-List
    }
}

# 2. 查微信进程打开的文件（通过 handle.exe 或 SysInternals）
Write-Host "`n=== 微信进程 ID ==="
$procs = Get-Process -Name Weixin
foreach ($proc in $procs) {
    Write-Host "PID=$($proc.Id) Path=$($proc.Path)"
}

# 3. 直接列出 C:\Users\v_wenloliu 的顶层目录
Write-Host "`n=== 用户目录顶层 ==="
Get-ChildItem "C:\Users\v_wenloliu" -Directory | Select-Object Name, FullName

# 4. 检查 Documents 目录
Write-Host "`n=== Documents 目录 ==="
$docs = "C:\Users\v_wenloliu\Documents"
if (Test-Path $docs) {
    Get-ChildItem $docs -Directory | Select-Object Name, FullName
}

# 5. 搜索 WeChat Files（只搜索 2 层深度）
Write-Host "`n=== 搜索 WeChat Files (2层) ==="
$drives = @("C:\", "D:\")
foreach ($drive in $drives) {
    Get-ChildItem $drive -Directory -Depth 3 -Filter "WeChat Files" -ErrorAction SilentlyContinue | Select-Object FullName
}

# 6. 搜索 MicroMsg 目录（wxid 目录）
Write-Host "`n=== 搜索 MicroMsg (3层) ==="
foreach ($drive in $drives) {
    Get-ChildItem $drive -Directory -Depth 4 -Filter "MicroMsg" -ErrorAction SilentlyContinue | Select-Object FullName
}
