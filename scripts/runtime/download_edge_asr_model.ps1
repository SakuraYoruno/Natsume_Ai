[CmdletBinding()]
param(
  [string]$Model = "large-v3",
  [string]$OutputDir = "D:\AI_Models\faster-whisper-large-v3",
  [string]$Revision = "main",
  [switch]$Force,
  [switch]$AutoInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$scriptPath = Join-Path $repoRoot "scripts\runtime\download_edge_asr_model.py"
if (-not (Test-Path -Path $scriptPath)) {
  throw "download_edge_asr_model.py not found: $scriptPath"
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCmd) {
  throw "python executable not found in PATH"
}

$args = @(
  $scriptPath,
  "--model", $Model,
  "--output-dir", $OutputDir,
  "--revision", $Revision
)
if ($Force.IsPresent) { $args += "--force" }
if ($AutoInstall.IsPresent) { $args += "--auto-install" }

& $pythonCmd.Source @args
if ($LASTEXITCODE -ne 0) {
  throw "ASR model download failed with exit code $LASTEXITCODE"
}
