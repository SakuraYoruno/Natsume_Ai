from __future__ import annotations

import json

from host_brain.app import prompt_builder as pb
from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_render_node_generation_prompt_uses_emotion_binding_template_override(
    monkeypatch,
) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "prompt_builder.emotion_binding.texts",
        content=json.dumps(
            {
                "echo_intensity": {
                    "mid": "custom-echo-mid",
                },
                "vad": {
                    "valence_positive": "custom-vad-positive",
                    "arousal_high": "custom-vad-arousal",
                    "dominance_high": "custom-vad-dominance",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    prompt = pb.render_node_generation_prompt(
        node_id="cortex_orchestrator",
        runtime_payload={
            "user_message": "继续说",
            "render_target_lang": "zh",
            "prompt_context": {
                "payload": {
                    "authoritative": [],
                    "retrieval": [],
                    "style_pack": {},
                    "constraints": {},
                    "emotion_pulse": {},
                },
                "validation": {"valid": True, "reason_code": "NONE", "violation_count": 0},
                "observability": {},
            },
            "retrieval": {"status": "OK", "reason_code": "NONE", "evidence_items": []},
            "emotion_context": {
                "mood_state": "steady",
                "emotion_tag": "HAPPY",
                "emotion_intensity": 0.7,
                "emotion_range_hint": "warm",
            },
            "emotion_vector": {
                "valence": 0.8,
                "arousal": 0.7,
                "dominance": 0.6,
            },
            "emotion_timeline": {
                "dominant_emotion": "HAPPY",
                "trend": "stable",
                "entry_count": 3,
                "mode": "LIVE",
            },
            "emotion_echo": {
                "echo_emotion_tag": "WARM",
                "response_hint": "stay close",
                "echo_intensity": 0.45,
                "bond_depth": "deep",
            },
        },
    )

    assert "custom-echo-mid" in prompt
    assert "custom-vad-positive" in prompt
    assert "custom-vad-arousal" in prompt
    assert "custom-vad-dominance" in prompt
