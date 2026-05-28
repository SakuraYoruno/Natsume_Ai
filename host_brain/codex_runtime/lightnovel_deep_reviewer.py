"""Action-backed review for recently recommended light novels."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse


_DETAIL_URL_RE = re.compile(r"https?://[^\s｜|；。]+", re.IGNORECASE)
_NUMBERED_ITEM_RE = re.compile(r"(?<!\d)(\d{1,2})[.、]\s*([^；\n]+)")
_CHOICE_QUERY_RE = re.compile(
    r"(?:喜欢|好看|想看|先看|推荐|选|挑).{0,8}哪|"
    r"哪(?:本|部|个)|哪个|哪部|"
    r"(?:这本|这部|这一个|这個|这本书|这部书|这书|它).{0,8}(?:好看|能看|值得看|怎么样|如何)"
)
_BODY_TEXT_MIN_CHARS = 500
_CACHE_VERSION = "lightnovel_deep_review:v3"


def _clean_text(value: Any, *, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
    if limit > 0 and len(text) > limit:
        return text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _enabled() -> bool:
    raw = str(os.environ.get("HOST_LIGHTNOVEL_DEEP_REVIEW_ENABLED") or "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def _cache_enabled() -> bool:
    raw = str(os.environ.get("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_ENABLED") or "").strip().lower()
    if not raw:
        return True
    return raw in {"1", "true", "yes", "on"}


def should_deep_review_lightnovel_selection(*, user_message: str, packet: Mapping[str, Any]) -> bool:
    if not _enabled():
        return False
    text = _clean_text(user_message)
    if not _CHOICE_QUERY_RE.search(text):
        return False
    facts = "\n".join(str(item or "") for item in list(packet.get("facts") or []))
    if not facts:
        return False
    if not any(token in facts for token in ("轻小说", "lightnovel", "lightnovel.fun", "可比较证据")):
        return False
    return bool(_DETAIL_URL_RE.search(facts)) or _trace_has_detail_refs(packet) or "推荐了" in facts


def review_lightnovel_selection_from_memory_packet(
    *,
    packet: Mapping[str, Any],
    user_message: str,
    request_id: str = "",
    person_id: str | None = None,
    session_id: str | None = None,
    ts_ms: int | None = None,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    cards = _extract_cards(packet)
    timeout_value = int(timeout_s or os.environ.get("HOST_LIGHTNOVEL_DEEP_REVIEW_TIMEOUT_S") or 12)
    max_items = _review_item_limit(user_message=user_message, card_count=len(cards))
    cards = cards[: max(1, min(max_items, 6))]
    signature = _cards_signature(cards)
    if not cards:
        result = _result(
            status="SKIPPED",
            reason_code="LIGHTNOVEL_DEEP_REVIEW_NO_CARDS",
            fact="我没拿到刚才那几本书的详情链接或 epub，所以不能装作已经翻看过。",
            items=[],
        )
        _annotate_review_result(result, recommendation_signature=signature, cached=False)
        persist_result = _persist_review_memory(
            result=result,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            recommendation_signature=signature,
        )
        _attach_persist_result(result, persist_result)
        return result

    cached = _load_cached_review(
        cards=cards,
        recommendation_signature=signature,
        person_id=person_id,
        session_id=session_id,
        now_ms=int(ts_ms or time.time() * 1000),
    )
    if cached:
        return cached

    reviewed = _review_cards(cards, timeout_s=timeout_value)

    ok_items = [item for item in reviewed if str(item.get("review_status") or "") == "OK"]
    if not ok_items:
        fact = "我回去查了详情页，但这次没有拿到可读网页或 epub；这轮不能负责任地说哪本更好。"
        result = _result(
            status="DEGRADED",
            reason_code="LIGHTNOVEL_DEEP_REVIEW_FETCH_FAILED",
            fact=fact,
            items=reviewed,
        )
        _annotate_review_result(result, recommendation_signature=signature, cached=False)
        persist_result = _persist_review_memory(
            result=result,
            request_id=request_id,
            person_id=person_id,
            session_id=session_id,
            ts_ms=ts_ms,
            recommendation_signature=signature,
        )
        _attach_persist_result(result, persist_result)
        return result

    selected = max(ok_items, key=lambda item: float(item.get("deep_review_score") or 0.0))
    fact = _build_review_fact(selected=selected, items=reviewed)
    result = _result(
        status="OK",
        reason_code="LIGHTNOVEL_DEEP_REVIEW_OK",
        fact=fact,
        items=reviewed,
        selected=selected,
    )
    _annotate_review_result(result, recommendation_signature=signature, cached=False)
    persist_result = _persist_review_memory(
        result=result,
        request_id=request_id,
        person_id=person_id,
        session_id=session_id,
        ts_ms=ts_ms,
        recommendation_signature=signature,
    )
    _attach_persist_result(result, persist_result)
    return result


def _review_cards(cards: Sequence[Mapping[str, Any]], *, timeout_s: int) -> list[dict[str, Any]]:
    indexed_cards = [(index, dict(card)) for index, card in enumerate(list(cards or []), start=1)]
    if not indexed_cards:
        return []
    max_workers = _review_worker_count(len(indexed_cards))
    if max_workers <= 1:
        return [_review_one_card(index=index, card=card, timeout_s=timeout_s) for index, card in indexed_cards]
    reviewed_by_index: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ln_deep_review") as executor:
        futures = {
            executor.submit(_review_one_card, index=index, card=card, timeout_s=timeout_s): index
            for index, card in indexed_cards
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                reviewed_by_index[index] = future.result()
            except Exception as exc:
                item = dict(indexed_cards[index - 1][1])
                item["index"] = int(item.get("index") or index)
                item.update(
                    {
                        "review_status": "FAILED",
                        "review_reason_code": "DETAIL_REVIEW_FAILED",
                        "review_error": str(exc)[:120],
                    }
                )
                item.update(_score_item(item, source_index=index - 1))
                reviewed_by_index[index] = item
    return [reviewed_by_index[index] for index, _card in indexed_cards if index in reviewed_by_index]


def _review_worker_count(card_count: int) -> int:
    raw = str(os.environ.get("HOST_LIGHTNOVEL_DEEP_REVIEW_WORKERS") or "").strip()
    try:
        configured = int(raw) if raw else 3
    except ValueError:
        configured = 3
    return max(1, min(int(card_count), configured, 6))


def _review_one_card(*, index: int, card: Mapping[str, Any], timeout_s: int) -> dict[str, Any]:
    item = dict(card)
    item["index"] = int(item.get("index") or index)
    detail_url = str(item.get("detail_url") or "").strip()
    if detail_url:
        item.update(_fetch_detail(detail_url, timeout_s=timeout_s))
    else:
        item["review_status"] = "NO_DETAIL_URL"
    item.update(_score_item(item, source_index=index - 1))
    return item


def _result(
    *,
    status: str,
    reason_code: str,
    fact: str,
    items: Sequence[Mapping[str, Any]],
    selected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_refs = [
        str(item.get("detail_url") or "")
        for item in items
        if str(item.get("detail_url") or "").strip()
    ]
    return {
        "status": status,
        "reason_code": reason_code,
        "fact": _clean_text(fact, limit=720),
        "selected": _public_item(selected or {}),
        "items": [_public_item(item) for item in items],
        "evidence_refs": evidence_refs,
    }


def _cached_result(
    *,
    fact: str,
    items: Sequence[Mapping[str, Any]],
    selected: Mapping[str, Any] | None,
    source_episode_id: str,
    recommendation_signature: str,
) -> dict[str, Any]:
    result = _result(
        status="OK",
        reason_code="LIGHTNOVEL_DEEP_REVIEW_CACHE_HIT",
        fact=fact or _build_review_fact(selected=selected or {}, items=items),
        items=items,
        selected=selected,
    )
    result["cached"] = True
    result["source"] = "cache"
    result["source_episode_id"] = source_episode_id
    result["recommendation_signature"] = str(recommendation_signature or "")
    return result


def _annotate_review_result(
    result: dict[str, Any],
    *,
    recommendation_signature: str,
    cached: bool,
) -> None:
    result["recommendation_signature"] = str(recommendation_signature or "")
    result["cached"] = bool(cached)
    result["source"] = "cache" if cached else "fetch"


def _attach_persist_result(result: dict[str, Any], persist_result: Mapping[str, Any] | None) -> None:
    if not isinstance(persist_result, Mapping):
        return
    result["persist_status"] = persist_result.get("status")
    life = persist_result.get("life_memory") if isinstance(persist_result.get("life_memory"), Mapping) else {}
    if life:
        result["persisted_episode_id"] = life.get("episode_id")
        result["persisted_primary_ref"] = life.get("primary_ref")


def _public_item(item: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "index",
        "title",
        "detail_url",
        "review_status",
        "intro_snippet",
        "update_time",
        "view_count",
        "comment_count",
        "chapter_signal",
        "content_char_count",
        "body_quality",
        "body_observations",
        "selection_reason",
        "deep_review_score",
        "score_reasons",
    )
    return {key: item.get(key) for key in keys if item.get(key) not in (None, "", [], {})}


def _load_cached_review(
    *,
    cards: Sequence[Mapping[str, Any]],
    recommendation_signature: str,
    person_id: str | None,
    session_id: str | None,
    now_ms: int,
) -> dict[str, Any] | None:
    if not _cache_enabled() or not recommendation_signature:
        return None
    max_age_days = float(os.environ.get("HOST_LIGHTNOVEL_DEEP_REVIEW_CACHE_MAX_AGE_DAYS") or 30)
    max_age_ms = max(1, int(max_age_days * 24 * 60 * 60 * 1000))
    try:
        from host_brain.app.dependencies import get_life_memory_store  # noqa: PLC0415

        store = get_life_memory_store()
    except Exception:
        return None
    ledger = getattr(store, "ledger", None)
    conn = getattr(ledger, "_conn", None)
    if conn is None:
        return None
    clauses = [
        "COALESCE(action, '') = ?",
        "COALESCE(status, '') != 'SUPPRESSED'",
        "COALESCE(truth_state, '') != 'SUPPRESSED'",
    ]
    params: list[Any] = ["lightnovel_recommendation_deep_review"]
    person = str(person_id or "").strip()
    session = str(session_id or "").strip()
    if person:
        clauses.append("(COALESCE(person_id, '') = ? OR COALESCE(person_id, '') = '')")
        params.append(person)
    elif session:
        clauses.append("(COALESCE(session_id, '') = ? OR COALESCE(session_id, '') = '')")
        params.append(session)
    try:
        rows = list(
            conn.execute(
                f"""
                SELECT episode_id, summary, last_touched_ts_ms, metadata
                FROM life_episode
                WHERE {' AND '.join(clauses)}
                ORDER BY last_touched_ts_ms DESC
                LIMIT 24
                """,
                params,
            ).fetchall()
        )
    except Exception:
        return None
    card_urls = [_normalize_detail_url(str(item.get("detail_url") or "")) for item in list(cards or [])]
    for row in rows:
        episode = dict(row)
        meta = _safe_json_dict(episode.get("metadata"))
        if meta.get("deep_review_cache_version") != _CACHE_VERSION:
            continue
        if meta.get("recommendation_signature") != recommendation_signature:
            continue
        touched = int(episode.get("last_touched_ts_ms") or 0)
        if touched and int(now_ms) - touched > max_age_ms:
            continue
        items = [dict(item) for item in list(meta.get("items") or []) if isinstance(item, Mapping)]
        selected = dict(meta.get("selected") or {}) if isinstance(meta.get("selected"), Mapping) else {}
        cached_urls = [_normalize_detail_url(str(item.get("detail_url") or "")) for item in items]
        if card_urls and cached_urls and not all(url in cached_urls for url in card_urls if url):
            continue
        fact = str(meta.get("fact") or episode.get("summary") or "").strip()
        if not items:
            continue
        return _cached_result(
            fact=fact,
            items=items,
            selected=selected,
            source_episode_id=str(episode.get("episode_id") or ""),
            recommendation_signature=recommendation_signature,
        )
    return None


def _safe_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        data = json.loads(value)
    except Exception:
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _extract_cards(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    cards = _extract_trace_cards(packet)
    facts = [str(item or "") for item in list(packet.get("facts") or [])]
    for fact in facts:
        if _is_deep_review_memory_fact(fact):
            continue
        source = fact.split("可比较证据：", 1)[-1] if "可比较证据：" in fact else fact
        for match in _NUMBERED_ITEM_RE.finditer(source):
            segment = _clean_text(match.group(2))
            if "｜" not in segment and "详情：" not in segment:
                continue
            card = _parse_card_segment(match.group(1), segment)
            if card and not any(_same_card(card, old) for old in cards):
                cards.append(card)
    return cards


def _trace_has_detail_refs(packet: Mapping[str, Any]) -> bool:
    return any(_normalize_detail_url(str(item.get("detail_url") or "")) for item in _trace_review_items(packet))


def _extract_trace_cards(packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for index, item in enumerate(_trace_review_items(packet), start=1):
        title = _clean_text(item.get("title"), limit=120)
        detail_url = _normalize_detail_url(str(item.get("detail_url") or item.get("source_ref") or ""))
        if not title and not detail_url:
            continue
        card = {
            "index": int(item.get("index") or index),
            "title": title,
            "detail_url": detail_url,
            "selection_reason": _clean_text(item.get("selection_reason"), limit=180),
            "intro_snippet": _clean_text(item.get("intro_snippet"), limit=260),
            "update_time": _clean_text(item.get("update_time"), limit=80),
            "view_count": _clean_text(item.get("view_count"), limit=40),
            "comment_count": _clean_text(item.get("comment_count"), limit=40),
        }
        card = {key: value for key, value in card.items() if value not in ("", 0, None, [], {})}
        if card and not any(_same_card(card, old) for old in cards):
            cards.append(card)
    return cards


def _trace_review_items(packet: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    items: list[Mapping[str, Any]] = []
    for item in _trace_collection_items(packet):
        if item not in items:
            items.append(item)
    for item in _trace_entity_items(packet):
        if item not in items:
            items.append(item)
    return items


def _trace_collection_items(packet: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    trace = packet.get("trace")
    if not isinstance(trace, Mapping):
        return []
    raw_items = trace.get("selected_collection_items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in raw_items if isinstance(item, Mapping)]


def _trace_entity_items(packet: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    trace = packet.get("trace")
    if not isinstance(trace, Mapping):
        return []
    raw_items = trace.get("selected_entity_items")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
        return []
    return [dict(item) for item in raw_items if isinstance(item, Mapping)]


def _review_item_limit(*, user_message: str, card_count: int) -> int:
    raw = str(os.environ.get("HOST_LIGHTNOVEL_DEEP_REVIEW_MAX_ITEMS") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    text = _clean_text(user_message)
    broad = any(token in text for token in ("十几本", "这几天", "最近", "全部", "所有", "哪些"))
    if broad:
        return min(max(3, int(card_count or 0)), 6)
    return 3


def _is_deep_review_memory_fact(fact: str) -> bool:
    text = _clean_text(fact)
    if not text:
        return False
    return "网页深读结果" in text or "lightnovel_recommendation_deep_review" in text or "当时更偏向第" in text


def _cards_signature(cards: Sequence[Mapping[str, Any]]) -> str:
    compact: list[dict[str, str]] = []
    for item in list(cards or [])[:6]:
        url = _normalize_detail_url(str(item.get("detail_url") or ""))
        title = _clean_text(item.get("title"), limit=120)
        compact.append({"url": url, "title": title})
    payload = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{_CACHE_VERSION}:{payload}".encode("utf-8", errors="ignore")).hexdigest()[:32]


def _normalize_detail_url(url: str) -> str:
    value = str(url or "").strip().rstrip("。；;")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value
    path = re.sub(r"/+$", "", parsed.path or "")
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{path}"


def _same_card(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        str(left.get("detail_url") or "").strip()
        and str(left.get("detail_url") or "").strip() == str(right.get("detail_url") or "").strip()
    ) or (
        _clean_text(left.get("title")).lower()
        and _clean_text(left.get("title")).lower() == _clean_text(right.get("title")).lower()
    )


def _parse_card_segment(index: str, segment: str) -> dict[str, Any]:
    parts = [_clean_text(part) for part in re.split(r"[｜|]", segment) if _clean_text(part)]
    if not parts:
        return {}
    title = re.sub(r"^\d{2,5}[.、]\s*", "", parts[0]).strip()
    card: dict[str, Any] = {"index": int(index), "title": title}
    for part in parts[1:]:
        label, sep, value = part.partition("：")
        if not sep:
            label, sep, value = part.partition(":")
        key = _label_key(label)
        if key and value:
            card[key] = _clean_text(value, limit=260)
    if not card.get("detail_url"):
        match = _DETAIL_URL_RE.search(segment)
        if match:
            card["detail_url"] = match.group(0).rstrip("。；;")
    return card


def _label_key(label: str) -> str:
    value = _clean_text(label)
    return {
        "看点": "selection_reason",
        "简介": "intro_snippet",
        "题材": "topic",
        "更新": "update_time",
        "热度": "heat_text",
        "详情": "detail_url",
        "链接": "detail_url",
    }.get(value, "")


def _fetch_detail(url: str, *, timeout_s: int) -> dict[str, Any]:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"review_status": "FAILED", "review_reason_code": "INVALID_URL"}
    try:
        import requests  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover
        return {"review_status": "FAILED", "review_reason_code": "IMPORT_FAILED", "review_error": str(exc)}
    last_error: Exception | None = None
    response = None
    for attempt in range(2):
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Natsume-Ai-LightnovelDeepReview/1.0",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.2",
                },
                timeout=max(3, int(timeout_s)),
            )
            break
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.35)
    if response is None:
        return {"review_status": "FAILED", "review_reason_code": "FETCH_FAILED", "review_error": str(last_error)[:120]}
    if response.status_code >= 400:
        return {"review_status": "FAILED", "review_reason_code": f"HTTP_{response.status_code}"}
    soup = BeautifulSoup(response.text or "", "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    title = _select_text(soup, ".article-title,h1,h2", limit=160)
    description = _meta_description(soup)
    info = _select_text(soup, ".article-infos", limit=900) or _clean_text(soup.get_text(" "), limit=900)
    chapter_signal = _chapter_signal(soup)
    content_text = _article_content_text(soup)
    if len(content_text) < _BODY_TEXT_MIN_CHARS:
        linked = _fetch_linked_chapter_text(soup, base_url=url, timeout_s=timeout_s)
        if len(linked) > len(content_text):
            content_text = linked
    body_review = _review_body_content(content_text, title=title or description)
    if not description and body_review.get("body_summary"):
        description = str(body_review.get("body_summary") or "")
    if not chapter_signal and body_review.get("chapter_signal"):
        chapter_signal = str(body_review.get("chapter_signal") or "")
    stats = _parse_stats(info)
    return {
        "review_status": "OK",
        "fetched_at_ms": int(time.time() * 1000),
        "title": title,
        "intro_snippet": description,
        "chapter_signal": chapter_signal,
        **body_review,
        **stats,
    }


def _select_text(soup: Any, selector: str, *, limit: int) -> str:
    node = soup.select_one(selector)
    return _clean_text(node.get_text(" ", strip=True) if node is not None else "", limit=limit)


def _meta_description(soup: Any) -> str:
    for attrs in ({"name": re.compile("^description$", re.IGNORECASE)}, {"property": "og:description"}):
        node = soup.find("meta", attrs=attrs)
        if node is not None:
            text = _clean_text(node.get("content"), limit=280)
            if text:
                return text
    return ""


def _chapter_signal(soup: Any) -> str:
    titles: list[str] = []
    for anchor in soup.find_all("a"):
        text = _clean_text(anchor.get_text(" "), limit=80)
        if not text:
            continue
        if re.search(r"(第\s*\d+\s*[章节話话]|序章|终章|終章|番外|chapter)", text, re.IGNORECASE):
            titles.append(text)
        if len(titles) >= 3:
            break
    return "；".join(titles)


def _article_content_text(soup: Any) -> str:
    content = soup.select_one(".article-content")
    if content is None:
        return ""
    return _normalize_body_text(content.get_text("\n", strip=True))


def _normalize_body_text(text: Any, *, limit: int = 120_000) -> str:
    lines: list[str] = []
    for raw_line in str(text or "").replace("\u3000", " ").splitlines():
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    normalized = "\n".join(lines).strip()
    if limit > 0 and len(normalized) > limit:
        return normalized[:limit]
    return normalized


def _fetch_linked_chapter_text(soup: Any, *, base_url: str, timeout_s: int) -> str:
    candidate_urls: list[str] = []
    for anchor in soup.find_all("a"):
        text = _clean_text(anchor.get_text(" "), limit=80)
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        if not re.search(r"(第\s*\d+\s*[章节話话]|序章|终章|終章|番外|chapter)", text, re.IGNORECASE):
            continue
        absolute = urljoin(base_url, href)
        if absolute not in candidate_urls:
            candidate_urls.append(absolute)
        if len(candidate_urls) >= 2:
            break
    if not candidate_urls:
        return ""
    try:
        import requests  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except Exception:  # pragma: no cover
        return ""
    texts: list[str] = []
    for url in candidate_urls:
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Natsume-Ai-LightnovelDeepReview/1.0",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.2",
                    "Referer": base_url,
                },
                timeout=max(3, int(timeout_s)),
            )
            if response.status_code >= 400:
                continue
            linked_soup = BeautifulSoup(response.text or "", "html.parser")
            text = _article_content_text(linked_soup)
            if text:
                texts.append(text)
        except Exception:
            continue
    return _normalize_body_text("\n".join(texts))


def _review_body_content(text: str, *, title: str) -> dict[str, Any]:
    content = _normalize_body_text(text)
    char_count = len(content)
    if char_count < _BODY_TEXT_MIN_CHARS:
        return {
            "body_quality": "NO_READABLE_BODY",
            "content_char_count": char_count,
            "body_observations": [],
        }

    first_line = _body_opening_line(content, fallback=title)
    body_chapter_signal = _body_chapter_signal(content)
    observations: list[str] = ["正文内容足够支撑判断"]
    if first_line and _looks_like_body_heading(first_line):
        observations.append(f"正文标题/开篇：{first_line}")
    elif first_line:
        observations.append("正文开篇已经进入故事叙事，不只是资料卡")

    signals = _body_signal_observations(content, title=title)
    observations.extend(signals)
    body_summary = "；".join(observations[1:4] or observations[:3])
    return {
        "body_quality": "READABLE_BODY",
        "content_char_count": char_count,
        "chapter_title": first_line,
        "chapter_signal": body_chapter_signal,
        "body_observations": observations[:5],
        "body_summary": body_summary,
    }


def _body_opening_line(content: str, *, fallback: str) -> str:
    housekeeping = re.compile(
        r"^("
        r"\d{1,2}/\d{1,2}\s*|"
        r"下载|下載|MG|"
        r"[-─—_]{3,}|"
        r"日文書名|中文標題|中文标题|作者|插圖|插图|圖源|图源|翻譯|翻译|校對|校对|"
        r"僅供|仅供|請|请|本站|轻之国度|輕之國度"
        r")",
        re.IGNORECASE,
    )
    lines = [_clean_text(line, limit=80) for line in content.splitlines()]
    for line in lines:
        if not line or housekeeping.search(line):
            continue
        if line in {"简介", "簡介", "序", "目录", "目錄"}:
            continue
        return _clean_text(line, limit=48)
    return _clean_text(fallback, limit=80)


def _looks_like_body_heading(line: str) -> bool:
    text = _clean_text(line)
    if len(text) > 52:
        return False
    return bool(
        re.match(r"^(序章|第\s*[\d一二三四五六七八九十百〇零]+\s*[章节話话]|终章|終章|番外|后记|後記|特典|chapter\b)", text, re.IGNORECASE)
        or re.match(r"^\d{1,4}[.、]", text)
        or "话" in text[:12]
        or "話" in text[:12]
    )


def _body_chapter_signal(content: str) -> str:
    signals: list[str] = []
    heading_re = re.compile(
        r"^(序章|第\s*[\d一二三四五六七八九十百〇零]+\s*[章节話话]|终章|終章|番外|后记|後記|特典|chapter\b)",
        re.IGNORECASE,
    )
    for raw_line in content.splitlines():
        line = _clean_text(raw_line, limit=60)
        if not line:
            continue
        if heading_re.match(line):
            signals.append(line)
        if len(signals) >= 3:
            break
    return "；".join(signals)


def _compact_count(value: int) -> str:
    if value >= 10000:
        return f"{value / 10000:.1f}万"
    return str(value)


def _count_tokens(text: str, tokens: Sequence[str]) -> int:
    return sum(str(text or "").count(token) for token in tokens)


def _body_signal_observations(content: str, *, title: str) -> list[str]:
    text = f"{title}\n{content}"
    groups: list[tuple[str, tuple[str, ...], str]] = [
        (
            "romance_domestic",
            ("恋爱", "恋愛", "晚安", "电话", "寂寞", "同居", "钥匙", "害羞", "撒娇", "房间", "依赖"),
            "读到的正文偏恋爱日常，核心在人物关系和亲密感推进",
        ),
        (
            "fantasy_world",
            ("异世界", "異世界", "皇女", "皇帝", "帝国", "奧爾良", "魔法", "王国", "王國", "贵族", "貴族", "后宫", "後宮"),
            "正文/简介有异世界皇女、皇帝或后宫路线，世界观钩子更直给",
        ),
        (
            "conversation_comedy",
            ("王道", "邪道", "章鱼烧", "章魚燒", "吐槽", "哈哈", "困惑", "认真", "闲聊", "河岸"),
            "正文主要靠对话和日常梗推进，轻喜剧感比较明显",
        ),
        (
            "growth_plan",
            ("培养", "培養", "计划", "計劃", "目标", "目標", "改善", "疗养", "孤独", "笑容"),
            "正文有成长/照顾线，角色关系不是只停在设定介绍",
        ),
    ]
    scored: list[tuple[int, str]] = []
    for _name, tokens, observation in groups:
        count = _count_tokens(text, tokens)
        if count > 0:
            scored.append((count, observation))
    quote_count = text.count("“") + text.count("「")
    if quote_count >= 12:
        scored.append((quote_count // 4, "对白密度高，读感更偏轻快互动"))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [observation for _score, observation in scored[:3]]


def _parse_stats(text: str) -> dict[str, str]:
    compact = _clean_text(text, limit=1000)
    out: dict[str, str] = {}
    update_match = re.search(r"(\d+(?:\.\d+)?\s*(?:分钟|分鐘|小時|小时|天|日|个月|個月|月|年)前)", compact)
    if update_match:
        out["update_time"] = update_match.group(1).strip()
    view_match = re.search(r"(?:閱覽數|閲覧数|浏览数|閱讀數|阅读数)\s*[:：]\s*([0-9.]+\s*(?:w|k|万|萬|千)?)", compact, re.IGNORECASE)
    if view_match:
        out["view_count"] = view_match.group(1).strip()
    comment_match = re.search(r"(?:評論|评论)\s*[:：]\s*([0-9.]+\s*(?:w|k|万|萬|千)?)", compact, re.IGNORECASE)
    if comment_match:
        out["comment_count"] = comment_match.group(1).strip()
    return out


def _score_item(item: Mapping[str, Any], *, source_index: int) -> dict[str, Any]:
    text = " ".join(
        _clean_text(item.get(key), limit=260)
        for key in ("title", "selection_reason", "intro_snippet", "topic", "chapter_signal")
        if _clean_text(item.get(key))
    )
    score = 20.0 - float(source_index)
    reasons: list[str] = []
    if any(token in text for token in ("恋爱", "恋愛", "日常", "青春", "校园", "學園", "学园")):
        score += 12.0
        reasons.append("恋爱日常信号明确")
    if any(token in text for token in ("异世界", "異世界", "皇女", "皇帝", "转生", "轉生", "奇幻", "魔法")):
        score += 10.0
        reasons.append("奇幻身份钩子明确")
    if any(token in text for token in ("新鲜", "新鮮", "培养", "培養", "反差", "偶像", "模拟", "遊戲", "游戏")):
        score += 8.0
        reasons.append("设定有新鲜感")
    if item.get("intro_snippet"):
        score += 5.0
        reasons.append("简介可读")
    content_chars = int(item.get("content_char_count") or 0)
    if content_chars >= _BODY_TEXT_MIN_CHARS:
        score += 10.0
        reasons.append("读到了正文内容")
    if content_chars >= 5000:
        score += 6.0
        reasons.append("正文体量充足")
    if item.get("body_observations"):
        score += 5.0
        reasons.append("有正文观察")
    if item.get("chapter_signal"):
        score += 4.0
        reasons.append("有章节/目录线索")
    if item.get("update_time"):
        score += 4.0
        reasons.append(f"更新较近：{item.get('update_time')}")
    if item.get("view_count") or item.get("comment_count") or item.get("heat_text"):
        score += 3.0
        reasons.append("热度有公开信号")
    if str(item.get("review_status") or "") != "OK":
        score -= 20.0
        reasons.append("详情页未读到")
    return {"deep_review_score": round(score, 2), "score_reasons": reasons[:4]}


def _build_review_fact(*, selected: Mapping[str, Any], items: Sequence[Mapping[str, Any]]) -> str:
    index = int(selected.get("index") or 1)
    title = _clean_text(selected.get("title"), limit=90) or "这本"
    body_observations = [
        _clean_text(item, limit=86)
        for item in list(selected.get("body_observations") or [])
        if _clean_text(item)
    ]
    detail_bits: list[str] = _spoken_memory_reason_bits(body_observations)
    if not detail_bits:
        for observation in body_observations[:4]:
            if "正文内容足够支撑判断" in observation:
                continue
            detail_bits.append(_spoken_review_reason(observation))
    intro = _fact_intro(selected.get("intro_snippet"))
    if intro and len(detail_bits) < 3:
        detail_bits.append(f"简介也不是空的，能看出{intro}")
    if selected.get("chapter_signal"):
        detail_bits.append("章节线索也比较清楚")
    if selected.get("update_time"):
        detail_bits.append(f"更新还算新，是{selected.get('update_time')}")
    if selected.get("view_count") or selected.get("comment_count"):
        heat = " / ".join(str(selected.get(key)) for key in ("view_count", "comment_count") if selected.get(key))
        detail_bits.append(f"公开热度有一点底，{heat}")
    reasons = list(selected.get("score_reasons") or [])
    spoken_reasons = [
        _spoken_review_reason(reason)
        for reason in reasons
        if str(reason or "") not in {"读到了正文内容", "正文体量充足", "有正文观察"}
    ]
    for reason in spoken_reasons:
        if reason and reason not in detail_bits and len(detail_bits) < 4:
            detail_bits.append(reason)
    if not detail_bits:
        detail_bits.append("翻进去以后比另外几本更想继续看")
    other_titles = [
        _other_item_summary(item, fallback_index=i)
        for i, item in enumerate(items, start=1)
        if int(item.get("index") or i) != index and item.get("title")
    ][:2]
    compare = f"另外我也顺手看了：{'、'.join(other_titles)}。" if other_titles else ""
    reason_text = "；".join(
        dict.fromkeys(
            _clean_reason_bit(bit)
            for bit in detail_bits[:5]
            if _clean_reason_bit(bit)
        )
    )
    return (
        f"网页深读结果（含正文）：当前更推荐第{index}本《{title}》。"
        f"原因：{reason_text}。{compare}"
    )


def _clean_reason_bit(value: Any) -> str:
    return _clean_text(value, limit=100).rstrip("。；;，,、 ")


def _spoken_review_reason(value: Any) -> str:
    text = _clean_text(value, limit=90)
    if not text:
        return ""
    replacements = (
        ("正文开篇已经进入故事叙事，不只是资料卡", "开篇不是资料卡，已经能读出故事"),
        ("正文/简介有异世界皇女、皇帝或后宫路线，世界观钩子更直给", "皇女/皇帝这条线更直接"),
        ("读到的正文偏恋爱日常，核心在人物关系和亲密感推进", "人物互动更好接"),
        ("正文主要靠对话和日常梗推进，轻喜剧感比较明显", "对话和日常梗更轻快"),
        ("正文有成长/照顾线，角色关系不是只停在设定介绍", "照顾线能撑住人物关系"),
        ("对白密度高，读感更偏轻快互动", "对白比较轻快"),
        ("恋爱日常信号明确", "恋爱日常的味道更明确"),
        ("奇幻身份钩子明确", "奇幻身份钩子更明确"),
        ("设定有新鲜感", "设定更有新鲜感"),
        ("简介可读", "简介读起来更顺"),
        ("有章节/目录线索", "章节线索也比较清楚"),
        ("热度有公开信号", "公开热度也有一点底"),
    )
    for raw, spoken in replacements:
        if raw in text:
            return spoken
    return text


def _fact_intro(value: Any) -> str:
    intro = _clean_text(value, limit=80)
    if not intro:
        return ""
    noisy_tokens = ("下載", "下载", "圖源", "图源", "翻譯", "翻译", "僅供", "仅供", "商業用途", "商业用途")
    if any(token in intro for token in noisy_tokens):
        return ""
    return intro


def _other_item_summary(item: Mapping[str, Any], *, fallback_index: int) -> str:
    index = int(item.get("index") or fallback_index)
    title = _clean_text(item.get("title"), limit=28)
    return f"第{index}本《{title}》"


def _persist_review_memory(
    *,
    result: Mapping[str, Any],
    request_id: str,
    person_id: str | None,
    session_id: str | None,
    ts_ms: int | None,
    recommendation_signature: str,
) -> dict[str, Any]:
    try:
        from host_brain.app.observability_persist import persist_life_action_event_memory  # noqa: PLC0415
    except Exception:
        return {"status": "SKIPPED", "reason_code": "PERSIST_IMPORT_FAILED"}
    signature = str(recommendation_signature or "").strip()
    owner = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(person_id or session_id or "global").strip())[:80] or "global"
    action_id = f"{owner}:lightnovel_deep_review:{signature or _stable_result_signature(result)}"
    memory_anchor = _build_review_memory_anchor(result)
    evidence_refs = list(result.get("evidence_refs") or [])
    if signature:
        evidence_refs.append(f"action://event/{action_id}")
    try:
        return persist_life_action_event_memory(
            event_source="surface_memory_recall",
            action_kind="lightnovel_recommendation_deep_review",
            action_id=action_id,
            actor_id="natsume",
            request_id=request_id or None,
            intent_id="surface_memory_followup",
            person_id=person_id,
            session_id=session_id,
            status=str(result.get("status") or "UNKNOWN"),
            action_summary=memory_anchor or str(result.get("fact") or ""),
            ts_ms=int(ts_ms or time.time() * 1000),
            evidence_refs=evidence_refs,
            metadata={
                "domain_event_kind": "lightnovel_recommendation_deep_review",
                "deep_review_cache_version": _CACHE_VERSION,
                "recommendation_signature": signature,
                "reason_code": result.get("reason_code"),
                "fact": result.get("fact"),
                "memory_anchor": memory_anchor,
                "selected": dict(result.get("selected") or {}),
                "items": list(result.get("items") or []),
            },
        )
    except Exception as exc:
        return {"status": "FAILED", "reason_code": "PERSIST_FAILED", "detail": str(exc)[:160]}


def _stable_result_signature(result: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {
            "selected": dict(result.get("selected") or {}),
            "items": list(result.get("items") or []),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()[:32]


def _build_review_memory_anchor(result: Mapping[str, Any]) -> str:
    selected = dict(result.get("selected") or {}) if isinstance(result.get("selected"), Mapping) else {}
    items = [dict(item) for item in list(result.get("items") or []) if isinstance(item, Mapping)]
    if not selected or not items:
        return _clean_text(result.get("fact"), limit=360)
    index = int(selected.get("index") or 1)
    title = _clean_text(selected.get("title"), limit=80) or "这本"
    observations = [
        _clean_text(value, limit=80)
        for value in list(selected.get("body_observations") or [])
        if _clean_text(value)
    ]
    reason_bits = _spoken_memory_reason_bits(observations)
    other_count = max(0, len(items) - 1)
    compare_text = f"也对比了另外 {other_count} 本。" if other_count else ""
    reason_text = "，".join(reason_bits[:2]) if reason_bits else "翻进去后更想继续看"
    return f"我翻过这组轻小说的详情和正文；当时更偏向第{index}本《{title}》，因为{reason_text}。{compare_text}".strip()


def _spoken_memory_reason_bits(observations: Sequence[str]) -> list[str]:
    joined = re.sub(r"\s+", "", " ".join(str(item or "") for item in observations))
    bits: list[str] = []
    if "开篇已经进入故事" in joined or "正文标题/开篇" in joined:
        bits.append("开篇比较抓人")
    if "异世界" in joined or "皇女" in joined or "皇帝" in joined:
        bits.append("设定线很直给")
    if "恋爱日常" in joined or "人物关系" in joined or "亲密感" in joined:
        bits.append("人物互动更好接")
    if "轻喜剧" in joined:
        bits.append("轻喜剧感明显")
    if "对白密度" in joined:
        bits.append("对白轻快")
    return list(dict.fromkeys(bits))


__all__ = [
    "review_lightnovel_selection_from_memory_packet",
    "should_deep_review_lightnovel_selection",
]
