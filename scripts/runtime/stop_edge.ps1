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

function Read-TrimmedFileText {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$Encoding = "ascii"
  )

  if (-not (Test-Path -Path $Path)) {
    return ""
  }

  $raw = Get-Content -Path $Path -Raw -Encoding $Encoding -ErrorAction SilentlyContinue
  if ($null -eq $raw) {
    return ""
  }

  return "$raw".Trim()
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeEdge = Join-Path $repoRoot "runtime\edge"
if (-not $PidFile) {
  $PidFile = Join-Path $runtimeEdge "edge.pid"
}

$wechatBridgePidFile = Join-Path $runtimeEdge "wechat_bridge.pid"

$stopped = $false
$edgePid = $null
$detail = ""

if (Test-Path -Path $PidFile) {
  $pidText = Read-TrimmedFileText -Path $PidFile -Encoding ascii
  if ($pidText) {
    $parsedEdgePid = 0
    if ([int]::TryParse($pidText, [ref]$parsedEdgePid)) {
      $edgePid = $parsedEdgePid
      $proc = Get-Process -Id $edgePid -ErrorAction SilentlyContinue
      if ($null -ne $proc) {
        Stop-Process -Id $edgePid -Force -ErrorAction SilentlyContinue
        $stopped = $true
        $detail = "edge process stopped"
      } else {
        $detail = "edge process already exited"
      }
    } else {
      $detail = "pid file is invalid: $pidText"
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

# 若存在微信桥接 PID 文件，一并停止桥接进程
$wechatBridgeStopped = $false
if (Test-Path -Path $wechatBridgePidFile) {
  $bridgePidText = Read-TrimmedFileText -Path $wechatBridgePidFile -Encoding ascii
  if ($bridgePidText) {
    $parsedBridgePid = 0
    if ([int]::TryParse($bridgePidText, [ref]$parsedBridgePid)) {
      $bridgePid = $parsedBridgePid
      $bridgeProc = Get-Process -Id $bridgePid -ErrorAction SilentlyContinue
      if ($null -ne $bridgeProc) {
        Stop-Process -Id $bridgePid -Force -ErrorAction SilentlyContinue
        $wechatBridgeStopped = $true
      }
    }
  }
  Remove-Item -Path $wechatBridgePidFile -Force -ErrorAction SilentlyContinue
}

$result = [ordered]@{
  status = if ($stopped) { "stopped" } else { "not_running" }
  pid = $edgePid
  pid_file = $PidFile
  port = $Port
  detail = $detail
  wechat_bridge_stopped = $wechatBridgeStopped
}
$result | ConvertTo-Json -Depth 4
