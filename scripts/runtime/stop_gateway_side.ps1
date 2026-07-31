[CmdletBinding()]
param(
  [string]$RuntimeRoot = "",
  [int]$Port = 19000,
  [int]$WaitSeconds = 15
)

$ErrorActionPreference = "Stop"

function Test-GatewaySideProcess {
  param([int]$ProcessId)
  $row = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $row) {
    return $false
  }
  return ([string]$row.CommandLine -like "*scripts*runtime*run_gateway_side.py*")
}

function Get-ListeningPid {
  param([int]$ProbePort)
  $connection = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -eq $connection) {
    return $null
  }
  return [int]$connection.OwningProcess
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $RuntimeRoot) {
  $RuntimeRoot = Join-Path $repoRoot "runtime\gateway"
} elseif (-not [System.IO.Path]::IsPathRooted($RuntimeRoot)) {
  $RuntimeRoot = Join-Path $repoRoot $RuntimeRoot
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$pidFile = Join-Path $RuntimeRoot "gateway.pid"
$stopFile = Join-Path $RuntimeRoot "gateway.stop"

$gatewayPid = $null
if (Test-Path -LiteralPath $pidFile) {
  $text = (Get-Content -LiteralPath $pidFile -Raw -Encoding ascii).Trim()
  if ($text) {
    $candidate = [int]$text
    if (Test-GatewaySideProcess -ProcessId $candidate) {
      $gatewayPid = $candidate
    }
  }
}
if ($null -eq $gatewayPid) {
  $candidate = Get-ListeningPid -ProbePort $Port
  if ($null -ne $candidate -and (Test-GatewaySideProcess -ProcessId $candidate)) {
    $gatewayPid = $candidate
  }
}

$detail = "gateway not running"
$graceful = $false
if ($null -ne $gatewayPid) {
  New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
  Set-Content -LiteralPath $stopFile -Value "stop" -Encoding ascii
  $deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $WaitSeconds))
  while ([DateTime]::UtcNow -lt $deadline) {
    if ($null -eq (Get-Process -Id $gatewayPid -ErrorAction SilentlyContinue)) {
      $graceful = $true
      break
    }
    Start-Sleep -Milliseconds 200
  }
  if (-not $graceful) {
    Stop-Process -Id $gatewayPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
  }
  $detail = if ($graceful) {
    "gateway stopped through durable runtime shutdown"
  } else {
    "gateway required forced process stop"
  }
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
[ordered]@{
  side = "gateway"
  status = "stopped"
  pid = $gatewayPid
  graceful = $graceful
  port = $Port
  remote_edge_contacted = $false
  detail = $detail
} | ConvertTo-Json -Depth 4
