[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [int]$WaitSeconds = 60,
  [int]$StabilityWaitSeconds = 8,
  [switch]$ForceRestart,
  [bool]$AutoDegradeOnAsrCrash = $false,
  [bool]$EnforceCnAsrJaTtsMode = $true,
  [ValidateSet("", "uia", "uia_ocr", "vision", "win_notify", "vision_notify")]
  [string]$WeChatInboundBackend = "",
  [string]$WeChatVisionModel = "",
  [string]$WeChatVisionApiUrl = "",
  [double]$WeChatVisionTimeoutS = 0,
  [double]$WeChatVisionMinConfidence = -1,
  [double]$WeChatVisionMinIntervalS = 0
)

$ErrorActionPreference = "Stop"

$startEdgeScript = Join-Path $PSScriptRoot "start_edge.ps1"
if (-not (Test-Path -Path $startEdgeScript)) {
  throw "start_edge.ps1 not found: $startEdgeScript"
}

if (-not [string]::IsNullOrWhiteSpace($WeChatInboundBackend)) {
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_INBOUND_BACKEND", $WeChatInboundBackend, "Process")
}
$resolvedVisionModel = $WeChatVisionModel
$resolvedVisionTimeoutS = $WeChatVisionTimeoutS
$resolvedVisionMinIntervalS = $WeChatVisionMinIntervalS
if ($WeChatInboundBackend -in @("vision", "vision_notify")) {
  if ([string]::IsNullOrWhiteSpace($resolvedVisionModel)) {
    # qwen2.5vl:3b is often CPU-only on edge; gemma3:4b is faster and uses GPU on common setups.
    $resolvedVisionModel = "gemma3:4b"
  }
  if ($resolvedVisionTimeoutS -le 0) {
    $resolvedVisionTimeoutS = 60
  }
  if ($resolvedVisionMinIntervalS -le 0) {
    $resolvedVisionMinIntervalS = 2.5
  }
}
if (-not [string]::IsNullOrWhiteSpace($resolvedVisionModel)) {
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_VISION_MODEL", $resolvedVisionModel, "Process")
}
if (-not [string]::IsNullOrWhiteSpace($WeChatVisionApiUrl)) {
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_VISION_API_URL", $WeChatVisionApiUrl, "Process")
}
if ($resolvedVisionTimeoutS -gt 0) {
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_VISION_TIMEOUT_S", ([string]$resolvedVisionTimeoutS), "Process")
}
if ($WeChatVisionMinConfidence -ge 0) {
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_VISION_MIN_CONFIDENCE", ([string]$WeChatVisionMinConfidence), "Process")
}
if ($resolvedVisionMinIntervalS -gt 0) {
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_VISION_MIN_INTERVAL_S", ([string]$resolvedVisionMinIntervalS), "Process")
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
