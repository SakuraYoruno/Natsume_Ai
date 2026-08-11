from __future__ import annotations

import json
import sqlite3
import time
from functools import partial
import sys
from types import SimpleNamespace

import pytest

from host_brain.agent_runtime.lightnovel_deep_reviewer import (
    _cards_signature,
    _extract_cards,
    may_deep_review_lightnovel_selection,
    review_lightnovel_selection_from_memory_packet,
    should_deep_review_lightnovel_selection,
)
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.memory.life_memory.store import LifeMemoryStore


@pytest.fixture(autouse=True)
def _configure_review_persistence(monkeypatch):  # noqa: ANN001
    from host_brain.app.observability_persist import persist_life_action_event_memory

    review = review_lightnovel_selection_from_memory_packet

    def _review_with_explicit_persistence(**kwargs):  # noqa: ANN003, ANN202
        life_memory_store = kwargs.get("life_memory_store")
        kwargs.setdefault(
            "persist_life_action_event_memory",
            partial(
                persist_life_action_event_memory,
                life_memory_store=life_memory_store,
            ),
        )
        return review(**kwargs)

    monkeypatch.setattr(
        sys.modules[__name__],
        "review_lightnovel_selection_from_memory_packet",
        _review_with_explicit_persistence,
    )


class _Response:
    status_code = 200
    text = ""

    def __init__(self, text: str) -> None:
        self.text = text


def _html(title: str, description: str, *, info: str = "", chapters: str = "") -> str:
    return f"""
    <html>
      <head><title>{title}</title><meta name="description" content="{description}"/></head>
      <body>
        <h1 class="article-title">{title}</h1>
        <div class="article-infos">{info}</div>
        <div class="article-content">{chapters}</div>
      </body>
    </html>
    """


def _body(text: str, repeat: int = 8) -> str:
    return "<p>" + "</p><p>".join([text] * repeat) + "</p>"


def _metadata_dict(row: dict) -> dict:
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        return json.loads(metadata)
    return dict(metadata or {})


def test_lightnovel_deep_review_fetches_detail_pages_and_builds_fact(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "0")
    pages = {
        "https://www.lightnovel.fun/cn/detail/1": _html(
            "恋爱日常 A",
            "前美少女童星与搭把手后的关系展开，偏轻松恋爱日常。",
            info="1 小时前 浏览数：10 评论：1",
            chapters=_body("第16话 晚安电话。备用钥匙、同居感、寂寞和撒娇让两人的关系继续推进。", 16),
        ),
        "https://www.lightnovel.fun/cn/detail/2": _html(
            "与神在河岸",
            "标题和简介带奇幻身份钩子，信息完整度比普通列表项更高。",
            info="30 分钟前 浏览数：79 评论：1",
            chapters='<a href="/c/1">第1章 河岸</a><a href="/c/2">第2章 神明</a>'
            + _body("第1章 河岸。异世界、皇女、魔法、神明、王道和邪道让这一段有奇幻闲聊的钩子。", 32),
        ),
    }

    def fake_get(url, **_kwargs):  # noqa: ANN001
        return _Response(pages[str(url)])

    monkeypatch.setattr("requests.get", fake_get)
    packet = {
        "facts": [
            "记忆线索：我刚才推荐了 2 本轻小说；可比较证据："
            "1. 恋爱日常 A｜看点：恋爱日常｜简介：前美少女童星｜详情：https://www.lightnovel.fun/cn/detail/1；"
            "2. 与神在河岸｜看点：公开信号较好｜简介：奇幻身份钩子｜详情：https://www.lightnovel.fun/cn/detail/2"
        ]
    }

    result = review_lightnovel_selection_from_memory_packet(
        packet=packet,
        user_message="这两本哪本好看？",
        request_id="req-test",
        person_id="person-test",
        session_id="session-test",
    )

    assert result["status"] == "OK"
    assert result["cached"] is False
    assert result["source"] == "fetch"
    assert result["recommendation_signature"]
    assert "网页深读结果（含正文）" in result["fact"]
    assert "与神在河岸" in result["fact"]
    assert "正文内容足够支撑判断" not in result["fact"]
    assert "我回去打开详情页" not in result["fact"]
    assert "当前更推荐第" in result["fact"]
    assert "原因：" in result["fact"]
    assert result["selected"]["content_char_count"] > 500
    assert result["selected"]["body_observations"]
    assert any(item.get("detail_url") == "https://www.lightnovel.fun/cn/detail/2" and item.get("content_char_count") > 500 for item in result["items"])


