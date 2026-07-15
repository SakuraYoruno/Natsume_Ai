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
    desktop = _text("desktop_pet/bridge_security.cjs")
    wechat = _text("tools/wxautox4_bridge.py")

    assert "NATSUME_MATEENGINE_BRIDGE_HMAC_SECRET" in mate
    assert "NATSUME_MATEENGINE_BRIDGE_AUDIT_JSONL_PATH" in mate
    assert "NATSUME_MATEENGINE_PLUGIN_HMAC_SECRET" in mate
    assert "NATSUME_DESKTOP_PET_HMAC_SECRET" in desktop
    assert "NATSUME_DESKTOP_PET_AUDIT_JSONL_PATH" in desktop
    assert 'BRIDGE_HMAC_SECRET_ENV = "NATSUME_WECHAT_BRIDGE_HMAC_SECRET"' in wechat
    assert "NATSUME_WECHAT_GATEWAY_CALLBACK_HMAC_SECRET" in wechat

    assert 'env.NATSUME_BRIDGE_HMAC_SECRET' not in desktop


def test_mate_assembly_patcher_is_kept_as_an_offline_tool() -> None:
    project = _text("tools/mateengine/assembly_patcher/MateEngineAssemblyPatcher.csproj")
    source = _text("tools/mateengine/assembly_patcher/Program.cs")

    assert "Mono.Cecil" in project
    assert 'const string TextPrefix = "NATSUME_TEXT::";' in source
    assert 'var backupPath = assemblyPath + ".natsume-bak";' in source
    assert "File.Copy(assemblyPath, backupPath);" in source
    assert "PatchActionEvent(module, applyAction)" in source


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
        "tools/mateengine_bridge_server.py",
        "tools/mateengine/assembly_patcher/Program.cs",
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
