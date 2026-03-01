[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$Neo4jHome = "",
  [int]$Neo4jWaitSeconds = 45,
  [int]$ChromaWaitSeconds = 30
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

function Get-ListeningPid {
  param([int]$Port)
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Wait-TcpPort {
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
  $EnvFile = Join-Path $repoRoot "configs\runtime\local_services.env"
}

$runtimeRoot = Join-Path $repoRoot "runtime"
$neoRoot = Join-Path $runtimeRoot "neo4j"
$chromaRoot = Join-Path $runtimeRoot "chroma"

Ensure-Directory -Path $runtimeRoot
Ensure-Directory -Path $neoRoot
Ensure-Directory -Path $chromaRoot

$envValues = Read-EnvFile -Path $EnvFile
Set-ProcessEnv -Values $envValues

if (-not $env:JAVA_HOME -or -not (Test-Path (Join-Path $env:JAVA_HOME "bin\java.exe"))) {
  $jdkFallback = "C:\Program Files\Microsoft\jdk-21.0.10.7-hotspot"
  if (Test-Path (Join-Path $jdkFallback "bin\java.exe")) {
    $env:JAVA_HOME = $jdkFallback
  }
}
if ($env:JAVA_HOME -and (Test-Path (Join-Path $env:JAVA_HOME "bin\java.exe"))) {
  $env:Path = "$($env:JAVA_HOME)\bin;$($env:Path)"
}

$neo4jUri = if ($envValues.ContainsKey("BOND_NEO4J_URI")) { [string]$envValues["BOND_NEO4J_URI"] } elseif ($env:BOND_NEO4J_URI) { [string]$env:BOND_NEO4J_URI } else { "bolt://127.0.0.1:7687" }
$neo4jPort = Get-PortFromUri -UriText $neo4jUri -DefaultPort 7687
$neoPidFile = Join-Path $neoRoot "neo4j.pid"
$neoStatus = "running_existing"
$neoPid = Get-ListeningPid -Port $neo4jPort

if (-not $neoPid) {
  if (-not $Neo4jHome) {
    $candidates = Get-ChildItem -Path $neoRoot -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "neo4j-community-*" } | Sort-Object LastWriteTime -Descending
    if ($candidates.Count -gt 0) {
      $Neo4jHome = $candidates[0].FullName
    }
  }
  if (-not $Neo4jHome -or -not (Test-Path $Neo4jHome)) {
    throw "Neo4j home not found. Expected extracted folder under runtime\\neo4j\\neo4j-community-*"
  }

  $neo4jBat = Join-Path $Neo4jHome "bin\neo4j.bat"
  if (-not (Test-Path $neo4jBat)) {
    throw "neo4j.bat not found: $neo4jBat"
  }

  $neoStdOut = Join-Path $neoRoot "neo4j.console.out.log"
  $neoStdErr = Join-Path $neoRoot "neo4j.console.err.log"
  $neoProc = Start-Process -FilePath $neo4jBat -ArgumentList "console" -PassThru -RedirectStandardOutput $neoStdOut -RedirectStandardError $neoStdErr

  if (-not (Wait-TcpPort -Port $neo4jPort -TimeoutSeconds $Neo4jWaitSeconds)) {
    $tail = ""
    $neoLog = Join-Path $Neo4jHome "logs\neo4j.log"
    if (Test-Path $neoLog) {
      $tail = (Get-Content -Path $neoLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
    }
    throw "Neo4j failed to become ready on port $neo4jPort within $Neo4jWaitSeconds seconds.`n$tail"
  }

  $neoPid = Get-ListeningPid -Port $neo4jPort
  if (-not $neoPid) {
    $neoPid = [int]$neoProc.Id
  }
  $neoStatus = "started"
}

Set-Content -Path $neoPidFile -Value ([string]$neoPid) -Encoding ascii

$chromaEndpoint = if ($envValues.ContainsKey("CHROMA_ENDPOINT")) { [string]$envValues["CHROMA_ENDPOINT"] } elseif ($env:CHROMA_ENDPOINT) { [string]$env:CHROMA_ENDPOINT } else { "http://127.0.0.1:8000" }
$chromaUri = [Uri]$chromaEndpoint
$chromaHost = if ($chromaUri.Host) { [string]$chromaUri.Host } else { "127.0.0.1" }
$chromaPort = if ($chromaUri.Port -gt 0) { [int]$chromaUri.Port } else { 8000 }
$chromaPidFile = Join-Path $chromaRoot "chroma.pid"
$chromaStatus = "running_existing"
$chromaPid = Get-ListeningPid -Port $chromaPort

if (-not $chromaPid) {
  $chromaCmd = Get-Command chroma -ErrorAction SilentlyContinue
  $chromaExe = if ($chromaCmd) { [string]$chromaCmd.Source } else { "C:\Program Files\Python311\Scripts\chroma.exe" }
  if (-not (Test-Path $chromaExe)) {
    throw "chroma executable not found. Expected command `chroma` or file at: $chromaExe"
  }

  $chromaDataPath = Join-Path $chromaRoot "data"
  Ensure-Directory -Path $chromaDataPath
  $chromaStdOut = Join-Path $chromaRoot "chroma.out.log"
  $chromaStdErr = Join-Path $chromaRoot "chroma.err.log"
  $chromaProc = Start-Process -FilePath $chromaExe -ArgumentList @("run", "--path", $chromaDataPath, "--host", $chromaHost, "--port", ([string]$chromaPort)) -PassThru -RedirectStandardOutput $chromaStdOut -RedirectStandardError $chromaStdErr

  $heartbeatUrl = "http://$chromaHost`:$chromaPort/api/v2/heartbeat"
  if (-not (Wait-Http -Url $heartbeatUrl -TimeoutSeconds $ChromaWaitSeconds)) {
    $tail = ""
    if (Test-Path $chromaStdErr) {
      $tail = (Get-Content -Path $chromaStdErr -Tail 60 -ErrorAction SilentlyContinue) -join "`n"
    }
    throw "Chroma failed to become ready at $heartbeatUrl within $ChromaWaitSeconds seconds.`n$tail"
  }

  $chromaPid = Get-ListeningPid -Port $chromaPort
  if (-not $chromaPid) {
    $chromaPid = [int]$chromaProc.Id
  }
  $chromaStatus = "started"
}

Set-Content -Path $chromaPidFile -Value ([string]$chromaPid) -Encoding ascii

$result = [ordered]@{
  env_file = $EnvFile
  neo4j = [ordered]@{
    status = $neoStatus
    uri = $neo4jUri
    port = $neo4jPort
    pid = $neoPid
    pid_file = $neoPidFile
  }
  chroma = [ordered]@{
    status = $chromaStatus
    endpoint = $chromaEndpoint
    port = $chromaPort
    pid = $chromaPid
    pid_file = $chromaPidFile
  }
}

$result | ConvertTo-Json -Depth 6
