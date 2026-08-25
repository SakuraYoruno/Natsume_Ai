from __future__ import annotations

import pytest

from host_brain.model_runtime.prompt_specs import list_prompt_specs, resolve_prompt_spec_json


def test_prompt_specs_expose_versioned_agent_prompt_catalog() -> None:
    spec_ids = {spec.spec_id for spec in list_prompt_specs()}
    required_agent_specs = {
        "surface_reply_handler.runtime_texts",
        "repair_agent_handler.runtime_texts",
        "circadian_agent.circadian_mood_hint",
        "cerebellum_agent.correction_narrative",
        "prefrontal_agent.plan_response.prompt",
    }

    assert "multimodal_observer.model_prompt.templates" in spec_ids
    assert "immune_agent.semantic_intent_analysis" in spec_ids
    assert required_agent_specs.issubset(spec_ids)
    assert all(spec.version.startswith("v") for spec in list_prompt_specs())

    multimodal = resolve_prompt_spec_json("multimodal_observer.model_prompt.templates")
    assert "JSON" in multimodal["image_system_prompt"]
    assert "不能角色扮演" in multimodal["image_system_prompt"]

    immune = resolve_prompt_spec_json("immune_agent.semantic_intent_analysis")
    assert "intent_class" in immune["system_prompt"]

    with pytest.raises(KeyError):
        resolve_prompt_spec_json("surface_reply.minimal_fallback.default")
