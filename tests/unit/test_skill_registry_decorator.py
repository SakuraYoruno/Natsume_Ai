from __future__ import annotations

import pytest

from host_brain.skills.registry import SkillPermissionModel, SkillRegistry, skill


class _DemoSkillProvider:
    @skill(
        skill_name="demo_echo",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        description="Echo payload for contract test",
    )
    def echo(self, args, ctx):
        return {"status": "OK", "echo": dict(args or {}), "ctx": dict(ctx or {})}

    @skill(
        skill_name="demo_sensitive",
        clearance="HIGH",
        risk_level=4,
        scope="system",
        reversible=False,
        require_confirm=True,
    )
    def sensitive(self, args, ctx):
        return {"status": "OK", "detail": "sensitive path"}


def test_skill_registry_register_decorated_and_execute() -> None:
    provider = _DemoSkillProvider()
    registry = SkillRegistry()
    count = registry.register_decorated(provider)
    assert count == 2
    assert set(registry.list_skill_names()) == {"demo_echo", "demo_sensitive"}

    meta = registry.require("demo_echo")
    result = meta.handler({"x": 1}, {"session_id": "s1"})
    assert result["status"] == "OK"
    assert result["echo"]["x"] == 1
    assert meta.description == "Echo payload for contract test"


def test_skill_meta_permission_model_property() -> None:
    provider = _DemoSkillProvider()
    registry = SkillRegistry()
    registry.register_decorated(provider)
    meta = registry.require("demo_sensitive")
    permission = meta.permission_model
    assert isinstance(permission, SkillPermissionModel)
    assert permission.clearance == "HIGH"
    assert permission.scope == "system"
    assert permission.require_confirm is True


def test_register_decorated_rejects_duplicate_skill_name() -> None:
    provider = _DemoSkillProvider()
    registry = SkillRegistry()
    registry.register_decorated(provider)
    with pytest.raises(ValueError, match="skill already registered"):
        registry.register_decorated(provider)
