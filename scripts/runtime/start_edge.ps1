[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$LogDir = "",
  [int]$WaitSeconds = 20,
  [switch]$ForceRestart,
  [switch]$ShowConsoleWindow,
  [int]$StabilityWaitSeconds = 8,
  [bool]$AutoDegradeOnAsrCrash = $true,
  [bool]$BreakawayFromSsh = $true,
  [bool]$EnforceCnAsrJaTtsMode = $true
)

$ErrorActionPreference = "Stop"

function Read-EnvFile {
  param([string]$Path)
  $values = @{}
  if (-not (Test-Path -Path $Path)) {
    return $values
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  $lines = [System.IO.File]::ReadAllLines($Path, $utf8NoBom)
  foreach ($line in $lines) {
    $raw = [string]$line
    $trimmed = $raw.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    $sep = $trimmed.IndexOf("=")
    if ($sep -lt 1) {
      continue
    }
    $key = $trimmed.Substring(0, $sep).Trim().TrimStart([char]0xFEFF)
    $value = $trimmed.Substring($sep + 1).Trim()
    if ($key) {
      $values[$key] = $value
    }
  }
  return $values
}

function Set-ProcessEnv {
  param([hashtable]$Values)
  foreach ($key in $Values.Keys) {
    [Environment]::SetEnvironmentVariable($key, [string]$Values[$key], "Process")
  }
}

function Ensure-ProcessEnvDefault {
  param(
    [string]$Key,
    [string]$DefaultValue
  )
  $current = [Environment]::GetEnvironmentVariable($Key, "Process")
  if (-not [string]::IsNullOrWhiteSpace([string]$current)) {
    return $false
  }
  [Environment]::SetEnvironmentVariable($Key, [string]$DefaultValue, "Process")
  return $true
}

function Get-NvidiaCudaDllDirs {
  param([string]$PythonRoot)
  $dirs = New-Object System.Collections.Generic.List[string]
  if (-not $PythonRoot) {
    return @()
  }
  $sitePackages = Join-Path $PythonRoot "Lib\site-packages"
  $candidates = @(
    (Join-Path $sitePackages "nvidia\cublas\bin"),
    (Join-Path $sitePackages "nvidia\cudnn\bin")
  )
  foreach ($candidate in $candidates) {
    if (-not $candidate) {
      continue
    }
    if (Test-Path -Path $candidate) {
      $dirs.Add((Resolve-Path -Path $candidate).Path) | Out-Null
    }
  }
  return @($dirs)
}

function Merge-PathEntries {
  param(
    [string]$CurrentPath,
    [string[]]$ExtraEntries
  )
  $merged = New-Object System.Collections.Generic.List[string]
  $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)

  if ($CurrentPath) {
    foreach ($segment in $CurrentPath.Split(';')) {
      $item = [string]$segment
      if (-not $item) {
        continue
      }
      $trimmed = $item.Trim()
      if (-not $trimmed) {
        continue
      }
      if ($seen.Add($trimmed)) {
        $merged.Add($trimmed) | Out-Null
      }
    }
  }

  foreach ($entry in ($ExtraEntries | Where-Object { $_ })) {
    $trimmed = ([string]$entry).Trim()
    if (-not $trimmed) {
      continue
    }
    if ($seen.Add($trimmed)) {
      $merged.Add($trimmed) | Out-Null
    }
  }

  return ($merged -join ';')
}

