"""Input validation for isolated surface generation.

Extracted from isolated_surface_harness.py — validators that ensure
incoming payloads are structurally sound and free of forbidden keys.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.persona.surface.data_models import (
    IsolatedSurfaceInput,
    ReplyControlPacket,
)
from host_brain.persona.surface.scene_constants import (
    _ALLOWED_TOP_LEVEL_KEYS,
    _FORBIDDEN_INPUT_KEYS,
    _FORBIDDEN_KEY_HINTS,
)
from host_brain.core.runtime_helpers import _coerce_float, _coerce_int
from host_brain.persona.surface.util import (
    _clean_text,
    _optional_list,
    _validate_model,
)


def _is_forbidden_key(key: Any) -> bool:
    normalized = str(key or "").strip().lower()
    if normalized in _FORBIDDEN_INPUT_KEYS:
        return True
    return any(hint in normalized for hint in _FORBIDDEN_KEY_HINTS)


def find_forbidden_input_paths(payload: Any, *, prefix: str = "") -> tuple[str, ...]:
    # 递归返回具体路径而非只报顶层键，便于调用方定位嵌套适配数据；列表也检查，
    # 防止把受限上下文藏进候选项或附件数组绕过白名单。
    paths: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if _is_forbidden_key(key_text):
                paths.append(path)
            paths.extend(find_forbidden_input_paths(value, prefix=path))
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, item in enumerate(payload):
            paths.extend(find_forbidden_input_paths(item, prefix=f"{prefix}[{index}]"))
    return tuple(dict.fromkeys(paths))


def build_isolated_surface_input(
    payload: Mapping[str, Any], *, soul_md: str | None = None
) -> IsolatedSurfaceInput:
    # 顶层白名单先拒绝未知扩展，再递归拒绝语义上危险的 raw context。两层检查
    # 共同防止调用方以新字段或嵌套字段把完整历史/器官输出注入生成提示词。
    unexpected = sorted(
        str(key) for key in payload.keys() if str(key) not in _ALLOWED_TOP_LEVEL_KEYS
    )
    if unexpected:
        raise ValueError(
            "isolated surface input contains unexpected fields: "
            + ", ".join(unexpected[:8])
        )
    forbidden_paths = find_forbidden_input_paths(payload)
    if forbidden_paths:
        raise ValueError(
            "isolated surface input contains forbidden raw context: "
            + ", ".join(forbidden_paths[:8])
        )

    # 只接受规范 ReplyControlPacket（或可验证 Mapping）；其余业务字段被拆到有限
    # request payload，不能让自由形状的 control 直接携带未经限额的指令。
    control_payload = payload.get("control", payload.get("reply_control_packet", {}))
    if isinstance(control_payload, ReplyControlPacket):
        control = control_payload
    elif isinstance(control_payload, Mapping):
        control = _validate_model(ReplyControlPacket, control_payload)  # type: ignore[assignment]
    else:
        raise ValueError("isolated surface input requires a mapping ReplyControlPacket")

    request_payload = {
        "soul_md": _clean_text(
            soul_md if soul_md is not None else payload.get("soul_md")
        ),
        "user_message": _clean_text(payload.get("user_message")),
        "control": control,
        "facts": _optional_list(payload.get("facts")),
        "must_not_do": _optional_list(payload.get("must_not_do")),
        "accepted_task_results": _optional_list(
            payload.get("accepted_task_results")
        ),
        "case_id": _clean_text(payload.get("case_id")),
        # --- Inner-state leakage pass-through ---
        "inner_weather": _clean_text(payload.get("inner_weather")),
        "body_state_hint": _clean_text(payload.get("body_state_hint")),
        "care_signal": _coerce_float(payload.get("care_signal")),
        "continuity_signal": _coerce_float(payload.get("continuity_signal")),
        "tension_hint": _clean_text(payload.get("tension_hint")),
        "liveliness": _coerce_float(payload.get("liveliness")) if payload.get("liveliness") is not None else 0.5,
        "active_loops": _coerce_int(payload.get("active_loops")),
    }
    # Soul 与当前用户文本是隔离生成的最小必需输入；缺失时 fail closed，避免模型
    # 在无身份或无问题的情况下依据残留上下文自行编造回复。
    if not request_payload["soul_md"]:
        raise ValueError("isolated surface input requires soul_md")
    if not request_payload["user_message"]:
        raise ValueError("isolated surface input requires user_message")
    return _validate_model(IsolatedSurfaceInput, request_payload)  # type: ignore[return-value]
