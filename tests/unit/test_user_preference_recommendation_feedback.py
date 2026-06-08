from __future__ import annotations

from host_brain.app.recommendation_feedback import record_user_preference_signal_if_present
from host_brain.app.user_preference_store import (
    LIGHTNOVEL_DOMAIN,
    UserPreferenceStore,
    build_lightnovel_dynamic_taste_profile,
    record_recommendation_delivery,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger


def test_explicit_lightnovel_preference_updates_profile() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    result = record_user_preference_signal_if_present(
        session_id="s1",
        person_id="u1",
        user_message="我喜欢奇幻设定强一点的小说",
        payload={},
        ledger=ledger,
        ts_ms=1000,
    )

    assert result is not None
    profile = UserPreferenceStore(ledger=ledger).get_profile(person_id="u1", domain=LIGHTNOVEL_DOMAIN)
    assert profile is not None
    assert any("奇幻" in item["label"] for item in profile["likes"])


def test_recommendation_index_feedback_binds_item_and_traits() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    record_recommendation_delivery(
        person_id="u1",
        session_id="s1",
        domain=LIGHTNOVEL_DOMAIN,
        platform="qq",
        conversation_id="c1",
        request_id="r1",
        job_id="j1",
        recommendations_ref="recommendations.json",
        ranked_recommendations_ref="ranked.json",
        items=[
            {
                "recommendation_rank": 1,
                "title": "魔法学院的转生皇女",
                "series_key": "magic-princess",
                "preference_matches": [
                    {"group_id": "fantasy_hook", "label": "奇幻身份钩子", "keywords": ["魔法", "皇女"], "score": 20}
                ],
            },
            {"recommendation_rank": 2, "title": "校园恋爱日常", "series_key": "school-love"},
        ],
        ledger=ledger,
    )

    result = record_user_preference_signal_if_present(
        session_id="s1",
        person_id="u1",
        user_message="第一本我喜欢",
        payload={"conversation_id": "c1"},
        ledger=ledger,
        ts_ms=2000,
    )

    assert result is not None
    profile = UserPreferenceStore(ledger=ledger).get_profile(person_id="u1", domain=LIGHTNOVEL_DOMAIN)
    assert profile is not None
    assert any("魔法学院" in item["label"] for item in profile["liked_items"])
    assert any("奇幻身份钩子" in item["label"] for item in profile["likes"])


def test_ambiguous_this_book_feedback_does_not_bind_specific_item() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    record_recommendation_delivery(
        person_id="u1",
        session_id="s1",
        domain=LIGHTNOVEL_DOMAIN,
        platform="qq",
        conversation_id="c1",
        request_id="r1",
        job_id="j1",
        recommendations_ref="recommendations.json",
        ranked_recommendations_ref="ranked.json",
        items=[
            {"recommendation_rank": 1, "title": "A", "series_key": "a"},
            {"recommendation_rank": 2, "title": "B", "series_key": "b"},
        ],
        ledger=ledger,
    )

    record_user_preference_signal_if_present(
        session_id="s1",
        person_id="u1",
        user_message="这本我喜欢",
        payload={"conversation_id": "c1"},
        ledger=ledger,
        ts_ms=2000,
    )

    profile = UserPreferenceStore(ledger=ledger).get_profile(person_id="u1", domain=LIGHTNOVEL_DOMAIN)
    assert profile is not None
    assert profile["liked_items"] == []
    assert any("推荐集合" in item["label"] for item in profile["likes"])


def test_negative_preference_affects_dynamic_lightnovel_profile() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    record_user_preference_signal_if_present(
        session_id="s1",
        person_id="u1",
        user_message="我不喜欢后宫套路小说",
        payload={},
        ledger=ledger,
        ts_ms=1000,
    )

    profile = build_lightnovel_dynamic_taste_profile(
        {"version": "base", "positive_keyword_groups": {}, "negative_keyword_groups": {}},
        person_id="u1",
        ledger=ledger,
    )

    assert profile["negative_keyword_groups"]
    assert any("后宫" in ",".join(group["keywords"]) for group in profile["negative_keyword_groups"].values())
