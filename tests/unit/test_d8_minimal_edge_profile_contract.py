"""Keep the loopback-only Edge smoke profile from regressing into a full start."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_main_service_declares_a_fail_closed_minimal_rpc_profile() -> None:
    source = (ROOT / "scripts/runtime/start_edge_main_service.ps1").read_text(
        encoding="utf-8"
    )
    assert '[ValidateSet("full", "minimal_rpc")]' in source
    assert 'ExecutionProfile=minimal_rpc requires a loopback BindHost' in source
    assert "EDGE_HMAC_SHARED_KEY or EDGE_HMAC_SHARED_KEY_REF" in source
    assert "disabled_by_minimal_rpc_profile" in source

    for key, value in (
        ("EDGE_ASR_ENABLED", "false"),
        ("EDGE_TTS_ENABLED", "false"),
        ("EDGE_GATEWAY_ENABLED", "false"),
        ("EDGE_QQ_ONEBOT_AUTOSTART", "false"),
        ("EDGE_VTS_AUTOSTART", "false"),
        ("EDGE_FACE_CAPTURE_ENABLED", "false"),
        ("EDGE_RPC_GATEWAY_ENABLED", "true"),
    ):
        assert f"$envValues['{key}'] = '{value}'" in source
    assert "$envValues['EDGE_EMBODIMENT_TRANSPORT_DB'] = $EmbodimentTransportDb" in source
    assert "$envValues['NATSUME_DATA_DIR'] = $MinimalDataDir" in source


def test_distributed_entrypoint_refuses_peripheral_side_profile_for_minimal_rpc() -> None:
    source = (ROOT / "scripts/runtime/start_distributed_services.ps1").read_text(
        encoding="utf-8"
    )
    assert '[ValidateSet("full", "minimal_rpc")]' in source
    assert "EdgeExecutionProfile=minimal_rpc requires EdgeStartupProfile=main_service" in source
    assert "'127.0.0.1'" in source
    assert "edge_execution_profile = $EdgeExecutionProfile" in source
