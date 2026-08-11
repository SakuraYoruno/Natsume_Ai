from __future__ import annotations

import ast
from pathlib import Path

import pytest

from natsume_contracts.edge.embodiment_v1 import (
    EMBODIMENT_COMMAND_EXPIRED,
    EMBODIMENT_COMMAND_SCHEMA_VERSION,
    EMBODIMENT_KIND_VOICE,
    build_embodiment_command,
    parse_embodiment_command,
)


ROOT = Path(__file__).resolve().parents[2]

# Historical AW06-B inventory: direct device calls, legacy device RPC calls,
# and Host implementation imports across the scoped production paths.
LEGACY_DIRECT_EFFECT_BASELINE = tuple(f"AW06B-DIRECT-{index:02d}" for index in range(1, 32))

SCOPED_PRODUCTION_PATHS = (
    "host_brain/app/chat_handler.py",
    "host_brain/app/input_routes.py",
    "host_brain/app/mateengine_routes.py",
    "host_brain/input_runtime/outbound_delivery.py",
    "host_brain/app/agent_runtime_shared.py",
    "host_brain/app/obs_routes_stats_system.py",
    "host_brain/channels/life_delivery_transport.py",
    "host_brain/channels/vts.py",
    "host_brain/tribe/motor_cortex_agent.py",
)
FORBIDDEN_IMPORTED_SYMBOLS = {
    "post_speak", "post_vts", "post_desktop_pet", "resolve_vts_expression",
    "send_mateengine_command", "send_mateengine_action", "send_mateengine_say",
    "send_mateengine_signal",
}


def test_embodiment_command_is_stable_versioned_and_authority_bound() -> None:
    command = build_embodiment_command(
        kind=EMBODIMENT_KIND_VOICE,
        action="speak",
        payload={"text": "hello"},
        authority_ref="authority://host/test",
        grant_ref="grant://test/1",
        deadline_ms=2_000,
        idempotency_key="stable-key",
        request_id="req-1",
        issued_at_ms=1_000,
    )
    duplicate = build_embodiment_command(
        kind=EMBODIMENT_KIND_VOICE,
        action="speak",
        payload={"text": "hello"},
        authority_ref="authority://host/test",
        grant_ref="grant://test/1",
        deadline_ms=2_000,
        idempotency_key="stable-key",
        request_id="req-1",
        issued_at_ms=1_000,
    )

    assert command.schema_version == EMBODIMENT_COMMAND_SCHEMA_VERSION
    assert command.command_id == duplicate.command_id
    assert command.authority_ref and command.grant_ref
    assert command.deadline_ms > command.issued_at_ms
    assert parse_embodiment_command(command.as_dict()) == command

    with pytest.raises(ValueError, match=EMBODIMENT_COMMAND_EXPIRED):
        build_embodiment_command(
            kind=EMBODIMENT_KIND_VOICE,
            action="speak",
            payload={},
            authority_ref="authority://host/test",
            grant_ref="grant://test/1",
            deadline_ms=999,
            idempotency_key="expired-key",
            request_id="req-expired",
            issued_at_ms=1_000,
        )


def test_scoped_host_production_direct_effect_inventory_is_31_to_zero() -> None:
    assert len(LEGACY_DIRECT_EFFECT_BASELINE) == 31
    violations: list[str] = []
    for relative in SCOPED_PRODUCTION_PATHS:
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8-sig"), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                forbidden = sorted(imported & FORBIDDEN_IMPORTED_SYMBOLS)
                if forbidden:
                    violations.append(f"{relative}:{node.lineno}:{','.join(forbidden)}")
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [str(node.module or "")]
                )
                if any(module == "edge_client" or module.startswith("edge_client.") for module in modules):
                    violations.append(f"{relative}:{node.lineno}:edge_client")

    assert violations == []


def test_host_device_transports_delegate_to_embodiment_and_have_no_physical_io() -> None:
    embodiment = (ROOT / "host_brain/channels/embodiment.py").read_text(encoding="utf-8")
    embodiment_commands = (
        ROOT / "host_brain/channels/embodiment_commands.py"
    ).read_text(encoding="utf-8")
    notification = (ROOT / "host_brain/skills/impl/notification_executor.py").read_text(encoding="utf-8")
    mateengine = (ROOT / "host_brain/channels/mateengine.py").read_text(encoding="utf-8")
    host_desktop = (ROOT / "host_brain/app/automation/host_desktop.py").read_text(encoding="utf-8")

    assert not (ROOT / "host_brain/channels/edge_rpc_client.py").exists()
    assert not (ROOT / "host_brain/app/vision/edge_rpc_client.py").exists()
    assert not (ROOT / "host_brain/app/vision/agent_loop.py").exists()
    assert "async def submit_avatar_behavior" in embodiment_commands
    assert "from host_brain.channels.embodiment_commands import" in embodiment
    for retired in (
        "post_vts", "post_asr_control", "post_barge_in", "post_desktop_pet",
        "post_speak", "post_screenshot", "post_automate", "post_visual_locate",
    ):
        assert f"async def {retired}" not in embodiment
    assert "submit_avatar_command" in embodiment
    assert "submit_voice_command" in notification
    assert "socket.socket(" not in mateengine
    assert "urllib.request.urlopen(" not in mateengine
    assert "from edge_client" not in host_desktop
    assert "import edge_client" not in host_desktop


def test_only_edge_owns_mateengine_physical_transport() -> None:
    driver = (ROOT / "edge_client/actuators/mateengine_driver.py").read_text(encoding="utf-8")
    endpoint = (ROOT / "edge_client/app/fastapi_app.py").read_text(encoding="utf-8")

    assert "urllib.request.urlopen(" in driver
    assert "socket.socket(" in driver
    assert '@app.post("/v1/edge/embodiment")' in endpoint
    assert "parse_embodiment_command" in endpoint
    assert "DurableEmbodimentRuntime" in endpoint
    assert "SQLiteEdgeEmbodimentStore" in endpoint
    assert "transport_runtime.execute" in endpoint
