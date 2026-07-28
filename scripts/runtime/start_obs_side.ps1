[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$RuntimeRoot = "",
  [string]$LogDir = "",
  [string]$BindHost = "",
  [int]$Port = 0,
  [int]$WaitSeconds = 40,
  [switch]$ForceRestart
)

$ErrorActionPreference = "Stop"

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

function Ensure-Directory {
  param([string]$Path)
  if (-not (Test-Path -Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function ConvertTo-CmdArgument {
  param([AllowNull()][string]$Value)
  if ($null -eq $Value) {
    return '""'
  }
  $text = [string]$Value
  if (-not $text) {
    return '""'
  }
  if ($text -notmatch '[\s"]') {
    return $text
  }
  return '"' + ($text -replace '"', '\"') + '"'
}

function Join-CmdArguments {
  param([string[]]$Arguments)
  $parts = @()
  foreach ($arg in @($Arguments)) {
    $parts += (ConvertTo-CmdArgument -Value ([string]$arg))
  }
  return ($parts -join ' ')
}

function Get-ListeningPid {
  param([int]$ProbePort)
  $conn = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Get-ObsProcessIds {
  param([int]$ProbePort)
  $pattern = "(^|\s)--port\s+$ProbePort(\s|$)"
  $pids = @()
  foreach ($procInfo in @(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue)) {
    $cmd = [string]$procInfo.CommandLine
    if (-not $cmd) {
      continue
    }
    if ($cmd -notlike "*-m host_brain.obs_main*") {
      continue
    }
    if ($cmd -notmatch $pattern) {
      continue
    }
    $pids += [int]$procInfo.ProcessId
  }
  return ($pids | Sort-Object -Unique)
}

function Is-ObsProcess {
  param([int]$ProcessId)
  $procInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $procInfo) {
    return $false
  }
  $cmd = [string]$procInfo.CommandLine
  if (-not $cmd) {
    return $false
  }
  if ($cmd -like "*-m host_brain.obs_main*") {
    return $true
  }
  if ($cmd -like "*host_brain.app.obs_fastapi_app*") {
    return $true
  }
  return $false
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  if ($ProcessId -le 0) {
    return $false
  }
  $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($null -eq $proc) {
    return $true
  }
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

function Test-PythonRuntime {
  param([string]$PythonPath)
  if ([string]::IsNullOrWhiteSpace($PythonPath) -or -not (Test-Path $PythonPath)) {
    return $false
  }
  try {
    $null = & $PythonPath -c "import fastapi, uvicorn, pydantic" 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $EnvFile) {
  $defaultEnv = Join-Path $repoRoot ".env"
  $fallbackEnv = Join-Path $repoRoot ".env.example"
  if (Test-Path -Path $defaultEnv) {
    $EnvFile = $defaultEnv
  } else {
    $EnvFile = $fallbackEnv
  }
}

if (-not $RuntimeRoot) {
  $RuntimeRoot = Join-Path $repoRoot "runtime\host"
} elseif (-not [System.IO.Path]::IsPathRooted($RuntimeRoot)) {
  $RuntimeRoot = Join-Path $repoRoot $RuntimeRoot
}
Ensure-Directory -Path $RuntimeRoot
$RuntimeRoot = (Resolve-Path -Path $RuntimeRoot).Path

if (-not $LogDir) {
  $LogDir = Join-Path $RuntimeRoot "logs"
}
Ensure-Directory -Path $LogDir

$pidFile = Join-Path $RuntimeRoot "obs.pid"
$stdoutLog = Join-Path $LogDir "obs.stdout.log"
$stderrLog = Join-Path $LogDir "obs.stderr.log"
$runtimeEnvFile = Join-Path $RuntimeRoot "host.runtime.env"

$envValues = Read-EnvFile -Path $EnvFile

if (-not $BindHost) {
  if ($envValues.ContainsKey("HOST_OBS_APP_HOST")) {
    $BindHost = [string]$envValues["HOST_OBS_APP_HOST"]
  } elseif ($env:HOST_OBS_APP_HOST) {
    $BindHost = [string]$env:HOST_OBS_APP_HOST
  } else {
    $BindHost = "127.0.0.1"
  }
}

if ($Port -le 0) {
  if ($envValues.ContainsKey("HOST_OBS_APP_PORT")) {
    $Port = [int]$envValues["HOST_OBS_APP_PORT"]
  } elseif ($env:HOST_OBS_APP_PORT) {
    $Port = [int]$env:HOST_OBS_APP_PORT
  } else {
    $Port = 18082
  }
}

$probeHost = if ($BindHost -eq "0.0.0.0") { "127.0.0.1" } else { $BindHost }

if (Test-Path -Path $pidFile) {
  $existingPidText = (Get-Content -Path $pidFile -Raw -Encoding ascii).Trim()
  if ($existingPidText) {
    $existingPid = [int]$existingPidText
    $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    $existingObsProc = $false
    if ($null -ne $existingProc) {
      $existingObsProc = Is-ObsProcess -ProcessId $existingPid
    }
    if ($null -ne $existingProc -and $existingObsProc -and -not $ForceRestart.IsPresent) {
      $result = [ordered]@{
        side = "obs"
        status = "already_running"
        pid = $existingPid
        pid_file = $pidFile
        host = $BindHost
        port = $Port
        health_url = "http://$probeHost`:$Port/health"
        env_file = $EnvFile
        runtime_env_file = $runtimeEnvFile
      }
      $result | ConvertTo-Json -Depth 8
      return
    }
    if ($null -ne $existingProc -and $existingObsProc -and $ForceRestart.IsPresent) {
      Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
      Start-Sleep -Milliseconds 500
    }
    if ($null -eq $existingProc -or -not $existingObsProc) {
      Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
    }
  }
}

$staleObsPids = @(Get-ObsProcessIds -ProbePort $Port)
if ($staleObsPids.Count -gt 0) {
  if (-not $ForceRestart.IsPresent) {
    $existingAlivePid = $staleObsPids | Select-Object -First 1
    $result = [ordered]@{
      side = "obs"
      status = "already_running"
      pid = [int]$existingAlivePid
      pid_file = $pidFile
      host = $BindHost
      port = $Port
      health_url = "http://$probeHost`:$Port/health"
      env_file = $EnvFile
      runtime_env_file = $runtimeEnvFile
    }
    $result | ConvertTo-Json -Depth 8
    return
  }
  foreach ($stalePid in $staleObsPids) {
    Stop-ProcessTree -ProcessId ([int]$stalePid) | Out-Null
  }
  Start-Sleep -Milliseconds 750
}

$listeningPid = Get-ListeningPid -ProbePort $Port
if ($null -ne $listeningPid) {
  if (-not $ForceRestart.IsPresent) {
    if (Is-ObsProcess -ProcessId $listeningPid) {
      $result = [ordered]@{
        side = "obs"
        status = "already_running"
        pid = $listeningPid
        pid_file = $pidFile
        host = $BindHost
        port = $Port
        health_url = "http://$probeHost`:$Port/health"
        env_file = $EnvFile
        runtime_env_file = $runtimeEnvFile
      }
      $result | ConvertTo-Json -Depth 8
      return
    }
    throw "port $Port is already in use by pid $listeningPid (non-obs process)"
  }
  Stop-Process -Id $listeningPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

$pythonExe = $null
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if ((Test-Path $venvPython) -and (Test-PythonRuntime -PythonPath $venvPython)) {
  $pythonExe = $venvPython
} else {
  $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $pythonCmd) {
    throw "python executable not found in PATH"
  }
  $pythonExe = $pythonCmd.Source
}

$obsArgs = @("-m", "host_brain.obs_main", "--host", $BindHost, "--port", ([string]$Port), "--log-level", "INFO")
$stdoutPrev = "$stdoutLog.prev"
$stderrPrev = "$stderrLog.prev"
if (Test-Path -Path $stdoutPrev) {
  Remove-Item -Path $stdoutPrev -Force -ErrorAction SilentlyContinue
}
if (Test-Path -Path $stderrPrev) {
  Remove-Item -Path $stderrPrev -Force -ErrorAction SilentlyContinue
}
if (Test-Path -Path $stdoutLog) {
  Move-Item -Path $stdoutLog -Destination $stdoutPrev -Force
}
if (Test-Path -Path $stderrLog) {
  Move-Item -Path $stderrLog -Destination $stderrPrev -Force
}

$previousRuntimeEnv = [Environment]::GetEnvironmentVariable("HOST_RUNTIME_ENV_FILE", "Process")
[Environment]::SetEnvironmentVariable("HOST_RUNTIME_ENV_FILE", $runtimeEnvFile, "Process")
try {
  $startProcessArgs = @{
    FilePath = $pythonExe
    ArgumentList = $obsArgs
    WorkingDirectory = $repoRoot
    WindowStyle = "Hidden"
    RedirectStandardOutput = $stdoutLog
    RedirectStandardError = $stderrLog
    PassThru = $true
  }
  $proc = Start-Process @startProcessArgs
} finally {
  [Environment]::SetEnvironmentVariable("HOST_RUNTIME_ENV_FILE", $previousRuntimeEnv, "Process")
}

for ($i = 0; $i -lt ($WaitSeconds * 2); $i++) {
  $currentProc = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
  if ($null -eq $currentProc) {
    break
  }
  $candidatePid = Get-ListeningPid -ProbePort $Port
  if ($null -ne $candidatePid -and [int]$candidatePid -gt 0) {
    break
  }
  Start-Sleep -Milliseconds 500
}

$healthUrl = "http://$probeHost`:$Port/health"
$healthOk = Wait-Http -Url $healthUrl -TimeoutSeconds $WaitSeconds
if (-not $healthOk) {
  $metricsUrl = "http://$probeHost`:$Port/obs/metrics"
  $healthOk = Wait-Http -Url $metricsUrl -TimeoutSeconds 8
}
if (-not $healthOk) {
  $runObsPids = @(Get-ObsProcessIds -ProbePort $Port)
  foreach ($runPid in $runObsPids) {
    Stop-ProcessTree -ProcessId ([int]$runPid) | Out-Null
  }
  Stop-ProcessTree -ProcessId $proc.Id | Out-Null
  $stderrTail = ""
  if (Test-Path -Path $stderrLog) {
    $stderrTail = (Get-Content -Path $stderrLog -Tail 80 -ErrorAction SilentlyContinue) -join "`n"
  }
  throw "obs service failed health check at $healthUrl within $WaitSeconds seconds.`n$stderrTail"
}

$obsPid = [int]$proc.Id
$listeningPid = $null
for ($i = 0; $i -lt 20; $i++) {
  $candidatePid = Get-ListeningPid -ProbePort $Port
  if ($null -ne $candidatePid -and [int]$candidatePid -gt 0) {
    $listeningPid = [int]$candidatePid
    break
  }
  Start-Sleep -Milliseconds 250
}
if ($null -ne $listeningPid) {
  $obsPid = [int]$listeningPid
}
Set-Content -Path $pidFile -Value ([string]$obsPid) -Encoding ascii

$result = [ordered]@{
  side = "obs"
  status = "started"
  pid = $obsPid
  pid_file = $pidFile
  host = $BindHost
  port = $Port
  health_url = $healthUrl
  env_file = $EnvFile
  runtime_root = $RuntimeRoot
  runtime_env_file = $runtimeEnvFile
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
}
$json = $result | ConvertTo-Json -Depth 8
Write-Output $json
return
