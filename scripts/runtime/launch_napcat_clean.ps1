$ErrorActionPreference = 'Stop'

$QQSelfId = if ($args.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace([string]$args[0])) {
  [string]$args[0]
} else {
  '230749497'
}

$preferredRoot = [string]$env:EDGE_NAPCAT_SHELL_ROOT
$candidateRoots = @()
if (-not [string]::IsNullOrWhiteSpace($preferredRoot)) {
  $candidateRoots += $preferredRoot
}
$candidateRoots += @(
  'D:\Apps\NapCat.Shell.clean',
  'D:\Apps\NapCat.Shell'
)

$napCatRoot = ''
foreach ($candidateRoot in $candidateRoots) {
  if ([string]::IsNullOrWhiteSpace($candidateRoot)) {
    continue
  }
  $expandedRoot = [Environment]::ExpandEnvironmentVariables($candidateRoot.Trim())
  $launcher = Join-Path $expandedRoot 'launcher-user.bat'
  if (Test-Path -Path $launcher) {
    $napCatRoot = $expandedRoot
    break
  }
}

if ([string]::IsNullOrWhiteSpace($napCatRoot)) {
  throw ('NapCat launcher-user.bat not found in: {0}' -f (($candidateRoots | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) }) -join ', '))
}

Set-Location $napCatRoot
Remove-Item (Join-Path $napCatRoot 'cache\qrcode.png') -Force -ErrorAction SilentlyContinue
& (Join-Path $napCatRoot 'launcher-user.bat') $QQSelfId
exit $LASTEXITCODE
