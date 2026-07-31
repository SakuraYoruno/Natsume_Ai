"""EmotionalSilenceEngine：情绪驱动的沉默能力——Phase XVIII。

职责：
  1. 根据当前情绪状态计算回复意愿值（response_willingness）
  2. 维护话题抗拒映射（topic_resistance_map）
  3. 生成沉默状态快照（SilenceState），供 prompt_builder 注入
  4. 提供 tick 接口，随时间自然恢复意愿

设计原则：
  - 纯逻辑层，不直接影响 LLM 调用（由 prompt_builder 注入指令）
  - 情绪极低时，回复意愿降低，prompt 中注入"极短回复"指令
  - 某些话题触发真实抗拒（非礼貌转移），注入"话题回避"指令
  - 极端情况（willingness < 0.15）可产生"嗯"、"……"等极短回复
  - 意愿值随时间自然恢复（每分钟 +0.02，上限 1.0）

沉默触发条件：
  - mood_state: "low" / "depressed" / "withdrawn"
  - emotion_tag: "SAD" / "ANGRY" / "HURT" / "EXHAUSTED" / "NUMB"
  - emotion_valence < -0.5（情绪效价极负）
  - emotion_arousal < 0.15（激活度极低，麻木/疲惫）
  - 话题命中 topic_resistance_map（话题抗拒）

回复意愿分级：
  - [0.85, 1.0]  NORMAL    — 正常回复
  - [0.60, 0.85) SUBDUED   — 回复偏短，语气平淡
  - [0.35, 0.60) RELUCTANT — 明显不想说话，极简回复
  - [0.15, 0.35) SILENT    — 极短回复（"嗯"、"……"、"不知道"）
  - [0.0,  0.15) MUTE      — 几乎不回复（"……"、沉默）
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.model_runtime.invocation import complete_compiled_prompt_sync
from host_brain.model_runtime.trace import model_attempt_result_trace
from host_brain.model_runtime.prompt_contract import resolve_prompt_contract_json
from host_brain.model_runtime import EMOTIONAL_SILENCE_PROMPT_ID

from host_brain.persona.offline_continuity import (
    absence_recovery_factor_for_absence_band,
    resolve_absence_band as resolve_offline_absence_band,
)

_logger = logging.getLogger("Natsume.emotional_silence")

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 情绪标签 → 沉默权重（越高越容易触发沉默）
_EMOTION_SILENCE_WEIGHT: dict[str, float] = {
    "SAD":       0.7,
    "HURT":      0.75,
    "DEPRESSED": 0.8,
    "EXHAUSTED": 0.65,
    "NUMB":      0.85,
    "ANGRY":     0.5,
    "ANXIOUS":   0.3,
    "LONELY":    0.4,
    "NEUTRAL":   0.0,
    "HAPPY":    -0.3,
    "LOVING":   -0.2,
    "EXCITED":  -0.4,
    "CARING":   -0.1,
}

# mood_state → 沉默权重
_MOOD_SILENCE_WEIGHT: dict[str, float] = {
    "depressed":  0.8,
    "withdrawn":  0.7,
    "low":        0.5,
    "melancholy": 0.6,
    "tired":      0.4,
    "steady":     0.0,
    "elevated":  -0.2,
    "excited":   -0.3,
}

# 回复意愿分级阈值
WILLINGNESS_NORMAL    = 0.85
WILLINGNESS_SUBDUED   = 0.60
WILLINGNESS_RELUCTANT = 0.35
WILLINGNESS_SILENT    = 0.15
WILLINGNESS_MUTE      = 0.0

# 意愿自然恢复速率（每分钟）
_RECOVERY_RATE_PER_MINUTE = 0.02

# 话题抗拒衰减速率（每次 tick 降低 0.05，直到消失）
_RESISTANCE_DECAY_PER_TICK = 0.05

# 默认话题抗拒强度（0.0~1.0）
_DEFAULT_TOPIC_RESISTANCE = 0.7

# 话题抗拒触发的意愿惩罚
_TOPIC_RESISTANCE_PENALTY = 0.3


def _resolve_absence_band(offline_state: dict[str, Any] | None) -> str:
    return resolve_offline_absence_band(offline_state)


def _absence_recovery_factor(absence_band: str) -> float:
    return absence_recovery_factor_for_absence_band(absence_band)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SilenceState:
    """沉默状态快照——供 prompt_builder 注入。"""

    response_willingness: float = 1.0
    """回复意愿值 [0.0, 1.0]。"""

    willingness_level: str = "NORMAL"
    """意愿分级：NORMAL / SUBDUED / RELUCTANT / SILENT / MUTE。"""

    silence_hint: str = ""
    """给 LLM 的沉默指令（自然语言）。"""

    topic_resistance_active: bool = False
    """当前是否有话题抗拒激活。"""

    resistant_topic: str = ""
    """当前抗拒的话题标签（如有）。"""

    topic_resistance_hint: str = ""
    """给 LLM 的话题抗拒指令（自然语言）。"""

    trigger_reason: str = ""
    """触发沉默的原因（调试用）。"""

    def as_dict(self) -> dict[str, Any]:
        return {
            "response_willingness": self.response_willingness,
            "willingness_level": self.willingness_level,
            "silence_hint": self.silence_hint,
            "topic_resistance_active": self.topic_resistance_active,
            "resistant_topic": self.resistant_topic,
            "topic_resistance_hint": self.topic_resistance_hint,
            "trigger_reason": self.trigger_reason,
        }

    @property
    def is_active(self) -> bool:
        """是否处于有效沉默状态（非 NORMAL）。"""
        return self.willingness_level != "NORMAL" or self.topic_resistance_active


class RetiredEmotionalSilenceEngine:
    """Fail-closed production placeholder for the retired mutable engine.

    A canonical Host must not construct the historical in-memory
    ``EmotionalSilenceEngine`` merely because a rollback-window port still
    exists in the lifecycle contract.  This object deliberately exposes no
    state or mutation methods: any missed production caller receives a
    deterministic failure instead of silently recreating a second response
    posture authority.  Explicit legacy/offline compositions can still
    instantiate :class:`EmotionalSilenceEngine` directly.
    """

    status = "RETIRED_CANONICAL_LIFE"
    authority = "CANONICAL_LIFE_JOURNAL"
    reason_code = "LEGACY_SILENCE_ENGINE_RETIRED"
    # The governed ToolBroker fingerprints captured adapter state while it
    # registers lifecycle-bound handlers.  This is a read-only identity for
    # that fingerprinting path, not a compatibility implementation of the
    # retired engine.  All behavioral reads and mutations still fail closed
    # through ``__getattr__`` below.
    __natsume_adapter_version__ = "retired-emotional-silence-adapter.v1"

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"{self.reason_code}:{str(name or '').strip() or 'operation'}")


# ---------------------------------------------------------------------------
# 沉默指令模板
# ---------------------------------------------------------------------------

# 意愿分级 → 给 LLM 的沉默指令
_SILENCE_HINT_MAP: dict[str, str] = {
    "NORMAL":    "",
    "SUBDUED":   "",
    "RELUCTANT": "",
    "SILENT":    "",
    "MUTE":      "",
}

# 话题抗拒 → 给 LLM 的抗拒指令
_TOPIC_RESISTANCE_HINT_TEMPLATE = ""


def _load_runtime_hints() -> dict[str, Any]:
    return {
        "silence_hints": dict(_SILENCE_HINT_MAP),
        "topic_resistance_hint": _TOPIC_RESISTANCE_HINT_TEMPLATE,
    }


def _load_llm_hint_templates() -> dict[str, Any]:
    raw = resolve_prompt_contract_json("emotional_silence.llm_hints")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_section(mapping: Mapping[str, Any], key: str) -> dict[str, str]:
    raw = mapping.get(key)
    if isinstance(raw, Mapping):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else v for k, v in kwargs.items()})
    except Exception:
        return text


# ---------------------------------------------------------------------------
# EmotionalSilenceEngine
# ---------------------------------------------------------------------------

class EmotionalSilenceEngine:
    """情绪驱动的沉默能力引擎（Phase XVIII）。

    用法：
        engine = EmotionalSilenceEngine()
        # 每次对话前更新情绪状态
        engine.update_emotion(
            emotion_tag="SAD",
            emotion_valence=-0.7,
            emotion_arousal=0.1,
            mood_state="low",
        )
        # 获取沉默状态快照
        state = engine.get_silence_state(current_topic="工作")
        # 注入 prompt
        if state.is_active:
            prompt += state.silence_hint
    """

    def __init__(self, *, model_attempt_service: Any = None) -> None:
        self._model_attempt_service = model_attempt_service
        # 当前回复意愿值 [0.0, 1.0]
        self._response_willingness: float = 1.0
        # 上次 tick 时间戳（毫秒）
        self._last_tick_ms: int = _now_ms()
        # 话题抗拒映射：topic_label → resistance_strength [0.0, 1.0]
        self._topic_resistance_map: dict[str, float] = {}
        # 当前激活的抗拒话题（最近一次命中的）
        self._active_resistant_topic: str = ""
        # 上次触发沉默的原因
        self._last_trigger_reason: str = ""
        # 情绪状态缓存（用于 tick 时的自然恢复判断）
        self._current_emotion_tag: str = "NEUTRAL"
        self._current_mood_state: str = "steady"
        self._current_valence: float = 0.0
        self._current_arousal: float = 0.3
        self._last_model_attempt_trace: dict[str, Any] = {}

    # ── 情绪更新 ──────────────────────────────────────────────────────────

    def update_emotion(
        self,
        *,
        emotion_tag: str = "NEUTRAL",
        emotion_valence: float = 0.0,
        emotion_arousal: float = 0.3,
        mood_state: str = "steady",
    ) -> float:
        """更新情绪状态，重新计算回复意愿值。

        Returns:
            新的 response_willingness 值。
        """
        self._current_emotion_tag = str(emotion_tag).strip().upper() or "NEUTRAL"
        self._current_mood_state = str(mood_state).strip().lower() or "steady"
        self._current_valence = float(emotion_valence)
        self._current_arousal = float(emotion_arousal)

        new_willingness = self._compute_willingness(
            emotion_tag=self._current_emotion_tag,
            emotion_valence=self._current_valence,
            emotion_arousal=self._current_arousal,
            mood_state=self._current_mood_state,
        )

        # 意愿值只降不升（升由 tick 负责，避免情绪更新时突然恢复）
        if new_willingness < self._response_willingness:
            self._response_willingness = new_willingness
            self._last_trigger_reason = (
                f"emotion={self._current_emotion_tag} "
                f"valence={self._current_valence:.2f} "
                f"arousal={self._current_arousal:.2f} "
                f"mood={self._current_mood_state}"
            )
            _logger.debug(
                "沉默引擎：意愿降低 → %.3f，原因：%s",
                self._response_willingness,
                self._last_trigger_reason,
            )

        return self._response_willingness

    def _compute_willingness(
        self,
        *,
        emotion_tag: str,
        emotion_valence: float,
        emotion_arousal: float,
        mood_state: str,
    ) -> float:
        """计算回复意愿值 [0.0, 1.0]。

        算法：
        1. 基础意愿 = 1.0
        2. 情绪标签权重：从 _EMOTION_SILENCE_WEIGHT 查找，负值表示提升意愿
        3. mood_state 权重：从 _MOOD_SILENCE_WEIGHT 查找
        4. VAD 惩罚：valence < -0.5 额外 -0.2；arousal < 0.15 额外 -0.25
        5. 综合惩罚 = 情绪权重 + mood权重 + VAD惩罚，clamp 到 [0, 0.9]
        6. willingness = 1.0 - 综合惩罚，clamp 到 [0.05, 1.0]
        """
        emotion_weight = _EMOTION_SILENCE_WEIGHT.get(emotion_tag, 0.0)
        mood_weight = _MOOD_SILENCE_WEIGHT.get(mood_state, 0.0)

        # VAD 额外惩罚
        vad_penalty = 0.0
        if emotion_valence < -0.5:
            vad_penalty += 0.2 * abs(emotion_valence + 0.5) / 0.5  # 线性增强
        if emotion_arousal < 0.15:
            vad_penalty += 0.25 * (0.15 - emotion_arousal) / 0.15  # 越低惩罚越大

        total_penalty = max(0.0, min(0.9, emotion_weight + mood_weight + vad_penalty))
        willingness = max(0.05, min(1.0, 1.0 - total_penalty))
        return round(willingness, 4)

    # ── 话题抗拒 ──────────────────────────────────────────────────────────

    def add_topic_resistance(
        self,
        topic_label: str,
        *,
        strength: float = _DEFAULT_TOPIC_RESISTANCE,
    ) -> None:
        """添加话题抗拒。

        Args:
            topic_label: 话题标签（如"工作"、"前任"、"家庭"）
            strength: 抗拒强度 [0.0, 1.0]，默认 0.7
        """
        label = str(topic_label).strip()
        if not label:
            return
        self._topic_resistance_map[label] = float(max(0.0, min(1.0, strength)))
        _logger.debug("话题抗拒已添加: %s (强度=%.2f)", label, strength)

    def remove_topic_resistance(self, topic_label: str) -> None:
        """移除话题抗拒。"""
        self._topic_resistance_map.pop(str(topic_label).strip(), None)

    def check_topic_resistance(self, current_topic: str) -> tuple[bool, str, float]:
        """检查当前话题是否触发抗拒。

        Returns:
            (is_resistant, matched_topic_label, resistance_strength)
        """
        if not current_topic or not self._topic_resistance_map:
            return False, "", 0.0

        topic_lower = str(current_topic).strip().lower()
        for label, strength in self._topic_resistance_map.items():
            if label.lower() in topic_lower or topic_lower in label.lower():
                return True, label, strength

        return False, "", 0.0

    # ── Tick（自然恢复）──────────────────────────────────────────────────

    def tick(
        self,
        *,
        user_id: str = "",
        current_topic: str = "",
        bond_summary: dict[str, Any] | None = None,
        emotion_context: dict[str, Any] | None = None,
        life_snapshot_reader: Any = None,
    ) -> dict[str, Any]:
        """每分钟调用一次，驱动意愿自然恢复和话题抗拒衰减。

        `motive_state` 会参与恢复/衰减速率调制：
        - `reach_out` / `continuity` 高时，恢复更快、抗拒衰减更快；
        - `withdraw` / `self_protect` 高时，恢复更慢、抗拒衰减更慢。

        Returns:
            {"willingness": float, "recovered": bool, "decayed_topics": list[str]}
        """
        now = _now_ms()
        elapsed_minutes = (now - self._last_tick_ms) / 60000.0
        self._last_tick_ms = now

        motive_state: dict[str, Any] = {}
        motive_recovery_factor = 1.0
        resistance_decay_factor = 1.0
        absence_band = "recent"
        absence_recovery_factor = 1.0
        try:
            from host_brain.digital_life.application_boundary import (  # noqa: PLC0415
                project_inner_world_state,
                read_life_snapshot,
            )
            from host_brain.persona.identity import resolve_persona_self_id  # noqa: PLC0415
            from host_brain.persona.motive_runtime import (  # noqa: PLC0415
                build_offline_state_from_inner_state,
                compute_runtime_motive_state,
            )

            persona_user_id = resolve_persona_self_id(user_id)
            if life_snapshot_reader is None:
                raise RuntimeError("LIFE_SNAPSHOT_READER_NOT_COMPOSED")
            inner_state = project_inner_world_state(
                read_life_snapshot(persona_user_id, reader=life_snapshot_reader)
            )
            offline_state = build_offline_state_from_inner_state(inner_state, now_ms=now)
            absence_band = _resolve_absence_band(offline_state)
            absence_recovery_factor = _absence_recovery_factor(absence_band)

            runtime_emotion_context = dict(emotion_context or {})
            runtime_emotion_context.setdefault("emotion_tag", self._current_emotion_tag)
            runtime_emotion_context.setdefault(
                "emotion_intensity",
                max(abs(self._current_valence), abs(self._current_arousal)),
            )
            runtime_emotion_context.setdefault("mood_state", self._current_mood_state)

            motive_state = compute_runtime_motive_state(
                persona_user_id=persona_user_id,
                current_topic=current_topic,
                bond_summary=bond_summary,
                emotion_context=runtime_emotion_context,
                silence_state=self.get_silence_state(current_topic=current_topic).as_dict(),
                now_ms=now,
            )
            reach_out_drive = float(motive_state.get("reach_out_drive") or 0.0)
            continuity_drive = float(motive_state.get("continuity_drive") or 0.0)
            withdraw_drive = float(motive_state.get("withdraw_drive") or 0.0)
            self_protect_drive = float(motive_state.get("self_protect_drive") or 0.0)
            motive_recovery_factor = max(
                0.35,
                min(
                    1.60,
                    1.0
                    + reach_out_drive * 0.30
                    + continuity_drive * 0.10
                    - withdraw_drive * 0.25
                    - self_protect_drive * 0.20,
                ),
            )
            resistance_decay_factor = max(
                0.50,
                min(
                    1.50,
                    1.0
                    + reach_out_drive * 0.20
                    - withdraw_drive * 0.20
                    - self_protect_drive * 0.20,
                ),
            )
            motive_recovery_factor = min(1.85, motive_recovery_factor * absence_recovery_factor)
            resistance_decay_factor = min(1.65, resistance_decay_factor * max(1.0, absence_recovery_factor - 0.02))
        except Exception as motive_exc:
            _logger.debug("沉默引擎 motive tick skipped: %s", motive_exc)

        recovered = False
        # 意愿自然恢复（仅在情绪不是极端负向时）
        if self._response_willingness < 1.0:
            # 情绪极端时恢复更慢
            recovery_factor = 1.0
            if self._current_valence < -0.5 or self._current_arousal < 0.15:
                recovery_factor = 0.3
            elif self._current_valence < -0.3:
                recovery_factor = 0.6
            recovery_factor *= motive_recovery_factor

            recovery = _RECOVERY_RATE_PER_MINUTE * elapsed_minutes * recovery_factor
            new_willingness = min(1.0, self._response_willingness + recovery)
            if new_willingness > self._response_willingness:
                self._response_willingness = round(new_willingness, 4)
                recovered = True

        # 话题抗拒衰减
        decayed_topics: list[str] = []
        for label in list(self._topic_resistance_map.keys()):
            new_strength = (
                self._topic_resistance_map[label]
                - _RESISTANCE_DECAY_PER_TICK * elapsed_minutes * resistance_decay_factor
            )
            if new_strength <= 0.0:
                del self._topic_resistance_map[label]
                decayed_topics.append(label)
            else:
                self._topic_resistance_map[label] = round(new_strength, 4)

        return {
            "willingness": self._response_willingness,
            "recovered": recovered,
            "decayed_topics": decayed_topics,
            "motive_state": motive_state,
            "absence_band": absence_band,
            "absence_recovery_factor": round(absence_recovery_factor, 3),
            "motive_recovery_factor": round(motive_recovery_factor, 3),
            "resistance_decay_factor": round(resistance_decay_factor, 3),
        }

    # ── 状态快照 ──────────────────────────────────────────────────────────

    def get_silence_state(
        self,
        *,
        current_topic: str = "",
    ) -> SilenceState:
        """获取当前沉默状态快照。

        Args:
            current_topic: 当前对话话题（用于话题抗拒检测）

        Returns:
            SilenceState 快照，供 prompt_builder 注入。
        """
        willingness = self._response_willingness
        level = _classify_willingness(willingness)
        templates = _load_runtime_hints()
        silence_hint_map = _mapping_section(templates, "silence_hints")
        silence_hint = str(silence_hint_map.get(level, _SILENCE_HINT_MAP.get(level, "")))

        # 话题抗拒检测
        is_resistant, resistant_topic, resistance_strength = self.check_topic_resistance(current_topic)
        topic_resistance_hint = ""
        if is_resistant:
            # 话题抗拒额外降低意愿
            effective_willingness = max(0.05, willingness - _TOPIC_RESISTANCE_PENALTY * resistance_strength)
            effective_level = _classify_willingness(effective_willingness)
            silence_hint = str(silence_hint_map.get(effective_level, _SILENCE_HINT_MAP.get(effective_level, silence_hint)))
            topic_resistance_hint = _format_template_text(
                templates.get("topic_resistance_template", _TOPIC_RESISTANCE_HINT_TEMPLATE),
                topic=resistant_topic,
            )
            self._active_resistant_topic = resistant_topic
        else:
            self._active_resistant_topic = ""

        llm_hints = self._maybe_render_llm_hints(
            current_topic=current_topic,
            willingness=willingness,
            level=level,
            is_resistant=is_resistant,
            resistant_topic=resistant_topic,
            silence_hint=silence_hint,
            topic_resistance_hint=topic_resistance_hint,
            trigger_reason=self._last_trigger_reason,
        )
        if llm_hints:
            silence_hint = str(llm_hints.get("silence_hint") or silence_hint).strip()
            topic_resistance_hint = str(
                llm_hints.get("topic_resistance_hint") or topic_resistance_hint
            ).strip()

        return SilenceState(
            response_willingness=round(willingness, 4),
            willingness_level=level,
            silence_hint=silence_hint,
            topic_resistance_active=is_resistant,
            resistant_topic=resistant_topic,
            topic_resistance_hint=topic_resistance_hint,
            trigger_reason=self._last_trigger_reason,
        )

    def _maybe_render_llm_hints(
        self,
        *,
        current_topic: str,
        willingness: float,
        level: str,
        is_resistant: bool,
        resistant_topic: str,
        silence_hint: str,
        topic_resistance_hint: str,
        trigger_reason: str,
    ) -> dict[str, Any]:
        if self._model_attempt_service is None:
            return {}
        templates = _load_llm_hint_templates()
        if not templates:
            return {}

        system_prompt = str(templates.get("system_prompt") or "").strip() or (
            "你负责根据沉默状态生成更自然的提示语，只输出 JSON。"
        )
        user_template = str(templates.get("user_prompt") or "").strip() or (
            "response_willingness={willingness:.2f}\n"
            "willingness_level={level}\n"
            "emotion_tag={emotion_tag}\n"
            "mood_state={mood_state}\n"
            "current_topic={current_topic}\n"
            "topic_resistance_active={topic_resistance_active}\n"
            "resistant_topic={resistant_topic}\n"
            "trigger_reason={trigger_reason}\n"
            "rule_silence_hint={silence_hint}\n"
            "rule_topic_resistance_hint={topic_resistance_hint}\n\n"
            "请输出 JSON：{{\"silence_hint\":\"...\",\"topic_resistance_hint\":\"...\"}}"
        )
        try:
            completion = complete_compiled_prompt_sync(
                attempt_service=self._model_attempt_service,
                prompt_id=EMOTIONAL_SILENCE_PROMPT_ID,
                messages=(
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_template.format(
                            willingness=max(0.0, min(1.0, float(willingness))),
                            level=str(level or "NORMAL").upper(),
                            emotion_tag=self._current_emotion_tag,
                            mood_state=self._current_mood_state,
                            current_topic=str(current_topic or "").strip() or "无",
                            topic_resistance_active="true" if is_resistant else "false",
                            resistant_topic=str(resistant_topic or "").strip() or "无",
                            trigger_reason=str(trigger_reason or "").strip() or "none",
                            silence_hint=str(silence_hint or "").strip() or "无",
                            topic_resistance_hint=str(topic_resistance_hint or "").strip() or "无",
                        ),
                    },
                ),
                request_id=f"silence_hint::{int(time.time() * 1000)}",
                scene_type="SILENCE_STATE",
                cognitive_tier="C",
                deadline_ms=1_800,
                foreground=True,
                metadata={"caller": "emotional_silence_hints"},
            )
        except Exception:
            return {}
        self._last_model_attempt_trace = model_attempt_result_trace(completion)
        if not completion.success:
            return {}
        try:
            parsed = json.loads(str(completion.content or ""))
        except Exception:
            return {}
        return {
            "silence_hint": str(parsed.get("silence_hint") or "").strip(),
            "topic_resistance_hint": str(parsed.get("topic_resistance_hint") or "").strip(),
            "model_attempt": dict(self._last_model_attempt_trace),
        }

    # ── 查询 ──────────────────────────────────────────────────────────────

    @property
    def response_willingness(self) -> float:
        """当前回复意愿值。"""
        return self._response_willingness

    @property
    def last_model_attempt_trace(self) -> dict[str, Any]:
        return dict(self._last_model_attempt_trace)

    @property
    def topic_resistance_map(self) -> dict[str, float]:
        """当前话题抗拒映射（只读副本）。"""
        return dict(self._topic_resistance_map)

    def apply_body_sense_penalty(self, penalty: float) -> float:
        """接入身体感知惩罚——疲劳度影响回复意愿（v1.12 新增）。

        由 BodySenseModel.get_willingness_penalty() 提供惩罚值，
        在每次对话开始时调用，将疲劳度映射为意愿降低。

        Args:
            penalty: 惩罚值 [0.0, 0.35]，来自 BodySenseModel.get_willingness_penalty()

        Returns:
            新的 response_willingness 值。
        """
        penalty = float(max(0.0, min(0.35, penalty)))
        if penalty <= 0.0:
            return self._response_willingness

        new_willingness = max(0.05, self._response_willingness - penalty)
        if new_willingness < self._response_willingness:
            self._response_willingness = round(new_willingness, 4)
            self._last_trigger_reason = (
                f"body_sense_fatigue_penalty={penalty:.3f}"
            )
            _logger.debug(
                "沉默引擎：疲劳惩罚 -%.3f → willingness=%.3f",
                penalty, self._response_willingness,
            )
        return self._response_willingness

    def reset(self) -> None:
        """重置沉默引擎（测试用）。"""
        self._response_willingness = 1.0
        self._topic_resistance_map.clear()
        self._active_resistant_topic = ""
        self._last_trigger_reason = ""
        self._current_emotion_tag = "NEUTRAL"
        self._current_mood_state = "steady"
        self._current_valence = 0.0
        self._current_arousal = 0.3


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _classify_willingness(willingness: float) -> str:
    """将意愿值分类为等级字符串。"""
    if willingness >= WILLINGNESS_NORMAL:
        return "NORMAL"
    elif willingness >= WILLINGNESS_SUBDUED:
        return "SUBDUED"
    elif willingness >= WILLINGNESS_RELUCTANT:
        return "RELUCTANT"
    elif willingness >= WILLINGNESS_SILENT:
        return "SILENT"
    else:
        return "MUTE"


def _now_ms() -> int:
    return int(time.time() * 1000)
