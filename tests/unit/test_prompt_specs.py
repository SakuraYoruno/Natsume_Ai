from __future__ import annotations

import pytest

from host_brain.model_runtime.prompt_specs import get_prompt_spec, list_prompt_specs, resolve_prompt_spec_json


def test_prompt_specs_expose_versioned_agent_prompt_catalog() -> None:
    spec_ids = {spec.spec_id for spec in list_prompt_specs()}
    required_agent_specs = {
        "react_loop.prompt_templates",
        "surface_reply_handler.runtime_texts",
        "intent_agent_handler.system_prompt_extra",
        "translate_agent_handler.runtime_texts",
        "retrieval_agent_handler.runtime_texts",
        "judge_agent_handler.prompt_templates",
        "emotion_agent_handler.prompt_templates",
        "pentest_agent_handler.system_prompt_extra",
        "repair_agent_handler.runtime_texts",
        "supervisor_dispatch_handler.prompt_templates",
        "sub_agent_protocol.agent_capabilities",
        "circadian_agent.circadian_mood_hint",
        "cerebellum_agent.correction_narrative",
        "hippocampus.recall_association.prompt",
        "prefrontal_agent.plan_response.prompt",
        "vision.action_planner.prompt_templates",
    }

    assert "multimodal_observer.model_prompt.templates" in spec_ids
    assert "thalamus_agent.intent_classification_prompts" in spec_ids
    assert "immune_agent.semantic_intent_analysis" in spec_ids
    assert required_agent_specs.issubset(spec_ids)
    assert all(spec.version.startswith("v") for spec in list_prompt_specs())

    multimodal = resolve_prompt_spec_json("multimodal_observer.model_prompt.templates")
    assert "JSON" in multimodal["image_system_prompt"]
    assert "不能角色扮演" in multimodal["image_system_prompt"]

    immune = resolve_prompt_spec_json("immune_agent.semantic_intent_analysis")
    assert "intent_class" in immune["system_prompt"]

    pentest = resolve_prompt_spec_json("pentest_agent_handler.system_prompt_extra")
    assert isinstance(pentest, str)
    assert "Pentest Domain Pack" in pentest
    assert "pentest.secure-run" in pentest
    assert "side_effects_executed=false" in pentest

    spec = get_prompt_spec("pentest_agent_handler.system_prompt_extra")
    assert spec.category == "agent_prompt"
    assert spec.scope == "pentest_agent"

    with pytest.raises(KeyError):
        resolve_prompt_spec_json("surface_reply.minimal_fallback.default")
