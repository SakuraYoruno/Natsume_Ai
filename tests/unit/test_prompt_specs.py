from __future__ import annotations

import pytest

from host_brain.app.prompt_specs import list_prompt_specs, resolve_prompt_spec_json


def test_prompt_specs_only_expose_structural_json_prompts() -> None:
    spec_ids = {spec.spec_id for spec in list_prompt_specs()}

    assert "multimodal_observer.model_prompt.templates" in spec_ids
    assert "thalamus_agent.intent_classification_prompts" in spec_ids
    assert "immune_agent.semantic_intent_analysis" in spec_ids

    multimodal = resolve_prompt_spec_json("multimodal_observer.model_prompt.templates")
    assert "JSON" in multimodal["image_system_prompt"]
    assert "不能角色扮演" in multimodal["image_system_prompt"]

    immune = resolve_prompt_spec_json("immune_agent.semantic_intent_analysis")
    assert "intent_class" in immune["system_prompt"]

    with pytest.raises(KeyError):
        resolve_prompt_spec_json("chat_agent.minimal_fallback.default")
