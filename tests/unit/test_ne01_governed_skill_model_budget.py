from __future__ import annotations

import pytest

from host_brain.app.governed_frozen_skill_runtime import (
    _trusted_skill_budget_observations,
)
from host_brain.app.governed_task_authority import (
    GovernedHostAdapterError,
)
from host_brain.contracts.agent_runtime.worker_budget_usage import (
    build_worker_budget_usage,
)
from host_brain.model_runtime import (
    EMERGENT_GROWTH_PATTERN_PROMPT_ID,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ModelBudgetGuard,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
    compiled_prompt_request,
)
from host_brain.model_runtime.governed_skill_budget import (
    GOVERNED_SKILL_MODEL_BUDGET_ATTESTATION_SCHEMA_VERSION,
    GovernedSkillModelBudgetRuntime,
)
from host_brain.model_runtime.governed_budget import (
    GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID,
    GovernedModelBudgetError,
)
from host_brain.skills.impl.emergent_growth_skill import (
    execute_emergent_growth_weekly,
)
from host_brain.skills.registry import SkillMeta


def _guard() -> ModelBudgetGuard:
    return ModelBudgetGuard(
        budget_id="budget://governed-skill-model",
        graph_id="graph-governed-skill-model",
        graph_revision=3,
        node_id="node-governed-skill-model",
        attempt_id="attempt-governed-skill-model",
        deadline_ms=5_000,
        max_provider_attempts=2,
        max_token_count=100,
        max_output_tokens=40,
        max_model_cost_micros=1_000,
    )


def _composition(*, usage: dict[str, int] | None):
    requests = []

    async def complete(request):  # noqa: ANN001, ANN202
        requests.append(request)
        response = {"content": '{"signal":"rich"}'}
        if usage is not None:
            response["usage"] = dict(usage)
        return response

    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=StaticRoutePolicy(
            RouteDecision(
                decision_id="route-governed-skill-model",
                targets=(
                    ProviderTarget(
                        "governed-skill-model",
                        "test",
                        "model-test",
                    ),
                ),
                reason="UNIT_TEST",
                max_attempts=2,
            )
        ),
        provider_adapters=(
            CallableProviderAdapter(
                "governed-skill-model",
                complete_fn=complete,
            ),
        ),
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    return composition, requests


def _execution_context() -> dict[str, object]:
    return {
        "request_id": "request-governed-skill-model",
        "graph_id": "graph-governed-skill-model",
        "graph_revision": 3,
        "node_id": "node-governed-skill-model",
        "attempt_id": "attempt-governed-skill-model",
        "budget": {
            "budget_id": "budget://governed-skill-model",
        },
    }


def test_compiled_prompt_request_carries_only_typed_model_budget_guard() -> None:
    guard = _guard()
    request = compiled_prompt_request(
        prompt_id=EMERGENT_GROWTH_PATTERN_PROMPT_ID,
        messages=({"role": "user", "content": "inspect"},),
        request_id="request-governed-skill-model",
        scene_type="EMERGENT_GROWTH",
        cognitive_tier="B",
        deadline_ms=5_000,
        model_budget_guard=guard,
    )

    assert request.model_budget_guard is guard
    with pytest.raises(TypeError, match="ModelBudgetGuard"):
        compiled_prompt_request(
            prompt_id=EMERGENT_GROWTH_PATTERN_PROMPT_ID,
            messages=({"role": "user", "content": "inspect"},),
            request_id="request-governed-skill-model",
            scene_type="EMERGENT_GROWTH",
            cognitive_tier="B",
            deadline_ms=5_000,
            model_budget_guard={"max_token_count": 100},  # type: ignore[arg-type]
        )


