from __future__ import annotations

from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane
from host_brain.app.context.soul_runtime import get_compiled_soul
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import ConversationHistory, TurnStore


def test_dialogue_state_uses_real_conversation_window_for_repeat_identity(monkeypatch) -> None:
    display_name = get_compiled_soul().display_name
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = TurnStore(ledger)
    history = ConversationHistory(store)

    store.append_generated(
        session_id="sess-repeat",
        person_id="p1",
        request_id="req-1",
        role="user",
        content="你是",
        ts_ms=1000,
        lang="zh",
    )
    store.append_generated(
        session_id="sess-repeat",
        person_id="p1",
        request_id="req-2",
        role="assistant",
        content=f"我是{display_name}。",
        ts_ms=1001,
        lang="zh",
    )

    monkeypatch.setattr(
        "host_brain.app.dependencies._conversation_history",
        history,
        raising=False,
    )

    plane = assemble_prompt_context_plane(
        node_id="cortex_orchestrator",
        runtime_payload={
            "request": {
                "user_message": "你谁",
                "request_id": "req-3",
                "session_id": "sess-repeat",
                "person_id": "p1",
            },
            "render": {"target_lang": "zh"},
            "prompt_context": {"payload": {}},
            "render_policy": {},
        },
    )

    assert plane.continuity.turns_injected == 2
    assert plane.conversation_interpretation.state.literal_topic == "identity"
    assert plane.conversation_interpretation.state.interaction_move == "reconfirm"
    assert plane.conversation_interpretation.state.recent_identity_answered is True
    assert plane.conversation_interpretation.state.repeat_question_count == 1
    assert plane.reply_plan.state["response_goal"] == "identify"
    assert plane.reply_plan.state["tactic_family"] in {
        "ack_name_then_wait",
        "ack_repeat_then_light_probe",
        "hold_name_then_open_floor",
        "name_only_then_pause",
    }
    assert plane.reply_plan.state["reply_contract"]
