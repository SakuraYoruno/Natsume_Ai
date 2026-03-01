import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.vts import (
    S_AVATAR_EXPRESSION_MAP,
    S_AVATAR_TALK_GATE,
    STATUS_APPLIED,
    STATUS_REJECTED,
    TALK_END,
    TALK_START,
    request_talk_gate,
    resolve_vts_expression,
)
from host_brain.core.errors import AVATAR_FREE_ACTION_BLOCKED, AVATAR_TAG_INVALID


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "channels"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestAvatarContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_100_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_avatar_01_talk_gate_is_deterministic_and_auditable(self) -> None:
        start = request_talk_gate(
            action=TALK_START,
            ledger=self.ledger,
            request_id="req_avatar_gate_001",
            ts_ms=self.now_ms,
        )
        end = request_talk_gate(
            action=TALK_END,
            ledger=self.ledger,
            request_id="req_avatar_gate_002",
            ts_ms=self.now_ms + 1,
        )

        self.assertTrue(start.success)
        self.assertEqual(STATUS_APPLIED, start.status)
        self.assertEqual("talk_gate::talk_start", start.expression_id)
        self.assertEqual(S_AVATAR_TALK_GATE, start.stage)

        self.assertTrue(end.success)
        self.assertEqual(STATUS_APPLIED, end.status)
        self.assertEqual("talk_gate::talk_end", end.expression_id)
        self.assertEqual(S_AVATAR_TALK_GATE, end.stage)

        self.assertEqual(2, len(self.ledger.avatar_action_log))
        self.assertEqual("req_avatar_gate_001", self.ledger.avatar_action_log[0]["request_id"])
        self.assertEqual("req_avatar_gate_002", self.ledger.avatar_action_log[1]["request_id"])

    def test_t_avatar_01_emotion_map_is_fixed_and_replayable(self) -> None:
        payload = _load_json(FIXTURES / "good" / "vts_emotion_valid.json")
        first = resolve_vts_expression(
            emotion_tag=payload["emotion_tag"],
            ledger=self.ledger,
            request_id=payload["request_id"],
            trigger=payload["trigger"],
            ts_ms=self.now_ms + 2,
        )
        second = resolve_vts_expression(
            emotion_tag=payload["emotion_tag"],
            ledger=self.ledger,
            request_id="req_avatar_001_repeat",
            trigger=payload["trigger"],
            ts_ms=self.now_ms + 3,
        )

        self.assertTrue(first.success)
        self.assertEqual(STATUS_APPLIED, first.status)
        self.assertEqual("HAPPY", first.emotion_tag)
        self.assertEqual(first.expression_id, second.expression_id)
        self.assertEqual(S_AVATAR_EXPRESSION_MAP, first.stage)

        rows = self.ledger.avatar_action_log
        self.assertEqual(2, len(rows))
        self.assertEqual("HAPPY", rows[0]["emotion_tag"])
        self.assertEqual(rows[0]["expression_id"], rows[1]["expression_id"])
        self.assertEqual(S_AVATAR_EXPRESSION_MAP, rows[0]["stage"])

    def test_t_avatar_01_invalid_tag_and_free_action_attempt_are_rejected(self) -> None:
        bad_tag = _load_json(FIXTURES / "bad" / "vts_invalid_tag.json")
        free_action = _load_json(FIXTURES / "bad" / "vts_free_action_attempt.json")

        invalid = resolve_vts_expression(
            emotion_tag=bad_tag["emotion_tag"],
            ledger=self.ledger,
            request_id=bad_tag["request_id"],
            trigger=bad_tag["trigger"],
            ts_ms=self.now_ms + 4,
        )
        blocked = resolve_vts_expression(
            emotion_tag=free_action["emotion_tag"],
            ledger=self.ledger,
            request_id=free_action["request_id"],
            trigger=free_action["trigger"],
            raw_expression_id=free_action["raw_expression_id"],
            raw_action_params=free_action["raw_action_params"],
            ts_ms=self.now_ms + 5,
        )

        self.assertFalse(invalid.success)
        self.assertEqual(STATUS_REJECTED, invalid.status)
        self.assertEqual(AVATAR_TAG_INVALID, invalid.reason_code)
        self.assertEqual(S_AVATAR_EXPRESSION_MAP, invalid.stage)

        self.assertFalse(blocked.success)
        self.assertEqual(STATUS_REJECTED, blocked.status)
        self.assertEqual(AVATAR_FREE_ACTION_BLOCKED, blocked.reason_code)
        self.assertEqual(S_AVATAR_EXPRESSION_MAP, blocked.stage)

        self.assertEqual(2, len(self.ledger.avatar_action_log))
        first, second = self.ledger.avatar_action_log
        self.assertEqual(AVATAR_TAG_INVALID, first["reason_code"])
        self.assertEqual(AVATAR_FREE_ACTION_BLOCKED, second["reason_code"])


if __name__ == "__main__":
    unittest.main()
