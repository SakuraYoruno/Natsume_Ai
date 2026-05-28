[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$PackagePath,

  [Parameter(Mandatory = $true)]
  [string]$EdgeSshHost,

  [string]$EdgeSshUser = "",
  [int]$EdgeSshPort = 22,

  [Parameter(Mandatory = $true)]
  [string]$EdgeModelRoot,

  [string]$RemoteShell = "pwsh",
  [string]$RemoteTempDir = "",
  [switch]$NoExpand
)

$ErrorActionPreference = "Stop"

function Resolve-ToolOrThrow {
  param([string]$Name)
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) {
    throw "$Name was not found in PATH"
  }
  return $cmd.Source
}

$resolvedPackage = (Resolve-Path -LiteralPath $PackagePath).Path
$packageName = Split-Path -Leaf $resolvedPackage
$target = if ($EdgeSshUser) { "$EdgeSshUser@$EdgeSshHost" } else { $EdgeSshHost }
$remoteZip = if ([string]::IsNullOrWhiteSpace($RemoteTempDir)) {
  $packageName
} else {
  ($RemoteTempDir.TrimEnd("\", "/") + "/" + $packageName)
}

$scp = Resolve-ToolOrThrow -Name "scp"
$ssh = Resolve-ToolOrThrow -Name "ssh"

$scpArgs = @()
if ($EdgeSshPort -gt 0 -and $EdgeSshPort -ne 22) {
  $scpArgs += "-P"
  $scpArgs += [string]$EdgeSshPort
}
$scpArgs += $resolvedPackage
$scpArgs += "${target}:$remoteZip"
& $scp @scpArgs
if ($LASTEXITCODE -ne 0) {
  throw "scp upload failed with exit code $LASTEXITCODE"
}

$expanded = $false
if (-not $NoExpand) {
  $remoteCommand = @(
    $RemoteShell,
    "-NoProfile",
    "-Command",
    "`"New-Item -ItemType Directory -Force -Path '$EdgeModelRoot' | Out-Null; Expand-Archive -LiteralPath '$remoteZip' -DestinationPath '$EdgeModelRoot' -Force; Remove-Item -LiteralPath '$remoteZip' -Force`""
  ) -join " "

  $sshArgs = @()
  if ($EdgeSshPort -gt 0 -and $EdgeSshPort -ne 22) {
    $sshArgs += "-p"
    $sshArgs += [string]$EdgeSshPort
  }
  $sshArgs += $target
  $sshArgs += $remoteCommand
  & $ssh @sshArgs
  if ($LASTEXITCODE -ne 0) {
    throw "remote package expansion failed with exit code $LASTEXITCODE"
  }
  $expanded = $true
}

[pscustomobject]@{
  success = $true
  package = $resolvedPackage
  target = $target
  remote_zip = $remoteZip
  edge_model_root = $EdgeModelRoot
  expanded = $expanded
  next_env = @{
    EDGE_TTS_MODEL_PATH = $EdgeModelRoot
  }
} | ConvertTo-Json -Depth 5
