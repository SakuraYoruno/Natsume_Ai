"""T-PROACT-01~06：ProactiveTalk Skill 单元测试。

覆盖：
  T-PROACT-01  空闲触发，用户端收到主动消息，审计链完整
  T-PROACT-02  Gaming → SCHED_CONDITION_UNMET
  T-PROACT-03  频率超限（idle 时间不足）→ SCHED_CONDITION_UNMET
  T-PROACT-04  内容含执行 intent → QA 拦截（通过 qa_checker 模拟）
  T-PROACT-05  Edge 不可达（ui_pusher 异常）→ 静默降级，不重试
  T-PROACT-06  Sleep 模式 → 仅允许（ALLOWED_MODES 包含 sleep）
"""

from __future__ import annotations

import pytest

from host_brain.skills.impl.proactive_talk import (
    execute_proactive_talk,
    ALLOWED_MODES,
    STATUS_EXECUTED,
    STATUS_REJECTED,
    STATUS_DEGRADED,
)
from host_brain.core.errors import SCHED_CONDITION_UNMET
from host_brain.app.surface_reply_preprocessor import _apply_render_policy_hard_guard


# ── 辅助工厂 ──

def _ctx(
    sys_mode: str = "normal",
    last_interaction_ts: int | None = None,
    **kwargs,
) -> dict:
    return {
        "sys_mode": sys_mode,
        "last_interaction_ts": last_interaction_ts,
        "request_id": "req-proact",
        "intent_id": "intent-proact",
        "time_period": "evening",
        "user_name": "主人",
        **kwargs,
    }


def _make_llm_generator(greeting: str = "主人晚上好～") -> callable:
    def _gen(inp: dict) -> dict:
        return {"greeting": greeting}
    return _gen


def _make_ui_pusher() -> tuple[callable, list[dict]]:
    pushed: list[dict] = []
    def _pusher(msg: dict) -> dict:
        pushed.append(msg)
        return {"status": "OK"}
    return _pusher, pushed


# ── T-PROACT-01 ──

class TestProactiveTalkTrigger:
    """T-PROACT-01：空闲触发，用户端收到主动消息，审计链完整。"""

    def test_idle_trigger_sends_message(self) -> None:
        pusher, pushed = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,  # 很久以前
                llm_generator=_make_llm_generator("主人晚上好～"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["reason_code"] is None
        assert pushed == []
        message = result["delivery_intent"]["payload"]
        assert message["type"] == "proactive_talk"
        assert "主人晚上好" in message["content"]

    def test_audit_chain_fields(self) -> None:
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
                request_id="req-audit-proact",
                intent_id="intent-audit-proact",
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["request_id"] == "req-audit-proact"
        assert result["intent_id"] == "intent-audit-proact"
        assert result["event_id"] is not None
        assert result["ts_ms"] > 0


# ── T-PROACT-02 ──

class TestProactiveTalkGamingBlock:
    """T-PROACT-02：Gaming → SCHED_CONDITION_UNMET。"""

    def test_gaming_blocked(self) -> None:
        result = execute_proactive_talk(
            args={},
            context=_ctx(sys_mode="gaming"),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET

    def test_focus_blocked(self) -> None:
        result = execute_proactive_talk(
            args={},
            context=_ctx(sys_mode="focus"),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET


# ── T-PROACT-03 ──

class TestProactiveTalkIdleThreshold:
    """T-PROACT-03：idle 时间不足 → SCHED_CONDITION_UNMET。"""

    def test_not_idle_enough_rejected(self) -> None:
        import time
        now = int(time.time() * 1000)
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 4 * 60 * 60 * 1000},  # 4h
            context=_ctx(
                last_interaction_ts=now - 60_000,  # 1 分钟前
            ),
        )
        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET
        assert "elapsed_ms" in result

    def test_idle_enough_allowed(self) -> None:
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,  # 很久以前
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED

    def test_no_last_interaction_ts_allowed(self) -> None:
        """无 last_interaction_ts 时不检查空闲时间，直接允许。"""
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={},
            context=_ctx(
                last_interaction_ts=None,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED


# ── T-PROACT-04 ──

class TestProactiveTalkQABlock:
    """T-PROACT-04：内容含执行 intent → LLM 生成空内容模拟 QA 拦截。"""

    def test_empty_greeting_falls_back_to_template(self) -> None:
        pusher, _ = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=lambda inp: {"greeting": ""},  # 空内容
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["template_fallback_used"] is True
        assert result["template_fallback_reason"] == "LLM_EMPTY_OUTPUT"

    def test_llm_unavailable_uses_template(self) -> None:
        """无 LLM 时使用降级模板，仍可发送。"""
        pusher, pushed = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=None,
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert pushed == []
        assert result["delivery_intent"]["destination"] == "edge.proactive"

    def test_llm_fallback_marker_uses_template(self) -> None:
        pusher, pushed = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=lambda _inp: {"greeting": "", "fallback_to_template": True},
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["template_fallback_used"] is True
        assert pushed == []
        assert result["delivery_intent"]["destination"] == "edge.proactive"


# ── T-PROACT-05 ──

class TestProactiveTalkEdgeUnavailable:
    """T-PROACT-05：Edge 不可达（ui_pusher 异常）→ 静默降级，不重试。"""

    def test_ui_pusher_exception_degrades(self) -> None:
        def _bad_pusher(msg: dict) -> dict:
            raise ConnectionError("Edge unreachable")

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=_bad_pusher,
            ),
        )
        # Policy never invokes transport; Edge availability is handled by the
        # durable outbox dispatcher after command commit.
        assert result["status"] == STATUS_EXECUTED
        assert result["push_result"] is None
        assert result["delivery_intent"]["destination"] == "edge.proactive"

    def test_no_ui_pusher_still_executed(self) -> None:
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=None,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert result["push_result"] is None


