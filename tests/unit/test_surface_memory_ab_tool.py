from __future__ import annotations

import argparse
import asyncio
import json

from tools.surface_memory_ab import _compare_variants, default_cases, run_surface_memory_ab


def test_surface_memory_ab_default_cases_use_quality_pack() -> None:
    quality_cases = default_cases("quality_pack")
    smoke_cases = default_cases("smoke")

    assert len(quality_cases) > len(smoke_cases) == 8
    assert all(case.scene_id for case in quality_cases)
    assert {case.case_id for case in quality_cases} >= {
        "short_ack_plain",
        "relationship_doubt_direct",
        "negative_feedback_grammar",
    }


def test_surface_memory_ab_does_not_mark_same_visible_text_as_worse() -> None:
    comparison = _compare_variants(
        {
            "affect": {"selected_text": "哼，回来就好。"},
            "memory_affect": {
                "selected_text": "哼，回来就好。",
                "source_ref_leak": False,
                "observation_codes": [],
            },
        }
    )

    assert comparison["same_selected_text"] is True
    assert comparison["manual_review_required"] is False
    assert comparison["memory_worse"] is False


def test_surface_memory_ab_treats_different_text_as_manual_review_not_worse() -> None:
    comparison = _compare_variants(
        {
            "affect": {"selected_text": "嗯，我听着呢，别停。"},
            "memory_affect": {
                "selected_text": "哼，别停，往下。",
                "source_ref_leak": False,
                "observation_codes": [],
            },
        }
    )

    assert comparison["manual_review_required"] is True
    assert comparison["memory_worse"] is False


def test_surface_memory_ab_marks_only_hard_issues_as_worse() -> None:
    comparison = _compare_variants(
        {
            "affect": {"selected_text": "哼，有一点，别笑。"},
            "memory_affect": {
                "selected_text": "memory://x 有一点啦。",
                "source_ref_leak": True,
                "observation_codes": [],
            },
        }
    )

    assert comparison["memory_worse"] is True
    assert comparison["manual_review_required"] is False
