from __future__ import annotations

import json

from host_brain.persona.continuity_surface_sample_bank import (
    DEFAULT_CONTINUITY_SURFACE_SAMPLE_BANK_PATH,
    clear_continuity_surface_sample_bank_cache,
    get_continuity_surface_sample_bank,
    render_continuity_surface_generation_guidance,
)


def test_default_continuity_surface_sample_bank_loads() -> None:
    clear_continuity_surface_sample_bank_cache()
    bank = get_continuity_surface_sample_bank()

    selected = bank.select(response_move="ack_with_topic_hook", task_mode="short_ack")

    assert DEFAULT_CONTINUITY_SURFACE_SAMPLE_BANK_PATH.exists()
    assert bank.protocol_lines
    assert bank.rewrite_samples
    assert selected.move_samples
    assert selected.move_samples[0].sample_id == "ack_with_topic_hook"


def test_continuity_surface_generation_guidance_renders_bank_samples() -> None:
    clear_continuity_surface_sample_bank_cache()

    rendered = render_continuity_surface_generation_guidance(
        response_move="ack_with_topic_hook",
        task_mode="short_ack",
    )

    assert "连续链表层生成协议" in rendered
    assert "open loop=未完成话口" in rendered
    assert "给你来一段长的 -> 日常就日常，别端着。" in rendered
    assert "锁死事实数 -> 事实数先控制住。" in rendered
    assert "懂了，旧细节退成方向词就行。" in rendered


def test_continuity_surface_generation_guidance_falls_back_to_default_sample() -> None:
    clear_continuity_surface_sample_bank_cache()

    rendered = render_continuity_surface_generation_guidance(
        response_move="unknown_move",
        task_mode="unknown_task",
    )

    assert "先按这轮继续收。" in rendered
    assert "degraded_reason=FALLBACK_CONTINUITY_SURFACE_SAMPLES" in rendered


def test_continuity_surface_sample_bank_can_be_overridden(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    bank_path = tmp_path / "continuity_surface_generation_bank.json"
    bank_path.write_text(
        json.dumps(
            {
                "protocol_lines": ["把内部话翻成私聊句。"],
                "term_map": [{"internal": "raw history", "surface": "旧对话原文"}],
                "rewrite_samples": [{"bad": "你看着接", "good": "我先按这轮继续收。"}],
                "move_samples": [
                    {
                        "sample_id": "custom_ack",
                        "response_moves": ["ack_with_topic_hook"],
                        "task_modes": ["short_ack"],
                        "examples": ["行，这轮先落在当前话题上。"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOST_CONTINUITY_SURFACE_SAMPLE_BANK_PATH", str(bank_path))
    clear_continuity_surface_sample_bank_cache()

    rendered = render_continuity_surface_generation_guidance(
        response_move="ack_with_topic_hook",
        task_mode="short_ack",
    )

    assert "把内部话翻成私聊句。" in rendered
    assert "raw history=旧对话原文" in rendered
    assert "行，这轮先落在当前话题上。" in rendered
