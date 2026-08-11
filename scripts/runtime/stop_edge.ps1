[CmdletBinding()]
param(
  [string]$PidFile = "",
  [int]$Port = 17800
)

$ErrorActionPreference = "Stop"

function Get-ListeningPid {
  param([int]$ProbePort)
  $conn = Get-NetTCPConnection -LocalPort $ProbePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($null -eq $conn) {
    return $null
  }
  return [int]$conn.OwningProcess
}

function Read-TrimmedFileText {
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$Encoding = "ascii"
  )

  if (-not (Test-Path -Path $Path)) {
    return ""
  }

  $raw = Get-Content -Path $Path -Raw -Encoding $Encoding -ErrorAction SilentlyContinue
  if ($null -eq $raw) {
    return ""
  }

  return "$raw".Trim()
}

function Get-DescendantProcessIds {
  param([int]$RootProcessId)

  $snapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
  $pending = [System.Collections.Generic.Queue[int]]::new()
  $seen = [System.Collections.Generic.HashSet[int]]::new()
  $result = [System.Collections.Generic.List[int]]::new()
  $pending.Enqueue($RootProcessId)
  [void]$seen.Add($RootProcessId)

  while ($pending.Count -gt 0) {
    $parentId = $pending.Dequeue()
    foreach ($candidate in @($snapshot | Where-Object { [int]$_.ParentProcessId -eq $parentId })) {
      $candidateId = [int]$candidate.ProcessId
      if ($candidateId -le 0 -or -not $seen.Add($candidateId)) {
        continue
      }
      $result.Add($candidateId)
      $pending.Enqueue($candidateId)
    }
  }

  return @($result)
}

function Stop-ProcessTree {
  param([int]$RootProcessId)

  if ($RootProcessId -le 0) {
    return @()
  }

  $descendantIds = @(Get-DescendantProcessIds -RootProcessId $RootProcessId)
  $treeIds = @($RootProcessId) + @($descendantIds)
  Stop-Process -Id $RootProcessId -Force -ErrorAction SilentlyContinue
  foreach ($descendantId in @($descendantIds | Sort-Object -Descending)) {
    Stop-Process -Id $descendantId -Force -ErrorAction SilentlyContinue
  }

  $deadline = [DateTime]::UtcNow.AddSeconds(8)
  do {
    $remainingIds = @(
      $treeIds | Where-Object {
        $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue)
      }
    )
    if ($remainingIds.Count -eq 0) {
      break
    }
    Start-Sleep -Milliseconds 200
  } while ([DateTime]::UtcNow -lt $deadline)

  if ($remainingIds.Count -gt 0) {
    $taskkillExe = Join-Path $env:SystemRoot "System32\taskkill.exe"
    if (Test-Path -LiteralPath $taskkillExe -PathType Leaf) {
      & $taskkillExe /PID $RootProcessId /T /F 2>$null | Out-Null
      foreach ($remainingId in $remainingIds) {
        & $taskkillExe /PID $remainingId /F 2>$null | Out-Null
      }
    }
  }
  return @($descendantIds)
}

function Stop-StaleRepoEdgeProcesses {
  param([string]$RepoRoot)

  if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    return @()
  }

  $repoNeedle = $RepoRoot.ToLowerInvariant()
  $workerNeedle = (Join-Path $RepoRoot "edge_client\actuators\tts_persistent_worker.py").ToLowerInvariant()
  $staleIds = [System.Collections.Generic.HashSet[int]]::new()
  foreach ($candidate in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    $candidateId = [int]$candidate.ProcessId
    if ($candidateId -le 0 -or $candidateId -eq $PID) {
      continue
    }
    $commandLine = [string]$candidate.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
      continue
    }
    $normalized = $commandLine.ToLowerInvariant()
    $isWorker = $normalized.Contains($workerNeedle)
    $isRepoEdge = $normalized.Contains($repoNeedle) -and $normalized.Contains("-m edge_client")
    if ($isWorker -or $isRepoEdge) {
      [void]$staleIds.Add($candidateId)
    }
  }

  foreach ($staleId in @($staleIds | Sort-Object)) {
    if ($null -ne (Get-Process -Id $staleId -ErrorAction SilentlyContinue)) {
      [void](Stop-ProcessTree -RootProcessId $staleId)
    }
  }
  return @($staleIds | Sort-Object)
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$qqOneBotManagerScript = Join-Path $PSScriptRoot "qq_onebot_manager.ps1"
if (-not (Test-Path -Path $qqOneBotManagerScript)) {
  throw "qq_onebot_manager.ps1 not found: $qqOneBotManagerScript"
}
. $qqOneBotManagerScript
$runtimeEdge = Join-Path $repoRoot "runtime\edge"
if (-not $PidFile) {
  $PidFile = Join-Path $runtimeEdge "edge.pid"
}

