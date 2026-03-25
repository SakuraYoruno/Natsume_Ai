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

function Read-ProcessEnvOverrides {
  param([string[]]$Keys)
  $result = @{}
  foreach ($key in $Keys) {
    if ([string]::IsNullOrWhiteSpace([string]$key)) {
      continue
    }
    $value = [Environment]::GetEnvironmentVariable($key, "Process")
    if ($null -eq $value) {
      continue
    }
    $text = [string]$value
    if ([string]::IsNullOrWhiteSpace($text)) {
      continue
    }
    $result[$key] = $text
  }
  return $result
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

function Invoke-SchtasksBreakaway {
  param(
    [string]$TaskName,
    [string]$TaskCommand,
    [string]$FailureContext = "breakaway launch",
    [bool]$InteractiveTask = $false
  )
  $schtasksCmd = Get-Command schtasks.exe -ErrorAction SilentlyContinue
  if ($null -eq $schtasksCmd) {
    throw "schtasks.exe not found; cannot launch breakaway process"
  }

  $targetTime = (Get-Date).AddMinutes(3)
  $taskTime = $targetTime.ToString("HH:mm")
  $taskDateCandidates = @(
    $targetTime.ToString("yyyy/MM/dd", [System.Globalization.CultureInfo]::InvariantCulture),
    $targetTime.ToString("MM/dd/yyyy", [System.Globalization.CultureInfo]::InvariantCulture)
  )

  $created = $false
  $createErrors = New-Object System.Collections.Generic.List[string]
  foreach ($taskDate in $taskDateCandidates) {
    $createOutput = @()
    $createExitCode = 1
    $createArgs = @(
      "/Create", "/TN", $TaskName, "/SC", "ONCE", "/SD", $taskDate, "/ST", $taskTime, "/TR", $TaskCommand, "/F"
    )
    if ($InteractiveTask) {
      $createArgs += "/IT"
    }
    try {
      $createOutput = & $schtasksCmd.Source @createArgs 2>&1
      $createExitCode = [int]$LASTEXITCODE
    } catch {
      if ($LASTEXITCODE -ne 0) {
        $createExitCode = [int]$LASTEXITCODE
      }
      $createOutput = @($_.Exception.Message)
    }
    if ($createExitCode -eq 0) {
      $created = $true
      break
    }
    $detail = (($createOutput | ForEach-Object { [string]$_ }) -join "`n")
    if (-not [string]::IsNullOrWhiteSpace($detail)) {
      $createErrors.Add("[/SD $taskDate] (exit=$createExitCode) $detail") | Out-Null
    }
  }

  if (-not $created) {
    $createOutput = @()
    $createExitCode = 1
    $createArgs = @(
      "/Create", "/TN", $TaskName, "/SC", "ONCE", "/ST", $taskTime, "/TR", $TaskCommand, "/F"
    )
    if ($InteractiveTask) {
      $createArgs += "/IT"
    }
    try {
      $createOutput = & $schtasksCmd.Source @createArgs 2>&1
      $createExitCode = [int]$LASTEXITCODE
    } catch {
      if ($LASTEXITCODE -ne 0) {
        $createExitCode = [int]$LASTEXITCODE
      }
      $createOutput = @($_.Exception.Message)
    }
    if ($createExitCode -eq 0) {
      $created = $true
    } else {
      $detail = (($createOutput | ForEach-Object { [string]$_ }) -join "`n")
      if (-not [string]::IsNullOrWhiteSpace($detail)) {
        $createErrors.Add("[no /SD] (exit=$createExitCode) $detail") | Out-Null
      }
    }
  }

  if (-not $created) {
    $detail = ($createErrors -join "`n---`n")
    if (-not $detail) {
      $detail = "unknown create failure"
    }
    throw "failed to create breakaway task $TaskName ($FailureContext).`n$detail"
  }

  try {
    $runOutput = @()
    $runExitCode = 1
    try {
      $runOutput = & $schtasksCmd.Source /Run /TN $TaskName 2>&1
      $runExitCode = [int]$LASTEXITCODE
    } catch {
      if ($LASTEXITCODE -ne 0) {
        $runExitCode = [int]$LASTEXITCODE
      }
      $runOutput = @($_.Exception.Message)
    }
    if ($runExitCode -ne 0) {
      $detail = (($runOutput | ForEach-Object { [string]$_ }) -join "`n")
      throw "failed to run breakaway task $TaskName ($FailureContext).`n(exit=$runExitCode) $detail"
    }
  } finally {
    try {
      & $schtasksCmd.Source /Delete /TN $TaskName /F 2>&1 | Out-Null
    } catch {
    }
  }
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
    $pythonEscaped = ([string]$PythonPath).Replace("'", "''")
    $stdoutEscaped = ([string]$StdoutLog).Replace("'", "''")
    $stderrEscaped = ([string]$StderrLog).Replace("'", "''")
    $pythonCmd = "start `"`" /B powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"& '$pythonEscaped' -m edge_client 1>> '$stdoutEscaped' 2>> '$stderrEscaped'`""
    $launcherLines += $pythonCmd
    $launcherLines += "endlocal"
    Set-Content -Path $LauncherScriptPath -Value $launcherLines -Encoding ascii

    $taskName = "NatsumeEdgeLaunch_$([Guid]::NewGuid().ToString('N').Substring(0, 10))"
    $taskCommand = "`"$LauncherScriptPath`""
    Invoke-SchtasksBreakaway -TaskName $taskName -TaskCommand $taskCommand -FailureContext "edge launch" -InteractiveTask $true

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

function Invoke-BreakawayCommand {
  param(
    [string]$RepoRoot,
    [string]$LauncherScriptPath,
    [string]$CommandLine,
    [hashtable]$EnvVars = @{},
    [string]$TaskPrefix = "NatsumeLaunch",
    [bool]$InteractiveTask = $false
  )
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
  $launcherLines += $CommandLine
  $launcherLines += "endlocal"
  Set-Content -Path $LauncherScriptPath -Value $launcherLines -Encoding ascii

  $prefix = [string]$TaskPrefix
  if (-not $prefix) {
    $prefix = "NatsumeLaunch"
  }
  $taskName = "{0}_{1}" -f $prefix, ([Guid]::NewGuid().ToString('N').Substring(0, 10))
  $taskCommand = "`"$LauncherScriptPath`""
  Invoke-SchtasksBreakaway -TaskName $taskName -TaskCommand $taskCommand -FailureContext $prefix -InteractiveTask $InteractiveTask
}

function Find-ProcessIdByCommandLine {
  param(
    [string]$ImageName,
    [string]$ContainsText
  )
  if ([string]::IsNullOrWhiteSpace($ImageName) -or [string]::IsNullOrWhiteSpace($ContainsText)) {
    return $null
  }
  $needle = $ContainsText.ToLowerInvariant()
  $rows = Get-CimInstance -ClassName Win32_Process -Filter ("Name = '{0}'" -f $ImageName) -ErrorAction SilentlyContinue
  foreach ($row in $rows) {
    $cmd = [string]$row.CommandLine
    if (-not $cmd) {
      continue
    }
    if ($cmd.ToLowerInvariant().Contains($needle)) {
      return [int]$row.ProcessId
    }
  }
  return $null
}

function Get-ProcessIdsByCommandLine {
  param(
    [string]$ImageName,
    [string]$ContainsText
  )
  $ids = New-Object System.Collections.Generic.List[int]
  if ([string]::IsNullOrWhiteSpace($ImageName) -or [string]::IsNullOrWhiteSpace($ContainsText)) {
    return @()
  }
  $needle = $ContainsText.ToLowerInvariant()
  $rows = Get-CimInstance -ClassName Win32_Process -Filter ("Name = '{0}'" -f $ImageName) -ErrorAction SilentlyContinue
  foreach ($row in $rows) {
    $cmd = [string]$row.CommandLine
    if (-not $cmd) {
      continue
    }
    if ($cmd.ToLowerInvariant().Contains($needle)) {
      $ids.Add([int]$row.ProcessId) | Out-Null
    }
  }
  return @($ids | Select-Object -Unique)
}

function Stop-ProcessesByIdList {
  param([int[]]$Ids)
  $stopped = New-Object System.Collections.Generic.List[int]
  foreach ($procId in ($Ids | Where-Object { $_ -gt 0 } | Select-Object -Unique)) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
      continue
    }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    $stopped.Add([int]$procId) | Out-Null
  }
  return @($stopped)
}

