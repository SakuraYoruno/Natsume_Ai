from __future__ import annotations

import json

from host_brain.app.prompt_template_store import PromptTemplateStore
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.persona.body_sense import BodySenseModel
from host_brain.persona.emotional_silence import EmotionalSilenceEngine


def _install_store(monkeypatch, template_id: str, content: dict) -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = PromptTemplateStore(ledger=ledger, cache_ttl_ms=0)
    store.upsert_template(
        template_id,
        content=json.dumps(content, ensure_ascii=False),
        enabled=True,
    )

    import host_brain.app.dependencies as deps

    monkeypatch.setattr(deps, "_ledger", ledger, raising=False)
    monkeypatch.setattr(deps, "_prompt_template_store", store, raising=False)


def test_body_sense_style_hints_use_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "body_sense.style_hints",
        {
            "drowsiness": {
                "DROWSY": "custom-drowsy",
            },
            "fatigue": {
                "WEARY": "custom-weary",
            },
            "loneliness": {
                "LONGING": "custom-longing",
            },
        },
    )

    model = BodySenseModel()
    model._drowsiness = 0.7
    model._fatigue = 0.7
    model._loneliness = 0.7

    state = model.get_state()

    assert "custom-drowsy" in state.body_sense_hint
    assert "custom-weary" in state.body_sense_hint
    assert "custom-longing" in state.body_sense_hint


def test_emotional_silence_runtime_hints_use_template_override(monkeypatch) -> None:
    _install_store(
        monkeypatch,
        "emotional_silence.runtime_hints",
        {
            "silence_hints": {
                "SILENT": "custom-silent",
            },
            "topic_resistance_template": "custom-topic:{topic}",
        },
    )

    engine = EmotionalSilenceEngine()
    engine._response_willingness = 0.4
    engine.add_topic_resistance("工作", strength=0.5)

    state = engine.get_silence_state(current_topic="工作安排")

    assert state.silence_hint == "custom-silent"
    assert state.topic_resistance_hint == "custom-topic:工作"
