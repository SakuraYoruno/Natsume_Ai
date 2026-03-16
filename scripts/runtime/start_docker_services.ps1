[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$ComposeFile = "",
  [string]$ProjectName = "natsume-ext",
  [int]$DockerDaemonWaitSeconds = 90,
  [int]$Neo4jWaitSeconds = 90,
  [int]$ChromaWaitSeconds = 30,
  [int]$RedisWaitSeconds = 20
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

function Get-PortFromUri {
  param(
    [string]$UriText,
    [int]$DefaultPort
  )
  try {
    $uri = [Uri]$UriText
    if ($uri.Port -gt 0) {
      return [int]$uri.Port
    }
  } catch {
  }
  $match = [regex]::Match([string]$UriText, ":(\d+)$")
  if ($match.Success) {
    return [int]$match.Groups[1].Value
  }
  return [int]$DefaultPort
}

function Get-RedisPortFromUrl {
  param(
    [string]$RedisUrl,
    [int]$DefaultPort
  )
  if (-not $RedisUrl) {
    return [int]$DefaultPort
  }
  try {
    $uri = [Uri]$RedisUrl
    if ($uri.Port -gt 0) {
      return [int]$uri.Port
    }
  } catch {
  }
  return [int]$DefaultPort
}

function Wait-TcpPort {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $conn) {
      return $true
    }
    Start-Sleep -Milliseconds 500
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

function Resolve-DockerExecutable {
  $cmd = Get-Command docker -ErrorAction SilentlyContinue
  if ($null -ne $cmd) {
    return [string]$cmd.Source
  }

  $candidates = @("C:\Program Files\Docker\Docker\resources\bin\docker.exe")
  if ($env:ProgramFiles) {
    $candidates += Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"
  }
  if ($env:ProgramW6432) {
    $candidates += Join-Path $env:ProgramW6432 "Docker\Docker\resources\bin\docker.exe"
  }
  if ($env:LOCALAPPDATA) {
    $candidates += Join-Path $env:LOCALAPPDATA "Programs\Docker\Docker\resources\bin\docker.exe"
  }

  foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if ($candidate -and (Test-Path -Path $candidate)) {
      return [string]$candidate
    }
  }
  throw "docker executable not found in PATH or common install paths"
}

function Test-DockerDaemon {
  param([string]$DockerExe)
  if (-not $DockerExe) {
    return $false
  }
  $prevPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $DockerExe info --format "{{.ServerVersion}}" *> $null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  } finally {
    $ErrorActionPreference = $prevPreference
  }
}

function Start-DockerDesktopProcess {
  $candidates = @("C:\Program Files\Docker\Docker\Docker Desktop.exe")
  if ($env:ProgramFiles) {
    $candidates += Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
  }
  if ($env:ProgramW6432) {
    $candidates += Join-Path $env:ProgramW6432 "Docker\Docker\Docker Desktop.exe"
  }
  if ($env:LOCALAPPDATA) {
    $candidates += Join-Path $env:LOCALAPPDATA "Programs\Docker\Docker\Docker Desktop.exe"
  }
  foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if (-not $candidate -or -not (Test-Path -Path $candidate)) {
      continue
    }
    try {
      Start-Process -FilePath $candidate -ErrorAction Stop | Out-Null
      return $true
    } catch {
    }
  }
  return $false
}

function Ensure-DockerDaemonRunning {
  param(
    [string]$DockerExe,
    [int]$TimeoutSeconds = 90
  )
  if (Test-DockerDaemon -DockerExe $DockerExe) {
    return
  }

  $actions = @()
  $dockerService = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
  if ($null -ne $dockerService) {
    if ($dockerService.Status -ne "Running") {
      try {
        Start-Service -Name "com.docker.service" -ErrorAction Stop
        $actions += "service_started"
      } catch {
        $actions += "service_start_failed"
      }
    } else {
      $actions += "service_already_running"
    }
  } else {
    $actions += "service_not_found"
  }

  if (-not (Test-DockerDaemon -DockerExe $DockerExe)) {
    if (Start-DockerDesktopProcess) {
      $actions += "desktop_launch_requested"
    } else {
      $actions += "desktop_not_found_or_launch_failed"
    }
  }

  $waitSeconds = if ($TimeoutSeconds -gt 0) { $TimeoutSeconds } else { 90 }
  $deadline = (Get-Date).AddSeconds($waitSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Test-DockerDaemon -DockerExe $DockerExe) {
      return
    }
    Start-Sleep -Seconds 2
  }

  $detail = if ($actions.Count -gt 0) { ($actions -join ", ") } else { "no_auto_start_actions" }
  throw "docker daemon not ready after ${waitSeconds}s (actions=$detail). please start Docker Desktop and retry."
}

