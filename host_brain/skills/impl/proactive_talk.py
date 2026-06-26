"""Proactive Talk Skill——主动对话。

由调度器在空闲/Normal 时触发；根据时段 + 上次对话间隔
→ LLM 生成主动问候/关心 → 推送到 UI 队列。
空闲阈值 < 4h 则跳过，避免打扰。
"""

from __future__ import annotations

from collections.abc import Mapping
import datetime
import logging
from typing import Any, Callable

from host_brain.core.errors import SCHED_CONDITION_UNMET, SCHED_NO_CONTENT
from host_brain.core.ids import now_ms, new_event_id

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 方向 B：情感驱动问候——情感上下文融合到主动对话
# ---------------------------------------------------------------------------

def _build_emotion_hint_for_proactive(
    emotion_context: dict[str, Any] | None,
) -> str:
    """方向 B：从情感上下文构建 LLM prompt 情感提示。

    Args:
        emotion_context: 包含以下可选字段：
            - emotion_tag: 当前情感标签
            - mood_state: 当前心情状态
            - user_emotion_profile: 用户情感画像摘要
            - emotion_timeline_summary: 情感时间线摘要
            - triggered_emotions: 主动情感触发器结果
            - emotion_diary: 最近情感日记

    Returns:
        拼接好的情感提示文本（可直接注入 LLM prompt）
    """
    if not emotion_context or not isinstance(emotion_context, dict):
        return ""

    hints: list[str] = []

    # 1. 当前情感状态
    tag = str(emotion_context.get("emotion_tag") or "").strip()
    mood = str(emotion_context.get("mood_state") or "").strip()
    if tag and tag != "NEUTRAL":
        _EMOTION_ZH: dict[str, str] = {
            "HAPPY": "开心", "EXCITED": "兴奋", "SAD": "有点难过",
            "ANGRY": "有些生气", "CALM": "平静", "THINKING": "沉思",
            "LOVING": "充满爱意", "EMBARRASSED": "害羞", "CONFUSED": "困惑",
            "BORED": "有点无聊", "PROUD": "自豪", "GRATEFUL": "感激",
            "ANXIOUS": "有些焦虑", "FRUSTRATED": "沮丧", "SURPRISED": "惊喜",
        }
        zh = _EMOTION_ZH.get(tag, tag)
        hints.append(f"你现在的心情是{zh}的")

    # 2. 用户情感画像（方向 F 输出）
    profile = emotion_context.get("user_emotion_profile")
    if isinstance(profile, dict):
        profile_hint = str(profile.get("profile_hint") or "").strip()
        if profile_hint:
            hints.append(profile_hint)

    # 3. 主动情感触发器结果
    triggers = emotion_context.get("triggered_emotions")
    if isinstance(triggers, list) and triggers:
        trigger_tags = [str(t.get("emotion_tag", "")) for t in triggers[:2] if isinstance(t, dict)]
        if trigger_tags:
            hints.append(f"你感受到了{'、'.join(trigger_tags)}的情绪涌上来")

    # 4. 情感日记片段（方向 D 输出）
    diary = emotion_context.get("emotion_diary")
    if isinstance(diary, dict):
        diary_text = str(diary.get("diary_text") or "").strip()
        if diary_text:
            hints.append(f"你最近的情感日记写道：{diary_text[:60]}")

    return "；".join(hints) + "。" if hints else ""


def _build_motive_hint_for_proactive(
    motive_state: dict[str, Any] | None,
) -> str:
    """把 motive_state 转成主动对话可消费的自然语言提示。"""
    if not motive_state or not isinstance(motive_state, dict):
        return ""

    reach_out = float(motive_state.get("reach_out_drive") or 0.0)
    care = float(motive_state.get("care_drive") or 0.0)
    continuity = float(motive_state.get("continuity_drive") or 0.0)
    withdraw = float(motive_state.get("withdraw_drive") or 0.0)
    protect = float(motive_state.get("self_protect_drive") or 0.0)

    hints: list[str] = []
    if reach_out >= 0.6 or continuity >= 0.55:
        hints.append("你现在其实想主动靠近对方，延续联系感")
    if care >= 0.55:
        hints.append("你会更想照顾和安抚对方")
    if withdraw >= 0.6 or protect >= 0.6:
        hints.append("但也要稍微收一点，不要显得打扰或越界")
    return "；".join(hints) + "。" if hints else ""


def _coerce_render_policy(raw_policy: Any) -> dict[str, Any]:
    if isinstance(raw_policy, Mapping):
        return dict(raw_policy)
    return {}


