[CmdletBinding()]
param(
  [string]$RepoRoot = '',
  [string]$EnvFile = ''
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
  param([string]$Preferred)
  if (-not [string]::IsNullOrWhiteSpace($Preferred)) {
    return (Resolve-Path $Preferred).Path
  }
  return (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

function Read-EnvFile {
  param([string]$Path)
  $values = @{}
  if (-not (Test-Path -Path $Path)) {
    return $values
  }
  foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
    $raw = [string]$line
    $trimmed = $raw.Trim()
    if (-not $trimmed -or $trimmed.StartsWith('#')) {
      continue
    }
    $sep = $trimmed.IndexOf('=')
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
    [Environment]::SetEnvironmentVariable($key, [string]$Values[$key], 'Process')
  }
}

$RepoRoot = Resolve-RepoRoot -Preferred $RepoRoot
$qqOneBotManagerScript = Join-Path $PSScriptRoot 'qq_onebot_manager.ps1'
if (-not (Test-Path -Path $qqOneBotManagerScript)) {
  throw "qq_onebot_manager.ps1 not found: $qqOneBotManagerScript"
}
. $qqOneBotManagerScript

if ([string]::IsNullOrWhiteSpace($EnvFile)) {
  $defaultEnv = Join-Path $RepoRoot '.env'
  $fallbackEnv = Join-Path $RepoRoot '.env.example'
  if (Test-Path -Path $defaultEnv) {
    $EnvFile = $defaultEnv
  } else {
    $EnvFile = $fallbackEnv
  }
}

$envValues = Read-EnvFile -Path $EnvFile
Set-ProcessEnv -Values $envValues
$result = Ensure-QQOneBotRunning -EnvValues $envValues -RuntimeRoot (Join-Path $RepoRoot 'runtime\edge')
$result | ConvertTo-Json -Depth 8

if (@('autostart_failed', 'autostart_timeout') -contains ([string]$result.status)) {
  exit 1
}
exit 0