def test_lightnovel_deep_review_requires_selection_query_and_recommendation_packet() -> None:
    assert may_deep_review_lightnovel_selection(user_message="这三本哪本好看？")
    assert may_deep_review_lightnovel_selection(user_message="刚才第二本的详情")
    assert not may_deep_review_lightnovel_selection(user_message="今天有点累，安静陪我一句。")
    assert should_deep_review_lightnovel_selection(
        user_message="这三本哪本好看？",
        packet={"facts": ["记忆线索：可比较证据：1. A｜详情：https://www.lightnovel.fun/cn/detail/1"]},
    )
    assert should_deep_review_lightnovel_selection(
        user_message="昨天那几本好看吗",
        packet={"facts": ["记忆线索：可比较证据：1. A｜详情：https://www.lightnovel.fun/cn/detail/1"]},
    )
    assert not should_deep_review_lightnovel_selection(
        user_message="今天聊点别的",
        packet={"facts": ["记忆线索：可比较证据：1. A｜详情：https://www.lightnovel.fun/cn/detail/1"]},
    )


def test_lightnovel_deep_review_degrades_when_only_summary_is_available(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "0")

    def fake_get(url, **_kwargs):  # noqa: ANN001
        assert str(url) == "https://www.lightnovel.fun/cn/detail/summary-only"
        return _Response(
            """
            <html>
              <head><title>只有简介</title><meta name="description" content="只有公开简介，没有正文。"/></head>
              <body><h1 class="article-title">只有简介</h1><div class="article-content">短简介。</div></body>
            </html>
            """
        )

    monkeypatch.setattr("requests.get", fake_get)
    result = review_lightnovel_selection_from_memory_packet(
        packet={
            "facts": [
                "记忆线索：我刚才推荐了 1 本轻小说；可比较证据："
                "1. 只有简介｜详情：https://www.lightnovel.fun/cn/detail/summary-only"
            ]
        },
        user_message="这本好看吗？",
        request_id="req-summary-only",
        person_id="person-test",
        session_id="session-test",
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_NO_READABLE_BODY"
    assert "不能装作" in result["fact"]


def test_lightnovel_deep_review_can_trigger_from_readiness_trace_after_facts_are_dropped() -> None:
    packet = {
        "facts": [],
        "rows": [
            {
                "fact": "本次轻小说推荐集合，记录 3 个条目；1. A｜看点：恋爱日常；2. B｜看点：奇幻身份",
                "store_id": "life_memory_collection",
            }
        ],
        "trace": {
            "plan": {"target_domains": ["lightnovel_recommendation"]},
            "recall_answer_readiness": {
                "status": "NOT_READY",
                "response_mode": "fetch_or_request_detail_evidence",
                "surface_action": "fetch_more_evidence",
            },
            "selected_collection_items": [
                {"index": 1, "title": "A", "selection_reason": "恋爱日常"},
                {"index": 2, "title": "B", "selection_reason": "奇幻身份"},
            ],
        },
    }

    assert should_deep_review_lightnovel_selection(user_message="这三本哪本好看？", packet=packet)
    cards = _extract_cards(packet)
    assert [card["title"] for card in cards[:2]] == ["A", "B"]


def test_lightnovel_card_extraction_ignores_previous_deep_review_fact() -> None:
    packet = {
        "facts": [
            "网页深读结果（含正文）：当前更推荐第1本《A》。另外我也看了第3本《C》。可比较证据：1. A｜详情：https://www.lightnovel.fun/cn/detail/1；3. C｜详情：https://www.lightnovel.fun/cn/detail/3",
            "记忆线索：我刚才推荐了 3 本轻小说；可比较证据：1. A｜看点：恋爱日常｜详情：https://www.lightnovel.fun/cn/detail/1；2. B｜看点：奇幻｜详情：https://www.lightnovel.fun/cn/detail/2；3. C｜看点：轻喜剧｜详情：https://www.lightnovel.fun/cn/detail/3",
        ]
    }

    cards = _extract_cards(packet)

    assert [card["index"] for card in cards] == [1, 2, 3]
    assert [card["detail_url"] for card in cards] == [
        "https://www.lightnovel.fun/cn/detail/1",
        "https://www.lightnovel.fun/cn/detail/2",
        "https://www.lightnovel.fun/cn/detail/3",
    ]


def test_lightnovel_deep_review_extracts_machine_readable_collection_items() -> None:
    packet = {
        "facts": ["集合记忆：最近轻小说推荐集合，记录 15 个条目；可比较证据：1. A｜看点：恋爱日常；2. B｜看点：奇幻身份"],
        "trace": {
            "selected_collection_items": [
                {
                    "index": 1,
                    "title": "A",
                    "detail_url": "https://www.lightnovel.fun/cn/detail/1",
                    "selection_reason": "恋爱日常",
                },
                {
                    "index": 2,
                    "title": "B",
                    "detail_url": "https://www.lightnovel.fun/cn/detail/2",
                    "selection_reason": "奇幻身份钩子",
                },
            ]
        },
    }

    assert should_deep_review_lightnovel_selection(user_message="这十几本里哪本好看？", packet=packet)
    cards = _extract_cards(packet)
    assert [card["title"] for card in cards[:2]] == ["A", "B"]
    assert [card["detail_url"] for card in cards[:2]] == [
        "https://www.lightnovel.fun/cn/detail/1",
        "https://www.lightnovel.fun/cn/detail/2",
    ]


def test_lightnovel_deep_review_extracts_entity_trace_items() -> None:
    packet = {
        "facts": ["实体记忆：与神在河岸；出现 1 次；最近证据：来源：https://www.lightnovel.fun/cn/detail/2"],
        "trace": {
            "selected_entity_items": [
                {
                    "index": 1,
                    "title": "与神在河岸",
                    "detail_url": "https://www.lightnovel.fun/cn/detail/2",
                    "selection_reason": "奇幻身份钩子",
                    "intro_snippet": "神明与河岸的奇幻闲聊。",
                }
            ]
        },
    }

    assert should_deep_review_lightnovel_selection(user_message="这本好看吗？", packet=packet)
    cards = _extract_cards(packet)
    assert len(cards) == 1
    assert cards[0]["title"] == "与神在河岸"
    assert cards[0]["detail_url"] == "https://www.lightnovel.fun/cn/detail/2"
    assert cards[0]["selection_reason"] == "奇幻身份钩子"


def test_lightnovel_deep_review_does_not_replace_card_title_with_404_page(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "0")

    def fake_get(url, **_kwargs):  # noqa: ANN001
        assert str(url) == "https://www.lightnovel.fun/cn/detail/missing"
        return _Response(_html("404", "", chapters=""))

    monkeypatch.setattr("requests.get", fake_get)
    packet = {
        "facts": ["实体记忆：与神在河岸；出现 1 次。"],
        "trace": {
            "selected_entity_items": [
                {
                    "index": 1,
                    "title": "与神在河岸",
                    "detail_url": "https://www.lightnovel.fun/cn/detail/missing",
                    "selection_reason": "奇幻身份钩子",
                    "intro_snippet": "神明与河岸的奇幻闲聊。",
                }
            ]
        },
    }

    result = review_lightnovel_selection_from_memory_packet(
        packet=packet,
        user_message="与神在河岸这本好看吗？",
        request_id="req-404-title",
        person_id="person-test",
        session_id="session-test",
    )

    assert result["status"] == "DEGRADED"
    assert result["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED"
    assert result["items"][0]["title"] == "与神在河岸"
    assert result["items"][0]["review_status"] == "FAILED"
    assert "404" not in result["fact"]
    assert result["selected"] == {}


def test_lightnovel_deep_review_marks_partial_scope_for_recent_many_items(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "0")
    pages = {
        f"https://www.lightnovel.fun/cn/detail/{index}": _html(
                f"推荐 {index}",
                f"第 {index} 本简介，偏轻小说日常。",
                chapters=_body(f"第1章 推荐 {index}。角色对话、关系推进和日常展开。", 24),
            )
        for index in range(1, 7)
    }

    def fake_get(url, **_kwargs):  # noqa: ANN001
        assert str(url) in pages
        return _Response(pages[str(url)])

    monkeypatch.setattr("requests.get", fake_get)
    packet = {
        "facts": ["集合记忆：这几天轻小说推荐集合，记录 12 个条目。"],
        "trace": {
            "selected_collection_items": [
                {
                    "index": index,
                    "title": f"推荐 {index}",
                    "detail_url": f"https://www.lightnovel.fun/cn/detail/{index}",
                    "selection_reason": "恋爱日常",
                }
                for index in range(1, 13)
            ]
        },
    }

    result = review_lightnovel_selection_from_memory_packet(
        packet=packet,
        user_message="这几天十几本里哪本最好看？",
        request_id="req-partial-scope",
        person_id="person-test",
        session_id="session-test",
    )

    assert result["status"] == "OK"
    assert result["source_card_count"] == 12
    assert result["reviewed_item_count"] == 6
    assert result["review_item_limit"] == 6
    assert result["partial_review"] is True
    assert len(result["items"]) == 6


def test_lightnovel_deep_review_uses_cached_life_episode_without_fetch(monkeypatch) -> None:  # noqa: ANN001
    packet = {
        "facts": [
            "记忆线索：我刚才推荐了 2 本轻小说；可比较证据："
            "1. 恋爱日常 A｜看点：恋爱日常｜详情：https://www.lightnovel.fun/cn/detail/1；"
            "2. 与神在河岸｜看点：奇幻身份钩子｜详情：https://www.lightnovel.fun/cn/detail/2"
        ]
    }
    cards = [
        {"index": 1, "title": "恋爱日常 A", "detail_url": "https://www.lightnovel.fun/cn/detail/1"},
        {"index": 2, "title": "与神在河岸", "detail_url": "https://www.lightnovel.fun/cn/detail/2"},
    ]
    signature = _cards_signature(cards)
    selected = {
        "index": 2,
        "title": "与神在河岸",
        "detail_url": "https://www.lightnovel.fun/cn/detail/2",
        "body_observations": ["正文主要靠对话和日常梗推进，轻喜剧感比较明显"],
    }
    items = [
        {**cards[0], "review_status": "OK", "body_observations": ["读到的正文偏恋爱日常"]},
        {**cards[1], "review_status": "OK", "body_observations": ["正文主要靠对话和日常梗推进，轻喜剧感比较明显"]},
    ]
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE life_episode (
            episode_id TEXT,
            summary TEXT,
            last_touched_ts_ms INTEGER,
            metadata TEXT,
            action TEXT,
            status TEXT,
            truth_state TEXT,
            person_id TEXT,
            session_id TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO life_episode VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ep-cached-review",
            "我翻过这组轻小说的详情和正文；当时更偏向第2本《与神在河岸》。",
            int(time.time() * 1000),
            json.dumps(
                {
                    "deep_review_cache_version": "lightnovel_deep_review:v4",
                    "recommendation_signature": signature,
                    "fact": "网页深读结果（含正文）：我回去打开详情页和正文比较了这些轻小说，当前更推荐第2本《与神在河岸》。原因：正文主要靠对话和日常梗推进，轻喜剧感比较明显。",
                    "selected": selected,
                    "items": items,
                },
                ensure_ascii=False,
            ),
            "lightnovel_recommendation_deep_review",
            "ACTIVE",
            "VERIFIED",
            "person-test",
            "session-test",
        ),
    )
    conn.commit()
    cached_row = dict(
        conn.execute(
            "SELECT episode_id, summary, last_touched_ts_ms, metadata FROM life_episode"
        ).fetchone()
    )
    fake_store = SimpleNamespace(
        persistence=SimpleNamespace(
            list_episode_records=lambda **_kwargs: [cached_row]
        )
    )
    def fail_get(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("cache hit should not fetch detail pages")

    monkeypatch.setattr("requests.get", fail_get)

    result = review_lightnovel_selection_from_memory_packet(
        packet=packet,
        user_message="这两本哪本好看？",
        request_id="req-cache",
        person_id="person-test",
        session_id="session-test",
        life_memory_store=fake_store,
    )

    assert result["status"] == "OK"
    assert result["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_CACHE_HIT"
    assert result["cached"] is True
    assert result["source"] == "cache"
    assert result["source_episode_id"] == "ep-cached-review"
    assert result["recommendation_signature"] == signature
    assert result["selected"]["title"] == "与神在河岸"


def test_lightnovel_deep_review_cache_hit_backfills_current_collection_item(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "1")
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.write_background_episode(
            event_source="codex_production",
            event_id="recommendation-source-cache-backfill",
            task_id="agent_lightnovel_recommender",
            status="COMPLETED",
            detail="给用户推荐了 2 本轻小说。",
            person_id="person-test",
            session_id="session-test",
            ts_ms=1_800_000_200_000,
            metadata={
                "action_kind": "lightnovel_recommendation_delivery",
                "selected_recommendations": [
                    {"title": "恋爱日常 A", "detail_url": "https://www.lightnovel.fun/cn/detail/cache-a"},
                    {"title": "与神在河岸", "detail_url": "https://www.lightnovel.fun/cn/detail/cache-b"},
                ],
            },
        )
        delivery_collection = next(
            row
            for row in ledger.life_memory_collection
            if _metadata_dict(row).get("exact_episode_id") == source["episode_id"]
        )
        delivery_items = sorted(
            [
                row
                for row in ledger.life_memory_collection_item
                if row["collection_id"] == delivery_collection["collection_id"]
            ],
            key=lambda row: int(row["position"] or 0),
        )
        cards = [
            {"index": 1, "title": "恋爱日常 A", "detail_url": "https://www.lightnovel.fun/cn/detail/cache-a"},
            {"index": 2, "title": "与神在河岸", "detail_url": "https://www.lightnovel.fun/cn/detail/cache-b"},
        ]
        signature = _cards_signature(cards)
        cached = store.write_background_episode(
            event_source="surface_memory_recall",
            event_id="cached-review-cache-backfill",
            status="OK",
            detail="我翻过这组轻小说的详情和正文；当时更偏向第2本《与神在河岸》。",
            person_id="person-test",
            session_id="session-test",
            ts_ms=1_800_000_200_050,
            metadata={
                "action_kind": "lightnovel_recommendation_deep_review",
                "deep_review_cache_version": "lightnovel_deep_review:v4",
                "recommendation_signature": signature,
                "fact": "网页深读结果（含正文）：当前更推荐第2本《与神在河岸》。原因：正文主要靠对话和日常梗推进，轻喜剧感比较明显。",
                "selected": {
                    **cards[1],
                    "review_status": "OK",
                    "body_observations": ["正文主要靠对话和日常梗推进，轻喜剧感比较明显"],
                },
                "items": [
                    {**cards[0], "review_status": "OK", "body_observations": ["读到的正文偏恋爱日常"]},
                    {**cards[1], "review_status": "OK", "body_observations": ["正文主要靠对话和日常梗推进，轻喜剧感比较明显"]},
                ],
            },
        )
        def fail_get(*_args, **_kwargs):  # noqa: ANN001
            raise AssertionError("cache hit should not fetch detail pages")

        monkeypatch.setattr("requests.get", fail_get)
        packet = {
            "facts": [
                "记忆线索：我刚才推荐了 2 本轻小说；可比较证据："
                "1. 恋爱日常 A｜详情：https://www.lightnovel.fun/cn/detail/cache-a；"
                "2. 与神在河岸｜详情：https://www.lightnovel.fun/cn/detail/cache-b"
            ],
            "source_refs": [f"life_memory_collection://{delivery_collection['collection_id']}"],
            "trace": {
                "selected_collection_items": [
                    {
                        "index": int(item["position"]),
                        "title": item["item_title"],
                        "detail_url": item["source_ref"],
                        "collection_id": item["collection_id"],
                        "collection_item_id": item["collection_item_id"],
                        "episode_id": item["episode_id"],
                    }
                    for item in delivery_items
                ]
            },
        }

        result = review_lightnovel_selection_from_memory_packet(
            packet=packet,
            user_message="这两本哪本好看？",
            request_id="req-cache-backfill",
            person_id="person-test",
            session_id="session-test",
            ts_ms=1_800_000_200_100,
            life_memory_store=store,
        )

        assert result["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_CACHE_HIT"
        assert result["source_episode_id"] == cached["episode_id"]
        updated_b = next(
            row
            for row in ledger.life_memory_collection_item
            if row["collection_item_id"] == delivery_items[1]["collection_item_id"]
        )
        updated_meta = _metadata_dict(updated_b)
        assert updated_meta["deep_review_episode_id"] == cached["episode_id"]
        assert "轻喜剧" in " ".join(updated_meta["body_observations"])
        assert any(
            row["episode_id"] == cached["episode_id"]
            and row["collection_item_id"] == delivery_items[1]["collection_item_id"]
            for row in ledger.life_memory_entity_mention
        )
    finally:
        ledger.close()


def test_lightnovel_deep_review_ignores_cached_404_review(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "1")
    packet = {
        "facts": [
            "记忆线索：我刚才推荐了 1 本轻小说；可比较证据："
            "1. 与神在河岸｜看点：奇幻身份钩子｜详情：https://www.lightnovel.fun/cn/detail/river"
        ]
    }
    cards = [
        {"index": 1, "title": "与神在河岸", "detail_url": "https://www.lightnovel.fun/cn/detail/river"},
    ]
    signature = _cards_signature(cards)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE life_episode (
            episode_id TEXT,
            summary TEXT,
            last_touched_ts_ms INTEGER,
            metadata TEXT,
            action TEXT,
            status TEXT,
            truth_state TEXT,
            person_id TEXT,
            session_id TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO life_episode VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ep-bad-cached-review",
            "网页深读结果（含正文）：当前更推荐第1本《404》。",
            int(time.time() * 1000),
            json.dumps(
                {
                    "deep_review_cache_version": "lightnovel_deep_review:v4",
                    "recommendation_signature": signature,
                    "fact": "网页深读结果（含正文）：当前更推荐第1本《404》。原因：简介也不是空的。",
                    "selected": {"index": 1, "title": "404", "detail_url": cards[0]["detail_url"]},
                    "items": [{**cards[0], "title": "404", "review_status": "OK"}],
                },
                ensure_ascii=False,
            ),
            "lightnovel_recommendation_deep_review",
            "ACTIVE",
            "VERIFIED",
            "person-test",
            "session-test",
        ),
    )
    conn.commit()
    fake_store = SimpleNamespace(ledger=SimpleNamespace(_conn=conn))
    def fake_get(url, **_kwargs):  # noqa: ANN001
        assert str(url) == "https://www.lightnovel.fun/cn/detail/river"
        return _Response(
            _html(
                "与神在河岸",
                "神明、河岸与现实感交叠。",
                chapters=_body("第1章 河岸。神明、河岸、闲聊和奇幻身份让这一段有轻快感。", 18),
            )
        )

    monkeypatch.setattr("requests.get", fake_get)

    result = review_lightnovel_selection_from_memory_packet(
        packet=packet,
        user_message="与神在河岸这本好看吗？",
        request_id="req-ignore-404-cache",
        person_id="person-test",
        session_id="session-test",
        life_memory_store=fake_store,
    )

    assert result["status"] == "OK"
    assert result["source"] == "fetch"
    assert result["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_OK"
    assert result["selected"]["title"] == "与神在河岸"
    assert "404" not in result["fact"]


def test_lightnovel_deep_review_ignores_cached_failed_review(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "1")
    packet = {
        "facts": [
            "记忆线索：我刚才推荐了 1 本轻小说；可比较证据："
            "1. 与神在河岸｜看点：奇幻身份钩子｜详情：https://www.lightnovel.fun/cn/detail/river"
        ]
    }
    cards = [
        {"index": 1, "title": "与神在河岸", "detail_url": "https://www.lightnovel.fun/cn/detail/river"},
    ]
    signature = _cards_signature(cards)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE life_episode (
            episode_id TEXT,
            summary TEXT,
            last_touched_ts_ms INTEGER,
            metadata TEXT,
            action TEXT,
            status TEXT,
            truth_state TEXT,
            person_id TEXT,
            session_id TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO life_episode VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ep-failed-cached-review",
            "我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。",
            int(time.time() * 1000),
            json.dumps(
                {
                    "deep_review_cache_version": "lightnovel_deep_review:v4",
                    "recommendation_signature": signature,
                    "reason_code": "LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
                    "fact": "我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。",
                    "selected": {},
                    "items": [{**cards[0], "review_status": "FAILED", "score_reasons": ["详情页未读到"]}],
                },
                ensure_ascii=False,
            ),
            "lightnovel_recommendation_deep_review",
            "ACTIVE",
            "VERIFIED",
            "person-test",
            "session-test",
        ),
    )
    conn.commit()
    fake_store = SimpleNamespace(ledger=SimpleNamespace(_conn=conn))
    def fake_get(url, **_kwargs):  # noqa: ANN001
        assert str(url) == "https://www.lightnovel.fun/cn/detail/river"
        return _Response(
            _html(
                "与神在河岸",
                "神明、河岸与现实感交叠。",
                chapters=_body("第1章 河岸。神明、河岸、闲聊和奇幻身份让这一段有轻快感。", 18),
            )
        )

    monkeypatch.setattr("requests.get", fake_get)

    result = review_lightnovel_selection_from_memory_packet(
        packet=packet,
        user_message="与神在河岸这本好看吗？",
        request_id="req-ignore-failed-cache",
        person_id="person-test",
        session_id="session-test",
        life_memory_store=fake_store,
    )

    assert result["status"] == "OK"
    assert result["source"] == "fetch"
    assert result["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_OK"
    assert result["selected"]["title"] == "与神在河岸"


def test_lightnovel_deep_review_persists_backlink_to_source_recommendation(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "0")
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.write_background_episode(
            event_source="codex_production",
            event_id="recommendation-source",
            task_id="agent_lightnovel_recommender",
            status="COMPLETED",
            detail="给用户推荐了 2 本轻小说。",
            person_id="person-test",
            session_id="session-test",
            ts_ms=1_800_000_000_000,
            metadata={
                "action_kind": "lightnovel_recommendation_delivery",
                "selected_recommendations": [
                    {"title": "恋爱日常 A", "detail_url": "https://www.lightnovel.fun/cn/detail/1"},
                    {"title": "与神在河岸", "detail_url": "https://www.lightnovel.fun/cn/detail/2"},
                ],
            },
        )
        pages = {
            "https://www.lightnovel.fun/cn/detail/1": _html(
                "恋爱日常 A",
                "关系展开偏恋爱日常。",
                chapters=_body("第1章 晚安电话。备用钥匙、同居感和撒娇让人物关系推进。", 20),
            ),
            "https://www.lightnovel.fun/cn/detail/2": _html(
                "与神在河岸",
                "奇幻身份钩子更明确。",
                chapters=_body("第1章 河岸。异世界、皇女、魔法和神明让这一段更有奇幻钩子。", 28),
            ),
        }

        def fake_get(url, **_kwargs):  # noqa: ANN001
            return _Response(pages[str(url)])

        monkeypatch.setattr("requests.get", fake_get)
        packet = {
            "facts": [
                "集合记忆：本次轻小说推荐集合，记录 2 个条目；可比较证据："
                "1. 恋爱日常 A｜详情：https://www.lightnovel.fun/cn/detail/1；"
                "2. 与神在河岸｜详情：https://www.lightnovel.fun/cn/detail/2"
            ],
            "source_refs": ["life_memory_collection://col-test"],
            "trace": {
                "selected_collection_items": [
                    {
                        "index": 1,
                        "title": "恋爱日常 A",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/1",
                        "collection_id": "col-test",
                        "collection_item_id": "coli-a",
                        "episode_id": source["episode_id"],
                    },
                    {
                        "index": 2,
                        "title": "与神在河岸",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/2",
                        "collection_id": "col-test",
                        "collection_item_id": "coli-b",
                        "episode_id": source["episode_id"],
                    },
                ]
            },
        }

        result = review_lightnovel_selection_from_memory_packet(
            packet=packet,
            user_message="这两本哪本好看？",
            request_id="req-backlink",
            person_id="person-test",
            session_id="session-test",
            ts_ms=1_800_000_000_100,
            life_memory_store=store,
        )

        review_episode_id = str(result.get("persisted_episode_id") or "")
        assert result["status"] == "OK"
        assert review_episode_id
        review_row = next(row for row in ledger.life_episode if row["episode_id"] == review_episode_id)
        metadata = review_row["metadata"]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        assert source["episode_id"] in metadata["source_episode_ids"]
        assert "col-test" in metadata["source_collection_ids"]
        assert "coli-a" in metadata["source_collection_item_ids"]
        assert any("life_memory_collection://col-test" == ref for ref in metadata["evidence_refs"])
        assert any(
            row["source_episode_id"] == review_episode_id
            and row["target_episode_id"] == source["episode_id"]
            and row["link_kind"] == "detail_review_of"
            for row in ledger.life_episode_link
        )
    finally:
        ledger.close()


def test_lightnovel_deep_review_writes_body_evidence_back_to_collection_entity(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED", "0")
    ledger = SQLiteLedger(db_path=":memory:")
    try:
        store = LifeMemoryStore(ledger=ledger)
        source = store.write_background_episode(
            event_source="codex_production",
            event_id="recommendation-source-real-items",
            task_id="agent_lightnovel_recommender",
            status="COMPLETED",
            detail="给用户推荐了 2 本轻小说。",
            person_id="person-test",
            session_id="session-test",
            ts_ms=1_800_000_100_000,
            metadata={
                "action_kind": "lightnovel_recommendation_delivery",
                "selected_recommendations": [
                    {
                        "title": "恋爱日常 A",
                        "selection_reason": "恋爱日常",
                        "intro_snippet": "关系展开偏恋爱日常。",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/a",
                    },
                    {
                        "title": "与神在河岸",
                        "selection_reason": "奇幻身份钩子",
                        "intro_snippet": "河岸和神明的奇幻故事。",
                        "detail_url": "https://www.lightnovel.fun/cn/detail/b",
                    },
                ],
            },
        )
        delivery_collection = next(
            row
            for row in ledger.life_memory_collection
            if _metadata_dict(row).get("exact_episode_id") == source["episode_id"]
        )
        delivery_items = sorted(
            [
                row
                for row in ledger.life_memory_collection_item
                if row["collection_id"] == delivery_collection["collection_id"]
            ],
            key=lambda row: int(row["position"] or 0),
        )
        pages = {
            "https://www.lightnovel.fun/cn/detail/a": _html(
                "恋爱日常 A",
                "关系展开偏恋爱日常。",
                chapters=_body("第1章 晚安电话。备用钥匙和同居感让人物关系推进。", 18),
            ),
            "https://www.lightnovel.fun/cn/detail/b": _html(
                "与神在河岸",
                "奇幻身份钩子更明确。",
                chapters=_body("第1章 河岸。异世界、皇女、皇帝、魔法和神明让这一段更有奇幻钩子。", 30),
            ),
        }

        def fake_get(url, **_kwargs):  # noqa: ANN001
            return _Response(pages[str(url)])

        monkeypatch.setattr("requests.get", fake_get)
        packet = {
            "facts": [
                "集合记忆：本次轻小说推荐集合，记录 2 个条目；可比较证据："
                "1. 恋爱日常 A｜详情：https://www.lightnovel.fun/cn/detail/a；"
                "2. 与神在河岸｜详情：https://www.lightnovel.fun/cn/detail/b"
            ],
            "source_refs": [f"life_memory_collection://{delivery_collection['collection_id']}"],
            "trace": {
                "selected_collection_items": [
                    {
                        "index": int(item["position"]),
                        "title": item["item_title"],
                        "detail_url": item["source_ref"],
                        "collection_id": item["collection_id"],
                        "collection_item_id": item["collection_item_id"],
                        "episode_id": item["episode_id"],
                    }
                    for item in delivery_items
                ]
            },
        }

        result = review_lightnovel_selection_from_memory_packet(
            packet=packet,
            user_message="这两本哪本好看？",
            request_id="req-body-backfill",
            person_id="person-test",
            session_id="session-test",
            ts_ms=1_800_000_100_100,
            life_memory_store=store,
        )

        assert result["status"] == "OK"
        review_episode_id = str(result.get("persisted_episode_id") or "")
        assert review_episode_id
        updated_b = next(
            row
            for row in ledger.life_memory_collection_item
            if row["collection_item_id"] == delivery_items[1]["collection_item_id"]
        )
        updated_meta = _metadata_dict(updated_b)
        assert updated_meta["deep_review_episode_id"] == review_episode_id
        assert "异世界" in " ".join(updated_meta["body_observations"])
        assert updated_meta["detail_evidence_ref"] == result["persisted_primary_ref"]
        assert any(
            row["episode_id"] == review_episode_id
            and row["collection_item_id"] == delivery_items[1]["collection_item_id"]
            for row in ledger.life_memory_entity_mention
        )

        entity_packet = store.recall_entity_packet(
            query="与神在河岸 这本好看吗？",
            top_k=3,
            person_id="person-test",
            session_id="session-test",
            now_ts_ms_value=1_800_000_101_000,
        )
        assert entity_packet.candidates
        entity_text = entity_packet.candidates[0]["text"]
        assert "深读" in entity_text
        assert "奇幻" in entity_text
    finally:
        ledger.close()
