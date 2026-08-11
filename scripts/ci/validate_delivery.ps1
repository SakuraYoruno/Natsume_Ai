param(
    [string]$BaseRef = "origin/dev",
    [string]$PrBodyFile = "",
    [string]$PrTitle = "",
    [switch]$RequirePrMetadata
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$sha = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sha)) {
    throw "无法解析当前 commit SHA"
}

$reportDirectory = Join-Path $repoRoot "artifacts\local-gates\$sha"
New-Item -ItemType Directory -Force -Path $reportDirectory | Out-Null
$reportFile = Join-Path $reportDirectory "change-metadata.json"

$arguments = @(
    "tools/delivery/change_metadata_gate.py",
    "--root", $repoRoot,
    "--base-ref", $BaseRef,
    "--head-ref", "HEAD",
    "--report-file", $reportFile
)
if (-not [string]::IsNullOrWhiteSpace($PrBodyFile)) {
    $arguments += @("--pr-body-file", $PrBodyFile)
}
if (-not [string]::IsNullOrWhiteSpace($PrTitle)) {
    $arguments += @("--pr-title", $PrTitle)
}
if ($RequirePrMetadata) {
    $arguments += "--require-pr-metadata"
}

& python @arguments
if ($LASTEXITCODE -ne 0) {
    throw "交付元数据门禁失败；报告：$reportFile"
}

Write-Host "交付元数据门禁通过：$reportFile"
