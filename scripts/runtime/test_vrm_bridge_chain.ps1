param(
    [string]$ModelPath = $env:EDGE_VRM_MODEL_PATH,
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 18770,
    [double]$TimeoutS = 1.5
)

$ErrorActionPreference = "Stop"

if (-not $ModelPath) {
    $ModelPath = $env:VRM_MODEL_PATH
}

$healthUrl = "http://${HostAddress}:${Port}/health"
try {
    $health = Invoke-RestMethod -Uri $healthUrl -TimeoutSec ([Math]::Max(1, [int][Math]::Ceiling($TimeoutS)))
}
catch {
    throw "VRM bridge health check failed at ${healthUrl}. Start scripts/runtime/start_unity_avatar_runtime.ps1 first. $($_.Exception.Message)"
}

$env:Natsume_ENV = "dev"
$env:EDGE_AVATAR_BACKEND = "vrm"
$env:EDGE_VRM_BRIDGE_HOST = $HostAddress
$env:EDGE_VRM_BRIDGE_PORT = [string]$Port
$env:EDGE_VRM_BRIDGE_TIMEOUT_S = [string]$TimeoutS
$env:EDGE_ASR_ENABLED = "false"
$env:EDGE_TTS_ENABLED = "false"
$env:EDGE_RESOURCE_GATE_ENABLED = "false"
if ($ModelPath) {
    $env:EDGE_VRM_MODEL_PATH = $ModelPath
}

$python = @'
import json
import os

from edge_client.config import load_edge_config
from edge_client.runtime.bootstrap import build_edge_runtime

cfg_result = load_edge_config(env=dict(os.environ))
if not cfg_result.success or cfg_result.config is None:
    print(json.dumps({"success": False, "stage": "config", "detail": cfg_result.as_dict()}, ensure_ascii=False, indent=2))
    raise SystemExit(1)

runtime = build_edge_runtime(cfg_result.config)
try:
    driver = runtime.vts_driver
    connect_result = driver.connect() if driver is not None else None
    expression_result = driver.set_expression("HAPPY", intensity=0.85) if driver is not None else None
    talk_result = driver.talk_start() if driver is not None else None
    mouth_result = driver.set_mouth_open_level(
        0.72,
        _include_companions=True,
        _batch_companions=True,
        _motion_values={
            "FaceAngleX": 4.0,
            "FaceAngleY": -6.0,
            "MouthSmile": 0.58,
            "HandRightPositionY": 0.62,
            "HandRightAngleZ": -24.0,
            "MocopiBodyAngleZ": -5.0,
        },
    ) if driver is not None else None
    body_summary = runtime.voice_pipeline.perform_body_action(
        action_id="wave",
        emotion_tag="HAPPY",
        intensity=0.85,
        duration_s=0.8,
    )
    talk_end_result = driver.talk_end() if driver is not None else None
    health = driver.health() if driver is not None else {}
    summary = {
        "success": bool(
            connect_result and connect_result.success
            and expression_result and expression_result.success
            and talk_result and talk_result.success
            and mouth_result and mouth_result.success
            and body_summary.get("success")
            and talk_end_result and talk_end_result.success
        ),
        "driver": type(driver).__name__ if driver is not None else None,
        "connect": connect_result.as_dict() if connect_result is not None else None,
        "expression": expression_result.as_dict() if expression_result is not None else None,
        "talk_start": talk_result.as_dict() if talk_result is not None else None,
        "mouth": mouth_result.as_dict() if mouth_result is not None else None,
        "body_action": {
            key: body_summary.get(key)
            for key in (
                "success",
                "state",
                "reason",
                "frames_sent",
                "error_count",
                "model_move_frames_sent",
                "model_move_error_count",
            )
        },
        "talk_end": talk_end_result.as_dict() if talk_end_result is not None else None,
        "health": {
            "backend": health.get("backend"),
            "state": health.get("state"),
            "bridge": health.get("bridge"),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0 if summary["success"] else 1)
finally:
    runtime.close()
'@

$python | python -
