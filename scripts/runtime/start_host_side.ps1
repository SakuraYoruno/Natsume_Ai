[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [string]$BindHost = "",
  [int]$Port = 0,
  [int]$WaitSeconds = 40,
  [string]$Neo4jHome = "",
  [int]$Neo4jWaitSeconds = 45,
  [int]$ChromaWaitSeconds = 30,
  [switch]$ForceRestart,
  [switch]$StartupCheck
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

$startLocalScript = Join-Path $PSScriptRoot "start_local_services.ps1"
if (-not (Test-Path -Path $startLocalScript)) {
  throw "start_local_services.ps1 not found: $startLocalScript"
}
$localRaw = & $startLocalScript `
  -EnvFile $EnvFile `
  -Neo4jHome $Neo4jHome `
  -Neo4jWaitSeconds $Neo4jWaitSeconds `
  -ChromaWaitSeconds $ChromaWaitSeconds
$localJson = ($localRaw | Out-String).Trim()
$localServices = $null
if ($localJson) {
  try {
    $localServices = $localJson | ConvertFrom-Json
  } catch {
    $localServices = [ordered]@{
      raw = $localJson
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

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCmd) {
  throw "python executable not found in PATH"
}

$hostArgs = @("-m", "host_brain", "--host", $BindHost, "--port", ([string]$Port), "--log-level", "INFO")
if ($StartupCheck.IsPresent) {
  $hostArgs += "--startup-check"
}

$proc = Start-Process `
  -FilePath $pythonCmd.Source `
  -ArgumentList $hostArgs `
  -WorkingDirectory $repoRoot `
  -PassThru `
  -RedirectStandardOutput $stdoutLog `
  -RedirectStandardError $stderrLog

$healthUrl = "http://$probeHost`:$Port/health"
if (-not (Wait-Http -Url $healthUrl -TimeoutSeconds $WaitSeconds)) {
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

$result = [ordered]@{
  side = "host"
  status = "started"
  pid = $hostPid
  pid_file = $pidFile
  host = $BindHost
  port = $Port
  health_url = $healthUrl
  env_file = $EnvFile
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
  local_services = $localServices
  obs_status = $obsStatus
  mcp_registry_status = $mcpRegistryStatus
  startup_warnings = $startupWarnings
}
$result | ConvertTo-Json -Depth 10
