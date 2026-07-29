[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$RuntimeRoot = "",
  [string]$PidFile = "",
  [int]$Port = 0,
  [int]$ObsPort = 0,
  [int]$MonitorProxyPort = 0
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
  param([int]$ProbePort)
  $conn = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
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

function Resolve-PythonExe {
  param([string]$RepoRoot)
  $venvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $python) {
    return $null
  }
  return [string]$python.Source
}

function Resolve-AuditDbPath {
  param([hashtable]$RuntimeValues, [hashtable]$EnvValues)
  foreach ($name in @("NATSUME_AUDIT_DB", "HOST_AUDIT_SQLITE_PATH")) {
    $candidate = ""
    if ($RuntimeValues.ContainsKey($name)) {
      $candidate = [string]$RuntimeValues[$name]
    } elseif ($EnvValues.ContainsKey($name)) {
      $candidate = [string]$EnvValues[$name]
    } else {
      $candidate = [string][Environment]::GetEnvironmentVariable($name, "Process")
    }
    if (-not [string]::IsNullOrWhiteSpace($candidate)) {
      return $candidate.Trim()
    }
  }
  return ""
}

function Release-ConfirmedStoppedCatalogAuthority {
  param(
    [string]$RepoRoot,
    [string]$AuditDbPath,
    [int]$ExpectedHostPid
  )
  if ([string]::IsNullOrWhiteSpace($AuditDbPath)) {
    return [ordered]@{ status = "NOT_ATTEMPTED"; reason = "audit_db_path_missing" }
  }
  if (-not (Test-Path -LiteralPath $AuditDbPath)) {
    return [ordered]@{ status = "NOT_ATTEMPTED"; reason = "audit_db_missing" }
  }
  $pythonExe = Resolve-PythonExe -RepoRoot $RepoRoot
  if ([string]::IsNullOrWhiteSpace($pythonExe)) {
    return [ordered]@{ status = "NOT_ATTEMPTED"; reason = "python_not_found" }
  }
  $releaseScript = Join-Path $RepoRoot "scripts\runtime\release_host_catalog_authority.py"
  if (-not (Test-Path -LiteralPath $releaseScript)) {
    return [ordered]@{ status = "NOT_ATTEMPTED"; reason = "release_script_missing" }
  }
  try {
    $raw = & $pythonExe $releaseScript --db-path $AuditDbPath --expected-host-pid $ExpectedHostPid
    $output = ($raw | Out-String).Trim()
    if (-not $output) {
      return [ordered]@{ status = "RELEASE_FAILED"; reason = "empty_release_output" }
    }
    try {
      $result = $output | ConvertFrom-Json
    } catch {
      return [ordered]@{ status = "RELEASE_FAILED"; reason = "invalid_release_output" }
    }
    if ($LASTEXITCODE -ne 0 -or $null -eq $result -or -not $result.status) {
      return [ordered]@{ status = "RELEASE_FAILED"; reason = "release_command_failed" }
    }
    return $result
  } catch {
    return [ordered]@{ status = "RELEASE_FAILED"; reason = "release_command_exception" }
  }
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
$runtimeValues = Read-EnvFile -Path $runtimeEnvFile

function Resolve-RuntimePort {
  param(
    [int]$ExplicitPort,
    [string]$Name,
    [int]$DefaultPort
  )
  if ($ExplicitPort -gt 0) {
    return $ExplicitPort
  }
  if ($runtimeValues.ContainsKey($Name)) {
    return [int]$runtimeValues[$Name]
  }
  if ($envValues.ContainsKey($Name)) {
    return [int]$envValues[$Name]
  }
  $processValue = [Environment]::GetEnvironmentVariable($Name, "Process")
  if ($processValue) {
    return [int]$processValue
  }
  return $DefaultPort
}

if ($Port -le 0) {
  $Port = Resolve-RuntimePort -ExplicitPort 0 -Name "HOST_APP_PORT" -DefaultPort 18080
}
$ObsPort = Resolve-RuntimePort -ExplicitPort $ObsPort -Name "HOST_OBS_APP_PORT" -DefaultPort 18082
$MonitorProxyPort = Resolve-RuntimePort -ExplicitPort $MonitorProxyPort -Name "HOST_MONITOR_PROXY_PORT" -DefaultPort 18180

$stopped = $false
$hostPid = $null
$detail = ""
$catalogAuthority = [ordered]@{ status = "NOT_REQUIRED" }

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
  $listeningPid = Get-ListeningPid -ProbePort $Port
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

# A forced Windows process stop cannot execute the Host's ``finally`` block.
# Before another Host is launched, prove that the recorded Host PID and its
# listener are both absent, then release only that exact durable coordinator
# fence.  A mismatched owner, a still-bound port, or a failed helper remains a
# visible degraded shutdown rather than silently shortening or bypassing the
# Service Catalog lease.
if ($null -ne $hostPid -and [int]$hostPid -gt 0) {
  $hostStillAlive = $null -ne (Get-Process -Id ([int]$hostPid) -ErrorAction SilentlyContinue)
  $listenerAfterStop = Get-ListeningPid -ProbePort $Port
  if (-not $hostStillAlive -and $null -eq $listenerAfterStop) {
    $auditDbPath = Resolve-AuditDbPath -RuntimeValues $runtimeValues -EnvValues $envValues
    $catalogAuthority = Release-ConfirmedStoppedCatalogAuthority `
      -RepoRoot $repoRoot `
      -AuditDbPath $auditDbPath `
      -ExpectedHostPid ([int]$hostPid)
    if ([string]$catalogAuthority.status -notin @("RELEASED", "NOT_ACTIVE")) {
      $detail = if ($detail) {
        "$detail; catalog authority release=$($catalogAuthority.status)"
      } else {
        "catalog authority release=$($catalogAuthority.status)"
      }
    }
  } elseif ($stopped) {
    $catalogAuthority = [ordered]@{
      status = "NOT_RELEASED"
      reason = if ($hostStillAlive) { "host_process_still_alive" } else { "host_listener_still_bound" }
    }
    $detail = if ($detail) {
      "$detail; catalog authority release=$($catalogAuthority.status)"
    } else {
      "catalog authority release=$($catalogAuthority.status)"
    }
  }
}

$obsProcessResult = $null
$stopObsScript = Join-Path $PSScriptRoot "stop_obs_side.ps1"
if (Test-Path -Path $stopObsScript) {
  try {
    $obsRaw = & $stopObsScript `
      -EnvFile $EnvFile `
      -RuntimeRoot $RuntimeRoot `
      -Port $ObsPort
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
    $monitorRaw = & $stopMonitorScript `
      -EnvFile $EnvFile `
      -RuntimeRoot (Join-Path $RuntimeRoot "monitor_proxy") `
      -Port $MonitorProxyPort
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

Remove-Item -Path $runtimeEnvFile -Force -ErrorAction SilentlyContinue

$resultStatus = if (
  $stopped -and [string]$catalogAuthority.status -notin @("RELEASED", "NOT_ACTIVE", "NOT_REQUIRED")
) {
  "degraded"
} elseif ($stopped) {
  "stopped"
} else {
  "not_running"
}

$result = [ordered]@{
  side = "host"
  status = $resultStatus
  pid = $hostPid
  pid_file = $PidFile
  runtime_root = $RuntimeRoot
  port = $Port
  obs_port = $ObsPort
  monitor_proxy_port = $MonitorProxyPort
  detail = $detail
  catalog_authority = $catalogAuthority
  obs_process = $obsProcessResult
  monitor_proxy = $monitorProxyResult
}
$result | ConvertTo-Json -Depth 6
