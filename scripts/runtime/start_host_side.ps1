[CmdletBinding()]
param(
  [string]$EnvFile = "",
  [string]$RuntimeRoot = "",
  [string]$LogDir = "",
  [string]$CanonicalDbPath = "",
  [string]$AgentWorkspaceRoot = "",
  [string]$AgentWorkspaceHmacSecretRef = "",
  [string]$BindHost = "",
  [int]$Port = 0,
  [ValidateSet("docker", "none")]
  [string]$ExternalServicesBackend = "docker",
  [string]$DockerComposeFile = "",
  [string]$DockerProjectName = "natsume-ext",
  [int]$WaitSeconds = 40,
  [int]$Neo4jWaitSeconds = 90,
  [int]$ChromaWaitSeconds = 30,
  [switch]$ForceRestart,
  [switch]$StartupCheck,
  [switch]$ShowConsoleWindow,
  [bool]$BreakawayFromConsole = $true,
  [switch]$EnableMonitorProxy,
  [switch]$DisableMonitorProxy,
  [string]$MonitorProxyHost = "",
  [int]$MonitorProxyPort = 0,
  [string]$NginxExe = ""
)

$ErrorActionPreference = "Stop"

function Read-EnvFile {
  param([string]$Path)
  $values = @{}
  if (-not (Test-Path -Path $Path)) {
    return $values
  }
  foreach ($line in Get-Content -Path $Path -Encoding UTF8) {
    $raw = [string]$line
    $trimmed = $raw.Trim()
    if (-not $trimmed -or $trimmed.StartsWith("#")) {
      continue
    }
    $sep = $trimmed.IndexOf("=")
    if ($sep -lt 1) {
      continue
    }
    $key = $trimmed.Substring(0, $sep).Trim()
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
    $existing = [Environment]::GetEnvironmentVariable([string]$key, "Process")
    if ([string]::IsNullOrWhiteSpace([string]$existing)) {
      [Environment]::SetEnvironmentVariable($key, [string]$Values[$key], "Process")
    }
  }
}

function Ensure-Directory {
  param([string]$Path)
  if (-not (Test-Path -Path $Path)) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
  }
}

function Get-NormalizedDirectoryPath {
  param(
    [string]$Path,
    [string]$BasePath
  )
  if ([string]::IsNullOrWhiteSpace($Path)) {
    throw "directory path must not be empty"
  }
  $candidate = [Environment]::ExpandEnvironmentVariables($Path.Trim())
  if (-not [System.IO.Path]::IsPathRooted($candidate)) {
    $candidate = Join-Path $BasePath $candidate
  }
  return [System.IO.Path]::GetFullPath($candidate).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
  )
}

function Test-DirectoryPathsOverlap {
  param(
    [string]$Left,
    [string]$Right
  )
  $comparison = [System.StringComparison]::OrdinalIgnoreCase
  $leftPath = $Left.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
  )
  $rightPath = $Right.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
  )
  if ([string]::Equals($leftPath, $rightPath, $comparison)) {
    return $true
  }
  $separator = [string][System.IO.Path]::DirectorySeparatorChar
  return (
    $leftPath.StartsWith($rightPath + $separator, $comparison) -or
    $rightPath.StartsWith($leftPath + $separator, $comparison)
  )
}

function Assert-AgentWorkspaceRootSeparated {
  param(
    [string]$WorkspaceRoot,
    [string]$SourceRepository
  )
  if (Test-DirectoryPathsOverlap -Left $WorkspaceRoot -Right $SourceRepository) {
    throw (
      "HOST_AGENT_WORKSPACE_ROOT_OVERLAPS_SOURCE: " +
      "managed workspace root must be outside the source repository tree"
    )
  }
}

function ConvertTo-CmdArgument {
  param([AllowNull()][string]$Value)
  if ($null -eq $Value) {
    return '""'
  }
  $text = [string]$Value
  if (-not $text) {
    return '""'
  }
  if ($text -notmatch '[\s"]') {
    return $text
  }
  return '"' + ($text -replace '"', '\"') + '"'
}

function Join-CmdArguments {
  param([string[]]$Arguments)
  $parts = @()
  foreach ($arg in @($Arguments)) {
    $parts += (ConvertTo-CmdArgument -Value ([string]$arg))
  }
  return ($parts -join ' ')
}

function Invoke-ChildPowerShellScript {
  param(
    [string]$ScriptPath,
    [hashtable]$NamedArguments,
    [ValidateRange(1, 86400)]
    [int]$TimeoutSeconds = 120
  )

  # Do not capture a native child through PowerShell's pipeline here.  The
  # startup scripts intentionally launch long-lived descendants (for example
  # nginx).  A descendant can inherit the pipeline's stdout handle, keeping the
  # pipeline open after the child PowerShell process has exited and making this
  # orchestrator wait for the service itself to stop.
  #
  # WMI creates the direct child outside this process' stdio handle tree.  The
  # child writes its success/error streams to files from inside its own
  # runspace, so a service descendant never receives an orchestrator pipe or a
  # redirected output-file handle.  We then wait only for the WMI-created PID.
  $stdoutPath = [System.IO.Path]::GetTempFileName()
  $stderrPath = [System.IO.Path]::GetTempFileName()
  $exitCodePath = [System.IO.Path]::GetTempFileName()
  try {
    $invocationParts = @(
      "&",
      ("'{0}'" -f ([string]$ScriptPath).Replace("'", "''"))
    )
    foreach ($entry in @($NamedArguments.GetEnumerator() | Sort-Object Key)) {
      $name = "-$([string]$entry.Key)"
      $value = $entry.Value
      if ($value -is [bool] -or $value -is [System.Management.Automation.SwitchParameter]) {
        if ([bool]$value) {
          $invocationParts += $name
        }
        continue
      }
      if ($null -eq $value) {
        continue
      }
      $invocationParts += $name
      $invocationParts += ("'{0}'" -f ([string]$value).Replace("'", "''"))
    }

    $stdoutLiteral = "'{0}'" -f $stdoutPath.Replace("'", "''")
    $stderrLiteral = "'{0}'" -f $stderrPath.Replace("'", "''")
    $exitCodeLiteral = "'{0}'" -f $exitCodePath.Replace("'", "''")
    $invocationLine = $invocationParts -join " "
    $childSource = @"
`$ErrorActionPreference = "Stop"
`$childExitCode = 0
try {
  $invocationLine | Set-Content -LiteralPath $stdoutLiteral -Encoding UTF8
} catch {
  `$childExitCode = 1
  (`$_ | Out-String) | Set-Content -LiteralPath $stderrLiteral -Encoding UTF8
}
[System.IO.File]::WriteAllText($exitCodeLiteral, [string]`$childExitCode)
exit `$childExitCode
"@

    $encodedCommand = [Convert]::ToBase64String(
      [System.Text.Encoding]::Unicode.GetBytes($childSource)
    )
    $powershellExe = Join-Path $PSHOME "powershell.exe"
    $childCommandLine = @(
      (ConvertTo-CmdArgument -Value $powershellExe),
      "-NoProfile",
      "-NonInteractive",
      "-ExecutionPolicy",
      "Bypass",
      "-EncodedCommand",
      $encodedCommand
    ) -join " "
    $startup = ([wmiclass]"Win32_ProcessStartup").CreateInstance()
    $startup.ShowWindow = 0
    $createResult = ([wmiclass]"Win32_Process").Create(
      $childCommandLine,
      (Get-Location).Path,
      $startup
    )
    if ([int]$createResult.ReturnValue -ne 0) {
      throw "failed to launch child startup script: return=$($createResult.ReturnValue); script=$ScriptPath"
    }
    $childProcess = Get-Process -Id ([int]$createResult.ProcessId) -ErrorAction SilentlyContinue
    if ($null -ne $childProcess) {
      # The bounded wait observes only this WMI-created PowerShell, not its
      # background service descendants.  A hung startup script must not stall
      # the whole Host orchestrator indefinitely.
      $completed = $childProcess.WaitForExit([int]($TimeoutSeconds * 1000))
      if (-not $completed) {
        $childPid = [int]$childProcess.Id
        $terminationStatus = "process_tree_termination_requested"
        try {
          # Kill the tree while the direct wrapper still exists.  Killing the
          # wrapper first would orphan a Python/nginx service it already
          # launched and make the next startup collide with the occupied port.
          $taskkillExe = Join-Path $env:SystemRoot "System32\taskkill.exe"
          if (-not (Test-Path -LiteralPath $taskkillExe)) {
            throw "taskkill.exe not found: $taskkillExe"
          }
          & $taskkillExe /PID $childPid /T /F 2>$null | Out-Null
          if ($LASTEXITCODE -ne 0) {
            throw "taskkill.exe failed with exit code $LASTEXITCODE for pid $childPid"
          }
          if ($childProcess.WaitForExit(5000)) {
            $terminationStatus = "process_tree_terminated"
          } else {
            $childProcess.Kill()
            if ($childProcess.WaitForExit(5000)) {
              $terminationStatus = "direct_child_terminated_tree_unconfirmed"
            } else {
              $terminationStatus = "process_tree_termination_incomplete"
            }
          }
        } catch {
          try {
            $childProcess.Kill()
            $null = $childProcess.WaitForExit(5000)
          } catch {
          }
          $terminationStatus = "process_tree_termination_failed: $($_.Exception.Message)"
        }
        throw "child startup script timed out after $TimeoutSeconds seconds; pid=$childPid; cleanup=$terminationStatus; script=$ScriptPath"
      }
    }

    $exitCodeText = [System.IO.File]::ReadAllText($exitCodePath).Trim()
    $exitCode = if ($exitCodeText) { [int]$exitCodeText } else { 1 }
    $stdoutText = [System.IO.File]::ReadAllText($stdoutPath)
    $stderrText = [System.IO.File]::ReadAllText($stderrPath)
    if ($exitCode -ne 0) {
      $detail = $stderrText.Trim()
      if ($detail) {
        throw "child startup script failed with exit code $exitCode`: $ScriptPath`n$detail"
      }
      throw "child startup script failed with exit code $exitCode`: $ScriptPath"
    }
    return $stdoutText
  } finally {
    Remove-Item -LiteralPath $stdoutPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stderrPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $exitCodePath -Force -ErrorAction SilentlyContinue
  }
}

