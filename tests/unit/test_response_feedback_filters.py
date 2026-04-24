from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.app.response_feedback_store import (
    detect_explicit_response_feedback,
    record_feedback_for_latest_persona_reply,
)


def _log_persona_reply(
    ledger: SQLiteLedger,
    *,
    request_id: str,
    session_id: str,
    person_id: str,
    reply_preview: str,
    ts_ms: int = 1000,
) -> None:
    ledger.log_voice_event(
        {
            "request_id": request_id,
            "session_id": session_id,
            "event_type": "INPUT_COMPLETED",
            "direction": "HOST_INTERNAL",
            "status": "OK",
            "reason_code": None,
            "stage": "chat_agent.default",
            "detail": "persona reply",
            "payload": {
                "source": "GATEWAY",
                "person_id": person_id,
                "input_preview": "我问你就说吗，我们不熟吧",
                "reply_preview": reply_preview,
            },
            "ts_ms": ts_ms,
        }
    )


def _mark_bad_reply(
    ledger: SQLiteLedger,
    *,
    session_id: str,
    person_id: str,
    user_message: str = "这句话太模板了，不像她",
) -> dict[str, object]:
    result = record_feedback_for_latest_persona_reply(
        session_id=session_id,
        person_id=person_id,
        user_message=user_message,
        ledger=ledger,
        ts_ms=2000,
    )
    assert result is not None
    return result


def test_record_feedback_marks_latest_persona_reply_only() -> None:
    ledger = SQLiteLedger(":memory:", check_same_thread=False)
    try:
        _log_persona_reply(
            ledger,
            request_id="req_badcase_001",
            session_id="qq_dm_badcase_001",
            person_id="person_badcase_001",
            reply_preview="我们并不熟。我只是觉得，既然你遇到了麻烦，能帮一把就帮一把。",
        )

        result = _mark_bad_reply(
            ledger,
            session_id="qq_dm_badcase_001",
            person_id="person_badcase_001",
        )
        assert result["target_request_id"] == "req_badcase_001"
        assert float(result["feedback_score"]) == -1.0
        assert "template_like" in list(result["feedback_tags"])
        assert "out_of_persona" in list(result["feedback_tags"])
        rows = ledger._conn.execute("SELECT COUNT(*) AS c FROM response_feedback_log").fetchone()
        assert int(rows["c"]) == 1
    finally:
        ledger.close()


def test_detect_explicit_response_feedback_only_marks_explicit_negative_lines() -> None:
    detected = detect_explicit_response_feedback("这句话太模板了，不像她")
    assert detected is not None
    assert float(detected["feedback_score"]) == -1.0
    assert "template_like" in list(detected["feedback_tags"])
    assert "out_of_persona" in list(detected["feedback_tags"])
