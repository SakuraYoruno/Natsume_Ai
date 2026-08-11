"""Legacy caller context cannot revive SkillLoader execution."""

from __future__ import annotations

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
from host_brain.governor.tokens import TokenService
from host_brain.skills.loader import SkillLoader


class _CaptureRegistry:
    def __init__(self) -> None:
        self.reads = 0

    def require(self, _skill_name: str) -> None:
        self.reads += 1
        raise AssertionError("retired SkillLoader read its registry")


def test_emotion_context_cannot_reenable_retired_skill_loader() -> None:
    registry = _CaptureRegistry()
    loader = SkillLoader(
        registry=registry,
        token_service=TokenService(),
        ledger=SQLiteLedger(":memory:"),
    )

    result = loader.execute(
        intent={
            "id": "intent-retired-emotion",
            "action": "test_skill",
        },
        governor_output={"decision": "APPROVE"},
        context={"emotion_tag": "HAPPY", "mood_state": "engaged"},
    )

    assert result.result_code == GOVERNED_SKILL_TASK_AUTHORITY_REQUIRED
    assert result.runtime_payload["side_effects_executed"] is False
    assert registry.reads == 0
