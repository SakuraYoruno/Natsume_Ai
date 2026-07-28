[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$RuntimeRoot = "",
  [string]$PidFile = "",
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
if (-not $PidFile) {
  $PidFile = Join-Path $RuntimeRoot "obs.pid"
}

$envValues = Read-EnvFile -Path $EnvFile
if ($Port -le 0) {
  if ($envValues.ContainsKey("HOST_OBS_APP_PORT")) {
    $Port = [int]$envValues["HOST_OBS_APP_PORT"]
  } elseif ($env:HOST_OBS_APP_PORT) {
    $Port = [int]$env:HOST_OBS_APP_PORT
  } else {
    $Port = 18082
  }
}

$stopped = $false
$obsPid = $null
$detail = ""

if (Test-Path -Path $PidFile) {
  $pidText = (Get-Content -Path $PidFile -Raw -Encoding ascii).Trim()
  if ($pidText) {
    $obsPid = [int]$pidText
    if (Is-ObsProcess -ProcessId $obsPid) {
      $ok = Stop-ProcessTree -ProcessId $obsPid
      if ($ok) {
        $stopped = $true
        $detail = "obs process stopped via pid file"
      } else {
        $detail = "failed to stop obs process from pid file"
      }
    } else {
      $detail = "pid file points to non-obs pid $obsPid, fallback to port probe"
    }
  } else {
    $detail = "obs pid file is empty"
  }
  Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

if (-not $stopped) {
  $obsPids = @(Get-ObsProcessIds -ProbePort $Port)
  if ($obsPids.Count -gt 0) {
    $allStopped = $true
    foreach ($candidatePid in $obsPids) {
      $ok = Stop-ProcessTree -ProcessId ([int]$candidatePid)
      if (-not $ok) {
        $allStopped = $false
      }
    }
    if ($allStopped) {
      $stopped = $true
      $obsPid = [int]($obsPids | Select-Object -First 1)
      $detail = "obs process stopped by command-line probe"
    } elseif (-not $detail) {
      $detail = "failed to stop obs process by command-line probe"
    }
  }
}

if (-not $stopped) {
  $listeningPid = Get-ListeningPid -ProbePort $Port
  if ($null -ne $listeningPid) {
    if (Is-ObsProcess -ProcessId $listeningPid) {
      $ok = Stop-ProcessTree -ProcessId $listeningPid
      if ($ok) {
        $stopped = $true
        $obsPid = $listeningPid
        $detail = "obs process stopped by port probe"
      } else {
        $detail = "failed to stop obs process by port probe"
      }
    } else {
      $obsPid = $listeningPid
      $detail = "port $Port is occupied by non-obs pid $listeningPid"
    }
  } elseif (-not $detail) {
    $detail = "obs not running"
  }
}

$result = [ordered]@{
  side = "obs"
  status = if ($stopped) { "stopped" } else { "not_running" }
  pid = $obsPid
  pid_file = $PidFile
  runtime_root = $RuntimeRoot
  port = $Port
  detail = $detail
}
$result | ConvertTo-Json -Depth 6
