[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$NginxExe = "",
  [string]$BindHost = "",
  [int]$Port = 0,
  [string]$UpstreamHost = "",
  [int]$UpstreamPort = 0,
  [string]$ObsUpstreamHost = "",
  [int]$ObsUpstreamPort = 0,
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

function Get-MonitorProxyPids {
  param([string]$PrefixPath)
  $needle = ([string]$PrefixPath).Trim().ToLowerInvariant()
  if (-not $needle) {
    return @()
  }
  $pids = @()
  foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name = 'nginx.exe'" -ErrorAction SilentlyContinue)) {
    $cmd = ([string]$proc.CommandLine).ToLowerInvariant()
    if ($cmd -and $cmd.Contains($needle)) {
      $pids += [int]$proc.ProcessId
    }
  }
  return ($pids | Sort-Object -Unique)
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
  Start-Sleep -Milliseconds 300
  $left = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($left) {
    cmd /c "taskkill /PID $ProcessId /T /F" | Out-Null
    Start-Sleep -Milliseconds 300
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

if (-not $ObsUpstreamHost) {
  if ($envValues.ContainsKey("HOST_OBS_APP_HOST")) {
    $ObsUpstreamHost = [string]$envValues["HOST_OBS_APP_HOST"]
  } elseif ($env:HOST_OBS_APP_HOST) {
    $ObsUpstreamHost = [string]$env:HOST_OBS_APP_HOST
  } else {
    $ObsUpstreamHost = $UpstreamHost
  }
}
if ($ObsUpstreamHost -eq "0.0.0.0") {
  $ObsUpstreamHost = "127.0.0.1"
}

if ($ObsUpstreamPort -le 0) {
  if ($envValues.ContainsKey("HOST_OBS_APP_PORT")) {
    $ObsUpstreamPort = [int]$envValues["HOST_OBS_APP_PORT"]
  } elseif ($env:HOST_OBS_APP_PORT) {
    $ObsUpstreamPort = [int]$env:HOST_OBS_APP_PORT
  } else {
    $ObsUpstreamPort = 18082
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
  return
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
$cacheDir = Join-Path $nginxPrefix "cache"
Ensure-Directory -Path $cacheDir

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
    obs_upstream = "http://$ObsUpstreamHost`:$ObsUpstreamPort"
    nginx_exe = $nginxPath
    config_path = $confPath
    env_file = $EnvFile
  }
  $result | ConvertTo-Json -Depth 8
  return
}
if ($null -ne $existingPid -and $ForceRestart.IsPresent) {
  Stop-ProcessTree -ProcessId ([int]$existingPid) | Out-Null
  Start-Sleep -Milliseconds 500
}

if ($ForceRestart.IsPresent) {
  foreach ($procId in @(Get-MonitorProxyPids -PrefixPath $nginxPrefix)) {
    Stop-ProcessTree -ProcessId ([int]$procId) | Out-Null
  }
}

$monitorPath = Convert-WindowsPathToNginxPath -Path $StaticDir
if (-not $monitorPath.EndsWith("/")) {
  $monitorPath += "/"
}
$monitorAssetsPath = $monitorPath + "assets/"
$escapedUpstreamHost = $UpstreamHost.Replace("'", "''")
$escapedObsUpstreamHost = $ObsUpstreamHost.Replace("'", "''")
$nginxCachePath = Convert-WindowsPathToNginxPath -Path $cacheDir

$config = @"
worker_processes  1;
error_log  logs/error.log warn;
pid        logs/nginx.pid;

events {
    worker_connections  2048;
}

http {
    types {
        text/html                             html htm;
        text/css                              css;
        application/javascript                js mjs;
        application/json                      json map;
        image/svg+xml                         svg svgz;
        image/png                             png;
        image/jpeg                            jpg jpeg;
        image/gif                             gif;
        image/x-icon                          ico;
        font/woff2                            woff2;
        font/woff                             woff;
    }
    default_type  application/octet-stream;
    sendfile        on;
    tcp_nopush      on;
    tcp_nodelay     on;
    keepalive_timeout  65;
    server_tokens off;
    proxy_cache_path $nginxCachePath levels=1:2 keys_zone=obs_microcache:16m max_size=256m inactive=60s use_temp_path=off;

    gzip on;
    gzip_min_length 1024;
    gzip_comp_level 5;
    gzip_types text/plain text/css application/json application/javascript text/javascript application/xml image/svg+xml;

    upstream host_backend {
        server ${escapedUpstreamHost}:$UpstreamPort;
        keepalive 32;
    }

    upstream obs_backend {
        server ${escapedObsUpstreamHost}:$ObsUpstreamPort;
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
            types {
                text/css               css;
                application/javascript js mjs;
                application/json       json map;
                image/svg+xml          svg svgz;
                image/png              png;
                image/jpeg             jpg jpeg;
                image/gif              gif;
                image/x-icon           ico;
                font/woff2             woff2;
                font/woff              woff;
            }
            alias $monitorAssetsPath;
            expires 30d;
            add_header Cache-Control "public, max-age=2592000, immutable";
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

        location /obs/ws/health {
            proxy_pass http://obs_backend;
            proxy_http_version 1.1;
            proxy_set_header Upgrade `$http_upgrade;
            proxy_set_header Connection "upgrade";
            proxy_set_header Host `$http_host;
            proxy_set_header X-Real-IP `$remote_addr;
            proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto `$scheme;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        location /obs/trace-feed/live {
            proxy_pass http://obs_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host `$http_host;
            proxy_set_header X-Real-IP `$remote_addr;
            proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto `$scheme;
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 3600s;
            proxy_send_timeout 3600s;
        }

        location /obs/ {
            proxy_pass http://obs_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host `$http_host;
            proxy_set_header X-Real-IP `$remote_addr;
            proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto `$scheme;
            # Operator responses are principal-scoped.  Never place them in a
            # shared cache: anonymous/public responses are already cheap and a
            # cache key that omits authority could leak authenticated data.
            proxy_cache off;
            add_header X-Natsume-Obs-Cache BYPASS always;
            proxy_read_timeout 120s;
            proxy_send_timeout 120s;
        }

        location /api/ui/ {
            proxy_pass http://obs_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host `$http_host;
            proxy_set_header X-Real-IP `$remote_addr;
            proxy_set_header X-Forwarded-For `$proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto `$scheme;
            # /api/ui is an authenticated operator namespace.  Its responses
            # must never be shared across sessions.
            proxy_cache off;
            add_header X-Natsume-Obs-Cache BYPASS always;
            proxy_read_timeout 120s;
            proxy_send_timeout 120s;
        }

        location / {
            proxy_pass http://host_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host `$http_host;
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

$nginxStart = Start-Process -FilePath $nginxPath -ArgumentList @('-p', "$nginxPrefix\", '-c', 'conf/nginx.conf') -WorkingDirectory $nginxPrefix -WindowStyle Hidden -PassThru

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
    obs_upstream = "http://$ObsUpstreamHost`:$ObsUpstreamPort"
    nginx_exe = $nginxPath
    config_path = $confPath
    error_log = $errorLog
    error_tail = $errorTail
    env_file = $EnvFile
  }
  $result | ConvertTo-Json -Depth 10
  return
}

$proxyPidValue = $null
if (Test-Path -Path $pidFile) {
  $pidText = (Get-Content -Path $pidFile -Raw -Encoding ASCII).Trim()
  if ($pidText) {
    $proxyPidValue = [int]$pidText
  }
}
if ($null -eq $proxyPidValue) {
  $proxyPidValue = Get-ListeningPid -ProbePort $Port
}
if ($null -eq $proxyPidValue -and $null -ne $nginxStart) {
  $proxyPidValue = [int]$nginxStart.Id
}

$result = [ordered]@{
  side = "monitor_proxy"
  status = "started"
  pid = $proxyPidValue
  host = $BindHost
  port = $Port
  monitor_url = "http://$BindHost`:$Port/monitor/"
  upstream = "http://$UpstreamHost`:$UpstreamPort"
  obs_upstream = "http://$ObsUpstreamHost`:$ObsUpstreamPort"
  nginx_exe = $nginxPath
  config_path = $confPath
  access_log = $accessLog
  error_log = $errorLog
  static_dir = $StaticDir
  env_file = $EnvFile
}
$result | ConvertTo-Json -Depth 10
