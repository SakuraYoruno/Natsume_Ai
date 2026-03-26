[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function ConvertFrom-QQOneBotBoolText {
  param(
    [string]$Raw,
    [bool]$Default = $false
  )
  if ([string]::IsNullOrWhiteSpace($Raw)) {
    return $Default
  }
  $normalized = $Raw.Trim().ToLowerInvariant()
  return @('1', 'true', 'yes', 'on') -contains $normalized
}

function Get-QQOneBotApiUri {
  param([string]$ApiBase)
  if ([string]::IsNullOrWhiteSpace($ApiBase)) {
    return $null
  }
  try {
    return [Uri]$ApiBase
  } catch {
    return $null
  }
}

function Get-QQOneBotHealthUrl {
  param(
    [string]$ApiBase,
    [string]$Override = ''
  )
  if (-not [string]::IsNullOrWhiteSpace($Override)) {
    return $Override.Trim()
  }
  if ([string]::IsNullOrWhiteSpace($ApiBase)) {
    return ''
  }
  return ($ApiBase.TrimEnd('/')) + '/get_status'
}

function Get-QQOneBotPortOwnerPid {
  param([string]$ApiBase)
  $uri = Get-QQOneBotApiUri -ApiBase $ApiBase
  if ($null -eq $uri) {
    return $null
  }
  $hostName = [string]$uri.Host
  if ([string]::IsNullOrWhiteSpace($hostName)) {
    return $null
  }
  $normalizedHost = $hostName.Trim().ToLowerInvariant()
  if (@('127.0.0.1', 'localhost', '0.0.0.0') -notcontains $normalizedHost) {
    return $null
  }
  $probePort = [int]$uri.Port
  if ($probePort -le 0) {
    return $null
  }
  $conn = Get-NetTCPConnection -LocalPort $probePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Test-QQOneBotHealth {
  param(
    [string]$ApiBase,
    [string]$HealthUrl = '',
    [int]$TimeoutSeconds = 5
  )
  $resolvedHealthUrl = Get-QQOneBotHealthUrl -ApiBase $ApiBase -Override $HealthUrl
  if ([string]::IsNullOrWhiteSpace($resolvedHealthUrl)) {
    return [pscustomobject]@{
      success = $false
      detail = 'health url not configured'
      response = $null
      health_url = ''
    }
  }

  try {
    $response = Invoke-RestMethod -Uri $resolvedHealthUrl -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec $TimeoutSeconds
    $responseStatus = [string]$response.status
    $responseRetcode = $response.retcode
    if ($responseStatus -eq 'ok' -or $responseRetcode -eq 0) {
      return [pscustomobject]@{
        success = $true
        detail = 'onebot health check passed'
        response = $response
        health_url = $resolvedHealthUrl
      }
    }
    return [pscustomobject]@{
      success = $false
      detail = "onebot health returned non-ok status=$responseStatus retcode=$responseRetcode"
      response = $response
      health_url = $resolvedHealthUrl
    }
  } catch {
    try {
      $response = Invoke-RestMethod -Uri $resolvedHealthUrl -Method Get -TimeoutSec $TimeoutSeconds
      $responseStatus = [string]$response.status
      $responseRetcode = $response.retcode
      if ($responseStatus -eq 'ok' -or $responseRetcode -eq 0) {
        return [pscustomobject]@{
          success = $true
          detail = 'onebot health check passed'
          response = $response
          health_url = $resolvedHealthUrl
        }
      }
      return [pscustomobject]@{
        success = $false
        detail = "onebot health returned non-ok status=$responseStatus retcode=$responseRetcode"
        response = $response
        health_url = $resolvedHealthUrl
      }
    } catch {
      return [pscustomobject]@{
        success = $false
        detail = [string]$_.Exception.Message
        response = $null
        health_url = $resolvedHealthUrl
      }
    }
  }
}

function Read-QQOneBotState {
  param([string]$StateFile)
  if ([string]::IsNullOrWhiteSpace($StateFile) -or -not (Test-Path -Path $StateFile)) {
    return $null
  }
  try {
    $raw = Get-Content -Path $StateFile -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
      return $null
    }
    return ($raw | ConvertFrom-Json)
  } catch {
    return $null
  }
}

function Write-QQOneBotState {
  param(
    [string]$StateFile,
    [hashtable]$State
  )
  if ([string]::IsNullOrWhiteSpace($StateFile)) {
    return
  }
  $dir = Split-Path -Parent $StateFile
  if (-not [string]::IsNullOrWhiteSpace($dir) -and -not (Test-Path -Path $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  $json = ($State | ConvertTo-Json -Depth 8)
  Set-Content -Path $StateFile -Value $json -Encoding UTF8
}

function Remove-QQOneBotState {
  param([string]$StateFile)
  if (-not [string]::IsNullOrWhiteSpace($StateFile) -and (Test-Path -Path $StateFile)) {
    Remove-Item -Path $StateFile -Force -ErrorAction SilentlyContinue
  }
}

function ConvertTo-QQOneBotPsLiteral {
  param([string]$Value)
  if ($null -eq $Value) {
    return "''"
  }
  return "'" + ($Value.Replace("'", "''")) + "'"
}

function Invoke-QQOneBotBreakawayTask {
  param(
    [string]$TaskName,
    [string]$TaskCommand,
    [string]$FailureContext = 'qq onebot breakaway launch',
    [bool]$InteractiveTask = $true
  )

  $schtasksCmd = Get-Command schtasks.exe -ErrorAction SilentlyContinue
  if ($null -eq $schtasksCmd) {
    throw 'schtasks.exe not found; cannot launch qq onebot breakaway process'
  }

  $targetTime = (Get-Date).AddMinutes(3)
  $taskTime = $targetTime.ToString('HH:mm')
  $taskDateCandidates = @(
    $targetTime.ToString('yyyy/MM/dd', [System.Globalization.CultureInfo]::InvariantCulture),
    $targetTime.ToString('MM/dd/yyyy', [System.Globalization.CultureInfo]::InvariantCulture)
  )

  $created = $false
  $createErrors = New-Object System.Collections.Generic.List[string]
  foreach ($taskDate in $taskDateCandidates) {
    $createOutput = @()
    $createExitCode = 1
    $createArgs = @(
      '/Create', '/TN', $TaskName, '/SC', 'ONCE', '/SD', $taskDate, '/ST', $taskTime, '/TR', $TaskCommand, '/F'
    )
    if ($InteractiveTask) {
      $createArgs += '/IT'
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
      '/Create', '/TN', $TaskName, '/SC', 'ONCE', '/ST', $taskTime, '/TR', $TaskCommand, '/F'
    )
    if ($InteractiveTask) {
      $createArgs += '/IT'
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
      $detail = 'unknown create failure'
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

function Start-QQOneBotProcess {
  param(
    [pscustomobject]$StartSpec,
    [string]$RuntimeRoot,
    [bool]$UseBreakawayLaunch = $false
  )

  $argumentList = @($StartSpec.argument_list)
  if (-not $UseBreakawayLaunch) {
    $startArgs = @{
      FilePath = [string]$StartSpec.file_path
      WorkingDirectory = [string]$StartSpec.working_directory
      PassThru = $true
      WindowStyle = 'Hidden'
    }
    if ($argumentList.Count -gt 0) {
      $startArgs['ArgumentList'] = $argumentList
    }
    return (Start-Process @startArgs)
  }

  if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    throw 'runtime root is required for qq onebot breakaway launch'
  }
  if (-not (Test-Path -Path $RuntimeRoot)) {
    New-Item -ItemType Directory -Path $RuntimeRoot -Force | Out-Null
  }
  $logDir = Join-Path $RuntimeRoot 'logs'
  if (-not (Test-Path -Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
  }

  $launcherScript = Join-Path $RuntimeRoot 'launch_qq_onebot.ps1'
  $stdoutLog = Join-Path $logDir 'qq_onebot.stdout.log'
  $stderrLog = Join-Path $logDir 'qq_onebot.stderr.log'
  $launchPidFile = Join-Path $RuntimeRoot 'qq_onebot.launch.pid'

  $launcherLines = @(
    '$ErrorActionPreference = ''Stop''', 
    '$argumentList = @('
  )
  foreach ($arg in $argumentList) {
    $launcherLines += ('  ' + (ConvertTo-QQOneBotPsLiteral -Value ([string]$arg)))
  }
  $launcherLines += @(
    ')',
    '$startArgs = @{',
    ('  FilePath = ' + (ConvertTo-QQOneBotPsLiteral -Value ([string]$StartSpec.file_path))),
    ('  WorkingDirectory = ' + (ConvertTo-QQOneBotPsLiteral -Value ([string]$StartSpec.working_directory))),
    '  PassThru = $true',
    '  WindowStyle = ''Hidden''',
    ('  RedirectStandardOutput = ' + (ConvertTo-QQOneBotPsLiteral -Value $stdoutLog)),
    ('  RedirectStandardError = ' + (ConvertTo-QQOneBotPsLiteral -Value $stderrLog)),
    '}',
    'if ($argumentList.Count -gt 0) {',
    '  $startArgs[''ArgumentList''] = $argumentList',
    '}',
    '$proc = Start-Process @startArgs',
    'if ($null -ne $proc) {',
    ('  Set-Content -Path ' + (ConvertTo-QQOneBotPsLiteral -Value $launchPidFile) + ' -Value ([string]$proc.Id) -Encoding ascii'),
    '}'
  )
  Set-Content -Path $launcherScript -Value $launcherLines -Encoding UTF8

  $launcherEscaped = ([string]$launcherScript).Replace('"', '""')
  $taskName = "NatsumeQQOneBot_$([Guid]::NewGuid().ToString('N').Substring(0, 10))"
  $taskCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$launcherEscaped`""
  Invoke-QQOneBotBreakawayTask -TaskName $taskName -TaskCommand $taskCommand -FailureContext 'qq onebot launch' -InteractiveTask $true

  return [pscustomobject]@{
    Id = 0
    launch_mode = 'breakaway'
    launcher_script = $launcherScript
    stdout_log = $stdoutLog
    stderr_log = $stderrLog
  }
}

function Resolve-QQOneBotStartSpec {
  param(
    [string]$LauncherPath,
    [string]$LauncherArgs,
    [string]$WorkDir,
    [string]$SelfId,
    [string]$ApiBase
  )
  if ([string]::IsNullOrWhiteSpace($LauncherPath)) {
    throw 'EDGE_QQ_ONEBOT_LAUNCHER_PATH is required when EDGE_QQ_ONEBOT_AUTOSTART=true'
  }
  $resolvedPath = [Environment]::ExpandEnvironmentVariables($LauncherPath.Trim())
  if (-not (Test-Path -Path $resolvedPath)) {
    throw "onebot launcher not found: $resolvedPath"
  }
  $resolvedArgs = [string]$LauncherArgs
  if (-not [string]::IsNullOrEmpty($resolvedArgs)) {
    $resolvedArgs = $resolvedArgs.Replace('{QQ_SELF_ID}', [string]$SelfId)
    $resolvedArgs = $resolvedArgs.Replace('{QQ_ONEBOT_API_BASE}', [string]$ApiBase)
  }
  $resolvedWorkDir = if (-not [string]::IsNullOrWhiteSpace($WorkDir)) {
    [Environment]::ExpandEnvironmentVariables($WorkDir.Trim())
  } else {
    Split-Path -Parent $resolvedPath
  }
  $ext = [IO.Path]::GetExtension($resolvedPath).ToLowerInvariant()
  switch ($ext) {
    '.cmd' {
      $commandLine = if ([string]::IsNullOrWhiteSpace($resolvedArgs)) {
        ('call "{0}"' -f $resolvedPath)
      } else {
        ('call "{0}" {1}' -f $resolvedPath, $resolvedArgs)
      }
      return [pscustomobject]@{
        file_path = 'cmd.exe'
        argument_list = @('/c', $commandLine)
        working_directory = $resolvedWorkDir
        launcher_path = $resolvedPath
        launcher_args = $resolvedArgs
      }
    }
    '.bat' {
      $commandLine = if ([string]::IsNullOrWhiteSpace($resolvedArgs)) {
        ('call "{0}"' -f $resolvedPath)
      } else {
        ('call "{0}" {1}' -f $resolvedPath, $resolvedArgs)
      }
      return [pscustomobject]@{
        file_path = 'cmd.exe'
        argument_list = @('/c', $commandLine)
        working_directory = $resolvedWorkDir
        launcher_path = $resolvedPath
        launcher_args = $resolvedArgs
      }
    }
    '.ps1' {
      $argumentList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $resolvedPath)
      if (-not [string]::IsNullOrWhiteSpace($resolvedArgs)) {
        $argumentList += $resolvedArgs
      }
      return [pscustomobject]@{
        file_path = 'powershell.exe'
        argument_list = $argumentList
        working_directory = $resolvedWorkDir
        launcher_path = $resolvedPath
        launcher_args = $resolvedArgs
      }
    }
    default {
      $argumentList = @()
      if (-not [string]::IsNullOrWhiteSpace($resolvedArgs)) {
        $argumentList += $resolvedArgs
      }
      return [pscustomobject]@{
        file_path = $resolvedPath
        argument_list = $argumentList
        working_directory = $resolvedWorkDir
        launcher_path = $resolvedPath
        launcher_args = $resolvedArgs
      }
    }
  }
}

function Stop-QQDesktopProcesses {
  $processes = @(Get-Process -Name 'QQ' -ErrorAction SilentlyContinue)
  if ($processes.Count -le 0) {
    return 0
  }
  foreach ($process in $processes) {
    try {
      Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    } catch {
    }
  }
  Start-Sleep -Seconds 2
  return $processes.Count
}

function Ensure-QQOneBotRunning {
  param(
    [hashtable]$EnvValues,
    [string]$RuntimeRoot
  )
  $apiBase = [string]$EnvValues['QQ_ONEBOT_API_BASE']
  $selfId = [string]$EnvValues['QQ_SELF_ID']
  $autostart = ConvertFrom-QQOneBotBoolText -Raw ([string]$EnvValues['EDGE_QQ_ONEBOT_AUTOSTART']) -Default $false
  $killQQBeforeStart = ConvertFrom-QQOneBotBoolText -Raw ([string]$EnvValues['EDGE_QQ_ONEBOT_KILL_QQ_BEFORE_START']) -Default $false
  $sshSessionDetected = (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CONNECTION)) -or (-not [string]::IsNullOrWhiteSpace([string]$env:SSH_CLIENT))
  $launcherPath = [string]$EnvValues['EDGE_QQ_ONEBOT_LAUNCHER_PATH']
  $launcherArgs = [string]$EnvValues['EDGE_QQ_ONEBOT_LAUNCHER_ARGS']
  $workDir = [string]$EnvValues['EDGE_QQ_ONEBOT_WORKDIR']
  $healthUrl = Get-QQOneBotHealthUrl -ApiBase $apiBase -Override ([string]$EnvValues['EDGE_QQ_ONEBOT_HEALTH_URL'])
  $waitSeconds = 60
  try {
    $rawWait = [string]$EnvValues['EDGE_QQ_ONEBOT_START_WAIT_SECONDS']
    if (-not [string]::IsNullOrWhiteSpace($rawWait)) {
      $waitSeconds = [Math]::Max(5, [Math]::Min(600, [int]$rawWait))
    }
  } catch {
    $waitSeconds = 60
  }
  $stateFile = Join-Path $RuntimeRoot 'qq_onebot.state.json'

  $result = [ordered]@{
    enabled = (-not [string]::IsNullOrWhiteSpace($apiBase))
    api_base = $apiBase
    health_url = $healthUrl
    autostart_enabled = $autostart
    kill_qq_before_start = $killQQBeforeStart
    ssh_session_detected = $sshSessionDetected
    breakaway_launch = $sshSessionDetected
    launcher_path = $launcherPath
    launcher_args = $launcherArgs
    working_directory = $workDir
    wait_seconds = $waitSeconds
    state_file = $stateFile
    status = 'disabled'
    detail = 'qq onebot disabled because QQ_ONEBOT_API_BASE is empty'
    owned = $false
    pid = $null
  }

  if ([string]::IsNullOrWhiteSpace($apiBase)) {
    Remove-QQOneBotState -StateFile $stateFile
    return [pscustomobject]$result
  }

  $health = Test-QQOneBotHealth -ApiBase $apiBase -HealthUrl $healthUrl -TimeoutSeconds 5
  if ($health.success) {
    $result.status = 'already_running'
    $result.detail = [string]$health.detail
    $result.pid = Get-QQOneBotPortOwnerPid -ApiBase $apiBase
    Write-QQOneBotState -StateFile $stateFile -State @{
      owned = $false
      pid = $result.pid
      api_base = $apiBase
      health_url = $healthUrl
      observed_at = (Get-Date).ToString('o')
    }
    return [pscustomobject]$result
  }

  if (-not $autostart) {
    $result.status = 'not_running'
    $result.detail = 'qq onebot not reachable and EDGE_QQ_ONEBOT_AUTOSTART=false'
    Remove-QQOneBotState -StateFile $stateFile
    return [pscustomobject]$result
  }

  try {
    if ($killQQBeforeStart) {
      $stoppedQQCount = Stop-QQDesktopProcesses
      if ($stoppedQQCount -gt 0) {
        $result.detail = "stopped $stoppedQQCount existing QQ process(es) before onebot autostart"
      }
    }
    $startSpec = Resolve-QQOneBotStartSpec -LauncherPath $launcherPath -LauncherArgs $launcherArgs -WorkDir $workDir -SelfId $selfId -ApiBase $apiBase
    $proc = Start-QQOneBotProcess -StartSpec $startSpec -RuntimeRoot $RuntimeRoot -UseBreakawayLaunch $sshSessionDetected
    $deadline = (Get-Date).AddSeconds($waitSeconds)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
      Start-Sleep -Milliseconds 1000
      $health = Test-QQOneBotHealth -ApiBase $apiBase -HealthUrl $healthUrl -TimeoutSeconds 5
      if ($health.success) {
        $healthy = $true
        break
      }
    }
    if (-not $healthy) {
      $result.status = 'autostart_timeout'
      $result.detail = "qq onebot did not become healthy within $waitSeconds seconds"
      $result.pid = Get-QQOneBotPortOwnerPid -ApiBase $apiBase
      Remove-QQOneBotState -StateFile $stateFile
      return [pscustomobject]$result
    }

    $managedPid = Get-QQOneBotPortOwnerPid -ApiBase $apiBase
    if ($null -eq $managedPid -and $null -ne $proc) {
      $managedPid = [int]$proc.Id
    }
    $result.status = 'autostarted'
    $result.detail = 'qq onebot autostart succeeded'
    $result.owned = $true
    $result.pid = $managedPid
    Write-QQOneBotState -StateFile $stateFile -State @{
      owned = $true
      pid = $managedPid
      api_base = $apiBase
      health_url = $healthUrl
      launcher_path = [string]$startSpec.launcher_path
      launcher_args = [string]$startSpec.launcher_args
      working_directory = [string]$startSpec.working_directory
      started_at = (Get-Date).ToString('o')
    }
    return [pscustomobject]$result
  } catch {
    $result.status = 'autostart_failed'
    $result.detail = [string]$_.Exception.Message
    Remove-QQOneBotState -StateFile $stateFile
    return [pscustomobject]$result
  }
}

function Stop-QQOneBotIfOwned {
  param(
    [string]$RuntimeRoot,
    [string]$ApiBase = ''
  )
  $stateFile = Join-Path $RuntimeRoot 'qq_onebot.state.json'
  $state = Read-QQOneBotState -StateFile $stateFile
  if ($null -eq $state) {
    return [pscustomobject]@{
      status = 'not_managed'
      pid = $null
      owned = $false
      detail = 'qq onebot state file not found'
      state_file = $stateFile
    }
  }

  $owned = [bool]$state.owned
  $resolvedApiBase = if (-not [string]::IsNullOrWhiteSpace($ApiBase)) { $ApiBase } else { [string]$state.api_base }
  if (-not $owned) {
    Remove-QQOneBotState -StateFile $stateFile
    return [pscustomobject]@{
      status = 'not_owned'
      pid = $state.pid
      owned = $false
      detail = 'qq onebot was not started by edge runtime management'
      state_file = $stateFile
    }
  }

  $stopped = $false
  $stoppedPid = $null
  $detail = 'qq onebot process already exited'
  $candidatePid = $null
  try {
    if ($null -ne $state.pid) {
      $candidatePid = [int]$state.pid
    }
  } catch {
    $candidatePid = $null
  }
  if ($null -ne $candidatePid) {
    $proc = Get-Process -Id $candidatePid -ErrorAction SilentlyContinue
    if ($null -ne $proc) {
      Stop-Process -Id $candidatePid -Force -ErrorAction SilentlyContinue
      $stopped = $true
      $stoppedPid = $candidatePid
      $detail = 'qq onebot process stopped by managed pid'
    }
  }

  if (-not $stopped) {
    $portPid = Get-QQOneBotPortOwnerPid -ApiBase $resolvedApiBase
    if ($null -ne $portPid) {
      Stop-Process -Id $portPid -Force -ErrorAction SilentlyContinue
      $stopped = $true
      $stoppedPid = $portPid
      $detail = 'qq onebot process stopped by api port probe'
    }
  }

  Remove-QQOneBotState -StateFile $stateFile
  return [pscustomobject]@{
    status = $(if ($stopped) { 'stopped' } else { 'not_running' })
    pid = $stoppedPid
    owned = $true
    detail = $detail
    state_file = $stateFile
  }
}

