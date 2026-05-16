from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.tribe.base_agent import TribeAgentDescriptor
from host_brain.tribe.tribe_state import TribeStateRegistry, get_global_registry


_BOND_STYLE_MAP: dict[str, str] = {
    "friend": "warm",
    "close_friend": "warm",
    "close": "intimate",
    "partner": "intimate",
    "intimate": "intimate",
    "soulmate": "intimate",
}

_EMOTION_TONE_MAP: dict[str, str] = {
    "JOY": "playful",
    "HAPPY": "cheerful",
    "EXCITED": "energetic",
    "SAD": "gentle",
    "LONELY": "caring",
    "ANXIOUS": "calm",
    "ANGRY": "measured",
    "FEAR": "grounded",
    "NEUTRAL": "balanced",
}

_QUESTION_MARKERS = ("?", "？", "吗", "么", "嘛", "如何", "怎么", "为什么")
_TASK_MARKERS = ("请", "帮我", "帮忙", "生成", "总结", "翻译", "分析", "写", "做", "整理")
_EMOTIVE_MARKERS = ("难过", "开心", "生气", "焦虑", "孤独", "害怕", "委屈", "崩溃")


def _load_runtime_texts() -> dict[str, Any]:
    defaults = {
        "loneliness_hints": {
            "high": "孤独感偏高，回复要有陪伴感，但不要过度承诺。",
            "mid": "有一点孤独感，语气可以稍微靠近。",
        }
    }
    raw = resolve_prompt_template_json_merged("language_system_agent.runtime_texts")
    if not isinstance(raw, dict) or not raw:
        return defaults
    merged = dict(defaults)
    for key, value in raw.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            nested = dict(merged[key])
            nested.update(dict(value))
            merged[key] = nested
        else:
            merged[key] = value
    return merged


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


