[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [int]$WaitSeconds = 120,
  [int]$StabilityWaitSeconds = 8,
  [switch]$ForceRestart,
  [bool]$AutoDegradeOnAsrCrash = $false,
  [bool]$EnforceCnAsrJaTtsMode = $true,
  [ValidateSet("", "uia_hook", "off")]
  [string]$WeChatInboundBackend = "",
  [string]$WeChatVisionModel = "",
  [string]$WeChatVisionApiUrl = "",
  [double]$WeChatVisionTimeoutS = 0,
  [double]$WeChatVisionMinConfidence = -1,
  [double]$WeChatVisionMinIntervalS = 0
)

$ErrorActionPreference = "Stop"

function Set-OptionalProcessEnv {
  param(
    [string]$Key,
    [object]$Value
  )
  if ($null -eq $Value) {
    return
  }
  $text = [string]$Value
  if ([string]::IsNullOrWhiteSpace($text)) {
    return
  }
  [Environment]::SetEnvironmentVariable($Key, $text, "Process")
}

$startEdgeScript = Join-Path $PSScriptRoot "start_edge.ps1"
if (-not (Test-Path -Path $startEdgeScript)) {
  throw "start_edge.ps1 not found: $startEdgeScript"
}

Set-OptionalProcessEnv -Key "EDGE_WECHAT_INBOUND_BACKEND" -Value $WeChatInboundBackend
Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_MODEL" -Value $WeChatVisionModel
Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_API_URL" -Value $WeChatVisionApiUrl
if ($WeChatVisionTimeoutS -gt 0) {
  Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_TIMEOUT_S" -Value $WeChatVisionTimeoutS
}
if ($WeChatVisionMinConfidence -ge 0) {
  Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_MIN_CONFIDENCE" -Value $WeChatVisionMinConfidence
}
if ($WeChatVisionMinIntervalS -gt 0) {
  Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_MIN_INTERVAL_S" -Value $WeChatVisionMinIntervalS
}

$edgeRaw = & $startEdgeScript `
  -EnvFile $EnvFile `
  -LogDir $LogDir `
  -WaitSeconds $WaitSeconds `
  -StabilityWaitSeconds $StabilityWaitSeconds `
  -ForceRestart:$ForceRestart.IsPresent `
  -AutoDegradeOnAsrCrash:$AutoDegradeOnAsrCrash `
  -EnforceCnAsrJaTtsMode:$EnforceCnAsrJaTtsMode

$edgeJson = ($edgeRaw | Out-String).Trim()
if (-not $edgeJson) {
  throw "start_edge.ps1 returned empty output"
}

try {
  $edgeResult = $edgeJson | ConvertFrom-Json
} catch {
  throw "failed to parse start_edge.ps1 output as JSON: $edgeJson"
}

$result = [ordered]@{
  side = "edge"
  edge = $edgeResult
}
$result | ConvertTo-Json -Depth 8
