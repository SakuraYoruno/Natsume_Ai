import unittest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import CONFIRM_REQUIRED, SKILL_TOKEN_MISSING, UNDO_REQUIRED
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry


def _read_only_status(args: dict, _context: dict | None) -> dict:
    return {"status": "ok", "scope": args.get("scope", "unknown")}


def _high_risk_write(args: dict, _context: dict | None) -> dict:
    return {"status": "written", "manifest_ref": f"manifest::{args.get('scope', 'default')}"}


def _high_risk_write_no_manifest(_args: dict, _context: dict | None) -> dict:
    return {"status": "written"}


class TestSkillLoaderContract(unittest.TestCase):
    def setUp(self) -> None:
        self.now_ms = 1_800_000_000_000
        self.ledger = SQLiteLedger(db_path=":memory:")
        self.tokens = TokenService(approval_ttl_ms=20_000, confirm_ttl_ms=20_000, undo_ttl_ms=20_000)
        self.registry = SkillRegistry()
        self.registry.register(
            SkillMeta(
                skill_name="read_only_status",
                clearance="LOW",
                risk_level=1,
                scope="host",
                reversible=False,
                handler=_read_only_status,
            )
        )
        self.registry.register(
            SkillMeta(
                skill_name="high_risk_write",
                clearance="HIGH",
                risk_level=4,
                scope="system",
                reversible=True,
                require_confirm=True,
                handler=_high_risk_write,
            )
        )
        self.registry.register(
            SkillMeta(
                skill_name="high_risk_write_no_manifest",
                clearance="HIGH",
                risk_level=4,
                scope="system",
                reversible=True,
                require_confirm=True,
                handler=_high_risk_write_no_manifest,
            )
        )
        self.loader = SkillLoader(registry=self.registry, token_service=self.tokens, ledger=self.ledger)

    def test_t_skill_01_success_has_evidence_ref_and_approval_ref(self) -> None:
        intent = {
            "id": "intent_skill_001",
            "session_id": "session_skill",
            "seq": 1,
            "action": "read_only_status",
            "args": {"scope": "system"},
        }
        approval = self.tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        governor_output = {
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": approval},
        }

        result = self.loader.execute(intent=intent, governor_output=governor_output, now_ts_ms=self.now_ms)
        self.assertEqual("SUCCESS", result.status)
        self.assertTrue(result.evidence_ref and result.evidence_ref.startswith("evidence::"))
        self.assertEqual(approval, result.detail["approval_token_ref"])

    def test_t15_missing_approval_token_ref_rejected(self) -> None:
        intent = {
            "id": "intent_skill_002",
            "session_id": "session_skill",
            "seq": 2,
            "action": "read_only_status",
            "args": {"scope": "system"},
        }
        governor_output = {"decision": "APPROVE", "tokens": {}}
        result = self.loader.execute(intent=intent, governor_output=governor_output, now_ts_ms=self.now_ms)
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(SKILL_TOKEN_MISSING, result.result_code)

    def test_t34_to_t38_high_risk_requires_confirm(self) -> None:
        intent = {
            "id": "intent_skill_003",
            "session_id": "session_skill",
            "seq": 3,
            "action": "high_risk_write",
            "args": {"scope": "critical"},
        }
        approval = self.tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        governor_output = {"decision": "APPROVE", "tokens": {"approval_token_ref": approval}}
        result = self.loader.execute(intent=intent, governor_output=governor_output, now_ts_ms=self.now_ms)
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(CONFIRM_REQUIRED, result.result_code)

    def test_idempotent_same_intent_id_does_not_execute_twice(self) -> None:
        intent = {
            "id": "intent_skill_004",
            "session_id": "session_skill",
            "seq": 4,
            "action": "read_only_status",
            "args": {"scope": "system"},
        }

        approval_1 = self.tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        first = self.loader.execute(
            intent=intent,
            governor_output={"decision": "APPROVE", "tokens": {"approval_token_ref": approval_1}},
            now_ts_ms=self.now_ms,
        )

        approval_2 = self.tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        second = self.loader.execute(
            intent=intent,
            governor_output={"decision": "APPROVE", "tokens": {"approval_token_ref": approval_2}},
            now_ts_ms=self.now_ms,
        )

        self.assertEqual("SUCCESS", first.status)
        self.assertEqual("IDEMPOTENT_REPLAY", second.status)
        self.assertEqual("NOOP_ALREADY_EXECUTED", second.result_code)

    def test_reversible_execution_requires_manifest_ref(self) -> None:
        intent = {
            "id": "intent_skill_005",
            "session_id": "session_skill",
            "seq": 5,
            "action": "high_risk_write_no_manifest",
            "args": {"scope": "critical"},
        }
        approval = self.tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        confirm = self.tokens.issue_confirm_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        result = self.loader.execute(
            intent=intent,
            governor_output={
                "decision": "APPROVE",
                "tokens": {
                    "approval_token_ref": approval,
                    "confirm_token_ref": confirm,
                },
            },
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("REJECTED", result.status)
        self.assertEqual(UNDO_REQUIRED, result.result_code)

    def test_reversible_execution_with_manifest_ref_succeeds(self) -> None:
        intent = {
            "id": "intent_skill_006",
            "session_id": "session_skill",
            "seq": 6,
            "action": "high_risk_write",
            "args": {"scope": "critical"},
        }
        approval = self.tokens.issue_approval_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        confirm = self.tokens.issue_confirm_token(
            intent_id=intent["id"],
            skill_name=intent["action"],
            session_id=intent["session_id"],
            seq=intent["seq"],
            now_ts_ms=self.now_ms,
        )
        result = self.loader.execute(
            intent=intent,
            governor_output={
                "decision": "APPROVE",
                "tokens": {
                    "approval_token_ref": approval,
                    "confirm_token_ref": confirm,
                },
            },
            now_ts_ms=self.now_ms,
        )
        self.assertEqual("SUCCESS", result.status)
        self.assertTrue(result.manifest_ref and result.manifest_ref.startswith("manifest::"))
        self.assertTrue(result.undo_token_ref and result.undo_token_ref.startswith("undo::"))
