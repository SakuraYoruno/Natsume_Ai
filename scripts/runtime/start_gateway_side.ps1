[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$RuntimeRoot = "",
  [string]$DatabasePath = "",
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 19000,
  [string]$HostUrl = "http://127.0.0.1:18080",
  [string]$Platforms = "",
  [int]$WaitSeconds = 30,
  [switch]$DisableTribeEvents,
  [switch]$ForceRestart,
  [switch]$ShowConsoleWindow
)

$ErrorActionPreference = "Stop"

function Read-EnvFile {
  param([string]$Path)
  $values = @{}
  if (-not (Test-Path -LiteralPath $Path)) {
    return $values
  }
  foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
    $trimmed = ([string]$line).Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    $separator = $trimmed.IndexOf("=")
    if ($separator -lt 1) {
      continue
    }
    $key = $trimmed.Substring(0, $separator).Trim()
    if ($key) {
      $values[$key] = $trimmed.Substring($separator + 1).Trim()
    }
  }
  return $values
}

function Set-ProcessEnvDefaults {
  param([hashtable]$Values)
  foreach ($key in $Values.Keys) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable([string]$key, "Process"))) {
      [Environment]::SetEnvironmentVariable([string]$key, [string]$Values[$key], "Process")
    }
  }
}

function Get-ListeningPid {
  param([int]$ProbePort)
  $connection = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($null -eq $connection) {
    return $null
  }
  return [int]$connection.OwningProcess
}

function Test-GatewaySideProcess {
  param([int]$ProcessId)
  $row = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $row) {
    return $false
  }
  return ([string]$row.CommandLine -like "*scripts*runtime*run_gateway_side.py*")
}

function Get-PythonRuntime {
  param([string]$RepositoryRoot)
  $venvPython = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    try {
      & $venvPython -c "import fastapi, uvicorn" 2>$null
      if ($LASTEXITCODE -eq 0) {
        return $venvPython
      }
    } catch {
    }
  }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $python) {
    throw "python executable not found"
  }
  return $python.Source
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $EnvFile) {
  $candidate = Join-Path $repoRoot ".env"
  $EnvFile = if (Test-Path -LiteralPath $candidate) {
    $candidate
  } else {
    Join-Path $repoRoot ".env.example"
  }
}
$envValues = Read-EnvFile -Path $EnvFile
Set-ProcessEnvDefaults -Values $envValues

