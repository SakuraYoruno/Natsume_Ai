[CmdletBinding()]
param(
  [string]$PidFile = "",
  [int]$Port = 0
)

$ErrorActionPreference = "Stop"

$stopEdgeScript = Join-Path $PSScriptRoot "stop_edge.ps1"
if (-not (Test-Path -Path $stopEdgeScript)) {
  throw "stop_edge.ps1 not found: $stopEdgeScript"
}

$invokeArgs = @{}
if ($PidFile) {
  $invokeArgs["PidFile"] = $PidFile
}
if ($Port -gt 0) {
  $invokeArgs["Port"] = $Port
}

$edgeRaw = & $stopEdgeScript @invokeArgs
$edgeJson = ($edgeRaw | Out-String).Trim()
if (-not $edgeJson) {
  throw "stop_edge.ps1 returned empty output"
}

try {
  $edgeResult = $edgeJson | ConvertFrom-Json
} catch {
  throw "failed to parse stop_edge.ps1 output as JSON: $edgeJson"
}

$result = [ordered]@{
  side = "edge"
  edge = $edgeResult
}
$result | ConvertTo-Json -Depth 8
