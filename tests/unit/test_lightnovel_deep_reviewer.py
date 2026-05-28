from __future__ import annotations

import json
import sqlite3
import time
from types import SimpleNamespace

from host_brain.codex_runtime.lightnovel_deep_reviewer import (
    _cards_signature,
    _extract_cards,
    review_lightnovel_selection_from_memory_packet,
    should_deep_review_lightnovel_selection,
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
    assert should_deep_review_lightnovel_selection(
        user_message="这三本哪本好看？",
        packet={"facts": ["记忆线索：可比较证据：1. A｜详情：https://www.lightnovel.fun/cn/detail/1"]},
    )
    assert not should_deep_review_lightnovel_selection(
        user_message="今天聊点别的",
        packet={"facts": ["记忆线索：可比较证据：1. A｜详情：https://www.lightnovel.fun/cn/detail/1"]},
    )


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
                    "deep_review_cache_version": "lightnovel_deep_review:v3",
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
    fake_store = SimpleNamespace(ledger=SimpleNamespace(_conn=conn))
    monkeypatch.setattr("host_brain.app.dependencies.get_life_memory_store", lambda: fake_store)

    def fail_get(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError("cache hit should not fetch detail pages")

    monkeypatch.setattr("requests.get", fail_get)

    result = review_lightnovel_selection_from_memory_packet(
        packet=packet,
        user_message="这两本哪本好看？",
        request_id="req-cache",
        person_id="person-test",
        session_id="session-test",
    )

    assert result["status"] == "OK"
    assert result["reason_code"] == "LIGHTNOVEL_DEEP_REVIEW_CACHE_HIT"
    assert result["cached"] is True
    assert result["source"] == "cache"
    assert result["source_episode_id"] == "ep-cached-review"
    assert result["recommendation_signature"] == signature
    assert result["selected"]["title"] == "与神在河岸"
