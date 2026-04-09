from __future__ import annotations

from host_brain.app import chat_context_builder as ccb
from host_brain.core import prompt_context as pc


def test_chat_context_builder_uses_hippocampus_projection(monkeypatch) -> None:
    class _Hippocampus:
        def build_prompt_retrieval_rows(self, **kwargs):
            assert kwargs["retrieval_summary"]["status"] == "OK"
            assert isinstance(kwargs["retrieval_evidence_items"], list)
            return [
                {
                    "slot": "retrieval",
                    "kind": "episodic",
                    "content": "来自 hippocampus 接口",
                    "score": 0.9,
                    "confidence": 0.9,
                    "store_id": "episodic_vector",
                    "source_ref": "episodic://from_hippo",
                    "evidence_ref": "ev://1",
                }
            ]

    monkeypatch.setattr(
        "host_brain.tribe.hippocampus_agent.get_hippocampus_agent",
        lambda: _Hippocampus(),
    )
    monkeypatch.setattr(
        "host_brain.app.emotion_context.normalize_chat_emotion_context",
        lambda _raw: {
            "mood_state": "steady",
            "emotion_tag": "NEUTRAL",
            "emotion_range_hint": "",
            "allowed_emotion_tags": ["NEUTRAL"],
        },
    )
    monkeypatch.setattr("host_brain.app.dependencies.get_bond_stage_engine", lambda: None)
    monkeypatch.setattr(ccb, "validate_context_pack", lambda _pack: pc.PromptContextValidationResult())

    prompt_context, _ = ccb.build_chat_context_pack(
        request_id="req-ctx-hippo",
        session_id="sess-ctx-hippo",
        thread_id=None,
        retrieval_summary={"status": "OK", "reason_code": "NONE"},
        retrieval_evidence_items=[{"snippet": "will be replaced by hippocampus"}],
        style_bindings=None,
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
        cpb_slots={
            "style_canon": "STYLE_SLOT",
            "world_canon": "WORLD_SLOT",
            "emotion_recall": "EMOTION_SLOT",
        },
    )

    retrieval_rows = list(prompt_context.get("retrieval") or [])
    assert len(retrieval_rows) == 1
    assert retrieval_rows[0]["content"] == "来自 hippocampus 接口"
