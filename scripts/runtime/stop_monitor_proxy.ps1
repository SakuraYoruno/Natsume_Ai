[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$NginxExe = "",
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

function Get-MonitorProxyPids {
  param([string]$PrefixPath)
  $needle = ([string]$PrefixPath).Trim().ToLowerInvariant()
  if (-not $needle) {
    return @()
  }
  $pids = @()
  foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name = 'nginx.exe'" -ErrorAction SilentlyContinue)) {
    $cmd = ([string]$proc.CommandLine).ToLowerInvariant()
    if ($cmd -and $cmd.Contains($needle.ToLowerInvariant())) {
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

if ($Port -le 0) {
  if ($envValues.ContainsKey("HOST_MONITOR_PROXY_PORT")) {
    $Port = [int]$envValues["HOST_MONITOR_PROXY_PORT"]
  } elseif ($env:HOST_MONITOR_PROXY_PORT) {
    $Port = [int]$env:HOST_MONITOR_PROXY_PORT
  } else {
    $Port = 18180
  }
}

$runtimeRoot = Join-Path $repoRoot "runtime\host\monitor_proxy"
$nginxPrefix = Join-Path $runtimeRoot "nginx"
$pidFile = Join-Path $nginxPrefix "logs\nginx.pid"
$confPath = Join-Path $nginxPrefix "conf\nginx.conf"

$nginxPath = Resolve-NginxExe -Candidate $NginxExe -EnvValues $envValues
$stopped = $false
$detail = ""
$proxyPid = $null

if (Test-Path -Path $pidFile) {
  $pidText = (Get-Content -Path $pidFile -Raw -Encoding ASCII).Trim()
  if ($pidText) {
    $proxyPid = [int]$pidText
  }
}

if ($nginxPath -and (Test-Path -Path $confPath) -and (Test-Path -Path $pidFile)) {
  try {
    & $nginxPath -p "$nginxPrefix\" -c "conf/nginx.conf" -s quit | Out-Null
    Start-Sleep -Milliseconds 700
  } catch {
    $detail = "nginx quit signal failed: $($_.Exception.Message)"
  }
}

$matchingPids = @(Get-MonitorProxyPids -PrefixPath $nginxPrefix)
foreach ($procId in $matchingPids) {
  if ($null -eq $proxyPid) {
    $proxyPid = [int]$procId
  }
  Stop-ProcessTree -ProcessId ([int]$procId) | Out-Null
}

$listeningPid = Get-ListeningPid -ProbePort $Port
if ($null -ne $listeningPid) {
  if ($null -eq $proxyPid -or [int]$listeningPid -eq [int]$proxyPid) {
    Stop-ProcessTree -ProcessId ([int]$listeningPid) | Out-Null
  }
}

$left = Get-ListeningPid -ProbePort $Port
if ($null -eq $left) {
  $stopped = $true
  if (-not $detail) {
    $detail = "monitor proxy stopped"
  }
} else {
  if (-not $detail) {
    $detail = "port $Port still occupied by pid $left"
  }
}

if (Test-Path -Path $pidFile) {
  Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
}

$result = [ordered]@{
  side = "monitor_proxy"
  status = if ($stopped) { "stopped" } else { "not_running" }
  pid = if ($null -ne $proxyPid) { $proxyPid } else { $left }
  port = $Port
  detail = $detail
  env_file = $EnvFile
}
$result | ConvertTo-Json -Depth 8
