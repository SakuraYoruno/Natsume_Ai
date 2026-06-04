param(
  [string]$SettingsPath = (Join-Path $env:USERPROFILE "AppData\LocalLow\Shinymoon\MateEngineX\settings.json"),
  [switch]$NoBackup
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -Path $SettingsPath)) {
  throw "MateEngine settings not found: $SettingsPath"
}

if (-not $NoBackup.IsPresent) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $backupPath = "$SettingsPath.natsume-stable-bak-$stamp"
  Copy-Item -LiteralPath $SettingsPath -Destination $backupPath -Force
} else {
  $backupPath = $null
}

$settings = Get-Content -Raw -LiteralPath $SettingsPath | ConvertFrom-Json

function Set-SettingValue {
  param(
    [Parameter(Mandatory = $true)][object]$Target,
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][object]$Value
  )
  $property = $Target.PSObject.Properties[$Name]
  if ($null -eq $property) {
    $Target | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
  } else {
    $property.Value = $Value
  }
}

$updates = [ordered]@{
  idleSwitchTime = 9999.0
  idleTransitionTime = 0.0
  enableDanceSwitch = $false
  enableDancing = $false
  enableRandomMessages = $false
  enableRandomAvatar = $false
  enableLocomotion = $false
  enableMouseTracking = $false
  enableIK = $false
  enableHandHolding = $false
  enableWindowSitting = $false
  headBlend = 0.0
  eyeBlend = 0.0
  spineBlend = 0.0
  enableParticles = $false
  enableMinecraftMessages = $true
}

foreach ($key in $updates.Keys) {
  Set-SettingValue -Target $settings -Name $key -Value $updates[$key]
}

$settings | ConvertTo-Json -Depth 32 | Set-Content -LiteralPath $SettingsPath -Encoding UTF8

[ordered]@{
  ok = $true
  mode = "mateengine_stable_verification"
  settings_path = $SettingsPath
  backup_path = $backupPath
  updates = $updates
} | ConvertTo-Json -Depth 8
