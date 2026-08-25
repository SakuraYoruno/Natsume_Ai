import unittest

from host_brain.app.observability import build_request_observability_report
from host_brain.audit.sqlite_ledger import SQLiteLedger


class TestObservabilityTimelineContract(unittest.TestCase):
    def test_t_obs_01_request_timeline_is_readonly_and_aggregated(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        request_id = "req_obs_001"

        ledger.log_intent(
            {
                "request_id": request_id,
                "thread_id": "thread_obs_001",
                "turn_id": "turn_obs_001",
                "intent_id": "intent_obs_001",
                "session_id": "sess_obs_001",
                "seq": 1,
                "ts_ms": 1_800_040_000_001,
                "nonce": "nonce_obs_001",
                "parse_stage": "S_INTENT_PARSE",
                "raw_text": "hello",
                "normalized_json": {"id": "intent_obs_001"},
                "proposer_node_id": "core_reasoner_primary",
                "model_snapshot": "test-model",
                "prompt_profile_id": "default",
                "tool_visibility_hash": "",
                "proposed_intent_draft_json": {"id": "intent_obs_001"},
                "lint_result": {"ok": True},
            }
        )
        ledger.log_governor(
            {
                "request_id": request_id,
                "thread_id": "thread_obs_001",
                "turn_id": "turn_obs_001",
                "intent_id": "intent_obs_001",
                "decision": "APPROVE",
                "reason_code": None,
                "rule_trace": [{"gate_id": "R1", "hit": False}],
                "tokens": {"approval_token_ref": "approval::obs"},
                "snapshot_refs": {"hard_state_ref": "hard::obs"},
                "stage": "S_GOVERNOR",
                "ts_ms": 1_800_040_000_002,
            }
        )
        ledger.log_skill_exec(
            {
                "request_id": request_id,
                "thread_id": "thread_obs_001",
                "turn_id": "turn_obs_001",
                "intent_id": "intent_obs_001",
                "event_id": "event_obs_001",
                "skill_name": "read_only_status",
                "approval_token_ref": "approval::obs",
                "confirm_token_ref": None,
                "undo_token_ref": None,
                "result_status": "SUCCESS",
                "result_code": "TOOL_EXECUTED",
                "result_digest": "ok",
                "evidence_ref": "evidence::obs",
                "manifest_ref": None,
                "ts_ms": 1_800_040_000_003,
            }
        )
        ledger.log_error(
            request_id=request_id,
            stage="S_QA",
            reason_code="QA_OUTPUT_INVALID",
            detail="invalid output",
        )
        ledger.log_channel_io(
            {
                "request_id": request_id,
                "channel": "wechat",
                "direction": "EGRESS",
                "thread_key": "wechat::u::bot",
                "message_id": "msg_obs_001",
                "user_id": "u",
                "status": "SENT",
                "reason_code": None,
                "stage": "S_CHANNEL_EGRESS",
                "detail": "message sent",
                "payload_digest": "sha256:obs",
                "ts_ms": 1_800_040_000_004,
            }
        )
        ledger.log_voice_event(
            {
                "request_id": request_id,
                "session_id": "sess_obs_001",
                "event_type": "SPEAK",
                "direction": "HOST_TO_EDGE",
                "seq": 2,
                "status": "ACCEPTED",
                "reason_code": None,
                "stage": "S_VOICE_TTS",
                "payload": {"text": "ok"},
                "detail": "speech accepted",
                "ts_ms": 1_800_040_000_005,
            }
        )

        report = build_request_observability_report(request_id=request_id, ledger=ledger)

        self.assertEqual(request_id, report["request_id"])
        self.assertEqual("COMPLETE", report["trace_status"])
        self.assertEqual(6, report["timeline_total"])
        self.assertEqual(
            {
                "channel_io_log": 1,
                "error_log": 1,
                "governor_log": 1,
                "intent_log": 1,
                "skill_exec_log": 1,
                "voice_event_log": 1,
            },
            report["sources"],
        )

        timeline = report["timeline"]
        self.assertEqual(6, len(timeline))
        self.assertListEqual(
            [
                "error_log",
                "intent_log",
                "governor_log",
                "skill_exec_log",
                "channel_io_log",
                "voice_event_log",
            ],
            [row["source"] for row in timeline],
        )

        ledger.close()

    def test_t_obs_01_requires_request_id(self) -> None:
        ledger = SQLiteLedger(db_path=":memory:")
        with self.assertRaises(ValueError):
            build_request_observability_report(request_id="", ledger=ledger)
        ledger.close()


if __name__ == "__main__":
    unittest.main()
