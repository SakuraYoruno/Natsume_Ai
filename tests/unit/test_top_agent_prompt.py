from __future__ import annotations

from unittest.mock import patch

from host_brain.app.top_agent_prompt import (
    _build_recent_session_context_block,
    build_top_agent_prompt,
)


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
        ) as adjust_gen_params, patch(
            "host_brain.app.top_agent_prompt._build_recent_session_context_block",
            return_value="",
        ) as recent_context:
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

        assert "SYSTEM_PROMPT" in system_prompt
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
        assert recent_context.call_count == 1
        assert build_runtime_input.call_args.kwargs["scene_type"] == "CHAT"
        assert build_runtime_input.call_args.kwargs["render_target_lang"] == "ja"
        assert recent_context.call_args.kwargs["render_target_lang"] == "ja"
        assert "話し方の補正（高優先度）" in system_prompt

    def test_build_recent_session_context_block_formats_previous_turns(self) -> None:
        class _FakeLedger:
            def fetch_by_time_range(self, table: str, *, limit: int = 100, **_: object) -> list[dict[str, object]]:
                assert table == "voice_event_log"
                assert limit == 2000
                return [
                    {
                        "request_id": "req-old-1",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "贪吃蛇",
                            "reply_preview": "贪吃蛇啊……是说那个游戏吗？",
                        },
                    },
                    {
                        "request_id": "req-old-2",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "贪吃蛇",
                            "reply_preview": "贪吃蛇……你是想玩这个游戏吗？",
                        },
                    },
                    {
                        "request_id": "req-current",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "我刚才说什么了啊",
                            "reply_preview": "当前请求不应进入上下文",
                        },
                    },
                ]

        with patch("host_brain.app.dependencies.get_ledger", return_value=_FakeLedger()):
            block = _build_recent_session_context_block(
                session_id="qq_dm_3414578517",
                request_id="req-current",
                max_turns=2,
            )

        assert "Recent session context:" in block
        assert "User: 贪吃蛇" in block
        assert "Assistant: 贪吃蛇啊……是说那个游戏吗？" in block
        assert "Assistant: 贪吃蛇……你是想玩这个游戏吗？" in block
        assert "当前请求不应进入上下文" not in block

    def test_build_recent_session_context_block_filters_japanese_and_corrupted_rows_for_zh(self) -> None:
        class _FakeLedger:
            def fetch_by_time_range(self, table: str, *, limit: int = 100, **_: object) -> list[dict[str, object]]:
                assert table == "voice_event_log"
                assert limit == 2000
                return [
                    {
                        "request_id": "req-old-1",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "贪吃蛇",
                            "reply_preview": "贪吃蛇啊……是说那个游戏吗？",
                        },
                    },
                    {
                        "request_id": "req-old-2",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "我刚才说什么了啊",
                            "reply_preview": "ちょっと待ってね…",
                        },
                    },
                    {
                        "request_id": "req-old-3",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "????????",
                            "reply_preview": "?????",
                        },
                    },
                ]

        with patch("host_brain.app.dependencies.get_ledger", return_value=_FakeLedger()):
            block = _build_recent_session_context_block(
                session_id="qq_dm_3414578517",
                request_id="req-current",
                render_target_lang="zh",
                max_turns=4,
            )

        assert "Recent session context:" in block
        assert "User: 贪吃蛇" in block
        assert "Assistant: 贪吃蛇啊……是说那个游戏吗？" in block
        assert "User: 我刚才说什么了啊" in block
        assert "ちょっと待ってね…" not in block
        assert "????????" not in block
        assert "?????" not in block

    def test_build_recent_session_context_block_uses_person_id_across_sessions(self) -> None:
        class _FakeCursor:
            def __init__(self, rows: list[dict[str, object]]) -> None:
                self._rows = rows

            def fetchall(self) -> list[dict[str, object]]:
                return list(self._rows)

        class _FakeConn:
            def execute(self, _sql: str, _params: tuple[object, ...]) -> _FakeCursor:
                return _FakeCursor([
                    {
                        "request_id": "req-latest",
                        "session_id": "wechat_session_01",
                        "payload": {
                            "person_id": "user-001",
                            "input_preview": "寰俊鍒氭墠璇磋繃鐨勮瘽",
                            "reply_preview": "鎴戝湪寰俊閲岃繕鍥炶繃浣犮€?",
                        },
                        "ts_ms": 30,
                    },
                    {
                        "request_id": "req-middle",
                        "session_id": "qq_session_02",
                        "payload": {
                            "person_id": "user-001",
                            "input_preview": "QQ閲岃杩囩殑璇?",
                            "reply_preview": "鎴戝湪QQ閲岃繕鍥炶繃浣犮€?",
                        },
                        "ts_ms": 20,
                    },
                    {
                        "request_id": "req-foreign",
                        "session_id": "other_session_99",
                        "payload": {
                            "person_id": "user-999",
                            "input_preview": "鍒汉鐨勫唴瀹?",
                            "reply_preview": "涓嶅簲琚甫鍏?",
                        },
                        "ts_ms": 10,
                    },
                ])

        class _FakeLedger:
            _conn = _FakeConn()

        with patch("host_brain.app.dependencies.get_ledger", return_value=_FakeLedger()):
            block = _build_recent_session_context_block(
                session_id="asr_session_current",
                person_id="user-001",
                request_id="req-current",
                render_target_lang="zh",
            )

        assert "Recent continuity context:" in block
        assert "寰俊鍒氭墠璇磋繃鐨勮瘽" in block
        assert "QQ閲岃杩囩殑璇?" in block
        assert "鍒汉鐨勫唴瀹?" not in block

    def test_build_recent_session_context_block_adds_older_history_summary(self) -> None:
        class _FakeLedger:
            def fetch_by_time_range(self, table: str, *, limit: int = 100, **_: object) -> list[dict[str, object]]:
                assert table == "voice_event_log"
                assert limit == 2000
                return [
                    {
                        "request_id": "req-old-1",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "old topic alpha",
                            "reply_preview": "old reply alpha",
                        },
                    },
                    {
                        "request_id": "req-old-2",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "old topic beta",
                            "reply_preview": "old reply beta",
                        },
                    },
                    {
                        "request_id": "req-recent-1",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "recent topic one",
                            "reply_preview": "recent reply one",
                        },
                    },
                    {
                        "request_id": "req-recent-2",
                        "session_id": "qq_dm_3414578517",
                        "event_type": "INPUT_COMPLETED",
                        "payload": {
                            "input_preview": "recent topic two",
                            "reply_preview": "recent reply two",
                        },
                    },
                ]

        with patch("host_brain.app.dependencies.get_ledger", return_value=_FakeLedger()):
            block = _build_recent_session_context_block(
                session_id="qq_dm_3414578517",
                request_id="req-current",
                render_target_lang="zh",
                max_turns=2,
            )

        assert "Recent session context:" in block
        assert "recent topic one" in block
        assert "recent topic two" in block
        assert "Earlier continuity summary:" in block
        assert "old topic alpha" in block or "old topic beta" in block

    def test_build_top_agent_prompt_appends_recent_session_context(self) -> None:
        with patch(
            "host_brain.app.chat_runtime_input.build_chat_runtime_input",
            return_value={"runtime": "input"},
        ), patch(
            "host_brain.app.chat_context_builder.build_chat_prompt_runtime_payload",
            return_value={"gen_params": {"temperature": 0.2}},
        ), patch(
            "host_brain.app.prompt_builder.render_node_generation_prompt",
            return_value="PROMPT_BODY",
        ), patch(
            "host_brain.app.prompt_builder._build_node_role_instruction",
            return_value="SYSTEM_PROMPT",
        ), patch(
            "host_brain.app.top_agent_prompt._build_recent_session_context_block",
            return_value="Recent session context:\nUser: 贪吃蛇\nAssistant: 贪吃蛇啊……是说那个游戏吗？",
        ):
            prompt_text, _, _ = build_top_agent_prompt(
                user_message="我刚才说什么了啊",
                session_id="qq_dm_3414578517",
                request_id="req-current",
            )

        assert "Recent session context:" in prompt_text
        assert "Assistant: 贪吃蛇啊……是说那个游戏吗？" in prompt_text
