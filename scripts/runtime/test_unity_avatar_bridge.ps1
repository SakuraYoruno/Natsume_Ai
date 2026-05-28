param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 18770
)

$ErrorActionPreference = "Stop"

$base = "http://${HostAddress}:${Port}"
$health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 5

$commands = @(
    @{ type = "expression"; emotion_tag = "HAPPY"; intensity = 0.85 },
    @{ type = "talk"; talking = $true },
    @{ type = "mouth"; value = 0.72; motion_values = @{ FaceAngleY = 5.0; MouthSmile = 0.6; HandRightPositionY = 0.7 } },
    @{ type = "body_action"; action_id = "wave"; intensity = 0.85; duration_s = 1.6 },
    @{ type = "talk"; talking = $false }
)

$results = foreach ($command in $commands) {
    Invoke-RestMethod -Method Post -Uri "$base/command" -Body ($command | ConvertTo-Json -Depth 8) -ContentType "application/json; charset=utf-8" -TimeoutSec 5
}

[pscustomobject]@{
    success = $true
    initial_health = $health
    command_results = $results
    final_health = Invoke-RestMethod -Uri "$base/health" -TimeoutSec 5
} | ConvertTo-Json -Depth 10
