"""Generic evidence/body fetch for memory recall answer-readiness."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import unquote, urlparse

from host_brain.agent_runtime.review_support import (
    _clean_text,
    _packet_needs_detail_fetch,
)


ROOT = Path(__file__).resolve().parents[2]
_URL_RE = re.compile(r"https?://[^\s，。；;、｜|)）>\"']+", re.IGNORECASE)
_WIN_PATH_RE = re.compile(r"[A-Za-z]:\\[^\s，。；;、｜|)）>\"']+")
_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".json",
    ".jsonl",
    ".csv",
    ".log",
    ".xml",
    ".yaml",
    ".yml",
}
_SECRET_NAME_TOKENS = (
    ".env",
    "secret",
    "token",
    "cookie",
    "credential",
    "password",
    "private_key",
)


def _enabled() -> bool:
    raw = str(os.environ.get("HOST_GENERIC_EVIDENCE_REVIEW_ENABLED") or "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def should_review_generic_evidence(*, user_message: str, packet: Mapping[str, Any]) -> bool:
    if not _enabled():
        return False
    if _packet_looks_like_lightnovel(packet):
        return False
    if not _packet_needs_detail_fetch(packet):
        return False
    if not _looks_like_evidence_question(user_message=user_message, packet=packet):
        return False
    return bool(_extract_targets(packet))


def review_generic_evidence_from_memory_packet(
    *,
    packet: Mapping[str, Any],
    user_message: str,
    request_id: str = "",
    person_id: str | None = None,
    session_id: str | None = None,
    ts_ms: int | None = None,
    timeout_s: int | None = None,
    persist_life_action_event_memory: Any = None,
) -> dict[str, Any]:
    targets = _extract_targets(packet)
    max_targets = _review_target_limit()
    targets = targets[:max_targets]
    if not targets:
        result = _result(
            status="SKIPPED",
            reason_code="GENERIC_EVIDENCE_REVIEW_NO_TARGETS",
            fact="我找到了相关记忆，但没有拿到可重新读取的网页、文件或正文证据。",
            items=[],
        )
        _attach_persist_result(
            result,
            _persist_review_memory(
                result=result,
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                ts_ms=ts_ms,
                persist_life_action_event_memory=persist_life_action_event_memory,
            ),
        )
        return result

    timeout_value = int(timeout_s or os.environ.get("HOST_GENERIC_EVIDENCE_REVIEW_TIMEOUT_S") or 8)
    items = [_fetch_target(target, timeout_s=timeout_value) for target in targets]
    ok_items = [
        item
        for item in items
        if str(item.get("review_status") or "") == "OK" and _clean_text(item.get("excerpt"))
    ]
    if not ok_items:
        result = _result(
            status="DEGRADED",
            reason_code="GENERIC_EVIDENCE_REVIEW_FETCH_FAILED",
            fact="我找到了相关记忆，也尝试重新读取证据来源，但这次没有拿到可用正文；不能把旧摘要当成完整依据。",
            items=items,
        )
        _attach_persist_result(
            result,
            _persist_review_memory(
                result=result,
                request_id=request_id,
                person_id=person_id,
                session_id=session_id,
                ts_ms=ts_ms,
                persist_life_action_event_memory=persist_life_action_event_memory,
            ),
        )
        return result

    result = _result(
        status="OK",
        reason_code="GENERIC_EVIDENCE_REVIEW_OK",
        fact=_build_review_fact(items=ok_items, user_message=user_message),
        items=items,
    )
    _attach_persist_result(
        result,
        _persist_review_memory(
            result=result,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            persist_life_action_event_memory=persist_life_action_event_memory,
        ),
    )
    return result


def _looks_like_evidence_question(*, user_message: str, packet: Mapping[str, Any]) -> bool:
    text = _clean_text(user_message)
    if any(token in text for token in ("依据", "证据", "来源", "网页", "文件", "内容", "靠谱", "可信", "看过", "读过")):
        return True
    trace = packet.get("trace")
    if not isinstance(trace, Mapping):
        return False
    readiness = trace.get("recall_answer_readiness")
    if isinstance(readiness, Mapping) and str(readiness.get("need_type") or "") == "evidence_review":
        return True
    plan = trace.get("plan")
    return isinstance(plan, Mapping) and str(plan.get("query_kind") or "") == "evidence_review"


def _packet_looks_like_lightnovel(packet: Mapping[str, Any]) -> bool:
    text = "\n".join(_packet_text_sources(packet))
    if "lightnovel.fun" in text or "轻小说" in text:
        return True
    trace = packet.get("trace")
    if not isinstance(trace, Mapping):
        return False
    for key in ("plan", "recall_receipt", "recall_need"):
        value = trace.get(key)
        if not isinstance(value, Mapping):
            continue
        domains = [str(item) for item in list(value.get("target_domains") or [])]
        if "lightnovel_recommendation" in domains:
            return True
    return False


def _extract_targets(packet: Mapping[str, Any]) -> list[dict[str, str]]:
    refs: list[str] = []
    for key in ("source_refs", "evidence_refs"):
        refs.extend(str(item or "") for item in list(packet.get(key) or []))
    rows = packet.get("rows")
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            refs.extend(
                str(row.get(key) or "")
                for key in ("source_ref", "evidence_ref", "fact", "text", "content")
            )
    trace = packet.get("trace")
    if isinstance(trace, Mapping):
        for key in ("selected_collection_items", "selected_entity_items"):
            raw_items = trace.get(key)
            if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
                continue
            for item in raw_items:
                if not isinstance(item, Mapping):
                    continue
                refs.extend(
                    str(item.get(name) or "")
                    for name in (
                        "detail_url",
                        "source_url",
                        "url",
                        "href",
                        "document_ref",
                        "file_ref",
                        "file_path",
                        "body_ref",
                        "detail_evidence_ref",
                        "source_ref",
                    )
                )
    targets: list[dict[str, str]] = []
    for raw in refs:
        for url in _URL_RE.findall(raw):
            url = _clean_ref(url)
            if url and "lightnovel.fun" not in url and not _has_target(targets, url):
                targets.append({"kind": "url", "ref": url})
        for path_text in _path_refs(raw):
            path = _resolve_file_path(path_text)
            if path and not _has_target(targets, str(path)):
                targets.append({"kind": "file", "ref": str(path)})
    return targets


def _packet_text_sources(packet: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for fact in list(packet.get("facts") or []):
        text = _clean_text(fact)
        if text:
            values.append(text)
    rows = packet.get("rows")
    if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            text = _clean_text(row.get("fact") or row.get("text") or row.get("content"))
            if text:
                values.append(text)
    return list(dict.fromkeys(values))


def _clean_ref(value: Any) -> str:
    return str(value or "").strip().strip("。；;，,、)]}>\"'")


def _path_refs(value: Any) -> list[str]:
    text = str(value or "").strip()
    refs: list[str] = []
    if text.lower().startswith("file://"):
        refs.append(text)
    refs.extend(_WIN_PATH_RE.findall(text))
    if text and not _URL_RE.search(text) and ("\\" in text or "/" in text):
        refs.append(text)
    return list(dict.fromkeys(refs))


def _resolve_file_path(value: str) -> Path | None:
    raw = _clean_ref(value)
    if not raw:
        return None
    if raw.lower().startswith("file://"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path or "")
        if re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
    try:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        path = path.resolve()
    except Exception:
        return None
    if not path.exists() or not path.is_file():
        return None
    name_lower = path.name.lower()
    if any(token in name_lower for token in _SECRET_NAME_TOKENS):
        return None
    if path.suffix.lower() not in _TEXT_SUFFIXES:
        return None
    max_bytes = int(os.environ.get("HOST_GENERIC_EVIDENCE_REVIEW_MAX_FILE_BYTES") or 1_000_000)
    try:
        if path.stat().st_size > max(1, max_bytes):
            return None
    except OSError:
        return None
    return path


def _has_target(targets: Sequence[Mapping[str, str]], ref: str) -> bool:
    return any(str(item.get("ref") or "") == ref for item in targets)


def _fetch_target(target: Mapping[str, str], *, timeout_s: int) -> dict[str, Any]:
    kind = str(target.get("kind") or "")
    ref = str(target.get("ref") or "")
    if kind == "url":
        return _fetch_url(ref, timeout_s=timeout_s)
    if kind == "file":
        return _read_file(ref)
    return {"kind": kind, "ref": ref, "review_status": "FAILED", "review_reason_code": "UNKNOWN_TARGET"}


def _fetch_url(url: str, *, timeout_s: int) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": "url", "ref": url, "detail_url": url}
    try:
        import requests  # noqa: PLC0415

        response = requests.get(
            url,
            timeout=max(1, int(timeout_s or 1)),
            headers={"User-Agent": "Natsume-Ai evidence review"},
        )
    except Exception as exc:
        return {**item, "review_status": "FAILED", "review_reason_code": "FETCH_FAILED", "review_error": str(exc)[:160]}
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code < 200 or status_code >= 300:
        return {**item, "review_status": "FAILED", "review_reason_code": f"HTTP_{status_code}"}
    raw_text = str(getattr(response, "text", "") or "")
    title, body = _html_or_text_to_body(raw_text)
    excerpt = _clean_text(body, limit=1800)
    if not excerpt:
        return {**item, "review_status": "FAILED", "review_reason_code": "EMPTY_BODY"}
    return {
        **item,
        "review_status": "OK",
        "title": title or url,
        "excerpt": excerpt,
        "content_char_count": len(body),
        "source": "web",
    }


def _read_file(path_text: str) -> dict[str, Any]:
    path = _resolve_file_path(path_text)
    item: dict[str, Any] = {"kind": "file", "ref": str(path or path_text)}
    if path is None:
        return {**item, "review_status": "FAILED", "review_reason_code": "FILE_NOT_ALLOWED_OR_MISSING"}
    try:
        raw_text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as exc:
        return {**item, "review_status": "FAILED", "review_reason_code": "FILE_READ_FAILED", "review_error": str(exc)[:160]}
    title, body = _html_or_text_to_body(raw_text)
    excerpt = _clean_text(body, limit=1800)
    if not excerpt:
        return {**item, "review_status": "FAILED", "review_reason_code": "EMPTY_BODY"}
    return {
        **item,
        "review_status": "OK",
        "title": title or path.name,
        "excerpt": excerpt,
        "content_char_count": len(body),
        "source": "file",
    }


def _html_or_text_to_body(raw_text: str) -> tuple[str, str]:
    text = str(raw_text or "")
    if "<html" not in text.lower() and "<body" not in text.lower():
        return "", text
    try:
        from bs4 import BeautifulSoup  # noqa: PLC0415

        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        body = soup.get_text(" ", strip=True)
        return title, body
    except Exception:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
        title = _clean_text(re.sub(r"<[^>]+>", " ", title_match.group(1))) if title_match else ""
        body = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
        body = re.sub(r"<[^>]+>", " ", body)
        return title, body


def _build_review_fact(*, items: Sequence[Mapping[str, Any]], user_message: str) -> str:
    labels = [
        _clean_text(item.get("title"), limit=60) or _clean_text(item.get("ref"), limit=60)
        for item in list(items or [])[:3]
    ]
    excerpts = [
        _clean_text(item.get("excerpt"), limit=300).rstrip("。；; ")
        for item in list(items or [])[:2]
        if _clean_text(item.get("excerpt"))
    ]
    label_text = "、".join(label for label in labels if label) or "证据来源"
    excerpt_text = "；".join(excerpts)
    if "靠谱" in _clean_text(user_message) or "可信" in _clean_text(user_message):
        tail = "这些正文可以用来判断它的来源、时间和限制条件。"
    else:
        tail = "这些正文可以作为这次回答的依据。"
    return _clean_text(f"证据补读结果：重新读取了 {len(items)} 个来源（{label_text}）。可用依据：{excerpt_text}。{tail}", limit=900)


def _review_target_limit() -> int:
    raw = str(os.environ.get("HOST_GENERIC_EVIDENCE_REVIEW_MAX_TARGETS") or "").strip()
    try:
        return max(1, min(int(raw) if raw else 3, 6))
    except ValueError:
        return 3


def _result(*, status: str, reason_code: str, fact: str, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evidence_refs = [
        str(item.get("ref") or item.get("detail_url") or "")
        for item in items
        if str(item.get("ref") or item.get("detail_url") or "").strip()
    ]
    return {
        "status": status,
        "reason_code": reason_code,
        "fact": _clean_text(fact, limit=920),
        "items": [_public_item(item) for item in items],
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
        "cached": False,
        "source": "fetch",
    }


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "kind": item.get("kind"),
            "ref": item.get("ref"),
            "title": _clean_text(item.get("title"), limit=120),
            "review_status": item.get("review_status"),
            "review_reason_code": item.get("review_reason_code"),
            "content_char_count": item.get("content_char_count"),
            "source": item.get("source"),
        }.items()
        if value not in ("", None, [], {})
    }


def _persist_review_memory(
    *,
    result: Mapping[str, Any],
    request_id: str,
    person_id: str | None,
    session_id: str | None,
    ts_ms: int | None,
    persist_life_action_event_memory: Any = None,
) -> dict[str, Any]:
    raw_enabled = str(os.environ.get("HOST_GENERIC_EVIDENCE_REVIEW_PERSIST_ENABLED") or "true").strip().lower()
    if raw_enabled in {"0", "false", "no", "off"}:
        return {"status": "SKIPPED", "reason_code": "PERSIST_DISABLED"}
    if not callable(persist_life_action_event_memory):
        return {"status": "SKIPPED", "reason_code": "PERSIST_PORT_NOT_COMPOSED"}
    owner = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(person_id or session_id or "global").strip())[:80] or "global"
    signature = _stable_result_signature(result)
    action_id = f"{owner}:generic_evidence_review:{signature}"
    evidence_refs = list(result.get("evidence_refs") or [])
    evidence_refs.append(f"action://event/{action_id}")
    try:
        return persist_life_action_event_memory(
            event_source="surface_memory_recall",
            action_kind="generic_evidence_review",
            action_id=action_id,
            actor_id="natsume",
            request_id=request_id or None,
            intent_id="surface_memory_followup",
            person_id=person_id,
            session_id=session_id,
            status=str(result.get("status") or "UNKNOWN"),
            action_summary=_clean_text(result.get("fact"), limit=360),
            ts_ms=int(ts_ms or time.time() * 1000),
            evidence_refs=evidence_refs,
            metadata={
                "domain_event_kind": "generic_evidence_review",
                "reason_code": result.get("reason_code"),
                "fact": result.get("fact"),
                "items": list(result.get("items") or []),
            },
        )
    except Exception as exc:
        return {"status": "FAILED", "reason_code": "PERSIST_FAILED", "detail": str(exc)[:160]}


def _attach_persist_result(result: dict[str, Any], persist_result: Mapping[str, Any]) -> None:
    result["persist_status"] = str(persist_result.get("status") or "")
    result["persisted_episode_id"] = str(persist_result.get("episode_id") or "")
    result["persist_reason_code"] = str(persist_result.get("reason_code") or "")


def _stable_result_signature(result: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "items": list(result.get("items") or []),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:32]


__all__ = [
    "review_generic_evidence_from_memory_packet",
    "should_review_generic_evidence",
]