def _render_policy_blocks_proactive(render_policy: Mapping[str, Any] | None) -> bool:
    policy = _coerce_render_policy(render_policy)
    if not policy:
        return False
    return not bool(policy.get("allow_proactive_after_reply", True))


def _apply_render_policy_to_proactive_text(text: str, render_policy: Mapping[str, Any] | None) -> str:
    normalized = str(text or "").strip()
    policy = _coerce_render_policy(render_policy)
    if not normalized or not policy:
        return normalized
    try:
        from host_brain.app.surface_reply_preprocessor import _apply_render_policy_hard_guard  # noqa: PLC0415

        guarded = _apply_render_policy_hard_guard(normalized, policy)
        intimacy_ceiling = float(policy.get("intimacy_ceiling", 1.0) or 1.0)
        if intimacy_ceiling <= 0.35 and any(marker in str(guarded or "") for marker in ("亲爱的", "宝贝", "宝宝", "抱抱", "亲亲")):
            return "你先缓一缓。"
        return str(guarded or "").strip()
    except Exception as exc:
        logger.debug("proactive render_policy hard guard failed: %s", exc)
        return normalized

# Skill 元数据常量
SKILL_NAME = "proactive_talk"
_SCENE_BLOCKING_TAGS = {"focus_work", "fullscreen_video", "sleeping_screen", "meeting"}
_SCENE_GAMING_TAGS = {"gaming", "gameplay", "battle", "lobby"}


def _normalize_scene_tag_values(raw_tags: Any) -> list[str]:
    values: list[str] = []

    def _push(value: Any) -> None:
        if isinstance(value, str):
            for part in str(value).replace(";", ",").replace("|", ",").split(","):
                normalized = part.strip().lower().replace("-", "_").replace(" ", "_")
                normalized = normalized.strip("_")
                if normalized:
                    values.append(normalized)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                _push(item)

    _push(raw_tags)

    alias_map: dict[str, list[str]] = {
        "focus": ["focus_work"],
        "working": ["focus_work"],
        "coding": ["focus_work"],
        "editing": ["focus_work"],
        "study": ["focus_work"],
        "document": ["focus_work"],
        "focus_work": ["focus_work"],
        "video": ["fullscreen_video"],
        "fullscreen": ["fullscreen_video"],
        "fullscreen_video": ["fullscreen_video"],
        "movie": ["fullscreen_video"],
        "sleep": ["sleeping_screen"],
        "screensaver": ["sleeping_screen"],
        "lock_screen": ["sleeping_screen"],
        "sleeping_screen": ["sleeping_screen"],
        "meeting": ["meeting"],
        "conference": ["meeting"],
        "video_call": ["meeting"],
        "gaming": ["gaming"],
        "game": ["gaming"],
        "gameplay": ["gaming", "gameplay"],
        "battle": ["gaming", "battle"],
        "combat": ["gaming", "battle"],
        "lobby": ["gaming", "lobby"],
    }

    normalized: list[str] = []
    for tag in values:
        for item in alias_map.get(tag, [tag]):
            if item not in normalized:
                normalized.append(item)
    return normalized


def _coerce_scene_awareness(raw_scene: Any) -> dict[str, Any]:
    if not isinstance(raw_scene, Mapping):
        return {
            "scene_tags": [],
            "current_activity": None,
            "app_name": None,
            "companion_suitable": None,
            "source": "",
            "confidence": None,
            "has_screenshot": False,
        }

    scene_tags = _normalize_scene_tag_values(raw_scene.get("scene_tags"))
    current_activity = str(raw_scene.get("current_activity") or "").strip() or None
    app_name = str(raw_scene.get("app_name") or "").strip() or None
    companion_suitable = raw_scene.get("companion_suitable") if isinstance(raw_scene.get("companion_suitable"), bool) else None
    confidence = raw_scene.get("confidence")
    try:
        if confidence is not None:
            confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = None

    inferred_tags = _normalize_scene_tag_values(current_activity)
    for tag in inferred_tags:
        if tag not in scene_tags:
            scene_tags.append(tag)

    if companion_suitable is None:
        if any(tag in _SCENE_BLOCKING_TAGS for tag in scene_tags):
            companion_suitable = False
        elif any(tag in _SCENE_GAMING_TAGS for tag in scene_tags):
            companion_suitable = True

    return {
        "scene_tags": scene_tags,
        "current_activity": current_activity,
        "app_name": app_name,
        "companion_suitable": companion_suitable,
        "source": str(raw_scene.get("source") or "").strip(),
        "confidence": confidence,
        "has_screenshot": bool(raw_scene.get("has_screenshot")),
    }


