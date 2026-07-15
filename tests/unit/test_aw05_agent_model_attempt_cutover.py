from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from host_brain.agent_runtime.agent_executor import AgentExecutor
from host_brain.agent_runtime.agent_prompt import (
    AGENT_MODEL_PROMPT_VERSION,
    AGENT_REACT_PROMPT_ID,
    SUBAGENT_GATEWAY_PROMPT_ID,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.agent_runtime.subagent_gateway import _SubAgentGatewayAdapter
from host_brain.model_runtime import (
    InMemoryAttemptAuditRepository,
    ModelAttemptService,
    PromptRegistry,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
)
from host_brain.model_runtime.provider_adapters import ProviderAdapterRegistry


class _ScriptedAdapter:
    adapter_id = "test.agent"

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.requests = []

    async def complete(self, request):  # noqa: ANN001, ANN202
        self.requests.append(request)
        return ProviderResponse(content=self._responses.pop(0))

    async def stream(self, request):  # noqa: ANN001
        response = await self.complete(request)
        yield response.content


def _service(registry: PromptRegistry, adapter: _ScriptedAdapter) -> ModelAttemptService:
    route = RouteDecision(
        decision_id="route-agent-test",
        targets=(
            ProviderTarget(
                adapter_id=adapter.adapter_id,
                provider="test",
                model_id="test-agent-model",
                timeout_ms=5_000,
            ),
        ),
        reason="unit_test",
        max_attempts=2,
    )
    return ModelAttemptService(
        prompt_registry=registry,
        route_policy=StaticRoutePolicy(route),
        provider_adapters=ProviderAdapterRegistry((adapter,)),
        audit_repository=InMemoryAttemptAuditRepository(),
    )


def test_agent_executor_uses_one_request_with_service_owned_semantic_retry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    registry = build_live_production_prompt_registry()
    adapter = _ScriptedAdapter(
        [
            json.dumps({"thought": "repair me", "tool_args": []}),
            json.dumps(
                {
                    "thought": "private",
                    "tool_id": "agent_finish",
                    "tool_args": {},
                    "final_answer": "canonical agent result",
                }
            ),
        ]
    )
    service = _service(registry, adapter)
    monkeypatch.setattr("host_brain.composition.process_services.get_prompt_registry", lambda: registry)
    monkeypatch.setattr("host_brain.composition.process_services.get_model_attempt_service", lambda: service)
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-aw05-agent",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "inspect the architecture",
            "metadata": {"request_id": "req-aw05-agent"},
        }
    )

    result = AgentExecutor(
        model_attempt_service=service,
        prompt_registry=registry,
    ).execute(order, workspace=tmp_path)

    assert result.status == "EXECUTED"
    assert result.result_text == "canonical agent result"
    assert len(adapter.requests) == 2
    assert {request.request_id for request in adapter.requests} == {"req-aw05-agent:agent-step:1"}
    assert adapter.requests[1].retry_reason == "missing_agent_tool_id"
    assert all(request.prompt.prompt_id == AGENT_REACT_PROMPT_ID for request in adapter.requests)
    assert all(request.prompt.version == AGENT_MODEL_PROMPT_VERSION for request in adapter.requests)
    trace = result.metadata["model_attempts"][0]
    assert trace["route_decision_id"] == "route-agent-test"
    assert [attempt["status"] for attempt in trace["attempts"]] == ["rejected", "succeeded"]
    assert result.metadata["model_provider"] == "test"
    assert result.metadata["model_id"] == "test-agent-model"


def test_subagent_gateway_has_no_private_provider_call_or_secret_state(monkeypatch) -> None:
    registry = build_live_production_prompt_registry()
    adapter = _ScriptedAdapter(["sub-agent canonical output"])
    service = _service(registry, adapter)
    monkeypatch.setattr("host_brain.composition.process_services.get_prompt_registry", lambda: registry)
    monkeypatch.setattr("host_brain.composition.process_services.get_model_attempt_service", lambda: service)
    gateway = _SubAgentGatewayAdapter(
        provider_name="gemini",
        model_id="legacy-model-must-not-route",
        api_key="secret-must-not-be-retained",
        timeout_ms=4_000,
        base_url_override="https://legacy.invalid",
        application_ports=SimpleNamespace(
            prompt_registry=lambda: registry,
            model_attempt_service=lambda: service,
        ),
        request_id="req-aw05-parent",
    )

    result = gateway.complete([{"role": "user", "content": "do bounded work"}])

    assert result["content"] == "sub-agent canonical output"
    assert result["provider"] == "test"
    assert result["model"] == "test-agent-model"
    assert result["model_attempt"]["route_decision_id"] == "route-agent-test"
    assert adapter.requests[0].prompt.prompt_id == SUBAGENT_GATEWAY_PROMPT_ID
    assert adapter.requests[0].prompt.version == AGENT_MODEL_PROMPT_VERSION
    assert adapter.requests[0].scene_type == "SUBAGENT"
    assert adapter.requests[0].request_id == "req-aw05-parent:subagent-model:1"
    assert not hasattr(gateway, "_api_key")
    assert not hasattr(gateway, "_llm")
