"""Deterministic VTube Studio channel controls and audit logging."""

from __future__ import annotations

import asyncio
import os
import logging
from dataclasses import dataclass
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AVATAR_FREE_ACTION_BLOCKED,
    AVATAR_TAG_INVALID,
    AVATAR_TRIGGER_INVALID,
    VTS_CONNECTION_FAILED,
    VTS_DEGRADED_FALLBACK,
)
from host_brain.core.ids import new_request_id, now_ms
from host_brain.channels.vts_runtime_mapping import (
    VTS_EMOTION_TAG_ALIASES,
    load_runtime_emotion_mapping,
)

_logger = logging.getLogger("Natsume.vts")

# 模块级主事件循环引用。
# 在 asyncio.to_thread 的 worker 线程中，get_event_loop() 会抛出 RuntimeError，
# 必须在主线程启动时通过 register_main_loop() 保存引用。
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def register_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Register the main event loop for use in worker threads.

    应在应用启动时（FastAPI lifespan startup）调用，确保
    asyncio.to_thread 的 worker 线程能通过 run_coroutine_threadsafe
    将协程提交到主事件循环。
    """
    global _MAIN_LOOP
    _MAIN_LOOP = loop
    _logger.debug("VTS 主事件循环已注册: %s", loop)


S_AVATAR_TALK_GATE = "S_AVATAR_TALK_GATE"
S_AVATAR_EXPRESSION_MAP = "S_AVATAR_EXPRESSION_MAP"

STATUS_APPLIED = "APPLIED"
STATUS_REJECTED = "REJECTED"
STATUS_DEGRADED = "DEGRADED"

TALK_START = "TALK_START"
TALK_END = "TALK_END"

TRIGGER_HOST = "HOST"
TRIGGER_LLM = "LLM"

EMOTION_TO_EXPRESSION_ID: dict[str, str] = load_runtime_emotion_mapping()

# M28-L（OPT-34）：未知情感 fallback 映射（按情感极性分组）
EMOTION_FALLBACK_MAP: dict[str, str] = {
    # 正向情感 → happy
    "POSITIVE": EMOTION_TO_EXPRESSION_ID.get("HAPPY", "HK_Happy"),
    # 负向情感 → sad
    "NEGATIVE": EMOTION_TO_EXPRESSION_ID.get("SAD", "哭哭"),
    # 中性/未知 → neutral
    "UNKNOWN": EMOTION_TO_EXPRESSION_ID.get("NEUTRAL", "移除所有按键表情"),
}

DEGRADED_FALLBACK_EXPRESSION_TAG = "NEUTRAL"
DEGRADED_FALLBACK_EXPRESSION_ID = EMOTION_TO_EXPRESSION_ID[DEGRADED_FALLBACK_EXPRESSION_TAG]

# ---------------------------------------------------------------------------
# 方向 C：VTS 情绪渐变动画——表情过渡帧计算
# ---------------------------------------------------------------------------

# emotion_tag → 近似 VAD 向量（用于过渡帧插值计算）
EMOTION_TAG_TO_VAD: dict[str, tuple[float, float, float]] = {
    "NEUTRAL":     (0.0,  0.0,  0.0),
    "HAPPY":       (0.6,  0.3,  0.3),
    "SAD":         (-0.5, -0.3, -0.3),
    "ANGRY":       (-0.6,  0.6,  0.5),
    "SURPRISED":   (0.2,  0.7,  0.0),
    "THINKING":    (0.0,  0.1,  0.4),
    "EXCITED":     (0.7,  0.7,  0.4),
    "CALM":        (0.2, -0.4,  0.1),
    "FRUSTRATED":  (-0.4,  0.3, -0.2),
    "ANXIOUS":     (-0.3,  0.5, -0.3),
    "LOVING":      (0.8,  0.4,  0.2),
    "EMBARRASSED": (-0.2,  0.3, -0.4),
    "CONFUSED":    (-0.1,  0.2, -0.2),
    "BORED":       (-0.2, -0.5, -0.1),
    "PROUD":       (0.5,  0.3,  0.6),
    "GRATEFUL":    (0.6,  0.2,  0.1),
}

# 过渡帧距离阈值：欧氏距离 > 此值才生成过渡帧（避免微小变化抖动）
_TRANSITION_DISTANCE_THRESHOLD = 0.3

# 最大过渡帧数（过多会让表情切换变慢）
_MAX_TRANSITION_FRAMES = 3

# 默认每帧间隔（毫秒）
_DEFAULT_FRAME_INTERVAL_MS = 150


@dataclass(frozen=True)
class EmotionTransitionFrame:
    """方向 C：表情过渡帧描述。"""
    frame_index: int          # 帧序号（0=首帧，N-1=末帧即目标帧）
    emotion_tag: str          # 该帧的 emotion_tag
    expression_id: str        # 该帧的 expression_id
    interpolation_t: float    # 插值系数 [0, 1]
    delay_ms: int             # 距离首帧的延迟（毫秒）
    vad: tuple[float, float, float]  # 该帧的 VAD 值


def build_emotion_transition_frames(
    *,
    from_tag: str | None,
    to_tag: str,
    frame_interval_ms: int = _DEFAULT_FRAME_INTERVAL_MS,
    max_frames: int = _MAX_TRANSITION_FRAMES,
) -> list[EmotionTransitionFrame]:
    """方向 C：构建从 from_tag 到 to_tag 的表情过渡帧序列。

    若两个标签距离小于阈值，或 from_tag 为空/相同，则直接返回仅含目标帧的列表。

    Args:
        from_tag: 当前表情标签（None 或空表示无历史表情）
        to_tag: 目标表情标签
        frame_interval_ms: 每帧间隔（毫秒），默认 150ms
        max_frames: 最大过渡帧数（不含目标帧），默认 3

    Returns:
        过渡帧列表（最后一帧始终是目标帧），最少 1 帧
    """
    from host_brain.core.metabolism import EmotionVector  # noqa: PLC0415

    to_tag_norm = str(to_tag or "").strip().upper() or "NEUTRAL"
    from_tag_norm = str(from_tag or "").strip().upper()

    to_vad = EMOTION_TAG_TO_VAD.get(to_tag_norm, (0.0, 0.0, 0.0))
    to_expr = EMOTION_TO_EXPRESSION_ID.get(to_tag_norm, DEGRADED_FALLBACK_EXPRESSION_ID)

    # 仅含目标帧的快速路径
    target_frame = EmotionTransitionFrame(
        frame_index=0,
        emotion_tag=to_tag_norm,
        expression_id=to_expr,
        interpolation_t=1.0,
        delay_ms=0,
        vad=to_vad,
    )

    # 无起始标签 / 起止相同 → 直接目标帧
    if not from_tag_norm or from_tag_norm == to_tag_norm:
        return [target_frame]

    from_vad = EMOTION_TAG_TO_VAD.get(from_tag_norm, (0.0, 0.0, 0.0))

    # 计算 VAD 欧氏距离
    start_vec = EmotionVector(valence=from_vad[0], arousal=from_vad[1], dominance=from_vad[2])
    end_vec = EmotionVector(valence=to_vad[0], arousal=to_vad[1], dominance=to_vad[2])
    dist = start_vec.distance(end_vec)

    # 距离小 → 跳过过渡
    if dist < _TRANSITION_DISTANCE_THRESHOLD:
        return [target_frame]

    # 根据距离动态计算过渡帧数：距离越大帧越多（上限 max_frames）
    num_transition = min(max_frames, max(1, int(dist / 0.4)))

    frames: list[EmotionTransitionFrame] = []
    total_steps = num_transition + 1  # 含目标帧

    for i in range(total_steps):
        t = (i + 1) / total_steps
        interp_vec = EmotionVector.interpolate(start_vec, end_vec, t)
        interp_tag = interp_vec.to_emotion_tag()
        interp_expr = EMOTION_TO_EXPRESSION_ID.get(interp_tag, DEGRADED_FALLBACK_EXPRESSION_ID)
        interp_vad = (
            round(interp_vec.valence, 3),
            round(interp_vec.arousal, 3),
            round(interp_vec.dominance, 3),
        )

        frames.append(EmotionTransitionFrame(
            frame_index=i,
            emotion_tag=interp_tag,
            expression_id=interp_expr,
            interpolation_t=round(t, 3),
            delay_ms=i * frame_interval_ms,
            vad=interp_vad,
        ))

    # 确保最后一帧一定是目标 tag（消除插值舍入误差）
    if frames and frames[-1].emotion_tag != to_tag_norm:
        frames[-1] = EmotionTransitionFrame(
            frame_index=frames[-1].frame_index,
            emotion_tag=to_tag_norm,
            expression_id=to_expr,
            interpolation_t=1.0,
            delay_ms=frames[-1].delay_ms,
            vad=to_vad,
        )

    return frames


@dataclass(frozen=True)
class VTSActionResult:
    success: bool
    request_id: str
    trigger: str
    emotion_tag: str | None
    expression_id: str | None
    status: str
    reason_code: str | None
    stage: str
    detail: str


def request_talk_gate(
    *,
    action: str,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    trigger: str = TRIGGER_HOST,
    ts_ms: int | None = None,
) -> VTSActionResult:
    """Apply deterministic talk gate transitions."""

    req_id = request_id or new_request_id()
    now_value = int(ts_ms if ts_ms is not None else now_ms())
    normalized = str(action or "").strip().upper()

    if normalized not in {TALK_START, TALK_END}:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=None,
            expression_id=None,
            status=STATUS_REJECTED,
            reason_code=AVATAR_TRIGGER_INVALID,
            stage=S_AVATAR_TALK_GATE,
            detail=f"unsupported talk gate action: {action!r}",
            ts_ms=now_value,
        )

    expression_id = f"talk_gate::{normalized.lower()}"
    return _log_result(
        ledger=ledger,
        request_id=req_id,
        trigger=trigger,
        emotion_tag=None,
        expression_id=expression_id,
        status=STATUS_APPLIED,
        reason_code=None,
        stage=S_AVATAR_TALK_GATE,
        detail=f"talk gate {normalized.lower()} applied",
        ts_ms=now_value,
    )


def resolve_vts_expression(
    *,
    emotion_tag: str | None,
    ledger: SQLiteLedger,
    request_id: str | None = None,
    trigger: str = TRIGGER_LLM,
    raw_expression_id: str | None = None,
    raw_action_params: dict[str, Any] | None = None,
    ts_ms: int | None = None,
    edge_available: bool = True,
) -> VTSActionResult:
    """Resolve emotion tag to fixed expression mapping and block free actions.

    当 ``edge_available=False`` 或映射失败但有 neutral 降级备选时，返回
    ``status=DEGRADED`` + neutral 表情，而非直接 REJECTED；审计写入降级详情。
    """

    req_id = request_id or new_request_id()
    now_value = int(ts_ms if ts_ms is not None else now_ms())

    # ── Edge 不可达 → DEGRADED ──
    if not edge_available:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=_normalize_emotion_tag(emotion_tag) or DEGRADED_FALLBACK_EXPRESSION_TAG,
            expression_id=DEGRADED_FALLBACK_EXPRESSION_ID,
            status=STATUS_DEGRADED,
            reason_code=VTS_DEGRADED_FALLBACK,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail="edge not available, degraded to neutral fallback",
            ts_ms=now_value,
        )

    if raw_expression_id or raw_action_params:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=_normalize_emotion_tag(emotion_tag),
            expression_id=None,
            status=STATUS_REJECTED,
            reason_code=AVATAR_FREE_ACTION_BLOCKED,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail="free-form avatar action parameters are blocked",
            ts_ms=now_value,
        )

    normalized_tag = _normalize_emotion_tag(emotion_tag)
    if not normalized_tag:
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=None,
            expression_id=None,
            status=STATUS_REJECTED,
            reason_code=AVATAR_TAG_INVALID,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail="emotion_tag is required",
            ts_ms=now_value,
        )

    expression_id = EMOTION_TO_EXPRESSION_ID.get(normalized_tag)
    if not expression_id:
        # ── 映射未知 → DEGRADED（neutral 备选） ──
        return _log_result(
            ledger=ledger,
            request_id=req_id,
            trigger=trigger,
            emotion_tag=normalized_tag,
            expression_id=DEGRADED_FALLBACK_EXPRESSION_ID,
            status=STATUS_DEGRADED,
            reason_code=VTS_DEGRADED_FALLBACK,
            stage=S_AVATAR_EXPRESSION_MAP,
            detail=f"unsupported emotion_tag '{normalized_tag}', degraded to neutral fallback",
            ts_ms=now_value,
        )

    result = _log_result(
        ledger=ledger,
        request_id=req_id,
        trigger=trigger,
        emotion_tag=normalized_tag,
        expression_id=expression_id,
        status=STATUS_APPLIED,
        reason_code=None,
        stage=S_AVATAR_EXPRESSION_MAP,
        detail="expression resolved via fixed mapping",
        ts_ms=now_value,
    )

    # Edge/VTS emotion resolution used to mirror into the local desktop pet.
    # Keep that bridge opt-in now that the Host MateEngine model is driven by
    # chat lifecycle poses; otherwise late VTS expression work can overwrite
    # the visible "speak -> home" desktop-pet state.
    if _mateengine_vts_emotion_mirror_enabled():
        try:
            from host_brain.channels.mateengine import send_mateengine_action  # noqa: PLC0415

            send_mateengine_action(
                action="emote",
                emotion_tag=normalized_tag,
                request_id=req_id,
                intensity=0.7,
            )
        except Exception as exc:
            _logger.debug(
                "MateEngine local desktop pet emotion bridge failed: %s (request_id=%s)",
                exc,
                req_id,
            )

    # ── 真正驱动 Edge VTS（fire-and-forget，不阻塞调用方）──────────────────
    # 通过 EdgeRpcClient 发送 HTTP POST /v1/edge/vts，让 Edge 端执行表情切换。
    # 本函数可能在 asyncio.to_thread 的 worker 线程中被调用，因此必须使用
    # run_coroutine_threadsafe 而非 ensure_future，以确保协程被提交到正确的
    # 事件循环（主线程循环）。若无运行中的事件循环则跳过，不影响审计结果。
    try:
        from host_brain.channels.edge_rpc_client import post_desktop_pet as _post_desktop_pet  # noqa: PLC0415
        from host_brain.channels.edge_rpc_client import post_vts as _post_vts  # noqa: PLC0415

        async def _fire_edge_vts() -> None:
            try:
                await _post_vts(
                    action="set_expression",
                    emotion_tag=normalized_tag,
                    request_id=req_id,
                )
                pet_touch_type = _resolve_desktop_pet_touch_type(normalized_tag)
                if pet_touch_type and _desktop_pet_emotion_bridge_enabled():
                    await _post_desktop_pet(
                        action="touch",
                        touch_type=pet_touch_type,
                        expression=normalized_tag,
                        emotion_tag=normalized_tag,
                        request_id=req_id,
                    )
            except Exception as exc:
                _logger.debug(
                    "Edge VTS HTTP 调用失败（降级）: %s (request_id=%s)",
                    exc, req_id,
                )

        # 获取主事件循环：
        # 1. 优先尝试 get_running_loop()（在主线程 async 上下文中直接调用时有效）
        # 2. fallback 到模块级 _MAIN_LOOP（在 asyncio.to_thread 的 worker 线程中，
        #    get_event_loop() 会抛出 RuntimeError，必须使用预先保存的主循环引用）
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = _MAIN_LOOP
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(_fire_edge_vts(), loop)
    except ImportError:
        pass

    return result


def _desktop_pet_emotion_bridge_enabled() -> bool:
    raw = str(os.getenv("HOST_DESKTOP_PET_EMOTION_BRIDGE_ENABLED", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _mateengine_vts_emotion_mirror_enabled() -> bool:
    raw = str(os.getenv("HOST_MATEENGINE_VTS_EMOTION_MIRROR_ENABLED", "0")).strip().lower()
    return raw not in {"0", "false", "no", "off", "disabled"}


def _resolve_desktop_pet_touch_type(emotion_tag: str | None) -> str | None:
    normalized = _normalize_emotion_tag(emotion_tag)
    if not normalized:
        return None
    return {
        "HAPPY": "head",
        "JOY": "head",
        "EXCITED": "head",
        "PLAYFUL": "hair",
        "AFFECTION": "head",
        "COMFORT": "head",
        "SURPRISED": "poke",
        "SHY": "hair",
        "CONFUSED": "body",
        "THINKING": "body",
        "SAD": "body",
        "ANGRY": "poke",
        "NEUTRAL": "idle",
    }.get(normalized)


def _normalize_emotion_tag(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if not normalized:
        return None
    return VTS_EMOTION_TAG_ALIASES.get(normalized, normalized)


def _log_result(
    *,
    ledger: SQLiteLedger,
    request_id: str,
    trigger: str,
    emotion_tag: str | None,
    expression_id: str | None,
    status: str,
    reason_code: str | None,
    stage: str,
    detail: str,
    ts_ms: int,
) -> VTSActionResult:
    ledger.log_avatar_action(
        {
            "request_id": request_id,
            "trigger": str(trigger),
            "emotion_tag": emotion_tag,
            "expression_id": expression_id,
            "status": status,
            "reason_code": reason_code,
            "stage": stage,
            "detail": detail,
            "ts_ms": ts_ms,
        }
    )
    return VTSActionResult(
        success=status in (STATUS_APPLIED, STATUS_DEGRADED),
        request_id=request_id,
        trigger=str(trigger),
        emotion_tag=emotion_tag,
        expression_id=expression_id,
        status=status,
        reason_code=reason_code,
        stage=stage,
        detail=detail,
    )
