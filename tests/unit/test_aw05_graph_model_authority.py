from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.integrations.graph_executor import GraphExecutor
from host_brain.model_runtime import (
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    UnknownGraphModelSemanticError,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
    graph_model_semantic,
)


class _CapturingRoutePolicy:
    def __init__(self) -> None:
        self.requests = []
        self.prompts = []

    async def resolve(self, request, prompt):  # noqa: ANN001, ANN201
        self.requests.append(request)
        self.prompts.append(prompt)
        return RouteDecision(
            "route-graph-authority",
            (ProviderTarget("graph-test", "mock", "canonical-model", timeout_ms=3000),),
            "unit-test",
            max_attempts=1,
        )


def test_graph_semantics_are_central_and_unknown_nodes_fail_closed() -> None:
    renderer = graph_model_semantic("renderer_ja_c_style")
    reasoner = graph_model_semantic("core_reasoner_primary")

    assert (renderer.prompt_id, renderer.scene_type, renderer.cognitive_tier) == (
        "graph.renderer.ja_c_style.json",
        "GRAPH_RENDERER",
        "C",
    )
    assert (reasoner.prompt_id, reasoner.scene_type, reasoner.cognitive_tier) == (
        "graph.core_reasoner.primary.json",
        "GRAPH_CORE_REASONER",
        "A",
    )
    with pytest.raises(UnknownGraphModelSemanticError):
        graph_model_semantic("unregistered_graph_node")


def test_graph_executor_invokes_only_canonical_composition_with_node_semantics() -> None:
    route = _CapturingRoutePolicy()
    provider_attempts = []

    def _complete(attempt):  # noqa: ANN001, ANN202
        provider_attempts.append(attempt)
        return '{"narrative":{"text":"ok"},"intent_draft":{},"meta":{"draft_only":true}}'

    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=route,
        provider_adapters=[CallableProviderAdapter("graph-test", complete_fn=_complete)],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    gateway_bindings = []

    class _InvokingGateway:
        def __init__(self, provider) -> None:  # noqa: ANN001
            self._provider = provider

        def generate(self, **kwargs):  # noqa: ANN003, ANN201
            self._provider(dict(kwargs["prompt_payload"]))
            return SimpleNamespace(
                success=True,
                status="OK",
                reason_code=None,
                output={
                    "payload": {
                        "narrative": {"text": "ok"},
                        "intent_draft": {},
                        "meta": {"draft_only": True},
                    }
                },
            )

    def _gateway_factory(**kwargs):  # noqa: ANN003, ANN202
        gateway_bindings.append(kwargs)
        return _InvokingGateway(kwargs["provider"])

    executor = GraphExecutor(
        handlers={},
        gateway_factory=_gateway_factory,
        model_attempt_service=composition.attempt_service,
        model_nodes={
            "renderer_ja_c_style": {
                "provider": "mock",
                "model_id": "configured-model",
                "timeout_ms": 3000,
                "available": True,
            }
        },
    )
    result = executor._invoke_model_node(
        node_id="respond",
        binding={"model_node_id": "renderer_ja_c_style", "task": "CHAT"},
        state={
            "request_id": "req-graph-authority",
            "graph_id": "graph-authority",
            "timeout_ms": 5000,
            "payload": {"input": "hello"},
            "edge_map": {},
            "model_profile_id": "test",
            "model_calls": [],
        },
    )

    assert result["__orchestration_status__"] == "OK"
    assert len(route.requests) == len(route.prompts) == len(provider_attempts) == 1
    assert route.requests[0].scene_type == "GRAPH_RENDERER"
    assert route.requests[0].cognitive_tier == "C"
    assert route.prompts[0].prompt_id == "graph.renderer.ja_c_style.json"
    assert provider_attempts[0].prompt.prompt_id == "graph.renderer.ja_c_style.json"
    assert gateway_bindings[0]["api_key"] is None
    assert getattr(gateway_bindings[0]["provider"], "natsume_provider_kind") == (
        "canonical_model_attempt"
    )


def test_graph_executor_rejects_unregistered_model_semantic_before_gateway() -> None:
    executor = GraphExecutor(
        handlers={},
        gateway_factory=lambda **_: pytest.fail("gateway must not be constructed"),
        model_nodes={
            "rogue_node": {
                "provider": "mock",
                "model_id": "rogue",
                "timeout_ms": 3000,
            }
        },
    )

    with pytest.raises(UnknownGraphModelSemanticError):
        executor._invoke_model_node(
            node_id="rogue_node",
            binding={"model_node_id": "rogue_node"},
            state={"request_id": "req-rogue", "payload": {}, "model_calls": []},
        )