function Test-SafeOperatorQueryAuthBindingsSnapshot {
  param([AllowNull()][string]$Value)
  $raw = ([string]$Value).Trim()
  if (-not $raw -or $raw.Length -gt 65536 -or -not $raw.StartsWith("[")) {
    return $false
  }
  try {
    $decoded = $raw | ConvertFrom-Json -ErrorAction Stop
  } catch {
    return $false
  }
  $bindings = @($decoded)
  if ($bindings.Count -lt 1) {
    return $false
  }
  $allowedFields = @(
    "token_sha256",
    "principal_id",
    "tenant_id",
    "key_id",
    "principal_kind",
    "scopes"
  )
  foreach ($binding in $bindings) {
    if ($null -eq $binding) {
      return $false
    }
    $fieldNames = @($binding.PSObject.Properties.Name)
    if ($fieldNames.Count -lt 3 -or @($fieldNames | Where-Object { $_ -notin $allowedFields }).Count -gt 0) {
      return $false
    }
    if ([string]$binding.token_sha256 -notmatch '^[a-f0-9]{64}$') {
      return $false
    }
    if ([string]$binding.principal_id -notmatch '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$') {
      return $false
    }
    if ($binding.PSObject.Properties.Name -contains "tenant_id" -and [string]$binding.tenant_id -notmatch '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$') {
      return $false
    }
    if ($binding.PSObject.Properties.Name -contains "key_id" -and [string]$binding.key_id -notmatch '^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$') {
      return $false
    }
    if ($binding.PSObject.Properties.Name -contains "principal_kind" -and [string]$binding.principal_kind -notin @("user", "service")) {
      return $false
    }
    $scopes = @($binding.scopes)
    if ($scopes.Count -lt 1 -or @($scopes | Where-Object { [string]$_ -notmatch '^[a-z0-9][a-z0-9:._/-]{0,127}$' }).Count -gt 0) {
      return $false
    }
  }
  return $true
}

function Test-PathWithinDirectory {
  param(
    [string]$Path,
    [string]$Directory
  )
  $comparison = [System.StringComparison]::OrdinalIgnoreCase
  $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
  )
  $root = [System.IO.Path]::GetFullPath($Directory).TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
  )
  $separator = [string][System.IO.Path]::DirectorySeparatorChar
  return (
    [string]::Equals($candidate, $root, $comparison) -or
    $candidate.StartsWith($root + $separator, $comparison)
  )
}

function Test-SensitiveRuntimeEnvEntry {
  param(
    [string]$Name,
    [AllowNull()][string]$Value
  )
  $normalizedName = ([string]$Name).Trim()
  $normalizedValue = ([string]$Value).Trim()
  if (-not $normalizedName) {
    return $false
  }
  # Neo4j direct auth is an indivisible runtime credential.  The password is
  # intentionally excluded below, so retaining only its username would create
  # an unusable half-credential when this snapshot is reused.  Persist the
  # sec:// auth ref instead and resolve both values in the Docker launcher.
  if (
    [string]::Equals(
      $normalizedName,
      "NATSUME_NEO4J_USER",
      [System.StringComparison]::OrdinalIgnoreCase
    )
  ) {
    return $true
  }
  if (
    $normalizedName -match '(?i)_REF$' -and
    $normalizedValue.StartsWith("sec://", [System.StringComparison]::Ordinal)
  ) {
    return $false
  }
  # An operator query binding holds only SHA-256 digests, identities, and
  # scopes. It is not an authentication credential and must survive the
  # detached Host/OBS launch boundary; a raw token field or an unknown field
  # still fails this exact structural allowlist and remains excluded below.
  if ([string]::Equals($normalizedName, "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS", [System.StringComparison]::OrdinalIgnoreCase)) {
    return -not (Test-SafeOperatorQueryAuthBindingsSnapshot -Value $normalizedValue)
  }
  if (
    $normalizedName -match '(?i)(^|_)(PASSWORD|PASSWD|SECRET|TOKEN|AUTH)(_|$)' -or
    $normalizedName -match '(?i)_KEYS?$'
  ) {
    return $true
  }
  return $false
}

function Write-RuntimeEnvFile {
  param(
    [string]$Path,
    [hashtable]$SeedValues
  )

  $snapshot = @{}
  foreach ($entry in [Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
    $name = [string]$entry.Key
    $value = [string]$entry.Value
    if (
      $name -match '^(HOST_|EDGE_|Natsume_|OPENAI_|GEMINI_|DEEPSEEK_|MINIMAX_|GOOGLE_)' -and
      -not (Test-SensitiveRuntimeEnvEntry -Name $name -Value $value)
    ) {
      $snapshot[$name] = $value
    }
  }
  foreach ($key in $SeedValues.Keys) {
    $value = [Environment]::GetEnvironmentVariable([string]$key, 'Process')
    if (
      $null -ne $value -and
      -not (Test-SensitiveRuntimeEnvEntry -Name ([string]$key) -Value ([string]$value)
    )) {
      $snapshot[[string]$key] = [string]$value
    }
  }

  $lines = @()
  foreach ($key in ($snapshot.Keys | Sort-Object)) {
    $lines += ("{0}={1}" -f $key, ([string]$snapshot[$key]))
  }
  Set-Content -Path $Path -Value $lines -Encoding UTF8
  return $snapshot
}

function Get-ListeningPid {
  param([int]$ProbePort)
  $conn = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Is-HostProcess {
  param([int]$ProcessId)
  $procInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $procInfo) {
    return $false
  }
  $cmd = [string]$procInfo.CommandLine
  if (-not $cmd) {
    return $false
  }
  if ($cmd -like "*-m host_brain.obs_main*") {
    return $false
  }
  if ($cmd -like "*-m host_brain --host*") {
    return $true
  }
  if ($cmd -like "*host_brain.app.asgi*") {
    return $true
  }
  return $false
}

function Get-HostProcessIds {
  $result = @()
  $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue
  foreach ($procInfo in @($processes)) {
    $procId = [int]$procInfo.ProcessId
    if ($procId -le 0) {
      continue
    }
    if (Is-HostProcess -ProcessId $procId) {
      $result += $procId
    }
  }
  return @($result | Sort-Object -Unique)
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  if ($ProcessId -le 0) {
    return $false
  }
  $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($null -eq $proc) {
    return $true
  }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
  $left = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  if ($left) {
    cmd /c "taskkill /PID $ProcessId /T /F" | Out-Null
    Start-Sleep -Milliseconds 500
    $left = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
  }
  return ($null -eq $left)
}

function Start-HostCommand {
  param(
    [string]$CommandLine,
    [string]$WorkingDirectory,
    [bool]$ShowWindow,
    [bool]$Breakaway
  )
  $fullCommandLine = "$env:ComSpec /d /c $CommandLine"
  if (-not $Breakaway) {
    $args = @{
      FilePath = $env:ComSpec
      ArgumentList = @('/d', '/c', $CommandLine)
      WorkingDirectory = $WorkingDirectory
      PassThru = $true
    }
    if (-not $ShowWindow) {
      $args["WindowStyle"] = "Hidden"
    }
    $started = Start-Process @args
    return [pscustomobject]@{
      Id = [int]$started.Id
      breakaway = $false
      command_line = $fullCommandLine
    }
  }

  $startup = ([wmiclass]"Win32_ProcessStartup").CreateInstance()
  $startup.ShowWindow = if ($ShowWindow) { 1 } else { 0 }
  $createResult = ([wmiclass]"Win32_Process").Create($fullCommandLine, $WorkingDirectory, $startup)
  if ([int]$createResult.ReturnValue -ne 0) {
    throw "failed to launch host process via Win32_Process.Create: return=$($createResult.ReturnValue)"
  }
  return [pscustomobject]@{
    Id = [int]$createResult.ProcessId
    breakaway = $true
    command_line = $fullCommandLine
  }
}

function Wait-Http {
  param(
    [string]$Url,
    [int]$TimeoutSeconds
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
        return $true
      }
    } catch {
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Test-HttpPayloadIdentity {
  param(
    [string]$Url,
    [ValidateSet("health", "readiness")]
    [string]$Kind,
    [string]$ExpectedCatalogStage = "",
    [int]$TimeoutSec = 3
  )
  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
      return $false
    }
    $payload = $response.Content | ConvertFrom-Json
    if ($null -eq $payload) {
      return $false
    }
    $expectedStage = ([string]$ExpectedCatalogStage).Trim().ToUpperInvariant()
    if ($expectedStage) {
      $stage = [string]$response.Headers["x-natsume-catalog-stage"]
      $candidateOwner = [string]$response.Headers["x-natsume-catalog-candidate-owner"]
      $operationDigest = [string]$response.Headers["x-natsume-catalog-operation-digest"]
      $snapshotDigest = [string]$response.Headers["x-natsume-catalog-snapshot-digest"]
      if (
        $stage.Trim().ToUpperInvariant() -ne $expectedStage -or
        $candidateOwner -ne "host.main" -or
        $operationDigest -notmatch '^sha256:[0-9a-f]{64}$' -or
        $snapshotDigest -notmatch '^sha256:[0-9a-f]{64}$'
      ) {
        return $false
      }
    }
    $fields = @($payload.PSObject.Properties.Name)
    if ($Kind -eq "health") {
      return (
        $fields -contains "status" -and
        $fields -contains "version" -and
        $fields -contains "timestamp" -and
        $fields -contains "details"
      )
    }
    return (
      $fields -contains "ready" -and
      $fields -contains "status" -and
      $fields -contains "timestamp" -and
      $fields -contains "summary"
    )
  } catch {
    return $false
  }
}