if (-not $RuntimeRoot) {
  $RuntimeRoot = Join-Path $repoRoot "runtime\gateway"
} elseif (-not [System.IO.Path]::IsPathRooted($RuntimeRoot)) {
  $RuntimeRoot = Join-Path $repoRoot $RuntimeRoot
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
$logDir = Join-Path $RuntimeRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (-not $DatabasePath) {
  if ($env:EDGE_GATEWAY_TRANSPORT_DB_PATH) {
    $DatabasePath = [string]$env:EDGE_GATEWAY_TRANSPORT_DB_PATH
  } else {
    $DatabasePath = Join-Path $RuntimeRoot "gateway_transport.sqlite3"
  }
}
if (-not [System.IO.Path]::IsPathRooted($DatabasePath)) {
  $DatabasePath = Join-Path $repoRoot $DatabasePath
}
$DatabasePath = [System.IO.Path]::GetFullPath($DatabasePath)

if (-not $BindHost) {
  $BindHost = [string]($env:EDGE_GATEWAY_BIND_HOST)
}
if (-not $BindHost) {
  $BindHost = "127.0.0.1"
}
if ($Port -le 0) {
  $Port = if ($env:EDGE_GATEWAY_PORT) { [int]$env:EDGE_GATEWAY_PORT } else { 19000 }
}
if (-not $HostUrl) {
  $HostUrl = [string]$env:EDGE_EVENT_REPORT_HOST_URL
}
if (-not $HostUrl) {
  $HostUrl = "http://127.0.0.1:18080"
}
if (-not $Platforms -and $env:EDGE_GATEWAY_PLATFORMS) {
  $Platforms = [string]$env:EDGE_GATEWAY_PLATFORMS
}

$pidFile = Join-Path $RuntimeRoot "gateway.pid"
$stopFile = Join-Path $RuntimeRoot "gateway.stop"
$stdoutLog = Join-Path $logDir "gateway.stdout.log"
$stderrLog = Join-Path $logDir "gateway.stderr.log"
$stopScript = Join-Path $PSScriptRoot "stop_gateway_side.ps1"

if ($ForceRestart.IsPresent) {
  & $stopScript -RuntimeRoot $RuntimeRoot -Port $Port | Out-Null
}

if (Test-Path -LiteralPath $pidFile) {
  $existingText = (Get-Content -LiteralPath $pidFile -Raw -Encoding ascii).Trim()
  if ($existingText) {
    $existingPid = [int]$existingText
    if ((Get-Process -Id $existingPid -ErrorAction SilentlyContinue) -and (Test-GatewaySideProcess -ProcessId $existingPid)) {
      [ordered]@{
        side = "gateway"
        status = "already_running"
        pid = $existingPid
        bind_host = $BindHost
        port = $Port
        health_url = "http://$BindHost`:$Port/v1/gateway/health"
        remote_edge_started = $false
      } | ConvertTo-Json -Depth 5
      exit 0
    }
  }
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

$listeningPid = Get-ListeningPid -ProbePort $Port
if ($null -ne $listeningPid) {
  throw "gateway port $Port is already occupied by pid $listeningPid"
}

Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue
$pythonExe = Get-PythonRuntime -RepositoryRoot $repoRoot
$runner = Join-Path $PSScriptRoot "run_gateway_side.py"
$arguments = @(
  $runner,
  "--bind-host", $BindHost,
  "--port", [string]$Port,
  "--host-url", $HostUrl,
  "--db-path", $DatabasePath,
  "--stop-file", $stopFile
)
if ($Platforms) {
  $arguments += @("--platforms", $Platforms)
}
if ($DisableTribeEvents.IsPresent) {
  $arguments += "--disable-tribe-events"
}

$startParameters = @{
  FilePath = $pythonExe
  ArgumentList = $arguments
  WorkingDirectory = $repoRoot
  RedirectStandardOutput = $stdoutLog
  RedirectStandardError = $stderrLog
  PassThru = $true
}
if (-not $ShowConsoleWindow.IsPresent) {
  $startParameters["WindowStyle"] = "Hidden"
}
$process = Start-Process @startParameters

$healthUrl = "http://$BindHost`:$Port/v1/gateway/health"
$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $WaitSeconds))
$health = $null
while ([DateTime]::UtcNow -lt $deadline) {
  if ($process.HasExited) {
    break
  }
  try {
    $candidate = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
    if (
      [string]$candidate.status -eq "ok" -and
      [bool]$candidate.transport.worker_alive -and
      [string]$candidate.transport.mode -eq "DURABLE"
    ) {
      $health = $candidate
      break
    }
  } catch {
  }
  Start-Sleep -Milliseconds 250
}

if ($null -eq $health) {
  if (-not $process.HasExited) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
  }
  $stderrTail = if (Test-Path -LiteralPath $stderrLog) {
    (Get-Content -LiteralPath $stderrLog -Tail 40 -Encoding UTF8) -join "`n"
  } else {
    ""
  }
  throw "gateway failed readiness at $healthUrl`n$stderrTail"
}

Set-Content -LiteralPath $pidFile -Value ([string]$process.Id) -Encoding ascii
[ordered]@{
  side = "gateway"
  status = "started"
  pid = [int]$process.Id
  pid_file = $pidFile
  bind_host = $BindHost
  port = $Port
  host_url = $HostUrl
  health_url = $healthUrl
  database_path = $DatabasePath
  platforms = @($Platforms -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  transport_mode = [string]$health.transport.mode
  worker_alive = [bool]$health.transport.worker_alive
  remote_edge_started = $false
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
} | ConvertTo-Json -Depth 6
