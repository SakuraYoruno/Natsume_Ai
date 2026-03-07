"""Scene-routed node sequence tests for M33 model-plane orchestration.

Coverage:
- CHAT minimal chain: primary -> renderer
- core_reasoner_secondary remains fallback-only
- EXEC_INTENT includes planner/tool/safety/consistency
- WORK_* includes corresponding workbench node + consistency + renderer
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.fastapi_app import (
    CHAT_DIRECT_NODE_ID,
    CHAT_GRAPH_NODE_ORDER,
    SCENE_CHAT,
    SCENE_EXEC_INTENT,
    SCENE_WORK_CODE,
    SCENE_WORK_TRANSLATE,
    _build_chat_graph_config,
    _build_host_provider_fn_for_node,
    _resolve_scene_node_sequence,
)


def _make_mock_provider_fn() -> MagicMock:
    return MagicMock(return_value={"status": "OK"})


def _make_graph_config(scene_type: str = SCENE_CHAT) -> dict:
    mock_fn = _make_mock_provider_fn()
    return _build_chat_graph_config(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        timeout_ms=5000,
        available=True,
        api_key="test-key",
        provider_fn=mock_fn,
        scene_type=scene_type,
    )


class TestSceneNodeSequence:
    def test_chat_scene_returns_minimal_sequence(self) -> None:
        seq = _resolve_scene_node_sequence(SCENE_CHAT)
        assert seq == [CHAT_DIRECT_NODE_ID]
        assert len(seq) == 1

    def test_exec_intent_scene_contains_required_nodes(self) -> None:
        seq = _resolve_scene_node_sequence(SCENE_EXEC_INTENT)
        assert "planner_constraints" in seq
        assert "tool_planner" in seq
        assert "safety_verifier" in seq
        assert "consistency_verifier" in seq
        assert "core_reasoner_primary" in seq
        assert "renderer_ja_c_style" in seq
        assert "workbench_translate" not in seq
        assert "workbench_code_assist" not in seq

    def test_work_translate_scene_contains_translate_not_code(self) -> None:
        seq = _resolve_scene_node_sequence(SCENE_WORK_TRANSLATE)
        assert "workbench_translate" in seq
        assert "workbench_code_assist" not in seq
        assert "consistency_verifier" in seq
        assert "core_reasoner_primary" in seq
        assert "renderer_ja_c_style" in seq

    def test_work_code_scene_contains_code_not_translate(self) -> None:
        seq = _resolve_scene_node_sequence(SCENE_WORK_CODE)
        assert "workbench_code_assist" in seq
        assert "workbench_translate" not in seq
        assert "consistency_verifier" in seq
        assert "core_reasoner_primary" in seq
        assert "renderer_ja_c_style" in seq

    def test_unknown_scene_falls_back_to_chat(self) -> None:
        seq = _resolve_scene_node_sequence("UNKNOWN_SCENE_XYZ")
        assert seq == [CHAT_DIRECT_NODE_ID]

    def test_empty_scene_falls_back_to_chat(self) -> None:
        seq = _resolve_scene_node_sequence("")
        assert seq == [CHAT_DIRECT_NODE_ID]

    def test_none_scene_falls_back_to_chat(self) -> None:
        seq = _resolve_scene_node_sequence(None)  # type: ignore[arg-type]
        assert seq == [CHAT_DIRECT_NODE_ID]

    def test_renderer_always_last(self) -> None:
        for scene in [SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            seq = _resolve_scene_node_sequence(scene)
            assert seq[-1] == "renderer_ja_c_style", f"renderer not last in {scene}: {seq}"

    def test_primary_always_first(self) -> None:
        assert _resolve_scene_node_sequence(SCENE_CHAT)[0] == CHAT_DIRECT_NODE_ID
        for scene in [SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            seq = _resolve_scene_node_sequence(scene)
            assert seq[0] == "core_reasoner_primary", f"primary not first in {scene}: {seq}"

    def test_secondary_never_in_normal_sequence(self) -> None:
        for scene in [SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            seq = _resolve_scene_node_sequence(scene)
            assert "core_reasoner_secondary" not in seq, f"secondary in normal seq for {scene}: {seq}"


class TestGraphConfigSceneRouting:
    def test_chat_scene_node_bindings_and_edges(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        node_ids = [nb["node_id"] for nb in config["node_bindings"]]
        assert node_ids == [CHAT_DIRECT_NODE_ID]
        assert config["edges"] == []

    def test_secondary_not_in_chat_edges(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        edge_nodes = set()
        for edge in config["edges"]:
            edge_nodes.add(edge["from"])
            edge_nodes.add(edge["to"])
        assert "core_reasoner_secondary" not in edge_nodes

    def test_chat_fast_path_falls_back_directly_to_renderer(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        fallback_edges = config["fallback_edges"]
        assert fallback_edges == []

    def test_exec_intent_keeps_secondary_fallback_chain(self) -> None:
        config = _make_graph_config(SCENE_EXEC_INTENT)
        fallback_edges = config["fallback_edges"]
        assert {"from": "core_reasoner_primary", "to": "core_reasoner_secondary"} in fallback_edges
        assert {"from": "core_reasoner_secondary", "to": "consistency_verifier"} in fallback_edges

    def test_exec_intent_node_bindings_count(self) -> None:
        config = _make_graph_config(SCENE_EXEC_INTENT)
        node_ids = [nb["node_id"] for nb in config["node_bindings"]]
        assert "planner_constraints" in node_ids
        assert "tool_planner" in node_ids
        assert "safety_verifier" in node_ids
        assert "consistency_verifier" in node_ids
        assert len(config["edges"]) == 5

    def test_work_translate_node_bindings(self) -> None:
        config = _make_graph_config(SCENE_WORK_TRANSLATE)
        node_ids = [nb["node_id"] for nb in config["node_bindings"]]
        assert "workbench_translate" in node_ids
        assert "consistency_verifier" in node_ids
        assert "workbench_code_assist" not in node_ids
        assert len(config["edges"]) == 3

    def test_work_code_node_bindings(self) -> None:
        config = _make_graph_config(SCENE_WORK_CODE)
        node_ids = [nb["node_id"] for nb in config["node_bindings"]]
        assert "workbench_code_assist" in node_ids
        assert "consistency_verifier" in node_ids
        assert "workbench_translate" not in node_ids
        assert len(config["edges"]) == 3

    def test_model_node_overrides_always_has_all_9_nodes(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        overrides = config["model_node_overrides"]
        for node_id in CHAT_GRAPH_NODE_ORDER:
            assert node_id in overrides, f"node {node_id} missing from model_node_overrides"

    def test_entry_node_matches_scene_sequence(self) -> None:
        assert _make_graph_config(SCENE_CHAT)["entry_node"] == CHAT_DIRECT_NODE_ID
        for scene in [SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            config = _make_graph_config(scene)
            assert config["entry_node"] == "core_reasoner_primary"

    def test_edges_match_node_sequence(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        assert config["edges"] == []


class TestGraphMetaSceneField:
    def test_chat_scene_meta_type(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        assert config["graph_meta"]["scene_type"] == "CHAT"

    def test_exec_intent_scene_meta_type(self) -> None:
        config = _make_graph_config(SCENE_EXEC_INTENT)
        assert config["graph_meta"]["scene_type"] == "EXEC_INTENT"

    def test_active_node_count_in_meta(self) -> None:
        for scene in [SCENE_CHAT, SCENE_EXEC_INTENT, SCENE_WORK_TRANSLATE, SCENE_WORK_CODE]:
            config = _make_graph_config(scene)
            expected_count = len(_resolve_scene_node_sequence(scene))
            assert config["graph_meta"]["active_node_count"] == expected_count, (
                f"scene={scene}: active_node_count={config['graph_meta']['active_node_count']} != {expected_count}"
            )

    def test_online_chain_updated(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        assert "scene_routed" in config["graph_meta"]["online_chain"]

    def test_policy_timeout_budget_is_exposed(self) -> None:
        config = _make_graph_config(SCENE_CHAT)
        policy_timeout_ms = int(config.get("policy_timeout_ms") or 0)
        assert 8_000 <= policy_timeout_ms <= 12_000

    def test_policy_timeout_budget_accounts_for_retry_and_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS", "3")
        monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS", "100")
        monkeypatch.delenv("HOST_GEMINI_PRIMARY_DISABLE_MODEL_FALLBACK", raising=False)
        monkeypatch.delenv("HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID", raising=False)

        config = _make_graph_config(SCENE_CHAT)
        policy_timeout_ms = int(config.get("policy_timeout_ms") or 0)
        # CHAT fast path caps total graph budget into an 8-12s window.
        assert 8_000 <= policy_timeout_ms <= 12_000


def test_core_reasoner_primary_gemini_pro_retries_timeout(monkeypatch) -> None:
    from host_brain.app import fastapi_app as appmod

    call_count = 0

    def _fake_builder(**kwargs):  # noqa: ANN003
        _ = kwargs

        def _provider(_payload):  # noqa: ANN001
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("Request timed out.")
            return {"status": "OK"}

        return _provider

    monkeypatch.setattr(appmod, "_build_langchain_chat_provider_fn", _fake_builder)
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS", "0")

    provider_fn = _build_host_provider_fn_for_node(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        api_key="test-key",
        timeout_ms=12000,
        node_id="core_reasoner_primary",
    )

    result = provider_fn({"input": {"user_message": "retry me"}})
    assert result == {"status": "OK"}
    assert call_count == 2


def test_core_reasoner_primary_permission_failure_is_not_retried(monkeypatch) -> None:
    from host_brain.app import fastapi_app as appmod

    call_count = 0

    def _fake_builder(**kwargs):  # noqa: ANN003
        _ = kwargs

        def _provider(_payload):  # noqa: ANN001
            nonlocal call_count
            call_count += 1
            raise RuntimeError("403 permission denied for model")

        return _provider

    monkeypatch.setattr(appmod, "_build_langchain_chat_provider_fn", _fake_builder)
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS", "0")

    provider_fn = _build_host_provider_fn_for_node(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        api_key="test-key",
        timeout_ms=12000,
        node_id="core_reasoner_primary",
    )

    try:
        provider_fn({"input": {"user_message": "do not retry"}})
    except RuntimeError as exc:
        assert "403" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected permission failure")
    assert call_count == 1


def test_core_reasoner_primary_quota_failure_downgrades_to_31pro_default(monkeypatch) -> None:
    from host_brain.app import fastapi_app as appmod

    call_counts = {
        "gemini-2.5-pro": 0,
        "gemini-3.1-pro-preview": 0,
    }

    def _fake_builder(**kwargs):  # noqa: ANN003
        model_id = str(kwargs.get("model_id") or "")

        def _provider(_payload):  # noqa: ANN001
            call_counts[model_id] = int(call_counts.get(model_id, 0)) + 1
            if model_id == "gemini-2.5-pro":
                raise RuntimeError("429 RESOURCE_EXHAUSTED: quota exceeded")
            return {"status": "OK", "model_id": model_id}

        return _provider

    monkeypatch.setattr(appmod, "_build_langchain_chat_provider_fn", _fake_builder)
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS", "0")
    monkeypatch.delenv("HOST_GEMINI_PRIMARY_DISABLE_MODEL_FALLBACK", raising=False)
    monkeypatch.delenv("HOST_GEMINI_PRIMARY_FALLBACK_MODEL_ID", raising=False)

    provider_fn = _build_host_provider_fn_for_node(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        api_key="test-key",
        timeout_ms=12000,
        node_id="core_reasoner_primary",
    )

    result = provider_fn({"input": {"user_message": "downgrade me"}})
    assert result == {"status": "OK", "model_id": "gemini-3.1-pro-preview"}
    assert call_counts["gemini-2.5-pro"] == 1
    assert call_counts["gemini-3.1-pro-preview"] == 1


def test_non_primary_node_does_not_retry_timeout(monkeypatch) -> None:
    from host_brain.app import fastapi_app as appmod

    call_count = 0

    def _fake_builder(**kwargs):  # noqa: ANN003
        _ = kwargs

        def _provider(_payload):  # noqa: ANN001
            nonlocal call_count
            call_count += 1
            raise TimeoutError("Request timed out.")

        return _provider

    monkeypatch.setattr(appmod, "_build_langchain_chat_provider_fn", _fake_builder)
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("HOST_GEMINI_PRIMARY_RETRY_DELAY_MS", "0")

    provider_fn = _build_host_provider_fn_for_node(
        provider_name="gemini",
        model_id="gemini-2.5-pro",
        api_key="test-key",
        timeout_ms=9000,
        node_id="core_reasoner_secondary",
    )

    try:
        provider_fn({"input": {"user_message": "single attempt only"}})
    except TimeoutError:
        pass
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected timeout")
    assert call_count == 1
