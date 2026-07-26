"""Contract for the retired token-only SkillLoader boundary."""

from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader


class _ForbiddenRegistry:
    def require(self, _skill_name: str) -> None:
        raise AssertionError("retired SkillLoader read the Skill registry")


def test_skill_loader_rejects_before_registry_token_or_handler() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    loader = SkillLoader(
        registry=_ForbiddenRegistry(),
        token_service=TokenService(),
        ledger=ledger,
    )

    result = loader.execute(
        intent={
            "id": "intent-retired-loader",
            "request_id": "request-retired-loader",
            "session_id": "session-retired-loader",
            "seq": 1,
            "action": "must-not-run",
            "args": {"secret": "must-not-reach-handler"},
        },
        governor_output={
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": "approval://legacy"},
        },
        context={"emotion_tag": "HAPPY"},
        now_ts_ms=1_800_000_000_000,
    )

    assert result.status == "REJECTED"
    assert result.result_code == GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
    assert result.runtime_payload == {
        "required_execution_surface": "governed_task_runtime",
        "side_effects_executed": False,
    }
    rows = ledger.trace_request("request-retired-loader")[
        "skill_exec_log"
    ]
    assert len(rows) == 1
    assert (
        rows[0]["result_code"]
        == GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
    )
    assert rows[0]["approval_token_ref"] is None


def test_skill_loader_replay_never_becomes_an_execution_authority() -> None:
    ledger = SQLiteLedger(db_path=":memory:")
    loader = SkillLoader(
        registry=_ForbiddenRegistry(),
        token_service=TokenService(),
        ledger=ledger,
    )
    intent = {
        "id": "intent-retired-replay",
        "request_id": "request-retired-replay",
        "action": "must-not-run",
    }

    first = loader.execute(
        intent=intent,
        governor_output={"decision": "APPROVE"},
        now_ts_ms=1_800_000_000_001,
    )
    second = loader.execute(
        intent=intent,
        governor_output={"decision": "APPROVE"},
        now_ts_ms=1_800_000_000_002,
    )

    assert first.result_code == GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
    assert second.result_code == GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
    assert first.runtime_payload["side_effects_executed"] is False
    assert second.runtime_payload["side_effects_executed"] is False
