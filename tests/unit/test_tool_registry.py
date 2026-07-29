"""ReAct Tool 目录退役执行 authority 后的契约测试。"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import host_brain.app.agents.tool_registry as registry
from host_brain.app.agents.tool_registry import (
    REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
    ToolSpec,
    execute_tool,
    get_tools_for_agent,
    is_tool_allowed,
)


def _assert_rejected(result: dict[str, object], tool_id: str) -> None:
    assert result == {
        "error": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "reason_code": REACT_TOOL_GOVERNED_ADMISSION_REQUIRED,
        "proposed_tool_id": tool_id,
        "result": None,
        "side_effects_executed": False,
        "required_execution_surface": "new immutable TaskGraph revision",
    }


@pytest.mark.parametrize(
    "tool_id,args",
    [
        ("search_knowledge", {"query": "private"}),
        ("list_skills", {}),
        ("run_skill", {"skill_name": "memory_store"}),
        ("dispatch_sub_agent", {"agent_id": "pentest_agent", "task": "probe"}),
        ("evaluate_artifact", {"artifact": {"confidence": 1.0}}),
        ("nonexistent_tool", {}),
    ],
)
def test_raw_dispatcher_is_fixed_rejection(
    tool_id: str,
    args: dict[str, object],
) -> None:
    _assert_rejected(execute_tool(tool_id, args), tool_id)


def test_static_registry_contains_metadata_without_callbacks() -> None:
    assert registry._TOOL_REGISTRY
    assert all(isinstance(spec, ToolSpec) for spec in registry._TOOL_REGISTRY.values())
    assert all(spec.executor is None for spec in registry._TOOL_REGISTRY.values())


@pytest.mark.parametrize(
    "agent_id",
    [
        "surface_reply",
        "translate_agent",
        "retrieval_agent",
        "intent_agent",
        "judge_agent",
    ],
)
def test_agent_discovery_returns_proposal_only_clones(agent_id: str) -> None:
    tools = get_tools_for_agent(agent_id, allow_side_effects=True)

    assert all(spec.executor is None for spec in tools)
    assert len({spec.tool_id for spec in tools}) == len(tools)


def test_pentest_agent_has_no_generic_react_tools() -> None:
    assert get_tools_for_agent("pentest_agent", allow_side_effects=False) == []
    assert get_tools_for_agent("pentest_agent", allow_side_effects=True) == []


def test_retired_emotion_agent_has_no_tool_visibility() -> None:
    assert get_tools_for_agent("emotion_agent", allow_side_effects=False) == []
    assert get_tools_for_agent("emotion_agent", allow_side_effects=True) == []


def test_side_effect_metadata_is_hidden_without_explicit_visibility() -> None:
    without_side_effects = {
        spec.tool_id
        for spec in get_tools_for_agent("intent_agent", allow_side_effects=False)
    }
    with_side_effects = {
        spec.tool_id
        for spec in get_tools_for_agent("intent_agent", allow_side_effects=True)
    }

    assert without_side_effects <= with_side_effects
    assert "run_skill" not in without_side_effects
    assert "dispatch_sub_agent" not in without_side_effects


def test_catalog_callback_cannot_cross_metadata_adapter() -> None:
    called: list[str] = []
    injected = ToolSpec(
        tool_id="search_knowledge",
        display_name="Injected",
        description="must remain metadata",
        parameters={"type": "object", "properties": {}, "required": []},
        has_side_effects=False,
        executor=lambda _args: called.append("executed"),
    )

    class _Catalog:
        def get(self, tool_id: str):
            assert tool_id
            return SimpleNamespace(
                id=injected.tool_id,
                name=injected.display_name,
                desc=injected.description,
                side_effect_level="READ",
                executor=injected.executor,
            )

    discovered = get_tools_for_agent("surface_reply", catalog=_Catalog())

    assert discovered
    assert all(spec.executor is None for spec in discovered)
    assert called == []


def test_allowlist_check_is_visibility_only() -> None:
    assert is_tool_allowed("evaluate_artifact", "judge_agent") is True
    assert is_tool_allowed("agent_write_file", "judge_agent") is False


def test_legacy_handler_symbols_and_service_locators_are_removed() -> None:
    for name in (
        "_tool_search_knowledge",
        "_tool_search_by_keyword",
        "_tool_search_by_vector",
        "_tool_get_context",
        "_tool_list_skills",
        "_tool_run_skill",
        "_tool_dispatch_sub_agent",
        "_tool_analyze_code",
        "_tool_translate_text",
        "_tool_evaluate_artifact",
        "_tool_compare_artifacts",
        "_tool_emotion_history_search",
        "_tool_emotion_timeline_query",
    ):
        assert not hasattr(registry, name)

    source = inspect.getsource(registry)
    assert "host_brain.composition.process_services" not in source
    assert "SemanticRecaller" not in source
    assert "execute_browser_skill" not in source
