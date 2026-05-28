[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [string]$RepoRoot = "",
  [string]$BindHost = "0.0.0.0",
  [int]$Port = 17800,
  [int]$WaitSeconds = 120,
  [switch]$ForceRestart,
  [switch]$CleanQQOneBot,
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

function Write-EnvFile {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [hashtable]$Values
  )

  $lines = @()
  foreach ($key in ($Values.Keys | Sort-Object)) {
    if ([string]::IsNullOrWhiteSpace([string]$key)) {
      continue
    }
    $lines += ("{0}={1}" -f [string]$key, [string]$Values[$key])
  }
  Set-Content -Path $Path -Value $lines -Encoding UTF8
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

function Is-EdgeMainProcess {
  param([int]$ProcessId)
  $procInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $procInfo) {
    return $false
  }
  $cmd = [string]$procInfo.CommandLine
  if (-not $cmd) {
    return $false
  }
  return ($cmd -like "*-m edge_client*")
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  if ($ProcessId -le 0) {
    return
  }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 300
  if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
    cmd /c "taskkill /PID $ProcessId /T /F" | Out-Null
  }
}

function Stop-ResidualEdgeMainProcesses {
  $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue
  foreach ($procInfo in @($processes)) {
    $procId = [int]$procInfo.ProcessId
    if ($procId -le 0) {
      continue
    }
    $cmd = [string]$procInfo.CommandLine
    if ($cmd -and $cmd -like "*-m edge_client*") {
      Stop-ProcessTree -ProcessId $procId
    }
  }
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
          Stop-ProcessTree -ProcessId $parsedPid
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
      Stop-ProcessTree -ProcessId $listeningPid
      $stoppedPid = $listeningPid
      $detail = "stopped_by_port_probe"
    }
  }

  Stop-ResidualEdgeMainProcesses

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
  $sshSessionDetected = (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CONNECTION)) -or (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CLIENT))
  if ($sshSessionDetected) {
    try {
      $commandLine = "cmd.exe /c start `"`" /min $TaskCommand"
      $createResult = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $commandLine } -ErrorAction Stop
      if ($null -ne $createResult -and [int]$createResult.ReturnValue -eq 0) {
        return
      }
    } catch {
      # Fall back to schtasks below; older Windows images may restrict WMI create.
    }
  }

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
$qqOneBotManagerScript = Join-Path $PSScriptRoot 'qq_onebot_manager.ps1'
if (-not (Test-Path -Path $qqOneBotManagerScript)) {
  throw "qq_onebot_manager.ps1 not found: $qqOneBotManagerScript"
}
. $qqOneBotManagerScript
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
$cleanNapCatLauncher = Join-Path $RepoRoot 'scripts\runtime\launch_napcat_clean.ps1'
$envValues['QQ_ONEBOT_API_BASE'] = 'http://127.0.0.1:3000'
$envValues['EDGE_QQ_ONEBOT_AUTOSTART'] = 'true'
if ([string]::IsNullOrWhiteSpace([string]$envValues['EDGE_QQ_ONEBOT_LAUNCHER_PATH'])) {
  $envValues['EDGE_QQ_ONEBOT_LAUNCHER_PATH'] = $cleanNapCatLauncher
}
if ([string]::IsNullOrWhiteSpace([string]$envValues['EDGE_QQ_ONEBOT_LAUNCHER_ARGS'])) {
  $envValues['EDGE_QQ_ONEBOT_LAUNCHER_ARGS'] = '{QQ_SELF_ID}'
}
$envValues['EDGE_QQ_ONEBOT_HEALTH_URL'] = 'http://127.0.0.1:3000/get_status'
$qqCleanStartValue = if ($CleanQQOneBot.IsPresent) { 'true' } else { 'false' }
$envValues['EDGE_QQ_ONEBOT_KILL_QQ_BEFORE_START'] = $qqCleanStartValue
$envValues['EDGE_QQ_ONEBOT_FORCE_CLEAN_START'] = $qqCleanStartValue
$effectiveEnvFile = Join-Path $runtimeRoot 'edge.runtime.env'
Write-EnvFile -Path $effectiveEnvFile -Values $envValues
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
$ensureOneBotScript = Join-Path $PSScriptRoot 'ensure_qq_onebot.ps1'
$healthUrl = "http://127.0.0.1:$Port/v1/edge/health"
$qqOneBotStateFile = Join-Path $runtimeRoot 'qq_onebot.state.json'
$qqOneBotApiBase = [string]$envValues['QQ_ONEBOT_API_BASE']
$qqOneBotHealthUrl = Get-QQOneBotHealthUrl -ApiBase $qqOneBotApiBase -Override ([string]$envValues['EDGE_QQ_ONEBOT_HEALTH_URL'])

$runnerContent = @'
[CmdletBinding()]
param(
  [string]$RepoRoot = "",
  [string]$EnvFile = "",
  [string]$PythonPath = "",
  [string]$BindHost = "0.0.0.0",
  [int]$Port = 17800
)

$ErrorActionPreference = 'Stop'

function Resolve-DefaultRepoRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
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

function Import-EnvFile {
  param([string]$Path)

  if (-not (Test-Path $Path)) {
    return
  }

  Get-Content $Path | ForEach-Object {
    if ([string]::IsNullOrWhiteSpace($_)) { return }
    $line = $_.Trim()
    if ($line.StartsWith('#')) { return }
    $ix = $line.IndexOf('=')
    if ($ix -lt 1) { return }
    $k = $line.Substring(0, $ix).Trim()
    $v = $line.Substring($ix + 1).Trim()
    [Environment]::SetEnvironmentVariable($k, $v, 'Process')
  }
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = Resolve-DefaultRepoRoot
}
if ([string]::IsNullOrWhiteSpace($EnvFile)) {
  $EnvFile = Join-Path $RepoRoot '.env'
}
$PythonPath = Resolve-PythonInterpreter -RepoRootPath $RepoRoot -PreferredPath $PythonPath

Import-EnvFile -Path $EnvFile

if (-not [string]::IsNullOrWhiteSpace($BindHost)) {
  $env:EDGE_RPC_HOST = $BindHost
}
if ($Port -gt 0) {
  $env:EDGE_RPC_PORT = [string]$Port
}

Set-Location $RepoRoot

# Python logging writes warnings to stderr during normal startup. In Windows
# PowerShell 5.1, native stderr can be surfaced as NativeCommandError and obey
# ErrorActionPreference, so keep setup strict but treat edge_client stderr as log.
$ErrorActionPreference = 'Continue'
& $PythonPath -m edge_client
exit $LASTEXITCODE
'@
Set-Content -Path $runnerScript -Value $runnerContent -Encoding ascii

if (-not (Test-Path -Path $runnerScript)) {
  throw "run_edge_task.ps1 not found: $runnerScript"
}
if (-not (Test-Path -Path $ensureOneBotScript)) {
  throw "ensure_qq_onebot.ps1 not found: $ensureOneBotScript"
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
      env_file = $effectiveEnvFile
      source_env_file = $EnvFile
      stdout_log = $stdoutLog
      stderr_log = $stderrLog
      startup_mode = 'main_service_breakaway'
      breakaway_launcher_used = $true
      auxiliary_management = 'qq_onebot_delegated'
      qq_onebot_status = if ([string]::IsNullOrWhiteSpace($qqOneBotApiBase)) { 'disabled' } else { 'unknown' }
      qq_onebot_state_file = $qqOneBotStateFile
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
$envEscaped = ([string]$effectiveEnvFile).Replace('"', '""')
$pythonEscaped = ([string]$PythonPath).Replace('"', '""')
$bindEscaped = ([string]$BindHost).Replace('"', '""')
$runnerEscaped = ([string]$runnerScript).Replace('"', '""')
$ensureOneBotEscaped = ([string]$ensureOneBotScript).Replace('"', '""')

$launcherLines = @(
  '@echo off',
  "cd /d `"$repoEscaped`"",
  "start `"`" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ensureOneBotEscaped`" -RepoRoot `"$repoEscaped`" -EnvFile `"$envEscaped`" 1>> `"$stdoutEscaped`" 2>> `"$stderrEscaped`"",
  "powershell -NoProfile -ExecutionPolicy Bypass -File `"$runnerEscaped`" -RepoRoot `"$repoEscaped`" -EnvFile `"$envEscaped`" -PythonPath `"$pythonEscaped`" -BindHost `"$bindEscaped`" -Port $Port 1>> `"$stdoutEscaped`" 2>> `"$stderrEscaped`""
)
Set-Content -Path $launcherScript -Value $launcherLines -Encoding ascii

$taskName = "NatsumeEdgeMainOnly_$([Guid]::NewGuid().ToString('N').Substring(0, 10))"
$sshSessionDetected = (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CONNECTION)) -or (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CLIENT))
$taskCommand = if ($sshSessionDetected) {
  "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runnerEscaped`" -RepoRoot `"$repoEscaped`" -EnvFile `"$envEscaped`" -PythonPath `"$pythonEscaped`" -BindHost `"$bindEscaped`" -Port $Port 1>> `"$stdoutEscaped`" 2>> `"$stderrEscaped`""
} else {
  "`"$launcherScript`""
}
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
$qqOneBotHealth = Test-QQOneBotHealth -ApiBase $qqOneBotApiBase -HealthUrl $qqOneBotHealthUrl -TimeoutSeconds 5
$qqOneBotState = Read-QQOneBotState -StateFile $qqOneBotStateFile
$qqOneBotStatus = if ([string]::IsNullOrWhiteSpace($qqOneBotApiBase)) { 'disabled' } elseif ($qqOneBotHealth.success) { 'healthy' } else { 'degraded' }
$qqOneBotDetail = if ([string]::IsNullOrWhiteSpace($qqOneBotApiBase)) { 'qq onebot disabled because QQ_ONEBOT_API_BASE is empty' } else { [string]$qqOneBotHealth.detail }

$result = [ordered]@{
  side = 'edge'
  edge = [ordered]@{
    status = $startupStatus
    pid = $edgePid
    pid_file = $pidFile
    port = $Port
    env_file = $effectiveEnvFile
    source_env_file = $EnvFile
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
    startup_mode = 'main_service_breakaway'
    breakaway_launcher_used = $true
    auxiliary_management = 'qq_onebot_delegated'
    qq_onebot_status = $qqOneBotStatus
    qq_onebot_detail = $qqOneBotDetail
    qq_onebot_state_file = $qqOneBotStateFile
    qq_onebot_owned = if ($null -ne $qqOneBotState) { [bool]$qqOneBotState.owned } else { $false }
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
