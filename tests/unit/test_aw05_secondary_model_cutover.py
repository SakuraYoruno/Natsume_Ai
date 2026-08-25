from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.model_runtime import (
    EMOTION_REACTIVITY_PROMPT_ID,
    INITIATIVE_TOPIC_PROMPT_ID,
    MIRROR_EMPATHY_PROMPT_ID,
    PREFRONTAL_PLAN_PROMPT_ID,
    PREFRONTAL_SELF_REFLECTION_PROMPT_ID,
    InMemoryAttemptAuditRepository,
    ModelAttemptService,
    ProviderResponse,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
)
from host_brain.model_runtime.provider_adapters import ProviderAdapterRegistry
from host_brain.persona.emotion_reactivity import ReactivityModifiers, render_reactivity_hint
from host_brain.tribe.initiative_governor_agent import InitiativeGovernorAgent
from host_brain.tribe.mirror_neuron_agent import MirrorNeuronAgent
from host_brain.tribe.prefrontal_agent import PrefrontalAgent
from host_brain.tribe.application_ports import TribeApplicationPorts


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.usefixtures("tribe_runtime_context")


class _ScriptedAdapter:
    adapter_id = "test.secondary"

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests = []

    async def complete(self, request):  # noqa: ANN001, ANN202
        self.requests.append(request)
        return ProviderResponse(content=self.responses.pop(0))

    async def stream(self, request):  # noqa: ANN001
        response = await self.complete(request)
        yield response.content