def test_governed_skill_model_runtime_reduces_and_attests_every_call() -> None:
    composition, requests = _composition(
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "model_cost_micros": 20,
        }
    )
    runtime = GovernedSkillModelBudgetRuntime(
        attempt_service=composition.attempt_service,
        model_budget_guard=_guard(),
        task_request_id="request-governed-skill-model",
    )

    for prompt in ("first", "second"):
        result = runtime.complete_sync(
            prompt_id=EMERGENT_GROWTH_PATTERN_PROMPT_ID,
            messages=({"role": "user", "content": prompt},),
            scene_type="EMERGENT_GROWTH",
            cognitive_tier="B",
            deadline_ms=10_000,
            foreground=False,
        )
        assert result.success is True

    assert len(requests) == 2
    assert requests[0].model_budget_guard == _guard()
    assert requests[1].model_budget_guard.max_token_count == 85
    assert requests[1].model_budget_guard.max_model_cost_micros == 980
    assert requests[1].model_budget_guard.graph_id == _guard().graph_id
    attestation = runtime.attestation()
    assert attestation is not None
    assert attestation["schema_version"] == (
        GOVERNED_SKILL_MODEL_BUDGET_ATTESTATION_SCHEMA_VERSION
    )
    assert attestation["task_request_id"] == (
        "request-governed-skill-model"
    )
    assert len(attestation["model_attempts"]) == 2

    observations = _trusted_skill_budget_observations(
        {
            "result": {"status": "OK"},
            "governed_skill_model_budget_attestation": attestation,
        },
        execution_context=_execution_context(),
    )
    usage = build_worker_budget_usage(
        usage_id="worker-budget://governed-skill-model",
        observations=observations,
    )
    assert usage is not None
    assert usage.measurements == {
        "retry_count": 0,
        "token_count": 30,
        "model_cost_micros": 40,
    }


def test_governed_skill_model_runtime_rejects_missing_provider_usage() -> None:
    composition, _requests = _composition(usage=None)
    runtime = GovernedSkillModelBudgetRuntime(
        attempt_service=composition.attempt_service,
        model_budget_guard=_guard(),
        task_request_id="request-governed-skill-model",
    )

    with pytest.raises(GovernedModelBudgetError) as raised:
        runtime.complete_sync(
            prompt_id=EMERGENT_GROWTH_PATTERN_PROMPT_ID,
            messages=({"role": "user", "content": "missing usage"},),
            scene_type="EMERGENT_GROWTH",
            cognitive_tier="B",
            deadline_ms=5_000,
        )
    assert raised.value.reason_code == (
        GOVERNED_MODEL_BUDGET_MEASUREMENT_INVALID
    )
    assert runtime.attestation() is None


def test_skill_budget_attestation_rejects_task_binding_drift() -> None:
    composition, _requests = _composition(
        usage={
            "prompt_tokens": 2,
            "completion_tokens": 3,
            "total_tokens": 5,
            "model_cost_micros": 7,
        }
    )
    runtime = GovernedSkillModelBudgetRuntime(
        attempt_service=composition.attempt_service,
        model_budget_guard=_guard(),
        task_request_id="request-governed-skill-model",
    )
    runtime.complete_sync(
        prompt_id=EMERGENT_GROWTH_PATTERN_PROMPT_ID,
        messages=({"role": "user", "content": "drift"},),
        scene_type="EMERGENT_GROWTH",
        cognitive_tier="B",
        deadline_ms=5_000,
    )
    attestation = dict(runtime.attestation() or {})
    attestation["attempt_id"] = "attempt-forged"

    with pytest.raises(
        GovernedHostAdapterError,
        match="attestation binding drifted",
    ):
        _trusted_skill_budget_observations(
            {
                "result": {"status": "OK"},
                "governed_skill_model_budget_attestation": attestation,
            },
            execution_context=_execution_context(),
        )


def test_emergent_growth_governed_path_requires_bound_model_runtime() -> None:
    with pytest.raises(
        RuntimeError,
        match="GOVERNED_SKILL_MODEL_BUDGET_REQUIRED",
    ):
        execute_emergent_growth_weekly(
            args={"user_id": "natsume_ai"},
            context={
                "request_id": "request-governed-skill-model",
                "_governed_execution_context": {
                    "graph_id": "graph-governed-skill-model"
                },
                "model_attempt_service": object(),
            },
        )


