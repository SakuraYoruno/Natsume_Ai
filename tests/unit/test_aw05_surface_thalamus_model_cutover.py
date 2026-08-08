from __future__ import annotations

from pathlib import Path

from host_brain.model_runtime import (
    AGENT_REACT_STEP_PROMPT_ID,
    AGENT_SUBAGENT_GATEWAY_PROMPT_ID,
    PERSONA_INNER_ACTIVITY_PROMPT_ID,
    RESEARCH_EXECUTION_PROMPT_ID,
    RESEARCH_QUALITY_VERIFIER_PROMPT_ID,
    SURFACE_MAIN_JSON_PROMPT_ID,
    SURFACE_MAIN_TEXT_PROMPT_ID,
    SURFACE_MEMORY_REPAIR_PROMPT_ID,
    VISION_ACTION_PLANNER_PROMPT_ID,
    CallableRoutePolicy,
    build_default_production_model_runtime,
)


class _Ledger:
    def log_model_attempt(self, _row) -> None:  # noqa: ANN001
        return None


def test_default_composition_installs_live_prompts_dynamic_route_and_one_attempt_adapters() -> None:
    composition = build_default_production_model_runtime(
        ledger=_Ledger(),
        credential_resolver=lambda _provider: "test-key",
        base_url_resolver=lambda _provider: None,
        transport=lambda **_kwargs: "unused",
    )

    expected = {
        AGENT_REACT_STEP_PROMPT_ID: "json_object",
        AGENT_SUBAGENT_GATEWAY_PROMPT_ID: "text",
        PERSONA_INNER_ACTIVITY_PROMPT_ID: "json_object",
        RESEARCH_EXECUTION_PROMPT_ID: "json_object",
        RESEARCH_QUALITY_VERIFIER_PROMPT_ID: "json_object",
        SURFACE_MAIN_JSON_PROMPT_ID: "json_object",
        SURFACE_MAIN_TEXT_PROMPT_ID: "text",
        SURFACE_MEMORY_REPAIR_PROMPT_ID: "json_object",
        VISION_ACTION_PLANNER_PROMPT_ID: "json_object",
    }
    assert {
        prompt_id: composition.prompt_registry.definition(prompt_id).output_contract.kind.value
        for prompt_id in expected
    } == expected
    assert isinstance(composition.route_policy, CallableRoutePolicy)
    assert composition.provider_adapters.ids() == (
        "production.codex",
        "production.deepseek",
        "production.gemini",
        "production.minimax",
        "production.openai",
        "production.openai_compatible",
    )


def test_surface_and_thalamus_have_no_legacy_pool_or_private_fallback_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    surface = (root / "host_brain/app/surface_main_chain_adapter.py").read_text(
        encoding="utf-8"
    )
    thalamus = (root / "host_brain/tribe/thalamus_agent.py").read_text(
        encoding="utf-8"
    )

    for source in (surface, thalamus):
        assert "acomplete_llm_with_pool" not in source
        assert "complete_llm_with_pool" not in source
    assert "_surface_gemini_fallback_runtime" not in surface
    assert 'trace_stage_meta["surface_llm_pool"]' not in surface
    assert "ports.model_attempt_service()" in surface
    assert "get_model_attempt_service" not in surface
    assert "host_brain.composition.process_services" not in surface
    assert "model_attempt_service" not in thalamus
    assert "complete_tribe" not in thalamus
    assert "must not call a model" in thalamus
    assert "application_ports.require(" in thalamus
    assert "host_brain.app" not in thalamus