# ── T-PROACT-06 ──

class TestProactiveTalkSleepMode:
    """T-PROACT-06：Sleep 模式 → ALLOWED_MODES 包含 sleep，允许触发。"""

    def test_sleep_mode_allowed(self) -> None:
        assert "sleep" in ALLOWED_MODES

        pusher, pushed = _make_ui_pusher()
        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                sys_mode="sleep",
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("晚安～"),
                ui_pusher=pusher,
            ),
        )
        assert result["status"] == STATUS_EXECUTED
        assert pushed == []
        assert result["delivery_intent"]["destination"] == "edge.proactive"

    def test_normal_mode_allowed(self) -> None:
        assert "normal" in ALLOWED_MODES


class TestProactiveTalkBioOpsActiveSilence:
    def test_active_silence_blocks_high_stress_late_night_proactive_push(self) -> None:
        pusher, pushed = _make_ui_pusher()

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                current_hour=2,
                user_stress="high",
                explicit_help_request=False,
                llm_generator=_make_llm_generator("我来看看你。"),
                ui_pusher=pusher,
            ),
        )

        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET
        assert "active_silence blocked proactive_talk" in result["detail"]
        assert result["bio_ops_runtime_trace"]["active_silence"]["silence_policy"] == "quiet_presence"
        assert "proactive_talk" in result["bio_ops_runtime_trace"]["forbidden_actions"]
        assert pushed == []

    def test_active_silence_trace_is_recorded_when_proactive_push_executes(self) -> None:
        pusher, pushed = _make_ui_pusher()

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                current_hour=14,
                user_stress="low",
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
            ),
        )

        assert result["status"] == STATUS_EXECUTED
        assert result["bio_ops_runtime_trace"]["proposal_only"] is True
        assert result["bio_ops_runtime_trace"]["active_silence"]["silence_policy"] == "normal_presence"
        assert pushed == []
        assert result["delivery_intent"]["destination"] == "edge.proactive"


class TestProactiveTalkMotiveState:
    def test_motive_pull_blocks_proactive_talk(self) -> None:
        pusher, pushed = _make_ui_pusher()

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候"),
                ui_pusher=pusher,
                motive_state={
                    "reach_out_drive": 0.20,
                    "continuity_drive": 0.18,
                    "withdraw_drive": 0.82,
                    "self_protect_drive": 0.88,
                    "dominant_motive": "self_protect",
                },
            ),
        )

        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET
        assert "motive gate blocked proactive_talk" in result["detail"]
        assert pushed == []

    def test_motive_hint_flows_into_llm_input(self) -> None:
        pusher, pushed = _make_ui_pusher()
        captured: dict[str, object] = {}

        def _capturing_llm(inp: dict) -> dict:
            captured.update(inp)
            return {"greeting": "我有点想你了，就来看看你在不在。"}

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_capturing_llm,
                ui_pusher=pusher,
                motive_state={
                    "reach_out_drive": 0.78,
                    "care_drive": 0.64,
                    "continuity_drive": 0.61,
                    "withdraw_drive": 0.22,
                    "self_protect_drive": 0.18,
                    "dominant_motive": "reach_out",
                },
            ),
        )

        assert result["status"] == STATUS_EXECUTED
        assert result["motive_driven"] is True
        assert result["motive_state"]["dominant_motive"] == "reach_out"
        assert captured["motive_state"]["reach_out_drive"] == 0.78
        assert "主动靠近对方" in str(captured["motive_hint"])
        assert result["delivery_intent"]["payload"]["dominant_motive"] == "reach_out"


class TestProactiveTalkRenderPolicy:
    def test_render_policy_blocks_proactive_talk(self) -> None:
        pusher, pushed = _make_ui_pusher()

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("问候一下。"),
                ui_pusher=pusher,
                render_policy={
                    "allow_proactive_after_reply": False,
                    "should_be_withdrawn": True,
                    "policy_reason_tags": ["willingness:LOW"],
                },
            ),
        )

        assert result["status"] == STATUS_REJECTED
        assert result["reason_code"] == SCHED_CONDITION_UNMET
        assert "render_policy blocked proactive_talk" in result["detail"]
        assert result["render_policy"]["allow_proactive_after_reply"] is False
        assert pushed == []

    def test_withdrawn_render_policy_softens_without_blocking_allowed_proactive(self) -> None:
        pusher, pushed = _make_ui_pusher()

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("我在，过来轻轻看你一眼。"),
                ui_pusher=pusher,
                render_policy={
                    "allow_proactive_after_reply": True,
                    "should_be_withdrawn": True,
                    "warmth_ceiling": 0.45,
                },
            ),
        )

        assert result["status"] == STATUS_EXECUTED
        assert result["render_policy"]["should_be_withdrawn"] is True
        assert pushed == []
        assert result["delivery_intent"]["payload"]["type"] == "proactive_talk"

    def test_render_policy_hard_guard_softens_proactive_message(self) -> None:
        pusher, pushed = _make_ui_pusher()

        result = execute_proactive_talk(
            args={"idle_threshold_ms": 1000},
            context=_ctx(
                last_interaction_ts=0,
                llm_generator=_make_llm_generator("哈哈，亲爱的，抱抱你～"),
                ui_pusher=pusher,
                render_policy={
                    "allow_proactive_after_reply": True,
                    "intimacy_ceiling": 0.2,
                    "humor_ceiling": 0.1,
                },
                render_policy_hard_guard=_apply_render_policy_hard_guard,
            ),
        )

        assert result["status"] == STATUS_EXECUTED
        assert result["render_policy"]["humor_ceiling"] == 0.1
        assert pushed == []
        assert result["delivery_intent"]["payload"]["content"] == "你先缓一缓。"