function Start-WeChatBridgeProcess {
  param(
    [string]$PythonPath,
    [string]$BridgeScriptPath,
    [string]$EdgeUrl,
    [string[]]$ExtraArgs = @(),
    [string]$RepoRoot,
    [string]$StdoutLog,
    [string]$StderrLog,
    [bool]$UseBreakawayLaunch = $false,
    [string]$LauncherScriptPath = "",
    [hashtable]$EnvVars = @{}
  )
  if ($UseBreakawayLaunch) {
    $argumentItems = New-Object System.Collections.Generic.List[string]
    $argumentItems.Add((([string]$BridgeScriptPath).Replace("'", "''"))) | Out-Null
    $argumentItems.Add("--edge-url") | Out-Null
    $argumentItems.Add((([string]$EdgeUrl).Replace("'", "''"))) | Out-Null
    foreach ($extraArg in $ExtraArgs) {
      if ([string]::IsNullOrWhiteSpace([string]$extraArg)) {
        continue
      }
      $argumentItems.Add((([string]$extraArg).Replace("'", "''"))) | Out-Null
    }
    $argumentListLiteral = ($argumentItems | ForEach-Object { "'$_'" }) -join ","
    $pythonEscaped = ([string]$PythonPath).Replace("'", "''")
    $repoEscaped = ([string]$RepoRoot).Replace("'", "''")
    $stdoutEscaped = ([string]$StdoutLog).Replace("'", "''")
    $stderrEscaped = ([string]$StderrLog).Replace("'", "''")
    $psStartCommand = "Start-Process -FilePath '$pythonEscaped' -ArgumentList @($argumentListLiteral) -WorkingDirectory '$repoEscaped' -WindowStyle Hidden -RedirectStandardOutput '$stdoutEscaped' -RedirectStandardError '$stderrEscaped'"
    $commandLine = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$psStartCommand`""
    Invoke-BreakawayCommand `
      -RepoRoot $RepoRoot `
      -LauncherScriptPath $LauncherScriptPath `
      -CommandLine $commandLine `
      -EnvVars $EnvVars `
      -TaskPrefix "NatsumeWechatBridge" `
      -InteractiveTask $true
    return [pscustomobject]@{ Id = 0 }
  }

  $bridgeArgs = @($BridgeScriptPath, "--edge-url", $EdgeUrl)
  foreach ($extraArg in $ExtraArgs) {
    if ([string]::IsNullOrWhiteSpace([string]$extraArg)) {
      continue
    }
    $bridgeArgs += [string]$extraArg
  }

  return (Start-Process -FilePath $PythonPath `
    -ArgumentList $bridgeArgs `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -PassThru `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog)
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
$wechatBridgeLauncherScriptPath = Join-Path $runtimeRoot "launch_wechat_bridge.cmd"
$sshSessionDetected = (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CONNECTION)) -or (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CLIENT))
$useBreakawayLauncher = [bool]($BreakawayFromSsh -and $sshSessionDetected)

