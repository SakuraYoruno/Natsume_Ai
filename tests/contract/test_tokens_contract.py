import json
import unittest
from pathlib import Path

from host_brain.core.errors import CONFIRM_REQUIRED, TOKEN_INVALID
from host_brain.governor.tokens import TokenService


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "tokens"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestTokenContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.svc = TokenService(approval_ttl_ms=1000, confirm_ttl_ms=1000, undo_ttl_ms=2000)

    def test_t34_approval_token_binding(self) -> None:
        spec = _load_json(FIXTURES / "good" / "approval_bound.json")
        approval = self.svc.issue_approval_token(now_ts_ms=self.now_ms, **spec)
        result = self.svc.validate_bound_token(
            approval,
            expected_type="approval",
            intent_id=spec["intent_id"],
            skill_name=spec["skill_name"],
            session_id=spec["session_id"],
            seq=spec["seq"],
            now_ts_ms=self.now_ms,
            consume=False,
        )
        self.assertTrue(result.valid)

    def test_t35_confirm_required_when_high_risk(self) -> None:
        spec = _load_json(FIXTURES / "good" / "approval_bound.json")
        approval = self.svc.issue_approval_token(now_ts_ms=self.now_ms, **spec)
        result = self.svc.validate_execution_tokens(
            intent_id=spec["intent_id"],
            skill_name=spec["skill_name"],
            session_id=spec["session_id"],
            seq=spec["seq"],
            approval_token_ref=approval,
            confirm_token_ref=None,
            require_confirm=True,
            now_ts_ms=self.now_ms,
        )
        self.assertFalse(result.valid)
        self.assertEqual(CONFIRM_REQUIRED, result.reason_code)

    def test_t36_forged_token_rejected(self) -> None:
        spec = _load_json(FIXTURES / "bad" / "forged_token.json")
        result = self.svc.validate_bound_token(
            spec["approval_token_ref"],
            expected_type="approval",
            intent_id=spec["intent_id"],
            skill_name=spec["skill_name"],
            session_id=spec["session_id"],
            seq=spec["seq"],
            now_ts_ms=self.now_ms,
            consume=False,
        )
        self.assertFalse(result.valid)
        self.assertEqual(TOKEN_INVALID, result.reason_code)

    def test_t37_expired_token_rejected(self) -> None:
        spec = _load_json(FIXTURES / "bad" / "expired_token.json")
        approval = self.svc.issue_approval_token(
            intent_id=spec["intent_id"],
            skill_name=spec["skill_name"],
            session_id=spec["session_id"],
            seq=spec["seq"],
            now_ts_ms=spec["issued_ts_ms"],
        )
        result = self.svc.validate_bound_token(
            approval,
            expected_type="approval",
            intent_id=spec["intent_id"],
            skill_name=spec["skill_name"],
            session_id=spec["session_id"],
            seq=spec["seq"],
            now_ts_ms=spec["issued_ts_ms"] + 2001,
            consume=False,
        )
        self.assertFalse(result.valid)
        self.assertEqual(TOKEN_INVALID, result.reason_code)

    def test_t38_reused_token_rejected(self) -> None:
        spec = _load_json(FIXTURES / "good" / "approval_bound.json")
        approval = self.svc.issue_approval_token(now_ts_ms=self.now_ms, **spec)
        first = self.svc.validate_bound_token(
            approval,
            expected_type="approval",
            intent_id=spec["intent_id"],
            skill_name=spec["skill_name"],
            session_id=spec["session_id"],
            seq=spec["seq"],
            now_ts_ms=self.now_ms,
            consume=True,
        )
        second = self.svc.validate_bound_token(
            approval,
            expected_type="approval",
            intent_id=spec["intent_id"],
            skill_name=spec["skill_name"],
            session_id=spec["session_id"],
            seq=spec["seq"],
            now_ts_ms=self.now_ms,
            consume=False,
        )
        self.assertTrue(first.valid)
        self.assertFalse(second.valid)
        self.assertEqual(TOKEN_INVALID, second.reason_code)
