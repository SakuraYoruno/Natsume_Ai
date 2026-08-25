"""记忆操作 Executor。

将 memory_registry.py 归一化层的 payload 路由到
host_brain/memory/ 模块执行。

支持的 action：
  - search：语义检索记忆
  - store：写入记忆
  - forget：删除记忆
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

_logger = logging.getLogger("Natsume.skills.memory_executor")
_MEMORY_FRAGMENT_RE = re.compile(r"[\r\n。！？!?；;，,]+")
_PREFERENCE_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("preference", "positive", re.compile(r"(?:我|本人|用户)?(?<!不)(?:更?喜欢|偏好|倾向于|习惯|常用)(?P<value>.+)", re.IGNORECASE)),
    ("instruction", "positive", re.compile(r"(?:我|用户)?(?<!不)(?:希望你|希望|想要|需要|请你|请)(?P<value>.+)", re.IGNORECASE)),
    ("preference", "negative", re.compile(r"(?:我|本人|用户)?(?:不喜欢|讨厌|不想要|不希望|不需要)(?P<value>.+)", re.IGNORECASE)),
    ("instruction", "negative", re.compile(r"(?:不要|别再|别|请不要)(?P<value>.+)", re.IGNORECASE)),
    ("preference", "positive", re.compile(r"(?:i|user)\s+(?:like|prefer|usually use|want|need)\s+(?P<value>.+)", re.IGNORECASE)),
    ("preference", "negative", re.compile(r"(?:i|user)\s+(?:do not like|don't like|dislike|hate|do not want|don't want)\s+(?P<value>.+)", re.IGNORECASE)),
)
_TASK_MEMORY_HINT_RE = re.compile(
    r"(确认|决定|完成|修复|接入|验证|下一步|待办|需要|开始|继续|todo|decision|confirmed|fixed|next)",
    re.IGNORECASE,
)
_FACT_MEMORY_HINT_RE = re.compile(
    r"(我是|我叫|我的|用户|项目|系统|主链|偏好|喜欢|不喜欢|is|am|prefer|likes|dislikes)",
    re.IGNORECASE,
)
_SEMANTIC_SEARCH_SCOPES = frozenset({"long_term", "line", "hybrid"})
_EPISODIC_SEARCH_SCOPES = frozenset({"recent_episode", "hybrid"})


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
        if isinstance(result, dict):
            status = str(result.get("status") or "").strip().upper()
            if status in {"FAILED", "DEGRADED", "REJECTED"}:
                return {
                    "status": status,
                    "manifest_ref": manifest_ref,
                    "action": action,
                    "reason_code": str(result.get("reason_code") or "MEMORY_EXECUTOR_DEGRADED").strip() or "MEMORY_EXECUTOR_DEGRADED",
                    "detail": str(result.get("detail") or result),
                }
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
    if action == "preference_extract":
        return _execute_preference_extract(payload, context)
    if action == "consolidate":
        return _execute_consolidate(payload, context)
    if action == "forget":
        return _execute_forget(payload, context)
    return {"note": f"unknown memory action: {action}"}


def _execute_search(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """检索记忆。

    recent_episode 用确定性 EpisodicStore 处理刚写入/最近事件；
    long_term 与 line 才进入语义召回；hybrid 必须显式指定。
    """
    query = str(payload.get("query") or "")
    memory_type = str(payload.get("memory_type") or "all")
    search_scope = _normalize_search_scope(str(payload.get("search_scope") or ""), memory_type=memory_type)
    top_k = int(payload.get("top_k") or 5)
    threshold = float(payload.get("threshold") or 0.6)
    tags = payload.get("tags") or []
    session_id = payload.get("session_id")
    session_filter = bool(payload.get("session_filter"))
    date_from = payload.get("date_from")
    date_to = payload.get("date_to")
    _ = date_from, date_to

    # 尝试通过 context 中的 semantic_recaller 执行检索
    ctx = context or {}
    semantic_recaller = ctx.get("semantic_recaller")
    if search_scope in _SEMANTIC_SEARCH_SCOPES and callable(semantic_recaller):
        try:
            results = semantic_recaller(
                query=query,
                top_k=top_k,
                threshold=threshold,
                tags=tags,
                session_id=session_id if session_filter else None,
                search_scope=search_scope,
            )
            normalized = _normalize_search_results(results or [], source="semantic_recaller", threshold=threshold)
            _logger.info("记忆检索（semantic_recaller）: query=%s, results=%d", query[:40], len(normalized))
            if normalized or search_scope in {"long_term", "line"}:
                return {"query": query, "results": normalized, "source": "semantic_recaller", "search_scope": search_scope}
        except Exception as exc:
            _logger.debug("semantic_recaller 调用失败: %s", exc)

    # 降级：通过 EpisodicStore 检索
    episodic_store = ctx.get("episodic_store")
    if search_scope in _EPISODIC_SEARCH_SCOPES and episodic_store is not None and memory_type in {"all", "episodic", "working"}:
        try:
            results = _search_episodic_store(
                episodic_store,
                query=query,
                top_k=top_k,
                threshold=threshold,
            )
            normalized = _normalize_search_results(results or [], source="episodic_store", threshold=threshold)
            _logger.info("记忆检索（episodic_store）: query=%s, results=%d", query[:40], len(normalized))
            return {"query": query, "results": normalized, "source": "episodic_store", "search_scope": search_scope}
        except Exception as exc:
            _logger.debug("episodic_store 检索失败: %s", exc)

    _logger.info("记忆检索（无可用后端）: query=%s", query[:40])
    return {"query": query, "results": [], "source": "none", "search_scope": search_scope, "note": "no memory backend available"}


def _normalize_search_scope(raw_scope: str, *, memory_type: str) -> str:
    scope = str(raw_scope or "").strip().lower()
    if scope in {"recent", "recent_episodic", "episode", "episodic"}:
        return "recent_episode"
    if scope in {"semantic", "longterm", "long_term_semantic"}:
        return "long_term"
    if scope in {"quote", "quotes", "dialogue", "lines", "line_semantic"}:
        return "line"
    if scope in {"recent_episode", "long_term", "line", "hybrid"}:
        return scope
    if str(memory_type or "").strip().lower() in {"semantic"}:
        return "long_term"
    return "recent_episode"


def _search_episodic_store(
    episodic_store: Any,
    *,
    query: str,
    top_k: int,
    threshold: float,
) -> list[Any]:
    search = getattr(episodic_store, "search", None)
    if callable(search):
        return list(
            search(
                query=query,
                top_k=top_k,
                threshold=threshold,
            )
            or []
        )
    recall = getattr(episodic_store, "recall", None)
    if callable(recall):
        return list(
            recall(
                query_text=query,
                top_k=top_k,
            )
            or []
        )
    return []


def _normalize_search_results(items: list[Any], *, source: str, threshold: float) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            row = dict(item)
        else:
            row = {
                "memory_id": getattr(item, "memory_id", None) or getattr(item, "doc_id", None) or getattr(item, "id", None),
                "content": getattr(item, "content", None) or getattr(item, "text", None),
                "score": getattr(item, "score", None),
                "metadata": getattr(item, "metadata", None),
                "source": getattr(item, "source", None),
            }
        content = str(row.get("content") or row.get("text") or "")
        memory_id = str(row.get("memory_id") or row.get("doc_id") or row.get("id") or "")
        row["content"] = content
        row.setdefault("text", content)
        if memory_id:
            row["memory_id"] = memory_id
            row.setdefault("doc_id", memory_id)
        row["source"] = str(row.get("source") or source)
        try:
            score = float(row.get("score"))
        except (TypeError, ValueError):
            score = 1.0 if content else 0.0
        row["score"] = score
        if threshold > 0.0 and score < threshold:
            continue
        normalized.append(row)
    return normalized


def _execute_store(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """写入记忆。"""
    content = str(payload.get("content") or "")
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
    return {
        "status": "DEGRADED",
        "reason_code": "MEMORY_WRITER_UNAVAILABLE",
        "detail": "no memory writer available",
        "content_length": len(content),
    }


def _split_memory_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for raw in _MEMORY_FRAGMENT_RE.split(str(text or "")):
        item = " ".join(str(raw or "").strip().split())
        if len(item) >= 3:
            fragments.append(item)
    return fragments


def _candidate_id(kind: str, evidence_text: str) -> str:
    digest = hashlib.sha1(f"{kind}:{evidence_text}".encode("utf-8")).hexdigest()[:12]
    return f"memcand_{digest}"


def _clean_candidate_value(value: str) -> str:
    text = str(value or "").strip(" \t:：。！？!?，,；;")
    text = re.sub(r"^(?:你|我|the|a|an)\s+", "", text, flags=re.IGNORECASE)
    return text[:240]


def _preference_candidate_text(*, kind: str, polarity: str, value: str) -> str:
    if kind == "instruction":
        if polarity == "negative":
            return f"用户希望避免{value}"
        return f"用户希望{value}"
    if polarity == "negative":
        return f"用户不喜欢{value}"
    return f"用户喜欢{value}"


def _extract_preference_candidates(text: str, *, max_candidates: int, source: str | None = None) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for fragment in _split_memory_fragments(text):
        for kind, polarity, pattern in _PREFERENCE_RULES:
            match = pattern.search(fragment)
            if match is None:
                continue
            value = _clean_candidate_value(match.group("value"))
            if len(value) < 2:
                continue
            candidate_text = _preference_candidate_text(kind=kind, polarity=polarity, value=value)
            key = f"{kind}:{polarity}:{candidate_text}".lower()
            if key in seen:
                continue
            seen.add(key)
            tags = ["preference", polarity]
            if kind == "instruction":
                tags.append("interaction_style")
            candidates.append(
                {
                    "candidate_id": _candidate_id(kind, fragment),
                    "kind": kind,
                    "polarity": polarity,
                    "text": candidate_text,
                    "value": value,
                    "confidence": 0.9 if kind == "preference" else 0.84,
                    "tags": tags,
                    "evidence_text": fragment,
                    "source": source or "memory_preference_extract",
                    "write_policy": "soft_memory_candidate",
                }
            )
            if len(candidates) >= max_candidates:
                return candidates
    return candidates


def _execute_preference_extract(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    _ = context
    text = str(
        payload.get("text")
        or payload.get("content")
        or payload.get("source_text")
        or payload.get("user_message")
        or ""
    )
    max_candidates = int(payload.get("max_candidates") or 8)
    max_candidates = max(1, min(50, max_candidates))
    source = str(payload.get("source") or "").strip() or None
    candidates = _extract_preference_candidates(text, max_candidates=max_candidates, source=source)
    return {
        "candidate_count": len(candidates),
        "candidates": candidates,
        "source": "rule_based_preference_extractor",
        "write_policy": "candidate_only",
    }


def _infer_memory_candidate_kind(fragment: str) -> tuple[str, list[str], float] | None:
    text = str(fragment or "").strip()
    if not text:
        return None
    if _TASK_MEMORY_HINT_RE.search(text):
        return ("task_event", ["task", "event"], 0.74)
    if _FACT_MEMORY_HINT_RE.search(text):
        return ("fact", ["fact"], 0.7)
    if len(text) >= 18:
        return ("conversation_note", ["conversation"], 0.58)
    return None


def _execute_consolidate(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    _ = context
    raw_items = payload.get("items")
    text_parts: list[str] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                value = item.get("text") or item.get("content") or item.get("message")
            else:
                value = item
            item_text = str(value or "").strip()
            if item_text:
                text_parts.append(item_text)
    text = str(
        payload.get("text")
        or payload.get("content")
        or payload.get("source_text")
        or "\n".join(text_parts)
        or ""
    )
    max_candidates = int(payload.get("max_candidates") or 10)
    max_candidates = max(1, min(50, max_candidates))
    include_preferences = bool(payload.get("include_preferences", True))
    source = str(payload.get("source") or "").strip() or "memory_consolidate"

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    if include_preferences:
        for candidate in _extract_preference_candidates(text, max_candidates=max_candidates, source=source):
            key = str(candidate.get("text") or "").lower()
            seen.add(key)
            candidates.append(candidate)
            if len(candidates) >= max_candidates:
                break

    if len(candidates) < max_candidates:
        preference_evidence = {str(item.get("evidence_text") or "") for item in candidates}
        for fragment in _split_memory_fragments(text):
            if fragment in preference_evidence:
                continue
            inferred = _infer_memory_candidate_kind(fragment)
            if inferred is None:
                continue
            kind, tags, confidence = inferred
            key = f"{kind}:{fragment}".lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "candidate_id": _candidate_id(kind, fragment),
                    "kind": kind,
                    "polarity": "neutral",
                    "text": fragment,
                    "confidence": confidence,
                    "tags": tags,
                    "evidence_text": fragment,
                    "source": source,
                    "write_policy": "soft_memory_candidate",
                }
            )
            if len(candidates) >= max_candidates:
                break

    return {
        "candidate_count": len(candidates),
        "candidates": candidates,
        "summary": f"extracted {len(candidates)} memory candidates",
        "source": "rule_based_memory_consolidator",
        "write_policy": "candidate_only",
    }


def _execute_forget(payload: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """删除记忆。"""
    memory_id = payload.get("memory_id")
    tags = payload.get("tags") or []
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
    return {
        "status": "DEGRADED",
        "reason_code": "MEMORY_STORE_UNAVAILABLE",
        "detail": "no memory store available",
        "deleted": False,
    }