$wechatEnvOverrideKeys = @(
  "EDGE_WECHAT_BRIDGE_ENABLED",
  "EDGE_WECHAT_BRIDGE_BACKEND",
  "EDGE_WECHAT_INBOUND_BACKEND",
  "EDGE_WECHAT_LISTENER_ENABLED",
  "EDGE_WECHAT_LISTENER_POLL_INTERVAL_S",
  "EDGE_WECHAT_LISTENER_DEDUP_WINDOW_S",
  "EDGE_WECHAT_CALLBACK_TIMEOUT_S",
  "EDGE_WECHAT_OCR_MIN_SCORE",
  "EDGE_WECHAT_OCR_INBOUND_X_RATIO_MAX",
  "EDGE_WECHAT_UIA_SCAN_CHAT_LIST",
  "EDGE_WECHAT_FORCE_ACTIVE_CHAT_TARGET",
  "EDGE_WECHAT_UIA_AUTO_RECOVER_ANCHOR_CLICK",
  "EDGE_WECHAT_SEND_TEXT_PREFER_ACTIVE_CHAT",
  "EDGE_WECHAT_UIA_ACTIVE_INBOUND_X_RATIO_MAX",
  "EDGE_WECHAT_VISION_MODEL",
  "EDGE_WECHAT_VISION_API_URL",
  "EDGE_WECHAT_VISION_TIMEOUT_S",
  "EDGE_WECHAT_VISION_MIN_CONFIDENCE",
  "EDGE_WECHAT_VISION_MIN_INTERVAL_S",
  "EDGE_WECHAT_ALLOW_EXTERNAL_CALLBACK_ONLY",
  "EDGE_WECHAT_AUTOSTART",
  "EDGE_WECHAT_AUTOSTART_WAIT_SECONDS"
)
$processWechatOverrides = Read-ProcessEnvOverrides -Keys $wechatEnvOverrideKeys

$envValues = Read-EnvFile -Path $EnvFile
foreach ($overrideKey in $processWechatOverrides.Keys) {
  $envValues[$overrideKey] = [string]$processWechatOverrides[$overrideKey]
}
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

function Get-RunningProcessByName {
  param([string[]]$Names)
  foreach ($name in $Names) {
    if ([string]::IsNullOrWhiteSpace([string]$name)) {
      continue
    }
    $proc = Get-Process -Name $name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $proc) {
      return $proc
    }
  }
  return $null
}

function Wait-ProcessByName {
  param(
    [string[]]$Names,
    [int]$TimeoutSeconds = 10
  )
  $deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
  while ((Get-Date) -lt $deadline) {
    $proc = Get-RunningProcessByName -Names $Names
    if ($null -ne $proc) {
      return $proc
    }
    Start-Sleep -Milliseconds 500
  }
  return $null
}

function Resolve-WeChatExecutablePath {
  param([hashtable]$EnvValues)

  $candidates = New-Object System.Collections.Generic.List[string]
  $explicitPath = [string](Get-EnvValueCaseInsensitive -Values $EnvValues -Key "EDGE_WECHAT_EXECUTABLE_PATH")
  if (-not [string]::IsNullOrWhiteSpace($explicitPath)) {
    [void]$candidates.Add($explicitPath.Trim())
  } elseif (-not [string]::IsNullOrWhiteSpace([string]$env:EDGE_WECHAT_EXECUTABLE_PATH)) {
    [void]$candidates.Add(([string]$env:EDGE_WECHAT_EXECUTABLE_PATH).Trim())
  }

  foreach ($fallbackPath in @(
    "C:\Program Files\Tencent\WeChat\WeChat.exe",
    "C:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
    "D:\Program Files\Tencent\WeChat\WeChat.exe",
    "D:\Program Files (x86)\Tencent\WeChat\WeChat.exe",
    "C:\Program Files\Tencent\Weixin\Weixin.exe",
    "C:\Program Files (x86)\Tencent\Weixin\Weixin.exe",
    "D:\Program Files\Tencent\Weixin\Weixin.exe",
    "D:\Program Files (x86)\Tencent\Weixin\Weixin.exe"
  )) {
    [void]$candidates.Add($fallbackPath)
  }

  $localAppData = [string]$env:LOCALAPPDATA
  if (-not [string]::IsNullOrWhiteSpace($localAppData)) {
    [void]$candidates.Add((Join-Path $localAppData "Tencent\WeChat\WeChat.exe"))
    [void]$candidates.Add((Join-Path $localAppData "Tencent\Weixin\Weixin.exe"))
  }

  $uniqueCandidates = $candidates | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
  foreach ($candidate in $uniqueCandidates) {
    if (Test-Path -Path $candidate) {
      return (Resolve-Path -Path $candidate).Path
    }
  }
  return $null
}

function Start-WeChatClient {
  param([string]$ExecutablePath)

  if ([string]::IsNullOrWhiteSpace($ExecutablePath)) {
    return [ordered]@{
      status = "launch_failed"
      detail = "wechat executable path is empty"
      executable_path = $null
      process_name = $null
      pid = $null
    }
  }

  if (-not (Test-Path -Path $ExecutablePath)) {
    return [ordered]@{
      status = "launch_failed"
      detail = "wechat executable not found: $ExecutablePath"
      executable_path = $ExecutablePath
      process_name = $null
      pid = $null
    }
  }

  try {
    $proc = Start-Process -FilePath $ExecutablePath -PassThru -ErrorAction Stop
    return [ordered]@{
      status = "launched"
      detail = "wechat launch requested"
      executable_path = $ExecutablePath
      process_name = [string]$proc.ProcessName
      pid = [int]$proc.Id
    }
  } catch {
    return [ordered]@{
      status = "launch_failed"
      detail = "wechat launch failed: $($_.Exception.Message)"
      executable_path = $ExecutablePath
      process_name = $null
      pid = $null
    }
  }
}

