from __future__ import annotations

import json

from host_brain.integrations.llm_gateway_schemas import ModelAttempt, PoolCompleteResult
from host_brain.retrieval import style_distiller as sd


def _pool_result(content: str) -> PoolCompleteResult:
    return PoolCompleteResult(
        content=content,
        final_model="test-model",
        final_provider="test-provider",
        attempts=[ModelAttempt(model="test-model", provider="test-provider", status="OK", latency_ms=6)],
        pool_exhausted=False,
        total_latency_ms=6,
        cached=False,
        cognitive_tier="B",
    )


def test_style_distiller_render_instruction_can_use_pool(monkeypatch) -> None:
    monkeypatch.setattr(sd, "render_style_instruction_v5", lambda profile: "base-style-instruction")
    monkeypatch.setattr(
        sd,
        "resolve_prompt_template_json_merged",
        lambda template_id: {
            "system_prompt": "compress",
            "user_prompt_template": "instruction={instruction_text}",
        }
        if template_id == "style_distiller.llm_render.prompt"
        else {},
    )

    import host_brain.integrations.llm_helpers as helpers

    monkeypatch.setattr(
        helpers,
        "complete_llm_with_pool_sync",
        lambda **kwargs: _pool_result(json.dumps({"instruction": "compressed-style-instruction"}, ensure_ascii=False)),
    )

    profile = sd.StyleProfile(total_cn=12, total_ja=6)
    distiller = sd.StyleDistiller(use_llm=True)
    rendered = distiller.render_instruction(profile, lang="cn")

    assert rendered == "compressed-style-instruction"
