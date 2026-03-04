import json
import unittest
from pathlib import Path

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.channels.wechat import (
    DIRECTION_EGRESS,
    DIRECTION_INGRESS,
    S_CHANNEL_EGRESS,
    S_CHANNEL_INGRESS,
    STATUS_DUPLICATE_IGNORED,
    STATUS_FAILED,
    STATUS_RECEIVED,
    receive_wechat_event,
    send_wechat_message,
)
from host_brain.core.errors import CHANNEL_DUPLICATE_MESSAGE, CHANNEL_SEND_FAILED


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "channels"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestChannelContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t_channel_01_ingress_traceable_and_duplicate_ignored(self) -> None:
        first = _load_json(FIXTURES / "good" / "wechat_ingress_message.json")
        duplicate = _load_json(FIXTURES / "bad" / "wechat_duplicate_message.json")

        first_result = receive_wechat_event(
            first,
            ledger=self.ledger,
            request_id="req_channel_001",
            ts_ms=self.now_ms,
        )
        self.assertTrue(first_result.accepted)
        self.assertFalse(first_result.duplicate)
        self.assertEqual(STATUS_RECEIVED, first_result.status)
        self.assertEqual("req_channel_001", first_result.request_id)

        dup_result = receive_wechat_event(
            duplicate,
            ledger=self.ledger,
            request_id="req_channel_002",
            ts_ms=self.now_ms + 1,
        )
        self.assertFalse(dup_result.accepted)
        self.assertTrue(dup_result.duplicate)
        self.assertEqual(STATUS_DUPLICATE_IGNORED, dup_result.status)
        self.assertEqual(CHANNEL_DUPLICATE_MESSAGE, dup_result.reason_code)

        self.assertEqual(2, len(self.ledger.channel_io_log))
        first_row = self.ledger.channel_io_log[0]
        dup_row = self.ledger.channel_io_log[1]

        self.assertEqual(DIRECTION_INGRESS, first_row["direction"])
        self.assertEqual("req_channel_001", first_row["request_id"])
        self.assertEqual(STATUS_RECEIVED, first_row["status"])
        self.assertEqual(S_CHANNEL_INGRESS, first_row["stage"])
        self.assertEqual("wx_msg_001", first_row["message_id"])
        self.assertTrue(str(first_row["payload_digest"]).startswith("sha256:"))

        self.assertEqual(DIRECTION_INGRESS, dup_row["direction"])
        self.assertEqual("req_channel_002", dup_row["request_id"])
        self.assertEqual(STATUS_DUPLICATE_IGNORED, dup_row["status"])
        self.assertEqual(CHANNEL_DUPLICATE_MESSAGE, dup_row["reason_code"])
        self.assertEqual(S_CHANNEL_INGRESS, dup_row["stage"])
        self.assertEqual(first_row["thread_key"], dup_row["thread_key"])

    def test_t_channel_01_egress_failure_ack_has_reason_and_stage(self) -> None:
        result = send_wechat_message(
            thread_key="wechat::wx_user_1001::Natsume_bot",
            message_id="wx_out_001",
            content="测试回复",
            ledger=self.ledger,
            request_id="req_channel_003",
            user_id="wx_user_1001",
            ack_success=False,
            reason_code=CHANNEL_SEND_FAILED,
            stage=S_CHANNEL_EGRESS,
            detail="wechat api timeout",
            ts_ms=self.now_ms + 2,
        )
        self.assertFalse(result.success)
        self.assertEqual(STATUS_FAILED, result.status)
        self.assertEqual(CHANNEL_SEND_FAILED, result.reason_code)
        self.assertEqual("req_channel_003", result.request_id)

        self.assertEqual(1, len(self.ledger.channel_io_log))
        row = self.ledger.channel_io_log[0]
        self.assertEqual(DIRECTION_EGRESS, row["direction"])
        self.assertEqual("req_channel_003", row["request_id"])
        self.assertEqual("wechat::wx_user_1001::Natsume_bot", row["thread_key"])
        self.assertEqual("wx_out_001", row["message_id"])
        self.assertEqual(STATUS_FAILED, row["status"])
        self.assertEqual(CHANNEL_SEND_FAILED, row["reason_code"])
        self.assertEqual(S_CHANNEL_EGRESS, row["stage"])
        self.assertEqual("wechat api timeout", row["detail"])
        self.assertTrue(str(row["payload_digest"]).startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