def _scene_awareness_blocks_proactive(scene_awareness: Mapping[str, Any] | None) -> tuple[bool, str]:
    scene = _coerce_scene_awareness(scene_awareness)
    blocked_tags = [tag for tag in scene.get("scene_tags") or [] if tag in _SCENE_BLOCKING_TAGS]
    if blocked_tags:
        return True, f"scene_tags={','.join(blocked_tags)}"
    if scene.get("companion_suitable") is False:
        current_activity = str(scene.get("current_activity") or "")
        return True, f"companion_suitable=false activity={current_activity or '-'}"
    return False, ""


def _build_scene_hint_for_proactive(scene_awareness: Mapping[str, Any] | None) -> str:
    scene = _coerce_scene_awareness(scene_awareness)
    scene_tags = scene.get("scene_tags") or []
    if not scene_tags:
        return ""
    if any(tag in _SCENE_GAMING_TAGS for tag in scene_tags):
        return "当前更像游戏中的陪伴场景，适合短句、轻陪伴、不打断操作。"
    if any(tag in _SCENE_BLOCKING_TAGS for tag in scene_tags):
        return "当前场景不适合主动打扰，应当避免发起额外对话。"
    return f"当前场景标签：{', '.join(scene_tags[:4])}。主动表达保持轻一点。"


SKILL_CLEARANCE = "LOW"
SKILL_RISK_LEVEL = 1
SKILL_SCOPE = "chat"
SKILL_REVERSIBLE = False

# 状态
STATUS_EXECUTED = "EXECUTED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

# 默认空闲阈值（毫秒）: 4 小时
DEFAULT_IDLE_THRESHOLD_MS = 4 * 60 * 60 * 1000

# 允许主动对话的 sys_mode
ALLOWED_MODES = {"normal", "sleep"}

# 回调类型
LLMGenerator = Callable[[dict[str, Any]], dict[str, Any]]
UIPusher = Callable[[dict[str, Any]], dict[str, Any]]


def _build_template_greeting(
    *,
    user_name: str,
    time_period: str,
    scene_awareness: Mapping[str, Any] | None = None,
) -> str:
    scene_tags = list((scene_awareness or {}).get("scene_tags") or [])
    if any(tag in _SCENE_GAMING_TAGS for tag in scene_tags):
        return f"{user_name}，我先轻轻陪你一会儿，不打扰你操作。"
    templates = {
        "morning": f"{user_name}早上好～新的一天开始了呢",
        "noon": f"{user_name}中午了，记得吃饭哦～",
        "evening": f"{user_name}晚上好～今天过得怎么样？",
        "night": f"{user_name}夜深了，注意休息哦～",
    }
    return templates.get(time_period, f"{user_name}，好久没聊了，在忙什么呢？")


def _get_time_period() -> str:
    """获取当前时段标签。"""
    hour = datetime.datetime.now().hour
    if 5 <= hour < 9:
        return "morning"
    elif 9 <= hour < 12:
        return "forenoon"
    elif 12 <= hour < 14:
        return "noon"
    elif 14 <= hour < 18:
        return "afternoon"
    elif 18 <= hour < 22:
        return "evening"
    else:
        return "night"


