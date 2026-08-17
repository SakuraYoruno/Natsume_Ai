"""供 Surface 规划消费的低层生命与偏好只读投影。

本模块在应用边界把持久化生命快照压缩为表层可用包；它不执行生命命令，
也不从模糊对话推导用户画像，失败时以空投影让上层保守降级。
"""

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
    """判断消息是否值得查询偏好；命中本身不构成用户偏好证据。"""

    # 正则仅用于判断“是否值得查询偏好”，不是偏好证据本身；真正的话题权属
    # 还要结合已验证偏好包，避免关键字相近的普通对话被错误接管。
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
    """获取与本轮一致的偏好证据包，查询失败时安全降级为空。"""

    # 优先复用本轮检索快照，维持回答与证据的一致性；仅在候选偏好问题才访问
    # 端口，读取异常返回空包，绝不把缺失信息扩写为默认喜好。
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
    """确认偏好链是否可取得本轮话题控制权，而不把关键词匹配升级为画像。"""

    # 有明确相关的证据包才拥有偏好话题；文本命中是兼容回退，后续事实层仍会
    # 区分“用户表达过”与“尚无可靠偏好”的两种回答边界。
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
    """按冻结优先级取得可供 Surface 消费的最小生命导演投影。

    trace 与检索快照优先于新读取，确保重试复用同一版本；新快照仅留下摘要引用，
    防止完整生命状态越过应用边界进入提示或用户可见层。
    """

    # authority 顺序为本轮 trace 冻结包、检索快照、按 person_id 新读取。仅将
    # 版本与摘要写入 trace 供恢复关联，不能把完整生命快照暴露给 Surface。
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