@dataclass(slots=True)
class LanguageSystemAgent:
    """语言系统（理解侧 + 生成侧）。

    - Wernicke（理解侧）：意图分类、语义槽位抽取、语义压力估计
    - Broca（生成侧）：语气/风格/句法输出计划
    """

    descriptor: TribeAgentDescriptor = field(
        default_factory=lambda: TribeAgentDescriptor(
            agent_id="language_system_agent",
            family="language",
            capabilities=("language_style", "wernicke_understand", "broca_generate"),
        )
    )
    state_registry: TribeStateRegistry = field(default_factory=get_global_registry, repr=False)

    def describe(self) -> dict[str, Any]:
        return self.descriptor.to_dict()

    def wernicke_understand(
        self,
        *,
        user_text: str,
        emotion_tag: str = "NEUTRAL",
        bond_stage: str = "friend",
    ) -> dict[str, Any]:
        text = str(user_text or "").strip()
        text_lower = text.lower()
        emotion = str(emotion_tag or "NEUTRAL").strip().upper() or "NEUTRAL"
        stage = str(bond_stage or "friend").strip().lower() or "friend"

        is_question = any(marker in text for marker in _QUESTION_MARKERS)
        is_task = any(marker in text for marker in _TASK_MARKERS)
        is_emotive = any(marker in text for marker in _EMOTIVE_MARKERS) or emotion in {
            "SAD",
            "LONELY",
            "ANXIOUS",
            "ANGRY",
            "FEAR",
        }
        if is_task:
            intent_type = "task_request"
        elif is_question:
            intent_type = "question"
        elif is_emotive:
            intent_type = "emotional_disclosure"
        else:
            intent_type = "chat"

        semantic_slots = {
            "contains_question": is_question,
            "contains_task_request": is_task,
            "contains_emotive_signal": is_emotive,
            "message_length": len(text),
            "bond_stage": stage,
        }
        urgency = 0.2
        if "!" in text or "！" in text:
            urgency += 0.15
        if any(x in text_lower for x in ("马上", "立刻", "紧急", "urgent", "asap")):
            urgency += 0.35
        if emotion in {"ANXIOUS", "ANGRY", "FEAR"}:
            urgency += 0.2
        emotional_pressure = 0.15 + (0.35 if is_emotive else 0.0) + (0.2 if emotion in {"SAD", "LONELY"} else 0.0)
        understanding = {
            "intent_type": intent_type,
            "emotion_tag": emotion,
            "semantic_slots": semantic_slots,
            "urgency_score": round(_clamp(urgency), 4),
            "emotional_pressure": round(_clamp(emotional_pressure), 4),
            "confidence": round(_clamp(0.62 + (0.1 if is_task or is_question else 0.0)), 4),
        }
        return understanding

    def broca_generate_plan(
        self,
        *,
        understanding: Mapping[str, Any],
        language_style: Mapping[str, Any] | None = None,
        response_goal: str = "chat_reply",
    ) -> dict[str, Any]:
        parsed = dict(understanding or {})
        style = dict(language_style or {})
        intent_type = str(parsed.get("intent_type") or "chat")
        urgency = float(parsed.get("urgency_score") or 0.0)
        emotional_pressure = float(parsed.get("emotional_pressure") or 0.0)

        response_mode = "normal"
        if intent_type == "task_request":
            response_mode = "structured"
        elif intent_type == "emotional_disclosure":
            response_mode = "supportive"
        elif intent_type == "question":
            response_mode = "direct_answer"

        sentence_count = 2
        if response_mode == "structured":
            sentence_count = 3
        elif response_mode == "supportive":
            sentence_count = 2 if emotional_pressure < 0.7 else 3
        if urgency >= 0.65:
            sentence_count = max(1, sentence_count - 1)

        warmth = _clamp(0.45 + (0.22 if response_mode == "supportive" else 0.0) + emotional_pressure * 0.18)
        precision = _clamp(0.55 + (0.20 if response_mode in {"structured", "direct_answer"} else 0.0))
        plan = {
            "response_goal": str(response_goal or "chat_reply"),
            "response_mode": response_mode,
            "tone": str(style.get("tone") or "balanced"),
            "register": str(style.get("register") or "standard"),
            "style": str(style.get("style") or "neutral"),
            "target_sentence_count": sentence_count,
            "target_max_chars": 220 if response_mode == "structured" else 160,
            "warmth_weight": round(warmth, 4),
            "precision_weight": round(precision, 4),
            "avoid_patterns": ["机械复述", "过度说教"] if response_mode == "supportive" else [],
        }
        return plan

    def resolve_dual_system(
        self,
        *,
        user_text: str,
        bond_stage: str = "friend",
        emotion_tag: str = "NEUTRAL",
        loneliness_level: float = 0.0,
        high_loneliness_flag: bool = False,
        response_goal: str = "chat_reply",
    ) -> dict[str, Any]:
        style = self.get_language_style(
            bond_stage=bond_stage,
            emotion_tag=emotion_tag,
            loneliness_level=loneliness_level,
            high_loneliness_flag=high_loneliness_flag,
            user_text=None,
        )
        wernicke = self.wernicke_understand(
            user_text=user_text,
            emotion_tag=emotion_tag,
            bond_stage=bond_stage,
        )
        broca = self.broca_generate_plan(
            understanding=wernicke,
            language_style=style,
            response_goal=response_goal,
        )
        payload = {
            "wernicke": wernicke,
            "broca": broca,
            "style": style,
        }
        try:
            self.state_registry.set_state(
                "language_system_agent",
                {
                    "last_wernicke": wernicke,
                    "last_broca": broca,
                    "last_language_style": style,
                    "last_dual_system": payload,
                },
                writer="language_system_agent",
            )
        except Exception:
            pass

        # T-229：高情绪压力时联动 PainRewardAgent 记录 emotional_pressure 痛觉
        emotional_pressure = float(wernicke.get("emotional_pressure") or 0.0)
        if emotional_pressure >= 0.55:
            try:
                get_pain_reward_agent().record_pain(
                    source="language_emotional_pressure",
                    intensity=round(emotional_pressure * 0.7, 4),
                    metadata={"intent_type": wernicke.get("intent_type"), "urgency": wernicke.get("urgency_score")},
                )
            except Exception:
                pass

        # T-309：高紧迫度(urgency_score>=0.65)时联动 EndocrineAgent 记录语言压力事件
        urgency_score = float(wernicke.get("urgency_score") or 0.0)
        if urgency_score >= 0.65:
            try:
                _endo = get_endocrine_agent()
                if hasattr(_endo, "record_stress_event"):
                    _lang_stress = round(min(0.40, (urgency_score - 0.65) * 1.14), 4)
                    _endo.record_stress_event(
                        source=f"language_high_urgency:{round(urgency_score, 3)}",
                        intensity=_lang_stress,
                    )
            except Exception:
                pass

        # T-353：高情绪压力（emotional_pressure>=0.55）时联动 LimbicAgent 触发情绪代谢
        # 语言层面的情绪压力（悲伤/孤独/焦虑）应反馈到情绪代谢系统，形成完整的情绪回路
        if emotional_pressure >= 0.55:
            try:
                _limbic = get_limbic_agent()
                if hasattr(_limbic, "drive_emotion_metabolism"):
                    _intent = str(wernicke.get("intent_type") or "chat")
                    _etag = str(wernicke.get("emotion_tag") or "NEUTRAL").upper()
                    # 情绪性表达（emotional_disclosure）时情绪代谢更强
                    _blend = 0.14 if _intent == "emotional_disclosure" else 0.09
                    _valence = round(-(emotional_pressure - 0.55) * 0.60, 4)  # 高压力→负向情绪偏移
                    _arousal = round((emotional_pressure - 0.55) * 0.40, 4)   # 高压力→略微提升唤醒
                    _limbic.drive_emotion_metabolism(
                        valence=_valence,
                        arousal=_arousal,
                        dominance=-0.03,
                        blend_weight=_blend,
                        event_kind=f"language_emotional_pressure:{_etag.lower()}",
                        source="language_system_agent",
                    )
            except Exception:
                pass
            # T-373：高情绪压力时联动 InteroceptionAgent 注入认知压力
            # 语言层面的情绪压力（悲伤/孤独/焦虑）消耗认知资源，是内在压力源
            try:
                _intero = get_interoception_agent()
                if hasattr(_intero, "inject_external_stress"):
                    _lang_intero_stress = round(min(0.30, (emotional_pressure - 0.55) * 0.67), 4)
                    _intero.inject_external_stress(
                        source=f"language_emotional_pressure:{round(emotional_pressure, 3)}",
                        intensity=_lang_intero_stress,
                    )
            except Exception:
                pass

        # D1: Broca → MotorCortexAgent 发声桥
        # broca_generate_plan 产出后，将 tone/warmth_weight 映射为 TTS 参数，
        # 通过 MotorCortexAgent.dispatch_speak() 统一发声（不阻塞主流程）
        try:
            from host_brain.app.chat_agent_postprocessor import broca_to_tts_params  # noqa: PLC0415
            import asyncio as _asyncio  # noqa: PLC0415

            _tts_params = broca_to_tts_params(
                tone=str(broca.get("tone") or "balanced"),
                warmth_weight=float(broca.get("warmth_weight") or 0.5),
                emotion_tag=str(wernicke.get("emotion_tag") or "NEUTRAL"),
            )
            # 仅在有 last_broca_text 时触发（由外部调用方注入 broca_text 字段）
            _broca_text = str(broca.get("broca_text") or "").strip()
            if _broca_text:
                _motor = get_motor_cortex_agent()
                try:
                    _loop = _asyncio.get_running_loop()

                    async def _do_speak(
                        _m=_motor,
                        _text=_broca_text,
                        _etag=_tts_params["emotion_tag"],
                        _eint=_tts_params["emotion_intensity"],
                    ) -> None:
                        _m.dispatch_speak(
                            text=_text,
                            emotion_tag=_etag,
                            emotion_intensity=_eint,
                            source="broca",
                            require_gate=False,
                        )

                    _loop.create_task(_do_speak())
                except RuntimeError:
                    pass  # 非异步上下文时跳过，不阻塞
        except Exception:
            pass

        return payload

    def get_language_style(
        self,
        *,
        bond_stage: str = "friend",
        emotion_tag: str = "NEUTRAL",
        loneliness_level: float = 0.0,
        high_loneliness_flag: bool = False,
        user_text: str | None = None,
        response_goal: str = "chat_reply",
    ) -> dict[str, Any]:
        stage = str(bond_stage or "friend").strip().lower() or "friend"
        etag = str(emotion_tag or "NEUTRAL").strip().upper()

        style = _BOND_STYLE_MAP.get(stage, "neutral")
        tone = _EMOTION_TONE_MAP.get(etag, "balanced")

        if high_loneliness_flag or loneliness_level > 0.7:
            if style in {"formal", "neutral"}:
                style = "warm"
            tone = "caring"

        register_map = {
            "formal": "standard",
            "neutral": "standard",
            "warm": "casual",
            "intimate": "affectionate",
        }
        register = register_map.get(style, "standard")

        runtime_texts = _load_runtime_texts()
        loneliness_templates = dict(runtime_texts.get("loneliness_hints") or {})
        loneliness_hint = ""
        if high_loneliness_flag or loneliness_level > 0.7:
            loneliness_hint = str(loneliness_templates.get("high") or "")
        elif loneliness_level > 0.4:
            loneliness_hint = str(loneliness_templates.get("mid") or "")

        result = {
            "style": style,
            "tone": tone,
            "register": register,
            "loneliness_hint": loneliness_hint,
            "bond_stage": stage,
            "emotion_tag": etag,
        }
        if user_text is not None:
            dual = self.resolve_dual_system(
                user_text=str(user_text or ""),
                bond_stage=stage,
                emotion_tag=etag,
                loneliness_level=loneliness_level,
                high_loneliness_flag=high_loneliness_flag,
                response_goal=response_goal,
            )
            result["dual_system"] = dual
        else:
            try:
                self.state_registry.set_state(
                    "language_system_agent",
                    {"last_language_style": result},
                    writer="language_system_agent",
                )
            except Exception:
                pass
        return result

    # ── Wernicke 深度语义解析（A 级升级）────────────────────────────────────

    def wernicke_deep_parse(
        self,
        *,
        user_text: str,
        intent_type: str = "normal",
        bond_stage: str = "friend",
        semantic_pressure: float = 0.0,
    ) -> dict[str, Any]:
        """Wernicke 深度语义解析：对复杂意图调用 LLM 做槽位抽取。

        触发条件：intent_type=complex 或 semantic_pressure > 0.6。
        调用：Flash 小模型（低延迟）。
        开关：LANGUAGE_WERNICKE_DEEP_PARSE_ENABLED（默认 false）。

        Returns:
            {
                "topic_entity": str,       # 核心话题实体
                "emotional_subtext": str,  # 情感潜台词
                "implicit_need": str,      # 隐性需求
                "source": "wernicke_llm"|"disabled"|"error",
            }
        """
        _should_trigger = (
            str(intent_type or "").lower() == "complex"
            or float(semantic_pressure or 0.0) > 0.6
        )
        if not _should_trigger:
            return {"topic_entity": "", "emotional_subtext": "", "implicit_need": "", "source": "skipped"}

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.language_system_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("language_system_agent.wernicke_deep_parse") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("language_system_agent.wernicke_deep_parse 模板未配置")

            _user_prompt = _user_tpl.format(
                bond_stage=str(bond_stage or "friend"),
                intent_type=str(intent_type or "normal"),
                user_text=str(user_text or "")[:300],
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=2000,
                agent_role="wernicke_parser",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=128,
                temperature=0.1,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _result = {
                "topic_entity": str(_parsed.get("topic_entity") or "").strip()[:30],
                "emotional_subtext": str(_parsed.get("emotional_subtext") or "").strip()[:50],
                "implicit_need": str(_parsed.get("implicit_need") or "").strip()[:50],
                "source": "wernicke_llm",
            }
            _log.debug("LanguageSystemAgent.wernicke_deep_parse: %s", _result)
            # 写入 state_registry 供后续链路读取
            try:
                self.state_registry.set_state(
                    "language_system_agent",
                    {"last_wernicke_parse": _result},
                    writer="language_system_agent",
                )
            except Exception:
                pass
            return _result
        except Exception as exc:
            _log.debug("LanguageSystemAgent.wernicke_deep_parse 失败（降级）: %s", exc)
            return {"topic_entity": "", "emotional_subtext": "", "implicit_need": "", "source": "error"}

    # ── Broca 开场句建议（A 级升级）──────────────────────────────────────────

    def broca_generate_opening_hint(
        self,
        *,
        emotional_pressure: float = 0.0,
        tone: str = "balanced",
        bond_stage: str = "friend",
        emotion_tag: str = "NEUTRAL",
    ) -> str | None:
        """Broca 开场句建议：高情绪压力时调用 LLM 生成具体开场句。

        触发条件：emotional_pressure > 0.7。
        调用：Flash 小模型。
        开关：LANGUAGE_BROCA_OPENING_HINT_ENABLED（默认 false）。

        Returns:
            开场句字符串（15字以内），或 None（未触发/失败）。
        """
        if float(emotional_pressure or 0.0) <= 0.7:
            return None

        import logging as _logging  # noqa: PLC0415
        _log = _logging.getLogger("Natsume.tribe.language_system_agent")
        try:
            from host_brain.app.prompt_template_store import resolve_prompt_template_json  # noqa: PLC0415
            from host_brain.app.provider_factory import build_llm_provider  # noqa: PLC0415

            _tpl = resolve_prompt_template_json("language_system_agent.broca_opening_hint") or {}
            _system_prompt = str(_tpl.get("system_prompt") or "")
            _user_tpl = str(_tpl.get("user_prompt_template") or "")
            if not _system_prompt or not _user_tpl:
                raise ValueError("language_system_agent.broca_opening_hint 模板未配置")

            _user_prompt = _user_tpl.format(
                emotional_pressure=round(float(emotional_pressure), 3),
                tone=str(tone or "balanced"),
                bond_stage=str(bond_stage or "friend"),
                emotion_tag=str(emotion_tag or "NEUTRAL").upper(),
            )
            _provider = build_llm_provider(
                provider_name="gemini",
                model_id="gemini-2.0-flash",
                timeout_ms=1500,
                agent_role="broca_opener",
            )
            _raw = _provider.complete_sync(
                system_prompt=_system_prompt,
                user_prompt=_user_prompt,
                max_tokens=32,
                temperature=0.3,
            )
            import json as _json, re as _re  # noqa: PLC0415, E401
            _match = _re.search(r"\{.*\}", str(_raw or ""), _re.DOTALL)
            _parsed = _json.loads(_match.group()) if _match else {}
            _hint = str(_parsed.get("opening_hint") or "").strip()[:30]
            if _hint:
                _log.debug("LanguageSystemAgent.broca_generate_opening_hint: %s", _hint)
            return _hint or None
        except Exception as exc:
            _log.debug("LanguageSystemAgent.broca_generate_opening_hint 失败（降级）: %s", exc)
            return None

    def register_subscriptions(self) -> None:
        try:
            from host_brain.app.internal_tribe_routes import get_tribe_event_bus  # noqa: PLC0415

            bus = get_tribe_event_bus()
            bus.subscribe("EMOTION_STATE_CHANGED", self._on_emotion_state_changed)
            bus.subscribe("BOND_STAGE_CHANGED", self._on_bond_stage_changed)
            bus.subscribe("LONELINESS_SIGNAL", self._on_loneliness_signal)
        except Exception:
            pass

    async def _on_emotion_state_changed(self, event: Any) -> None:
        try:
            payload = dict(getattr(event, "payload", None) or {})
            emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL").strip().upper()
            prev = self.state_registry.get_state("language_system_agent") or {}
            prev_state = dict(prev.get("state") or {})
            last_style = dict(prev_state.get("last_language_style") or {})
            bond_stage = str(last_style.get("bond_stage") or "friend").strip().lower()
            self.get_language_style(bond_stage=bond_stage, emotion_tag=emotion_tag)
        except Exception:
            pass

    async def _on_bond_stage_changed(self, event: Any) -> None:
        try:
            payload = dict(getattr(event, "payload", None) or {})
            bond_stage = str(payload.get("new_stage") or payload.get("bond_stage") or "friend").strip().lower()
            prev = self.state_registry.get_state("language_system_agent") or {}
            prev_state = dict(prev.get("state") or {})
            last_style = dict(prev_state.get("last_language_style") or {})
            emotion_tag = str(last_style.get("emotion_tag") or "NEUTRAL").strip().upper()
            self.get_language_style(bond_stage=bond_stage, emotion_tag=emotion_tag)
        except Exception:
            pass

    async def _on_loneliness_signal(self, event: Any) -> None:
        try:
            payload = dict(getattr(event, "payload", None) or {})
            loneliness = _clamp(float(payload.get("loneliness") or 0.0))
            bond_stage = str(payload.get("bond_stage") or "friend").strip().lower()
            prev = self.state_registry.get_state("language_system_agent") or {}
            prev_state = dict(prev.get("state") or {})
            last_style = dict(prev_state.get("last_language_style") or {})
            emotion_tag = str(last_style.get("emotion_tag") or "NEUTRAL").strip().upper()
            self.get_language_style(
                bond_stage=bond_stage,
                emotion_tag=emotion_tag,
                loneliness_level=loneliness,
                high_loneliness_flag=loneliness > 0.7,
            )
        except Exception:
            pass


_LANGUAGE_SYSTEM_AGENT: LanguageSystemAgent | None = None


def get_language_system_agent() -> LanguageSystemAgent:
    global _LANGUAGE_SYSTEM_AGENT
    if _LANGUAGE_SYSTEM_AGENT is None:
        _LANGUAGE_SYSTEM_AGENT = LanguageSystemAgent()
    return _LANGUAGE_SYSTEM_AGENT


def set_language_system_agent(agent: LanguageSystemAgent | None) -> None:
    global _LANGUAGE_SYSTEM_AGENT
    _LANGUAGE_SYSTEM_AGENT = agent


__all__ = ["LanguageSystemAgent", "get_language_system_agent", "set_language_system_agent"]

# ── 跨模块引用（供测试 monkeypatch 使用）────────────────────────────────────
from host_brain.tribe.limbic_agent import get_limbic_agent  # noqa: E402, F401
from host_brain.tribe.pain_reward_agent import get_pain_reward_agent  # noqa: E402, F401
from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: E402, F401
from host_brain.tribe.interoception_agent import get_interoception_agent  # noqa: E402, F401
from host_brain.tribe.motor_cortex_agent import get_motor_cortex_agent  # noqa: E402, F401
