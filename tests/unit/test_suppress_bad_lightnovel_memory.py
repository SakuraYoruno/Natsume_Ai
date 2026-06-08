from __future__ import annotations

import json
import sqlite3
from typing import Any

from scripts.runtime.suppress_bad_lightnovel_memory import (
    _find_candidates,
    _is_bad_lightnovel_quality_memory,
)


def _row(*, summary: str, metadata: dict[str, Any] | None = None, action: str = "chat_turn", result: str = "assistant_reply") -> sqlite3.Row:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE life_episode (
            episode_id TEXT,
            episode_kind TEXT,
            action TEXT,
            result TEXT,
            summary TEXT,
            metadata TEXT,
            truth_state TEXT,
            status TEXT,
            created_ts_ms INTEGER,
            last_touched_ts_ms INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO life_episode VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ep-test",
            "chat_episode",
            action,
            result,
            summary,
            json.dumps(metadata or {}, ensure_ascii=False),
            "VERIFIED",
            "ACTIVE",
            1,
            1,
        ),
    )
    return conn.execute("SELECT * FROM life_episode").fetchone()


def test_bad_lightnovel_memory_flags_404_quality_answer() -> None:
    row = _row(
        summary="User: 这三本书，你喜欢哪本？\nAssistant: 我会先看《404》。简介也不是空的。"
    )

    bad, reason = _is_bad_lightnovel_quality_memory(row, include_failed_deep_review=False)

    assert bad is True
    assert reason == "bad_marker:《404》"


def test_bad_lightnovel_memory_flags_unsupported_quality_claim() -> None:
    row = _row(
        summary=(
            "User: 昨天推荐 B：与神在河岸 这本好看吗？\n"
            "Assistant: 如果你喜欢偏冷峻、克制、带一点社会观察和内心拉扯的小说，"
            "《与神在河岸》通常是好看的。"
        )
    )

    bad, reason = _is_bad_lightnovel_quality_memory(row, include_failed_deep_review=False)

    assert bad is True
    assert reason.startswith("unsupported_quality_marker:")


def test_bad_lightnovel_memory_flags_style_guess_quality_claim() -> None:
    row = _row(
        summary=(
            "User: 昨天推荐 B：与神在河岸 这本好看吗？\n"
            "Assistant: 《与神在河岸》通常算是比较吃设定和吃文风的书，"
            "世界观有宗教、神秘感、寓言感。"
        )
    )

    bad, reason = _is_bad_lightnovel_quality_memory(row, include_failed_deep_review=False)

    assert bad is True
    assert reason.startswith("unsupported_quality_marker:")


def test_bad_lightnovel_memory_keeps_not_ready_answer() -> None:
    row = _row(
        summary=(
            "User: 昨天推荐 B：与神在河岸 这本好看吗？\n"
            "Assistant: 我记得有相关条目，但现在没有足够详情或正文证据，不能直接判断哪本更好看。"
        )
    )

    assert _is_bad_lightnovel_quality_memory(row, include_failed_deep_review=False) == (False, "")


def test_bad_lightnovel_memory_keeps_valid_deep_review_answer() -> None:
    row = _row(
        summary="User: 这三本哪本好看？\nAssistant: 我会先看《与神在河岸》。对白比较轻快。",
        metadata={
            "trace_stage_meta": {
                "surface_memory_recall_packet": {
                    "facts": [
                        "网页深读结果（含正文）：当前更推荐第2本《与神在河岸》。原因：对白比较轻快。"
                    ],
                    "trace": {
                        "lightnovel_deep_review": {
                            "status": "OK",
                            "selected": {"title": "与神在河岸"},
                        }
                    },
                }
            }
        },
    )

    assert _is_bad_lightnovel_quality_memory(row, include_failed_deep_review=False) == (False, "")


def test_find_candidates_keeps_failed_deep_review_by_default() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE life_episode (
            episode_id TEXT,
            episode_kind TEXT,
            action TEXT,
            result TEXT,
            summary TEXT,
            metadata TEXT,
            truth_state TEXT,
            status TEXT,
            created_ts_ms INTEGER,
            last_touched_ts_ms INTEGER
        )
        """
    )
    conn.execute(
        "INSERT INTO life_episode VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ep-failed-review",
            "action_episode",
            "lightnovel_recommendation_deep_review",
            "DEGRADED",
            "我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。",
            json.dumps(
                {
                    "reason_code": "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
                    "items": [{"review_status": "FAILED"}],
                    "selected": {},
                },
                ensure_ascii=False,
            ),
            "VERIFIED",
            "ACTIVE",
            1,
            1,
        ),
    )

    assert _find_candidates(conn, limit=10, include_failed_deep_review=False) == []
    candidates = _find_candidates(conn, limit=10, include_failed_deep_review=True)
    assert [item["episode_id"] for item in candidates] == ["ep-failed-review"]
