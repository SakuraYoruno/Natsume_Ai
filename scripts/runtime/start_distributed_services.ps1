[CmdletBinding()]
param(
  [string]$HostEnvFile = "",
  [string]$HostLogDir = "",
  [string]$HostBindHost = "",
  [int]$HostPort = 0,
  [int]$HostWaitSeconds = 40,
  [switch]$HostForceRestart,
  [switch]$HostStartupCheck,

  [ValidateSet("local", "ssh")]
  [string]$EdgeMode = "local",
  [string]$EdgeEnvFile = "",
  [string]$EdgeLogDir = "",
  [int]$EdgeWaitSeconds = 20,
  [switch]$EdgeForceRestart,

  [string]$EdgeSshHost = "",
  [string]$EdgeSshUser = "",
  [int]$EdgeSshPort = 22,
  [string]$EdgeRepoPath = "",
  [string]$EdgeRemoteShell = "pwsh",

  [string]$EdgeHealthUrl = "",
  [int]$EdgeHealthWaitSeconds = 20,
  [switch]$RollbackHostOnEdgeFailure
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

function Start-EdgeRemoteViaSsh {
  param(
    [string]$SshHost,
    [string]$SshUser,
    [int]$SshPort,
    [string]$RepoPath,
    [string]$RemoteShell,
    [string]$EnvFile,
    [string]$LogDir,
    [int]$WaitSeconds,
    [bool]$ForceRestart
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

  $remoteScript = "$RepoPath/scripts/runtime/start_edge_side.ps1"
  $remoteParts = @(
    $RemoteShell,
    "-NoProfile",
    "-File",
    "`"$remoteScript`""
  )

  if ($EnvFile) {
    $remoteParts += "-EnvFile"
    $remoteParts += "`"$EnvFile`""
  }
  if ($LogDir) {
    $remoteParts += "-LogDir"
    $remoteParts += "`"$LogDir`""
  }
  if ($WaitSeconds -gt 0) {
    $remoteParts += "-WaitSeconds"
    $remoteParts += [string]$WaitSeconds
  }
  if ($ForceRestart) {
    $remoteParts += "-ForceRestart"
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
    throw "remote edge start failed via ssh (exit=$LASTEXITCODE): $stderr"
  }
  $jsonText = ($raw | Out-String).Trim()
  if (-not $jsonText) {
    throw "remote edge start returned empty output"
  }
  try {
    return $jsonText | ConvertFrom-Json
  } catch {
    throw "failed to parse remote edge JSON output: $jsonText"
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$hostStartScript = Join-Path $PSScriptRoot "start_host_side.ps1"
$hostStopScript = Join-Path $PSScriptRoot "stop_host_side.ps1"
$edgeStartScript = Join-Path $PSScriptRoot "start_edge_side.ps1"

$hostArgs = @{
  EnvFile = $HostEnvFile
  LogDir = $HostLogDir
  BindHost = $HostBindHost
  Port = $HostPort
  WaitSeconds = $HostWaitSeconds
  ForceRestart = [bool]$HostForceRestart.IsPresent
  StartupCheck = [bool]$HostStartupCheck.IsPresent
}
$hostResult = Invoke-JsonScript -ScriptPath $hostStartScript -ArgsMap $hostArgs

$edgeResult = $null
$edgeError = $null
try {
  if ($EdgeMode -eq "local") {
    $edgeArgs = @{
      EnvFile = $EdgeEnvFile
      LogDir = $EdgeLogDir
      WaitSeconds = $EdgeWaitSeconds
      ForceRestart = [bool]$EdgeForceRestart.IsPresent
    }
    $edgeResult = Invoke-JsonScript -ScriptPath $edgeStartScript -ArgsMap $edgeArgs
  } else {
    $edgeResult = Start-EdgeRemoteViaSsh `
      -SshHost $EdgeSshHost `
      -SshUser $EdgeSshUser `
      -SshPort $EdgeSshPort `
      -RepoPath $EdgeRepoPath `
      -RemoteShell $EdgeRemoteShell `
      -EnvFile $EdgeEnvFile `
      -LogDir $EdgeLogDir `
      -WaitSeconds $EdgeWaitSeconds `
      -ForceRestart $EdgeForceRestart.IsPresent
  }

  if ($EdgeHealthUrl) {
    $ok = Wait-Http -Url $EdgeHealthUrl -TimeoutSeconds $EdgeHealthWaitSeconds
    if (-not $ok) {
      throw "edge health check timeout: $EdgeHealthUrl"
    }
  }
} catch {
  $edgeError = $_.Exception.Message
  if ($RollbackHostOnEdgeFailure.IsPresent) {
    try {
      $rollbackArgs = @{
        EnvFile = $HostEnvFile
        Port = $HostPort
      }
      [void](Invoke-JsonScript -ScriptPath $hostStopScript -ArgsMap $rollbackArgs)
    } catch {
    }
  }
}

$status = "started"
if ($edgeError) {
  $status = "partial_failed"
}

$result = [ordered]@{
  stage = "S_DISTRIBUTED_START"
  status = $status
  started_at_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  mode = [ordered]@{
    edge_mode = $EdgeMode
  }
  host = $hostResult
  edge = $edgeResult
  edge_error = $edgeError
}

$result | ConvertTo-Json -Depth 12
if ($edgeError) {
  exit 1
}
