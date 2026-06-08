param(
    [string]$PlayerPath = "D:\Natsume_Ai\artifacts\unity_avatar_runtime\NatsumeAvatarRuntime.exe",
    [string]$ModelPath = "",
    [int]$Width = 1600,
    [int]$Height = 900
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ModelPath)) {
    $ModelPath = Get-ChildItem -LiteralPath "$env:USERPROFILE\Downloads" -Recurse -Filter "*.vrm" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*1.05*" -or $_.DirectoryName -like "*1.05*" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}

if (-not (Test-Path -LiteralPath $PlayerPath)) {
    throw "Unity avatar runtime player not found: $PlayerPath"
}
if (-not (Test-Path -LiteralPath $ModelPath)) {
    throw "VRM model not found: $ModelPath"
}

$existing = Get-Process NatsumeAvatarRuntime -ErrorAction SilentlyContinue
if ($existing) {
    $existing | Stop-Process -Force
}

$env:NATSUME_AIMIS_VRM_PATH = $ModelPath
Start-Process -FilePath $PlayerPath -ArgumentList @(
    "-screen-width", $Width,
    "-screen-height", $Height,
    "-screen-fullscreen", "0"
)

Write-Host "Unity avatar runtime player started: $PlayerPath"
Write-Host "VRM: $ModelPath"