function Ensure-WeChatRegistryCompatibility {
  param([string]$ExecutablePath = "")

  $wechatRegPath = "Registry::HKEY_CURRENT_USER\Software\Tencent\WeChat"
  $weixinRegPath = "Registry::HKEY_CURRENT_USER\Software\Tencent\Weixin"
  $currentInstallPath = ""
  try {
    $currentInstallPath = [string]((Get-ItemProperty -Path $wechatRegPath -Name InstallPath -ErrorAction Stop).InstallPath)
  } catch {
    $currentInstallPath = ""
  }
  if (-not [string]::IsNullOrWhiteSpace($currentInstallPath)) {
    return [ordered]@{
      success = $true
      changed = $false
      status = "already_present"
      detail = "HKCU\\Software\\Tencent\\WeChat\\InstallPath already exists"
      install_path = $currentInstallPath
    }
  }

  $candidateInstallPath = ""
  try {
    $candidateInstallPath = [string]((Get-ItemProperty -Path $weixinRegPath -Name InstallPath -ErrorAction Stop).InstallPath)
  } catch {
    $candidateInstallPath = ""
  }

  if ([string]::IsNullOrWhiteSpace($candidateInstallPath) -and -not [string]::IsNullOrWhiteSpace($ExecutablePath)) {
    if (Test-Path -Path $ExecutablePath -PathType Leaf) {
      $candidateInstallPath = Split-Path -Parent $ExecutablePath
    } elseif (Test-Path -Path $ExecutablePath -PathType Container) {
      $candidateInstallPath = $ExecutablePath
    }
  }

  if ([string]::IsNullOrWhiteSpace($candidateInstallPath)) {
    return [ordered]@{
      success = $false
      changed = $false
      status = "missing_candidate"
      detail = "cannot infer WeChat InstallPath from registry or EDGE_WECHAT_EXECUTABLE_PATH"
      install_path = $null
    }
  }

  try {
    if (-not (Test-Path -Path $wechatRegPath)) {
      New-Item -Path $wechatRegPath -Force | Out-Null
    }
    New-ItemProperty -Path $wechatRegPath -Name InstallPath -Value $candidateInstallPath -PropertyType String -Force | Out-Null
    return [ordered]@{
      success = $true
      changed = $true
      status = "created_from_weixin"
      detail = "created HKCU\\Software\\Tencent\\WeChat\\InstallPath from Weixin install path"
      install_path = $candidateInstallPath
    }
  } catch {
    return [ordered]@{
      success = $false
      changed = $false
      status = "create_failed"
      detail = "failed to create WeChat registry compatibility key: $($_.Exception.Message)"
      install_path = $candidateInstallPath
    }
  }
}

