[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [int]$WaitSeconds = 120,
  [int]$StabilityWaitSeconds = 8,
  [switch]$ForceRestart,
  [switch]$Background,
  [switch]$GatewayVoiceAsrMode,
  [switch]$ShowConsoleWindow,
  [bool]$BreakawayFromSsh = $false,
  [bool]$AutoDegradeOnAsrCrash = $false,
  [bool]$EnforceCnAsrJaTtsMode = $true,
  [ValidateSet("", "wxautox4_hook", "off")]
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

function Set-ProcessEnvValue {
  param(
    [string]$Key,
    [string]$Value
  )
  if ([string]::IsNullOrWhiteSpace($Key) -or [string]::IsNullOrWhiteSpace($Value)) {
    return
  }
  [Environment]::SetEnvironmentVariable($Key, $Value, "Process")
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$napCatCleanLauncher = Join-Path $repoRoot "scripts\runtime\launch_napcat_clean.ps1"

Set-ProcessEnvValue -Key "QQ_ONEBOT_API_BASE" -Value "http://127.0.0.1:3000"
Set-ProcessEnvValue -Key "EDGE_QQ_ONEBOT_AUTOSTART" -Value "true"
Set-ProcessEnvValue -Key "EDGE_QQ_ONEBOT_LAUNCHER_PATH" -Value $napCatCleanLauncher
Set-ProcessEnvValue -Key "EDGE_QQ_ONEBOT_LAUNCHER_ARGS" -Value "{QQ_SELF_ID}"
Set-ProcessEnvValue -Key "EDGE_QQ_ONEBOT_HEALTH_URL" -Value "http://127.0.0.1:3000/get_status"
if ($GatewayVoiceAsrMode.IsPresent) {
  # Disable resident microphone ASR and only transcribe gateway voice messages.
  Set-ProcessEnvValue -Key "EDGE_ASR_ENABLED" -Value "false"
  Set-ProcessEnvValue -Key "EDGE_GATEWAY_VOICE_ASR_ENABLED" -Value "true"
}
Set-OptionalProcessEnv -Key "EDGE_WECHAT_INBOUND_BACKEND" -Value $WeChatInboundBackend
Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_MODEL" -Value $WeChatVisionModel
Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_API_URL" -Value $WeChatVisionApiUrl
Set-OptionalProcessEnv -Key "EDGE_QQ_ONEBOT_KILL_QQ_BEFORE_START" -Value "true"
Set-OptionalProcessEnv -Key "EDGE_QQ_ONEBOT_FORCE_CLEAN_START" -Value "true"
if ($WeChatVisionTimeoutS -gt 0) {
  Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_TIMEOUT_S" -Value $WeChatVisionTimeoutS
}
if ($WeChatVisionMinConfidence -ge 0) {
  Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_MIN_CONFIDENCE" -Value $WeChatVisionMinConfidence
}
if ($WeChatVisionMinIntervalS -gt 0) {
  Set-OptionalProcessEnv -Key "EDGE_WECHAT_VISION_MIN_INTERVAL_S" -Value $WeChatVisionMinIntervalS
}

$resolvedShowConsoleWindow = $true
if ($Background.IsPresent) {
  $resolvedShowConsoleWindow = $false
} elseif ($ShowConsoleWindow.IsPresent) {
  $resolvedShowConsoleWindow = $true
}

$sshSessionDetected = (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CONNECTION)) -or (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CLIENT))
$resolvedBreakawayFromSsh = if ($Background.IsPresent -or $sshSessionDetected) { $true } else { [bool]$BreakawayFromSsh }

$edgeRaw = & $startEdgeScript `
  -EnvFile $EnvFile `
  -LogDir $LogDir `
  -WaitSeconds $WaitSeconds `
  -StabilityWaitSeconds $StabilityWaitSeconds `
  -ForceRestart:$ForceRestart.IsPresent `
  -ShowConsoleWindow:$resolvedShowConsoleWindow `
  -BreakawayFromSsh:$resolvedBreakawayFromSsh `
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
