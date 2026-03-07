[CmdletBinding()]
param(
  [switch]$SkipHost,
  [switch]$SkipEdge,
  [switch]$StopLocalServices,
  [ValidateSet("docker")]
  [string]$LocalServicesBackend = "docker",
  [string]$DockerComposeFile = "",
  [string]$DockerProjectName = "natsume-ext",
  [switch]$DockerRemoveVolumes,

  [string]$HostEnvFile = "",
  [string]$HostPidFile = "",
  [int]$HostPort = 0,

  [ValidateSet("local", "ssh")]
  [string]$EdgeMode = "local",
  [string]$EdgePidFile = "",
  [int]$EdgePort = 0,

  [string]$EdgeSshHost = "",
  [string]$EdgeSshUser = "",
  [int]$EdgeSshPort = 22,
  [string]$EdgeRepoPath = "",
  [string]$EdgeRemoteShell = "pwsh"
)

$ErrorActionPreference = "Stop"

function Invoke-JsonScript {
  param(
    [string]$ScriptPath,
    [hashtable]$ArgsMap
  )
  if (-not (Test-Path -Path $ScriptPath)) {
    throw "script not found: $ScriptPath"
  }

  $invokeArgs = @()
  foreach ($key in $ArgsMap.Keys) {
    $value = $ArgsMap[$key]
    if ($value -is [bool]) {
      if ($value) {
        $invokeArgs += "-$key"
      }
      continue
    }
    if ($null -eq $value) {
      continue
    }
    if ($value -is [string] -and [string]::IsNullOrWhiteSpace($value)) {
      continue
    }
    if ($value -is [int] -and $value -le 0) {
      continue
    }
    $invokeArgs += "-$key"
    $invokeArgs += $value
  }

  $raw = & $ScriptPath @invokeArgs
  $jsonText = ($raw | Out-String).Trim()
  if (-not $jsonText) {
    throw "script returned empty output: $ScriptPath"
  }
  try {
    return $jsonText | ConvertFrom-Json
  } catch {
    throw "failed to parse JSON output from ${ScriptPath}: $jsonText"
  }
}

function Invoke-RemoteEdgeStopViaSsh {
  param(
    [string]$SshHost,
    [string]$SshUser,
    [int]$SshPort,
    [string]$RepoPath,
    [string]$RemoteShell,
    [string]$PidFile,
    [int]$Port
  )
  if (-not $SshHost) {
    throw "EdgeSshHost is required when EdgeMode=ssh"
  }
  if (-not $RepoPath) {
    throw "EdgeRepoPath is required when EdgeMode=ssh"
  }

  $sshCmd = Get-Command ssh -ErrorAction SilentlyContinue
  if ($null -eq $sshCmd) {
    throw "ssh executable not found in PATH"
  }

  $remoteScript = "$RepoPath/scripts/runtime/stop_edge_side.ps1"
  $remoteParts = @(
    $RemoteShell,
    "-NoProfile",
    "-File",
    "`"$remoteScript`""
  )
  if ($PidFile) {
    $remoteParts += "-PidFile"
    $remoteParts += "`"$PidFile`""
  }
  if ($Port -gt 0) {
    $remoteParts += "-Port"
    $remoteParts += [string]$Port
  }
  $remoteCommand = ($remoteParts -join " ")

  $target = if ($SshUser) { "$SshUser@$SshHost" } else { $SshHost }
  $sshArgs = @()
  if ($SshPort -gt 0 -and $SshPort -ne 22) {
    $sshArgs += "-p"
    $sshArgs += [string]$SshPort
  }
  $sshArgs += $target
  $sshArgs += $remoteCommand

  $raw = & $sshCmd.Source @sshArgs
  if ($LASTEXITCODE -ne 0) {
    $stderr = ($raw | Out-String).Trim()
    throw "remote edge stop failed via ssh (exit=$LASTEXITCODE): $stderr"
  }
  $jsonText = ($raw | Out-String).Trim()
  if (-not $jsonText) {
    throw "remote edge stop returned empty output"
  }
  try {
    return $jsonText | ConvertFrom-Json
  } catch {
    throw "failed to parse remote edge JSON output: $jsonText"
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$hostStopScript = Join-Path $PSScriptRoot "stop_host_side.ps1"
$edgeStopScript = Join-Path $PSScriptRoot "stop_edge_side.ps1"
$dockerServicesStopScript = Join-Path $PSScriptRoot "stop_docker_services.ps1"

$hostResult = $null
$edgeResult = $null
$localServicesResult = $null
$errors = @()

if (-not $SkipHost.IsPresent) {
  try {
    $hostArgs = @{
      EnvFile = $HostEnvFile
      PidFile = $HostPidFile
      Port = $HostPort
    }
    $hostResult = Invoke-JsonScript -ScriptPath $hostStopScript -ArgsMap $hostArgs
  } catch {
    $errors += "host stop failed: $($_.Exception.Message)"
  }
}

if (-not $SkipEdge.IsPresent) {
  try {
    if ($EdgeMode -eq "local") {
      $edgeArgs = @{
        PidFile = $EdgePidFile
        Port = $EdgePort
      }
      $edgeResult = Invoke-JsonScript -ScriptPath $edgeStopScript -ArgsMap $edgeArgs
    } else {
      $edgeResult = Invoke-RemoteEdgeStopViaSsh `
        -SshHost $EdgeSshHost `
        -SshUser $EdgeSshUser `
        -SshPort $EdgeSshPort `
        -RepoPath $EdgeRepoPath `
        -RemoteShell $EdgeRemoteShell `
        -PidFile $EdgePidFile `
        -Port $EdgePort
    }
  } catch {
    $errors += "edge stop failed: $($_.Exception.Message)"
  }
}

if ($StopLocalServices.IsPresent) {
  try {
    $dockerArgs = @{
      ComposeFile = $DockerComposeFile
      ProjectName = $DockerProjectName
      RemoveVolumes = [bool]$DockerRemoveVolumes.IsPresent
    }
    $localServicesResult = Invoke-JsonScript -ScriptPath $dockerServicesStopScript -ArgsMap $dockerArgs
  } catch {
    $errors += "local services stop failed: $($_.Exception.Message)"
  }
}

$status = if ($errors.Count -gt 0) { "partial_failed" } else { "stopped" }

$result = [ordered]@{
  stage = "S_DISTRIBUTED_STOP"
  status = $status
  stopped_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  mode = [ordered]@{
    edge_mode = $EdgeMode
    local_services_backend = $LocalServicesBackend
  }
  host = $hostResult
  edge = $edgeResult
  local_services = $localServicesResult
  errors = $errors
}

$result | ConvertTo-Json -Depth 12
if ($errors.Count -gt 0) {
  exit 1
}
