param(
    [string]$ProjectPath = "D:\Natsume_Ai\tools\unity_avatar_runtime",
    [string]$ModelPath = "",
    [string]$UnityPath = "C:\Program Files\Unity 2022.3.62f3\Editor\Unity.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $UnityPath)) {
    throw "Unity Editor not found: $UnityPath"
}
if (-not (Test-Path -LiteralPath $ProjectPath)) {
    throw "Unity project not found: $ProjectPath"
}
if ([string]::IsNullOrWhiteSpace($ModelPath)) {
    $ModelPath = Get-ChildItem -LiteralPath "$env:USERPROFILE\Downloads" -Recurse -Filter "*.vrm" -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "*1.05*" -or $_.DirectoryName -like "*1.05*" } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not (Test-Path -LiteralPath $ModelPath)) {
    throw "VRM model not found: $ModelPath"
}

$env:NATSUME_AIMIS_VRM_PATH = $ModelPath
$env:NATSUME_AVATAR_AUTOPLAY = "1"
Start-Process -FilePath $UnityPath -ArgumentList @(
    "-projectPath",
    $ProjectPath,
    "-executeMethod",
    "Natsume.AvatarRuntime.Editor.NatsumeAvatarRuntimeSetup.LaunchForRuntime"
)
