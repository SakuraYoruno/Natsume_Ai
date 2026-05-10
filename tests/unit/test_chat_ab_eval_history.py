from __future__ import annotations

from host_brain.app.cortex_prompt_builder import build_cortex_prompt


def test_cortex_prompt_uses_ab_eval_history_turns() -> None:
    messages, _gen_params, runtime_payload = build_cortex_prompt(
        user_message="为什么",
        render_target_lang="zh",
        request_id="ab_eval_case",
        session_id="ab_eval_case",
        person_id="ab_eval_user",
        eval_history_turns=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "哥哥，我来了。"},
        ],
        include_runtime_payload=True,
    )

    non_system_messages = [item for item in messages if item["role"] != "system"]
    assert non_system_messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "哥哥，我来了。"},
        {"role": "user", "content": "为什么"},
    ]
    assert runtime_payload["conversation_history"]["turns_injected"] == 2
    assert runtime_payload["conversation_history"]["fetched_turns"] == 2
