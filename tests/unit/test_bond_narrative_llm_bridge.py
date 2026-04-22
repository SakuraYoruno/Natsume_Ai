from __future__ import annotations

import json
from types import SimpleNamespace

from host_brain.bond.bond_narrative import BondNarrativeLayer
from host_brain.integrations.llm_gateway_schemas import ModelAttempt, PoolCompleteResult


def _pool_result(content: str) -> PoolCompleteResult:
    return PoolCompleteResult(
        content=content,
        final_model="test-model",
        final_provider="test-provider",
        attempts=[ModelAttempt(model="test-model", provider="test-provider", status="OK", latency_ms=8)],
        pool_exhausted=False,
        total_latency_ms=8,
        cached=False,
        cognitive_tier="B",
    )


def test_bond_narrative_rewrites_selected_items_with_pool(monkeypatch) -> None:
    class FakeMemoryIndex:
        def query(self, max_entries: int = 3):
            return SimpleNamespace(
                total_candidates=1,
                entries=[
                    SimpleNamespace(
                        text="那天一起聊到很晚的时候，感觉很安心",
                        emotion_tag="WARM",
                        relevance_score=0.9,
                        ts_ms=1_000,
                    )
                ],
            )

    monkeypatch.setattr(
        "host_brain.bond.bond_narrative._load_bond_narrative_llm_templates",
        lambda: {
            "system_prompt": "rewrite",
            "user_prompt_template": "items={items_json}",
        },
    )

    import host_brain.integrations.llm_helpers as helpers

    monkeypatch.setattr(
        helpers,
        "complete_llm_with_pool_sync",
        lambda **kwargs: _pool_result(
            json.dumps({"items": [{"index": 0, "text": "那晚和你聊到很深的时候，我一直记着那份安心。"}]}, ensure_ascii=False)
        ),
    )

    layer = BondNarrativeLayer(memory_index=FakeMemoryIndex(), use_llm=True)
    result = layer.build_narrative(user_id="user_1", bond_stage="FRIEND", now_ts_ms=10_000)

    assert result.items
    assert result.items[0].text == "那晚和你聊到很深的时候，我一直记着那份安心。"
    slot = result.to_prompt_slot()
    assert slot["shared_memories"][0] == "那晚和你聊到很深的时候，我一直记着那份安心。"
