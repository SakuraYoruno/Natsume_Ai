"""Low-level Life and preference projections consumed by Surface planning."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from host_brain.digital_life.application_boundary import (
    project_life_director_pack,
    read_life_snapshot,
)
from host_brain.surface_runtime.common import _safe_mapping
from host_brain.surface_runtime.ports import SurfaceRuntimePorts


def surface_user_preference_message(user_message: str) -> bool:
    text = str(user_message or "").strip()
    if not text:
        return False
    return bool(
        re.search(r"(轻小说|輕小說|小说|小說|书|書).{0,24}(偏好|口味|喜好|喜欢|喜歡|哪种|哪類|哪类|什么类型)", text)
        or re.search(r"(偏好|口味|喜好|喜欢|喜歡).{0,24}(轻小说|輕小說|小说|小說|书|書)", text)
        or re.search(r"你知道我喜欢.{0,16}(轻小说|輕小說|小说|小說|书|書)", text)
        or re.search(r"(第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|項|项|部|步)?|这本|这部|那本|哪本|刚才推荐|刚刚推荐).{0,18}(好看|怎么样|如何|值得|推荐|能看|先看)", text)
        or re.search(r"(第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|項|项|部|步)?|这本|这部|那本|刚才推荐|刚刚推荐).{0,18}(详情|详细内容|详细|内容|简介|介绍|讲什么|说什么)", text)
        or re.search(r"(详情|详细内容|详细|简介|介绍).{0,12}(第\s*(?:[一二三四五六七八九十]|\d{1,2})\s*(?:本|个|項|项|部|步)?|这本|这部|那本)", text)
        or re.search(r"(好看吗|好不好看|值不值得|推不推荐|先看哪本|哪本)", text)
    )


def surface_user_preference_pack(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
) -> dict[str, Any]:
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    pack = _safe_mapping(retrieval_summary.get("user_preference_pack"))
    if pack:
        return pack
    user_message = str(getattr(agent_input, "user_message", "") or "")
    if not surface_user_preference_message(user_message):
        return {}
    try:
        return _safe_mapping(
            ports.build_user_preference_pack(
                person_id=str(getattr(agent_input, "person_id", "") or "").strip() or None,
                query_text=user_message,
                session_id=str(getattr(agent_input, "session_id", "") or "").strip() or None,
                conversation_id=str(getattr(agent_input, "session_id", "") or "").strip() or None,
                ledger=ports.get_ledger(),
            )
        )
    except Exception:
        return {}


def surface_user_preference_query_owned(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
) -> bool:
    pack = surface_user_preference_pack(agent_input, ports=ports)
    if pack and bool(pack.get("query_relevant") or pack.get("has_profile")):
        return True
    return surface_user_preference_message(
        str(getattr(agent_input, "user_message", "") or "")
    )


def surface_life_director_pack(
    agent_input: Any,
    *,
    ports: SurfaceRuntimePorts,
    trace_stage_meta: Mapping[str, Any] | None = None,
    task_mode: str = "",
    response_move: str = "",
) -> dict[str, Any]:
    trace = _safe_mapping(trace_stage_meta)
    pack = _safe_mapping(trace.get("life_director"))
    if pack:
        return pack
    retrieval_summary = _safe_mapping(getattr(agent_input, "retrieval_summary", None))
    pack = _safe_mapping(
        retrieval_summary.get("life_director")
        or retrieval_summary.get("life_continuity_pack")
    )
    if pack:
        return pack
    try:
        snapshot = read_life_snapshot(
            str(getattr(agent_input, "person_id", "") or "").strip() or "default",
            reader=ports.get_life_snapshot_reader(),
        )
        pack = project_life_director_pack(snapshot)
        if isinstance(trace_stage_meta, dict):
            trace_stage_meta["life_snapshot_ref"] = {
                "person_id": snapshot["person_id"],
                "version": snapshot["version"],
                "state_digest": snapshot["state_digest"],
                "source": "life_snapshot_reader",
            }
        _ = task_mode, response_move
        return _safe_mapping(pack)
    except Exception:
        return {}


__all__ = [
    "surface_life_director_pack",
    "surface_user_preference_message",
    "surface_user_preference_pack",
    "surface_user_preference_query_owned",
]
