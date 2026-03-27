[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [string]$BindHost = "",
  [int]$Port = 0,
  [ValidateSet("docker")]
  [string]$ExternalServicesBackend = "docker",
  [string]$DockerComposeFile = "",
  [string]$DockerProjectName = "natsume-ext",
  [int]$WaitSeconds = 40,
  [int]$Neo4jWaitSeconds = 90,
  [int]$ChromaWaitSeconds = 30,
  [switch]$ForceRestart,
  [switch]$StartupCheck,
  [switch]$ShowConsoleWindow
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
  if ($cmd -like "*-m host_brain*") {
    return $true
  }
  if ($cmd -like "*host_brain.app.fastapi_app*") {
    return $true
  }
  return $false
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

function Parse-BoolFromText {
  param(
    [string]$Raw,
    [bool]$Default = $false
  )
  if ([string]::IsNullOrWhiteSpace($Raw)) {
    return $Default
  }
  $normalized = $Raw.Trim().ToLowerInvariant()
  return @("1", "true", "yes", "on") -contains $normalized
}

function Parse-IntFromText {
  param(
    [string]$Raw,
    [int]$Default,
    [int]$Min = 0,
    [int]$Max = 2147483647
  )
  try {
    $value = [int]$Raw
  } catch {
    $value = $Default
  }
  if ($value -lt $Min) {
    $value = $Min
  }
  if ($value -gt $Max) {
    $value = $Max
  }
  return $value
}

function Invoke-EdgeWarmup {
  param(
    [string]$PythonExe,
    [int]$TimeoutMs = 20000,
    [int]$Retries = 0,
    [string]$WarmupText = "startup warmup"
  )

  $pythonScript = @'
import asyncio
import json
import os

from host_brain.channels.edge_rpc_client import post_speak, post_vts

async def main():
    vts = await post_vts(action="talk_start", request_id="req_startup_warmup_vts")
    tts = await post_speak(
        text=os.environ.get("HOST_EDGE_WARMUP_TEXT", "startup warmup"),
        request_id="req_startup_warmup_tts",
        session_id="sess::startup_warmup",
        utterance_id="utt::startup_warmup",
        emotion_tag="NEUTRAL",
    )
    print(json.dumps({"vts": vts.as_dict(), "tts": tts.as_dict(), "ok": bool(tts.success)}, ensure_ascii=False))
    return 0 if bool(tts.success) else 2

raise SystemExit(asyncio.run(main()))
'@

  $oldTimeout = $env:HOST_EDGE_RPC_TIMEOUT_MS
  $oldRetries = $env:HOST_EDGE_RPC_RETRIES
  $oldText = $env:HOST_EDGE_WARMUP_TEXT

  try {
    $env:HOST_EDGE_RPC_TIMEOUT_MS = [string]$TimeoutMs
    $env:HOST_EDGE_RPC_RETRIES = [string]$Retries
    $env:HOST_EDGE_WARMUP_TEXT = [string]$WarmupText

    $output = $pythonScript | & $PythonExe - 2>&1
    $exitCode = [int]$LASTEXITCODE
    return [ordered]@{
      exit_code = $exitCode
      output = (($output | ForEach-Object { [string]$_ }) -join "`n")
    }
  } finally {
    if ($null -eq $oldTimeout) {
      Remove-Item Env:HOST_EDGE_RPC_TIMEOUT_MS -ErrorAction SilentlyContinue
    } else {
      $env:HOST_EDGE_RPC_TIMEOUT_MS = [string]$oldTimeout
    }
    if ($null -eq $oldRetries) {
      Remove-Item Env:HOST_EDGE_RPC_RETRIES -ErrorAction SilentlyContinue
    } else {
      $env:HOST_EDGE_RPC_RETRIES = [string]$oldRetries
    }
    if ($null -eq $oldText) {
      Remove-Item Env:HOST_EDGE_WARMUP_TEXT -ErrorAction SilentlyContinue
    } else {
      $env:HOST_EDGE_WARMUP_TEXT = [string]$oldText
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

$runtimeRoot = Join-Path $repoRoot "runtime\host"
Ensure-Directory -Path $runtimeRoot

if (-not $LogDir) {
  $LogDir = Join-Path $runtimeRoot "logs"
}
Ensure-Directory -Path $LogDir

$pidFile = Join-Path $runtimeRoot "host.pid"
$stdoutLog = Join-Path $LogDir "host.stdout.log"
$stderrLog = Join-Path $LogDir "host.stderr.log"

$envValues = Read-EnvFile -Path $EnvFile
Set-ProcessEnv -Values $envValues

if (-not $envValues.ContainsKey("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK") -and -not $env:HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK) {
  $env:HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK = "false"
}
if (-not $envValues.ContainsKey("HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT") -and -not $env:HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT) {
  $env:HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT = "true"
}

if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS") -and -not $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS) {
  $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS") -and -not $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS) {
  $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_AUTO_REPLY") -and -not $env:HOST_INPUT_WECHAT_AUTO_REPLY) {
  $env:HOST_INPUT_WECHAT_AUTO_REPLY = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY") -and -not $env:HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY) {
  $env:HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT") -and -not $env:HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT) {
  $env:HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT = "收到"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_SESSION_LANE_ENABLED") -and -not $env:HOST_INPUT_WECHAT_SESSION_LANE_ENABLED) {
  $env:HOST_INPUT_WECHAT_SESSION_LANE_ENABLED = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_COLLECT_DEBOUNCE_MS") -and -not $env:HOST_INPUT_WECHAT_COLLECT_DEBOUNCE_MS) {
  $env:HOST_INPUT_WECHAT_COLLECT_DEBOUNCE_MS = "1200"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_COLLECT_CAP") -and -not $env:HOST_INPUT_WECHAT_COLLECT_CAP) {
  $env:HOST_INPUT_WECHAT_COLLECT_CAP = "3"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_FORCE_ACTIVE_CHAT_TARGET") -and -not $env:HOST_INPUT_WECHAT_FORCE_ACTIVE_CHAT_TARGET) {
  $env:HOST_INPUT_WECHAT_FORCE_ACTIVE_CHAT_TARGET = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_BLOCK_FILEHELPER") -and -not $env:HOST_INPUT_WECHAT_BLOCK_FILEHELPER) {
  $env:HOST_INPUT_WECHAT_BLOCK_FILEHELPER = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_REPLY_WAIT_RESULT") -and -not $env:HOST_INPUT_WECHAT_REPLY_WAIT_RESULT) {
  $env:HOST_INPUT_WECHAT_REPLY_WAIT_RESULT = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS") -and -not $env:HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS) {
  $env:HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS = "12000"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_FALLBACK_ON_REJECT") -and -not $env:HOST_INPUT_WECHAT_FALLBACK_ON_REJECT) {
  $env:HOST_INPUT_WECHAT_FALLBACK_ON_REJECT = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT") -and -not $env:HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT) {
  $env:HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT = "System busy, please retry."
}

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

$probeHost = if ($BindHost -eq "0.0.0.0") { "127.0.0.1" } else { $BindHost }

$localServices = $null
$startDockerScript = Join-Path $PSScriptRoot "start_docker_services.ps1"
if (-not (Test-Path -Path $startDockerScript)) {
  throw "start_docker_services.ps1 not found: $startDockerScript"
}
$dockerRaw = & $startDockerScript `
  -EnvFile $EnvFile `
  -ComposeFile $DockerComposeFile `
  -ProjectName $DockerProjectName `
  -Neo4jWaitSeconds $Neo4jWaitSeconds `
  -ChromaWaitSeconds $ChromaWaitSeconds
$dockerJson = ($dockerRaw | Out-String).Trim()
if ($dockerJson) {
  try {
    $localServices = $dockerJson | ConvertFrom-Json
  } catch {
    $localServices = [ordered]@{
      raw = $dockerJson
    }
  }
}

if (Test-Path -Path $pidFile) {
  $existingPidText = (Get-Content -Path $pidFile -Raw -Encoding ascii).Trim()
  if ($existingPidText) {
    $existingPid = [int]$existingPidText
    $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($null -ne $existingProc -and -not $ForceRestart.IsPresent) {
      $result = [ordered]@{
        side = "host"
        status = "already_running"
        pid = $existingPid
        pid_file = $pidFile
        host = $BindHost
        port = $Port
        external_services_backend = $ExternalServicesBackend
        health_url = "http://$probeHost`:$Port/health"
        env_file = $EnvFile
        local_services = $localServices
      }
      $result | ConvertTo-Json -Depth 10
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

$listeningPid = Get-ListeningPid -ProbePort $Port
if ($null -ne $listeningPid) {
  if (-not $ForceRestart.IsPresent) {
    if (Is-HostProcess -ProcessId $listeningPid) {
      $result = [ordered]@{
        side = "host"
        status = "already_running"
        pid = $listeningPid
        pid_file = $pidFile
        host = $BindHost
        port = $Port
        external_services_backend = $ExternalServicesBackend
        health_url = "http://$probeHost`:$Port/health"
        env_file = $EnvFile
        local_services = $localServices
      }
      $result | ConvertTo-Json -Depth 10
      exit 0
    }
    throw "port $Port is already in use by pid $listeningPid (non-host process)"
  }
  Stop-Process -Id $listeningPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

function Test-PythonRuntime {
  param([string]$PythonPath)
  if ([string]::IsNullOrWhiteSpace($PythonPath) -or -not (Test-Path $PythonPath)) {
    return $false
  }
  try {
    $null = & $PythonPath -c "import fastapi, uvicorn, chromadb, pydantic, httpx" 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
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

$hostArgs = @("-m", "host_brain", "--host", $BindHost, "--port", ([string]$Port), "--log-level", "INFO")
if ($StartupCheck.IsPresent) {
  $hostArgs += "--startup-check"
}

$startProcessArgs = @{
  FilePath = $pythonExe
  ArgumentList = $hostArgs
  WorkingDirectory = $repoRoot
  PassThru = $true
  RedirectStandardOutput = $stdoutLog
  RedirectStandardError = $stderrLog
}
if (-not $ShowConsoleWindow.IsPresent) {
  $startProcessArgs["WindowStyle"] = "Hidden"
}
$proc = Start-Process @startProcessArgs

$healthUrl = "http://$probeHost`:$Port/health"
$healthOk = Wait-Http -Url $healthUrl -TimeoutSeconds $WaitSeconds
if (-not $healthOk) {
  $readyUrl = "http://$probeHost`:$Port/ready"
  $healthOk = Wait-Http -Url $readyUrl -TimeoutSeconds 8
}
if (-not $healthOk) {
  $listeningPid = Get-ListeningPid -ProbePort $Port
  if ($null -ne $listeningPid -and [int]$listeningPid -eq [int]$proc.Id) {
    $healthOk = $true
  }
}
if (-not $healthOk) {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  $stderrTail = ""
  if (Test-Path -Path $stderrLog) {
    $stderrTail = (Get-Content -Path $stderrLog -Tail 80 -ErrorAction SilentlyContinue) -join "`n"
  }
  throw "host service failed health check at $healthUrl within $WaitSeconds seconds.`n$stderrTail"
}

$hostPid = [int]$proc.Id
Set-Content -Path $pidFile -Value ([string]$hostPid) -Encoding ascii

# ── M8 TASK-0107: OBS / MCP 子系统健康探测 ──────────────────────────────
$obsStatus = "ok"
$mcpRegistryStatus = "ok"
$startupWarnings = @()

# 探测 /obs/metrics（期望 HTTP 200）
$obsMetricsUrl = "http://$probeHost`:$Port/obs/metrics"
try {
  $obsResp = Invoke-WebRequest -Uri $obsMetricsUrl -UseBasicParsing -TimeoutSec 5
  if ($obsResp.StatusCode -lt 200 -or $obsResp.StatusCode -ge 300) {
    $obsStatus = "degraded"
    $startupWarnings += "STARTUP_OBS_DEGRADED"
  }
} catch {
  $obsStatus = "degraded"
  $startupWarnings += "STARTUP_OBS_DEGRADED"
}

# 探测 MCP 注册表（通过 /health/mcp 端点，若不存在则降级）
$mcpHealthUrl = "http://$probeHost`:$Port/health/mcp"
try {
  $mcpResp = Invoke-WebRequest -Uri $mcpHealthUrl -UseBasicParsing -TimeoutSec 5
  if ($mcpResp.StatusCode -lt 200 -or $mcpResp.StatusCode -ge 300) {
    $mcpRegistryStatus = "degraded"
    $startupWarnings += "STARTUP_MCP_DEGRADED"
  }
} catch {
  # /health/mcp 端点不存在时视为降级（非阻断）
  $mcpRegistryStatus = "degraded"
  $startupWarnings += "STARTUP_MCP_DEGRADED"
}
# ────────────────────────────────────────────────────────────────────────────

# ── M9 TASK-0112: World Canon Sync 健康探测（F-32 告警覆盖）────────────────
$canonSyncStatus = "ok"
$canonSyncDetail = ""

# 探测 /health/canon_sync（若端点不存在则视为 degraded，非阻断）
$canonSyncHealthUrl = "http://$probeHost`:$Port/health/canon_sync"
try {
  $canonSyncResp = Invoke-WebRequest -Uri $canonSyncHealthUrl -UseBasicParsing -TimeoutSec 5
  if ($canonSyncResp.StatusCode -lt 200 -or $canonSyncResp.StatusCode -ge 300) {
    $canonSyncStatus = "degraded"
    $canonSyncDetail = "HTTP $($canonSyncResp.StatusCode) from $canonSyncHealthUrl"
    $startupWarnings += "STARTUP_CANON_SYNC_DEGRADED"
  }
} catch {
  # /health/canon_sync 端点不存在时视为 degraded（非阻断，M9 可选探测）
  $canonSyncStatus = "degraded"
  $canonSyncDetail = "endpoint unreachable: $($_.Exception.Message)"
  $startupWarnings += "STARTUP_CANON_SYNC_DEGRADED"
}

# F-32：canon_sync DEGRADED 时写入结构化告警日志（供监控系统捕获）
if ($canonSyncStatus -eq "degraded") {
  $alertPayload = [ordered]@{
    alert_code   = "STARTUP_CANON_SYNC_DEGRADED"
    severity     = "WARNING"
    ts_utc       = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    host         = $BindHost
    port         = $Port
    detail       = $canonSyncDetail
    action       = "canon_sync may be stale; world canon hotwords and slot injection may be outdated"
    remediation  = "check /health/canon_sync endpoint and canon_sync.py logs"
  }
  $alertJson = $alertPayload | ConvertTo-Json -Depth 5
  $alertLogPath = Join-Path $LogDir "startup_alerts.jsonl"
  Add-Content -Path $alertLogPath -Value $alertJson -Encoding UTF8
  Write-Warning "[STARTUP_CANON_SYNC_DEGRADED] $canonSyncDetail"
}
# ────────────────────────────────────────────────────────────────────────────

$edgeWarmupStatus = "skipped"
$edgeWarmupDetail = "edge warmup disabled by HOST_EDGE_WARMUP_ENABLED=false"
$edgeWarmupOutput = ""

$warmupEnabledRaw = if ($envValues.ContainsKey("HOST_EDGE_WARMUP_ENABLED")) {
  [string]$envValues["HOST_EDGE_WARMUP_ENABLED"]
} else {
  [string]$env:HOST_EDGE_WARMUP_ENABLED
}
$edgeWarmupEnabled = Parse-BoolFromText -Raw $warmupEnabledRaw -Default $true

if ($edgeWarmupEnabled) {
  $warmupTimeoutRaw = if ($envValues.ContainsKey("HOST_EDGE_WARMUP_TIMEOUT_MS")) {
    [string]$envValues["HOST_EDGE_WARMUP_TIMEOUT_MS"]
  } else {
    [string]$env:HOST_EDGE_WARMUP_TIMEOUT_MS
  }
  $warmupRetriesRaw = if ($envValues.ContainsKey("HOST_EDGE_WARMUP_RETRIES")) {
    [string]$envValues["HOST_EDGE_WARMUP_RETRIES"]
  } else {
    [string]$env:HOST_EDGE_WARMUP_RETRIES
  }
  $warmupText = if ($envValues.ContainsKey("HOST_EDGE_WARMUP_TEXT")) {
    [string]$envValues["HOST_EDGE_WARMUP_TEXT"]
  } elseif ($env:HOST_EDGE_WARMUP_TEXT) {
    [string]$env:HOST_EDGE_WARMUP_TEXT
  } else {
    "startup warmup"
  }

  $warmupTimeoutMs = Parse-IntFromText -Raw $warmupTimeoutRaw -Default 20000 -Min 1000 -Max 60000
  $warmupRetries = Parse-IntFromText -Raw $warmupRetriesRaw -Default 0 -Min 0 -Max 2

  try {
    $warmupResult = Invoke-EdgeWarmup `
      -PythonExe $pythonCmd.Source `
      -TimeoutMs $warmupTimeoutMs `
      -Retries $warmupRetries `
      -WarmupText $warmupText
    $edgeWarmupOutput = [string]$warmupResult["output"]
    if ([int]$warmupResult["exit_code"] -eq 0) {
      $edgeWarmupStatus = "ok"
      $edgeWarmupDetail = "edge vts/tts warmup succeeded"
    } else {
      $edgeWarmupStatus = "degraded"
      $edgeWarmupDetail = "edge warmup failed with exit_code=$($warmupResult["exit_code"])"
      $startupWarnings += "STARTUP_EDGE_WARMUP_DEGRADED"
      Write-Warning "[STARTUP_EDGE_WARMUP_DEGRADED] $edgeWarmupDetail"
    }
  } catch {
    $edgeWarmupStatus = "degraded"
    $edgeWarmupDetail = "edge warmup exception: $($_.Exception.Message)"
    $startupWarnings += "STARTUP_EDGE_WARMUP_DEGRADED"
    Write-Warning "[STARTUP_EDGE_WARMUP_DEGRADED] $edgeWarmupDetail"
  }
}

$result = [ordered]@{
  side = "host"
  status = "started"
  pid = $hostPid
  pid_file = $pidFile
  host = $BindHost
  port = $Port
  external_services_backend = $ExternalServicesBackend
  health_url = $healthUrl
  env_file = $EnvFile
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
  local_services = $localServices
  obs_status = $obsStatus
  mcp_registry_status = $mcpRegistryStatus
  canon_sync_status = $canonSyncStatus
  edge_warmup_status = $edgeWarmupStatus
  edge_warmup_detail = $edgeWarmupDetail
  edge_warmup_output = $edgeWarmupOutput
  startup_warnings = $startupWarnings
}
$result | ConvertTo-Json -Depth 10
