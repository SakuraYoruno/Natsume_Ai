param(
    [string]$ModelPath = $env:VRM_MODEL_PATH,
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 18760,
    [switch]$Install
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$BridgeDir = Join-Path $RepoRoot "tools\vrm_bridge"

if (-not (Test-Path -LiteralPath $BridgeDir)) {
    throw "VRM bridge directory not found: $BridgeDir"
}

if (-not $ModelPath) {
    $FallbackModel = Join-Path $BridgeDir "public\models\aimis.vrm"
    if (Test-Path -LiteralPath $FallbackModel) {
        $ModelPath = $FallbackModel
    }
}

if ($ModelPath -and -not (Test-Path -LiteralPath $ModelPath)) {
    throw "VRM model not found: $ModelPath"
}

if ($Install -or -not (Test-Path -LiteralPath (Join-Path $BridgeDir "node_modules"))) {
    Push-Location $BridgeDir
    try {
        npm install
    }
    finally {
        Pop-Location
    }
}

$env:VRM_BRIDGE_HOST = $HostAddress
$env:VRM_BRIDGE_PORT = [string]$Port
if ($ModelPath) {
    $env:VRM_MODEL_PATH = $ModelPath
}

Push-Location $BridgeDir
try {
    npm run dev
}
finally {
    Pop-Location
}
