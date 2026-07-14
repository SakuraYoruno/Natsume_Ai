from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_example_configuration_is_default_off_and_loopback_only() -> None:
    example = _text(".env.example")

    for row in (
        "NATSUME_BRIDGE_EXPOSURE_MODE=DISABLED",
        "HOST_MATEENGINE_BRIDGE_ENABLED=false",
        "EDGE_WECHAT_BRIDGE_ENABLED=false",
        "EDGE_WECHAT_BRIDGE_AUTO_START=false",
        "NATSUME_DESKTOP_PET_BRIDGE_ENABLED=false",
        "MATEENGINE_BRIDGE_HTTP_HOST=127.0.0.1",
        "MATEENGINE_BRIDGE_UDP_HOST=127.0.0.1",
        "MATEENGINE_BRIDGE_UDP_ENABLED=false",
        "NATSUME_DESKTOP_PET_HOST=127.0.0.1",
    ):
        assert row in example

    assert "MATEENGINE_BRIDGE_HTTP_HOST=0.0.0.0" not in example
    assert "MATEENGINE_BRIDGE_UDP_HOST=0.0.0.0" not in example
    assert "NATSUME_DESKTOP_PET_HOST=0.0.0.0" not in example


def test_effect_bridges_use_separate_hmac_and_audit_authorities() -> None:
    mate = _text("tools/mateengine_bridge_server.py")
    plugin = _text(
        "integrations/mateengine/NatsumeMateEngineBridge/BridgeSecurity.cs"
    )
    desktop = _text("desktop_pet/bridge_security.cjs")
    wechat = _text("tools/wxautox4_bridge.py")

    assert "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET" in mate
    assert "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH" in mate
    assert "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET" in mate
    assert "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET" in plugin
    assert "NATSUME_MATEENGINE_PLUGIN_AUDIT_JSONL_PATH" in plugin
    assert "NATSUME_DESKTOP_PET_HMAC_SECRET" in desktop
    assert "NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH" in desktop
    assert 'BRIDGE_HMAC_SECRET_ENV = "NATSUME_WECHAT_BRIDGE_HMAC_SECRET"' in wechat
    assert "NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET" in wechat

    assert 'GetEnvironmentVariable("NATSUME_BRIDGE_HMAC_SECRET")' not in plugin
    assert 'env.NATSUME_BRIDGE_HMAC_SECRET' not in desktop


def test_mate_plugin_auth_and_audit_precede_every_effect_enqueue() -> None:
    source = _text(
        "integrations/mateengine/NatsumeMateEngineBridge/"
        "NatsumeMateEngineBridgePlugin.cs"
    )
    listener = source[source.index("private void ListenLoop()") : source.index("private void HandleCommand")]

    assert listener.index("security.TryAuthenticate") < listener.index(
        "JsonUtility.FromJson<BridgeCommand>"
    )
    assert listener.index("security.CommitAccepted") < listener.index("_queue.Enqueue")
    assert "MaxCommandsPerFrame" in source
    assert "RelativePath(animator.transform, transform)" in source
    assert "IndexOf(cleanName" not in source

    security = _text(
        "integrations/mateengine/NatsumeMateEngineBridge/BridgeSecurity.cs"
    )
    assert "Audit log contains a torn terminal row." in security
    assert "_poisoned = true;" in security
    assert "bridge audit and replay path families must be distinct" in security
    assert "MaximumLiveClaims = 100_000" in security
    assert "CompactClaimInterval = 4_096" in security
    assert "CleanupClaimInterval = 4_096" in security
    assert "new FileInfo(_path).Length" not in security


def test_desktop_and_wechat_have_no_unsigned_effect_fallback() -> None:
    desktop_main = _text("desktop_pet/main.cjs")
    desktop_security = _text("desktop_pet/bridge_security.cjs")
    wechat = _text("tools/wxautox4_bridge.py")

    action = desktop_main[
        desktop_main.index('req.method === "POST" && req.url === "/action"') :
        desktop_main.index("writeJson(res, 404")
    ]
    assert action.index("readAuthorizedBody(req, ACTION_SCOPE)") < action.index(
        "sendAction(payload)"
    )
    assert "NATSUME_DESKTOP_PET_BRIDGE_ENABLED_REQUIRED" in desktop_security
    assert desktop_main.index("app.requestSingleInstanceLock()") < desktop_main.index(
        "app.whenReady()"
    )
    assert "if security is None or not _active_config_is_valid(security):" in wechat
    assert "default=False" in wechat


def test_protected_production_handoff_surfaces_are_not_claimed_here() -> None:
    changed_owners = {
        ".env.example",
        "desktop_pet/main.cjs",
        "desktop_pet/bridge_security.cjs",
        "integrations/mateengine/NatsumeMateEngineBridge/BridgeSecurity.cs",
        "integrations/mateengine/NatsumeMateEngineBridge/NatsumeMateEngineBridgePlugin.cs",
        "tools/mateengine_bridge_server.py",
        "tools/wxautox4_bridge.py",
    }
    protected = {
        "host_brain/app/router_registry.py",
        "host_brain/app/production_executor.py",
        "host_brain/app/production_studio.py",
        "host_brain/skills/skill_ids.py",
        "edge_client/channels/wechat_command_queue.py",
        "edge_client/runtime/bootstrap.py",
    }

    assert changed_owners.isdisjoint(protected)
