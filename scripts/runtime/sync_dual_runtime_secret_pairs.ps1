[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Path,
  [string]$SourcePath = ""
)

$ErrorActionPreference = "Stop"

$resolved = [Environment]::ExpandEnvironmentVariables($Path.Trim())
if (-not [System.IO.Path]::IsPathRooted($resolved)) {
  $resolved = [System.IO.Path]::GetFullPath($resolved)
}
if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
  throw "DUAL_RUNTIME_SECRET_FILE_NOT_FOUND"
}
$resolved = (Resolve-Path -LiteralPath $resolved).Path

function Read-EnvValues {
  param([string]$EnvPath)
  $result = @{}
  foreach ($line in Get-Content -LiteralPath $EnvPath -Encoding UTF8) {
    $trimmed = ([string]$line).Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    $separator = $trimmed.IndexOf("=")
    if ($separator -lt 1) {
      continue
    }
    $name = $trimmed.Substring(0, $separator).Trim().TrimStart([char]0xFEFF)
    $value = $trimmed.Substring($separator + 1).Trim()
    if ($name) {
      $result[$name] = $value
    }
  }
  return $result
}

$pairs = @(
  @("EDGE_HMAC_SHARED_KEY", "HOST_EDGE_RPC_SHARED_KEY"),
  @("EDGE_GATEWAY_HOST_HMAC_SECRET", "HOST_EDGE_GATEWAY_HMAC_SECRET"),
  @("EDGE_GATEWAY_REPLY_HMAC_SECRET", "HOST_GATEWAY_REPLY_HMAC_SECRET")
)
$values = Read-EnvValues -EnvPath $resolved
$sourceImported = $false
if (-not [string]::IsNullOrWhiteSpace($SourcePath)) {
  $sourceResolved = [Environment]::ExpandEnvironmentVariables($SourcePath.Trim())
  if (-not [System.IO.Path]::IsPathRooted($sourceResolved)) {
    $sourceResolved = [System.IO.Path]::GetFullPath($sourceResolved)
  }
  if (-not (Test-Path -LiteralPath $sourceResolved -PathType Leaf)) {
    throw "DUAL_RUNTIME_SECRET_SOURCE_FILE_NOT_FOUND"
  }
  $sourceValues = Read-EnvValues -EnvPath $sourceResolved
  foreach ($pair in $pairs) {
    $edgeName = [string]$pair[0]
    $sourceValue = [string]$sourceValues[$edgeName]
    if ([string]::IsNullOrWhiteSpace($sourceValue)) {
      throw "DUAL_RUNTIME_SECRET_SOURCE_PAIR_MISSING: $edgeName"
    }
    $values[$edgeName] = $sourceValue
  }
  $sourceImported = $true
}

$updated = @()
foreach ($pair in $pairs) {
  $edgeName = [string]$pair[0]
  $hostName = [string]$pair[1]
  $edgeValue = [string]$values[$edgeName]
  $hostValue = [string]$values[$hostName]
  if ([string]::IsNullOrWhiteSpace($edgeValue)) {
    if ([string]::IsNullOrWhiteSpace($hostValue)) {
      throw "DUAL_RUNTIME_SECRET_PAIR_MISSING: $edgeName<->$hostName"
    }
    $edgeValue = $hostValue
    $values[$edgeName] = $edgeValue
    $updated += $edgeName
  }
  if ([Text.Encoding]::UTF8.GetByteCount($edgeValue) -lt 32) {
    throw "DUAL_RUNTIME_SECRET_TOO_SHORT: $edgeName"
  }
  if (-not [string]::Equals(
      $hostValue,
      $edgeValue,
      [System.StringComparison]::Ordinal
    )) {
    $values[$hostName] = $edgeValue
    $updated += $hostName
  }
}

$temporary = "$resolved.tmp.$PID.$([Guid]::NewGuid().ToString('N'))"
$encoding = New-Object System.Text.UTF8Encoding($false)
try {
  $lines = @(
    $values.Keys |
      Sort-Object |
      ForEach-Object { "{0}={1}" -f $_, ([string]$values[$_]) }
  )
  [System.IO.File]::WriteAllLines($temporary, $lines, $encoding)
  Move-Item -LiteralPath $temporary -Destination $resolved -Force
  $principal = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
  $grant = "{0}:(F)" -f $principal
  & icacls.exe $resolved /inheritance:r /grant:r $grant | Out-Null
  if ($LASTEXITCODE -ne 0) {
    throw "DUAL_RUNTIME_SECRET_ACL_FAILED"
  }
} finally {
  Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
}

[ordered]@{
  status = "synchronized"
  path = $resolved
  pair_count = $pairs.Count
  source_imported = $sourceImported
  updated_names = @($updated | Sort-Object -Unique)
  secret_count = $values.Count
} | ConvertTo-Json -Depth 3