$wechatBridgePidFile = Join-Path $runtimeEdge "wechat_bridge.pid"

$stopped = $false
$edgePid = $null
$detail = ""
$descendantProcessIds = @()

if (Test-Path -Path $PidFile) {
  $pidText = Read-TrimmedFileText -Path $PidFile -Encoding ascii
  if ($pidText) {
    $parsedEdgePid = 0
    if ([int]::TryParse($pidText, [ref]$parsedEdgePid)) {
      $edgePid = $parsedEdgePid
      $proc = Get-Process -Id $edgePid -ErrorAction SilentlyContinue
      if ($null -ne $proc) {
        $descendantProcessIds = @(Stop-ProcessTree -RootProcessId $edgePid)
        $stopped = $true
        $detail = "edge process tree stopped"
      } else {
        $detail = "edge process already exited"
      }
    } else {
      $detail = "pid file is invalid: $pidText"
    }
  } else {
    $detail = "pid file is empty"
  }
  Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
} else {
  $detail = "pid file not found"
}

if (-not $stopped) {
  $listeningPid = Get-ListeningPid -ProbePort $Port
  if ($null -ne $listeningPid) {
    $descendantProcessIds = @(Stop-ProcessTree -RootProcessId $listeningPid)
    $stopped = $true
    $edgePid = $listeningPid
    $detail = "edge process tree stopped by port probe"
  }
}

# Edge may be force-stopped after spawning a persistent model worker. Windows
# does not automatically reap that child, so clean only workers and stale Edge
# launchers whose command line is rooted in this repository. Gateway and other
# user processes are intentionally outside this predicate.
$staleRepoEdgeProcessIds = @(Stop-StaleRepoEdgeProcesses -RepoRoot $repoRoot)

# 若存在微信桥接 PID 文件，一并停止桥接进程
$wechatBridgeStopped = $false
if (Test-Path -Path $wechatBridgePidFile) {
  $bridgePidText = Read-TrimmedFileText -Path $wechatBridgePidFile -Encoding ascii
  if ($bridgePidText) {
    $parsedBridgePid = 0
    if ([int]::TryParse($bridgePidText, [ref]$parsedBridgePid)) {
      $bridgePid = $parsedBridgePid
      $bridgeProc = Get-Process -Id $bridgePid -ErrorAction SilentlyContinue
      if ($null -ne $bridgeProc) {
        Stop-Process -Id $bridgePid -Force -ErrorAction SilentlyContinue
        $wechatBridgeStopped = $true
      }
    }
  }
  Remove-Item -Path $wechatBridgePidFile -Force -ErrorAction SilentlyContinue
}

$qqOneBotStopResult = Stop-QQOneBotIfOwned -RuntimeRoot $runtimeEdge

$result = [ordered]@{
  status = if ($stopped) { "stopped" } else { "not_running" }
  pid = $edgePid
  pid_file = $PidFile
  port = $Port
  detail = $detail
  descendant_process_ids = @($descendantProcessIds)
  stale_repo_edge_process_ids = @($staleRepoEdgeProcessIds)
  wechat_bridge_stopped = $wechatBridgeStopped
  qq_onebot = $qqOneBotStopResult
}
$result | ConvertTo-Json -Depth 4
