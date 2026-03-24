[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [int]$WaitSeconds = 120,
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
  Write-Warning "WeChatInboundBackend is deprecated and ignored (Unified Message Gateway mode only)."
}
if (-not [string]::IsNullOrWhiteSpace($WeChatVisionModel) -or
    -not [string]::IsNullOrWhiteSpace($WeChatVisionApiUrl) -or
    $WeChatVisionTimeoutS -gt 0 -or
    $WeChatVisionMinConfidence -ge 0 -or
    $WeChatVisionMinIntervalS -gt 0) {
  Write-Warning "Legacy WeChat vision/OCR parameters are ignored in Gateway DB-listen mode."
}

# Strict doc mode: force-disable legacy UIA bridge and listener chain.
[Environment]::SetEnvironmentVariable("EDGE_WECHAT_BRIDGE_ENABLED", "false", "Process")
[Environment]::SetEnvironmentVariable("EDGE_WECHAT_LISTENER_ENABLED", "false", "Process")
[Environment]::SetEnvironmentVariable("EDGE_WECHAT_INBOUND_BACKEND", "", "Process")

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