function Ensure-PathContains {
  param([string]$Directory)
  if (-not $Directory) {
    return
  }
  $current = [string]$env:PATH
  $parts = @()
  if ($current) {
    $parts = $current -split ";"
  }
  foreach ($part in $parts) {
    if ([string]::Equals($part.Trim(), $Directory.Trim(), [System.StringComparison]::OrdinalIgnoreCase)) {
      return
    }
  }
  if ($current) {
    $env:PATH = "$Directory;$current"
  } else {
    $env:PATH = $Directory
  }
}

function Load-JsonObjectFile {
  param([string]$Path)
  if (-not (Test-Path -Path $Path)) {
    return @{}
  }
  $encodings = @("utf8", "utf8BOM")
  foreach ($enc in $encodings) {
    try {
      $raw = Get-Content -Path $Path -Raw -Encoding $enc
      if (-not $raw) {
        continue
      }
      $obj = $raw | ConvertFrom-Json -ErrorAction Stop
      if ($obj -is [System.Collections.IDictionary]) {
        return $obj
      }
      if ($obj -and $obj.PSObject) {
        $ht = @{}
        foreach ($prop in $obj.PSObject.Properties) {
          $ht[[string]$prop.Name] = $prop.Value
        }
        return $ht
      }
    } catch {
    }
  }
  return @{}
}

