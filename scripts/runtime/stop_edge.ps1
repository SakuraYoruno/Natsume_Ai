[CmdletBinding()]
param(
  [string]$PidFile = "",
  [int]$Port = 17800
)

$ErrorActionPreference = "Stop"

function Get-ListeningPid {
  param([int]$ProbePort)
  $conn = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $PidFile) {
  $PidFile = Join-Path $repoRoot "runtime\edge\edge.pid"
}

$stopped = $false
$edgePid = $null
$detail = ""

if (Test-Path -Path $PidFile) {
  $pidText = (Get-Content -Path $PidFile -Raw -Encoding ascii).Trim()
  if ($pidText) {
    $edgePid = [int]$pidText
    $proc = Get-Process -Id $edgePid -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
      Stop-Process -Id $edgePid -Force -ErrorAction SilentlyContinue
      $stopped = $true
      $detail = "edge process stopped"
    } else {
      $detail = "edge process already exited"
    }
  } else {
    $detail = "pid file is empty"
  }
  Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
} else {
  $detail = "pid file not found"
}

if (-not $stopped) {
  $listeningPid = Get-ListeningPid -ProbePort $Port
  if ($null -ne $listeningPid) {
    Stop-Process -Id $listeningPid -Force -ErrorAction SilentlyContinue
    $stopped = $true
    $edgePid = $listeningPid
    $detail = "edge process stopped by port probe"
  }
}

$result = [ordered]@{
  status = if ($stopped) { "stopped" } else { "not_running" }
  pid = $edgePid
  pid_file = $PidFile
  port = $Port
  detail = $detail
}
$result | ConvertTo-Json -Depth 4