function Ensure-Directory {
  param([string]$Path)
  if (-not (Test-Path -Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function Get-ListeningPid {
  param([int]$Port)
  $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Wait-Port {
  param(
    [int]$Port,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (Get-ListeningPid -Port $Port) {
      return $true
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Start-EdgeProcess {
  param(
    [string]$PythonPath,
    [string]$RepoRoot,
    [string]$StdoutLog,
    [string]$StderrLog,
    [bool]$ShowConsoleWindow,
    [bool]$UseBreakawayLaunch = $false,
    [string]$LauncherScriptPath = "",
    [int]$Port = 0,
    [hashtable]$EnvVars = @{}
  )
  if ($UseBreakawayLaunch) {
    if (-not $LauncherScriptPath) {
      throw "launcher script path is required for breakaway launch"
    }
    $launcherLines = @(
      "@echo off",
      "cd /d `"$RepoRoot`"",
      "setlocal"
    )
    foreach ($envKey in $EnvVars.Keys) {
      $envName = ([string]$envKey).Trim()
      if (-not $envName) {
        continue
      }
      $envValue = [string]$EnvVars[$envKey]
      $safeValue = $envValue -replace '%', '%%'
      $launcherLines += ("set `"{0}={1}`"" -f $envName, $safeValue)
    }
    $launcherLines += "`"$PythonPath`" -m edge_client 1>>`"$StdoutLog`" 2>>`"$StderrLog`""
    $launcherLines += "endlocal"
    Set-Content -Path $LauncherScriptPath -Value $launcherLines -Encoding ascii

    $schtasksCmd = Get-Command schtasks.exe -ErrorAction SilentlyContinue
    if ($null -eq $schtasksCmd) {
      throw "schtasks.exe not found; cannot launch edge in breakaway mode"
    }
    $taskName = "NatsumeEdgeLaunch_$([Guid]::NewGuid().ToString('N').Substring(0, 10))"
    $taskTime = (Get-Date).AddMinutes(2).ToString("HH:mm")
    $taskCommand = "`"$LauncherScriptPath`""

    $createOutput = & $schtasksCmd.Source /Create /TN $taskName /SC ONCE /ST $taskTime /TR $taskCommand /F 2>&1
    if ($LASTEXITCODE -ne 0) {
      $detail = (($createOutput | ForEach-Object { [string]$_ }) -join "`n")
      throw "failed to create breakaway task $taskName.`n$detail"
    }
    $runOutput = & $schtasksCmd.Source /Run /TN $taskName 2>&1
    if ($LASTEXITCODE -ne 0) {
      $detail = (($runOutput | ForEach-Object { [string]$_ }) -join "`n")
      & $schtasksCmd.Source /Delete /TN $taskName /F 2>&1 | Out-Null
      throw "failed to run breakaway task $taskName.`n$detail"
    }
    & $schtasksCmd.Source /Delete /TN $taskName /F 2>&1 | Out-Null

    if ($Port -gt 0) {
      $deadline = (Get-Date).AddSeconds(2)
      while ((Get-Date) -lt $deadline) {
        $detectedPid = Get-ListeningPid -Port $Port
        if ($null -ne $detectedPid) {
          return (Get-Process -Id $detectedPid -ErrorAction SilentlyContinue)
        }
        Start-Sleep -Milliseconds 200
      }
    }
    return [pscustomobject]@{ Id = 0 }
  }

  $startProcessArgs = @{
    FilePath = $PythonPath
    ArgumentList = @("-m", "edge_client")
    WorkingDirectory = $RepoRoot
    PassThru = $true
    RedirectStandardOutput = $StdoutLog
    RedirectStandardError = $StderrLog
  }
  if (-not $ShowConsoleWindow) {
    $startProcessArgs["WindowStyle"] = "Hidden"
  }
  return (Start-Process @startProcessArgs)
}

function Wait-Process-And-Port-Stable {
  param(
    [int]$ProcessId,
    [int]$Port,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds([Math]::Max(0, $TimeoutSeconds))
  while ((Get-Date) -lt $deadline) {
    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
      return $false
    }
    $listeningPid = Get-ListeningPid -Port $Port
    if ($null -eq $listeningPid) {
      return $false
    }
    Start-Sleep -Milliseconds 500
  }
  return $true
}

function Wait-HttpHealthy {
  param(
    [string]$Url,
    [int]$TimeoutSeconds = 8
  )
  $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
        return $true
      }
    } catch {
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Parse-Bool {
  param(
    [object]$Value,
    [bool]$Default = $false
  )
  if ($null -eq $Value) {
    return [bool]$Default
  }
  $text = ([string]$Value).Trim().ToLowerInvariant()
  if (-not $text) {
    return [bool]$Default
  }
  if ($text -in @("1", "true", "yes", "on")) {
    return $true
  }
  if ($text -in @("0", "false", "no", "off")) {
    return $false
  }
  return [bool]$Default
}

function Get-EnvValueCaseInsensitive {
  param([hashtable]$Values, [string]$Key)
  if (-not $Values) { return $null }
  $lower = $Key.ToLowerInvariant()
  foreach ($k in $Values.Keys) {
    if ([string]$k -eq $Key -or ([string]$k).ToLowerInvariant() -eq $lower) {
      return $Values[$k]
    }
  }
  return $null
}

function Start-VtsViaSteam {
  param(
    [string]$SteamAppId
  )
  if (-not $SteamAppId) {
    return [ordered]@{
      status = "launch_failed"
      detail = "EDGE_VTS_STEAM_APP_ID is empty"
      steam_uri = $null
    }
  }

  $steamUri = "steam://rungameid/$SteamAppId"

  try {
    Start-Process -FilePath $steamUri -ErrorAction Stop | Out-Null
    return [ordered]@{
      status = "launched"
      detail = "launched via steam protocol handler"
      steam_uri = $steamUri
    }
  } catch {
    $firstError = $_.Exception.Message
    try {
      Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", "start", "", $steamUri) -WindowStyle Hidden -ErrorAction Stop | Out-Null
      return [ordered]@{
        status = "launched"
        detail = "launched via cmd start fallback"
        steam_uri = $steamUri
      }
    } catch {
      $secondError = $_.Exception.Message
      return [ordered]@{
        status = "launch_failed"
        detail = "steam launch failed: $firstError | fallback failed: $secondError"
        steam_uri = $steamUri
      }
    }
  }
}

function Resolve-VtsExecutablePath {
  param([hashtable]$EnvValues)

  $candidates = New-Object System.Collections.Generic.List[string]

  if ($EnvValues.ContainsKey("EDGE_VTS_EXECUTABLE_PATH")) {
    $candidate = [string]$EnvValues["EDGE_VTS_EXECUTABLE_PATH"]
    if (-not [string]::IsNullOrWhiteSpace($candidate)) {
      [void]$candidates.Add($candidate.Trim())
    }
  } elseif (-not [string]::IsNullOrWhiteSpace($env:EDGE_VTS_EXECUTABLE_PATH)) {
    [void]$candidates.Add($env:EDGE_VTS_EXECUTABLE_PATH.Trim())
  }

  $steamRoots = New-Object System.Collections.Generic.List[string]
  foreach ($regPath in @(
    "Registry::HKEY_CURRENT_USER\Software\Valve\Steam",
    "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Valve\Steam",
    "Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Valve\Steam"
  )) {
    try {
      $item = Get-ItemProperty -Path $regPath -ErrorAction Stop
      foreach ($prop in @("SteamPath", "InstallPath")) {
        $value = [string]$item.$prop
        if (-not [string]::IsNullOrWhiteSpace($value)) {
          [void]$steamRoots.Add($value.Trim())
        }
      }
    } catch {
    }
  }

  foreach ($fallbackRoot in @(
    "C:\Program Files (x86)\Steam",
    "D:\Program Files (x86)\Steam",
    "C:\Program Files\Steam",
    "D:\Program Files\Steam"
  )) {
    [void]$steamRoots.Add($fallbackRoot)
  }

  $uniqueSteamRoots = $steamRoots | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
  foreach ($steamRoot in $uniqueSteamRoots) {
    [void]$candidates.Add((Join-Path $steamRoot "steamapps\common\VTube Studio\VTube Studio.exe"))
    [void]$candidates.Add((Join-Path $steamRoot "steamapps\common\VTube Studio\VTubeStudio.exe"))
  }

  $uniqueCandidates = $candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
  foreach ($candidatePath in $uniqueCandidates) {
    if (Test-Path -Path $candidatePath) {
      return (Resolve-Path -Path $candidatePath).Path
    }
  }

  return $null
}

function Start-VtsViaExecutable {
  param([string]$ExecutablePath)

  if ([string]::IsNullOrWhiteSpace($ExecutablePath)) {
    return [ordered]@{
      status = "launch_failed"
      detail = "vts executable path is empty"
      executable_path = $null
    }
  }

  if (-not (Test-Path -Path $ExecutablePath)) {
    return [ordered]@{
      status = "launch_failed"
      detail = "vts executable not found: $ExecutablePath"
      executable_path = $ExecutablePath
    }
  }

  try {
    Start-Process -FilePath $ExecutablePath -ErrorAction Stop | Out-Null
    return [ordered]@{
      status = "launched"
      detail = "launched via direct executable path"
      executable_path = $ExecutablePath
    }
  } catch {
    return [ordered]@{
      status = "launch_failed"
      detail = "direct executable launch failed: $($_.Exception.Message)"
      executable_path = $ExecutablePath
    }
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $EnvFile) {
  $defaultEnv = Join-Path $repoRoot ".env"
  $fallbackEnv = Join-Path $repoRoot ".env.example"
  if (Test-Path -Path $defaultEnv) {
    $EnvFile = $defaultEnv
  } else {
    $EnvFile = $fallbackEnv
  }
}

$runtimeRoot = Join-Path $repoRoot "runtime\edge"
Ensure-Directory -Path $runtimeRoot

if (-not $LogDir) {
  $LogDir = Join-Path $runtimeRoot "logs"
}
Ensure-Directory -Path $LogDir

$pidFile = Join-Path $runtimeRoot "edge.pid"
$stdoutLog = Join-Path $LogDir "edge.stdout.log"
$stderrLog = Join-Path $LogDir "edge.stderr.log"
$launcherScriptPath = Join-Path $runtimeRoot "launch_edge.cmd"
$sshSessionDetected = (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CONNECTION)) -or (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CLIENT))
$useBreakawayLauncher = [bool]($BreakawayFromSsh -and $sshSessionDetected)

$envValues = Read-EnvFile -Path $EnvFile
Set-ProcessEnv -Values $envValues
$startupPreWarnings = @()
$startupEnvDefaultsApplied = @()
$startupEnvEnforcedApplied = @()

if ($EnforceCnAsrJaTtsMode) {
  $enforcedVoiceEnv = [ordered]@{
    EDGE_ASR_LANGUAGE = "zh"
    EDGE_TTS_LANGUAGE = "ja"
    EDGE_BARGE_IN_ENABLED = "false"
  }
  foreach ($enforcedKey in $enforcedVoiceEnv.Keys) {
    $desiredValue = [string]$enforcedVoiceEnv[$enforcedKey]
    $existingValue = [string]([Environment]::GetEnvironmentVariable($enforcedKey, "Process"))
    [Environment]::SetEnvironmentVariable($enforcedKey, $desiredValue, "Process")
    $envValues[$enforcedKey] = $desiredValue
    if ($existingValue.Trim().ToLowerInvariant() -ne $desiredValue.ToLowerInvariant()) {
      $startupEnvEnforcedApplied += "$enforcedKey=$desiredValue"
      if (-not [string]::IsNullOrWhiteSpace($existingValue)) {
        if (-not ($startupPreWarnings -contains "VOICE_MODE_ENV_OVERRIDDEN")) {
          $startupPreWarnings += "VOICE_MODE_ENV_OVERRIDDEN"
        }
      }
    }
  }
} else {
  if (Ensure-ProcessEnvDefault -Key "EDGE_ASR_LANGUAGE" -DefaultValue "zh") {
    $envValues["EDGE_ASR_LANGUAGE"] = "zh"
    $startupEnvDefaultsApplied += "EDGE_ASR_LANGUAGE=zh"
  }
  if (Ensure-ProcessEnvDefault -Key "EDGE_TTS_LANGUAGE" -DefaultValue "ja") {
    $envValues["EDGE_TTS_LANGUAGE"] = "ja"
    $startupEnvDefaultsApplied += "EDGE_TTS_LANGUAGE=ja"
  }
  if (Ensure-ProcessEnvDefault -Key "EDGE_BARGE_IN_ENABLED" -DefaultValue "false") {
    $envValues["EDGE_BARGE_IN_ENABLED"] = "false"
    $startupEnvDefaultsApplied += "EDGE_BARGE_IN_ENABLED=false"
  }
}

$resolvedAsrLanguage = [string]([Environment]::GetEnvironmentVariable("EDGE_ASR_LANGUAGE", "Process"))
$resolvedTtsLanguage = [string]([Environment]::GetEnvironmentVariable("EDGE_TTS_LANGUAGE", "Process"))
$resolvedBargeInEnabled = Parse-Bool -Value ([Environment]::GetEnvironmentVariable("EDGE_BARGE_IN_ENABLED", "Process")) -Default $false

$port = 17800
if ($envValues.ContainsKey("EDGE_RPC_PORT")) {
  $port = [int]$envValues["EDGE_RPC_PORT"]
} elseif ($env:EDGE_RPC_PORT) {
  $port = [int]$env:EDGE_RPC_PORT
}

$vtsPort = 8001
if ($envValues.ContainsKey("VTS_API_PORT")) {
  $vtsPort = [int]$envValues["VTS_API_PORT"]
} elseif ($env:VTS_API_PORT) {
  $vtsPort = [int]$env:VTS_API_PORT
}

$asrPort = 17801
if ($envValues.ContainsKey("EDGE_ASR_PORT")) {
  $asrPort = [int]$envValues["EDGE_ASR_PORT"]
} elseif ($env:EDGE_ASR_PORT) {
  $asrPort = [int]$env:EDGE_ASR_PORT
}

$ttsPort = 17802
if ($envValues.ContainsKey("EDGE_TTS_PORT")) {
  $ttsPort = [int]$envValues["EDGE_TTS_PORT"]
} elseif ($env:EDGE_TTS_PORT) {
  $ttsPort = [int]$env:EDGE_TTS_PORT
}

$vtsAutoStart = $false
if ($envValues.ContainsKey("EDGE_VTS_AUTOSTART")) {
  $vtsAutoStart = Parse-Bool -Value $envValues["EDGE_VTS_AUTOSTART"] -Default $false
} elseif ($env:EDGE_VTS_AUTOSTART) {
  $vtsAutoStart = Parse-Bool -Value $env:EDGE_VTS_AUTOSTART -Default $false
}

$vtsSteamAppId = "1325860"  # VTube Studio on Steam
if ($envValues.ContainsKey("EDGE_VTS_STEAM_APP_ID")) {
  $value = [string]$envValues["EDGE_VTS_STEAM_APP_ID"]
  if ($value.Trim()) { $vtsSteamAppId = $value.Trim() }
} elseif ($env:EDGE_VTS_STEAM_APP_ID) {
  $value = [string]$env:EDGE_VTS_STEAM_APP_ID
  if ($value.Trim()) { $vtsSteamAppId = $value.Trim() }
}

$vtsAutoStartWaitSeconds = 35
if ($envValues.ContainsKey("EDGE_VTS_AUTOSTART_WAIT_SECONDS")) {
  $candidate = [int]$envValues["EDGE_VTS_AUTOSTART_WAIT_SECONDS"]
  if ($candidate -gt 0) { $vtsAutoStartWaitSeconds = $candidate }
} elseif ($env:EDGE_VTS_AUTOSTART_WAIT_SECONDS) {
  $candidate = [int]$env:EDGE_VTS_AUTOSTART_WAIT_SECONDS
  if ($candidate -gt 0) { $vtsAutoStartWaitSeconds = $candidate }
}

$vtsExecutablePath = Resolve-VtsExecutablePath -EnvValues $envValues

$wechatBridgeEnabled = $false
$wechatBridgeEnvRaw = $null
$wechatBridgeEnvRaw = Get-EnvValueCaseInsensitive -Values $envValues -Key "EDGE_WECHAT_BRIDGE_ENABLED"
if ($null -ne $wechatBridgeEnvRaw) {
  $wechatBridgeEnabled = Parse-Bool -Value $wechatBridgeEnvRaw -Default $false
} elseif ($null -ne $env:EDGE_WECHAT_BRIDGE_ENABLED -and -not [string]::IsNullOrWhiteSpace([string]$env:EDGE_WECHAT_BRIDGE_ENABLED)) {
  $wechatBridgeEnabled = Parse-Bool -Value $env:EDGE_WECHAT_BRIDGE_ENABLED -Default $false
  $wechatBridgeEnvRaw = $env:EDGE_WECHAT_BRIDGE_ENABLED
}

$wechatBridgePidFile = Join-Path $runtimeRoot "wechat_bridge.pid"
$wechatBridgeLog = Join-Path $LogDir "wechat_bridge.log"
$wechatBridgeErrLog = Join-Path $LogDir "wechat_bridge.stderr.log"

if (Test-Path -Path $pidFile) {
  $existingPidText = (Get-Content -Path $pidFile -Raw -Encoding ascii).Trim()
  if ($existingPidText) {
    $existingPid = [int]$existingPidText
    $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($null -ne $existingProc -and -not $ForceRestart.IsPresent) {
      $result = [ordered]@{
        status = "already_running"
        pid = $existingPid
        pid_file = $pidFile
        port = $port
        env_file = $EnvFile
        ssh_session_detected = $sshSessionDetected
        breakaway_launcher_used = $useBreakawayLauncher
        wechat_bridge_env_raw = $wechatBridgeEnvRaw
        wechat_bridge_enabled = $wechatBridgeEnabled
      }
      $result | ConvertTo-Json -Depth 4
      exit 0
    }
    if ($null -ne $existingProc -and $ForceRestart.IsPresent) {
      Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
      Start-Sleep -Milliseconds 500
    }
    if ($null -eq $existingProc) {
      Remove-Item -Path $pidFile -Force -ErrorAction SilentlyContinue
    }
  }
}

$listeningPid = Get-ListeningPid -Port $port
if ($null -ne $listeningPid) {
  if (-not $ForceRestart.IsPresent) {
    $result = [ordered]@{
      status = "already_running"
      pid = $listeningPid
      pid_file = $pidFile
      port = $port
      env_file = $EnvFile
      ssh_session_detected = $sshSessionDetected
      breakaway_launcher_used = $useBreakawayLauncher
      wechat_bridge_env_raw = $wechatBridgeEnvRaw
      wechat_bridge_enabled = $wechatBridgeEnabled
    }
    $result | ConvertTo-Json -Depth 4
    exit 0
  }
  Stop-Process -Id $listeningPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $pythonCmd) {
  throw "python executable not found in PATH"
}

$cudaRuntimeDirs = Get-NvidiaCudaDllDirs -PythonRoot (Split-Path -Parent $pythonCmd.Source)
$cudaRuntimePathInjected = $false
if ($cudaRuntimeDirs.Count -gt 0) {
  $mergedPath = Merge-PathEntries -CurrentPath ([string]$env:PATH) -ExtraEntries $cudaRuntimeDirs
  [Environment]::SetEnvironmentVariable("PATH", $mergedPath, "Process")
  $envValues["PATH"] = $mergedPath
  $cudaRuntimePathInjected = $true
}

$forcedAsrDisabled = $false
$startupMode = "normal"
$attemptErrors = New-Object System.Collections.Generic.List[string]

function Start-EdgeAttempt {
  param(
    [string]$ModeLabel,
    [hashtable]$EnvOverrides
  )
  $launchEnvValues = @{}
  foreach ($baseKey in $envValues.Keys) {
    $launchEnvValues[$baseKey] = [string]$envValues[$baseKey]
  }
  foreach ($key in $EnvOverrides.Keys) {
    $launchEnvValues[$key] = [string]$EnvOverrides[$key]
    [Environment]::SetEnvironmentVariable($key, [string]$EnvOverrides[$key], "Process")
  }
  $proc = Start-EdgeProcess `
    -PythonPath $pythonCmd.Source `
    -RepoRoot $repoRoot `
    -StdoutLog $stdoutLog `
    -StderrLog $stderrLog `
    -ShowConsoleWindow $ShowConsoleWindow.IsPresent `
    -UseBreakawayLaunch $useBreakawayLauncher `
    -LauncherScriptPath $launcherScriptPath `
    -Port $port `
    -EnvVars $launchEnvValues

  if (-not (Wait-Port -Port $port -TimeoutSeconds $WaitSeconds)) {
    $stderrTail = ""
    if (Test-Path -Path $stderrLog) {
      $stderrTail = (Get-Content -Path $stderrLog -Tail 60 -ErrorAction SilentlyContinue) -join "`n"
    }
    throw "[$ModeLabel] edge rpc gateway failed to listen on port $port within $WaitSeconds seconds.`n$stderrTail"
  }
  $procId = $null
  if ($null -ne $proc) {
    try {
      $procId = [int]$proc.Id
    } catch {
      $procId = $null
    }
  }
  if ($null -eq $procId -or $procId -le 0) {
    $procId = Get-ListeningPid -Port $port
  }
  if ($null -eq $procId -or $procId -le 0) {
    throw "[$ModeLabel] edge rpc is listening on $port but owning process id could not be resolved."
  }
  if (-not (Wait-Process-And-Port-Stable -ProcessId $procId -Port $port -TimeoutSeconds $StabilityWaitSeconds)) {
    $stderrTail = ""
    if (Test-Path -Path $stderrLog) {
      $stderrTail = (Get-Content -Path $stderrLog -Tail 80 -ErrorAction SilentlyContinue) -join "`n"
    }
    throw "[$ModeLabel] edge process became unstable during ${StabilityWaitSeconds}s stabilization window (pid=$procId, port=$port).`n$stderrTail"
  }
  if ($ModeLabel -eq "normal" -and $AutoDegradeOnAsrCrash) {
    $asrHealthUrl = "http://127.0.0.1:$asrPort/health"
    if (-not (Wait-HttpHealthy -Url $asrHealthUrl -TimeoutSeconds 8)) {
      throw "[$ModeLabel] asr health probe failed at $asrHealthUrl; triggering asr_disabled_fallback."
    }
  }
  return (Get-Process -Id $procId -ErrorAction SilentlyContinue)
}

$proc = $null
try {
  $proc = Start-EdgeAttempt -ModeLabel "normal" -EnvOverrides @{}
} catch {
  $attemptErrors.Add($_.Exception.Message) | Out-Null
}

if (($null -eq $proc) -and $AutoDegradeOnAsrCrash) {
  $oldPid = Get-ListeningPid -Port $port
  if ($null -ne $oldPid) {
    Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
  }
  try {
    $proc = Start-EdgeAttempt -ModeLabel "asr_disabled_fallback" -EnvOverrides @{ EDGE_ASR_ENABLED = "false" }
    $forcedAsrDisabled = $true
    $startupMode = "asr_disabled_fallback"
  } catch {
    $attemptErrors.Add($_.Exception.Message) | Out-Null
  }
}

if ($null -eq $proc) {
  $detail = ($attemptErrors -join "`n---`n")
  throw "edge startup failed after all attempts.`n$detail"
}

$edgePid = [int]$proc.Id
Set-Content -Path $pidFile -Value ([string]$edgePid) -Encoding ascii

# ── M8 TASK-0107: Edge 子系统健康探测（ASR / TTS / VTS）────────────────────
$edgeHost = "127.0.0.1"
$asrStatus = "ok"
$ttsStatus = "ok"
$vtsStatus = "ok"
$vtsAutoStartStatus = "skipped"
$vtsAutoStartDetail = "EDGE_VTS_AUTOSTART disabled"
$startupWarnings = @($startupPreWarnings)

# ASR 健康探测（/health/asr 或 /asr/health）
try {
  $asrResp = Invoke-WebRequest -Uri "http://$edgeHost`:$asrPort/health" -UseBasicParsing -TimeoutSec 5
  if ($asrResp.StatusCode -lt 200 -or $asrResp.StatusCode -ge 300) {
    $asrStatus = "degraded"; $startupWarnings += "STARTUP_ASR_DEGRADED"
  }
} catch {
  $asrStatus = "degraded"; $startupWarnings += "STARTUP_ASR_DEGRADED"
}
if ($forcedAsrDisabled) {
  $asrStatus = "degraded"
  if (-not ($startupWarnings -contains "STARTUP_ASR_FORCE_DISABLED")) {
    $startupWarnings += "STARTUP_ASR_FORCE_DISABLED"
  }
}

# TTS 健康探测
try {
  $ttsResp = Invoke-WebRequest -Uri "http://$edgeHost`:$ttsPort/health" -UseBasicParsing -TimeoutSec 5
  if ($ttsResp.StatusCode -lt 200 -or $ttsResp.StatusCode -ge 300) {
    $ttsStatus = "degraded"; $startupWarnings += "STARTUP_TTS_DEGRADED"
  }
} catch {
  $ttsStatus = "degraded"; $startupWarnings += "STARTUP_TTS_DEGRADED"
}

# VTS 自启动（可选）：通过 Steam 拉起 VTube Studio
if ($vtsAutoStart) {
  $existingVtsPid = Get-ListeningPid -Port $vtsPort
  if ($existingVtsPid) {
    $vtsAutoStartStatus = "already_running"
    $vtsAutoStartDetail = "vts already listening on port $vtsPort (pid=$existingVtsPid)"
  } else {
    $launch = Start-VtsViaSteam -SteamAppId $vtsSteamAppId
    $vtsAutoStartStatus = [string]$launch.status
    $vtsAutoStartDetail = [string]$launch.detail
    $portReady = $false
    if ($vtsAutoStartStatus -eq "launched") {
      $portReady = Wait-Port -Port $vtsPort -TimeoutSeconds $vtsAutoStartWaitSeconds
      if ($portReady) {
        $vtsAutoStartStatus = "ready"
        $vtsAutoStartDetail = "vts listens on port $vtsPort after steam launch"
      } else {
        $vtsAutoStartStatus = "launch_timeout"
        $vtsAutoStartDetail = "steam launch requested but port $vtsPort not ready within $vtsAutoStartWaitSeconds seconds"
      }
    }

    if (-not $portReady -and $vtsExecutablePath) {
      $directLaunch = Start-VtsViaExecutable -ExecutablePath $vtsExecutablePath
      if ([string]$directLaunch.status -eq "launched") {
        if (Wait-Port -Port $vtsPort -TimeoutSeconds $vtsAutoStartWaitSeconds) {
          $vtsAutoStartStatus = "ready"
          $vtsAutoStartDetail = "vts listens on port $vtsPort after direct executable fallback"
        } else {
          $vtsAutoStartStatus = "launch_timeout"
          $vtsAutoStartDetail = "direct executable fallback launched but port $vtsPort not ready within $vtsAutoStartWaitSeconds seconds (path=$($directLaunch.executable_path))"
        }
      } else {
        if ($vtsAutoStartStatus -eq "launch_timeout") {
          $vtsAutoStartDetail = "$vtsAutoStartDetail; direct fallback failed: $($directLaunch.detail)"
        } else {
          $vtsAutoStartStatus = "launch_failed"
          $vtsAutoStartDetail = "steam launch failed and direct fallback failed: $($directLaunch.detail)"
        }
      }
    } elseif (-not $portReady -and -not $vtsExecutablePath -and $vtsAutoStartStatus -eq "launch_timeout") {
      $vtsAutoStartDetail = "$vtsAutoStartDetail; direct executable fallback unavailable"
    }
  }
}

# VTS 健康探测（VTube Studio API 端口）
$vtsConn = Get-NetTCPConnection -LocalPort $vtsPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $vtsConn) {
  $vtsStatus = "degraded"
  if (-not ($startupWarnings -contains "STARTUP_VTS_DEGRADED")) {
    $startupWarnings += "STARTUP_VTS_DEGRADED"
  }
}
# ────────────────────────────────────────────────────────────────────────────

# 微信桥接（可选）：EDGE_WECHAT_BRIDGE_ENABLED=1 时启动 wcferry 桥接，将微信消息 POST 到 Edge /v1/edge/wechat/callback
$wechatBridgePid = $null
$wechatBridgeStatus = "disabled"
$wechatBridgeError = $null
if ($wechatBridgeEnabled) {
  $bridgeScript = Join-Path $repoRoot "tools\wechat_wcferry_bridge.py"
  $edgeUrl = "http://127.0.0.1:$port"
  if (Test-Path -Path $bridgeScript) {
    try {
      $bridgeProc = Start-Process -FilePath $pythonCmd.Source `
        -ArgumentList @($bridgeScript, "--edge-url", $edgeUrl) `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $wechatBridgeLog `
        -RedirectStandardError $wechatBridgeErrLog
      $wechatBridgePid = [int]$bridgeProc.Id
      Set-Content -Path $wechatBridgePidFile -Value ([string]$wechatBridgePid) -Encoding ascii
      $wechatBridgeStatus = "started"
    } catch {
      $wechatBridgeStatus = "start_failed"
      $wechatBridgeError = $_.Exception.Message
      $startupWarnings += "WECHAT_BRIDGE_START_FAILED"
    }
  } else {
    $wechatBridgeStatus = "script_not_found"
    $wechatBridgeError = "script not found: $bridgeScript"
    $startupWarnings += "WECHAT_BRIDGE_SCRIPT_NOT_FOUND"
  }
}
# ────────────────────────────────────────────────────────────────────────────

$result = [ordered]@{
  status = "started"
  pid = $edgePid
  pid_file = $pidFile
  port = $port
  env_file = $EnvFile
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
  startup_mode = $startupMode
  asr_forced_disabled = $forcedAsrDisabled
  ssh_session_detected = $sshSessionDetected
  breakaway_launcher_used = $useBreakawayLauncher
  cuda_runtime_path_injected = $cudaRuntimePathInjected
  cuda_runtime_paths = @($cudaRuntimeDirs)
  asr_status = $asrStatus
  tts_status = $ttsStatus
  vts_status = $vtsStatus
  vts_port = $vtsPort
  vts_autostart_enabled = $vtsAutoStart
  vts_autostart_status = $vtsAutoStartStatus
  vts_autostart_detail = $vtsAutoStartDetail
  wechat_bridge_enabled = $wechatBridgeEnabled
  wechat_bridge_status = $wechatBridgeStatus
  wechat_bridge_pid = $wechatBridgePid
  wechat_bridge_pid_file = if ($wechatBridgePid) { $wechatBridgePidFile } else { $null }
  wechat_bridge_error = $wechatBridgeError
  wechat_bridge_log = if ($wechatBridgeEnabled) { $wechatBridgeLog } else { $null }
  wechat_bridge_env_raw = $wechatBridgeEnvRaw
  enforce_cn_asr_ja_tts_mode = $EnforceCnAsrJaTtsMode
  asr_language = $resolvedAsrLanguage
  tts_language = $resolvedTtsLanguage
  barge_in_enabled = $resolvedBargeInEnabled
  startup_env_defaults_applied = $startupEnvDefaultsApplied
  startup_env_enforced_applied = $startupEnvEnforcedApplied
  startup_warnings = $startupWarnings
}
$result | ConvertTo-Json -Depth 4