function Resolve-Neo4jAuth {
  param(
    [string]$AuthRef,
    [string]$RepoRoot
  )
  $refText = [string]$AuthRef
  if (-not $refText) {
    return ""
  }
  if (-not $refText.StartsWith("sec://")) {
    return $refText
  }
  $localStore = Join-Path $RepoRoot "configs\secrets\secret_store.runtime.local.json"
  $fallbackStore = Join-Path $RepoRoot "configs\secrets\secret_store.runtime.example.json"
  $store = Load-JsonObjectFile -Path $localStore
  if (-not $store.Contains($refText)) {
    $store = Load-JsonObjectFile -Path $fallbackStore
  }
  if ($store.Contains($refText)) {
    return [string]$store[$refText]
  }
  return ""
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
if (-not $ComposeFile) {
  $ComposeFile = Join-Path $PSScriptRoot "docker-compose.external.yml"
}
if (-not (Test-Path -Path $ComposeFile)) {
  throw "docker compose file not found: $ComposeFile"
}

$dockerExe = Resolve-DockerExecutable
$dockerDir = Split-Path -Path $dockerExe -Parent
Ensure-PathContains -Directory $dockerDir
Ensure-DockerDaemonRunning -DockerExe $dockerExe -TimeoutSeconds $DockerDaemonWaitSeconds

$envValues = Read-EnvFile -Path $EnvFile
$neo4jUri = if ($envValues.ContainsKey("BOND_NEO4J_URI")) { [string]$envValues["BOND_NEO4J_URI"] } elseif ($env:BOND_NEO4J_URI) { [string]$env:BOND_NEO4J_URI } else { "bolt://127.0.0.1:7687" }
$neo4jPort = Get-PortFromUri -UriText $neo4jUri -DefaultPort 7687
$neo4jHttpPort = 7474
$neo4jAuthRef = if ($envValues.ContainsKey("BOND_NEO4J_AUTH_REF")) { [string]$envValues["BOND_NEO4J_AUTH_REF"] } elseif ($env:BOND_NEO4J_AUTH_REF) { [string]$env:BOND_NEO4J_AUTH_REF } else { "" }
$neo4jAuthValue = Resolve-Neo4jAuth -AuthRef $neo4jAuthRef -RepoRoot $repoRoot
if (-not $neo4jAuthValue) {
  throw "failed to resolve BOND_NEO4J_AUTH_REF=$neo4jAuthRef"
}
$authParts = [string]$neo4jAuthValue -split ":", 2
if ($authParts.Length -ne 2 -or -not $authParts[0] -or -not $authParts[1]) {
  throw "neo4j auth must be 'username:password', got: $neo4jAuthRef"
}
$neo4jUser = [string]$authParts[0]
$neo4jPassword = [string]$authParts[1]

# Windows 常保留 8000/8100 等端口，Chroma 默认改用 18000
$chromaDefaultPort = 18000
$chromaEndpoint = if ($envValues.ContainsKey("CHROMA_ENDPOINT")) { [string]$envValues["CHROMA_ENDPOINT"] } elseif ($env:CHROMA_ENDPOINT) { [string]$env:CHROMA_ENDPOINT } else { "http://127.0.0.1:$chromaDefaultPort" }
$chromaPort = Get-PortFromUri -UriText $chromaEndpoint -DefaultPort $chromaDefaultPort
# 若为 Windows 常见保留端口，强制改为 18000
if ($chromaPort -eq 8100 -or $chromaPort -eq 8000) {
  Write-Warning "Chroma port $chromaPort is often reserved on Windows; using $chromaDefaultPort for Docker. Set CHROMA_ENDPOINT=http://127.0.0.1:$chromaDefaultPort and CHROMA_PORT=$chromaDefaultPort in .env."
  $chromaPort = $chromaDefaultPort
}
$chromaPortEnv = if ($envValues.ContainsKey("CHROMA_PORT")) { try { [int]$envValues["CHROMA_PORT"] } catch { 0 } } else { 0 }
if ($chromaPortEnv -eq 8100 -or $chromaPortEnv -eq 8000) { $chromaPort = $chromaDefaultPort }
$redisUrl = if ($envValues.ContainsKey("HOST_RATE_LIMIT_REDIS_URL")) { [string]$envValues["HOST_RATE_LIMIT_REDIS_URL"] } elseif ($env:HOST_RATE_LIMIT_REDIS_URL) { [string]$env:HOST_RATE_LIMIT_REDIS_URL } else { "redis://127.0.0.1:6379/0" }
$redisPort = Get-RedisPortFromUrl -RedisUrl $redisUrl -DefaultPort 6379

Ensure-Directory -Path (Join-Path $repoRoot "runtime\docker\neo4j\data")
Ensure-Directory -Path (Join-Path $repoRoot "runtime\docker\neo4j\logs")
Ensure-Directory -Path (Join-Path $repoRoot "runtime\docker\chroma\data")
Ensure-Directory -Path (Join-Path $repoRoot "runtime\docker\redis\data")

$env:NATSUME_NEO4J_USER = $neo4jUser
$env:NATSUME_NEO4J_PASSWORD = $neo4jPassword
$env:NATSUME_NEO4J_BOLT_PORT = [string]$neo4jPort
$env:NATSUME_NEO4J_HTTP_PORT = [string]$neo4jHttpPort
$env:NATSUME_CHROMA_PORT = [string]$chromaPort
$env:NATSUME_REDIS_PORT = [string]$redisPort
# 禁止 Docker 使用 8000/8100（Windows 常保留），最后一刻强制
if ($chromaPort -eq 8000 -or $chromaPort -eq 8100) {
  $env:NATSUME_CHROMA_PORT = "18000"
  $chromaPort = 18000
}
Write-Host "Chroma host port: $env:NATSUME_CHROMA_PORT (container stays 8000)"

& $dockerExe compose -f $ComposeFile -p $ProjectName up -d
if ($LASTEXITCODE -ne 0) {
  throw "docker compose up failed (exit=$LASTEXITCODE)"
}

if (-not (Wait-TcpPort -Port $neo4jPort -TimeoutSeconds $Neo4jWaitSeconds)) {
  $hint = "Check: docker logs natsume-neo4j (or $ProjectName-neo4j-1). Retry with -Neo4jWaitSeconds 120 or 180 if Neo4j is still starting."
  throw "neo4j docker service failed to listen on port $neo4jPort. $hint"
}
if (-not (Wait-Http -Url "http://127.0.0.1:$chromaPort/api/v2/heartbeat" -TimeoutSeconds $ChromaWaitSeconds)) {
  throw "chroma docker service failed heartbeat on port $chromaPort"
}
if (-not (Wait-TcpPort -Port $redisPort -TimeoutSeconds $RedisWaitSeconds)) {
  throw "redis docker service failed to listen on port $redisPort"
}

$result = [ordered]@{
  backend = "docker"
  env_file = $EnvFile
  compose_file = $ComposeFile
  project_name = $ProjectName
  neo4j = [ordered]@{
    status = "started"
    uri = $neo4jUri
    port = $neo4jPort
    auth_ref = $neo4jAuthRef
  }
  chroma = [ordered]@{
    status = "started"
    endpoint = $chromaEndpoint
    port = $chromaPort
  }
  redis = [ordered]@{
    status = "started"
    url = $redisUrl
    port = $redisPort
  }
}
$result | ConvertTo-Json -Depth 8
