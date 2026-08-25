from __future__ import annotations

from pathlib import Path

from host_brain.persona.surface.input_validation import build_isolated_surface_input
from host_brain.persona.surface.prompt_construction import build_isolated_surface_messages
from tools.build_three_layer_surface_cases import (
    RAW_CHAIN_SENTINEL_PREFIX,
    RAW_LEAK_SENTINELS,
    SOUL,
    build_three_layer_chain_cases,
    build_summary,
    build_three_layer_surface_cases,
)


def test_three_layer_surface_cases_are_surface_safe() -> None:
    cases = build_three_layer_surface_cases()

    assert len(cases) >= 8
    assert len({case["case_id"] for case in cases}) == len(cases)

    for case in cases:
        request = build_isolated_surface_input(
            {
                "case_id": case["case_id"],
                "reply_control_packet": case["reply_control_packet"],
                "soul_md": SOUL,
                "user_message": case["user_message"],
            }
        )
        rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))
        facts = "\n".join(request.control.facts)

        assert request.user_message
        assert len(request.control.facts) <= 6
        assert "连续摘要：" in facts
        assert "recent_turns" not in rendered
        assert "mid_term_summary" not in rendered
        for sentinel in RAW_LEAK_SENTINELS:
            assert sentinel not in rendered


def test_three_layer_surface_summary_counts_tasks() -> None:
    cases = build_three_layer_surface_cases()
    summary = build_summary(cases, out_dir=Path("out"))

    assert summary["cases"] == len(cases)
    assert summary["max_facts_per_case"] <= 6
    assert summary["raw_context_crosses_surface"] is False
    assert summary["task_counts"]["next_step"] >= 3
    assert summary["task_counts"]["identity"] == 1


def test_three_layer_chain_cases_recompress_long_continuity_without_raw_leak() -> None:
    cases = build_three_layer_chain_cases()

    assert len(cases) >= 31
    assert all(case.get("chain_id") == "three_layer_chain_36" for case in cases)
    assert max(int(case.get("turn_index") or 0) for case in cases) >= 31

    for case in cases:
        request = build_isolated_surface_input(
            {
                "case_id": case["case_id"],
                "reply_control_packet": case["reply_control_packet"],
                "soul_md": SOUL,
                "user_message": case["user_message"],
            }
        )
        rendered = "\n".join(message["content"] for message in build_isolated_surface_messages(request))

        assert len(request.control.facts) <= 6
        assert "连续摘要：" in "\n".join(request.control.facts)
        assert RAW_CHAIN_SENTINEL_PREFIX not in rendered
        assert "recent_turns" not in rendered

    summary = build_summary(cases, out_dir=Path("out"), suite="chain")
    assert summary["suite"] == "chain"
    assert summary["chain_count"] == 1
    assert summary["max_chain_turns"] >= 31
    assert summary["max_facts_per_case"] <= 6
