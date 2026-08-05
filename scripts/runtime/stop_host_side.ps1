[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$RuntimeRoot = "",
  [string]$PidFile = "",
  [string]$BindHost = "",
  [int]$Port = 0
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

function Get-ListeningPid {
  param(
    [int]$ProbePort,
    [string]$ProbeHost = ""
  )
  $connections = @(
    Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue
  )
  if ($connections.Count -eq 0) {
    return $null
  }
  if (-not [string]::IsNullOrWhiteSpace($ProbeHost)) {
    $normalizedHost = $ProbeHost.Trim().TrimStart("[").TrimEnd("]").ToLowerInvariant()
    if ($normalizedHost -eq "localhost") {
      $normalizedHost = "127.0.0.1"
    }
    $conn = $connections |
      Where-Object { ([string]$_.LocalAddress).ToLowerInvariant() -eq $normalizedHost } |
      Select-Object -First 1
    if ($null -eq $conn -and $normalizedHost -notin @("0.0.0.0", "::")) {
      $conn = $connections |
        Where-Object { ([string]$_.LocalAddress).ToLowerInvariant() -in @("0.0.0.0", "::") } |
        Select-Object -First 1
    }
    if ($null -eq $conn) {
      return $null
    }
    return [int]$conn.OwningProcess
  }
  $conn = $connections | Select-Object -First 1
  return [int]$conn.OwningProcess
}

function Is-HostProcess {
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
    return $false
  }
  if ($cmd -like "*-m host_brain --host*") {
    return $true
  }
  if ($cmd -like "*host_brain.app.asgi*") {
    return $true
  }
  return $false
}

function Get-HostProcessIds {
  $result = @()
  $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue
  foreach ($procInfo in @($processes)) {
    $procId = [int]$procInfo.ProcessId
    if ($procId -le 0) {
      continue
    }
    if (Is-HostProcess -ProcessId $procId) {
      $result += $procId
    }
  }
  return @($result | Sort-Object -Unique)
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

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRootWasExplicit = -not [string]::IsNullOrWhiteSpace($RuntimeRoot)
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
if (-not $PidFile) {
  $PidFile = Join-Path $RuntimeRoot "host.pid"
}
$runtimeEnvFile = Join-Path $RuntimeRoot "host.runtime.env"

$envValues = Read-EnvFile -Path $EnvFile
if (-not $BindHost) {
  if ($envValues.ContainsKey("HOST_APP_HOST")) {
    $BindHost = [string]$envValues["HOST_APP_HOST"]
  } elseif ($env:HOST_APP_HOST) {
    $BindHost = [string]$env:HOST_APP_HOST
  } else {
    $BindHost = "127.0.0.1"
  }
}
if ($Port -le 0) {
  if ($envValues.ContainsKey("HOST_APP_PORT")) {
    $Port = [int]$envValues["HOST_APP_PORT"]
  } elseif ($env:HOST_APP_PORT) {
    $Port = [int]$env:HOST_APP_PORT
  } else {
    $Port = 18080
  }
}

$stopped = $false
$hostPid = $null
$detail = ""

if (Test-Path -Path $PidFile) {
  $pidText = (Get-Content -Path $PidFile -Raw -Encoding ascii).Trim()
  if ($pidText) {
    $hostPid = [int]$pidText
    if (Is-HostProcess -ProcessId $hostPid) {
      $ok = Stop-ProcessTree -ProcessId $hostPid
      if ($ok) {
        $stopped = $true
        $detail = "host process stopped via pid file"
      } else {
        $detail = "failed to stop host process from pid file"
      }
    } else {
      $detail = "pid file points to non-host pid $hostPid, fallback to port probe"
    }
  } else {
    $detail = "host pid file is empty"
  }
  Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

if (-not $stopped) {
  $listeningPid = Get-ListeningPid -ProbePort $Port -ProbeHost $BindHost
  if ($null -ne $listeningPid) {
    if (Is-HostProcess -ProcessId $listeningPid) {
      $ok = Stop-ProcessTree -ProcessId $listeningPid
      if ($ok) {
        $stopped = $true
        $hostPid = $listeningPid
        $detail = "host process stopped by port probe"
      } else {
        $detail = "failed to stop host process by port probe"
      }
    } else {
      $hostPid = $listeningPid
      $detail = "port $Port is occupied by non-host pid $listeningPid"
    }
  } elseif (-not $detail) {
    $detail = "host not running"
  }
}

$remainingHostPids = @()
if (-not $runtimeRootWasExplicit) {
  foreach ($candidatePid in @(Get-HostProcessIds)) {
    if ($hostPid -ne $null -and [int]$candidatePid -eq [int]$hostPid) {
      continue
    }
    $ok = Stop-ProcessTree -ProcessId ([int]$candidatePid)
    if ($ok) {
      $stopped = $true
      $remainingHostPids += [int]$candidatePid
    }
  }
}
if ($remainingHostPids.Count -gt 0) {
  $detail = ("stopped residual host processes: {0}" -f (($remainingHostPids | Sort-Object -Unique) -join ","))
}

Remove-Item -Path $runtimeEnvFile -Force -ErrorAction SilentlyContinue

$obsProcessResult = $null
$stopObsScript = Join-Path $PSScriptRoot "stop_obs_side.ps1"
if (Test-Path -Path $stopObsScript) {
  try {
    $obsRaw = & $stopObsScript -EnvFile $EnvFile -RuntimeRoot $RuntimeRoot
    $obsJson = ($obsRaw | Out-String).Trim()
    if ($obsJson) {
      try {
        $obsProcessResult = $obsJson | ConvertFrom-Json
      } catch {
        $obsProcessResult = [ordered]@{
          side = "obs"
          status = "degraded"
          reason = "invalid_json_output"
          raw = $obsJson
        }
      }
    }
  } catch {
    $obsProcessResult = [ordered]@{
      side = "obs"
      status = "degraded"
      reason = "stop_failed"
      detail = $_.Exception.Message
    }
  }
}

$monitorProxyResult = $null
$stopMonitorScript = Join-Path $PSScriptRoot "stop_monitor_proxy.ps1"
if (Test-Path -Path $stopMonitorScript) {
  try {
    $monitorRaw = & $stopMonitorScript -EnvFile $EnvFile -RuntimeRoot (Join-Path $RuntimeRoot "monitor_proxy")
    $monitorJson = ($monitorRaw | Out-String).Trim()
    if ($monitorJson) {
      try {
        $monitorProxyResult = $monitorJson | ConvertFrom-Json
      } catch {
        $monitorProxyResult = [ordered]@{
          side = "monitor_proxy"
          status = "degraded"
          reason = "invalid_json_output"
          raw = $monitorJson
        }
      }
    }
  } catch {
    $monitorProxyResult = [ordered]@{
      side = "monitor_proxy"
      status = "degraded"
      reason = "stop_failed"
      detail = $_.Exception.Message
    }
  }
}

$result = [ordered]@{
  side = "host"
  status = if ($stopped) { "stopped" } else { "not_running" }
  pid = $hostPid
  pid_file = $PidFile
  runtime_root = $RuntimeRoot
  port = $Port
  detail = $detail
  obs_process = $obsProcessResult
  monitor_proxy = $monitorProxyResult
}
$result | ConvertTo-Json -Depth 6
