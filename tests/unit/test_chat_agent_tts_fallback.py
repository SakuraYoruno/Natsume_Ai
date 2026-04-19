from __future__ import annotations

from host_brain.app.chat_agent import _should_schedule_commit_tts_fallback


def test_should_schedule_commit_tts_fallback_skips_external_voice_path() -> None:
    should_schedule = _should_schedule_commit_tts_fallback(
        narrative="おはよう。",
        tts_sentence_index=0,
        suppress_tts=True,
        reply_channel="wechat",
        voice_outputs_enabled=True,
    )

    assert should_schedule is False


def test_should_schedule_commit_tts_fallback_skips_chat_api_when_voice_outputs_disabled() -> None:
    should_schedule = _should_schedule_commit_tts_fallback(
        narrative="おはよう。",
        tts_sentence_index=0,
        suppress_tts=False,
        reply_channel=None,
        voice_outputs_enabled=False,
    )

    assert should_schedule is False
