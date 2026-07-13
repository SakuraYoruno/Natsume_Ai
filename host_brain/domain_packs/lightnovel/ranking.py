"""Lightnovel taste profiles, history, scoring and ranking."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from host_brain.core.subprocess_utils import run as _subprocess_run
import threading
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlparse

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.production_studio import (
    AGENT_PRODUCTION_SKILL_IDS,
    build_persona_boundary_contract,
)
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.contracts.agent_runtime.work_order import AgentWorkOrder
from host_brain.capability_plane.broker import (
    CapabilityBroker,
    CapabilityDescriptor,
    CapabilityInvocationError,
)
from host_brain.capability_plane.contracts import CapabilityKind, CapabilityRequest
from host_brain.capability_plane.policies import PredicateCapabilityPolicy
from host_brain.core.ids import new_event_id, now_ms
from host_brain.domain_packs.production_composition import (
    ProductionDomainPackRuntime,
    build_production_domain_pack_runtime,
)
from host_brain.domain_packs.registry import DomainPackResolutionError

logger = logging.getLogger(__name__)

from host_brain.domain_packs.production_jobs import _resolve_order_chat_context

def _parse_bool_env(name: str, *, default: bool = False) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _parse_int_env(name: str, *, default: int, min_value: int, max_value: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(min_value), min(int(value), int(max_value)))


def _parse_env_text(name: str, *, default: str = "") -> str:
    value = str(os.environ.get(name) or "").strip()
    return value if value else str(default or "").strip()


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def _coerce_recommendation_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = payload.get("recommendations") or payload.get("items") or payload.get("value") or []
    else:
        rows = []
    items: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            items.append(dict(row))
    return items


def _default_lightnovel_taste_profile() -> dict[str, Any]:
    return {
        "version": "bootstrap-20260522",
        "profile_kind": "host_lightnovel_recommender",
        "positive_keyword_groups": {
            "romance_daily": {
                "label": "恋爱日常",
                "weight": 12,
                "keywords": [
                    "恋爱",
                    "恋愛",
                    "ラブコメ",
                    "爱情喜剧",
                    "告白",
                    "美少女",
                    "校园",
                    "校園",
                    "学园",
                    "學園",
                    "同居",
                    "青梅竹马",
                    "青梅竹馬",
                    "青春",
                    "日常",
                ],
            },
            "fantasy_hook": {
                "label": "异世界/特殊身份设定",
                "weight": 10,
                "keywords": [
                    "异世界",
                    "異世界",
                    "乙女游戏",
                    "乙女遊戲",
                    "皇女",
                    "皇帝",
                    "魔法",
                    "圣女",
                    "聖女",
                    "祭司",
                    "转生",
                    "轉生",
                    "恶役",
                    "惡役",
                    "贵族",
                    "貴族",
                ],
            },
            "fresh_hook": {
                "label": "设定新鲜感",
                "weight": 8,
                "keywords": [
                    "路人",
                    "培养",
                    "培養",
                    "偶像",
                    "游戏",
                    "遊戲",
                    "桌游",
                    "桌遊",
                    "模拟",
                    "模擬",
                    "反差",
                    "伪装",
                    "偽裝",
                    "社团",
                    "社團",
                ],
            },
        },
        "negative_keyword_groups": {
            "non_novel_news": {
                "label": "非轻小说资源资讯",
                "weight": -36,
                "keywords": [
                    "对谈",
                    "對談",
                    "访谈",
                    "訪談",
                    "资讯",
                    "資訊",
                    "动画",
                    "動畫",
                    "制片人",
                    "漫画",
                    "漫畫",
                    "模型",
                    "众筹",
                    "轻币",
                    "發輕幣",
                    "发轻币",
                    "酒",
                    "手游",
                    "游戏新闻",
                ],
            }
        },
    }


def _merge_lightnovel_taste_profile(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = _merge_lightnovel_taste_profile(merged[key], value)
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _load_lightnovel_taste_profile() -> dict[str, Any]:
    profile = _default_lightnovel_taste_profile()
    path_text = _parse_env_text("HOST_LIGHTNOVEL_RECOMMENDER_TASTE_PROFILE_PATH")
    env_json = _parse_env_text("HOST_LIGHTNOVEL_RECOMMENDER_TASTE_PROFILE_JSON")
    overrides: list[Mapping[str, Any]] = []
    if path_text:
        data = _read_json_file(Path(path_text))
        if isinstance(data, Mapping):
            overrides.append(data)
    if env_json:
        try:
            data = json.loads(env_json)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, Mapping):
            overrides.append(data)
    for override in overrides:
        profile = _merge_lightnovel_taste_profile(profile, override)
    return profile


def _lightnovel_taste_profile_for_order(order: AgentWorkOrder) -> dict[str, Any]:
    base_profile = _load_lightnovel_taste_profile()
    override = order.metadata.get("lightnovel_taste_profile")
    if isinstance(override, Mapping):
        return _merge_lightnovel_taste_profile(base_profile, override)
    return base_profile


def _parse_lightnovel_count(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    text = str(value or "").strip().lower().replace(",", "")
    if not text:
        return 0.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*([wk万萬千]?)", text)
    if not match:
        return 0.0
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix in {"w", "万", "萬"}:
        number *= 10_000
    elif suffix in {"k", "千"}:
        number *= 1_000
    return max(0.0, number)


def _parse_lightnovel_update_age_days(value: Any, *, now_ts_ms: int | None = None) -> float | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if any(token in text for token in ("刚刚", "剛剛", "今天", "分钟前", "分鐘前", "minute", "min")):
        return 0.0
    if "昨天" in text:
        return 1.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(小时|小時|hour|hours|h)", text)
    if match:
        return float(match.group(1)) / 24.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(天|日|day|days|d)", text)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*(周|週|week|weeks|w)", text)
    if match:
        return float(match.group(1)) * 7.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(个月|個月|月|month|months)", text)
    if match:
        return float(match.group(1)) * 30.0
    match = re.search(r"(\d+(?:\.\d+)?)\s*(年|year|years|y)", text)
    if match:
        return float(match.group(1)) * 365.0
    date_match = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if date_match:
        try:
            date_value = datetime(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
                tzinfo=timezone.utc,
            )
            now_dt = datetime.fromtimestamp((now_ts_ms or now_ms()) / 1000, tz=timezone.utc)
            return max(0.0, (now_dt - date_value).total_seconds() / 86_400)
        except ValueError:
            return None
    return None


def _lightnovel_item_text(item: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "title",
        "title_display",
        "subtitle",
        "tags",
        "genres",
        "category",
        "intro_snippet",
        "intro",
        "description",
        "summary",
        "rationale",
    ):
        value = item.get(key)
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(part) for part in value)
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def _lightnovel_item_key(item: Mapping[str, Any]) -> str:
    for key in ("detail_url", "url", "source_url"):
        value = str(item.get(key) or "").strip().lower().rstrip("/")
        if value:
            return value
    title = str(item.get("title") or item.get("title_display") or "").strip().lower()
    return title


def _lightnovel_detail_identity_key(*values: Any) -> str:
    for value in values:
        candidate = _supported_lightnovel_candidate_detail_id(str(value or "").strip())
        if candidate:
            return f"detail:{candidate}"
    return ""


def _supported_lightnovel_candidate_detail_id(url: str) -> str:
    value = str(url or "").strip()
    patterns = (
        (r"https?://(?:www\.)?lightnovel\.fun/(?:cn/)?detail/(\d+)(?:[/?#].*)?$", "lightnovel.fun"),
        (r"https?://book\.sfacg\.com/Novel/(\d+)/?(?:[?#].*)?$", "sfacg"),
        (r"https?://www\.17k\.com/book/(\d+)\.html(?:[?#].*)?$", "17k"),
        (r"https?://lightnovelsearch\.com/book/([^/?#]+)/?(?:[?#].*)?$", "lightnovelsearch"),
        (r"https?://www\.noveldot\.com/book-(\d+)/[^?#]+/?(?:[?#].*)?$", "noveldot"),
        (r"https?://(?:www\.)?linovelib\.com/(?:novel|book)/(\d+)(?:\.html)?(?:[/?#].*)?$", "linovelib"),
    )
    for pattern, prefix in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return f"{prefix}:{str(match.group(1)).lower()}"
    return ""


def _is_supported_lightnovel_candidate_url(url: str) -> bool:
    return bool(_supported_lightnovel_candidate_detail_id(url))


_LIGHTNOVEL_CHAPTER_NUMERAL_CHARS = "一二三四五六七八九十百千万萬零〇两兩\\d"


def _lightnovel_title_value(item: Mapping[str, Any]) -> str:
    return str(item.get("title") or item.get("title_display") or item.get("subtitle") or "").strip()


def _normalize_lightnovel_series_title(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        text = _normalize_lightnovel_candidate_title(text)
    except NameError:
        text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(?:合集|合集帖)\s+", "", text).strip()
    text = re.sub(r"^\s*(?:\[[^\]]{1,48}\]|【[^】]{1,48}】)\s*", "", text).strip()
    text = re.sub(
        r"\[[^\]]*(?:文库|文庫|日语|日語|录入|錄入|翻译|翻譯|更新|完结|完結|官方|至第|全书)[^\]]*\]",
        "",
        text,
    )
    text = re.sub(
        r"【[^】]*(?:文库|文庫|日语|日語|录入|錄入|翻译|翻譯|更新|完结|完結|官方|至第|全书)[^】]*】",
        "",
        text,
    )
    text = re.sub(r"[（(]\s*\d{1,2}/\d{1,2}[^）)]*[）)]\s*$", "", text).strip()
    text = re.sub(
        rf"\s*第\s*[{_LIGHTNOVEL_CHAPTER_NUMERAL_CHARS}]+\s*(?:话|話|章|节|節)\s*.*$",
        "",
        text,
    ).strip()
    text = re.sub(
        rf"\s*(?:更新至?|至)\s*第\s*[{_LIGHTNOVEL_CHAPTER_NUMERAL_CHARS}]+.*$",
        "",
        text,
    ).strip()
    text = re.sub(r"\s+\d{1,3}\s*$", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip(" -_—~～[]【】（）()")
    return text


def _lightnovel_series_key(item: Mapping[str, Any] | str) -> str:
    title = _normalize_lightnovel_series_title(_lightnovel_title_value(item) if isinstance(item, Mapping) else item)
    if not title:
        return ""
    compact = re.sub(r"[^\da-zA-Z\u3040-\u30ff\u3400-\u9fff]+", "", title).lower()
    return compact[:160]


def _lightnovel_history_aliases_for_record(record: Mapping[str, Any]) -> set[str]:
    aliases: set[str] = set()
    for raw in (record.get("key"), record.get("detail_url"), record.get("url"), record.get("source_url")):
        value = str(raw or "").strip().lower().rstrip("/")
        if value:
            aliases.add(value)
        detail_key = _lightnovel_detail_identity_key(value)
        if detail_key:
            aliases.add(detail_key)
    for alias in record.get("key_aliases") or record.get("history_aliases") or []:
        value = str(alias or "").strip().lower()
        if value:
            aliases.add(value)
    series_key = str(record.get("series_key") or "").strip().lower()
    if not series_key:
        series_key = _lightnovel_series_key(str(record.get("canonical_title") or record.get("title") or ""))
    if series_key:
        aliases.add(f"series:{series_key}")
    return aliases


def _lightnovel_history_lookup_keys(item: Mapping[str, Any], *, include_series: bool = True) -> set[str]:
    keys: set[str] = set()
    item_key = _lightnovel_item_key(item)
    if item_key:
        keys.add(item_key)
    detail_key = _lightnovel_detail_identity_key(item.get("detail_url"), item.get("url"), item.get("source_url"), item_key)
    if detail_key:
        keys.add(detail_key)
    if include_series:
        series_key = _lightnovel_series_key(item)
        if series_key:
            keys.add(f"series:{series_key}")
    return {key.strip().lower() for key in keys if key.strip()}


def _latest_lightnovel_history_record(
    history: Mapping[str, Mapping[str, Any]],
    keys: set[str],
) -> Mapping[str, Any] | None:
    latest: Mapping[str, Any] | None = None
    latest_ts = -1
    for key in keys:
        record = history.get(key)
        if not record:
            continue
        try:
            ts_ms = int(record.get("ts_ms") or 0)
        except (TypeError, ValueError):
            ts_ms = 0
        if latest is None or ts_ms > latest_ts:
            latest = record
            latest_ts = ts_ms
    return latest


def _lightnovel_history_path() -> Path:
    env_path = _parse_env_text("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return Path("artifacts") / "codex_runs" / "lightnovel_recommender" / "recommendation_history.jsonl"


def _load_lightnovel_recommendation_history(
    *,
    path: Path | None = None,
    now_ts_ms: int | None = None,
    window_days: int | None = None,
) -> dict[str, dict[str, Any]]:
    history_path = path or _lightnovel_history_path()
    if not history_path.exists() or not history_path.is_file():
        return {}
    now_value = now_ts_ms or now_ms()
    days = window_days
    if days is None:
        days = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_DAYS", default=45, min_value=1, max_value=365)
    min_ts = now_value - int(days) * 86_400_000
    latest: dict[str, dict[str, Any]] = {}
    try:
        lines = history_path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, Mapping):
            continue
        key = str(record.get("key") or "").strip().lower()
        ts_ms = int(record.get("ts_ms") or 0)
        if not key or ts_ms < min_ts:
            continue
        indexed_record = dict(record)
        for alias in _lightnovel_history_aliases_for_record(indexed_record):
            previous = latest.get(alias)
            if previous is None or ts_ms > int(previous.get("ts_ms") or 0):
                latest[alias] = indexed_record
    return latest


def _record_lightnovel_recommendation_history(
    items: list[dict[str, Any]],
    *,
    path: Path | None = None,
    job_id: str = "",
    recommendations_path: Path | None = None,
    now_ts_ms: int | None = None,
) -> Path | None:
    selected = [item for item in items if _lightnovel_item_key(item) or _lightnovel_series_key(item)]
    if not selected:
        return None
    history_path = path or _lightnovel_history_path()
    ts_ms = now_ts_ms or now_ms()
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as handle:
        for item in selected:
            canonical_title = _normalize_lightnovel_series_title(item.get("title") or item.get("title_display") or "")
            series_key = _lightnovel_series_key(item)
            aliases = sorted(_lightnovel_history_lookup_keys(item, include_series=True))
            record = {
                "ts_ms": ts_ms,
                "job_id": job_id,
                "key": _lightnovel_item_key(item),
                "key_aliases": aliases,
                "canonical_title": canonical_title,
                "series_key": series_key,
                "title": item.get("title") or item.get("title_display") or "",
                "detail_url": item.get("detail_url") or item.get("url") or item.get("source_url") or "",
                "score": item.get("recommendation_score"),
                "rank": item.get("recommendation_rank"),
                "recommendations_ref": str(recommendations_path or ""),
            }
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return history_path


def _keyword_group_matches(text: str, groups: Mapping[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    score = 0.0
    matches: list[dict[str, Any]] = []
    haystack = text.lower()
    for group_id, group in groups.items():
        if not isinstance(group, Mapping):
            continue
        keywords = [str(keyword) for keyword in group.get("keywords") or [] if str(keyword).strip()]
        hits = [keyword for keyword in keywords if keyword.lower() in haystack]
        if not hits:
            continue
        weight = float(group.get("weight") or 0)
        capped_hits = hits[:3]
        group_score = weight * min(len(hits), 3)
        score += group_score
        matches.append(
            {
                "group_id": str(group_id),
                "label": str(group.get("label") or group_id),
                "keywords": capped_hits,
                "score": round(group_score, 2),
            }
        )
    return score, matches


def _freshness_score(age_days: float | None) -> float:
    if age_days is None:
        return 0.0
    if age_days <= 1:
        return 18.0
    if age_days <= 3:
        return 14.0
    if age_days <= 7:
        return 10.0
    if age_days <= 30:
        return 6.0
    if age_days <= 120:
        return 2.0
    return 0.0


def _lightnovel_book_form_score(item: Mapping[str, Any]) -> tuple[float, str]:
    text = _lightnovel_item_text(item)
    score = 0.0
    labels: list[str] = []
    if re.search(r"(文库|文庫|卷|巻|整卷|全书|全書|完结|完結|epub)", text, re.IGNORECASE):
        score += 10.0
        labels.append("book_like")
    chapter_match = re.search(
        rf"第\s*[{_LIGHTNOVEL_CHAPTER_NUMERAL_CHARS}]+\s*(?:话|話|章|节|節)",
        _lightnovel_title_value(item),
    )
    update_to_chapter_match = re.search(
        rf"(?:更新至?|至)\s*第\s*[{_LIGHTNOVEL_CHAPTER_NUMERAL_CHARS}]+\s*(?:话|話|章|节|節)",
        _lightnovel_title_value(item),
    )
    if chapter_match:
        score -= 34.0
        labels.append("chapter_update")
    elif update_to_chapter_match:
        score -= 18.0
        labels.append("serial_update")
    return score, ",".join(labels) or "neutral"


def _lightnovel_content_access_score(item: Mapping[str, Any]) -> tuple[float, str]:
    state = str(item.get("content_access_state") or "UNKNOWN").strip().upper()
    if state == "READABLE":
        return 32.0, "READABLE"
    if state == "SUMMARY_ONLY":
        return -18.0, "SUMMARY_ONLY"
    if state == "BLOCKED":
        return -180.0, "BLOCKED"
    return -10.0, "UNKNOWN"


def _lightnovel_history_penalty_for_age(age_days: float, *, scope: str) -> tuple[float, str]:
    exact_block_days = _parse_int_env(
        "HOST_LIGHTNOVEL_RECOMMENDER_REPEAT_BLOCK_DAYS",
        default=14,
        min_value=1,
        max_value=365,
    )
    series_block_days = _parse_int_env(
        "HOST_LIGHTNOVEL_RECOMMENDER_SERIES_BLOCK_DAYS",
        default=21,
        min_value=1,
        max_value=365,
    )
    if scope == "exact":
        if age_days <= exact_block_days:
            return -220.0, "recent_exact_repeat"
        if age_days <= 45:
            return -90.0, "cooldown_exact_repeat"
        if age_days <= 90:
            return -35.0, "older_exact_repeat"
        return -12.0, "stale_exact_repeat"
    if age_days <= series_block_days:
        return -180.0, "recent_series_repeat"
    if age_days <= 45:
        return -70.0, "cooldown_series_repeat"
    if age_days <= 90:
        return -28.0, "older_series_repeat"
    return -10.0, "stale_series_repeat"


def _history_penalty(item: Mapping[str, Any], history: Mapping[str, Mapping[str, Any]], *, now_ts_ms: int) -> tuple[float, str]:
    exact_keys = _lightnovel_history_lookup_keys(item, include_series=False)
    series_key = _lightnovel_series_key(item)
    if not exact_keys and not series_key:
        return 0.0, "unknown"

    candidates: list[tuple[float, str]] = []
    exact_record = _latest_lightnovel_history_record(history, exact_keys)
    if exact_record:
        try:
            age_days = max(0.0, (now_ts_ms - int(exact_record.get("ts_ms") or 0)) / 86_400_000)
        except (TypeError, ValueError):
            age_days = 0.0
        candidates.append(_lightnovel_history_penalty_for_age(age_days, scope="exact"))

    if series_key:
        series_record = _latest_lightnovel_history_record(history, {f"series:{series_key}"})
        if series_record:
            try:
                age_days = max(0.0, (now_ts_ms - int(series_record.get("ts_ms") or 0)) / 86_400_000)
            except (TypeError, ValueError):
                age_days = 0.0
            candidates.append(_lightnovel_history_penalty_for_age(age_days, scope="series"))

    if not candidates:
        return 0.0, "new"
    return min(candidates, key=lambda row: row[0])


def _lightnovel_genre_bucket(item: Mapping[str, Any], preference_matches: list[dict[str, Any]]) -> str:
    if preference_matches:
        best = max(preference_matches, key=lambda match: float(match.get("score") or 0))
        return str(best.get("group_id") or "matched")
    text = _lightnovel_item_text(item)
    if any(token in text for token in ("异世界", "異世界", "魔法", "转生", "轉生")):
        return "fantasy_hook"
    if any(token in text for token in ("恋爱", "恋愛", "校园", "学园", "學園", "青春")):
        return "romance_daily"
    return "other"


def _score_lightnovel_recommendation_item(
    item: Mapping[str, Any],
    *,
    profile: Mapping[str, Any],
    history: Mapping[str, Mapping[str, Any]],
    source_index: int,
    now_ts_ms: int,
) -> dict[str, Any]:
    text = _lightnovel_item_text(item)
    positive_score, positive_matches = _keyword_group_matches(
        text,
        dict(profile.get("positive_keyword_groups") or {}),
    )
    negative_score, negative_matches = _keyword_group_matches(
        text,
        dict(profile.get("negative_keyword_groups") or {}),
    )
    view_score = min(18.0, _parse_lightnovel_count(item.get("view_count")) ** 0.32)
    comment_score = min(8.0, _parse_lightnovel_count(item.get("comment_count")) ** 0.42)
    age_days = _parse_lightnovel_update_age_days(item.get("update_time"), now_ts_ms=now_ts_ms)
    freshness = _freshness_score(age_days)
    book_form_score, book_form_state = _lightnovel_book_form_score(item)
    access_score, access_state = _lightnovel_content_access_score(item)
    metadata_score = 0.0
    if item.get("cover_url") or item.get("cover") or item.get("image_url"):
        metadata_score += 4.0
    if item.get("detail_url") or item.get("url") or item.get("source_url"):
        metadata_score += 3.0
    if item.get("intro_snippet") or item.get("intro") or item.get("description") or item.get("summary"):
        metadata_score += 4.0
    if item.get("rationale"):
        metadata_score += 2.0
    source_order_score = max(0.0, 14.0 - float(source_index) * 1.25)
    history_score, dedupe_state = _history_penalty(item, history, now_ts_ms=now_ts_ms)
    total = (
        source_order_score
        + positive_score
        + negative_score
        + view_score
        + comment_score
        + freshness
        + metadata_score
        + book_form_score
        + access_score
        + history_score
    )
    ranked = dict(item)
    preference_matches = positive_matches + negative_matches
    ranked["recommendation_score"] = round(total, 2)
    ranked["canonical_title"] = _normalize_lightnovel_series_title(ranked.get("title") or ranked.get("title_display") or "")
    ranked["series_key"] = _lightnovel_series_key(ranked)
    ranked["source_key"] = _lightnovel_source_key(ranked)
    ranked["score_breakdown"] = {
        "source_order": round(source_order_score, 2),
        "preference": round(positive_score, 2),
        "negative_preference": round(negative_score, 2),
        "freshness": round(freshness, 2),
        "heat": round(view_score + comment_score, 2),
        "metadata": round(metadata_score, 2),
        "book_form": round(book_form_score, 2),
        "content_access": round(access_score, 2),
        "history": round(history_score, 2),
    }
    ranked["preference_matches"] = preference_matches
    ranked["dedupe_state"] = dedupe_state
    ranked["book_form_state"] = book_form_state
    ranked["content_access_state"] = str(ranked.get("content_access_state") or access_state).strip().upper() or access_state
    ranked["diversity_bucket"] = _lightnovel_genre_bucket(ranked, positive_matches)
    matched_labels = [str(match.get("label") or "") for match in positive_matches if match.get("label")]
    if matched_labels:
        ranked["selection_reason"] = f"命中偏好：{'、'.join(matched_labels[:2])}"
    elif freshness > 0 or view_score + comment_score > 0:
        ranked["selection_reason"] = "公开信号较好：更新、热度或资料完整度较高"
    else:
        ranked["selection_reason"] = "作为候选保留：等待更多口味反馈校准"
    return ranked


def _lightnovel_primary_candidate_key(item: Mapping[str, Any]) -> str:
    series_key = _lightnovel_series_key(item)
    if series_key:
        return f"series:{series_key}"
    detail_key = _lightnovel_detail_identity_key(item.get("detail_url"), item.get("url"), item.get("source_url"))
    if detail_key:
        return detail_key
    return _lightnovel_item_key(item)


def _lightnovel_source_key(item: Mapping[str, Any]) -> str:
    for key in ("source_url", "detail_url", "url"):
        raw = str(item.get(key) or "").strip()
        if not raw:
            continue
        try:
            host = urlparse(raw).netloc.lower()
        except Exception:
            host = ""
        if not host:
            continue
        if host.endswith("lightnovel.fun"):
            return "lightnovel.fun"
        if host == "book.sfacg.com":
            return "sfacg.com"
        if host == "www.17k.com":
            return "17k.com"
        if host == "lightnovelsearch.com":
            return "lightnovelsearch.com"
        if host == "www.noveldot.com":
            return "noveldot.com"
        if host.endswith("linovelib.com"):
            return "linovelib.com"
        return host.removeprefix("www.")
    return "unknown"


def _lightnovel_history_state_blocks_visible(item: Mapping[str, Any]) -> bool:
    state = str(item.get("dedupe_state") or "").strip().lower()
    return state in {
        "recent_exact_repeat",
        "recent_series_repeat",
        "cooldown_exact_repeat",
        "cooldown_series_repeat",
    }


def _apply_lightnovel_visible_source_diversity(preferred: list[dict[str, Any]], *, max_items: int) -> list[dict[str, Any]]:
    limit = max(1, int(max_items))
    if limit <= 1 or len(preferred) <= 1:
        return preferred
    min_sources = _parse_int_env(
        "HOST_LIGHTNOVEL_RECOMMENDER_MIN_VISIBLE_SOURCES",
        default=2,
        min_value=1,
        max_value=limit,
    )
    visible = list(preferred[:limit])
    rest = list(preferred[limit:])
    available_sources = {_lightnovel_source_key(item) for item in preferred if _lightnovel_source_key(item) != "unknown"}
    if len(available_sources) < min_sources:
        return preferred
    visible_sources = {_lightnovel_source_key(item) for item in visible if _lightnovel_source_key(item) != "unknown"}
    if len(visible_sources) >= min_sources:
        return preferred

    min_score = float(
        _parse_int_env(
            "HOST_LIGHTNOVEL_RECOMMENDER_SOURCE_DIVERSITY_MIN_SCORE",
            default=8,
            min_value=-200,
            max_value=200,
        )
    )
    for index, candidate in enumerate(rest):
        source = _lightnovel_source_key(candidate)
        if source == "unknown" or source in visible_sources:
            continue
        adjusted_score = float(candidate.get("recommendation_adjusted_score") or candidate.get("recommendation_score") or 0)
        if adjusted_score < min_score:
            continue
        replace_index = min(
            range(len(visible)),
            key=lambda idx: float(visible[idx].get("recommendation_adjusted_score") or visible[idx].get("recommendation_score") or 0),
        )
        replaced = visible[replace_index]
        visible[replace_index] = candidate
        rest[index] = replaced
        return sorted(visible, key=lambda item: float(item.get("recommendation_adjusted_score") or item.get("recommendation_score") or 0), reverse=True) + rest
    return preferred


def _rank_lightnovel_recommendations(
    items: list[dict[str, Any]],
    *,
    max_items: int = 3,
    now_ts_ms: int | None = None,
    profile: Mapping[str, Any] | None = None,
    history: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not items:
        return []
    now_value = now_ts_ms or now_ms()
    taste_profile = dict(profile or _load_lightnovel_taste_profile())
    history_records = dict(history or _load_lightnovel_recommendation_history(now_ts_ms=now_value))
    scored_by_key: dict[str, dict[str, Any]] = {}
    for source_index, item in enumerate(items):
        scored_item = _score_lightnovel_recommendation_item(
            item,
            profile=taste_profile,
            history=history_records,
            source_index=source_index,
            now_ts_ms=now_value,
        )
        primary_key = _lightnovel_primary_candidate_key(scored_item) or f"row:{source_index}"
        previous = scored_by_key.get(primary_key)
        if previous is None or float(scored_item.get("recommendation_score") or 0) > float(previous.get("recommendation_score") or 0):
            scored_by_key[primary_key] = scored_item
    scored = list(scored_by_key.values())
    selected: list[dict[str, Any]] = []
    remaining = sorted(scored, key=lambda item: float(item.get("recommendation_score") or 0), reverse=True)
    bucket_counts: dict[str, int] = {}
    while remaining:
        best_index = 0
        best_adjusted = float("-inf")
        for index, item in enumerate(remaining):
            bucket = str(item.get("diversity_bucket") or "other")
            diversity_penalty = min(18.0, float(bucket_counts.get(bucket, 0)) * 8.0)
            adjusted_score = float(item.get("recommendation_score") or 0) - diversity_penalty
            if adjusted_score > best_adjusted:
                best_adjusted = adjusted_score
                best_index = index
        item = remaining.pop(best_index)
        bucket = str(item.get("diversity_bucket") or "other")
        diversity_penalty = min(18.0, float(bucket_counts.get(bucket, 0)) * 8.0)
        item["diversity_penalty"] = round(diversity_penalty, 2)
        item["recommendation_adjusted_score"] = round(float(item.get("recommendation_score") or 0) - diversity_penalty, 2)
        selected.append(item)
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    preferred = [item for item in selected if not _lightnovel_history_state_blocks_visible(item)]
    repeat_fallback = False
    if not preferred:
        preferred = selected[: max(1, int(max_items))]
        repeat_fallback = True
    if not repeat_fallback:
        preferred = _apply_lightnovel_visible_source_diversity(preferred, max_items=max_items)
    deferred = [item for item in selected if id(item) not in {id(row) for row in preferred}]
    ordered = preferred + deferred
    visible_ids = {id(row) for row in preferred[: max(1, int(max_items))]}
    for rank, item in enumerate(ordered, start=1):
        item["recommendation_rank"] = rank
        item["ranking_profile_version"] = str(taste_profile.get("version") or "custom")
        item["ranking_visible_candidate"] = id(item) in visible_ids
        if repeat_fallback and item["ranking_visible_candidate"]:
            item["repeat_fallback_visible"] = True
            item["selection_reason"] = "新候选不足，作为近期重复备选临时保留"
    return ordered


def _lightnovel_gateway_cover_url(item: Mapping[str, Any]) -> str:
    cover_url = str(item.get("cover_url") or item.get("cover") or item.get("image_url") or "").strip()
    if cover_url and _looks_like_content_image(cover_url):
        return cover_url
    return ""


def _visible_lightnovel_ranked_items(items: list[dict[str, Any]], *, max_items: int) -> list[dict[str, Any]]:
    limit = max(1, int(max_items))
    visible = [item for item in items if item.get("ranking_visible_candidate") is True]
    pool: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in [*visible, *items]:
        marker = id(item)
        if marker in seen:
            continue
        seen.add(marker)
        pool.append(item)
    if not pool:
        return []

    if _parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_PREFER_COVERS", default=True):
        pool = sorted(
            pool,
            key=lambda item: (
                1 if _lightnovel_gateway_cover_url(item) else 0,
                1 if item.get("ranking_visible_candidate") is True else 0,
                float(item.get("recommendation_adjusted_score") or item.get("recommendation_score") or 0),
            ),
            reverse=True,
        )
    return pool[:limit]



def _normalize_lightnovel_candidate_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    text = re.sub(r"^\s*\d+\s+", "", text).strip()
    # Strip leading bracket-wrapped prefixes: [作者], (状态), {标签}, 【标签】
    while True:
        stripped = text
        stripped = re.sub(r'^\s*\[[^\]]{1,80}\]\s*', '', stripped)
        stripped = re.sub(r'^\s*【[^】]{1,80}】\s*', '', stripped)
        stripped = re.sub(r'^\s*\{[^}]{1,80}\}\s*', '', stripped)
        stripped = re.sub(r'^\s*\([^)]{1,80}\)\s*', '', stripped)
        if stripped == text:
            break
        text = stripped
    # Strip trailing bracket-wrapped suffixes repeatedly (handle chain like [本体+特典](施工完毕))
    while True:
        stripped = text
        stripped = re.sub(r'\s*\[[^\]]{1,40}\]\s*$', '', stripped)
        stripped = re.sub(r'\s*【[^】]{1,40}】\s*$', '', stripped)
        stripped = re.sub(r'\s*\([^)]{1,40}\)\s*$', '', stripped)
        if stripped == text:
            break
        text = stripped
    text = re.sub(r"^Pick\s+", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"\s+Recommended\s*$", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^(?:合集|合集帖)\s+", "", text).strip()
    text = re.sub(r"\s+\d+(?:\.\d+)?\s*(?:分钟|小時|小时|天|日|个月|月|年)前\s*$", "", text).strip()
    return text

def _looks_like_content_image(url: str) -> bool:
    lower = str(url or "").strip().lower()
    if not lower.startswith(("http://", "https://")):
        return False
    if "res.lightnovel.fun/images/" in lower:
        return any(token in lower for token in (".jpg", ".jpeg", ".png", ".webp"))
    if "i.noire.cc/image/" in lower:
        return True
    return False


__all__ = ["rank_lightnovel_recommendations"]
rank_lightnovel_recommendations = _rank_lightnovel_recommendations