def execute_proactive_talk(
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行主动对话。

    args:
        idle_threshold_ms: 空闲阈值（毫秒），默认 4h
        topic_hint: 话题提示（可选）

    context:
        sys_mode: 当前系统模式
        last_interaction_ts: 上次对话时间戳（ms）
        request_id / intent_id
        llm_generator: LLM 生成回调
        ui_pusher: UI 推送回调
        bond_summary: Bond 摘要（可选）
        user_name: 用户昵称
        time_period: 时段（覆盖自动检测）
        emotion_context: 方向B 情感上下文（可选，含 emotion_tag/user_emotion_profile/triggered_emotions）
    """
    ctx = context or {}
    ts = now_ms()
    event_id = new_event_id()
    request_id = ctx.get("request_id")
    intent_id = ctx.get("intent_id")

    # sys_mode 检查
    sys_mode = str(ctx.get("sys_mode", "normal")).strip().lower()
    if sys_mode not in ALLOWED_MODES:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_CONDITION_UNMET,
            "detail": f"proactive_talk not allowed in sys_mode={sys_mode}",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 空闲时间检查
    idle_threshold = int(args.get("idle_threshold_ms", DEFAULT_IDLE_THRESHOLD_MS))
    last_ts = ctx.get("last_interaction_ts")
    if last_ts is not None:
        elapsed = ts - int(last_ts)
        if elapsed < idle_threshold:
            return {
                "status": STATUS_REJECTED,
                "event_id": event_id,
                "reason_code": SCHED_CONDITION_UNMET,
                "detail": f"idle time {elapsed}ms < threshold {idle_threshold}ms",
                "elapsed_ms": elapsed,
                "request_id": request_id,
                "ts_ms": ts,
            }

    # 时段检测
    time_period = str(ctx.get("time_period", "")).strip() or _get_time_period()
    user_name = str(ctx.get("user_name", "主人")).strip()

    # 方向 B：读取情感上下文
    emotion_context: dict[str, Any] | None = ctx.get("emotion_context")
    emotion_hint = _build_emotion_hint_for_proactive(emotion_context)
    motive_state: dict[str, Any] = dict(ctx.get("motive_state") or {})
    proactive_episode = motive_state.get("proactive_episode")
    if isinstance(proactive_episode, Mapping):
        proactive_episode = dict(proactive_episode)
    else:
        proactive_episode = {}
    motive_hint = _build_motive_hint_for_proactive(motive_state)
    render_policy: dict[str, Any] = _coerce_render_policy(ctx.get("render_policy"))
    scene_awareness: dict[str, Any] = _coerce_scene_awareness(ctx.get("scene_awareness"))
    scene_hint = _build_scene_hint_for_proactive(scene_awareness)

    if _render_policy_blocks_proactive(render_policy):
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_CONDITION_UNMET,
            "detail": (
                "render_policy blocked proactive_talk: "
                f"allow_proactive_after_reply={bool(render_policy.get('allow_proactive_after_reply', True))} "
                f"should_be_withdrawn={bool(render_policy.get('should_be_withdrawn', False))}"
            ),
            "request_id": request_id,
            "ts_ms": ts,
            "motive_state": motive_state,
            "render_policy": render_policy,
        }

    if motive_state:
        reach_out = float(motive_state.get("reach_out_drive") or 0.0)
        continuity = float(motive_state.get("continuity_drive") or 0.0)
        withdraw = float(motive_state.get("withdraw_drive") or 0.0)
        self_protect = float(motive_state.get("self_protect_drive") or 0.0)
        motive_push = max(reach_out, continuity)
        motive_pull = max(withdraw, self_protect)

        if motive_pull >= 0.75 and motive_push < 0.55:
            return {
                "status": STATUS_REJECTED,
                "event_id": event_id,
                "reason_code": SCHED_CONDITION_UNMET,
                "detail": (
                    "motive gate blocked proactive_talk: "
                    f"push={motive_push:.2f}, pull={motive_pull:.2f}"
                ),
                "request_id": request_id,
                "ts_ms": ts,
                "motive_state": motive_state,
            }

    scene_blocked, scene_block_reason = _scene_awareness_blocks_proactive(scene_awareness)
    if scene_blocked:
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_CONDITION_UNMET,
            "detail": f"scene_awareness blocked proactive_talk: {scene_block_reason}",
            "request_id": request_id,
            "ts_ms": ts,
            "motive_state": motive_state,
            "render_policy": render_policy,
            "scene_awareness": scene_awareness,
        }

    try:
        from host_brain.bio_ops.capabilities import evaluate_active_silence  # noqa: PLC0415

        active_silence = evaluate_active_silence(
            {
                "current_hour": int(ctx.get("current_hour") or datetime.datetime.now().hour),
                "user_stress": str(ctx.get("user_stress") or (emotion_context or {}).get("stress_level") or "").strip(),
                "explicit_help_request": bool(ctx.get("explicit_help_request")),
            }
        )
    except Exception as exc:
        active_silence = {
            "silence_policy": "normal_presence",
            "reason_code": "BIO_OPS_ACTIVE_SILENCE_TRACE_DEGRADED",
            "detail": str(exc)[:120],
        }

    if str(active_silence.get("silence_policy") or "") == "quiet_presence":
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_CONDITION_UNMET,
            "detail": "active_silence blocked proactive_talk: quiet_presence",
            "request_id": request_id,
            "ts_ms": ts,
            "motive_state": motive_state,
            "render_policy": render_policy,
            "scene_awareness": scene_awareness,
            "bio_ops_runtime_trace": {
                "source": "bio_ops.active_silence",
                "proposal_only": True,
                "active_silence": active_silence,
                "forbidden_actions": ["ui_push", "tts", "proactive_talk"],
            },
        }

    # LLM 生成主动问候
    llm_generator: LLMGenerator | None = ctx.get("llm_generator")
    greeting: str = ""
    fallback_to_template = False
    fallback_reason: str | None = None

    if llm_generator:
        try:
            # --- Memory context for life-feel proactive greetings ---
            recent_memories = ctx.get("recent_memories") or []
            pending_topics = ctx.get("pending_topics") or []
            last_user_emotion = ctx.get("last_user_emotion") or ""
            absence_hours = _compute_absence_hours(last_ts, ts)
            inner_weather = ctx.get("inner_weather") or ""

            llm_input = {
                "task": "proactive_talk",
                "time_period": time_period,
                "user_name": user_name,
                "topic_hint": args.get("topic_hint"),
                "bond_summary": ctx.get("bond_summary", {}),
                "ts_ms": ts,
                # Direction B: emotion-driven greeting context
                "emotion_hint": emotion_hint,
                "emotion_context": emotion_context or {},
                "motive_state": motive_state,
                "motive_hint": motive_hint,
                "render_policy": render_policy,
                "scene_awareness": scene_awareness,
                "scene_hint": scene_hint,
                # Life-feel: memory-grounded proactive context
                "recent_memories": recent_memories,
                "pending_topics": pending_topics,
                "last_user_emotion": last_user_emotion,
                "absence_hours": absence_hours,
                "inner_weather": inner_weather,
            }
            llm_result = llm_generator(llm_input)
            greeting = str(llm_result.get("greeting", llm_result.get("content", "")))
            fallback_to_template = bool(llm_result.get("fallback_to_template"))
            fallback_reason = str(llm_result.get("fallback_reason") or "").strip() or None
            if not greeting.strip():
                fallback_to_template = True
                fallback_reason = fallback_reason or "LLM_EMPTY_OUTPUT"
        except Exception as exc:
            logger.warning("LLM proactive talk failed: %s", exc)
            return {
                "status": STATUS_DEGRADED,
                "event_id": event_id,
                "reason_code": "PROACTIVE_LLM_UNAVAILABLE",
                "detail": f"LLM generation failed: {exc}",
                "request_id": request_id,
                "ts_ms": ts,
            }
    if (not llm_generator) or (fallback_to_template and not greeting.strip()):
        greeting = _build_template_greeting(
            user_name=user_name,
            time_period=time_period,
            scene_awareness=scene_awareness,
        )

    greeting = _apply_render_policy_to_proactive_text(greeting, render_policy)

    if not greeting.strip():
        return {
            "status": STATUS_REJECTED,
            "event_id": event_id,
            "reason_code": SCHED_NO_CONTENT,
            "detail": "LLM produced empty greeting",
            "request_id": request_id,
            "ts_ms": ts,
        }

    # 推送到 UI
    message = {
        "type": "proactive_talk",
        "content": greeting,
        "time_period": time_period,
        "event_id": event_id,
        "ts_ms": ts,
        # 方向 B：携带情感上下文供 UI 渲染（如表情切换）
        "emotion_tag": (emotion_context or {}).get("emotion_tag", "NEUTRAL"),
        "dominant_motive": motive_state.get("dominant_motive"),
        "proactive_episode": proactive_episode,
        "scene_tags": list(scene_awareness.get("scene_tags") or []),
        "scene_activity": scene_awareness.get("current_activity"),
    }

    ui_pusher: UIPusher | None = ctx.get("ui_pusher")
    push_result: dict[str, Any] | None = None
    if ui_pusher:
        try:
            push_result = ui_pusher(message)
        except Exception as exc:
            logger.warning("UI push failed for proactive talk: %s", exc)
            push_result = {"error": str(exc)}

    return {
        "status": STATUS_EXECUTED,
        "event_id": event_id,
        "reason_code": None,
        "detail": f"proactive talk sent: {time_period}",
        "message": message,
        "push_result": push_result,
        "request_id": request_id,
        "intent_id": intent_id,
        "ts_ms": ts,
        # 方向 B：审计字段
        "emotion_driven": bool(emotion_hint),
        "emotion_tag": (emotion_context or {}).get("emotion_tag", "NEUTRAL"),
        "motive_driven": bool(motive_hint),
        "motive_state": motive_state,
        "render_policy": render_policy,
        "scene_driven": bool(scene_hint),
        "scene_awareness": scene_awareness,
        "bio_ops_runtime_trace": {
            "source": "bio_ops.active_silence",
            "proposal_only": True,
            "active_silence": active_silence,
            "forbidden_actions": [],
        },
        "template_fallback_used": fallback_to_template,
        "template_fallback_reason": fallback_reason,
    }
