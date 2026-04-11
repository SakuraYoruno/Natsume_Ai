[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$NginxExe = "",
  [string]$BindHost = "",
  [int]$Port = 0,
  [string]$UpstreamHost = "",
  [int]$UpstreamPort = 0,
  [string]$StaticDir = "",
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

function Parse-BoolValue {
  param(
    [AllowNull()][object]$Value,
    [bool]$Default = $false
  )
  if ($null -eq $Value) {
    return $Default
  }
  $raw = ([string]$Value).Trim().ToLowerInvariant()
  if (-not $raw) {
    return $Default
  }
  return $raw -in @("1", "true", "yes", "on")
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

function Resolve-NginxExe {
  param(
    [string]$Candidate,
    [hashtable]$EnvValues
  )

  $candidates = @()
  if ($Candidate) {
    $candidates += $Candidate
  }
  if ($EnvValues.ContainsKey("NATSUME_NGINX_EXE")) {
    $candidates += [string]$EnvValues["NATSUME_NGINX_EXE"]
  }
  if ($env:NATSUME_NGINX_EXE) {
    $candidates += [string]$env:NATSUME_NGINX_EXE
  }
  try {
    $regValue = (Get-ItemProperty -Path "HKCU:\Environment" -Name "NATSUME_NGINX_EXE" -ErrorAction SilentlyContinue).NATSUME_NGINX_EXE
    if ($regValue) {
      $candidates += [string]$regValue
    }
  } catch {
  }
  $cmd = Get-Command nginx -ErrorAction SilentlyContinue
  if ($null -ne $cmd -and $cmd.Source) {
    $candidates += [string]$cmd.Source
  }
  $candidates += @(
    "C:\nginx\nginx.exe",
    "C:\Program Files\nginx\nginx.exe",
    "C:\Program Files (x86)\nginx\nginx.exe"
  )

  foreach ($item in $candidates) {
    $resolved = ([string]$item).Trim('"').Trim()
    if (-not $resolved) {
      continue
    }
    if (Test-Path -Path $resolved) {
      return (Resolve-Path -Path $resolved).Path
    }
  }
  return ""
}

function Convert-WindowsPathToNginxPath {
  param([string]$Path)
  $resolved = (Resolve-Path -Path $Path).Path
  return ($resolved -replace "\\", "/")
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
$envValues = Read-EnvFile -Path $EnvFile

if (-not $BindHost) {
  if ($envValues.ContainsKey("HOST_MONITOR_PROXY_HOST")) {
    $BindHost = [string]$envValues["HOST_MONITOR_PROXY_HOST"]
  } elseif ($env:HOST_MONITOR_PROXY_HOST) {
    $BindHost = [string]$env:HOST_MONITOR_PROXY_HOST
  } else {
    $BindHost = "127.0.0.1"
  }
}

if ($Port -le 0) {
  if ($envValues.ContainsKey("HOST_MONITOR_PROXY_PORT")) {
    $Port = [int]$envValues["HOST_MONITOR_PROXY_PORT"]
  } elseif ($env:HOST_MONITOR_PROXY_PORT) {
    $Port = [int]$env:HOST_MONITOR_PROXY_PORT
  } else {
    $Port = 18180
  }
}

if (-not $UpstreamHost) {
  if ($envValues.ContainsKey("HOST_APP_HOST")) {
    $UpstreamHost = [string]$envValues["HOST_APP_HOST"]
  } elseif ($env:HOST_APP_HOST) {
    $UpstreamHost = [string]$env:HOST_APP_HOST
  } else {
    $UpstreamHost = "127.0.0.1"
  }
}
if ($UpstreamHost -eq "0.0.0.0") {
  $UpstreamHost = "127.0.0.1"
}

if ($UpstreamPort -le 0) {
  if ($envValues.ContainsKey("HOST_APP_PORT")) {
    $UpstreamPort = [int]$envValues["HOST_APP_PORT"]
  } elseif ($env:HOST_APP_PORT) {
    $UpstreamPort = [int]$env:HOST_APP_PORT
  } else {
    $UpstreamPort = 18080
  }
}

if (-not $StaticDir) {
  $StaticDir = Join-Path $repoRoot "host_brain\app\static\monitor"
}
if (-not (Test-Path -Path $StaticDir)) {
  throw "monitor static dir not found: $StaticDir"
}

$nginxPath = Resolve-NginxExe -Candidate $NginxExe -EnvValues $envValues
if (-not $nginxPath) {
  $result = [ordered]@{
    side = "monitor_proxy"
    status = "degraded"
    reason = "nginx_not_found"
    host = $BindHost
    port = $Port
    upstream = "http://$UpstreamHost`:$UpstreamPort"
    monitor_url = "http://$BindHost`:$Port/monitor/"
    env_file = $EnvFile
  }
  $result | ConvertTo-Json -Depth 8
  exit 0
}

$runtimeRoot = Join-Path $repoRoot "runtime\host\monitor_proxy"
$nginxPrefix = Join-Path $runtimeRoot "nginx"
$confDir = Join-Path $nginxPrefix "conf"
$logsDir = Join-Path $nginxPrefix "logs"
$tempDir = Join-Path $nginxPrefix "temp"
$clientBodyTempDir = Join-Path $tempDir "client_body_temp"
$proxyTempDir = Join-Path $tempDir "proxy_temp"
$fastcgiTempDir = Join-Path $tempDir "fastcgi_temp"
$uwsgiTempDir = Join-Path $tempDir "uwsgi_temp"
$scgiTempDir = Join-Path $tempDir "scgi_temp"
Ensure-Directory -Path $runtimeRoot
Ensure-Directory -Path $nginxPrefix
Ensure-Directory -Path $confDir
Ensure-Directory -Path $logsDir
Ensure-Directory -Path $tempDir
Ensure-Directory -Path $clientBodyTempDir
Ensure-Directory -Path $proxyTempDir
Ensure-Directory -Path $fastcgiTempDir
Ensure-Directory -Path $uwsgiTempDir
Ensure-Directory -Path $scgiTempDir

$pidFile = Join-Path $logsDir "nginx.pid"
$confPath = Join-Path $confDir "nginx.conf"
$errorLog = Join-Path $logsDir "error.log"
$accessLog = Join-Path $logsDir "access.log"

$existingPid = Get-ListeningPid -ProbePort $Port
if ($null -ne $existingPid -and -not $ForceRestart.IsPresent) {
  $result = [ordered]@{
    side = "monitor_proxy"
    status = "already_running"
    pid = [int]$existingPid
    host = $BindHost
    port = $Port
    monitor_url = "http://$BindHost`:$Port/monitor/"
    upstream = "http://$UpstreamHost`:$UpstreamPort"
    nginx_exe = $nginxPath
    config_path = $confPath
    env_file = $EnvFile
  }
  $result | ConvertTo-Json -Depth 8
  exit 0
}
if ($null -ne $existingPid -and $ForceRestart.IsPresent) {
  Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

$monitorPath = Convert-WindowsPathToNginxPath -Path $StaticDir
if (-not $monitorPath.EndsWith("/")) {
  $monitorPath += "/"
}
$monitorAssetsPath = $monitorPath + "assets/"
$escapedUpstreamHost = $UpstreamHost.Replace("'", "''")

$config = @"
worker_processes  1;
error_log  logs/error.log warn;
pid        logs/nginx.pid;

events {
    worker_connections  2048;
}

http {
    default_type  application/octet-stream;
    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout  65;
    server_tokens off;

    gzip on;
    gzip_min_length 1024;
    gzip_comp_level 5;
    gzip_types text/plain text/css application/json application/javascript text/javascript application/xml image/svg+xml;

    upstream host_backend {
        server ${escapedUpstreamHost}:$UpstreamPort;
        keepalive 32;
    }

    server {
        listen $BindHost`:$Port;
        server_name _;
        client_max_body_size 16m;

        access_log logs/access.log;

        location = /panel {
            return 301 /monitor/;
        }

        location = /monitor {
            return 301 /monitor/;
        }

        location /monitor/assets/ {
            alias $monitorAssetsPath;
            expires 30d;
            add_header Cache-Control "public, max-age=2592000, immutable";
            try_files `$uri =404;
        }

        location = /monitor/index.html {
            alias ${monitorPath}index.html;
            add_header Cache-Control "no-cache";
        }

        location /monitor/ {
            alias $monitorPath;
            try_files `$uri `$uri/ /monitor/index.html;
            add_header Cache-Control "no-cache";
        }

        location /obs/trace-feed/live {
            proxy_pass http://host_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host `$host;
            proxy_set_header X-Real-IP `$remote_addr;
            proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto `$scheme;
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        location / {
            proxy_pass http://host_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host `$host;
            proxy_set_header X-Real-IP `$remote_addr;
            proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto `$scheme;
            proxy_read_timeout 120s;
            proxy_send_timeout 120s;
        }
    }
}
"@

Set-Content -Path $confPath -Value $config -Encoding ASCII

if (Test-Path -Path $pidFile) {
  Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}

& $nginxPath -p "$nginxPrefix\" -c "conf/nginx.conf" | Out-Null

$probeHost = if ($BindHost -eq "0.0.0.0") { "127.0.0.1" } else { $BindHost }
$monitorUrl = "http://$probeHost`:$Port/monitor/index.html"
$metricsUrl = "http://$probeHost`:$Port/obs/metrics"
$monitorOk = Wait-Http -Url $monitorUrl -TimeoutSeconds $WaitSeconds
$metricsOk = if ($monitorOk) { Wait-Http -Url $metricsUrl -TimeoutSeconds 8 } else { $false }

if (-not $monitorOk -or -not $metricsOk) {
  try {
    & $nginxPath -p "$nginxPrefix\" -c "conf/nginx.conf" -s quit | Out-Null
  } catch {
  }
  $errorTail = ""
  if (Test-Path -Path $errorLog) {
    $errorTail = (Get-Content -Path $errorLog -Tail 80 -ErrorAction SilentlyContinue) -join "`n"
  }
  $result = [ordered]@{
    side = "monitor_proxy"
    status = "degraded"
    reason = "health_check_failed"
    host = $BindHost
    port = $Port
    monitor_url = "http://$BindHost`:$Port/monitor/"
    upstream = "http://$UpstreamHost`:$UpstreamPort"
    nginx_exe = $nginxPath
    config_path = $confPath
    error_log = $errorLog
    error_tail = $errorTail
    env_file = $EnvFile
  }
  $result | ConvertTo-Json -Depth 10
  exit 0
}

$pid = $null
if (Test-Path -Path $pidFile) {
  $pidText = (Get-Content -Path $pidFile -Raw -Encoding ASCII).Trim()
  if ($pidText) {
    $pid = [int]$pidText
  }
}
if ($null -eq $pid) {
  $pid = Get-ListeningPid -ProbePort $Port
}

$result = [ordered]@{
  side = "monitor_proxy"
  status = "started"
  pid = $pid
  host = $BindHost
  port = $Port
  monitor_url = "http://$BindHost`:$Port/monitor/"
  upstream = "http://$UpstreamHost`:$UpstreamPort"
  nginx_exe = $nginxPath
  config_path = $confPath
  access_log = $accessLog
  error_log = $errorLog
  static_dir = $StaticDir
  env_file = $EnvFile
}
$result | ConvertTo-Json -Depth 10
