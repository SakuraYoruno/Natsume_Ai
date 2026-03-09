[CmdletBinding()]
param(
  [string]$ComposeFile = "",
  [string]$ProjectName = "natsume-ext",
  [switch]$RemoveVolumes
)

$ErrorActionPreference = "Stop"

function Resolve-DockerExecutable {
  $cmd = Get-Command docker -ErrorAction SilentlyContinue
  if ($null -ne $cmd) {
    return [string]$cmd.Source
  }

  $candidates = @("C:\Program Files\Docker\Docker\resources\bin\docker.exe")
  if ($env:ProgramFiles) {
    $candidates += Join-Path $env:ProgramFiles "Docker\Docker\resources\bin\docker.exe"
  }
  if ($env:ProgramW6432) {
    $candidates += Join-Path $env:ProgramW6432 "Docker\Docker\resources\bin\docker.exe"
  }
  if ($env:LOCALAPPDATA) {
    $candidates += Join-Path $env:LOCALAPPDATA "Programs\Docker\Docker\resources\bin\docker.exe"
  }

  foreach ($candidate in ($candidates | Select-Object -Unique)) {
    if ($candidate -and (Test-Path -Path $candidate)) {
      return [string]$candidate
    }
  }
  throw "docker executable not found in PATH or common install paths"
}

function Ensure-PathContains {
  param([string]$Directory)
  if (-not $Directory) {
    return
  }
  $current = [string]$env:PATH
  $parts = @()
  if ($current) {
    $parts = $current -split ";"
  }
  foreach ($part in $parts) {
    if ([string]::Equals($part.Trim(), $Directory.Trim(), [System.StringComparison]::OrdinalIgnoreCase)) {
      return
    }
  }
  if ($current) {
    $env:PATH = "$Directory;$current"
  } else {
    $env:PATH = $Directory
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $ComposeFile) {
  $ComposeFile = Join-Path $PSScriptRoot "docker-compose.external.yml"
}
if (-not (Test-Path -Path $ComposeFile)) {
  throw "docker compose file not found: $ComposeFile"
}

$dockerExe = Resolve-DockerExecutable
$dockerDir = Split-Path -Path $dockerExe -Parent
Ensure-PathContains -Directory $dockerDir

$args = @("compose", "-f", $ComposeFile, "-p", $ProjectName, "down")
if ($RemoveVolumes.IsPresent) {
  $args += "--volumes"
}
& $dockerExe @args
if ($LASTEXITCODE -ne 0) {
  throw "docker compose down failed (exit=$LASTEXITCODE)"
}

$result = [ordered]@{
  backend = "docker"
  status = "stopped"
  compose_file = $ComposeFile
  project_name = $ProjectName
  remove_volumes = [bool]$RemoveVolumes.IsPresent
}
$result | ConvertTo-Json -Depth 6
