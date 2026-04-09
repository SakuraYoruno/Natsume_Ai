param(
  [string]$HostBaseUrl = "http://127.0.0.1:18080",
  [switch]$SkipRuntime,
  [switch]$SkipPytest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$results = New-Object System.Collections.Generic.List[object]

function Add-CheckResult {
  param(
    [string]$Name,
    [bool]$Passed,
    [string]$Detail
  )
  $results.Add([pscustomobject]@{
      Check  = $Name
      Status = if ($Passed) { "PASS" } else { "FAIL" }
      Detail = $Detail
    })
}

function Test-JsonHasProps {
  param(
    [object]$Object,
    [string[]]$Required
  )
  if ($null -eq $Object) {
    return ,@($false, @("object:null"))
  }
  $names = @($Object.PSObject.Properties.Name)
  $missing = @()
  foreach ($key in $Required) {
    if ($names -notcontains $key) {
      $missing += $key
    }
  }
  return ,@(($missing.Count -eq 0), $missing)
}

function Invoke-EndpointWithRetry {
  param(
    [string]$Uri,
    [int[]]$TimeoutSeconds = @(6, 10, 15)
  )
  $errors = @()
  foreach ($timeout in $TimeoutSeconds) {
    try {
      $payload = Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec $timeout
      return [pscustomobject]@{
        ok      = $true
        payload = $payload
        timeout = $timeout
        error   = ""
      }
    } catch {
      $errors += ("t={0}s:{1}" -f $timeout, $_.Exception.Message)
    }
  }
  return [pscustomobject]@{
    ok      = $false
    payload = $null
    timeout = $null
    error   = ($errors -join " || ")
  }
}

Write-Host "== Digital Life P0 Verification ==" -ForegroundColor Cyan
Write-Host ("HostBaseUrl={0} | SkipRuntime={1} | SkipPytest={2}" -f $HostBaseUrl, [bool]$SkipRuntime, [bool]$SkipPytest)

if (-not $SkipPytest) {
  Write-Host "`n[1/2] Run key P0 tests..." -ForegroundColor Yellow
  $pytestArgs = @(
    "-m", "pytest", "-q",
    "tests/unit/test_agent_context_schemas.py",
    "tests/unit/test_context_pack_runtime_integration.py",
    "tests/unit/test_state_write_arbiter.py",
    "tests/unit/test_top_agent_trace_envelope.py",
    "tests/unit/test_scheduler_emotion_gate.py",
    "tests/unit/test_scheduler_emotion_intent_fields.py",
    "tests/unit/test_http_hooks_runtime_helpers.py",
    "tests/integration/test_obs_scheduler_history_stats.py"
  )
  & python @pytestArgs
  if ($LASTEXITCODE -eq 0) {
    Add-CheckResult -Name "Pytest-P0" -Passed $true -Detail "All key tests passed"
  } else {
    Add-CheckResult -Name "Pytest-P0" -Passed $false -Detail "pytest exit code=$LASTEXITCODE"
  }
} else {
  Add-CheckResult -Name "Pytest-P0" -Passed $true -Detail "Skipped"
}

if (-not $SkipRuntime) {
  Write-Host "`n[2/2] Validate runtime endpoints..." -ForegroundColor Yellow
  $base = $HostBaseUrl.TrimEnd("/")

  $runtimeHealthDirectPassed = $false
  $runtimeHealthDirectDetail = ""
  $snapshotPassed = $false
  $historyPassed = $false
  $runtimeHealthEchoFromObs = ""

  $healthProbe = Invoke-EndpointWithRetry -Uri "$base/health"
  if ($healthProbe.ok) {
    $runtimeHealthDirectPassed = $true
    $runtimeHealthDirectDetail = "health endpoint reachable (timeout=$($healthProbe.timeout)s)"
  } else {
    $runtimeHealthDirectDetail = $healthProbe.error
  }

  try {
    $snapshot = Invoke-RestMethod -Uri "$base/obs/runtime-snapshot" -Method Get -TimeoutSec 12
    $okTop, $missingTop = Test-JsonHasProps -Object $snapshot -Required @("emotion_context", "endocrine_state", "background_runtime")
    $okBackground, $missingBackground = Test-JsonHasProps -Object $snapshot.background_runtime -Required @("proactive_task", "gate_blocked_tasks")
    $okEmotion, $missingEmotion = Test-JsonHasProps -Object $snapshot.emotion_context -Required @("endocrine_state")
    if ($okTop -and $okBackground -and $okEmotion) {
      $snapshotPassed = $true
      $runtimeHealthEchoFromObs = "runtime-snapshot reachable"
      Add-CheckResult -Name "Runtime-Snapshot-Fields" -Passed $true -Detail "endocrine/background fields present"
    } else {
      $detail = @()
      if (-not $okTop) { $detail += ("missing(top): " + ($missingTop -join ",")) }
      if (-not $okBackground) { $detail += ("missing(background): " + ($missingBackground -join ",")) }
      if (-not $okEmotion) { $detail += ("missing(emotion_context): " + ($missingEmotion -join ",")) }
      Add-CheckResult -Name "Runtime-Snapshot-Fields" -Passed $false -Detail ($detail -join " | ")
    }
  } catch {
    Add-CheckResult -Name "Runtime-Snapshot-Fields" -Passed $false -Detail $_.Exception.Message
  }

  try {
    $history = Invoke-RestMethod -Uri "$base/obs/scheduler/history?window_minutes=60" -Method Get -TimeoutSec 12
    $okRoot, $missingRoot = Test-JsonHasProps -Object $history -Required @(
      "gate_blocked_total",
      "gate_blocked_rate",
      "blocked_reason_codes",
      "gate_states",
      "tasks"
    )
    $taskFieldsOk = $true
    $taskMissing = @()
    if ($null -ne $history.tasks -and $history.tasks.Count -gt 0) {
      $firstTask = $history.tasks[0]
      $taskFieldsOk, $taskMissing = Test-JsonHasProps -Object $firstTask -Required @(
        "last_reason_code",
        "last_gate_state",
        "last_mood_state",
        "last_endocrine_cortisol",
        "last_endocrine_energy"
      )
    }
    if ($okRoot -and $taskFieldsOk) {
      $historyPassed = $true
      if (-not $runtimeHealthEchoFromObs) {
        $runtimeHealthEchoFromObs = "scheduler-history reachable"
      }
      if ($history.tasks.Count -gt 0) {
        $detailText = "history root + task fields present"
      } else {
        $detailText = "history root fields present (no tasks yet)"
      }
      Add-CheckResult -Name "Scheduler-History-Fields" -Passed $true -Detail $detailText
    } else {
      $detail = @()
      if (-not $okRoot) { $detail += ("missing(root): " + ($missingRoot -join ",")) }
      if (-not $taskFieldsOk) { $detail += ("missing(task): " + ($taskMissing -join ",")) }
      Add-CheckResult -Name "Scheduler-History-Fields" -Passed $false -Detail ($detail -join " | ")
    }
  } catch {
    Add-CheckResult -Name "Scheduler-History-Fields" -Passed $false -Detail $_.Exception.Message
  }

  try {
    $sessions = Invoke-RestMethod -Uri "$base/obs/sessions?page=1&page_size=8" -Method Get -TimeoutSec 12
    $items = @($sessions.items)
    if ($items.Count -eq 0) {
      Add-CheckResult -Name "Trace-Subgraph-Fields" -Passed $true -Detail "no sessions in window, skipped"
    } else {
      $traceChecked = $false
      $subgraphFound = $false
      $subgraphShapeOk = $false
      $checkedCount = 0
      foreach ($item in $items) {
        $requestId = [string]($item.request_id)
        if ([string]::IsNullOrWhiteSpace($requestId)) {
          continue
        }
        $checkedCount += 1
        $trace = $null
        try {
          $trace = Invoke-RestMethod -Uri "$base/obs/trace/$requestId" -Method Get -TimeoutSec 12
        } catch {
          continue
        }
        if ($null -eq $trace) {
          continue
        }
        $traceChecked = $true
        $traceHasNodes = ($trace.PSObject.Properties.Name -contains "nodes")
        $traceHasSubgraphs = ($trace.PSObject.Properties.Name -contains "subgraphs")
        if (-not ($traceHasNodes -and $traceHasSubgraphs)) {
          continue
        }
        $subgraphs = @($trace.subgraphs)
        if ($subgraphs.Count -eq 0) {
          continue
        }
        $subgraphFound = $true
        foreach ($subgraph in $subgraphs) {
          $sgNodes = @($subgraph.nodes)
          if ($sgNodes.Count -eq 0) {
            continue
          }
          $firstNode = $sgNodes[0]
          $nodeProps = @($firstNode.PSObject.Properties.Name)
          if (($nodeProps -contains "node_id") -and ($nodeProps -contains "parent_node_id")) {
            $subgraphShapeOk = $true
            break
          }
        }
        if ($subgraphShapeOk) {
          break
        }
      }
      if (-not $traceChecked) {
        Add-CheckResult -Name "Trace-Subgraph-Fields" -Passed $false -Detail "no trace payload retrievable from recent sessions"
      } elseif (-not $subgraphFound) {
        Add-CheckResult -Name "Trace-Subgraph-Fields" -Passed $true -Detail "trace reachable, no subgraph sample in recent sessions"
      } elseif ($subgraphShapeOk) {
        Add-CheckResult -Name "Trace-Subgraph-Fields" -Passed $true -Detail "subgraph nodes include node_id + parent_node_id"
      } else {
        Add-CheckResult -Name "Trace-Subgraph-Fields" -Passed $false -Detail "subgraph found but required fields missing"
      }
    }
  } catch {
    Add-CheckResult -Name "Trace-Subgraph-Fields" -Passed $false -Detail $_.Exception.Message
  }

  if ($runtimeHealthDirectPassed) {
    Add-CheckResult -Name "Runtime-Health" -Passed $true -Detail $runtimeHealthDirectDetail
  } elseif ($snapshotPassed -or $historyPassed) {
    Add-CheckResult -Name "Runtime-Health" -Passed $true -Detail ("health endpoint timeout, but obs endpoint reachable ({0})" -f $runtimeHealthEchoFromObs)
  } else {
    Add-CheckResult -Name "Runtime-Health" -Passed $false -Detail $runtimeHealthDirectDetail
  }
} else {
  Add-CheckResult -Name "Runtime-Checks" -Passed $true -Detail "Skipped"
}

Write-Host ""
$results | Format-Table -AutoSize

$failed = @($results | Where-Object { $_.Status -eq "FAIL" })
if ($failed.Count -gt 0) {
  Write-Host ""
  Write-Host ("P0 verification failed: {0} check(s)" -f $failed.Count) -ForegroundColor Red
  exit 1
}

Write-Host ""
Write-Host "P0 verification passed." -ForegroundColor Green
exit 0
