from __future__ import annotations

from unittest.mock import patch

from host_brain.app.top_agent_prompt import build_top_agent_prompt


class TestTopAgentPrompt:
    def test_build_top_agent_prompt_includes_runtime_payload_and_capabilities(self) -> None:
        with patch(
            "host_brain.app.chat_runtime_input.build_chat_runtime_input",
            return_value={"runtime": "input"},
        ) as build_runtime_input, patch(
            "host_brain.app.chat_context_builder.build_chat_prompt_runtime_payload",
            return_value={"gen_params": {"temperature": 0.2}},
        ) as build_prompt_payload, patch(
            "host_brain.app.prompt_builder.render_node_generation_prompt",
            return_value="PROMPT_BODY",
        ) as render_prompt, patch(
            "host_brain.app.prompt_builder._build_node_role_instruction",
            return_value="SYSTEM_PROMPT",
        ) as build_role_instruction, patch(
            "host_brain.integrations.llm_helpers.adjust_gen_params_for_emotion",
            side_effect=lambda payload, emotion_context: {
                **payload,
                "gen_params": {"temperature": 0.42},
            },
        ) as adjust_gen_params:
            prompt_text, system_prompt, gen_params = build_top_agent_prompt(
                user_message="hello world",
                render_target_lang="ja",
                emotion_context={"emotion_tag": "HAPPY"},
                emotion_snapshot={"emotion_tag": "HAPPY"},
                retrieval_summary={"status": "OK"},
                retrieval_evidence_items=[{"kind": "world", "content": "fact"}],
                session_id="session-001",
                request_id="req-001",
                thread_id="thread-001",
                bond_summary={"stage": "FRIEND"},
                recall_slot_text={"world": "slot"},
            )

        assert system_prompt == "SYSTEM_PROMPT"
        assert gen_params == {"temperature": 0.42}
        assert prompt_text.startswith("PROMPT_BODY")
        assert "【可用能力" in prompt_text
        assert "code_agent" in prompt_text
        assert "【用户消息】\nhello world" in prompt_text
        assert build_runtime_input.call_count == 1
        assert build_prompt_payload.call_count == 1
        assert render_prompt.call_count == 1
        assert build_role_instruction.call_args.args[0] == "top_agent"
        assert adjust_gen_params.call_count == 1
        assert build_runtime_input.call_args.kwargs["scene_type"] == "CHAT"
        assert build_runtime_input.call_args.kwargs["render_target_lang"] == "ja"