def _install(responses: list[str]) -> _ScriptedAdapter:
    registry = build_live_production_prompt_registry()
    adapter = _ScriptedAdapter(responses)
    route = RouteDecision(
        decision_id="route-secondary-test",
        targets=(
            ProviderTarget(
                adapter_id=adapter.adapter_id,
                provider="test",
                model_id="secondary-model",
                timeout_ms=5_000,
            ),
        ),
        reason="unit_test",
        max_attempts=2,
    )
    service = ModelAttemptService(
        prompt_registry=registry,
        route_policy=StaticRoutePolicy(route),
        provider_adapters=ProviderAdapterRegistry((adapter,)),
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    adapter.service = service
    adapter.prompt_registry = registry
    adapter.application_ports = TribeApplicationPorts(
        model_attempt_service=lambda: service,
        prompt_registry=lambda: registry,
    )
    return adapter


def test_prefrontal_self_reflection_uses_registered_text_prompt(monkeypatch) -> None:
    adapter = _install(["我正在重新理解最近的平静。"])
    monkeypatch.setenv("HOST_BACKGROUND_TASKS_ENABLED", "true")

    class _Snapshot:
        def as_dict(self) -> dict:
            return {"recent_mood_trend": "steady"}

    monkeypatch.setattr(
        "host_brain.persona.persona_engine.PersonaEngine.get_instance",
        classmethod(lambda cls: SimpleNamespace(get_self_model_snapshot=lambda *_a, **_k: _Snapshot())),
    )

    def _pulse(_snapshot, *, llm_fn=None, **_kwargs):  # noqa: ANN001, ANN003
        return {"self_awareness": llm_fn("写一句真实自省")}

    monkeypatch.setattr("host_brain.persona.self_reflection.build_self_reflection_pulse", _pulse)

    result = PrefrontalAgent(
        application_ports=adapter.application_ports,
    ).resolve_self_reflection_context(
        persona_user_id="user-aw05",
        timeline_summary={},
        timeline_diary={},
        bond_state_obj=SimpleNamespace(last_event_ts_ms=1),
        bond_summary={},
        now_ms=123,
    )

    assert result["self_reflection_pulse"]["self_awareness"] == "我正在重新理解最近的平静。"
    assert result["model_attempts"][0]["route_decision_id"] == "route-secondary-test"
    assert adapter.requests[0].prompt.prompt_id == PREFRONTAL_SELF_REFLECTION_PROMPT_ID
    assert adapter.requests[0].scene_type == "SELF_REFLECTION"


@pytest.mark.asyncio
async def test_prefrontal_plan_uses_registered_json_prompt(monkeypatch) -> None:
    adapter = _install(
        [json.dumps({"action": "speak", "task": "回应", "tone_hint": "warm", "urgency": 0.4})],
    )
    monkeypatch.setattr(
        "host_brain.model_runtime.prompt_contract.resolve_prompt_contract_json",
        lambda _key: {
            "system_prompt": "只输出 JSON",
            "user_prompt_template": (
                "{user_message} {emotion_tag} {valence} {arousal} {bond_stage} {bond_intimacy} "
                "{motive_care} {motive_continuity} {motive_withdraw} {recall_summary} "
                "{immune_risk} {drift_alert}"
            ),
        },
    )

    result = await PrefrontalAgent(
        application_ports=adapter.application_ports,
    ).plan_response(
        user_message="你好",
        motive_state={"care": 0.5, "continuity": 0.5, "withdraw": 0.2},
        request_id="req-prefrontal-aw05",
        timeout_ms=2_000,
    )

    assert result["source"] == "prefrontal_llm"
    assert result["model_attempt"]["route_decision_id"] == "route-secondary-test"
    assert adapter.requests[0].prompt.prompt_id == PREFRONTAL_PLAN_PROMPT_ID


def test_mirror_and_initiative_use_independent_registered_prompts(monkeypatch) -> None:
    adapter = _install(
        [json.dumps({"empathy_hint": "先接住她的难过"}), json.dumps({"topic_seed": "聊聊今天的小发现"})],
    )

    def _templates(key: str):
        if "empathy" in key:
            return {
                "system_prompt": "json",
                "user_prompt_template": "{resonance_intensity} {user_emotion_tag} {bond_stage}",
            }
        return {
            "system_prompt": "json",
            "user_prompt_template": (
                "{loneliness_level} {anticipation_level} {emotion_tag} {bond_stage} {recent_topics}"
            ),
        }

    monkeypatch.setattr("host_brain.model_runtime.prompt_contract.resolve_prompt_contract_json", _templates)
    mirror = MirrorNeuronAgent(application_ports=adapter.application_ports)
    initiative = InitiativeGovernorAgent(application_ports=adapter.application_ports)

    assert mirror.generate_empathy_response_hint(resonance_intensity=0.8) == "先接住她的难过"
    assert initiative.generate_proactive_topic(loneliness_level=0.7) == "聊聊今天的小发现"
    assert [request.prompt.prompt_id for request in adapter.requests] == [
        MIRROR_EMPATHY_PROMPT_ID,
        INITIATIVE_TOPIC_PROMPT_ID,
    ]
    mirror_state = mirror.state_registry.get_state("mirror_neuron_agent") or {}
    initiative_state = initiative.state_registry.get_state("initiative_governor_agent") or {}
    assert (mirror_state.get("state") or {})["last_empathy_model_attempt"]["route_decision_id"]
    assert (initiative_state.get("state") or {})["last_proactive_topic_model_attempt"]["route_decision_id"]


def test_emotion_reactivity_exposes_canonical_trace(monkeypatch) -> None:
    adapter = _install([json.dumps({"reactivity_hint": "先放慢语气"})])
    monkeypatch.setattr(
        "host_brain.persona.emotion_reactivity._load_reactivity_llm_templates",
        lambda: {"system_prompt": "json", "user_prompt": {}},
    )
    trace: dict = {}

    hint = render_reactivity_hint(
        user_emotion_tag="SAD",
        bond_stage="FRIEND",
        modifiers=ReactivityModifiers(),
        model_attempt_sink=trace,
        model_attempt_service=adapter.service,
    )

    assert hint == "先放慢语气"
    assert trace["route_decision_id"] == "route-secondary-test"
    assert adapter.requests[0].prompt.prompt_id == EMOTION_REACTIVITY_PROMPT_ID


def test_secondary_production_sources_have_no_private_model_authority() -> None:
    sources = (
        "host_brain/tribe/prefrontal_agent.py",
        "host_brain/tribe/mirror_neuron_agent.py",
        "host_brain/tribe/initiative_governor_agent.py",
        "host_brain/tribe/hippocampus_agent.py",
        "host_brain/persona/emotion_reactivity.py",
        "host_brain/persona/emotional_silence.py",
        "host_brain/persona/inner_activity_agent.py",
    )
    forbidden = (
        "integrations.llm_helpers",
        "complete_llm_with_pool",
        "acomplete_llm_with_pool",
        "build_pool_prompt_llm_fn",
        "provider_name=",
        "model_id=",
        "api_key=",
        "gen_params=",
        "pool_exhausted",
    )
    for relative in sources:
        text = (ROOT / relative).read_text(encoding="utf-8-sig")
        assert all(token not in text for token in forbidden), relative

    inner = (ROOT / "host_brain/persona/inner_activity_agent.py").read_text(encoding="utf-8-sig")
    for retired in (
        "_run_inner_activity_codex_cli",
        "_build_inner_activity_codex_cli_command",
        "_INNER_ACTIVITY_RESULT_CACHE",
        "_reserve_inner_activity_direct_call_slot",
        "_log_inner_activity_direct_usage",
        "_resolve_inner_activity_llm_runtime",
        "_retry_inner_activity_llm_after_duplicate",
    ):
        assert retired not in inner
