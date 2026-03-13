"""记忆操作 Executor。

将 memory_registry.py 归一化层的 payload 路由到
host_brain/memory/ 模块执行。

支持的 action：
  - search：语义检索记忆
  - store：写入记忆
  - forget：删除记忆
"""

from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("Natsume.skills.memory_executor")


def execute_memory_skill(args: dict[str, Any], context: dict[str, Any] | None = None) -> dict[str, Any]:
    """执行记忆操作。

    Args:
        args: 归一化 payload（来自 memory_registry handler 的返回值）
        context: 执行上下文（含 episodic_writer / semantic_writer 等回调）

    Returns:
        执行结果 dict，包含 status / manifest_ref / detail
    """
    payload = args or {}
    action = str(payload.get("action") or "").strip().lower()
    manifest_ref = str(payload.get("manifest_ref") or f"memory::{action}")

    try:
        result = _dispatch_memory_action(action, payload, context)
        return {
            "status": "OK",
            "manifest_ref": manifest_ref,
            "action": action,
            "detail": result,
        }
    except Exception as exc:
        _logger.warning("Memory executor 执行失败（action=%s）: %s", action, exc)
        return {
            "status": "FAILED",
            "manifest_ref": manifest_ref,
            "action": action,
            "error": str(exc),
        }


def _dispatch_memory_action(
    action: str,
    payload: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """将 action 路由到对应的记忆操作。"""
    if action == "search":
        return _execute_search(payload, context)
    if action == "store":
        return _execute_store(payload, context)
    if action == "forget":
        return _execute_forget(payload, context)
    return {"note": f"unknown memory action: {action}"}


def _execute_search(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """语义检索记忆。"""
    query = str(payload.get("query") or "")
    memory_type = str(payload.get("memory_type") or "all")
    top_k = int(payload.get("top_k") or 5)
    threshold = float(payload.get("threshold") or 0.6)
    tags = payload.get("tags") or []
    session_id = payload.get("session_id")
    date_from = payload.get("date_from")
    date_to = payload.get("date_to")

    # 尝试通过 context 中的 semantic_recaller 执行检索
    ctx = context or {}
    semantic_recaller = ctx.get("semantic_recaller")
    if callable(semantic_recaller):
        try:
            results = semantic_recaller(
                query=query,
                top_k=top_k,
                threshold=threshold,
                tags=tags,
                session_id=session_id,
            )
            _logger.info("记忆检索（semantic_recaller）: query=%s, results=%d", query[:40], len(results or []))
            return {"query": query, "results": results or [], "source": "semantic_recaller"}
        except Exception as exc:
            _logger.debug("semantic_recaller 调用失败: %s", exc)

    # 降级：通过 EpisodicStore 检索
    episodic_store = ctx.get("episodic_store")
    if episodic_store is not None:
        try:
            results = episodic_store.search(
                query=query,
                top_k=top_k,
                threshold=threshold,
            )
            _logger.info("记忆检索（episodic_store）: query=%s", query[:40])
            return {"query": query, "results": results or [], "source": "episodic_store"}
        except Exception as exc:
            _logger.debug("episodic_store 检索失败: %s", exc)

    _logger.info("记忆检索（无可用后端）: query=%s", query[:40])
    return {"query": query, "results": [], "source": "none", "note": "no memory backend available"}


def _execute_store(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """写入记忆。"""
    content = str(payload.get("content") or "")
    memory_type = str(payload.get("memory_type") or "episodic")
    tags = payload.get("tags") or []
    importance = float(payload.get("importance") or 0.5)
    source = payload.get("source")
    emotion_tag = str(payload.get("emotion_tag") or "NEUTRAL")
    session_id = payload.get("session_id")

    ctx = context or {}
    episodic_writer = ctx.get("episodic_writer")
    if callable(episodic_writer):
        try:
            ref = episodic_writer(
                content=content,
                tags=tags,
                importance=importance,
                source=source,
                emotion_tag=emotion_tag,
                session_id=session_id,
            )
            _logger.info("记忆写入（episodic_writer）: content=%s...", content[:40])
            return {"content_length": len(content), "memory_ref": str(ref or ""), "source": "episodic_writer"}
        except Exception as exc:
            _logger.debug("episodic_writer 调用失败: %s", exc)

    _logger.info("记忆写入（无可用后端）: content=%s...", content[:40])
    return {"content_length": len(content), "note": "no memory writer available"}


def _execute_forget(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """删除记忆。"""
    memory_id = payload.get("memory_id")
    tags = payload.get("tags") or []
    memory_type = str(payload.get("memory_type") or "all")
    dry_run = bool(payload.get("dry_run", False))

    if dry_run:
        _logger.info("记忆删除（dry_run）: memory_id=%s, tags=%s", memory_id, tags)
        return {"dry_run": True, "memory_id": memory_id, "tags": tags}

    ctx = context or {}
    episodic_store = ctx.get("episodic_store")
    if episodic_store is not None and memory_id:
        try:
            episodic_store.delete(memory_id=memory_id)
            _logger.info("记忆删除: memory_id=%s", memory_id)
            return {"deleted": True, "memory_id": memory_id}
        except Exception as exc:
            _logger.debug("episodic_store 删除失败: %s", exc)

    _logger.info("记忆删除（无可用后端）: memory_id=%s", memory_id)
    return {"deleted": False, "note": "no memory store available"}