function Test-PythonImport {
  param(
    [string]$PythonPath,
    [string]$ModuleName
  )
  if ([string]::IsNullOrWhiteSpace($PythonPath) -or [string]::IsNullOrWhiteSpace($ModuleName)) {
    return $false
  }
  & $PythonPath -c "import $ModuleName" *> $null
  return ($LASTEXITCODE -eq 0)
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

$wechatBridgeBackend = "uia"
$wechatBridgeBackendRaw = Get-EnvValueCaseInsensitive -Values $envValues -Key "EDGE_WECHAT_BRIDGE_BACKEND"
if ($null -eq $wechatBridgeBackendRaw -and $env:EDGE_WECHAT_BRIDGE_BACKEND) {
  $wechatBridgeBackendRaw = $env:EDGE_WECHAT_BRIDGE_BACKEND
}
if ($null -ne $wechatBridgeBackendRaw -and -not [string]::IsNullOrWhiteSpace([string]$wechatBridgeBackendRaw)) {
  $candidateBackend = ([string]$wechatBridgeBackendRaw).Trim().ToLowerInvariant()
  if ($candidateBackend -in @("uia", "external")) {
    $wechatBridgeBackend = $candidateBackend
  } else {
    $wechatBridgeBackend = "uia"
    $startupPreWarnings += "WECHAT_BRIDGE_BACKEND_INVALID"
  }
}

$wechatAllowExternalCallbackOnly = $false
$wechatAllowExternalRaw = Get-EnvValueCaseInsensitive -Values $envValues -Key "EDGE_WECHAT_ALLOW_EXTERNAL_CALLBACK_ONLY"
if ($null -eq $wechatAllowExternalRaw -and $env:EDGE_WECHAT_ALLOW_EXTERNAL_CALLBACK_ONLY) {
  $wechatAllowExternalRaw = $env:EDGE_WECHAT_ALLOW_EXTERNAL_CALLBACK_ONLY
}
if ($null -ne $wechatAllowExternalRaw -and -not [string]::IsNullOrWhiteSpace([string]$wechatAllowExternalRaw)) {
  $wechatAllowExternalCallbackOnly = Parse-Bool -Value $wechatAllowExternalRaw -Default $false
}

if ($wechatBridgeEnabled -and $wechatBridgeBackend -eq "external" -and -not $wechatAllowExternalCallbackOnly) {
  $wechatBridgeBackend = "uia"
  if ($null -ne $wechatBridgeBackendRaw -and -not [string]::IsNullOrWhiteSpace([string]$wechatBridgeBackendRaw)) {
    $wechatBridgeBackendRaw = "$wechatBridgeBackendRaw (auto->uia)"
  } else {
    $wechatBridgeBackendRaw = "external(auto->uia)"
  }
  $envValues["EDGE_WECHAT_BRIDGE_BACKEND"] = "uia"
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_BRIDGE_BACKEND", "uia", "Process")
  if (-not ($startupPreWarnings -contains "WECHAT_BRIDGE_EXTERNAL_AUTO_FALLBACK_UIA")) {
    $startupPreWarnings += "WECHAT_BRIDGE_EXTERNAL_AUTO_FALLBACK_UIA"
  }
}

$wechatBridgeRequested = $wechatBridgeEnabled
if ($wechatBridgeRequested) {
  if (-not ($startupPreWarnings -contains "WECHAT_BRIDGE_FORCE_DISABLED_BY_GATEWAY")) {
    $startupPreWarnings += "WECHAT_BRIDGE_FORCE_DISABLED_BY_GATEWAY"
  }
}
$wechatBridgeEnabled = $false
$wechatBridgeBackend = "external"
$wechatBridgeBackendRaw = "forced_external_by_gateway_mode"
$envValues["EDGE_WECHAT_BRIDGE_ENABLED"] = "false"
[Environment]::SetEnvironmentVariable("EDGE_WECHAT_BRIDGE_ENABLED", "false", "Process")
$envValues["EDGE_WECHAT_BRIDGE_BACKEND"] = "external"
[Environment]::SetEnvironmentVariable("EDGE_WECHAT_BRIDGE_BACKEND", "external", "Process")
[Environment]::SetEnvironmentVariable("EDGE_WECHAT_LISTENER_ENABLED", "false", "Process")
[Environment]::SetEnvironmentVariable("EDGE_WECHAT_INBOUND_BACKEND", "", "Process")

$wechatClientNames = @("WeChat", "Weixin")
$wechatAutoStart = ($wechatBridgeEnabled -and $wechatBridgeBackend -ne "external")
$wechatAutoStartRaw = Get-EnvValueCaseInsensitive -Values $envValues -Key "EDGE_WECHAT_AUTOSTART"
if ($null -ne $wechatAutoStartRaw) {
  $wechatAutoStart = Parse-Bool -Value $wechatAutoStartRaw -Default $wechatAutoStart
} elseif ($null -ne $env:EDGE_WECHAT_AUTOSTART -and -not [string]::IsNullOrWhiteSpace([string]$env:EDGE_WECHAT_AUTOSTART)) {
  $wechatAutoStart = Parse-Bool -Value $env:EDGE_WECHAT_AUTOSTART -Default $wechatAutoStart
}

$wechatAutoStartWaitSeconds = 12
$wechatAutoStartWaitRaw = Get-EnvValueCaseInsensitive -Values $envValues -Key "EDGE_WECHAT_AUTOSTART_WAIT_SECONDS"
if ($null -ne $wechatAutoStartWaitRaw) {
  $candidate = [int]$wechatAutoStartWaitRaw
  if ($candidate -gt 0) { $wechatAutoStartWaitSeconds = $candidate }
} elseif ($env:EDGE_WECHAT_AUTOSTART_WAIT_SECONDS) {
  $candidate = [int]$env:EDGE_WECHAT_AUTOSTART_WAIT_SECONDS
  if ($candidate -gt 0) { $wechatAutoStartWaitSeconds = $candidate }
}

$wechatExecutablePath = Resolve-WeChatExecutablePath -EnvValues $envValues
if (-not [string]::IsNullOrWhiteSpace([string]$wechatExecutablePath)) {
  $envValues["EDGE_WECHAT_EXECUTABLE_PATH"] = [string]$wechatExecutablePath
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_EXECUTABLE_PATH", [string]$wechatExecutablePath, "Process")
}
$wechatClientStatus = "skipped"
$wechatClientDetail = "wechat bridge disabled"
$wechatClientPid = $null
$wechatClientProcessName = $null
$wechatRegistryCompatStatus = "skipped"
$wechatRegistryCompatDetail = "wechat bridge disabled"

$wechatBridgePidFile = Join-Path $runtimeRoot "wechat_bridge.pid"
$wechatBridgeLog = Join-Path $LogDir "wechat_bridge.log"
$wechatBridgeErrLog = Join-Path $LogDir "wechat_bridge.stderr.log"
$wechatCommandQueueFile = Join-Path $runtimeRoot "wechat_command_queue.jsonl"
$wechatCommandResultFile = Join-Path $runtimeRoot "wechat_command_result.jsonl"

if ($wechatBridgeBackend -eq "uia") {
  $queuePathRaw = [string](Get-EnvValueCaseInsensitive -Values $envValues -Key "EDGE_WECHAT_COMMAND_QUEUE_FILE")
  if (-not [string]::IsNullOrWhiteSpace($queuePathRaw)) {
    if ([System.IO.Path]::IsPathRooted($queuePathRaw)) {
      $wechatCommandQueueFile = $queuePathRaw
    } else {
      $wechatCommandQueueFile = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $queuePathRaw))
    }
  }
  $resultPathRaw = [string](Get-EnvValueCaseInsensitive -Values $envValues -Key "EDGE_WECHAT_COMMAND_RESULT_FILE")
  if (-not [string]::IsNullOrWhiteSpace($resultPathRaw)) {
    if ([System.IO.Path]::IsPathRooted($resultPathRaw)) {
      $wechatCommandResultFile = $resultPathRaw
    } else {
      $wechatCommandResultFile = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $resultPathRaw))
    }
  }

  $queueDir = Split-Path -Parent $wechatCommandQueueFile
  $resultDir = Split-Path -Parent $wechatCommandResultFile
  if (-not [string]::IsNullOrWhiteSpace($queueDir)) {
    Ensure-Directory -Path $queueDir
  }
  if (-not [string]::IsNullOrWhiteSpace($resultDir)) {
    Ensure-Directory -Path $resultDir
  }

  $envValues["EDGE_WECHAT_COMMAND_QUEUE_FILE"] = [string]$wechatCommandQueueFile
  $envValues["EDGE_WECHAT_COMMAND_RESULT_FILE"] = [string]$wechatCommandResultFile
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_COMMAND_QUEUE_FILE", [string]$wechatCommandQueueFile, "Process")
  [Environment]::SetEnvironmentVariable("EDGE_WECHAT_COMMAND_RESULT_FILE", [string]$wechatCommandResultFile, "Process")

  $uiaDefaultEnv = [ordered]@{
    EDGE_WECHAT_INBOUND_BACKEND = "uia_ocr"
    EDGE_WECHAT_UIA_SCAN_CHAT_LIST = "0"
    EDGE_WECHAT_FORCE_ACTIVE_CHAT_TARGET = "1"
    EDGE_WECHAT_UIA_AUTO_RECOVER = "0"
    EDGE_WECHAT_UIA_AUTO_RECOVER_ANCHOR_CLICK = "0"
    EDGE_WECHAT_SEND_TEXT_PREFER_ACTIVE_CHAT = "1"
    EDGE_WECHAT_UIA_ACTIVE_INBOUND_X_RATIO_MAX = "0.70"
    EDGE_WECHAT_VISION_MODEL = "qwen2.5vl:3b"
    EDGE_WECHAT_VISION_API_URL = "http://127.0.0.1:11434/api/chat"
    EDGE_WECHAT_VISION_TIMEOUT_S = "30"
    EDGE_WECHAT_VISION_MIN_CONFIDENCE = "0.50"
    EDGE_WECHAT_VISION_MIN_INTERVAL_S = "1.8"
  }
  foreach ($uiaKey in $uiaDefaultEnv.Keys) {
    $currentUiaValue = [string](Get-EnvValueCaseInsensitive -Values $envValues -Key $uiaKey)
    if (-not [string]::IsNullOrWhiteSpace($currentUiaValue)) {
      continue
    }
    $defaultUiaValue = [string]$uiaDefaultEnv[$uiaKey]
    $envValues[$uiaKey] = $defaultUiaValue
    [Environment]::SetEnvironmentVariable($uiaKey, $defaultUiaValue, "Process")
  }
}

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
        wechat_bridge_backend = $wechatBridgeBackend
        wechat_bridge_backend_raw = $wechatBridgeBackendRaw
        wechat_bridge_command_queue_file = if ($wechatBridgeBackend -eq "uia") { $wechatCommandQueueFile } else { $null }
        wechat_bridge_command_result_file = if ($wechatBridgeBackend -eq "uia") { $wechatCommandResultFile } else { $null }
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
      wechat_bridge_backend = $wechatBridgeBackend
      wechat_bridge_backend_raw = $wechatBridgeBackendRaw
      wechat_bridge_command_queue_file = if ($wechatBridgeBackend -eq "uia") { $wechatCommandQueueFile } else { $null }
      wechat_bridge_command_result_file = if ($wechatBridgeBackend -eq "uia") { $wechatCommandResultFile } else { $null }
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

