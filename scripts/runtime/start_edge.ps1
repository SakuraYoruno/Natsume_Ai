[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [int]$WaitSeconds = 20,
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

function Set-ProcessEnv {
  param([hashtable]$Values)
  foreach ($key in $Values.Keys) {
    [Environment]::SetEnvironmentVariable($key, [string]$Values[$key], "Process")
  }
}

function Ensure-Directory {
  param([string]$Path)
  if (-not (Test-Path -Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function Get-ListeningPid {
  param([int]$Port)
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Wait-Port {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Get-ListeningPid -Port $Port) {
      return $true
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Parse-Bool {
  param(
    [object]$Value,
    [bool]$Default = $false
  )
  if ($null -eq $Value) {
    return [bool]$Default
  }
  $text = ([string]$Value).Trim().ToLowerInvariant()
  if (-not $text) {
    return [bool]$Default
  }
  if ($text -in @("1", "true", "yes", "on")) {
    return $true
  }
  if ($text -in @("0", "false", "no", "off")) {
    return $false
  }
  return [bool]$Default
}

function Start-VtsViaSteam {
  param(
    [string]$SteamAppId
  )
  if (-not $SteamAppId) {
    return [ordered]@{
      status = "launch_failed"
      detail = "EDGE_VTS_STEAM_APP_ID is empty"
      steam_uri = $null
    }
  }

  $steamUri = "steam://rungameid/$SteamAppId"

  try {
    Start-Process -FilePath $steamUri -ErrorAction Stop | Out-Null
    return [ordered]@{
      status = "launched"
      detail = "launched via steam protocol handler"
      steam_uri = $steamUri
    }
  } catch {
    $firstError = $_.Exception.Message
    try {
      Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "start", "", $steamUri) -WindowStyle Hidden -ErrorAction Stop | Out-Null
      return [ordered]@{
        status = "launched"
        detail = "launched via cmd start fallback"
        steam_uri = $steamUri
      }
    } catch {
      $secondError = $_.Exception.Message
      return [ordered]@{
        status = "launch_failed"
        detail = "steam launch failed: $firstError | fallback failed: $secondError"
        steam_uri = $steamUri
      }
    }
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

$runtimeRoot = Join-Path $repoRoot "runtime\edge"
Ensure-Directory -Path $runtimeRoot

if (-not $LogDir) {
  $LogDir = Join-Path $runtimeRoot "logs"
}
Ensure-Directory -Path $LogDir

$pidFile = Join-Path $runtimeRoot "edge.pid"
$stdoutLog = Join-Path $LogDir "edge.stdout.log"
$stderrLog = Join-Path $LogDir "edge.stderr.log"

$envValues = Read-EnvFile -Path $EnvFile
Set-ProcessEnv -Values $envValues

$port = 17800
if ($envValues.ContainsKey("EDGE_RPC_PORT")) {
  $port = [int]$envValues["EDGE_RPC_PORT"]
} elseif ($env:EDGE_RPC_PORT) {
  $port = [int]$env:EDGE_RPC_PORT
}

$vtsPort = 8001
if ($envValues.ContainsKey("VTS_API_PORT")) {
  $vtsPort = [int]$envValues["VTS_API_PORT"]
} elseif ($env:VTS_API_PORT) {
  $vtsPort = [int]$env:VTS_API_PORT
}

$vtsAutoStart = $false
if ($envValues.ContainsKey("EDGE_VTS_AUTOSTART")) {
  $vtsAutoStart = Parse-Bool -Value $envValues["EDGE_VTS_AUTOSTART"] -Default $false
} elseif ($env:EDGE_VTS_AUTOSTART) {
  $vtsAutoStart = Parse-Bool -Value $env:EDGE_VTS_AUTOSTART -Default $false
}

$vtsSteamAppId = "1325860"  # VTube Studio on Steam
if ($envValues.ContainsKey("EDGE_VTS_STEAM_APP_ID")) {
  $value = [string]$envValues["EDGE_VTS_STEAM_APP_ID"]
  if ($value.Trim()) { $vtsSteamAppId = $value.Trim() }
} elseif ($env:EDGE_VTS_STEAM_APP_ID) {
  $value = [string]$env:EDGE_VTS_STEAM_APP_ID
  if ($value.Trim()) { $vtsSteamAppId = $value.Trim() }
}

$vtsAutoStartWaitSeconds = 35
if ($envValues.ContainsKey("EDGE_VTS_AUTOSTART_WAIT_SECONDS")) {
  $candidate = [int]$envValues["EDGE_VTS_AUTOSTART_WAIT_SECONDS"]
  if ($candidate -gt 0) { $vtsAutoStartWaitSeconds = $candidate }
} elseif ($env:EDGE_VTS_AUTOSTART_WAIT_SECONDS) {
  $candidate = [int]$env:EDGE_VTS_AUTOSTART_WAIT_SECONDS
  if ($candidate -gt 0) { $vtsAutoStartWaitSeconds = $candidate }
}

if (Test-Path -Path $pidFile) {
  $existingPidText = (Get-Content -Path $pidFile -Raw -Encoding ascii).Trim()
  if ($existingPidText) {
    $existingPid = [int]$existingPidText
    $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($null -ne $existingProc -and -not $ForceRestart.IsPresent) {
      $result = [ordered]@{
        status = "already_running"
        pid = $existingPid
        pid_file = $pidFile
        port = $port
        env_file = $EnvFile
      }
      $result | ConvertTo-Json -Depth 4
      exit 0
    }
    if ($null -ne $existingProc -and $ForceRestart.IsPresent) {
      Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
      Start-Sleep -Milliseconds 500
    }
    if ($null -eq $existingProc) {
      Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
    }
  }
}

$listeningPid = Get-ListeningPid -Port $port
if ($null -ne $listeningPid) {
  if (-not $ForceRestart.IsPresent) {
    $result = [ordered]@{
      status = "already_running"
      pid = $listeningPid
      pid_file = $pidFile
      port = $port
      env_file = $EnvFile
    }
    $result | ConvertTo-Json -Depth 4
    exit 0
  }
  Stop-Process -Id $listeningPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCmd) {
  throw "python executable not found in PATH"
}

$proc = Start-Process `
  -FilePath $pythonCmd.Source `
  -ArgumentList @("-m", "edge_client") `
  -WorkingDirectory $repoRoot `
  -PassThru `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog

if (-not (Wait-Port -Port $port -TimeoutSeconds $WaitSeconds)) {
  $stderrTail = ""
  if (Test-Path -Path $stderrLog) {
    $stderrTail = (Get-Content -Path $stderrLog -Tail 60 -ErrorAction SilentlyContinue) -join "`n"
  }
  throw "edge rpc gateway failed to listen on port $port within $WaitSeconds seconds.`n$stderrTail"
}

$edgePid = [int]$proc.Id
Set-Content -Path $pidFile -Value ([string]$edgePid) -Encoding ascii

# ── M8 TASK-0107: Edge 子系统健康探测（ASR / TTS / VTS）────────────────────
$edgeHost = "127.0.0.1"
$asrStatus = "ok"
$ttsStatus = "ok"
$vtsStatus = "ok"
$vtsAutoStartStatus = "skipped"
$vtsAutoStartDetail = "EDGE_VTS_AUTOSTART disabled"
$startupWarnings = @()

# ASR 健康探测（/health/asr 或 /asr/health）
$asrPort = 17801
if ($envValues.ContainsKey("EDGE_ASR_PORT")) { $asrPort = [int]$envValues["EDGE_ASR_PORT"] }
try {
  $asrResp = Invoke-WebRequest -Uri "http://$edgeHost`:$asrPort/health" -UseBasicParsing -TimeoutSec 5
  if ($asrResp.StatusCode -lt 200 -or $asrResp.StatusCode -ge 300) {
    $asrStatus = "degraded"; $startupWarnings += "STARTUP_ASR_DEGRADED"
  }
} catch {
  $asrStatus = "degraded"; $startupWarnings += "STARTUP_ASR_DEGRADED"
}

# TTS 健康探测
$ttsPort = 17802
if ($envValues.ContainsKey("EDGE_TTS_PORT")) { $ttsPort = [int]$envValues["EDGE_TTS_PORT"] }
try {
  $ttsResp = Invoke-WebRequest -Uri "http://$edgeHost`:$ttsPort/health" -UseBasicParsing -TimeoutSec 5
  if ($ttsResp.StatusCode -lt 200 -or $ttsResp.StatusCode -ge 300) {
    $ttsStatus = "degraded"; $startupWarnings += "STARTUP_TTS_DEGRADED"
  }
} catch {
  $ttsStatus = "degraded"; $startupWarnings += "STARTUP_TTS_DEGRADED"
}

# VTS 自启动（可选）：通过 Steam 拉起 VTube Studio
if ($vtsAutoStart) {
  $existingVtsPid = Get-ListeningPid -Port $vtsPort
  if ($existingVtsPid) {
    $vtsAutoStartStatus = "already_running"
    $vtsAutoStartDetail = "vts already listening on port $vtsPort (pid=$existingVtsPid)"
  } else {
    $launch = Start-VtsViaSteam -SteamAppId $vtsSteamAppId
    $vtsAutoStartStatus = [string]$launch.status
    $vtsAutoStartDetail = [string]$launch.detail
    if ($vtsAutoStartStatus -eq "launched") {
      if (Wait-Port -Port $vtsPort -TimeoutSeconds $vtsAutoStartWaitSeconds) {
        $vtsAutoStartStatus = "ready"
        $vtsAutoStartDetail = "vts listens on port $vtsPort after steam launch"
      } else {
        $vtsAutoStartStatus = "launch_timeout"
        $vtsAutoStartDetail = "steam launch requested but port $vtsPort not ready within $vtsAutoStartWaitSeconds seconds"
      }
    }
  }
}

# VTS 健康探测（VTube Studio API 端口）
$vtsConn = Get-NetTCPConnection -LocalPort $vtsPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $vtsConn) {
  $vtsStatus = "degraded"
  if (-not ($startupWarnings -contains "STARTUP_VTS_DEGRADED")) {
    $startupWarnings += "STARTUP_VTS_DEGRADED"
  }
}
# ────────────────────────────────────────────────────────────────────────────

$result = [ordered]@{
  status = "started"
  pid = $edgePid
  pid_file = $pidFile
  port = $port
  env_file = $EnvFile
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
  asr_status = $asrStatus
  tts_status = $ttsStatus
  vts_status = $vtsStatus
  vts_port = $vtsPort
  vts_autostart_enabled = $vtsAutoStart
  vts_autostart_status = $vtsAutoStartStatus
  vts_autostart_detail = $vtsAutoStartDetail
  startup_warnings = $startupWarnings
}
$result | ConvertTo-Json -Depth 4
