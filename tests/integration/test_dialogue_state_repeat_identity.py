from __future__ import annotations

from unittest.mock import patch

from host_brain.app.context.prompt_context_plane import assemble_prompt_context_plane
from host_brain.app.context.soul_spec import SoulSpec
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import ConversationHistory, TurnStore


def test_dialogue_state_uses_real_conversation_window_for_repeat_identity(monkeypatch) -> None:
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
        content="我是夏目蓝。",
        ts_ms=1001,
        lang="zh",
    )

    monkeypatch.setattr(
        "host_brain.app.dependencies._conversation_history",
        history,
        raising=False,
    )
    monkeypatch.setattr(
        "host_brain.app.context.prompt_context_plane.build_soul_spec",
        lambda **kwargs: SoulSpec(
            render_target_lang="zh",
            full_text="角色卡\n风格手册",
            compact_text="紧凑人格",
            citation="",
        ),
    )
    monkeypatch.setattr(
        "host_brain.app.context.prompt_context_plane.resolve_prompt_context_style_row",
        lambda prompt_context, node_id: {"style_instruction": "风格手册", "citation": ""},
    )
    monkeypatch.setattr(
        "host_brain.app.prompt_builder_utils._build_character_dynamic_patch",
        lambda **kwargs: "",
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
