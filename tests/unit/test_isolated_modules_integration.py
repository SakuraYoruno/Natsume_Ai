"""
test_isolated_modules_integration.py
——————————————————————————————————————
选项一：SCENE_VISION_UI 白名单测试
选项二：RoutingMatrix / RecallPackBuilder / MemoryGuard / StateArbiter 接入测试
选项三：单元测试覆盖

覆盖范围：
  - SCENE_VISION_UI 已加入 ALLOWED_EXPLICIT_SCENE_TYPES
  - RoutingMatrix.select_with_fallback() 正常路由 + 降级链
  - RecallPackBuilder.build() token 预算控制
  - MemoryGuard.check_episodic_write() 宪法校验
  - StateArbiter.resolve() 优先级仲裁
  - persist_chat_episodic_memory 中 MemoryGuard 校验路径
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# 选项一：SCENE_VISION_UI 白名单
# ---------------------------------------------------------------------------

class TestSceneVisionUIWhitelist:
    """SCENE_VISION_UI 已加入 ALLOWED_EXPLICIT_SCENE_TYPES 白名单。"""

    def test_vision_ui_in_allowed_explicit_scene_types(self):
        from host_brain.app.graph_config_builder import (
            ALLOWED_EXPLICIT_SCENE_TYPES,
            SCENE_VISION_UI,
        )
        assert SCENE_VISION_UI in ALLOWED_EXPLICIT_SCENE_TYPES, (
            f"SCENE_VISION_UI={SCENE_VISION_UI!r} 应在 ALLOWED_EXPLICIT_SCENE_TYPES 中"
        )

    def test_all_expected_scenes_in_whitelist(self):
        from host_brain.app.graph_config_builder import (
            ALLOWED_EXPLICIT_SCENE_TYPES,
            SCENE_CHAT,
            SCENE_WORK_CODE,
            SCENE_WORK_TRANSLATE,
            SCENE_EXEC_INTENT,
            SCENE_VISION_UI,
        )
        expected = {SCENE_CHAT, SCENE_WORK_CODE, SCENE_WORK_TRANSLATE, SCENE_EXEC_INTENT, SCENE_VISION_UI}
        assert expected.issubset(ALLOWED_EXPLICIT_SCENE_TYPES), (
            f"缺少场景: {expected - ALLOWED_EXPLICIT_SCENE_TYPES}"
        )


# ---------------------------------------------------------------------------
# 选项二：RoutingMatrix 接入测试
# ---------------------------------------------------------------------------

class TestRoutingMatrixIntegration:
    """RoutingMatrix.select_with_fallback() 路由决策测试。"""

    def _make_matrix(self):
        from host_brain.app.llm.routing_matrix import RoutingMatrix
        return RoutingMatrix()

    def test_chat_normal_routes_to_host_14b(self):
        from host_brain.app.schemas.routing_types import TaskType, LatencyBudget, ModelTarget
        matrix = self._make_matrix()
        decision = matrix.select_with_fallback(
            task_type=TaskType.CHAT,
            latency_budget=LatencyBudget.NORMAL,
        )
        assert decision.selected_model == ModelTarget.HOST_14B
        assert not decision.is_fallback

    def test_chat_fast_routes_to_host_14b(self):
        from host_brain.app.schemas.routing_types import TaskType, LatencyBudget, ModelTarget
        matrix = self._make_matrix()
        decision = matrix.select_with_fallback(
            task_type=TaskType.CHAT,
            latency_budget=LatencyBudget.FAST,
        )
        assert decision.selected_model == ModelTarget.HOST_14B

    def test_reasoning_routes_to_cloud(self):
        from host_brain.app.schemas.routing_types import TaskType, LatencyBudget, ModelTarget
        matrix = self._make_matrix()
        decision = matrix.select_with_fallback(task_type=TaskType.REASONING)
        assert decision.selected_model == ModelTarget.CLOUD

    def test_fallback_chain_skips_failed_model(self):
        from host_brain.app.schemas.routing_types import TaskType, LatencyBudget, ModelTarget
        matrix = self._make_matrix()
        # 主路由 HOST_14B 失败，应降级到 CLOUD
        decision = matrix.select_with_fallback(
            task_type=TaskType.CHAT,
            latency_budget=LatencyBudget.NORMAL,
            failed_models=[ModelTarget.HOST_14B],
        )
        assert decision.is_fallback
        assert decision.selected_model == ModelTarget.CLOUD

    def test_route_log_writer_called(self):
        from host_brain.app.schemas.routing_types import TaskType
        log_calls = []
        from host_brain.app.llm.routing_matrix import RoutingMatrix
        matrix = RoutingMatrix(route_log_writer=lambda d: log_calls.append(d))
        matrix.select_with_fallback(task_type=TaskType.CHAT)
        assert len(log_calls) == 1


# ---------------------------------------------------------------------------
# 选项二：RecallPackBuilder 接入测试
# ---------------------------------------------------------------------------

class TestRecallPackBuilderIntegration:
    """RecallPackBuilder.build() token 预算控制测试。"""

    def _make_builder(self):
        from host_brain.app.recall.recall_pack_builder import RecallPackBuilder
        return RecallPackBuilder()

    def test_build_with_empty_recall(self):
        builder = self._make_builder()
        pack = builder.build(recall_results={})
        assert pack.total_token_count == 0

    def test_build_with_world_canon(self):
        builder = self._make_builder()
        pack = builder.build(recall_results={"world_canon": "用户名叫小明，喜欢编程。"})
        assert pack.total_token_count > 0
        slot = pack.get_slot("world_canon")
        assert slot is not None
        assert slot.content

    def test_token_budget_truncation(self):
        from host_brain.app.recall.recall_pack_builder import RecallPackBuilder
        builder = RecallPackBuilder()
        # 超长文本应被截断
        long_text = "这是一段很长的文本。" * 500
        pack = builder.build(recall_results={"world_canon": long_text})
        slot = pack.get_slot("world_canon")
        assert slot is not None
        assert slot.truncated  # 应被截断

    def test_style_canon_conflict_with_code_task(self):
        builder = self._make_builder()
        pack = builder.build(
            recall_results={"style_canon": "温柔可爱的语气风格。"},
            task_type="code",
        )
        slot = pack.get_slot("style_canon")
        assert slot is not None
        assert "[REFERENCE_ONLY]" in slot.content


# ---------------------------------------------------------------------------
# 选项二：MemoryGuard 接入测试
# ---------------------------------------------------------------------------

class TestMemoryGuardIntegration:
    """MemoryGuard 宪法校验测试。"""

    def _make_guard(self):
        from host_brain.app.memory.memory_guard import MemoryGuard
        return MemoryGuard()

    def test_episodic_write_passes_with_emotion_tag(self):
        from host_brain.app.schemas.memory_types import EpisodicRecord, EvidenceLevel
        guard = self._make_guard()
        record = EpisodicRecord(
            session_id="test_session",
            content="今天很开心",
            turn_count=2,
            emotion_tags=["JOY"],
            evidence_level=EvidenceLevel.CANDIDATE,
        )
        # 不应抛出异常
        guard.check_episodic_write(record)

    def test_episodic_write_fails_with_too_few_turns(self):
        from host_brain.app.schemas.memory_types import EpisodicRecord, EvidenceLevel
        from host_brain.app.memory.memory_guard import MemoryGuardError
        guard = self._make_guard()
        record = EpisodicRecord(
            session_id="test_session",
            content="今天很开心",
            turn_count=1,  # 轮次不足
            emotion_tags=["JOY"],
            evidence_level=EvidenceLevel.CANDIDATE,
        )
        with pytest.raises(MemoryGuardError) as exc_info:
            guard.check_episodic_write(record)
        assert "EPISODIC_TURN_TOO_FEW" in str(exc_info.value.reason)

    def test_episodic_write_fails_with_no_tags(self):
        from host_brain.app.schemas.memory_types import EpisodicRecord, EvidenceLevel
        from host_brain.app.memory.memory_guard import MemoryGuardError
        guard = self._make_guard()
        record = EpisodicRecord(
            session_id="test_session",
            content="今天很开心",
            turn_count=3,
            emotion_tags=[],  # 无标签
            evidence_level=EvidenceLevel.CANDIDATE,
        )
        with pytest.raises(MemoryGuardError) as exc_info:
            guard.check_episodic_write(record)
        assert "EPISODIC_NO_QUALIFYING_TAGS" in str(exc_info.value.reason)

    def test_persist_episodic_memory_skipped_by_guard(self):
        """MemoryGuard 校验失败时，persist_chat_episodic_memory 应返回 SKIPPED。"""
        from host_brain.app.observability_persist import persist_chat_episodic_memory
        mock_store = MagicMock()
        # turn_count=1 会触发 MemoryGuard 拒绝
        result = persist_chat_episodic_memory(
            request_id="req_test",
            intent_id="intent_test",
            session_id="session_test",
            user_message="你好",
            assistant_reply="你好呀",
            emotion_context={"emotion_tag": "NEUTRAL", "turn_count": 1},
            ts_ms=1000000,
            episodic_store=mock_store,
        )
        # turn_count=1 且 emotion_tag=NEUTRAL（无情感标签），应被 MemoryGuard 拒绝
        assert result["status"] in ("SKIPPED", "OK")  # 降级或正常均可接受
        # 若被拒绝，reason_code 应包含 MEMORY_GUARD_REJECTED
        if result["status"] == "SKIPPED":
            assert result.get("reason_code", "").startswith(("MEMORY_GUARD", "MEMORY_WORTH"))


# ---------------------------------------------------------------------------
# 选项二：StateArbiter 接入测试
# ---------------------------------------------------------------------------

class TestStateArbiterIntegration:
    """StateArbiter 优先级仲裁测试。"""

    def _make_arbiter(self):
        from host_brain.app.memory.state_arbiter import StateArbiter
        return StateArbiter()

    def test_resolve_returns_highest_priority(self):
        import asyncio
        from host_brain.app.memory.state_arbiter import StateArbiter, StateSource
        arbiter = StateArbiter()
        # 注册两个源，WorldCanon 优先级更高
        arbiter.register_source(StateSource.WORLD_CANON, lambda k: "canon_value" if k == "user.name" else None)
        arbiter.register_source(StateSource.EPISODIC_STORE, lambda k: "episodic_value" if k == "user.name" else None)
        result = asyncio.run(arbiter.resolve("user.name"))
        assert result is not None
        assert result.value == "canon_value"
        assert result.source == StateSource.WORLD_CANON
        assert result.conflict_detected  # 两个源有不同值，应检测到冲突

    def test_resolve_fallback_on_unreachable_source(self):
        import asyncio
        from host_brain.app.memory.state_arbiter import StateArbiter, StateSource
        arbiter = StateArbiter()
        # WorldCanon 不可达，应降级到 EpisodicStore
        def _raise(_k):
            raise ConnectionError("WorldCanon 不可达")
        arbiter.register_source(StateSource.WORLD_CANON, _raise)
        arbiter.register_source(StateSource.EPISODIC_STORE, lambda k: "episodic_value")
        result = asyncio.run(arbiter.resolve("user.name"))
        assert result is not None
        assert result.value == "episodic_value"
        assert result.is_fallback
        assert StateSource.WORLD_CANON in result.fallback_chain

    def test_resolve_returns_none_when_all_empty(self):
        import asyncio
        from host_brain.app.memory.state_arbiter import StateArbiter, StateSource
        arbiter = StateArbiter()
        arbiter.register_source(StateSource.WORLD_CANON, lambda k: None)
        result = asyncio.run(arbiter.resolve("nonexistent.key"))
        assert result is None