# 微信桥接（可选）：
#   EDGE_WECHAT_BRIDGE_BACKEND=uia|external
#   external: 不拉起本地桥接，仅保留 /v1/edge/wechat/callback 供外部插件直推
$wechatBridgePid = $null
$wechatBridgeStatus = "disabled"
$wechatBridgeError = $null

# Always clean legacy bridge remnants in gateway mode.
if (Test-Path -Path $wechatBridgePidFile) {
  $staleBridgePidText = (Get-Content -Path $wechatBridgePidFile -Raw -Encoding ascii -ErrorAction SilentlyContinue).Trim()
  if ($staleBridgePidText) {
    $staleBridgePid = [int]$staleBridgePidText
    $staleBridgeProc = Get-Process -Id $staleBridgePid -ErrorAction SilentlyContinue
    if ($null -ne $staleBridgeProc) {
      Stop-Process -Id $staleBridgePid -Force -ErrorAction SilentlyContinue
    }
  }
  Remove-Item -Path $wechatBridgePidFile -Force -ErrorAction SilentlyContinue
}
$bridgeScriptNeedles = @("wechat_uia_bridge.py")
$existingBridgePids = @()
foreach ($bridgeScriptNeedle in $bridgeScriptNeedles) {
  $existingBridgePids += Get-ProcessIdsByCommandLine -ImageName "python.exe" -ContainsText $bridgeScriptNeedle
}
$existingBridgePids = @($existingBridgePids | Select-Object -Unique)
if ($existingBridgePids.Count -gt 0) {
  $stoppedBridgePids = Stop-ProcessesByIdList -Ids $existingBridgePids
  if ($stoppedBridgePids.Count -gt 0) {
    Start-Sleep -Milliseconds 300
  }
}