def test_emergent_growth_uses_bound_runtime_and_emits_canonical_trace() -> None:
    composition, requests = _composition(
        usage={
            "prompt_tokens": 4,
            "completion_tokens": 6,
            "total_tokens": 10,
            "model_cost_micros": 11,
        }
    )
    runtime = GovernedSkillModelBudgetRuntime(
        attempt_service=composition.attempt_service,
        model_budget_guard=_guard(),
        task_request_id="request-governed-skill-model",
    )

    class Prefrontal:
        def run_emergent_growth_weekly(self, **kwargs):  # noqa: ANN003, ANN201
            return {
                "status": "OK",
                "pattern_result": kwargs["llm_fn"]("inspect"),
                "growth_result": {"proposal_only": True},
            }

    result = execute_emergent_growth_weekly(
        args={"user_id": "natsume_ai"},
        context={
            "request_id": "request-governed-skill-model",
            "_governed_execution_context": {
                "graph_id": "graph-governed-skill-model"
            },
            "_governed_skill_model_budget_runtime": runtime,
            "model_attempt_service": composition.attempt_service,
            "prefrontal_agent": Prefrontal(),
            "episodic_store": object(),
        },
    )

    assert result["status"] == "OK"
    assert result["model_attempt"]["budget_measurement"][
        "token_count"
    ] == 10
    assert requests[0].model_budget_guard == _guard()
    assert runtime.attestation() is not None


def test_governed_skill_execution_wires_attestation_into_worker_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from host_brain.executive_runtime import skill_execution

    composition, requests = _composition(
        usage={
            "prompt_tokens": 3,
            "completion_tokens": 4,
            "total_tokens": 7,
            "model_cost_micros": 9,
        }
    )

    def handler(_args, context):  # noqa: ANN001, ANN202
        assert "model_attempt_service" not in context
        model_runtime = context[
            "_governed_skill_model_budget_runtime"
        ]
        result = model_runtime.complete_sync(
            prompt_id=EMERGENT_GROWTH_PATTERN_PROMPT_ID,
            messages=({"role": "user", "content": "execute"},),
            scene_type="EMERGENT_GROWTH",
            cognitive_tier="B",
            deadline_ms=5_000,
        )
        return {"status": "OK", "content": result.content}

    registry = type(
        "Registry",
        (),
        {
            "require": lambda _self, _skill_id: SkillMeta(
                skill_name="governed_model_skill",
                clearance="LOW",
                risk_level=1,
                scope="host",
                reversible=False,
                handler=handler,
                model_budget_required=True,
            )
        },
    )()
    monkeypatch.setattr(
        skill_execution,
        "_governed_node_grant_error",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        skill_execution,
        "bind_validated_skill_authority",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        skill_execution,
        "dispatch_executor_payload",
        lambda payload, **_kwargs: dict(payload["result"]),
    )
    monkeypatch.setattr(
        skill_execution,
        "_log_skill_exec_row",
        lambda **_kwargs: None,
    )

    outcome = skill_execution._execute_skill(
        ports=object(),
        request_body={"args": {}},
        request_id="request-governed-skill-model",
        session_id="session-governed-skill-model",
        person_id="person-governed-skill-model",
        intent_id="intent-governed-skill-model",
        skill_id="governed_model_skill",
        task_text="execute",
        user_message="execute",
        governor_result={"decision": "APPROVE", "tokens": {}},
        intent_draft={"route": "skill", "target_id": "governed_model_skill"},
        emotion_context={},
        render_target_lang="zh",
        skill_registry=registry,
        ledger=False,
        durable_authority_ledger=object(),
        capability_broker_override=object(),
        model_attempt_service_override=composition.attempt_service,
        governed_execution_context={
            "execution_mode": "governed_task_runtime",
            "request_id": "request-governed-skill-model",
            "target_id": "governed_model_skill",
            "graph_id": "graph-governed-skill-model",
            "graph_revision": 3,
            "node_id": "node-governed-skill-model",
            "attempt_id": "attempt-governed-skill-model",
            "budget": {
                "budget_id": "budget://governed-skill-model",
                "status": "ACTIVE",
                "remaining": {
                    "token_count": 100,
                    "time_ms": 5_000,
                    "retry_count": 1,
                    "model_cost_micros": 1_000,
                },
            },
        },
        execution_args_override={},
        verified_node_grant={"grant_ref": "grant://governed-model-skill"},
    )

    assert outcome.success is True
    assert len(requests) == 1
    attestation = outcome.payload[
        "governed_skill_model_budget_attestation"
    ]
    assert attestation["graph_id"] == "graph-governed-skill-model"
    observations = _trusted_skill_budget_observations(
        outcome.payload,
        execution_context=_execution_context(),
    )
    usage = build_worker_budget_usage(
        usage_id="worker-budget://governed-skill-model-execution",
        observations=observations,
    )
    assert usage is not None
    assert usage.measurements == {
        "retry_count": 0,
        "token_count": 7,
        "model_cost_micros": 9,
    }
