from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.conversation import ConversationHistory
from host_brain.composition.process_service_container import get_process_service_container
from host_brain.persistence.context_repository import ContextRepository
from host_brain.app.cortex_prompt_builder import build_cortex_prompt


def _plane_with_real_history(history: ConversationHistory, *, session_id: str, request_id: str) -> SimpleNamespace:
    window = history.fetch_window_for_messages(
        session_id=session_id,
        max_turns=10,
        exclude_request_id=request_id,
        render_target_lang="zh",
    )
    return SimpleNamespace(
        soul=SimpleNamespace(
            full_text="不是 AI 助手。\n角色卡\n不是客服腔。\n风格手册",
            compact_text="紧凑人格",
            citation="",
        ),
        continuity=SimpleNamespace(
            recent_turns=[
                {
                    "turn_id": turn.turn_id,
                    "request_id": turn.request_id,
                    "role": turn.role,
                    "content": turn.content,
                    "turn_index": turn.turn_index,
                    "lang": turn.lang,
                }
                for turn in window.recent_turns
            ],
            summary=window.older_summary,
            turns_injected=window.injected_count,
            fetched_turns=window.fetched_count,
            older_turn_count=window.older_turn_count,
            oldest_ts_ms=window.oldest_ts_ms,
            newest_ts_ms=window.newest_ts_ms,
            skipped_corrupted_count=window.skipped_corrupted_count,
            skipped_empty_count=window.skipped_empty_count,
            deduped_same_role_count=window.deduped_same_role_count,
        ),
        intent=SimpleNamespace(turn_context_sections=()),
    )


def test_chat_history_end_to_end_recent_messages_and_session_isolation() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    store = ContextRepository.from_ledger(ledger)
    history = ConversationHistory(store)

    for request_id, role, content, ts_ms in (
        ("req-1", "user", "今天在公司被夸了", 1000),
        ("req-2", "assistant", "嗯，听起来还不错。", 1001),
        ("req-3", "user", "饿了，我们点外卖吧", 1002),
        ("req-4", "assistant", "你想吃什么？", 1003),
        ("req-5", "user", "昨天那家店还记得吗", 1004),
        ("req-6", "assistant", "记得。你当时嫌它太咸。", 1005),
    ):
        store.append_generated_turn(
            {
                "turn_id": f"turn::{request_id}::{role}",
                "session_id": "sess-a",
                "person_id": "p1",
                "request_id": request_id,
                "role": role,
                "content": content,
                "ts_ms": ts_ms,
                "lang": "zh",
            }
        )

    store.append_generated_turn(
        {
            "turn_id": "turn::req-other::assistant",
            "session_id": "sess-b",
            "person_id": "p1",
            "request_id": "req-other",
            "role": "assistant",
            "content": "别的会话内容",
            "ts_ms": 2000,
            "lang": "zh",
        }
    )

    with patch(
        "host_brain.app.prompt_builder._build_node_role_instruction",
        return_value="节点指令",
    ), patch.object(
        get_process_service_container(),
        "conversation_history",
        history,
    ), patch(
        "host_brain.app.cortex_prompt_builder.assemble_prompt_context_plane",
        side_effect=lambda **kwargs: _plane_with_real_history(
            history,
            session_id=str(kwargs.get("runtime_payload", {}).get("request", {}).get("session_id") or "sess-a"),
            request_id=str(kwargs.get("runtime_payload", {}).get("request", {}).get("request_id") or "req-7"),
        ),
    ):
        messages, _gen_params, runtime_payload = build_cortex_prompt(
            user_message="那你昨天晚饭吃什么了",
            render_target_lang="zh",
            person_id="p1",
            session_id="sess-a",
            request_id="req-7",
            include_runtime_payload=True,
        )

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert messages[-1]["content"] == "那你昨天晚饭吃什么了"
    assert all("别的会话内容" not in message["content"] for message in messages)
    assert runtime_payload["conversation_history"]["turns_injected"] == 6
    assert runtime_payload["conversation_history"]["older_turn_count"] == 0
