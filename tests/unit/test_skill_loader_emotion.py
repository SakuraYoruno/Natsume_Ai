"""M27-I：SkillLoader 情感状态传递测试（T-SKILL-EMO-01~02）"""

from __future__ import annotations

import uuid
import pytest
from host_brain.skills.loader import SkillLoader
from host_brain.skills.registry import SkillMeta, SkillRegistry
from host_brain.governor.tokens import TokenService
from host_brain.audit.sqlite_ledger import SQLiteLedger


def _make_skill_registry(capture_context: list) -> SkillRegistry:
    """创建一个捕获 context 的测试 SkillRegistry"""
    def handler(args, context):
        capture_context.append(dict(context or {}))
        return {"result": "ok"}

    registry = SkillRegistry()
    registry.register(SkillMeta(
        skill_name="test_skill",
        clearance="LOW",
        risk_level=1,
        scope="host",
        reversible=False,
        handler=handler,
        require_confirm=False,
    ))
    return registry


class TestSkillLoaderEmotionContext:
    """T-SKILL-EMO-01~02：SkillLoader 情感状态传递测试"""

    def _make_loader(self, capture_context: list) -> SkillLoader:
        registry = _make_skill_registry(capture_context)
        ledger = SQLiteLedger(":memory:")
        token_service = TokenService()
        return SkillLoader(registry=registry, token_service=token_service, ledger=ledger)

    def _make_intent_and_gov_output(self, loader: SkillLoader) -> tuple[dict, dict]:
        """创建唯一的 intent 和对应的 governor_output，避免重放检测"""
        from host_brain.core.ids import now_ms
        intent_id = str(uuid.uuid4())
        session_id = str(uuid.uuid4())
        seq = 1
        token_ref = loader.token_service.issue_approval_token(
            intent_id=intent_id,
            skill_name="test_skill",
            session_id=session_id,
            seq=seq,
            now_ts_ms=now_ms(),
        )
        intent = {
            "id": intent_id,
            "session_id": session_id,
            "seq": seq,
            "action": "test_skill",
            "args": {},
        }
        gov_output = {
            "decision": "APPROVE",
            "tokens": {"approval_token_ref": token_ref},
        }
        return intent, gov_output

    def test_execute_with_emotion_context(self):
        """T-SKILL-EMO-01：execute(context={"emotion_tag": "HAPPY", "mood_state": "engaged"}) 正常调用不报错"""
        captured = []
        loader = self._make_loader(captured)
        intent, gov_output = self._make_intent_and_gov_output(loader)
        result = loader.execute(
            intent=intent,
            governor_output=gov_output,
            context={"emotion_tag": "HAPPY", "mood_state": "engaged"},
        )
        assert result.status == "SUCCESS"
        assert len(captured) == 1
        assert captured[0]["emotion_tag"] == "HAPPY"
        assert captured[0]["mood_state"] == "engaged"

    def test_execute_without_emotion_context_uses_defaults(self):
        """T-SKILL-EMO-02：context 无情感字段时，使用默认值，不报错"""
        captured = []
        loader = self._make_loader(captured)
        intent, gov_output = self._make_intent_and_gov_output(loader)
        result = loader.execute(
            intent=intent,
            governor_output=gov_output,
            context={},
        )
        assert result.status == "SUCCESS"
        assert len(captured) == 1
        assert captured[0]["emotion_tag"] == "NEUTRAL"
        assert captured[0]["mood_state"] == "steady"