if ($wechatBridgeEnabled) {
  $wechatClientStatus = "checking"
  $wechatClientDetail = "checking running WeChat process"

  if (Test-Path -Path $wechatBridgePidFile) {
    $staleBridgePidText = (Get-Content -Path $wechatBridgePidFile -Raw -Encoding ascii -ErrorAction SilentlyContinue).Trim()
    if ($staleBridgePidText) {
      $staleBridgePid = [int]$staleBridgePidText
      $staleBridgeProc = Get-Process -Id $staleBridgePid -ErrorAction SilentlyContinue
      if ($null -ne $staleBridgeProc) {
        Stop-Process -Id $staleBridgePid -Force -ErrorAction SilentlyContinue
      }
    }
    Remove-Item -Path $wechatBridgePidFile -Force -ErrorAction SilentlyContinue
  }

  $bridgeScriptNeedles = @("wechat_uia_bridge.py")
  $existingBridgePids = @()
  foreach ($bridgeScriptNeedle in $bridgeScriptNeedles) {
    $existingBridgePids += Get-ProcessIdsByCommandLine -ImageName "python.exe" -ContainsText $bridgeScriptNeedle
  }
  $existingBridgePids = @($existingBridgePids | Select-Object -Unique)
  if ($existingBridgePids.Count -gt 0) {
    $stoppedBridgePids = Stop-ProcessesByIdList -Ids $existingBridgePids
    if ($stoppedBridgePids.Count -gt 0) {
      Start-Sleep -Milliseconds 300
    }
  }

  if ($wechatBridgeBackend -eq "external") {
    $wechatClientStatus = "external_managed"
    $wechatClientDetail = "external plugin/webhook mode; local injector bridge disabled"
    $wechatRegistryCompatStatus = "external_managed"
    $wechatRegistryCompatDetail = "external backend skips local registry compatibility"
    $wechatBridgeStatus = "external_callback_only"
    $wechatBridgeError = $null
  } else {
  $wechatProc = Get-RunningProcessByName -Names $wechatClientNames
  if ($null -ne $wechatProc) {
    $wechatClientStatus = "running"
    $wechatClientPid = [int]$wechatProc.Id
    $wechatClientProcessName = [string]$wechatProc.ProcessName
    $wechatClientDetail = "wechat process is running ($wechatClientProcessName pid=$wechatClientPid)"
    if ($wechatClientProcessName -and $wechatClientProcessName.Equals("Weixin", [StringComparison]::OrdinalIgnoreCase)) {
      $wechatClientDetail = "$wechatClientDetail; detected Weixin.exe"
    }
  } else {
    if ($wechatAutoStart) {
      $wechatClientStatus = "autostarting"
      if (-not [string]::IsNullOrWhiteSpace($wechatExecutablePath)) {
        $launchResult = Start-WeChatClient -ExecutablePath $wechatExecutablePath
        if ([string]$launchResult.status -eq "launched") {
          $wechatProc = Wait-ProcessByName -Names $wechatClientNames -TimeoutSeconds $wechatAutoStartWaitSeconds
          if ($null -ne $wechatProc) {
            $wechatClientStatus = "autostarted"
            $wechatClientPid = [int]$wechatProc.Id
            $wechatClientProcessName = [string]$wechatProc.ProcessName
            $wechatClientDetail = "wechat autostarted ($wechatClientProcessName pid=$wechatClientPid)"
          } else {
            $wechatClientStatus = "autostart_timeout"
            $wechatClientDetail = "wechat launch requested but process not detected within $wechatAutoStartWaitSeconds seconds"
            if (-not ($startupWarnings -contains "WECHAT_CLIENT_AUTOSTART_TIMEOUT")) {
              $startupWarnings += "WECHAT_CLIENT_AUTOSTART_TIMEOUT"
            }
          }
        } else {
          $wechatClientStatus = "autostart_failed"
          $wechatClientDetail = [string]$launchResult.detail
          if (-not ($startupWarnings -contains "WECHAT_CLIENT_AUTOSTART_FAILED")) {
            $startupWarnings += "WECHAT_CLIENT_AUTOSTART_FAILED"
          }
        }
      } else {
        $wechatClientStatus = "executable_not_found"
        $wechatClientDetail = "wechat executable not found; set EDGE_WECHAT_EXECUTABLE_PATH"
        if (-not ($startupWarnings -contains "WECHAT_EXECUTABLE_NOT_FOUND")) {
          $startupWarnings += "WECHAT_EXECUTABLE_NOT_FOUND"
        }
      }
    } else {
      $wechatClientStatus = "not_running"
      $wechatClientDetail = "wechat process is not running and EDGE_WECHAT_AUTOSTART=false"
      if (-not ($startupWarnings -contains "WECHAT_CLIENT_NOT_RUNNING")) {
        $startupWarnings += "WECHAT_CLIENT_NOT_RUNNING"
      }
    }
  }

  if ($wechatProc) {
    $wechatRegistryCompat = Ensure-WeChatRegistryCompatibility -ExecutablePath $wechatExecutablePath
    $wechatRegistryCompatStatus = [string]$wechatRegistryCompat.status
    $wechatRegistryCompatDetail = [string]$wechatRegistryCompat.detail
    if (-not [bool]$wechatRegistryCompat.success) {
      if (-not ($startupWarnings -contains "WECHAT_REGISTRY_COMPAT_DEGRADED")) {
        $startupWarnings += "WECHAT_REGISTRY_COMPAT_DEGRADED"
      }
    }
  } else {
    $wechatRegistryCompatStatus = "skipped_no_wechat_process"
    $wechatRegistryCompatDetail = "wechat process is not available; skip registry compatibility"
  }

  if (-not $wechatProc) {
    $wechatBridgeStatus = "wechat_not_running"
    $wechatBridgeError = "wechat process not available; $wechatClientDetail"
    if (-not ($startupWarnings -contains "WECHAT_BRIDGE_SKIPPED_WECHAT_NOT_RUNNING")) {
      $startupWarnings += "WECHAT_BRIDGE_SKIPPED_WECHAT_NOT_RUNNING"
    }
  } else {
    $bridgeScript = $null
    $bridgeDependencyModule = "pyautogui"
    $bridgeDependencyHint = "pip install pyautogui pywinauto"
    $bridgeExtraArgs = @()
    $bridgeScript = Join-Path $repoRoot "tools\wechat_uia_bridge.py"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($wechatCommandQueueFile, "", $utf8NoBom)
    [System.IO.File]::WriteAllText($wechatCommandResultFile, "", $utf8NoBom)
    $bridgeExtraArgs = @(
      "--command-queue-file",
      $wechatCommandQueueFile,
      "--command-result-file",
      $wechatCommandResultFile
    )

    if (-not (Test-PythonImport -PythonPath $pythonCmd.Source -ModuleName $bridgeDependencyModule)) {
      $wechatBridgeStatus = "dependency_missing"
      $wechatBridgeError = "python dependency missing: $bridgeDependencyModule (install with: $bridgeDependencyHint)"
      if (-not ($startupWarnings -contains "WECHAT_BRIDGE_DEPENDENCY_MISSING")) {
        $startupWarnings += "WECHAT_BRIDGE_DEPENDENCY_MISSING"
      }
    } else {
      $edgeUrl = "http://127.0.0.1:$port"
      if (Test-Path -Path $bridgeScript) {
        try {
          # Truncate bridge logs on every launch to avoid stale diagnostics.
          $utf8NoBom = New-Object System.Text.UTF8Encoding $false
          [System.IO.File]::WriteAllText($wechatBridgeLog, "", $utf8NoBom)
          [System.IO.File]::WriteAllText($wechatBridgeErrLog, "", $utf8NoBom)

          $bridgeProc = Start-WeChatBridgeProcess `
            -PythonPath $pythonCmd.Source `
            -BridgeScriptPath $bridgeScript `
            -EdgeUrl $edgeUrl `
            -ExtraArgs $bridgeExtraArgs `
            -RepoRoot $repoRoot `
            -StdoutLog $wechatBridgeLog `
            -StderrLog $wechatBridgeErrLog `
            -UseBreakawayLaunch $useBreakawayLauncher `
            -LauncherScriptPath $wechatBridgeLauncherScriptPath `
            -EnvVars $envValues

          $bridgeProcId = $null
          if ($null -ne $bridgeProc) {
            try {
              $bridgeProcId = [int]$bridgeProc.Id
            } catch {
              $bridgeProcId = $null
            }
          }

          if ($null -eq $bridgeProcId -or $bridgeProcId -le 0) {
            $bridgeNeedle = [System.IO.Path]::GetFileName($bridgeScript)
            $probeDeadline = (Get-Date).AddSeconds(15)
            while ((Get-Date) -lt $probeDeadline) {
              $bridgeProcId = Find-ProcessIdByCommandLine -ImageName "python.exe" -ContainsText $bridgeNeedle
              if ($null -ne $bridgeProcId -and $bridgeProcId -gt 0) {
                break
              }
              Start-Sleep -Milliseconds 500
            }
          } else {
            Start-Sleep -Seconds 8
          }

          if ($null -eq $bridgeProcId -or $bridgeProcId -le 0) {
            $wechatBridgePid = $null
            $errTail = ""
            if (Test-Path -Path $wechatBridgeErrLog) {
              $errTail = (Get-Content -Path $wechatBridgeErrLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
            }
            $wechatBridgeStatus = "start_failed"
            $wechatBridgeError = if ($errTail) { "wechat bridge process not detected after launch.`n$errTail" } else { "wechat bridge process not detected after launch" }
            if (-not ($startupWarnings -contains "WECHAT_BRIDGE_START_FAILED")) {
              $startupWarnings += "WECHAT_BRIDGE_START_FAILED"
            }
            Remove-Item -Path $wechatBridgePidFile -Force -ErrorAction SilentlyContinue
          } else {
            $bridgeAlive = Get-Process -Id $bridgeProcId -ErrorAction SilentlyContinue
            if ($null -eq $bridgeAlive) {
              $errTail = ""
              if (Test-Path -Path $wechatBridgeErrLog) {
                $errTail = (Get-Content -Path $wechatBridgeErrLog -Tail 40 -ErrorAction SilentlyContinue) -join "`n"
              }
              $wechatBridgePid = $null
              $wechatBridgeStatus = "start_failed"
              $wechatBridgeError = if ($errTail) { "wechat bridge exited immediately.`n$errTail" } else { "wechat bridge exited immediately without stderr output" }
              if (-not ($startupWarnings -contains "WECHAT_BRIDGE_START_FAILED")) {
                $startupWarnings += "WECHAT_BRIDGE_START_FAILED"
              }
              Remove-Item -Path $wechatBridgePidFile -Force -ErrorAction SilentlyContinue
            } else {
              $wechatBridgePid = [int]$bridgeProcId
              Set-Content -Path $wechatBridgePidFile -Value ([string]$wechatBridgePid) -Encoding ascii
              $wechatBridgeStatus = "started"
            }
          }
        } catch {
          $wechatBridgePid = $null
          $wechatBridgeStatus = "start_failed"
          $wechatBridgeError = $_.Exception.Message
          if (-not ($startupWarnings -contains "WECHAT_BRIDGE_START_FAILED")) {
            $startupWarnings += "WECHAT_BRIDGE_START_FAILED"
          }
        }
      } else {
        $wechatBridgeStatus = "script_not_found"
        $wechatBridgeError = "script not found: $bridgeScript"
        if (-not ($startupWarnings -contains "WECHAT_BRIDGE_SCRIPT_NOT_FOUND")) {
          $startupWarnings += "WECHAT_BRIDGE_SCRIPT_NOT_FOUND"
        }
      }
    }
  }
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
  wechat_autostart_enabled = $wechatAutoStart
  wechat_autostart_wait_seconds = $wechatAutoStartWaitSeconds
  wechat_executable_path = $wechatExecutablePath
  wechat_client_status = $wechatClientStatus
  wechat_client_detail = $wechatClientDetail
  wechat_client_process_name = $wechatClientProcessName
  wechat_client_pid = $wechatClientPid
  wechat_registry_compat_status = $wechatRegistryCompatStatus
  wechat_registry_compat_detail = $wechatRegistryCompatDetail
  wechat_bridge_enabled = $wechatBridgeEnabled
  wechat_bridge_status = $wechatBridgeStatus
  wechat_bridge_pid = $wechatBridgePid
  wechat_bridge_pid_file = if ($wechatBridgePid) { $wechatBridgePidFile } else { $null }
  wechat_bridge_error = $wechatBridgeError
  wechat_bridge_log = if ($wechatBridgeEnabled) { $wechatBridgeLog } else { $null }
  wechat_bridge_error_log = if ($wechatBridgeEnabled) { $wechatBridgeErrLog } else { $null }
  wechat_bridge_env_raw = $wechatBridgeEnvRaw
  wechat_bridge_backend = $wechatBridgeBackend
  wechat_bridge_backend_raw = $wechatBridgeBackendRaw
  wechat_bridge_command_queue_file = if ($wechatBridgeBackend -eq "uia") { $wechatCommandQueueFile } else { $null }
  wechat_bridge_command_result_file = if ($wechatBridgeBackend -eq "uia") { $wechatCommandResultFile } else { $null }
  wechat_bridge_callback_url_local = "http://127.0.0.1:$port/v1/edge/wechat/callback"
  enforce_cn_asr_ja_tts_mode = $EnforceCnAsrJaTtsMode
  asr_language = $resolvedAsrLanguage
  tts_language = $resolvedTtsLanguage
  barge_in_enabled = $resolvedBargeInEnabled
  startup_env_defaults_applied = $startupEnvDefaultsApplied
  startup_env_enforced_applied = $startupEnvEnforcedApplied
  startup_warnings = $startupWarnings
}
$result | ConvertTo-Json -Depth 4
