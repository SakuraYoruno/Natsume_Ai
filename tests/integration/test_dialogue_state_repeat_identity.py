from __future__ import annotations

from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane
from host_brain.agent_runtime.context_read_ports import ConversationContextReadPorts
from host_brain.persona.context.soul_runtime import get_compiled_soul
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import ConversationHistory
from host_brain.persistence.context_repository import ContextRepository


def test_dialogue_state_uses_real_conversation_window_for_repeat_identity() -> None:
    display_name = get_compiled_soul().display_name
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = ContextRepository.from_ledger(ledger)
    history = ConversationHistory(store)

    store.append_generated_turn(
        {
            "turn_id": "turn::req-1::user",
            "session_id": "sess-repeat",
            "person_id": "p1",
            "request_id": "req-1",
            "role": "user",
            "content": "你是",
            "ts_ms": 1000,
            "lang": "zh",
        }
    )
    store.append_generated_turn(
        {
            "turn_id": "turn::req-2::assistant",
            "session_id": "sess-repeat",
            "person_id": "p1",
            "request_id": "req-2",
            "role": "assistant",
            "content": f"我是{display_name}。",
            "ts_ms": 1001,
            "lang": "zh",
        }
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
        context_read_ports=ConversationContextReadPorts(
            conversation_history=lambda: history,
            conversation_turn_store=lambda: store,
            session_topic_ledger_store=lambda: None,
            session_tactic_ledger_store=lambda: None,
        ),
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