function Wait-HostCatalogStage {
  param(
    [string]$Url,
    [string]$ExpectedCatalogStage,
    [int]$TimeoutSeconds,
    [int]$ExpectedProcessId = 0
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    if (
      $ExpectedProcessId -gt 0 -and
      $null -eq (Get-Process -Id $ExpectedProcessId -ErrorAction SilentlyContinue)
    ) {
      return $false
    }
    if (
      Test-HttpPayloadIdentity `
        -Url $Url `
        -Kind "health" `
        -ExpectedCatalogStage $ExpectedCatalogStage `
        -TimeoutSec 3
    ) {
      return $true
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Test-HostStartupStability {
  param(
    [int]$ExpectedPid,
    [int]$ProbePort,
    [string]$HealthUrl,
    [string]$ReadyUrl,
    [string]$ExpectedCatalogStage,
    [ValidateRange(2, 10)]
    [int]$RequiredConsecutiveChecks = 5
  )
  $consecutive = 0
  $maxChecks = $RequiredConsecutiveChecks + 5
  for ($attempt = 0; $attempt -lt $maxChecks; $attempt++) {
    $processAlive = (
      $null -ne (Get-Process -Id $ExpectedPid -ErrorAction SilentlyContinue) -and
      (Is-HostProcess -ProcessId $ExpectedPid)
    )
    $listeningPid = Get-ListeningPid -ProbePort $ProbePort
    $listenerMatches = (
      $null -ne $listeningPid -and
      [int]$listeningPid -eq $ExpectedPid
    )
    $healthIdentity = if ($processAlive -and $listenerMatches) {
      Test-HttpPayloadIdentity `
        -Url $HealthUrl `
        -Kind "health" `
        -ExpectedCatalogStage $ExpectedCatalogStage `
        -TimeoutSec 3
    } else {
      $false
    }
    $readyIdentity = if ($healthIdentity) {
      Test-HttpPayloadIdentity `
        -Url $ReadyUrl `
        -Kind "readiness" `
        -ExpectedCatalogStage $ExpectedCatalogStage `
        -TimeoutSec 3
    } else {
      $false
    }
    if ($processAlive -and $listenerMatches -and $healthIdentity -and $readyIdentity) {
      $consecutive += 1
      if ($consecutive -ge $RequiredConsecutiveChecks) {
        return $true
      }
    } else {
      $consecutive = 0
    }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Get-SanitizedLogTail {
  param(
    [string]$Path,
    [int]$Lines = 80
  )
  if (-not (Test-Path -LiteralPath $Path)) {
    return ""
  }
  $tail = (Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction SilentlyContinue) -join "`n"
  return (
    $tail -replace
      '(?im)(password|passwd|secret|token|authorization|api[_-]?key)(\s*[:=]\s*)([^\s,;]+)',
      '$1$2<redacted>'
  )
}

function Test-TcpPortQuick {
  param(
    [string]$HostName,
    [int]$Port,
    [int]$TimeoutMs = 1000
  )
  if ([string]::IsNullOrWhiteSpace($HostName) -or $Port -le 0) {
    return $false
  }
  $client = New-Object System.Net.Sockets.TcpClient
  try {
    $async = $client.BeginConnect($HostName, $Port, $null, $null)
    $ok = $async.AsyncWaitHandle.WaitOne([Math]::Max(100, $TimeoutMs), $false)
    if (-not $ok) {
      return $false
    }
    $client.EndConnect($async)
    return $client.Connected
  } catch {
    return $false
  } finally {
    try { $client.Close() } catch {}
  }
}

function Test-HttpQuick {
  param(
    [string]$Url,
    [int]$TimeoutSec = 3
  )
  if ([string]::IsNullOrWhiteSpace($Url)) {
    return $false
  }
  try {
    $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300)
  } catch {
    return $false
  }
}

function Resolve-UriHostPort {
  param(
    [string]$UriText,
    [string]$DefaultHost,
    [int]$DefaultPort
  )
  $hostValue = $DefaultHost
  $portValue = $DefaultPort
  $raw = [string]$UriText
  if (-not [string]::IsNullOrWhiteSpace($raw)) {
    try {
      $uri = [System.Uri]$raw
      if (-not [string]::IsNullOrWhiteSpace($uri.Host)) {
        $hostValue = $uri.Host
      }
      if ($uri.Port -gt 0) {
        $portValue = [int]$uri.Port
      }
    } catch {
      if ($raw -match '^(?:[^:]+://)?([^/:]+):(\d+)') {
        $hostValue = [string]$Matches[1]
        $portValue = [int]$Matches[2]
      }
    }
  }
  if ($hostValue -eq "0.0.0.0" -or $hostValue -eq "::") {
    $hostValue = "127.0.0.1"
  }
  return [ordered]@{
    host = $hostValue
    port = $portValue
  }
}

function Get-EnvSetting {
  param(
    [hashtable]$Values,
    [string]$Name,
    [string]$DefaultValue
  )
  $existing = [Environment]::GetEnvironmentVariable($Name, "Process")
  if (-not [string]::IsNullOrWhiteSpace($existing)) {
    return [string]$existing
  }
  if ($Values -and $Values.ContainsKey($Name)) {
    return [string]$Values[$Name]
  }
  return $DefaultValue
}

function Set-ObjectProperty {
  param(
    [AllowNull()][object]$Target,
    [string]$Name,
    [AllowNull()][object]$Value
  )
  if ($null -eq $Target -or [string]::IsNullOrWhiteSpace($Name)) {
    return
  }
  if ($Target -is [System.Collections.IDictionary]) {
    $Target[$Name] = $Value
    return
  }
  $Target | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
}

function Test-ExternalServicesReadiness {
  param(
    [hashtable]$EnvValues,
    [string]$Backend,
    [bool]$StartupSkipped
  )
  $chromaEndpoint = Get-EnvSetting -Values $EnvValues -Name "CHROMA_ENDPOINT" -DefaultValue "http://127.0.0.1:18000"
  $neo4jUri = Get-EnvSetting -Values $EnvValues -Name "BOND_NEO4J_URI" -DefaultValue "bolt://127.0.0.1:17687"
  $redisUrl = Get-EnvSetting -Values $EnvValues -Name "HOST_RATE_LIMIT_REDIS_URL" -DefaultValue "redis://127.0.0.1:6379/0"
  $chromaParsed = Resolve-UriHostPort -UriText $chromaEndpoint -DefaultHost "127.0.0.1" -DefaultPort 18000
  $neo4jParsed = Resolve-UriHostPort -UriText $neo4jUri -DefaultHost "127.0.0.1" -DefaultPort 17687
  $redisParsed = Resolve-UriHostPort -UriText $redisUrl -DefaultHost "127.0.0.1" -DefaultPort 6379
  $chromaHeartbeat = ("http://{0}:{1}/api/v2/heartbeat" -f $chromaParsed.host, $chromaParsed.port)
  $services = @(
    [ordered]@{
      service = "chroma"
      endpoint = $chromaEndpoint
      probe = $chromaHeartbeat
      ok = [bool](Test-HttpQuick -Url $chromaHeartbeat -TimeoutSec 3)
    },
    [ordered]@{
      service = "neo4j"
      uri = $neo4jUri
      host = $neo4jParsed.host
      port = [int]$neo4jParsed.port
      ok = [bool](Test-TcpPortQuick -HostName $neo4jParsed.host -Port ([int]$neo4jParsed.port) -TimeoutMs 1000)
    },
    [ordered]@{
      service = "redis"
      uri = $redisUrl
      host = $redisParsed.host
      port = [int]$redisParsed.port
      ok = [bool](Test-TcpPortQuick -HostName $redisParsed.host -Port ([int]$redisParsed.port) -TimeoutMs 1000)
    }
  )
  $missing = @()
  foreach ($svc in $services) {
    if (-not [bool]$svc.ok) {
      $missing += [string]$svc.service
    }
  }
  return [ordered]@{
    status = if ($missing.Count -eq 0) { "ready" } else { "unready" }
    backend = $Backend
    startup_skipped = $StartupSkipped
    degraded_for_recall_tests = [bool]($StartupSkipped -and $missing.Count -gt 0)
    missing = $missing
    services = $services
    warning = if ($StartupSkipped) { "external service startup skipped; recall/vector tests require external_services_ready.status=ready" } else { "" }
  }
}

function Parse-BoolValue {
  param(
    [AllowNull()][object]$Value,
    [bool]$Default = $false
  )
  if ($null -eq $Value) {
    return $Default
  }
  $raw = ([string]$Value).Trim().ToLowerInvariant()
  if (-not $raw) {
    return $Default
  }
  return $raw -in @("1", "true", "yes", "on")
}

function Resolve-Host14BBaseUrl {
  param([hashtable]$EnvValues)
  $raw = ""
  if ($EnvValues.ContainsKey("HOST_CHAT_LOCAL_LLM_BASE_URL")) {
    $raw = [string]$EnvValues["HOST_CHAT_LOCAL_LLM_BASE_URL"]
  } elseif ($env:HOST_CHAT_LOCAL_LLM_BASE_URL) {
    $raw = [string]$env:HOST_CHAT_LOCAL_LLM_BASE_URL
  } elseif ($EnvValues.ContainsKey("HOST_CHAT_HOST_14B_BASE_URL")) {
    $raw = [string]$EnvValues["HOST_CHAT_HOST_14B_BASE_URL"]
  } elseif ($env:HOST_CHAT_HOST_14B_BASE_URL) {
    $raw = [string]$env:HOST_CHAT_HOST_14B_BASE_URL
  } elseif ($EnvValues.ContainsKey("HOST_OPENAI_BASE_URL")) {
    $raw = [string]$EnvValues["HOST_OPENAI_BASE_URL"]
  } elseif ($env:HOST_OPENAI_BASE_URL) {
    $raw = [string]$env:HOST_OPENAI_BASE_URL
  } else {
    $raw = "http://127.0.0.1:11434/v1"
  }
  return $raw.Trim().TrimEnd('/')
}

function Split-ApiAndRootBaseUrl {
  param([string]$BaseUrl)
  $normalized = ([string]$BaseUrl).Trim().TrimEnd('/')
  if (-not $normalized) {
    return @{ api = $null; root = $null }
  }
  $root = if ($normalized.ToLowerInvariant().EndsWith('/v1')) {
    $normalized.Substring(0, $normalized.Length - 3).TrimEnd('/')
  } else {
    $normalized
  }
  return @{ api = $normalized; root = $root }
}

function Use-ManagedLocalOllama {
  param([hashtable]$EnvValues)
  $host14bEnabled = Parse-BoolValue -Value ($(if ($EnvValues.ContainsKey("HOST_CHAT_LOCAL_LLM_ENABLED")) { $EnvValues["HOST_CHAT_LOCAL_LLM_ENABLED"] } elseif ($env:HOST_CHAT_LOCAL_LLM_ENABLED) { $env:HOST_CHAT_LOCAL_LLM_ENABLED } elseif ($EnvValues.ContainsKey("HOST_CHAT_HOST_14B_ENABLED")) { $EnvValues["HOST_CHAT_HOST_14B_ENABLED"] } else { $env:HOST_CHAT_HOST_14B_ENABLED })) -Default $true
  if (-not $host14bEnabled) {
    return $false
  }
  $forceCloud = Parse-BoolValue -Value ($(if ($EnvValues.ContainsKey("HOST_CHAT_FORCE_CLOUD")) { $EnvValues["HOST_CHAT_FORCE_CLOUD"] } else { $env:HOST_CHAT_FORCE_CLOUD })) -Default $false
  if ($forceCloud) {
    return $false
  }
  $provider = ($(if ($EnvValues.ContainsKey("HOST_CHAT_LOCAL_LLM_PROVIDER")) { $EnvValues["HOST_CHAT_LOCAL_LLM_PROVIDER"] } elseif ($env:HOST_CHAT_LOCAL_LLM_PROVIDER) { $env:HOST_CHAT_LOCAL_LLM_PROVIDER } elseif ($EnvValues.ContainsKey("HOST_CHAT_HOST_14B_PROVIDER")) { $EnvValues["HOST_CHAT_HOST_14B_PROVIDER"] } else { $env:HOST_CHAT_HOST_14B_PROVIDER }))
  $provider = ([string]$provider).Trim().ToLowerInvariant()
  if (-not $provider) {
    $provider = 'openai'
  }
  if ($provider -ne 'openai') {
    return $false
  }
  $baseUrl = Resolve-Host14BBaseUrl -EnvValues $EnvValues
  if (-not $baseUrl) {
    return $false
  }
  try {
    $uri = [Uri]$baseUrl
  } catch {
    return $false
  }
  $hostName = $uri.Host.ToLowerInvariant()
  return ($hostName -in @('127.0.0.1', 'localhost') -and $uri.Port -eq 11434)
}

function Get-Host14BModelId {
  param([hashtable]$EnvValues)
  $value = $(if ($EnvValues.ContainsKey("HOST_CHAT_LOCAL_LLM_MODEL_ID")) { $EnvValues["HOST_CHAT_LOCAL_LLM_MODEL_ID"] } elseif ($env:HOST_CHAT_LOCAL_LLM_MODEL_ID) { $env:HOST_CHAT_LOCAL_LLM_MODEL_ID } elseif ($EnvValues.ContainsKey("HOST_CHAT_HOST_14B_MODEL_ID")) { $EnvValues["HOST_CHAT_HOST_14B_MODEL_ID"] } else { $env:HOST_CHAT_HOST_14B_MODEL_ID })
  $resolved = ([string]$value).Trim()
  if (-not $resolved) {
    $resolved = 'qwen3:14b'
  }
  return $resolved
}

function Test-OllamaReady {
  param([string]$RootBaseUrl)
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri ($RootBaseUrl.TrimEnd('/') + '/api/tags') -TimeoutSec 3
    return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300)
  } catch {
    return $false
  }
}

function Wait-OllamaReady {
  param(
    [string]$RootBaseUrl,
    [int]$TimeoutSec = 45
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if (Test-OllamaReady -RootBaseUrl $RootBaseUrl) {
      return $true
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Test-OllamaModelPresent {
  param(
    [string]$RootBaseUrl,
    [Parameter(Mandatory = $true)]
    [string]$ModelName
  )
  try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri ($RootBaseUrl.TrimEnd('/') + '/api/tags') -TimeoutSec 5
    $body = $resp.Content | ConvertFrom-Json
    foreach ($row in @($body.models)) {
      $candidate = [string]($row.name)
      if ($candidate -eq $ModelName) {
        return $true
      }
      if ($candidate -and $candidate.Split(':')[0] -eq $ModelName.Split(':')[0]) {
        return $true
      }
    }
  } catch {
  }
  return $false
}

function Ensure-LocalOllamaRuntime {
  param(
    [string]$RepoRoot,
    [hashtable]$EnvValues
  )

  return [ordered]@{
    status = 'disabled'
    provider = 'ollama'
    reason = 'local_ollama_autostart_removed'
    service_started = $false
    model_warmed = $false
  }
}

function Is-MateEngineBridgeProcess {
  param([int]$ProcessId)
  $procInfo = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if ($null -eq $procInfo) {
    return $false
  }
  $cmd = [string]$procInfo.CommandLine
  return [bool]($cmd -and $cmd -like "*mateengine_bridge_server.py*")
}

function Get-MateEngineBridgeProcessIds {
  $result = @()
  $processes = Get-CimInstance -ClassName Win32_Process -ErrorAction SilentlyContinue
  foreach ($procInfo in @($processes)) {
    $procId = [int]$procInfo.ProcessId
    if ($procId -le 0) {
      continue
    }
    $cmd = [string]$procInfo.CommandLine
    if ($cmd -and $cmd -like "*mateengine_bridge_server.py*") {
      $result += $procId
    }
  }
  return @($result | Sort-Object -Unique)
}

function Start-MateEngineBridgeRuntime {
  param(
    [string]$RepoRoot,
    [hashtable]$EnvValues,
    [string]$PythonExe,
    [string]$LogDir,
    [bool]$ForceRestartBridge,
    [bool]$ShowWindow,
    [bool]$Breakaway
  )

  $enabled = Parse-BoolValue -Value $(if ($EnvValues.ContainsKey("HOST_MATEENGINE_BRIDGE_ENABLED")) { $EnvValues["HOST_MATEENGINE_BRIDGE_ENABLED"] } else { $env:HOST_MATEENGINE_BRIDGE_ENABLED }) -Default $false
  $hostValue = Get-EnvSetting -Values $EnvValues -Name "HOST_MATEENGINE_BRIDGE_HTTP_HOST" -DefaultValue "127.0.0.1"
  $portValue = [int](Get-EnvSetting -Values $EnvValues -Name "HOST_MATEENGINE_BRIDGE_HTTP_PORT" -DefaultValue "18770")
  $probeHostValue = if ($hostValue -eq "0.0.0.0" -or $hostValue -eq "::") { "127.0.0.1" } else { $hostValue }
  $healthUrlValue = "http://$probeHostValue`:$portValue/health"
  $scriptPath = Join-Path $RepoRoot "tools\mateengine_bridge_server.py"
  $probeScriptPath = Join-Path $RepoRoot "tools\mateengine_bridge_health_probe.py"
  $bridgeStdoutLog = Join-Path $LogDir "mateengine_bridge.stdout.log"
  $bridgeStderrLog = Join-Path $LogDir "mateengine_bridge.stderr.log"

  if (-not $enabled) {
    return [ordered]@{
      side = "mateengine_bridge"
      status = "disabled_by_config"
      host = $hostValue
      port = $portValue
      health_url = $healthUrlValue
    }
  }
  $exposureMode = (Get-EnvSetting -Values $EnvValues -Name "NATSUME_BRIDGE_EXPOSURE_MODE" -DefaultValue "DISABLED").Trim().ToUpperInvariant()
  $keyId = Get-EnvSetting -Values $EnvValues -Name "NATSUME_MATEENGINE_BRIDGE_HMAC_KEY_ID" -DefaultValue ""
  $hmacSecret = Get-EnvSetting -Values $EnvValues -Name "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET" -DefaultValue ""
  $allowedPrincipals = Get-EnvSetting -Values $EnvValues -Name "NATSUME_MATEENGINE_BRIDGE_ALLOWED_PRINCIPALS" -DefaultValue ""
  $allowedScopes = Get-EnvSetting -Values $EnvValues -Name "NATSUME_MATEENGINE_BRIDGE_ALLOWED_SCOPES" -DefaultValue ""
  $clientPrincipal = Get-EnvSetting -Values $EnvValues -Name "NATSUME_MATEENGINE_BRIDGE_CLIENT_PRINCIPAL" -DefaultValue "host.mateengine.bridge"
  $allowedPrincipalItems = @(
    (($allowedPrincipals -replace '\s+', ',').Split(',')) |
      ForEach-Object { $_.Trim() } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )
  $allowedScopeItems = @(
    (($allowedScopes -replace '\s+', ',').Split(',')) |
      ForEach-Object { $_.Trim() } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )
  $embodimentOwner = (Get-EnvSetting -Values $EnvValues -Name "NATSUME_EMBODIMENT_BRIDGE_OWNER" -DefaultValue "").Trim().ToLowerInvariant()
  $configurationValid = `
    ($exposureMode -eq "LOOPBACK" -or $exposureMode -eq "REMOTE_APPROVED") -and `
    (-not [string]::IsNullOrWhiteSpace($keyId)) -and `
    ([System.Text.Encoding]::UTF8.GetByteCount($hmacSecret) -ge 32) -and `
    (-not [string]::IsNullOrWhiteSpace($clientPrincipal)) -and `
    ($allowedPrincipalItems -contains $clientPrincipal) -and `
    ($allowedScopeItems -contains "bridge:mateengine:read") -and `
    ($allowedScopeItems -contains "bridge:mateengine:admin") -and `
    ($embodimentOwner -eq "mateengine")
  if (-not $configurationValid) {
    return [ordered]@{
      side = "mateengine_bridge"
      status = "blocked"
      reason = "secure_catalog_configuration_required"
      host = $hostValue
      port = $portValue
      health_url = $healthUrlValue
    }
  }
  if (-not (Test-Path -Path $scriptPath)) {
    return [ordered]@{
      side = "mateengine_bridge"
      status = "degraded"
      reason = "script_not_found"
      script = $scriptPath
      health_url = $healthUrlValue
    }
  }

  $existingBridgeProcesses = @(Get-MateEngineBridgeProcessIds)
  if ($existingBridgeProcesses.Count -gt 0) {
    return [ordered]@{
      side = "mateengine_bridge"
      status = "blocked"
      reason = "existing_bridge_requires_operator_handoff"
      pids = $existingBridgeProcesses
      host = $hostValue
      port = $portValue
      health_url = $healthUrlValue
    }
  }
  if (-not (Test-Path -Path $probeScriptPath)) {
    return [ordered]@{
      side = "mateengine_bridge"
      status = "degraded"
      reason = "authenticated_health_probe_not_found"
      script = $probeScriptPath
      health_url = $healthUrlValue
    }
  }

  $listeningBridgePid = Get-ListeningPid -ProbePort $portValue
  if ($null -ne $listeningBridgePid) {
    return [ordered]@{
      side = "mateengine_bridge"
      status = "blocked"
      reason = "incumbent_listener_present"
      pid = [int]$listeningBridgePid
      host = $hostValue
      port = $portValue
      health_url = $healthUrlValue
    }
  }

  $env:MATEENGINE_BRIDGE_HTTP_HOST = $hostValue
  $env:MATEENGINE_BRIDGE_HTTP_PORT = [string]$portValue
  if (-not $env:MATEENGINE_SIGNAL_HOST) {
    $env:MATEENGINE_SIGNAL_HOST = Get-EnvSetting -Values $EnvValues -Name "HOST_MATEENGINE_SIGNAL_HOST" -DefaultValue "127.0.0.1"
  }
  if (-not $env:MATEENGINE_SIGNAL_PORT) {
    $env:MATEENGINE_SIGNAL_PORT = Get-EnvSetting -Values $EnvValues -Name "HOST_MATEENGINE_SIGNAL_PORT" -DefaultValue "32145"
  }

  $bridgeCommandLine = @(
    (ConvertTo-CmdArgument -Value $PythonExe),
    (ConvertTo-CmdArgument -Value $scriptPath),
    "1>>",
    (ConvertTo-CmdArgument -Value $bridgeStdoutLog),
    "2>>",
    (ConvertTo-CmdArgument -Value $bridgeStderrLog)
  ) -join " "

  $proc = Start-HostCommand `
    -CommandLine $bridgeCommandLine `
    -WorkingDirectory $RepoRoot `
    -ShowWindow:$ShowWindow `
    -Breakaway:$Breakaway

  & $PythonExe $probeScriptPath --url $healthUrlValue --timeout-seconds 10
  $healthOk = ($LASTEXITCODE -eq 0)
  if (-not $healthOk) {
    Stop-Process -Id ([int]$proc.Id) -Force -ErrorAction SilentlyContinue
  }
  return [ordered]@{
    side = "mateengine_bridge"
    status = if ($healthOk) { "started" } else { "blocked" }
    reason = if ($healthOk) { $null } else { "authenticated_health_check_failed" }
    pid = [int]$proc.Id
    host = $hostValue
    port = $portValue
    health_url = $healthUrlValue
    stdout_log = $bridgeStdoutLog
    stderr_log = $bridgeStderrLog
    detail = if ($healthOk) { "local MateEngine bridge is ready" } else { "local MateEngine bridge did not pass health check" }
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runtimeRootWasExplicit = -not [string]::IsNullOrWhiteSpace($RuntimeRoot)
if (-not $EnvFile) {
  $defaultEnv = Join-Path $repoRoot ".env"
  $fallbackEnv = Join-Path $repoRoot ".env.example"
  if (Test-Path -Path $defaultEnv) {
    $EnvFile = $defaultEnv
  } else {
    $EnvFile = $fallbackEnv
  }
}

if (-not $RuntimeRoot) {
  $RuntimeRoot = Join-Path $repoRoot "runtime\host"
} elseif (-not [System.IO.Path]::IsPathRooted($RuntimeRoot)) {
  $RuntimeRoot = Join-Path $repoRoot $RuntimeRoot
}
Ensure-Directory -Path $RuntimeRoot
$RuntimeRoot = (Resolve-Path -Path $RuntimeRoot).Path

if (-not $LogDir) {
  $LogDir = Join-Path $RuntimeRoot "logs"
}
Ensure-Directory -Path $LogDir

$pidFile = Join-Path $RuntimeRoot "host.pid"
$stdoutLog = Join-Path $LogDir "host.stdout.log"
$stderrLog = Join-Path $LogDir "host.stderr.log"
$runtimeEnvFile = Join-Path $RuntimeRoot "host.runtime.env"

$envValues = Read-EnvFile -Path $EnvFile
Set-ProcessEnv -Values $envValues

$operatorQueryBindings = [string][Environment]::GetEnvironmentVariable(
  "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS",
  "Process"
)
if (
  -not [string]::IsNullOrWhiteSpace($operatorQueryBindings) -and
  -not (Test-SafeOperatorQueryAuthBindingsSnapshot -Value $operatorQueryBindings)
) {
  throw "NATSUME_OPERATOR_QUERY_AUTH_BINDINGS must be a digest-only JSON array with explicit scopes"
}

# An explicit runtime root denotes an isolated Host instance. Its canonical
# ledger must be explicit or derived inside that same scope; inheriting a
# checked-in/.env database would silently mix acceptance receipts and user
# state. Bind all persistence consumers to one path and also pass it via the
# Host CLI below, so neither a setting alias nor a detached child can drift.
$configuredCanonicalDbPath = [string]$CanonicalDbPath
if ([string]::IsNullOrWhiteSpace($configuredCanonicalDbPath)) {
  if ($runtimeRootWasExplicit) {
    $configuredCanonicalDbPath = Join-Path (Split-Path -Parent $RuntimeRoot) "audit.sqlite3"
  } else {
    $configuredCanonicalDbPath = Get-EnvSetting `
      -Values $envValues `
      -Name "NATSUME_AUDIT_DB" `
      -DefaultValue "host_brain/data/audit.sqlite3"
  }
}
if ([string]::IsNullOrWhiteSpace($configuredCanonicalDbPath) -or $configuredCanonicalDbPath.Trim() -eq ":memory:") {
  throw "CanonicalDbPath must be a file-backed SQLite path"
}
if (-not [System.IO.Path]::IsPathRooted($configuredCanonicalDbPath)) {
  $configuredCanonicalDbPath = Join-Path $repoRoot $configuredCanonicalDbPath
}
$resolvedCanonicalDbPath = [System.IO.Path]::GetFullPath($configuredCanonicalDbPath)
$canonicalDbParent = Split-Path -Parent $resolvedCanonicalDbPath
if ($runtimeRootWasExplicit) {
  $runtimeScopeRoot = Split-Path -Parent $RuntimeRoot
  if (-not (Test-PathWithinDirectory -Path $canonicalDbParent -Directory $runtimeScopeRoot)) {
    throw "HOST_CANONICAL_DB_OUTSIDE_RUNTIME_SCOPE: CanonicalDbPath must remain inside the explicit runtime scope"
  }
}
Ensure-Directory -Path $canonicalDbParent
foreach ($persistenceName in @(
  "NATSUME_AUDIT_DB",
  "HOST_AUDIT_SQLITE_PATH",
  "HOST_AUDIT_DB_PATH",
  "HOST_TASK_RUNTIME_DB_PATH",
  "HOST_AGENT_REVIEW_DB_PATH",
  "HOST_RUNTIME_EVENT_DB_PATH"
)) {
  [Environment]::SetEnvironmentVariable($persistenceName, $resolvedCanonicalDbPath, "Process")
  $envValues[$persistenceName] = $resolvedCanonicalDbPath
}

# A production Host must receive the Workspace token secret through an opaque
# local-store reference. In particular, an isolated runtime cannot depend on
# the parent shell retaining a direct secret after a restart: the snapshot is
# intentionally secret-free and may persist only this sec:// reference.
if (-not [string]::IsNullOrWhiteSpace($AgentWorkspaceHmacSecretRef)) {
  $resolvedWorkspaceSecretRef = $AgentWorkspaceHmacSecretRef.Trim()
  if (-not $resolvedWorkspaceSecretRef.StartsWith("sec://workspace/", [System.StringComparison]::Ordinal)) {
    throw "AgentWorkspaceHmacSecretRef must use sec://workspace/..."
  }
  $configuredDirectWorkspaceSecret = [Environment]::GetEnvironmentVariable(
    "HOST_AGENT_WORKSPACE_HMAC_SECRET",
    "Process"
  )
  if (-not [string]::IsNullOrWhiteSpace([string]$configuredDirectWorkspaceSecret)) {
    throw "HOST_AGENT_WORKSPACE_HMAC_SECRET and AgentWorkspaceHmacSecretRef are mutually exclusive"
  }
  [Environment]::SetEnvironmentVariable(
    "HOST_AGENT_WORKSPACE_HMAC_SECRET_REF",
    $resolvedWorkspaceSecretRef,
    "Process"
  )
}

$configuredAgentWorkspaceRoot = if (-not [string]::IsNullOrWhiteSpace($AgentWorkspaceRoot)) {
  [string]$AgentWorkspaceRoot
} else {
  Get-EnvSetting `
    -Values $envValues `
    -Name "HOST_AGENT_WORKSPACE_MANAGED_ROOT" `
    -DefaultValue ""
}
$resolvedAgentWorkspaceRoot = ""
if (-not [string]::IsNullOrWhiteSpace($configuredAgentWorkspaceRoot)) {
  $candidateAgentWorkspaceRoot = Get-NormalizedDirectoryPath `
    -Path $configuredAgentWorkspaceRoot `
    -BasePath $repoRoot
  Assert-AgentWorkspaceRootSeparated `
    -WorkspaceRoot $candidateAgentWorkspaceRoot `
    -SourceRepository $repoRoot
  Ensure-Directory -Path $candidateAgentWorkspaceRoot
  $resolvedAgentWorkspaceRoot = (Resolve-Path -LiteralPath $candidateAgentWorkspaceRoot).Path
  Assert-AgentWorkspaceRootSeparated `
    -WorkspaceRoot $resolvedAgentWorkspaceRoot `
    -SourceRepository $repoRoot
  $env:HOST_AGENT_WORKSPACE_MANAGED_ROOT = $resolvedAgentWorkspaceRoot
  $envValues["HOST_AGENT_WORKSPACE_MANAGED_ROOT"] = $resolvedAgentWorkspaceRoot
}

if (-not $envValues.ContainsKey("HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK") -and -not $env:HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK) {
  $env:HOST_EDGE_RPC_ALLOW_LOCAL_FALLBACK = "false"
}
if (-not $envValues.ContainsKey("HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT") -and -not $env:HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT) {
  $env:HOST_EDGE_RPC_BLOCK_LOOPBACK_WECHAT = "true"
}

if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS") -and -not $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS) {
  $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS") -and -not $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS) {
  $env:HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_AUTO_REPLY") -and -not $env:HOST_INPUT_WECHAT_AUTO_REPLY) {
  $env:HOST_INPUT_WECHAT_AUTO_REPLY = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY") -and -not $env:HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY) {
  $env:HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT") -and -not $env:HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT) {
  $env:HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT = "收到"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_SESSION_LANE_ENABLED") -and -not $env:HOST_INPUT_WECHAT_SESSION_LANE_ENABLED) {
  $env:HOST_INPUT_WECHAT_SESSION_LANE_ENABLED = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_COLLECT_DEBOUNCE_MS") -and -not $env:HOST_INPUT_WECHAT_COLLECT_DEBOUNCE_MS) {
  $env:HOST_INPUT_WECHAT_COLLECT_DEBOUNCE_MS = "1200"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_COLLECT_CAP") -and -not $env:HOST_INPUT_WECHAT_COLLECT_CAP) {
  $env:HOST_INPUT_WECHAT_COLLECT_CAP = "3"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_FORCE_ACTIVE_CHAT_TARGET") -and -not $env:HOST_INPUT_WECHAT_FORCE_ACTIVE_CHAT_TARGET) {
  $env:HOST_INPUT_WECHAT_FORCE_ACTIVE_CHAT_TARGET = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_BLOCK_FILEHELPER") -and -not $env:HOST_INPUT_WECHAT_BLOCK_FILEHELPER) {
  $env:HOST_INPUT_WECHAT_BLOCK_FILEHELPER = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_REPLY_WAIT_RESULT") -and -not $env:HOST_INPUT_WECHAT_REPLY_WAIT_RESULT) {
  $env:HOST_INPUT_WECHAT_REPLY_WAIT_RESULT = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS") -and -not $env:HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS) {
  $env:HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS = "12000"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_FALLBACK_ON_REJECT") -and -not $env:HOST_INPUT_WECHAT_FALLBACK_ON_REJECT) {
  $env:HOST_INPUT_WECHAT_FALLBACK_ON_REJECT = "true"
}
if (-not $envValues.ContainsKey("HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT") -and -not $env:HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT) {
  $env:HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT = "System busy, please retry."
}

if (-not $BindHost) {
  if ($envValues.ContainsKey("HOST_APP_HOST")) {
    $BindHost = [string]$envValues["HOST_APP_HOST"]
  } elseif ($env:HOST_APP_HOST) {
    $BindHost = [string]$env:HOST_APP_HOST
  } else {
    $BindHost = "127.0.0.1"
  }
}

if ($Port -le 0) {
  if ($envValues.ContainsKey("HOST_APP_PORT")) {
    $Port = [int]$envValues["HOST_APP_PORT"]
  } elseif ($env:HOST_APP_PORT) {
    $Port = [int]$env:HOST_APP_PORT
  } else {
    $Port = 18080
  }
}

$probeHost = if ($BindHost -eq "0.0.0.0") { "127.0.0.1" } else { $BindHost }
$monitorProxyEnabled = $false
$obsEnabled = Parse-BoolValue -Value $(if ($env:HOST_OBS_APP_ENABLED) { $env:HOST_OBS_APP_ENABLED } elseif ($envValues.ContainsKey("HOST_OBS_APP_ENABLED")) { $envValues["HOST_OBS_APP_ENABLED"] } else { $null }) -Default $true
if ($DisableMonitorProxy.IsPresent) {
  $monitorProxyEnabled = $false
} elseif ($EnableMonitorProxy.IsPresent) {
  $monitorProxyEnabled = $true
} else {
  $monitorProxyEnabled = Parse-BoolValue -Value $(if ($envValues.ContainsKey("HOST_MONITOR_PROXY_ENABLED")) { $envValues["HOST_MONITOR_PROXY_ENABLED"] } else { $env:HOST_MONITOR_PROXY_ENABLED }) -Default $true
}

$obsBindHost = "127.0.0.1"
if ($env:HOST_OBS_APP_HOST) {
  $obsBindHost = [string]$env:HOST_OBS_APP_HOST
} elseif ($envValues.ContainsKey("HOST_OBS_APP_HOST")) {
  $obsBindHost = [string]$envValues["HOST_OBS_APP_HOST"]
}
if ($obsBindHost -eq "0.0.0.0") {
  $obsBindHost = "127.0.0.1"
}
$obsBindPort = 18082
if ($env:HOST_OBS_APP_PORT) {
  $obsBindPort = [int]$env:HOST_OBS_APP_PORT
} elseif ($envValues.ContainsKey("HOST_OBS_APP_PORT")) {
  $obsBindPort = [int]$envValues["HOST_OBS_APP_PORT"]
}

if (-not $MonitorProxyHost) {
  if ($env:HOST_MONITOR_PROXY_HOST) {
    $MonitorProxyHost = [string]$env:HOST_MONITOR_PROXY_HOST
  } elseif ($envValues.ContainsKey("HOST_MONITOR_PROXY_HOST")) {
    $MonitorProxyHost = [string]$envValues["HOST_MONITOR_PROXY_HOST"]
  } else {
    $MonitorProxyHost = "127.0.0.1"
  }
}
if ($MonitorProxyHost -eq "0.0.0.0") {
  $MonitorProxyHost = "127.0.0.1"
}
if ($MonitorProxyPort -le 0) {
  if ($env:HOST_MONITOR_PROXY_PORT) {
    $MonitorProxyPort = [int]$env:HOST_MONITOR_PROXY_PORT
  } elseif ($envValues.ContainsKey("HOST_MONITOR_PROXY_PORT")) {
    $MonitorProxyPort = [int]$envValues["HOST_MONITOR_PROXY_PORT"]
  } else {
    $MonitorProxyPort = 18180
  }
}

$env:HOST_OBS_APP_HOST = $obsBindHost
$env:HOST_OBS_APP_PORT = [string]$obsBindPort
$env:HOST_MONITOR_PROXY_HOST = $MonitorProxyHost
$env:HOST_MONITOR_PROXY_PORT = [string]$MonitorProxyPort
$env:HOST_MONITOR_PROXY_ENABLED = if ($monitorProxyEnabled) { "true" } else { "false" }
$env:HOST_MAIN_OBS_REDIRECT_ENABLED = if ($obsEnabled) { "true" } else { "false" }
$env:HOST_MAIN_MONITOR_REDIRECT_ENABLED = if ($monitorProxyEnabled) { "true" } else { "false" }
# The runtime snapshot is the restart authority. Persist the resolved explicit
# listener rather than the repository .env defaults, otherwise a forced
# isolated restart can silently reconstruct a public/default Host declaration.
$env:HOST_APP_HOST = $BindHost
$env:HOST_APP_PORT = [string]$Port

$localServices = $null
$localOllama = Ensure-LocalOllamaRuntime -RepoRoot $repoRoot -EnvValues $envValues
if ($ExternalServicesBackend -eq "none") {
  $localServices = [ordered]@{
    status = "skipped"
    backend = "none"
    detail = "external service startup skipped by request"
    degraded_for_recall_tests = $true
  }
} else {
  $startDockerScript = Join-Path $PSScriptRoot "start_docker_services.ps1"
  if (-not (Test-Path -Path $startDockerScript)) {
    throw "start_docker_services.ps1 not found: $startDockerScript"
  }
  $dockerRaw = & $startDockerScript `
    -EnvFile $EnvFile `
    -ComposeFile $DockerComposeFile `
    -ProjectName $DockerProjectName `
    -Neo4jWaitSeconds $Neo4jWaitSeconds `
    -ChromaWaitSeconds $ChromaWaitSeconds
  $dockerJson = ($dockerRaw | Out-String).Trim()
  if ($dockerJson) {
    try {
      $localServices = $dockerJson | ConvertFrom-Json
    } catch {
      $localServices = [ordered]@{
        raw = $dockerJson
      }
    }
  }
}
$externalServicesReady = Test-ExternalServicesReadiness `
  -EnvValues $envValues `
  -Backend $ExternalServicesBackend `
  -StartupSkipped ([bool]($ExternalServicesBackend -eq "none"))
if ($ExternalServicesBackend -eq "none" -and [string]$externalServicesReady.status -eq "ready") {
  Set-ObjectProperty -Target $localServices -Name "status" -Value "skipped_ready"
  Set-ObjectProperty -Target $localServices -Name "detail" -Value "external service startup skipped by request; existing services are ready"
  Set-ObjectProperty -Target $localServices -Name "degraded_for_recall_tests" -Value $false
} elseif ($ExternalServicesBackend -eq "none") {
  Set-ObjectProperty -Target $localServices -Name "status" -Value "skipped_unready"
  Set-ObjectProperty -Target $localServices -Name "detail" -Value "external service startup skipped by request; recall/vector tests must not run until missing services are ready"
  Set-ObjectProperty -Target $localServices -Name "degraded_for_recall_tests" -Value $true
}
Set-ObjectProperty -Target $localServices -Name "external_services_ready" -Value $externalServicesReady

if ($ForceRestart.IsPresent) {
  if ($monitorProxyEnabled) {
    $stopMonitorScript = Join-Path $PSScriptRoot "stop_monitor_proxy.ps1"
    if (-not (Test-Path -LiteralPath $stopMonitorScript)) {
      throw "stop_monitor_proxy.ps1 not found: $stopMonitorScript"
    }
    $null = & $stopMonitorScript `
      -EnvFile $EnvFile `
      -RuntimeRoot (Join-Path $RuntimeRoot "monitor_proxy") `
      -NginxExe $NginxExe `
      -Port $MonitorProxyPort
    $remainingMonitorPid = Get-ListeningPid -ProbePort $MonitorProxyPort
    if ($null -ne $remainingMonitorPid) {
      throw (
        "failed to stop existing monitor proxy on port $MonitorProxyPort " +
        "before forced restart; pid=$remainingMonitorPid"
      )
    }
  }
  if ($obsEnabled) {
    $stopObsScript = Join-Path $PSScriptRoot "stop_obs_side.ps1"
    if (-not (Test-Path -LiteralPath $stopObsScript)) {
      throw "stop_obs_side.ps1 not found: $stopObsScript"
    }
    $null = & $stopObsScript `
      -EnvFile $EnvFile `
      -RuntimeRoot $RuntimeRoot `
      -Port $obsBindPort
    $remainingObsPid = Get-ListeningPid -ProbePort $obsBindPort
    if ($null -ne $remainingObsPid) {
      throw (
        "failed to stop existing obs sidecar on port $obsBindPort " +
        "before forced restart; pid=$remainingObsPid"
      )
    }
  }
  $stopHostScript = Join-Path $PSScriptRoot "stop_host_side.ps1"
  if (-not (Test-Path -LiteralPath $stopHostScript)) {
    throw "stop_host_side.ps1 not found: $stopHostScript"
  }
  $restartHostRaw = & $stopHostScript `
    -EnvFile $EnvFile `
    -RuntimeRoot $RuntimeRoot `
    -Port $Port `
    -ObsPort $obsBindPort `
    -MonitorProxyPort $MonitorProxyPort
  $restartHostJson = ($restartHostRaw | Out-String).Trim()
  if ($restartHostJson) {
    try {
      $restartHostResult = $restartHostJson | ConvertFrom-Json
    } catch {
      throw "forced restart host shutdown returned invalid JSON"
    }
    if ([string]$restartHostResult.status -eq "degraded") {
      throw (
        "forced restart refused after Host catalog authority cleanup failed: " +
        [string]$restartHostResult.detail
      )
    }
  }
}

if (Test-Path -Path $pidFile) {
  $existingPidText = (Get-Content -Path $pidFile -Raw -Encoding ascii).Trim()
  if ($existingPidText) {
    $existingPid = [int]$existingPidText
    $existingProc = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    if ($null -ne $existingProc -and -not $ForceRestart.IsPresent) {
      $result = [ordered]@{
        side = "host"
        status = "already_running"
        pid = $existingPid
        pid_file = $pidFile
        host = $BindHost
        port = $Port
        external_services_backend = $ExternalServicesBackend
        health_url = "http://$probeHost`:$Port/health"
        env_file = $EnvFile
        local_services = $localServices
        external_services_ready = $externalServicesReady
        external_services_warning = [string]$externalServicesReady.warning
        local_ollama = $localOllama
        monitor_proxy_enabled = $monitorProxyEnabled
        monitor_proxy_status = "unknown"
      }
      $result | ConvertTo-Json -Depth 10
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

$listeningPid = Get-ListeningPid -ProbePort $Port
if ($null -ne $listeningPid) {
  if (-not $ForceRestart.IsPresent) {
    if (Is-HostProcess -ProcessId $listeningPid) {
      $result = [ordered]@{
        side = "host"
        status = "already_running"
        pid = $listeningPid
        pid_file = $pidFile
        host = $BindHost
        port = $Port
        external_services_backend = $ExternalServicesBackend
        health_url = "http://$probeHost`:$Port/health"
        env_file = $EnvFile
        local_services = $localServices
        external_services_ready = $externalServicesReady
        external_services_warning = [string]$externalServicesReady.warning
        local_ollama = $localOllama
        monitor_proxy_enabled = $monitorProxyEnabled
        monitor_proxy_status = "unknown"
      }
      $result | ConvertTo-Json -Depth 10
      exit 0
    }
    throw "port $Port is already in use by pid $listeningPid (non-host process)"
  }
  Stop-Process -Id $listeningPid -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
}

if ($ForceRestart.IsPresent -and -not $runtimeRootWasExplicit) {
  foreach ($candidatePid in @(Get-HostProcessIds)) {
    Stop-ProcessTree -ProcessId ([int]$candidatePid) | Out-Null
  }
}

function Test-PythonRuntime {
  param([string]$PythonPath)
  if ([string]::IsNullOrWhiteSpace($PythonPath) -or -not (Test-Path $PythonPath)) {
    return $false
  }
  try {
    $null = & $PythonPath -c "import fastapi, uvicorn, chromadb, pydantic, httpx" 2>$null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

$pythonExe = $null
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
if ((Test-Path $venvPython) -and (Test-PythonRuntime -PythonPath $venvPython)) {
  $pythonExe = $venvPython
} else {
  $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $pythonCmd) {
    throw "python executable not found in PATH"
  }
  $pythonExe = $pythonCmd.Source
}

$mateengineBridgeResult = Start-MateEngineBridgeRuntime `
  -RepoRoot $repoRoot `
  -EnvValues $envValues `
  -PythonExe $pythonExe `
  -LogDir $LogDir `
  -ForceRestartBridge:$ForceRestart.IsPresent `
  -ShowWindow:$ShowConsoleWindow.IsPresent `
  -Breakaway:$BreakawayFromConsole

$hostArgs = @(
  "-m", "host_brain",
  "--host", $BindHost,
  "--port", ([string]$Port),
  "--log-level", "INFO",
  "--db-path", $resolvedCanonicalDbPath
)
if ($StartupCheck.IsPresent) {
  $hostArgs += "--startup-check"
}
$catalogTargetStage = (
  Get-EnvSetting `
    -Values $envValues `
    -Name "HOST_SERVICE_CATALOG_TARGET_MODE" `
    -DefaultValue "ENFORCE"
).Trim().ToUpperInvariant()
if ($catalogTargetStage -notin @("AUDIT", "SHADOW", "PREFLIGHT", "CANARY", "ENFORCE")) {
  throw "HOST_SERVICE_CATALOG_TARGET_MODE is invalid"
}

$null = Write-RuntimeEnvFile -Path $runtimeEnvFile -SeedValues $envValues
$cmdLine = @(
  "set ""HOST_RUNTIME_ENV_FILE=$runtimeEnvFile""",
  "&&",
  (ConvertTo-CmdArgument -Value $pythonExe),
  (Join-CmdArguments -Arguments $hostArgs),
  "1>>",
  (ConvertTo-CmdArgument -Value $stdoutLog),
  "2>>",
  (ConvertTo-CmdArgument -Value $stderrLog)
) -join " "

$proc = Start-HostCommand `
  -CommandLine $cmdLine `
  -WorkingDirectory $repoRoot `
  -ShowWindow:$ShowConsoleWindow.IsPresent `
  -Breakaway:$BreakawayFromConsole

$healthUrl = "http://$probeHost`:$Port/health?skip_dependency_probes=1"
$healthOk = Wait-HostCatalogStage `
  -Url $healthUrl `
  -ExpectedCatalogStage $catalogTargetStage `
  -TimeoutSeconds $WaitSeconds `
  -ExpectedProcessId ([int]$proc.Id)
if (-not $healthOk) {
  Stop-ProcessTree -ProcessId ([int]$proc.Id) | Out-Null
  $stderrTail = ""
  if (Test-Path -Path $stderrLog) {
    $stderrTail = Get-SanitizedLogTail -Path $stderrLog -Lines 80
  }
  throw (
    "host service failed final catalog stage health check at $healthUrl " +
    "for stage $catalogTargetStage within $WaitSeconds seconds.`n$stderrTail"
  )
}

$hostPid = [int]$proc.Id
$listeningPid = $null
for ($i = 0; $i -lt 20; $i++) {
  $candidatePid = Get-ListeningPid -ProbePort $Port
  if ($null -ne $candidatePid -and [int]$candidatePid -gt 0) {
    $listeningPid = [int]$candidatePid
    break
  }
  Start-Sleep -Milliseconds 250
}
if ($null -ne $listeningPid) {
  $hostPid = [int]$listeningPid
}
Set-Content -Path $pidFile -Value ([string]$hostPid) -Encoding ascii

$startupWarnings = @()
$obsProcessResult = [ordered]@{
  side = "obs"
  status = if ($obsEnabled) { "disabled" } else { "disabled_by_config" }
}
if ($obsEnabled) {
  $startObsScript = Join-Path $PSScriptRoot "start_obs_side.ps1"
  if (-not (Test-Path -Path $startObsScript)) {
    $obsProcessResult = [ordered]@{
      side = "obs"
      status = "degraded"
      reason = "start_script_not_found"
      script = $startObsScript
    }
    $startupWarnings += "STARTUP_OBS_SIDECAR_DEGRADED"
  } else {
    try {
      $obsArgs = @{
        EnvFile = $EnvFile
        RuntimeRoot = $RuntimeRoot
        # The child launcher is deliberately detached from this PowerShell
        # process, so it must receive the resolved acceptance ports explicitly.
        # Reading only .env here would silently put an isolated Host and its
        # OBS sidecar on different port scopes.
        BindHost = $obsBindHost
        Port = $obsBindPort
        WaitSeconds = $WaitSeconds
      }
      if ($ForceRestart.IsPresent) {
        $obsArgs["ForceRestart"] = $true
      }
      # start_obs_side has two bounded WaitSeconds phases (listener and
      # health) plus an eight-second fallback probe.  Keep the wrapper alive
      # long enough for the child to reach its own cleanup path.
      $obsRaw = Invoke-ChildPowerShellScript `
        -ScriptPath $startObsScript `
        -NamedArguments $obsArgs `
        -TimeoutSeconds ([Math]::Max(60, (2 * $WaitSeconds) + 30))
      $obsJson = ($obsRaw | Out-String).Trim()
      if ($obsJson) {
        try {
          $obsProcessResult = $obsJson | ConvertFrom-Json
        } catch {
          $obsProcessResult = [ordered]@{
            side = "obs"
            status = "degraded"
            reason = "invalid_json_output"
            raw = $obsJson
          }
        }
      } else {
        $obsProcessResult = [ordered]@{
          side = "obs"
          status = "degraded"
          reason = "empty_output"
        }
      }
    } catch {
      $obsProcessResult = [ordered]@{
        side = "obs"
        status = "degraded"
        reason = "start_failed"
        detail = $_.Exception.Message
      }
    }
  }

  $obsProcessStatus = [string]$(if ($obsProcessResult.PSObject.Properties.Name -contains "status") { $obsProcessResult.status } else { "" })
  if ($obsProcessStatus -eq "degraded") {
    $startupWarnings += "STARTUP_OBS_SIDECAR_DEGRADED"
  }
}

# ── M8 TASK-0107: OBS / MCP 子系统健康探测 ──────────────────────────────
$obsStatus = "ok"
$mcpRegistryStatus = "ok"
if ([string]$externalServicesReady.status -ne "ready") {
  if ($ExternalServicesBackend -eq "none") {
    $startupWarnings += "STARTUP_EXTERNAL_SERVICES_SKIPPED_UNREADY"
  } else {
    $startupWarnings += "STARTUP_EXTERNAL_SERVICES_UNREADY"
  }
}

# 探测 /obs/metrics（优先命中 obs-sidecar，避免主进程再承担观测探针）
$obsProbeHost = if ($obsProcessResult.PSObject.Properties.Name -contains "host") { [string]$obsProcessResult.host } else { $obsBindHost }
$obsProbePort = if ($obsProcessResult.PSObject.Properties.Name -contains "port") { [int]$obsProcessResult.port } else { [int]$obsBindPort }
$obsMetricsUrl = "http://$obsProbeHost`:$obsProbePort/obs/metrics"
try {
  $obsResp = Invoke-WebRequest -Uri $obsMetricsUrl -UseBasicParsing -TimeoutSec 5
  if ($obsResp.StatusCode -lt 200 -or $obsResp.StatusCode -ge 300) {
    $obsStatus = "degraded"
    $startupWarnings += "STARTUP_OBS_DEGRADED"
  }
} catch {
  $obsStatus = "degraded"
  $startupWarnings += "STARTUP_OBS_DEGRADED"
}

# 探测 MCP 注册表（通过 /health/mcp 端点，若不存在则降级）
$mcpHealthUrl = "http://$probeHost`:$Port/health/mcp"
try {
  $mcpResp = Invoke-WebRequest -Uri $mcpHealthUrl -UseBasicParsing -TimeoutSec 5
  if ($mcpResp.StatusCode -lt 200 -or $mcpResp.StatusCode -ge 300) {
    $mcpRegistryStatus = "degraded"
    $startupWarnings += "STARTUP_MCP_DEGRADED"
  }
} catch {
  # /health/mcp 端点不存在时视为降级（非阻断）
  $mcpRegistryStatus = "degraded"
  $startupWarnings += "STARTUP_MCP_DEGRADED"
}
# ────────────────────────────────────────────────────────────────────────────

# ── M9 TASK-0112: World Canon Sync 健康探测（F-32 告警覆盖）────────────────
$canonSyncStatus = "ok"
$canonSyncDetail = ""

# 探测 /health/canon_sync（若端点不存在则视为 degraded，非阻断）
$canonSyncHealthUrl = "http://$probeHost`:$Port/health/canon_sync"
try {
  $canonSyncResp = Invoke-WebRequest -Uri $canonSyncHealthUrl -UseBasicParsing -TimeoutSec 5
  if ($canonSyncResp.StatusCode -lt 200 -or $canonSyncResp.StatusCode -ge 300) {
    $canonSyncStatus = "degraded"
    $canonSyncDetail = "HTTP $($canonSyncResp.StatusCode) from $canonSyncHealthUrl"
    $startupWarnings += "STARTUP_CANON_SYNC_DEGRADED"
  }
} catch {
  # /health/canon_sync 端点不存在时视为 degraded（非阻断，M9 可选探测）
  $canonSyncStatus = "degraded"
  $canonSyncDetail = "endpoint unreachable: $($_.Exception.Message)"
  $startupWarnings += "STARTUP_CANON_SYNC_DEGRADED"
}

# F-32：canon_sync DEGRADED 时写入结构化告警日志（供监控系统捕获）
if ($canonSyncStatus -eq "degraded") {
  $alertPayload = [ordered]@{
    alert_code   = "STARTUP_CANON_SYNC_DEGRADED"
    severity     = "WARNING"
    ts_utc       = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssZ")
    host         = $BindHost
    port         = $Port
    detail       = $canonSyncDetail
    action       = "canon_sync may be stale; world canon hotwords and slot injection may be outdated"
    remediation  = "check /health/canon_sync endpoint and canon_sync.py logs"
  }
  $alertJson = $alertPayload | ConvertTo-Json -Depth 5
  $alertLogPath = Join-Path $LogDir "startup_alerts.jsonl"
  Add-Content -Path $alertLogPath -Value $alertJson -Encoding UTF8
  Write-Warning "[STARTUP_CANON_SYNC_DEGRADED] $canonSyncDetail"
}
# ────────────────────────────────────────────────────────────────────────────

$monitorProxyResult = [ordered]@{
  side = "monitor_proxy"
  status = if ($monitorProxyEnabled) { "disabled" } else { "disabled_by_config" }
  host = $MonitorProxyHost
  port = $MonitorProxyPort
}

if ($monitorProxyEnabled) {
  $startMonitorScript = Join-Path $PSScriptRoot "start_monitor_proxy.ps1"
  if (-not (Test-Path -Path $startMonitorScript)) {
    $monitorProxyResult = [ordered]@{
      side = "monitor_proxy"
      status = "degraded"
      reason = "start_script_not_found"
      script = $startMonitorScript
    }
    $startupWarnings += "STARTUP_MONITOR_PROXY_DEGRADED"
  } else {
    try {
      $monitorArgs = @{
        EnvFile = $EnvFile
        RuntimeRoot = (Join-Path $RuntimeRoot "monitor_proxy")
        WaitSeconds = $WaitSeconds
        UpstreamHost = $probeHost
        UpstreamPort = $Port
        ObsUpstreamHost = [string]$(if ($obsProcessResult.PSObject.Properties.Name -contains "host") { $obsProcessResult.host } else { $probeHost })
        ObsUpstreamPort = [int]$(if ($obsProcessResult.PSObject.Properties.Name -contains "port") { $obsProcessResult.port } else { 18082 })
      }
      if ($MonitorProxyHost) {
        $monitorArgs["BindHost"] = $MonitorProxyHost
      }
      if ($MonitorProxyPort -gt 0) {
        $monitorArgs["Port"] = $MonitorProxyPort
      }
      if ($NginxExe) {
        $monitorArgs["NginxExe"] = $NginxExe
      }
      if ($ForceRestart.IsPresent) {
        $monitorArgs["ForceRestart"] = $true
      }

      # The proxy performs one WaitSeconds monitor probe and an eight-second
      # metrics fallback, so one phase plus 30 seconds covers its own bound.
      $monitorRaw = Invoke-ChildPowerShellScript `
        -ScriptPath $startMonitorScript `
        -NamedArguments $monitorArgs `
        -TimeoutSeconds ([Math]::Max(30, $WaitSeconds + 30))
      $monitorJson = ($monitorRaw | Out-String).Trim()
      if ($monitorJson) {
        try {
          $monitorProxyResult = $monitorJson | ConvertFrom-Json
        } catch {
          $monitorProxyResult = [ordered]@{
            side = "monitor_proxy"
            status = "degraded"
            reason = "invalid_json_output"
            raw = $monitorJson
          }
        }
      } else {
        $monitorProxyResult = [ordered]@{
          side = "monitor_proxy"
          status = "degraded"
          reason = "empty_output"
        }
      }
    } catch {
      $monitorProxyResult = [ordered]@{
        side = "monitor_proxy"
        status = "degraded"
        reason = "start_failed"
        detail = $_.Exception.Message
      }
    }
  }

  $proxyStatus = [string]$(if ($monitorProxyResult.PSObject.Properties.Name -contains "status") { $monitorProxyResult.status } else { "" })
  if ($proxyStatus -eq "degraded") {
    $startupWarnings += "STARTUP_MONITOR_PROXY_DEGRADED"
  }
}

$hostStable = Test-HostStartupStability `
  -ExpectedPid $hostPid `
  -ProbePort $Port `
  -HealthUrl $healthUrl `
  -ReadyUrl "http://$probeHost`:$Port/ready" `
  -ExpectedCatalogStage $catalogTargetStage `
  -RequiredConsecutiveChecks 5
$obsProcessStatus = [string]$(
  if ($obsProcessResult.PSObject.Properties.Name -contains "status") {
    $obsProcessResult.status
  } else {
    ""
  }
)
$obsStable = (
  -not $obsEnabled -or
  (
    $obsProcessStatus -in @("started", "already_running") -and
    (Test-HttpQuick -Url "http://$obsProbeHost`:$obsProbePort/health" -TimeoutSec 3)
  )
)
$proxyStatus = [string]$(
  if ($monitorProxyResult.PSObject.Properties.Name -contains "status") {
    $monitorProxyResult.status
  } else {
    ""
  }
)
$monitorStable = (
  -not $monitorProxyEnabled -or
  (
    $proxyStatus -in @("started", "already_running") -and
    (Test-HttpQuick -Url "http://$MonitorProxyHost`:$MonitorProxyPort/monitor/index.html" -TimeoutSec 3) -and
    (Test-HttpQuick -Url "http://$MonitorProxyHost`:$MonitorProxyPort/obs/metrics" -TimeoutSec 3)
  )
)
if (-not $hostStable -or -not $obsStable -or -not $monitorStable) {
  $stopMonitorScript = Join-Path $PSScriptRoot "stop_monitor_proxy.ps1"
  if ($monitorProxyEnabled -and (Test-Path -LiteralPath $stopMonitorScript)) {
    try {
      $null = & $stopMonitorScript `
        -EnvFile $EnvFile `
        -RuntimeRoot (Join-Path $RuntimeRoot "monitor_proxy") `
        -Port $MonitorProxyPort
    } catch {
    }
  }
  $stopObsScript = Join-Path $PSScriptRoot "stop_obs_side.ps1"
  if ($obsEnabled -and (Test-Path -LiteralPath $stopObsScript)) {
    try {
      $null = & $stopObsScript `
        -EnvFile $EnvFile `
        -RuntimeRoot $RuntimeRoot `
        -Port $obsBindPort
    } catch {
    }
  }
  Stop-ProcessTree -ProcessId $hostPid | Out-Null
  Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
  $stderrTail = Get-SanitizedLogTail -Path $stderrLog -Lines 80
  throw (
    "HOST_STACK_STARTUP_STABILITY_FAILED: host=$hostStable; " +
    "obs=$obsStable; monitor=$monitorStable.`n$stderrTail"
  )
}

$result = [ordered]@{
  side = "host"
  status = "started"
  pid = $hostPid
  pid_file = $pidFile
  host = $BindHost
  port = $Port
  external_services_backend = $ExternalServicesBackend
  health_url = $healthUrl
  env_file = $EnvFile
  runtime_root = $RuntimeRoot
  canonical_db_path = $resolvedCanonicalDbPath
  agent_workspace_root = $resolvedAgentWorkspaceRoot
  runtime_env_file = $runtimeEnvFile
  stdout_log = $stdoutLog
  stderr_log = $stderrLog
  local_services = $localServices
  external_services_ready = $externalServicesReady
  external_services_warning = [string]$externalServicesReady.warning
  local_ollama = $localOllama
  obs_status = $obsStatus
  mcp_registry_status = $mcpRegistryStatus
  canon_sync_status = $canonSyncStatus
  monitor_proxy_enabled = $monitorProxyEnabled
  obs_enabled = $obsEnabled
  mateengine_bridge = $mateengineBridgeResult
  obs_process = $obsProcessResult
  monitor_proxy_status = [string]$(if ($monitorProxyResult.PSObject.Properties.Name -contains "status") { $monitorProxyResult.status } else { "" })
  monitor_proxy = $monitorProxyResult
  breakaway_from_console = $BreakawayFromConsole
  launcher_pid = [int]$proc.Id
  startup_warnings = $startupWarnings
}
$result | ConvertTo-Json -Depth 10
