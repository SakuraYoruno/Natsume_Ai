param(
    [string]$ProjectPath = "D:\Natsume_Ai\tools\unity_avatar_runtime",
    [string]$UnityPath = "C:\Program Files\Unity 2022.3.62f3\Editor\Unity.exe",
    [string]$LogPath = "D:\Natsume_Ai\artifacts\unity_avatar_runtime\build.log"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $UnityPath)) {
    throw "Unity Editor not found: $UnityPath"
}
if (-not (Test-Path -LiteralPath $ProjectPath)) {
    throw "Unity project not found: $ProjectPath"
}

$logDir = Split-Path -Parent $LogPath
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$playerPath = "D:\Natsume_Ai\artifacts\unity_avatar_runtime\NatsumeAvatarRuntime.exe"
$arguments = @(
    "-batchmode",
    "-quit",
    "-projectPath", $ProjectPath,
    "-executeMethod", "Natsume.AvatarRuntime.Editor.NatsumeAvatarRuntimeSetup.BuildWindowsPlayer",
    "-logFile", $LogPath
)
$process = Start-Process -FilePath $UnityPath -ArgumentList $arguments -PassThru -Wait

if ($process.ExitCode -ne 0) {
    $logText = if (Test-Path -LiteralPath $LogPath) { Get-Content -Raw -LiteralPath $LogPath } else { "" }
    $buildSucceeded = $logText -match "Build Finished, Result: Success" -and (Test-Path -LiteralPath $playerPath)
    if (-not $buildSucceeded) {
        if (Test-Path -LiteralPath $LogPath) {
            Get-Content -Tail 120 -LiteralPath $LogPath
        }
        throw "Unity avatar runtime build failed with exit code $($process.ExitCode)"
    }
    Write-Warning "Unity returned exit code $($process.ExitCode), but the build log and player artifact indicate success."
}

if (-not (Test-Path -LiteralPath $playerPath)) {
    Get-Content -Tail 120 -LiteralPath $LogPath
    throw "Unity avatar runtime player was not created: $playerPath"
}

Write-Host "Unity avatar runtime player built: $playerPath"
