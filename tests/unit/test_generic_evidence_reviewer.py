from __future__ import annotations

from pathlib import Path

from host_brain.codex_runtime.generic_evidence_reviewer import (
    review_generic_evidence_from_memory_packet,
    should_review_generic_evidence,
)


class _Response:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text


def _packet_with_source(source_ref: str) -> dict:
    return {
        "facts": [],
        "rows": [
            {
                "fact": "昨天查过一个网页报告，旧摘要只说看起来还行。",
                "store_id": "life_episode",
                "source_ref": source_ref,
            }
        ],
        "trace": {
            "plan": {"query_kind": "evidence_review", "target_domains": ["web", "content"]},
            "recall_answer_readiness": {
                "need_type": "evidence_review",
                "response_mode": "fetch_or_request_detail_evidence",
                "surface_action": "fetch_more_evidence",
            },
        },
    }


def test_generic_evidence_review_fetches_web_body(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_GENERIC_EVIDENCE_REVIEW_PERSIST_ENABLED", "0")

    def fake_get(url, **_kwargs):  # noqa: ANN001
        assert url == "https://example.com/report"
        return _Response(
            """
            <html><head><title>样本报告</title></head>
            <body><script>ignore()</script><h1>样本报告</h1>
            <p>报告正文列出了样本来源、更新时间和限制条件。</p>
            <p>结论只适用于这个小样本，不应该外推。</p></body></html>
            """
        )

    monkeypatch.setattr("requests.get", fake_get)
    packet = _packet_with_source("https://example.com/report")

    assert should_review_generic_evidence(user_message="昨天那个网页内容靠谱吗？依据是什么？", packet=packet)
    result = review_generic_evidence_from_memory_packet(
        packet=packet,
        user_message="昨天那个网页内容靠谱吗？依据是什么？",
        request_id="req-web",
        person_id="person-1",
        session_id="sess-1",
    )

    assert result["status"] == "OK"
    assert result["reason_code"] == "GENERIC_EVIDENCE_REVIEW_OK"
    assert result["evidence_refs"] == ["https://example.com/report"]
    assert "样本来源、更新时间和限制条件" in result["fact"]
    assert result["items"][0]["title"] == "样本报告"
    assert result["persist_status"] == "SKIPPED"


def test_generic_evidence_review_reads_safe_local_text_file(monkeypatch, tmp_path: Path) -> None:  # noqa: ANN001
    monkeypatch.setenv("HOST_GENERIC_EVIDENCE_REVIEW_PERSIST_ENABLED", "0")
    note = tmp_path / "report.md"
    note.write_text("# 本地报告\n\n正文写明：这个结论来自离线文件，有明确时间和来源。", encoding="utf-8")
    packet = _packet_with_source(str(note))

    assert should_review_generic_evidence(user_message="那个文件内容依据是什么？", packet=packet)
    result = review_generic_evidence_from_memory_packet(
        packet=packet,
        user_message="那个文件内容依据是什么？",
        request_id="req-file",
        person_id="person-1",
        session_id="sess-1",
    )

    assert result["status"] == "OK"
    assert result["items"][0]["source"] == "file"
    assert "离线文件" in result["fact"]


def test_generic_evidence_review_does_not_take_lightnovel_packets() -> None:
    packet = {
        "facts": [],
        "rows": [{"fact": "轻小说推荐集合。", "source_ref": "https://www.lightnovel.fun/cn/detail/1"}],
        "trace": {
            "plan": {"query_kind": "anaphoric_selection", "target_domains": ["lightnovel_recommendation"]},
            "recall_answer_readiness": {
                "need_type": "collection_selection",
                "response_mode": "fetch_or_request_detail_evidence",
                "surface_action": "fetch_more_evidence",
            },
        },
    }

    assert not should_review_generic_evidence(user_message="这本好看吗？", packet=packet)
