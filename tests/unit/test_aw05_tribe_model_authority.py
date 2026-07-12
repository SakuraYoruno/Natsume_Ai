from __future__ import annotations

from pathlib import Path

import pytest

from host_brain.app import dependencies
from host_brain.model_runtime import (
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    TRIBE_PROMPT_IDS,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)
from host_brain.tribe.cerebellum_agent import CerebellumAgent
from host_brain.tribe.circadian_agent import CircadianAgent
from host_brain.tribe.immune_agent import ImmuneAgent
from host_brain.tribe.language_system_agent import LanguageSystemAgent
from host_brain.tribe.model_invocation import complete_tribe_json, tribe_model_semantic
from host_brain.tribe.motor_cortex_agent import MotorCortexAgent


TRIBE_MODEL_FILES = (
    "circadian_agent.py",
    "cerebellum_agent.py",
    "endocrine_agent.py",
    "glymphatic_agent.py",
    "immune_agent.py",
    "interoception_agent.py",
    "language_system_agent.py",
    "motor_cortex_agent.py",
    "pain_reward_agent.py",
    "peripheral_agent.py",
    "proprioception_agent.py",
    "somatic_agent.py",
    "vestibular_agent.py",
)


class _CapturingRoute:
    def __init__(self) -> None:
        self.requests = []
        self.prompts = []

    async def resolve(self, request, prompt):  # noqa: ANN001, ANN201
        self.requests.append(request)
        self.prompts.append(prompt)
        return RouteDecision(
            "route-tribe-unit",
            (ProviderTarget("tribe-test", "mock", "tribe-model"),),
            "unit-test",
            max_attempts=1,
        )


def test_every_tribe_semantic_has_unique_registered_json_prompt() -> None:
    assert len(TRIBE_PROMPT_IDS) == 15
    assert len(TRIBE_PROMPT_IDS) == len(set(TRIBE_PROMPT_IDS.values()))
    registry = build_live_production_prompt_registry()

    for semantic_id, prompt_id in TRIBE_PROMPT_IDS.items():
        rendered = registry.render(
            prompt_id,
            {"messages": [{"role": "user", "content": semantic_id}]},
        )
        assert rendered.output_contract.kind.value == "json_object"
        assert tribe_model_semantic(semantic_id).prompt_id == prompt_id


def test_tribe_helper_uses_process_composition_and_central_json_parser() -> None:
    route = _CapturingRoute()
    attempts = []

    def _complete(attempt):  # noqa: ANN001, ANN202
        attempts.append(attempt)
        return '{"hint":"ok"}'

    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=route,
        provider_adapters=[CallableProviderAdapter("tribe-test", complete_fn=_complete)],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    dependencies.reset_model_runtime_composition(composition)
    try:
        parsed = complete_tribe_json(
            semantic_id="circadian_mood_hint",
            system_prompt="system",
            user_prompt="user",
            request_id="req-tribe-helper",
        )
    finally:
        dependencies.reset_model_runtime_composition()

    assert parsed == {"hint": "ok"}
    assert route.requests[0].scene_type == "TRIBE_INTERNAL"
    assert route.requests[0].cognitive_tier == "C"
    assert route.prompts[0].prompt_id == TRIBE_PROMPT_IDS["circadian_mood_hint"]
    assert attempts[0].prompt.prompt_id == TRIBE_PROMPT_IDS["circadian_mood_hint"]


def test_representative_tribe_business_returns_are_preserved(monkeypatch) -> None:  # noqa: ANN001
    outputs = {
        "circadian_mood_hint": {"circadian_hint": "夜间节律"},
        "cerebellum_correction_narrative": {"correction_narrative": "正在调整"},
        "cerebellum_failure_pattern": {"failure_insight": "检查重复失败"},
        "language_wernicke_deep_parse": {
            "topic_entity": "主题",
            "emotional_subtext": "潜台词",
            "implicit_need": "需求",
        },
        "language_broca_opening_hint": {"opening_hint": "先慢慢说"},
        "immune_semantic_intent": {
            "intent_class": "SUSPICIOUS",
            "confidence": 0.75,
            "reason": "灰区",
        },
        "motor_speak_style": {
            "speed": 1.1,
            "pause_hint": "短",
            "filler_hint": "嗯",
        },
    }
    monkeypatch.setattr(
        "host_brain.app.prompt_template_store.resolve_prompt_template_json",
        lambda _prompt_id: {"system_prompt": "system", "user_prompt_template": "user"},
    )
    monkeypatch.setattr(
        "host_brain.tribe.model_invocation.complete_tribe_json",
        lambda *, semantic_id, **_: dict(outputs[semantic_id]),
    )

    assert CircadianAgent().generate_circadian_mood_hint(
        energy_level=0.2,
        sleep_pressure=0.7,
    ) == "夜间节律"
    cerebellum = CerebellumAgent()
    assert cerebellum.generate_correction_narrative(strategy="RETRY_ONCE") == "正在调整"
    assert cerebellum.analyze_failure_pattern(consecutive_failures=3) == "检查重复失败"
    language = LanguageSystemAgent()
    parsed = language.wernicke_deep_parse(user_text="复杂输入", intent_type="complex")
    assert parsed["source"] == "wernicke_llm"
    assert parsed["implicit_need"] == "需求"
    assert language.broca_generate_opening_hint(emotional_pressure=0.8) == "先慢慢说"
    immune = ImmuneAgent().semantic_intent_analysis(text="灰区", rule_score=0.4)
    assert immune["intent_class"] == "SUSPICIOUS"
    assert immune["source"] == "semantic_llm"
    style = MotorCortexAgent().plan_speak_style(arousal=0.8)
    assert style == {
        "speed": 1.1,
        "pause_hint": "短",
        "filler_hint": "嗯",
        "source": "motor_llm",
    }


def test_tribe_sources_have_no_private_model_or_regex_json_authority() -> None:
    tribe_dir = Path(__file__).resolve().parents[2] / "host_brain" / "tribe"
    combined = "\n".join(
        (tribe_dir / filename).read_text(encoding="utf-8")
        for filename in TRIBE_MODEL_FILES
    )
    for forbidden in (
        "build_llm_provider",
        "provider_factory",
        ".complete_sync(",
        "max_tokens=",
        "temperature=",
        "timeout_ms=",
        "_re.search",
        "_json.loads",
    ):
        assert forbidden not in combined
    for semantic_id in TRIBE_PROMPT_IDS:
        assert combined.count(f'semantic_id="{semantic_id}"') == 1


def test_unknown_tribe_semantic_fails_closed() -> None:
    with pytest.raises(KeyError, match="TRIBE_MODEL_SEMANTIC_UNKNOWN"):
        tribe_model_semantic("unknown")
