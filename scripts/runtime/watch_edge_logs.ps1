[CmdletBinding()]
param(
  [string]$LogDir = "",
  [int]$TailLines = 120,
  [switch]$VoiceOnly
)

$ErrorActionPreference = "Stop"

if ($TailLines -lt 1) {
  $TailLines = 1
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ([string]::IsNullOrWhiteSpace($LogDir)) {
  $LogDir = Join-Path $repoRoot "runtime\edge\logs"
}
if (-not (Test-Path -Path $LogDir)) {
  New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$stdoutLog = Join-Path $LogDir "edge.stdout.log"
$stderrLog = Join-Path $LogDir "edge.stderr.log"

if (-not (Test-Path -Path $stdoutLog)) {
  New-Item -ItemType File -Path $stdoutLog -Force | Out-Null
}
if (-not (Test-Path -Path $stderrLog)) {
  New-Item -ItemType File -Path $stderrLog -Force | Out-Null
}

Write-Host "Watching edge logs:"
Write-Host "  stdout: $stdoutLog"
Write-Host "  stderr: $stderrLog"
if ($VoiceOnly.IsPresent) {
  Write-Host "  filter: EDGE_VOICE_MON"
}
Write-Host ""

Get-Content -Path @($stdoutLog, $stderrLog) -Tail $TailLines -Wait |
  ForEach-Object {
    $line = [string]$_
    if ($VoiceOnly.IsPresent) {
      if ($line -match "EDGE_VOICE_MON") {
        Write-Output $line
      }
      return
    }
    Write-Output $line
  }

