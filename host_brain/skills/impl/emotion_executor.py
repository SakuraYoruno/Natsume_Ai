"""情绪/Bond 关系 Executor。

将 emotion_registry.py 归一化层的 payload 路由到
host_brain/bond/ 模块执行。

支持的 action：
  - update：更新情绪状态
  - bond_update：更新 Bond 积分
  - get：查询情绪状态
  - bond_get：查询 Bond 积分
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("Natsume.skills.emotion_executor")


def execute_emotion_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行情绪/Bond 关系操作。

    Args:
        args: 归一化 payload（来自 emotion_registry handler 的返回值）
        context: 执行上下文（含 bond_update_queue / bond_stage_engine 等回调）

    Returns:
        执行结果 dict，包含 status / manifest_ref / detail
    """
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"emotion::{action}")

    try:
        result = _dispatch_emotion_action(action, payload, context)
        return {
            "status": "OK",
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": result,
        }
    except Exception as exc:
        _logger.warning("Emotion executor 执行失败（action=%s）: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _dispatch_emotion_action(
    action: str,
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 action 路由到对应的情绪/Bond 操作。"""
    _action_map = {
        "update": _execute_emotion_update,
        "bond_update": _execute_bond_update,
        "get": _execute_emotion_get,
        "bond_get": _execute_bond_get,
    }
    handler = _action_map.get(action)
    if handler is None:
        return {"note": f"unknown emotion action: {action}"}
    return handler(payload, context)


def _execute_emotion_update(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """更新情绪状态。"""
    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL")
    mood_state = str(payload.get("mood_state") or "steady")
    valence = float(payload.get("valence") or 0.0)
    arousal = float(payload.get("arousal") or 0.5)
    trigger = payload.get("trigger")
    sync_vts = bool(payload.get("sync_vts", True))
    session_id = payload.get("session_id")

    ctx = context or {}

    # 通过 mode_changer 更新情绪状态
    mode_changer = ctx.get("mode_changer")
    if callable(mode_changer):
        try:
            mode_changer(
                emotion_tag=emotion_tag,
                mood_state=mood_state,
                valence=valence,
                arousal=arousal,
                trigger=trigger,
                session_id=session_id,
            )
            _logger.info("情绪更新（mode_changer）: emotion=%s, mood=%s", emotion_tag, mood_state)
        except Exception as exc:
            _logger.debug("mode_changer 调用失败: %s", exc)

    # 同步触发 VTS 表情切换
    if sync_vts:
        try:
            from host_brain.skills.impl.vts_executor import execute_vts_skill
            execute_vts_skill(
                {
                    "tool": "vts",
                    "action": "expression",
                    "emotion_tag": emotion_tag,
                    "fade_time_ms": 300,
                    "hold_ms": 0,
                    "manifest_ref": f"vts_expression::{emotion_tag}",
                },
                context,
            )
        except Exception as exc:
            _logger.debug("VTS 表情同步失败: %s", exc)

    return {
        "emotion_tag": emotion_tag,
        "mood_state": mood_state,
        "valence": valence,
        "arousal": arousal,
    }


def _execute_bond_update(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """更新 Bond 关系积分。"""
    event_type = str(payload.get("event_type") or "positive_interaction")
    delta = payload.get("delta")
    user_id = payload.get("user_id")
    note = payload.get("note")
    session_id = payload.get("session_id")

    ctx = context or {}

    # T-95：humor_landed 事件 → 向 TribeEventBus 发布 HUMOR_LANDED
    if event_type == "humor_landed":
        try:
            from host_brain.tribe.ans_agent import get_ans_agent  # noqa: PLC0415
            _intensity = max(0.0, min(1.0, float(payload.get("intensity") or 0.3)))
            get_ans_agent().emit_humor_landed(
                humor_type=str(payload.get("humor_type") or "general"),
                intensity=_intensity,
            )
            _logger.debug("emit_humor_landed: intensity=%.2f", _intensity)
        except Exception as exc:
            _logger.debug("emit_humor_landed 调用失败: %s", exc)

    # 通过 bond_update_queue 写入 Bond 事件
    bond_update_queue = ctx.get("bond_update_queue")
    if bond_update_queue is not None:
        try:
            bond_update_queue.enqueue(
                event_type=event_type,
                delta=delta,
                user_id=user_id,
                note=note,
                session_id=session_id,
            )
            _logger.info("Bond 积分更新（bond_update_queue）: event=%s, delta=%s", event_type, delta)
            return {"event_type": event_type, "delta": delta, "queued": True}
        except Exception as exc:
            _logger.debug("bond_update_queue 调用失败: %s", exc)

    _logger.info("Bond 积分更新（无可用后端）: event=%s", event_type)
    return {"event_type": event_type, "delta": delta, "queued": False}


def _execute_emotion_get(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """查询当前情绪状态。"""
    include_history = bool(payload.get("include_history", False))
    history_limit = int(payload.get("history_limit") or 10)

    ctx = context or {}
    # 从 context 中读取当前情绪状态
    emotion_tag = str(ctx.get("emotion_tag") or "NEUTRAL")
    mood_state = str(ctx.get("mood_state") or "steady")

    result: dict[str, Any] = {
        "emotion_tag": emotion_tag,
        "mood_state": mood_state,
    }
    if include_history:
        result["history"] = []  # 实际实现中从 ledger 读取
        result["history_limit"] = history_limit

    _logger.info("情绪状态查询: emotion=%s, mood=%s", emotion_tag, mood_state)
    return result


def _execute_bond_get(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """查询 Bond 关系积分。"""
    user_id = payload.get("user_id")
    include_history = bool(payload.get("include_history", False))
    history_limit = int(payload.get("history_limit") or 20)

    ctx = context or {}
    bond_stage_engine = ctx.get("bond_stage_engine")
    if bond_stage_engine is not None:
        try:
            score = bond_stage_engine.get_score(user_id=user_id)
            stage = bond_stage_engine.get_stage(user_id=user_id)
            _logger.info("Bond 积分查询: user=%s, score=%s, stage=%s", user_id, score, stage)
            result: dict[str, Any] = {"score": score, "stage": stage, "user_id": user_id}
            if include_history:
                result["history"] = []
                result["history_limit"] = history_limit
            return result
        except Exception as exc:
            _logger.debug("bond_stage_engine 查询失败: %s", exc)

    return {"score": 0, "stage": "FRIEND", "user_id": user_id, "note": "no bond engine available"}
