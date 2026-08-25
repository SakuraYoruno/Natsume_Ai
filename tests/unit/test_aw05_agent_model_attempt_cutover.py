from __future__ import annotations

import json
from pathlib import Path

from host_brain.agent_runtime.agent_executor import AgentExecutor
from host_brain.agent_runtime.agent_tools import (
    AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED,
)
from host_brain.agent_runtime.agent_prompt import (
    AGENT_MODEL_PROMPT_VERSION,
)
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
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
from host_brain.model_runtime import AGENT_REACT_STEP_PROMPT_ID
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
    assert all(request.prompt.prompt_id == AGENT_REACT_STEP_PROMPT_ID for request in adapter.requests)
    assert all(request.prompt.version == AGENT_MODEL_PROMPT_VERSION for request in adapter.requests)
    trace = result.metadata["model_attempts"][0]
    assert trace["route_decision_id"] == "route-agent-test"
    assert [attempt["status"] for attempt in trace["attempts"]] == ["rejected", "succeeded"]
    assert result.metadata["model_provider"] == "test"
    assert result.metadata["model_id"] == "test-agent-model"


def test_agent_executor_blocks_write_order_before_model_attempt(
    tmp_path: Path,
) -> None:
    registry = build_live_production_prompt_registry()
    adapter = _ScriptedAdapter(
        [
            json.dumps(
                {
                    "thought": "claim completion without doing the write",
                    "tool_id": "agent_finish",
                    "tool_args": {},
                    "final_answer": "pretend the write completed",
                }
            )
        ]
    )
    service = _service(registry, adapter)
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-aw05-agent-cancel",
            "domain": "code",
            "run_mode": "worker",
            "objective": "do not write after cancellation",
            "allowed_actions": ["write_files"],
            "forbidden_actions": ["modify_secret_files"],
            "metadata": {"request_id": "req-aw05-agent-cancel"},
        }
    )
    result = AgentExecutor(
        model_attempt_service=service,
        prompt_registry=registry,
    ).execute(order, workspace=tmp_path)

    assert result.status == "BLOCKED"
    assert result.reason_code == AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED
    assert result.acceptance["decision"] == "NOT_RUN"
    assert result.verification["status"] == "NOT_RUN"
    assert result.metadata["model_attempts"] == []
    assert adapter.requests == []
    assert not (tmp_path / "must-not-exist.txt").exists()


def test_agent_executor_records_read_tool_proposal_without_nested_execution(
    tmp_path: Path,
) -> None:
    registry = build_live_production_prompt_registry()
    adapter = _ScriptedAdapter(
        [
            json.dumps(
                {
                    "thought": "read inside the running node",
                    "tool_id": "agent_read_file",
                    "tool_args": {
                        "path": "must-not-be-read.txt",
                    },
                }
            )
        ]
    )
    service = _service(registry, adapter)
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-aw05-agent-tool-proposal",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "propose a read",
            "allowed_actions": ["read_files"],
            "forbidden_actions": ["modify_secret_files"],
            "metadata": {"request_id": "req-aw05-agent-tool-proposal"},
        }
    )
    result = AgentExecutor(
        model_attempt_service=service,
        prompt_registry=registry,
    ).execute(order, workspace=tmp_path)

    assert result.status == "DEGRADED"
    assert result.reason_code == AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED
    assert result.metadata["stopped_reason"] == "TOOL_GOVERNED_ADMISSION_REQUIRED"
    assert result.risk_flags == [AGENT_TOOL_GOVERNED_ADMISSION_REQUIRED]
    assert len(adapter.requests) == 1
    assert not (tmp_path / "must-not-be-read.txt").exists()


def test_agent_executor_planning_proposal_exposes_only_terminal_report(
    tmp_path: Path,
) -> None:
    registry = build_live_production_prompt_registry()
    adapter = _ScriptedAdapter(
        [
            json.dumps(
                {
                    "thought": "produce the bounded proposal",
                    "tool_id": "agent_finish",
                    "tool_args": {},
                    "final_answer": "先定义验收目标，再覆盖关键路径、异常状态与可访问性。",
                },
                ensure_ascii=False,
            )
        ]
    )
    service = _service(registry, adapter)
    order = AgentWorkOrder.from_mapping(
        {
            "work_order_id": "wo-aw05-plan-proposal",
            "domain": "code",
            "run_mode": "inspect",
            "objective": "给出前端可用性审查方案",
            "allowed_actions": ["generate_report"],
            "forbidden_actions": ["modify_secret_files"],
            "metadata": {
                "request_id": "req-aw05-plan-proposal",
                "proposal_only": True,
                "execution_policy": "PRE_ACCEPTANCE_PROPOSAL_ONLY",
            },
        }
    )

    result = AgentExecutor(
        model_attempt_service=service,
        prompt_registry=registry,
    ).execute(order, workspace=tmp_path)

    assert result.status == "EXECUTED"
    assert "关键路径" in result.result_text
    assert len(adapter.requests) == 1
    rendered_prompt = "\n".join(
        str(message.get("content") or "")
        for message in adapter.requests[0].prompt.messages
    )
    assert "effect-free planning proposal node" in rendered_prompt
    assert "final_answer under 1,200 Chinese characters" in rendered_prompt
    assert "agent_read_file" not in rendered_prompt
