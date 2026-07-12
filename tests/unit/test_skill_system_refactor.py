from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.skills.declarative import DeclarativeSkillProvider, DeclarativeSkillSpec
from host_brain.skills.executor_registry import ToolExecutorRegistry, ToolExecutorSpec
from host_brain.skills.registry import SkillRegistry


def _normalize_demo_args(args: dict[str, object]) -> dict[str, object]:
    return {"value": str((args or {}).get("value") or "").upper()}


class _DeclarativeDemoProvider(DeclarativeSkillProvider):
    skill_specs = (
        DeclarativeSkillSpec(
            skill_name="demo_declarative",
            method_name="demo_handler",
            clearance="LOW",
            risk_level=1,
            scope="host",
            reversible=True,
            description="Declarative demo skill",
            tool="demo",
            action="echo",
            normalize_args=_normalize_demo_args,
            context_aliases=(("session_id", "session_id"), ("request_id", "request_id")),
        ),
    )


def test_register_decorated_supports_declarative_provider() -> None:
    provider = _DeclarativeDemoProvider()
    registry = SkillRegistry()

    count = registry.register_decorated(provider)
    payload = registry.require("demo_declarative").handler(
        {"value": "hello"},
        {"session_id": "sess-1", "request_id": "req-1"},
    )
    direct = provider.demo_handler({"value": "hello"}, {"session_id": "sess-1"})

    assert count == 1
    assert payload["tool"] == "demo"
    assert payload["action"] == "echo"
    assert payload["value"] == "HELLO"
    assert payload["session_id"] == "sess-1"
    assert payload["request_id"] == "req-1"
    assert direct["value"] == "HELLO"


def test_tool_executor_registry_dispatches_and_passthroughs() -> None:
    registry = ToolExecutorRegistry()
    seen: list[str] = []

    def _handler(payload: dict[str, object]) -> dict[str, object]:
        seen.append(str(payload.get("tool") or ""))
        return {"status": "EXECUTED", "tool": payload.get("tool")}

    registry.register(ToolExecutorSpec("demo", _handler, aliases=("demo_alias",)))

    result_primary = registry._dispatch_unchecked({"tool": "demo"})
    result_alias = registry._dispatch_unchecked({"tool": "demo_alias"})

    assert result_primary["status"] == "EXECUTED"
    assert result_alias["status"] == "EXECUTED"
    with pytest.raises(LookupError, match="not registered"):
        registry._dispatch_unchecked({"tool": "missing", "x": 1})
    assert seen == ["demo", "demo_alias"]


def test_declarative_skill_spec_can_skip_default_action() -> None:
    spec = DeclarativeSkillSpec(
        skill_name="demo_optional_action",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=True,
        description="optional action demo",
        tool="demo",
        action=None,
        normalize_args=lambda args: {"value": args.get("value")},
    )

    payload = spec.build_handler()({"value": "ok"}, None)

    assert payload["tool"] == "demo"
    assert payload["value"] == "ok"
    assert "action" not in payload
