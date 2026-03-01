[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Read-PidFile {
  param([string]$Path)
  if (-not (Test-Path -Path $Path)) {
    return $null
  }
  $text = (Get-Content -Path $Path -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $text) {
    return $null
  }
  $pidValue = 0
  if ([int]::TryParse([string]$text, [ref]$pidValue)) {
    return [int]$pidValue
  }
  return $null
}

function Get-ListeningPid {
  param([int]$Port)
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Stop-ProcessById {
  param(
    [int]$ProcessId,
    [string]$Service
  )
  $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($null -eq $proc) {
    return [ordered]@{
      service = $Service
      status = "already_stopped"
      pid = $ProcessId
    }
  }
  $stopped = Stop-ManagedProcessTree -ProcessId $ProcessId
  return [ordered]@{
    service = $Service
    status = $(if ($stopped) { "stopped" } else { "stop_failed" })
    pid = $ProcessId
  }
}

function Stop-PortIfManaged {
  param(
    [int]$Port,
    [string]$Service,
    [string]$CommandPattern
  )
  $ownerProcessId = Get-ListeningPid -Port $Port
  if (-not $ownerProcessId) {
    return [ordered]@{
      service = $Service
      status = "not_listening"
      pid = $null
      port = $Port
    }
  }
  $procInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ownerProcessId" -ErrorAction SilentlyContinue
  $cmd = [string]$procInfo.CommandLine
  if ($cmd -and $cmd -like $CommandPattern) {
    $stopped = Stop-ManagedProcessTree -ProcessId $ownerProcessId
    return [ordered]@{
      service = $Service
      status = $(if ($stopped) { "stopped_by_port_owner" } else { "stop_failed_by_port_owner" })
      pid = $ownerProcessId
      port = $Port
    }
  }
  return [ordered]@{
    service = $Service
    status = "skip_non_managed_owner"
    pid = $ownerProcessId
    port = $Port
  }
}

function Stop-ManagedProcessTree {
  param([int]$ProcessId)
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
  $left = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($left) {
    cmd /c "taskkill /PID $ProcessId /T /F" | Out-Null
    Start-Sleep -Milliseconds 500
    $left = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  }
  return ($null -eq $left)
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRoot = Join-Path $repoRoot "runtime"
$neoRoot = Join-Path $runtimeRoot "neo4j"
$chromaRoot = Join-Path $runtimeRoot "chroma"

$neoPidFile = Join-Path $neoRoot "neo4j.pid"
$chromaPidFile = Join-Path $chromaRoot "chroma.pid"

$results = @()

$chromaPid = Read-PidFile -Path $chromaPidFile
if ($chromaPid) {
  $results += Stop-ProcessById -ProcessId $chromaPid -Service "chroma"
}
$results += Stop-PortIfManaged -Port 8000 -Service "chroma" -CommandPattern "*runtime\chroma*"
if (Test-Path -Path $chromaPidFile) {
  Remove-Item -Path $chromaPidFile -Force -ErrorAction SilentlyContinue
}

$neoPid = Read-PidFile -Path $neoPidFile
if ($neoPid) {
  $results += Stop-ProcessById -ProcessId $neoPid -Service "neo4j"
}
$results += Stop-PortIfManaged -Port 7687 -Service "neo4j" -CommandPattern "*neo4j-community-*"
if (Test-Path -Path $neoPidFile) {
  Remove-Item -Path $neoPidFile -Force -ErrorAction SilentlyContinue
}

$results | ConvertTo-Json -Depth 5
