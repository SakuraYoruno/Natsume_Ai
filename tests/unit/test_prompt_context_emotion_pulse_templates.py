from __future__ import annotations

import json
from types import SimpleNamespace

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.prompt_context import build_emotion_pulse_slot


def test_build_emotion_pulse_slot_uses_template_override(monkeypatch) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        "prompt_context.emotion_pulse_texts",
        content=json.dumps(
            {
                "style_hint": {
                    "valence_high": "custom-valence-high",
                    "arousal_high": "custom-arousal-high",
                    "dominance_high": "custom-dominance-high",
                },
                "flow_hint": {
                    "improving": "custom-flow-improving",
                },
            },
            ensure_ascii=False,
        ),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)

    result = build_emotion_pulse_slot(
        SimpleNamespace(valence=0.7, arousal=0.8, dominance=0.6),
        emotion_tag="happy",
        mood_state="steady",
        inner_monologue="baseline-thought",
        timeline_summary={"trend": "improving"},
    )

    assert result["style_hint"] == "custom-valence-high，custom-arousal-high，custom-dominance-high"
    assert result["inner_monologue"] == "baseline-thought。custom-flow-improving"
