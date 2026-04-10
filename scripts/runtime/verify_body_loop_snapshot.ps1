[CmdletBinding()]
param(
  [string]$BaseUrl = "http://127.0.0.1:18080",
  [string]$Topic = "人体闭环联调验收",
  [int]$Samples = 3,
  [int]$IntervalSeconds = 2,
  [switch]$StrictSlots
)

$ErrorActionPreference = "Stop"

if ($Samples -lt 1) {
  throw "Samples must be >= 1"
}
if ($IntervalSeconds -lt 0) {
  throw "IntervalSeconds must be >= 0"
}

$requiredKeys = @(
  "somatic_state",
  "peripheral_state",
  "circadian_state",
  "proprioception_state",
  "initiative_decision",
  "default_mode_state",
  "mirror_neuron_state",
  "memory_layers",
  "language_dual_system",
  "homeostasis_report",
  "interoception_state",
  "motor_cortex_state",
  "cerebellum_state",
  "glymphatic_state",
  "vestibular_state"
)

$requiredSlots = @(
  "circadian_state",
  "proprioception_state",
  "default_mode_state",
  "mirror_neuron_state",
  "language_dual_system",
  "homeostasis_report",
  "interoception_state",
  "motor_cortex_state",
  "cerebellum_state",
  "glymphatic_state",
  "vestibular_state"
)

function Invoke-Snapshot {
  param(
    [string]$Url,
    [string]$CurrentTopic
  )
  $encodedTopic = [System.Uri]::EscapeDataString($CurrentTopic)
  $uri = "$Url/obs/runtime-snapshot?current_topic=$encodedTopic"
  $resp = Invoke-WebRequest -UseBasicParsing -Uri $uri -TimeoutSec 30
  if ($resp.StatusCode -lt 200 -or $resp.StatusCode -ge 300) {
    throw "HTTP status $($resp.StatusCode) from $uri"
  }
  return ($resp.Content | ConvertFrom-Json)
}

$failures = @()
$rows = @()

for ($i = 1; $i -le $Samples; $i++) {
  $snapshot = Invoke-Snapshot -Url $BaseUrl.TrimEnd("/") -CurrentTopic $Topic
  $present = @{}
  foreach ($k in $requiredKeys) {
    $present[$k] = [bool]($snapshot.PSObject.Properties.Name -contains $k)
    if (-not $present[$k]) {
      $failures += "sample#$i missing key: $k"
    }
  }

  $slotStatus = @{}
  foreach ($slot in $requiredSlots) {
    $status = [string]$snapshot.slot_status.$slot
    $slotStatus[$slot] = $status
    if (-not $status) {
      $failures += "sample#$i slot status missing: $slot"
      continue
    }
    if ($StrictSlots.IsPresent) {
      if ($status -ne "live") {
        $failures += "sample#$i slot not live: $slot=$status"
      }
    } else {
      if ($status -in @("error", "unavailable")) {
        $failures += "sample#$i slot unhealthy: $slot=$status"
      }
    }
  }

  $rows += [PSCustomObject]@{
    sample = $i
    ts_ms = [int64]$snapshot.timestamp
    sys_mode = [string]$snapshot.sys_mode
    degraded = [bool]$snapshot.degraded
    interoceptive_index = [double]($snapshot.interoception_state.interoceptive_index)
    vestibular_jitter = [double]($snapshot.vestibular_state.jitter_index)
    vestibular_state = [string]$snapshot.vestibular_state.stability_state
    route_bias = [string]$snapshot.vestibular_state.route_bias
    glymphatic_executed = [bool]$snapshot.glymphatic_state.executed
    homeostasis_status = [string]$snapshot.homeostasis_report.status
    language_mode = [string]$snapshot.language_dual_system.broca.response_mode
  }

  if ($i -lt $Samples -and $IntervalSeconds -gt 0) {
    Start-Sleep -Seconds $IntervalSeconds
  }
}

$summary = [ordered]@{
  base_url = $BaseUrl
  samples = $Samples
  strict_slots = [bool]$StrictSlots.IsPresent
  passed = ($failures.Count -eq 0)
  failure_count = $failures.Count
  failures = $failures
  latest = $rows[-1]
  rows = $rows
}

$summary | ConvertTo-Json -Depth 8

if ($failures.Count -gt 0) {
  exit 2
}
