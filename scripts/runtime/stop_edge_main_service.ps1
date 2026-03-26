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
$qqOneBotManagerScript = Join-Path $PSScriptRoot "qq_onebot_manager.ps1"
if (-not (Test-Path -Path $qqOneBotManagerScript)) {
  throw "qq_onebot_manager.ps1 not found: $qqOneBotManagerScript"
}
. $qqOneBotManagerScript
$runtimeEdge = Join-Path $repoRoot "runtime\edge"
if (-not $PidFile) {
  $PidFile = Join-Path $runtimeEdge "edge.pid"
}

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
        $detail = "edge main process stopped"
      } else {
        $detail = "edge main process already exited"
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
    $detail = "edge main process stopped by port probe"
  }
}

$qqOneBotStopResult = Stop-QQOneBotIfOwned -RuntimeRoot $runtimeEdge

$result = [ordered]@{
  side = 'edge'
  edge = [ordered]@{
    status = if ($stopped) { 'stopped' } else { 'not_running' }
    pid = $edgePid
    pid_file = $PidFile
    port = $Port
    detail = $detail
    startup_mode = 'main_service_breakaway'
    auxiliary_management = 'qq_onebot_managed'
    qq_onebot = $qqOneBotStopResult
  }
}
$result | ConvertTo-Json -Depth 8
