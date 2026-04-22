from __future__ import annotations

import host_brain.app.chat_generation_payload as cgp
from host_brain.core import prompt_context as pc


def test_prompt_context_uses_hippocampus_projection(monkeypatch) -> None:
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
    monkeypatch.setattr(cgp, "validate_prompt_context", lambda _pack: pc.PromptContextValidationResult())

    prompt_context, _ = cgp.build_prompt_context(
        request_id="req-ctx-hippo",
        session_id="sess-ctx-hippo",
        thread_id=None,
        retrieval_summary={"status": "OK", "reason_code": "NONE"},
        retrieval_evidence_items=[{"snippet": "will be replaced by hippocampus"}],
        style_bindings=None,
        bond_summary={},
        emotion_context={},
        render_target_lang="zh",
    )

    retrieval_rows = list(prompt_context.get("retrieval") or [])
    assert len(retrieval_rows) == 1
    assert retrieval_rows[0]["content"] == "来自 hippocampus 接口"
