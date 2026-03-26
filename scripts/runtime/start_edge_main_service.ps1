[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [string]$RepoRoot = "",
  [string]$BindHost = "0.0.0.0",
  [int]$Port = 17800,
  [int]$WaitSeconds = 120,
  [switch]$ForceRestart,
  [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

function Ensure-Directory {
  param([string]$Path)
  if (-not [string]::IsNullOrWhiteSpace($Path) -and -not (Test-Path -Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function Read-EnvFile {
  param([string]$Path)
  $values = @{}
  if (-not (Test-Path -Path $Path)) {
    return $values
  }
  foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
    $raw = [string]$line
    $trimmed = $raw.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    $sep = $trimmed.IndexOf("=")
    if ($sep -lt 1) {
      continue
    }
    $key = $trimmed.Substring(0, $sep).Trim()
    $value = $trimmed.Substring($sep + 1).Trim()
    if ($key) {
      $values[$key] = $value
    }
  }
  return $values
}

function Resolve-RepoRoot {
  param([string]$Preferred)
  if (-not [string]::IsNullOrWhiteSpace($Preferred)) {
    return (Resolve-Path $Preferred).Path
  }
  return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function Resolve-PythonInterpreter {
  param(
    [string]$RepoRootPath,
    [string]$PreferredPath
  )
  if (-not [string]::IsNullOrWhiteSpace($PreferredPath)) {
    return $PreferredPath
  }
  $venvPython = Join-Path $RepoRootPath '.venv\Scripts\python.exe'
  if (Test-Path $venvPython) {
    return $venvPython
  }
  $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
  if ($null -ne $pythonCmd) {
    return $pythonCmd.Source
  }
  throw 'python executable not found; set -PythonPath explicitly'
}

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

function Wait-Http {
  param(
    [string]$Url,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
        return $true
      }
    } catch {
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Stop-EdgeMainOnly {
  param(
    [string]$PidFilePath,
    [int]$RpcPort
  )

  $stoppedPid = $null
  $detail = "not_running"

  if (Test-Path -Path $PidFilePath) {
    $pidText = Read-TrimmedFileText -Path $PidFilePath -Encoding ascii
    if ($pidText) {
      $parsedPid = 0
      if ([int]::TryParse($pidText, [ref]$parsedPid)) {
        $proc = Get-Process -Id $parsedPid -ErrorAction SilentlyContinue
        if ($null -ne $proc) {
          Stop-Process -Id $parsedPid -Force -ErrorAction SilentlyContinue
          $stoppedPid = $parsedPid
          $detail = "stopped_by_pid_file"
        }
      }
    }
    Remove-Item -Path $PidFilePath -Force -ErrorAction SilentlyContinue
  }

  if ($null -eq $stoppedPid -and $RpcPort -gt 0) {
    $listeningPid = Get-ListeningPid -ProbePort $RpcPort
    if ($null -ne $listeningPid) {
      Stop-Process -Id $listeningPid -Force -ErrorAction SilentlyContinue
      $stoppedPid = $listeningPid
      $detail = "stopped_by_port_probe"
    }
  }

  return [ordered]@{
    pid = $stoppedPid
    detail = $detail
  }
}

function Invoke-SchtasksBreakaway {
  param(
    [string]$TaskName,
    [string]$TaskCommand
  )

  $schtasksCmd = Get-Command schtasks.exe -ErrorAction SilentlyContinue
  if ($null -eq $schtasksCmd) {
    throw 'schtasks.exe not found; cannot launch breakaway process'
  }

  $targetTime = (Get-Date).AddMinutes(3)
  $taskTime = $targetTime.ToString('HH:mm')
  $taskDate = $targetTime.ToString('yyyy/MM/dd', [System.Globalization.CultureInfo]::InvariantCulture)

  try {
    & $schtasksCmd.Source /Create /TN $TaskName /SC ONCE /SD $taskDate /ST $taskTime /TR $TaskCommand /F /IT 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw "failed to create task $TaskName"
    }
    & $schtasksCmd.Source /Run /TN $TaskName 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw "failed to run task $TaskName"
    }
  } finally {
    try {
      & $schtasksCmd.Source /Delete /TN $TaskName /F 2>&1 | Out-Null
    } catch {
    }
  }
}

$RepoRoot = Resolve-RepoRoot -Preferred $RepoRoot
$runtimeRoot = Join-Path $RepoRoot 'runtime\edge'
Ensure-Directory -Path $runtimeRoot

if (-not $LogDir) {
  $LogDir = Join-Path $runtimeRoot 'logs'
}
Ensure-Directory -Path $LogDir

if (-not $EnvFile) {
  $EnvFile = Join-Path $RepoRoot '.env'
}

$envValues = Read-EnvFile -Path $EnvFile
if ($Port -le 0) {
  $portRaw = [string]$envValues['EDGE_RPC_PORT']
  if ($portRaw) {
    try {
      $Port = [int]$portRaw
    } catch {
      $Port = 17800
    }
  } else {
    $Port = 17800
  }
}
if ([string]::IsNullOrWhiteSpace($BindHost)) {
  $BindHost = [string]$envValues['EDGE_RPC_HOST']
  if ([string]::IsNullOrWhiteSpace($BindHost)) {
    $BindHost = '0.0.0.0'
  }
}

$PythonPath = Resolve-PythonInterpreter -RepoRootPath $RepoRoot -PreferredPath $PythonPath
$pidFile = Join-Path $runtimeRoot 'edge.pid'
$stdoutLog = Join-Path $LogDir 'edge.stdout.log'
$stderrLog = Join-Path $LogDir 'edge.stderr.log'
$runnerScript = Join-Path $runtimeRoot 'run_edge_task.ps1'
$launcherScript = Join-Path $runtimeRoot 'launch_edge_main.cmd'
$healthUrl = "http://127.0.0.1:$Port/v1/edge/health"

if (-not (Test-Path -Path $runnerScript)) {
  throw "run_edge_task.ps1 not found: $runnerScript"
}

$existingPid = if ($Port -gt 0) { Get-ListeningPid -ProbePort $Port } else { $null }
$startupStatus = 'started'
$startupDetail = 'edge main service launched via breakaway scheduled task'
$restartInfo = [ordered]@{ pid = $null; detail = 'not_requested' }

if ($null -ne $existingPid -and -not $ForceRestart.IsPresent) {
  $result = [ordered]@{
    side = 'edge'
    edge = [ordered]@{
      status = 'already_running'
      pid = $existingPid
      pid_file = $pidFile
      port = $Port
      env_file = $EnvFile
      stdout_log = $stdoutLog
      stderr_log = $stderrLog
      startup_mode = 'main_service_breakaway'
      breakaway_launcher_used = $true
      auxiliary_management = 'not_managed'
      detail = "edge main service already listening on port $Port (pid=$existingPid)"
    }
  }
  $result | ConvertTo-Json -Depth 8
  exit 0
}

if ($ForceRestart.IsPresent) {
  $restartInfo = Stop-EdgeMainOnly -PidFilePath $pidFile -RpcPort $Port
  Start-Sleep -Milliseconds 800
}

$stdoutEscaped = ([string]$stdoutLog).Replace('"', '""')
$stderrEscaped = ([string]$stderrLog).Replace('"', '""')
$repoEscaped = ([string]$RepoRoot).Replace('"', '""')
$envEscaped = ([string]$EnvFile).Replace('"', '""')
$pythonEscaped = ([string]$PythonPath).Replace('"', '""')
$bindEscaped = ([string]$BindHost).Replace('"', '""')
$runnerEscaped = ([string]$runnerScript).Replace('"', '""')

$launcherLines = @(
  '@echo off',
  "cd /d `"$repoEscaped`"",
  "powershell -NoProfile -ExecutionPolicy Bypass -File `"$runnerEscaped`" -RepoRoot `"$repoEscaped`" -EnvFile `"$envEscaped`" -PythonPath `"$pythonEscaped`" -BindHost `"$bindEscaped`" -Port $Port 1>> `"$stdoutEscaped`" 2>> `"$stderrEscaped`""
)
Set-Content -Path $launcherScript -Value $launcherLines -Encoding ascii

$taskName = "NatsumeEdgeMainOnly_$([Guid]::NewGuid().ToString('N').Substring(0, 10))"
$taskCommand = "`"$launcherScript`""
Invoke-SchtasksBreakaway -TaskName $taskName -TaskCommand $taskCommand

$ready = Wait-Http -Url $healthUrl -TimeoutSeconds $WaitSeconds
if (-not $ready) {
  $startupStatus = 'launch_timeout'
  $startupDetail = "edge main service did not become healthy within $WaitSeconds seconds"
}

$edgePid = if ($Port -gt 0) { Get-ListeningPid -ProbePort $Port } else { $null }
if ($null -ne $edgePid) {
  Set-Content -Path $pidFile -Value ([string]$edgePid) -Encoding ascii
}

$result = [ordered]@{
  side = 'edge'
  edge = [ordered]@{
    status = $startupStatus
    pid = $edgePid
    pid_file = $pidFile
    port = $Port
    env_file = $EnvFile
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
    startup_mode = 'main_service_breakaway'
    breakaway_launcher_used = $true
    auxiliary_management = 'not_managed'
    force_restart = [bool]$ForceRestart.IsPresent
    restart_previous_pid = $restartInfo.pid
    restart_previous_detail = $restartInfo.detail
    detail = $startupDetail
  }
}

$result | ConvertTo-Json -Depth 8
if ($startupStatus -eq 'launch_timeout') {
  exit 1
}
