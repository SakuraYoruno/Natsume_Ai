from __future__ import annotations

import json
from types import SimpleNamespace

from host_brain.composition import process_services as dependencies
from host_brain.bond.bond_narrative import BondNarrativeLayer
from host_brain.model_runtime import (
    BOND_NARRATIVE_REWRITE_PROMPT_ID,
    CallableProviderAdapter,
    InMemoryAttemptAuditRepository,
    ProviderTarget,
    RouteDecision,
    StaticRoutePolicy,
    build_live_production_prompt_registry,
    build_model_runtime_composition,
)


def test_bond_narrative_rewrites_selected_items_with_model_attempt_service(monkeypatch) -> None:
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

    captured = {}

    async def _complete(attempt):  # noqa: ANN001
        captured["attempt"] = attempt
        return {
            "content": json.dumps(
                {"items": [{"index": 0, "text": "那晚和你聊到很深的时候，我一直记着那份安心。"}]},
                ensure_ascii=False,
            )
        }

    route = RouteDecision(
        decision_id="bond-route",
        targets=(ProviderTarget("test.bond", "mock", "bond-model", timeout_ms=2_500),),
        reason="TEST_BOND_ROUTE",
        max_attempts=2,
    )
    composition = build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=StaticRoutePolicy(route),
        provider_adapters=[CallableProviderAdapter("test.bond", complete_fn=_complete)],
        audit_repository=InMemoryAttemptAuditRepository(),
    )
    dependencies.reset_model_runtime_composition(composition)
    try:
        layer = BondNarrativeLayer(
            memory_index=FakeMemoryIndex(),
            use_llm=True,
            model_attempt_service=dependencies.get_model_attempt_service(),
        )
        result = layer.build_narrative(user_id="user_1", bond_stage="FRIEND", now_ts_ms=10_000)
    finally:
        dependencies.reset_model_runtime_composition()

    assert result.items
    assert result.items[0].text == "那晚和你聊到很深的时候，我一直记着那份安心。"
    slot = result.to_prompt_slot()
    assert slot["shared_memories"][0] == "那晚和你聊到很深的时候，我一直记着那份安心。"
    assert captured["attempt"].prompt.prompt_id == BOND_NARRATIVE_REWRITE_PROMPT_ID
    assert result.model_attempt_trace["route_decision_id"] == "bond-route"
    assert result.model_attempt_trace["attempt_ids"]
