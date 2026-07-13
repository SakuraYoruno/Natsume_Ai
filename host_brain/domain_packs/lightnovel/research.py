"""Lightnovel source discovery, detail enrichment and report materialization."""

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

from host_brain.domain_packs.production_jobs import _parse_bool_env, _parse_int_env, _read_json_file, _short_text
from host_brain.domain_packs.lightnovel.ranking import (
    _coerce_recommendation_items,
    _is_supported_lightnovel_candidate_url,
    _LIGHTNOVEL_CHAPTER_NUMERAL_CHARS,
    _lightnovel_taste_profile_for_order,
    _looks_like_content_image,
    _normalize_lightnovel_candidate_title,
    _rank_lightnovel_recommendations,
    _visible_lightnovel_ranked_items,
)

def _write_lightnovel_ranked_recommendations(
    *,
    recommendations_path: Path,
    ranked_items: list[dict[str, Any]],
    max_items: int,
    now_ts_ms: int | None = None,
) -> Path | None:
    if not ranked_items:
        return None
    ranked_path = recommendations_path.with_name("ranked_recommendations.json")
    payload = {
        "generated_ts_ms": now_ts_ms or now_ms(),
        "source_recommendations_ref": str(recommendations_path),
        "ranking_profile_version": str(ranked_items[0].get("ranking_profile_version") or ""),
        "visible_count": len(_visible_lightnovel_ranked_items(ranked_items, max_items=max_items)),
        "items": ranked_items,
    }
    try:
        ranked_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return None
    return ranked_path


def _candidate_lightnovel_recommendation_paths(*, result: Mapping[str, Any], run_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        workspace = str(metadata.get("workspace") or "").strip()
        if workspace:
            candidates.append(Path(workspace) / "recommendations.json")
    artifacts_dir = str(result.get("artifacts_dir") or "").strip()
    if artifacts_dir:
        candidates.append(Path(artifacts_dir) / "recommendations.json")
        candidates.append(Path(artifacts_dir) / "workspace" / "recommendations.json")
    candidates.extend(
        [
            run_dir / "recommendations.json",
            run_dir / "workspace" / "recommendations.json",
        ]
    )

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = str(path.expanduser())
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path.expanduser())
    return unique


def _candidate_lightnovel_workspace_paths(
    *,
    order: AgentWorkOrder,
    result: Mapping[str, Any],
    run_dir: Path,
) -> list[Path]:
    candidates: list[Path] = []
    metadata = result.get("metadata")
    if isinstance(metadata, Mapping):
        workspace = str(metadata.get("workspace") or "").strip()
        if workspace:
            candidates.append(Path(workspace))
    artifacts_dir = str(result.get("artifacts_dir") or "").strip()
    if artifacts_dir:
        artifacts_path = Path(artifacts_dir)
        candidates.append(artifacts_path)
        candidates.append(artifacts_path / "workspace")
    candidates.extend(
        [
            run_dir,
            run_dir / "workspace",
            run_dir.parent.parent / order.work_order_id / "workspace",
        ]
    )

    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = str(path.expanduser())
        if not resolved or resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path.expanduser())
    return unique


def _find_lightnovel_recommendations_file(*, result: Mapping[str, Any], run_dir: Path) -> Path | None:
    for path in _candidate_lightnovel_recommendation_paths(result=result, run_dir=run_dir):
        if path.exists() and path.is_file():
            return path
    return None


def _materialize_lightnovel_recommendations_from_result(
    *,
    order: AgentWorkOrder,
    result: Mapping[str, Any],
    run_dir: Path,
) -> Path | None:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id != "agent_lightnovel_recommender":
        return None

    existing = _find_lightnovel_recommendations_file(result=result, run_dir=run_dir)
    if existing is not None:
        return existing

    metadata = dict(result.get("metadata") or {}) if isinstance(result.get("metadata"), Mapping) else {}
    candidate_items = _collect_lightnovel_candidate_items(metadata)
    if not candidate_items:
        return None

    recommendations = _lightnovel_recommendations_from_browser_items(candidate_items)
    if not recommendations:
        return None

    max_items = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_MAX_ITEMS", default=3, min_value=1, max_value=8)
    detail_max_items = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_DETAIL_ENRICH_MAX", default=24, min_value=0, max_value=40)
    detail_timeout_s = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_DETAIL_TIMEOUT_S", default=8, min_value=3, max_value=30)
    taste_profile = _lightnovel_taste_profile_for_order(order)
    pre_ranked_items = _rank_lightnovel_recommendations(
        recommendations,
        max_items=max_items,
        profile=taste_profile,
    )
    enriched_items = _enrich_lightnovel_recommendations_from_details(
        pre_ranked_items,
        max_items=max(max_items, detail_max_items),
        timeout_s=detail_timeout_s,
    )
    ranked_items = _rank_lightnovel_recommendations(
        enriched_items,
        max_items=max_items,
        profile=taste_profile,
    )
    if not ranked_items:
        return None

    run_dir.mkdir(parents=True, exist_ok=True)
    recommendations_path = run_dir / "recommendations.json"
    cover_manifest_path = run_dir / "cover_manifest.json"
    report_path = run_dir / "recommendation_report.md"
    result_contract_path = run_dir / "result_contract.json"
    recommendations_path.write_text(
        json.dumps(
            {
                "schema_version": "Natsume.lightnovel_recommendations.v1",
                "generated_ts_ms": now_ms(),
                "source": "recovered_browser_research_candidates",
                "recommendations": ranked_items,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    cover_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "Natsume.lightnovel_cover_manifest.v1",
                "covers": [
                    {
                        "title": item.get("title") or item.get("title_display") or "",
                        "detail_url": item.get("detail_url") or item.get("url") or "",
                        "cover_url": item.get("cover_url") or "",
                    }
                    for item in ranked_items
                    if str(item.get("cover_url") or "").strip()
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_native_lightnovel_report(
        report_path=report_path,
        ranked_items=ranked_items,
        source_count=int(metadata.get("source_count") or 0),
        readable_source_count=int(metadata.get("readable_source_count") or 0),
    )
    result_contract_path.write_text(
        json.dumps(
            {
                "workorder_id": order.work_order_id,
                "status": "complete",
                "recommendation_count": len(ranked_items),
                "recommendations_ref": str(recommendations_path),
                "sources_ref": str(run_dir / "sources.jsonl"),
                "report_ref": str(report_path),
                "reason_code": None,
                "recovered_from_interrupted_browser_research": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return recommendations_path


def _recover_lightnovel_recommender_result(
    *,
    order: AgentWorkOrder,
    result: Mapping[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    recovered = dict(result)
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id != "agent_lightnovel_recommender":
        return recovered
    if str(recovered.get("status") or "").upper() == "EXECUTED":
        existing_recommendations_path = _find_lightnovel_recommendations_file(result=recovered, run_dir=run_dir)
        if existing_recommendations_path is not None:
            existing_items = _coerce_recommendation_items(_read_json_file(existing_recommendations_path))
            if existing_items:
                return recovered
            metadata = dict(recovered.get("metadata") or {}) if isinstance(recovered.get("metadata"), Mapping) else {}
            metadata.update(
                {
                    "workspace": str(run_dir),
                    "recommendations_ref": str(existing_recommendations_path),
                    "result_contract_ref": str(run_dir / "result_contract.json"),
                    "report_path": str(run_dir / "recommendation_report.md"),
                    "empty_recommendations_blocked": True,
                }
            )
            risk_flags = [
                str(flag)
                for flag in recovered.get("risk_flags", [])
                if str(flag).strip()
            ]
            if "lightnovel_browser_no_recommendations" not in risk_flags:
                risk_flags.append("lightnovel_browser_no_recommendations")
            recovered.update(
                {
                    "status": "BLOCKED",
                    "summary": "Lightnovel recommender produced no usable recommendation candidates.",
                    "reason_code": "LIGHTNOVEL_PLAYWRIGHT_NO_RECOMMENDATIONS",
                    "detail": "recommendations.json exists but contains no sendable items; empty recommendations must not be treated as executed.",
                    "risk_flags": risk_flags,
                    "metadata": metadata,
                }
            )
            return recovered
        materialized_path = _materialize_lightnovel_recommendations_from_result(
            order=order,
            result=recovered,
            run_dir=run_dir,
        )
        if materialized_path is not None:
            materialized_items = _coerce_recommendation_items(_read_json_file(materialized_path))
            if not materialized_items:
                metadata = dict(recovered.get("metadata") or {}) if isinstance(recovered.get("metadata"), Mapping) else {}
                metadata.update(
                    {
                        "workspace": str(run_dir),
                        "recommendations_ref": str(materialized_path),
                        "result_contract_ref": str(run_dir / "result_contract.json"),
                        "report_path": str(run_dir / "recommendation_report.md"),
                        "empty_recovery_blocked": True,
                        "recovery_reason_code": "LIGHTNOVEL_BROWSER_CANDIDATES_EMPTY",
                    }
                )
                risk_flags = [
                    str(flag)
                    for flag in recovered.get("risk_flags", [])
                    if str(flag).strip()
                ]
                if "lightnovel_browser_no_recommendations" not in risk_flags:
                    risk_flags.append("lightnovel_browser_no_recommendations")
                recovered.update(
                    {
                        "status": "BLOCKED",
                        "summary": "Lightnovel interrupted-job recovery found no usable recommendation candidates.",
                        "reason_code": "LIGHTNOVEL_PLAYWRIGHT_NO_RECOMMENDATIONS",
                        "detail": "Interrupted browser research recovery materialized an empty recommendations payload; empty recovery is blocked.",
                        "risk_flags": risk_flags,
                        "metadata": metadata,
                    }
                )
                return recovered
            metadata = dict(recovered.get("metadata") or {}) if isinstance(recovered.get("metadata"), Mapping) else {}
            metadata.update(
                {
                    "workspace": str(run_dir),
                    "recommendations_ref": str(materialized_path),
                    "result_contract_ref": str(run_dir / "result_contract.json"),
                    "report_path": str(run_dir / "recommendation_report.md"),
                    "recovered_from_interrupted_browser_research": True,
                    "recovery_reason_code": "LIGHTNOVEL_BROWSER_CANDIDATES_MATERIALIZED",
                }
            )
            recovered.update(
                {
                    "summary": "Recovered lightnovel recommendations from interrupted browser research candidates.",
                    "detail": "Host restarted after browser research and before recommendation delivery; recommendations were materialized from saved candidates.",
                    "reason_code": None,
                    "metadata": metadata,
                }
            )
            return recovered
        return recovered

    for workspace in _candidate_lightnovel_workspace_paths(order=order, result=recovered, run_dir=run_dir):
        recommendations_path = workspace / "recommendations.json"
        contract_path = workspace / "result_contract.json"
        report_path = workspace / "recommendation_report.md"
        if not recommendations_path.exists() or not recommendations_path.is_file():
            continue
        if not contract_path.exists() or not contract_path.is_file():
            continue
        contract = _read_json_file(contract_path)
        if not isinstance(contract, Mapping):
            continue
        contract_status = str(contract.get("status") or "").strip().lower()
        if contract_status not in {"complete", "completed", "executed", "ok", "success"}:
            continue
        items = _coerce_recommendation_items(_read_json_file(recommendations_path))
        if not items:
            continue

        original_status = str(recovered.get("status") or "").upper()
        original_reason_code = recovered.get("reason_code")
        original_detail = recovered.get("detail")
        metadata = dict(recovered.get("metadata") or {})
        metadata.update(
            {
                "workspace": str(workspace),
                "recommendations_ref": str(recommendations_path),
                "result_contract_ref": str(contract_path),
                "report_path": str(report_path) if report_path.exists() else "",
                "recovered_from_failed_agent": True,
                "recovery_reason_code": "LIGHTNOVEL_ARTIFACTS_COMPLETE",
                "original_worker_status": original_status,
                "original_worker_reason_code": original_reason_code,
                "original_worker_detail": original_detail,
            }
        )
        risk_flags = [
            str(flag)
            for flag in recovered.get("risk_flags", [])
            if str(flag).strip() and str(flag).strip() != "codex_exec_failed"
        ]
        risk_flags.append("agent_exec_recovered")
        recovered.update(
            {
                "status": "EXECUTED",
                "summary": "Recovered completed lightnovel recommendations from workspace artifacts.",
                "result_text": recovered.get("result_text")
                or "Lightnovel recommendations completed and recovered from workspace artifacts.",
                "reason_code": None,
                "detail": "Agent wrapper failed after artifacts were written; completed recommendation artifacts were recovered.",
                "risk_flags": risk_flags,
                "metadata": metadata,
            }
        )
        return recovered

    materialized_path = _materialize_lightnovel_recommendations_from_result(
        order=order,
        result=recovered,
        run_dir=run_dir,
    )
    if materialized_path is not None:
        materialized_items = _coerce_recommendation_items(_read_json_file(materialized_path))
        if not materialized_items:
            metadata = dict(recovered.get("metadata") or {}) if isinstance(recovered.get("metadata"), Mapping) else {}
            metadata.update(
                {
                    "workspace": str(run_dir),
                    "recommendations_ref": str(materialized_path),
                    "result_contract_ref": str(run_dir / "result_contract.json"),
                    "report_path": str(run_dir / "recommendation_report.md"),
                    "empty_recovery_blocked": True,
                    "recovery_reason_code": "LIGHTNOVEL_BROWSER_CANDIDATES_EMPTY",
                }
            )
            risk_flags = [
                str(flag)
                for flag in recovered.get("risk_flags", [])
                if str(flag).strip() and str(flag).strip() != "codex_exec_failed"
            ]
            if "lightnovel_browser_no_recommendations" not in risk_flags:
                risk_flags.append("lightnovel_browser_no_recommendations")
            recovered.update(
                {
                    "status": "BLOCKED",
                    "summary": "Lightnovel interrupted-job recovery found no usable recommendation candidates.",
                    "result_text": recovered.get("result_text") or "Lightnovel recommender found no usable recommendation candidates.",
                    "reason_code": "LIGHTNOVEL_PLAYWRIGHT_NO_RECOMMENDATIONS",
                    "detail": "Interrupted browser research recovery materialized an empty recommendations payload; empty recovery is blocked.",
                    "risk_flags": risk_flags,
                    "metadata": metadata,
                }
            )
            return recovered
        metadata = dict(recovered.get("metadata") or {}) if isinstance(recovered.get("metadata"), Mapping) else {}
        metadata.update(
            {
                "workspace": str(run_dir),
                "recommendations_ref": str(materialized_path),
                "result_contract_ref": str(run_dir / "result_contract.json"),
                "report_path": str(run_dir / "recommendation_report.md"),
                "recovered_from_interrupted_browser_research": True,
                "recovery_reason_code": "LIGHTNOVEL_BROWSER_CANDIDATES_MATERIALIZED",
            }
        )
        risk_flags = [
            str(flag)
            for flag in recovered.get("risk_flags", [])
            if str(flag).strip() and str(flag).strip() != "codex_exec_failed"
        ]
        risk_flags.append("lightnovel_interrupted_job_recovered")
        recovered.update(
            {
                "status": "EXECUTED",
                "summary": "Recovered lightnovel recommendations from interrupted browser research candidates.",
                "result_text": recovered.get("result_text") or "Lightnovel recommendations recovered from saved browser candidates.",
                "reason_code": None,
                "detail": "Host restarted after browser research and before recommendation delivery; recommendations were materialized from saved candidates.",
                "risk_flags": risk_flags,
                "metadata": metadata,
            }
        )
        return recovered

    return recovered


def _extract_lightnovel_update_time(*values: Any) -> str:
    text = " ".join(str(value or "") for value in values if str(value or "").strip())
    match = re.search(r"(\d+(?:\.\d+)?\s*(?:分钟|小時|小时|天|日|个月|月|年)前)", text)
    if match:
        return match.group(1).strip()
    date_match = re.search(r"(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}/\d{1,2})", text)
    return date_match.group(1).strip() if date_match else ""




_LIGHTNOVEL_LIST_ITEM_MARKER_RE = re.compile(r"(?:^|\s)(?:[1-9]|1[0-9]|20)\s+(?=[\[【《「『A-Za-z\u4e00-\u9fff])")


def _normalize_lightnovel_candidate_context(*, title: str, context: Any) -> str:
    text = re.sub(r"\s+", " ", str(context or "").strip())
    if not text or text == title:
        return ""
    if len(_LIGHTNOVEL_LIST_ITEM_MARKER_RE.findall(text)) >= 3:
        return ""
    if len(text) > max(len(title) + 160, 320):
        return ""
    return _short_text(text, limit=260)


def _is_lightnovel_detail_page_url(url: str) -> bool:
    return _is_supported_lightnovel_candidate_url(url)


def _looks_like_lightnovel_chapter_only_title(title: str) -> bool:
    normalized = _normalize_lightnovel_candidate_title(title)
    stripped = re.sub(r"^(?:合集|合集帖)\s+", "", normalized).strip()
    stripped = re.sub(r"^\s*(?:\[[^\]]{1,48}\]|【[^】]{1,48}】)\s*", "", stripped).strip()
    if re.fullmatch(rf"第[{_LIGHTNOVEL_CHAPTER_NUMERAL_CHARS}]+(?:卷|巻)", stripped):
        return True
    if re.fullmatch(rf"第[{_LIGHTNOVEL_CHAPTER_NUMERAL_CHARS}]+(?:话|話|章|节|節)(?:[:：].*)?", stripped):
        return True
    if re.fullmatch(r"\d{3,5}[.．、].{1,80}", stripped):
        return True
    return False


def _is_lightnovel_recommendation_candidate(*, title: str, url: str, context: str) -> bool:
    if not _is_lightnovel_detail_page_url(url):
        return False
    if not title:
        return False
    if _looks_like_lightnovel_chapter_only_title(title):
        return False
    compact = f"{title} {context}"
    if title.startswith(("置顶", "置頂")):
        return False
    blocked_tokens = (
        # Forum/meta content
        "版规", "版規", "勋章", "勳章", "工资申请", "工資申請",
        "禁止机翻", "禁止機翻", "每日进度更新", "每日進度更新",
        "新项目的传闻", "新項目的傳聞", "资讯区发轻币", "資訊區發輕幣",
        "发轻币", "發輕幣",
        # News/interviews
        "访谈", "訪談", "对谈", "對談", "制片人", "制作管理",
        "動畫化", "动画化", "電視動畫", "电视动画",
        # Merch/physical
        "模型", "众筹", "眾籌", "烧酒", "燒酒", "主题酒", "主題酒",
        # Music/audio (not novels)
        "FLAC", "Hi-Res", "kHz", "mora自购", "自购", "无损",
        "テーマソング", "コレクション", "ストライクウィッチーズ",
        "24bit", "96.0kHz",
        # Games/apps (not novels)
        "安卓版", "SLG", "RPG", "ARPG", "汉化/动态", "新汉化",
        "步兵", "乱伦", "后宫SLG", "养成互动", "完整版",
        # Video/MP4/animation (not novels)
        "MP4", "720P", "1080P", "字幕组",
    )
    if any(token in compact for token in blocked_tokens):
        return False
    return True


def _collect_lightnovel_candidate_items(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def _add(row: Any, *, source_ref: str = "", source_title: str = "") -> None:
        if not isinstance(row, Mapping):
            return
        item = dict(row)
        if source_ref and not item.get("source_ref"):
            item["source_ref"] = source_ref
        if source_title and not item.get("source_title"):
            item["source_title"] = source_title
        rows.append(item)

    for item in list(metadata.get("candidate_items") or []):
        _add(item)
    for source in list(metadata.get("source_summaries") or []):
        if not isinstance(source, Mapping):
            continue
        source_ref = str(source.get("source_ref") or "").strip()
        source_title = str(source.get("title") or "").strip()
        for item in list(source.get("items") or []):
            _add(item, source_ref=source_ref, source_title=source_title)
    sources_path = str(metadata.get("sources_path") or "").strip()
    if sources_path:
        try:
            for line in Path(sources_path).read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                source = json.loads(line)
                if not isinstance(source, Mapping):
                    continue
                source_ref = str(source.get("source_ref") or "").strip()
                source_title = str(source.get("title") or "").strip()
                for item in list(source.get("items") or []):
                    _add(item, source_ref=source_ref, source_title=source_title)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    return rows


def _lightnovel_recommendations_from_browser_items(items: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        title = _normalize_lightnovel_candidate_title(item.get("title"))
        url = str(item.get("url") or item.get("detail_url") or item.get("source_url") or "").strip()
        if not title or not url:
            continue
        if not _is_lightnovel_recommendation_candidate(title=title, url=url, context=str(item.get("context") or "")):
            continue
        key = url.lower().rstrip("/") or title.lower()
        if key in seen:
            continue
        seen.add(key)
        context = _normalize_lightnovel_candidate_context(
            title=title,
            context=item.get("context"),
        )
        source_ref = str(item.get("source_ref") or "").strip()
        source_title = str(item.get("source_title") or "").strip()
        cover_url = str(item.get("cover_url") or item.get("cover") or item.get("image_url") or "").strip()
        # Use detail-page enriched intro if available, otherwise list-page context
        enriched_intro = str(item.get("intro_snippet") or "").strip()
        intro_text = enriched_intro if enriched_intro else context
        recommendations.append(
            {
                "title": title,
                "detail_url": url,
                "source_url": source_ref or url,
                "source_title": source_title,
                "cover_url": cover_url,
                "intro_snippet": intro_text,
                "view_count": str(item.get("view_count") or ""),
                "comment_count": str(item.get("comment_count") or ""),
                "update_time": _extract_lightnovel_update_time(item.get("title"), intro_text),
                "rationale": "公开列表页候选；按偏好、热度/新鲜度、去重历史再排序。",
                "selection_reason": "公开列表页候选；按偏好、热度/新鲜度、去重历史再排序。",
            }
        )
    return recommendations


def _node_attr_int(node: Any, name: str) -> int:
    try:
        return int(float(str(node.get(name) or "").strip()))
    except (TypeError, ValueError):
        return 0


def _candidate_img_src(node: Any, *, base_url: str) -> str:
    for attr in ("src", "data-src", "data-original", "data-lazy-src"):
        raw = str(node.get(attr) or "").strip()
        if raw and not raw.startswith("data:"):
            return urljoin(base_url, raw)
    return ""




def _lightnovel_detail_numeric_id(url: str) -> str:
    match = re.search(r"/detail/(\d+)(?:[/?#].*)?$", str(url or "").strip())
    return match.group(1) if match else ""


def _looks_like_lightnovel_detail_cover(url: str, *, detail_url: str) -> bool:
    return _looks_like_content_image(url)


def _download_lightnovel_gateway_cover(cover_url: str, *, run_dir: Path, index: int) -> Path | None:
    if not _looks_like_content_image(cover_url):
        return None
    try:
        import requests  # noqa: PLC0415
    except Exception:
        return None
    try:
        response = requests.get(
            cover_url,
            headers={
                "User-Agent": "Natsume-Ai-LightnovelRecommender/1.0",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": "https://www.lightnovel.fun/",
            },
            timeout=20,
        )
    except Exception:
        return None
    if response.status_code >= 400:
        return None
    content = bytes(response.content or b"")
    if len(content) < 128 or len(content) > 15 * 1024 * 1024:
        return None
    content_type = str(response.headers.get("content-type") or "").lower()
    lower_url = cover_url.lower()
    if "png" in content_type or ".png" in lower_url:
        suffix = ".png"
    elif "webp" in content_type or ".webp" in lower_url:
        suffix = ".webp"
    elif "jpeg" in content_type or "jpg" in content_type or ".jpg" in lower_url or ".jpeg" in lower_url:
        suffix = ".jpg"
    else:
        suffix = ".jpg"
    cache_dir = run_dir / "gateway_cover_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"lightnovel_cover_{max(1, int(index))}{suffix}"
    try:
        path.write_bytes(content)
    except OSError:
        return None
    return path


def _select_lightnovel_cover_url(soup: Any, *, base_url: str) -> str:
    for selector in (
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ):
        node = soup.select_one(selector)
        raw = str(node.get("content") or "").strip() if node is not None else ""
        if raw:
            candidate = urljoin(base_url, raw)
            if _looks_like_lightnovel_detail_cover(candidate, detail_url=base_url):
                return candidate

    image_rows: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    selectors = (
        ".article-infos img",
        ".article-info img",
        ".article-header img",
        ".book-info img",
        ".novel-info img",
        ".cover img",
        ".article-content img",
        "main img",
        "img",
    )
    for selector_index, selector in enumerate(selectors):
        for index, image in enumerate(soup.select(selector)):
            src = _candidate_img_src(image, base_url=base_url)
            if not _looks_like_lightnovel_detail_cover(src, detail_url=base_url):
                continue
            if src in seen:
                continue
            seen.add(src)
            width = _node_attr_int(image, "img-width") or _node_attr_int(image, "width")
            height = _node_attr_int(image, "img-height") or _node_attr_int(image, "height")
            if width and height and width > height * 1.35:
                continue
            portrait = bool(width and height and height >= width * 1.15)
            score = (50 if portrait else 0) + max(0, 20 - selector_index)
            image_rows.append((score, -index, src))
    if not image_rows:
        return ""
    image_rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return image_rows[0][2]


def _parse_lightnovel_detail_stats(text: str) -> dict[str, str]:
    compact = _short_text(text, limit=800)
    out: dict[str, str] = {}
    update_match = re.search(r"(\d+(?:\.\d+)?\s*(?:分钟|小時|小时|天|日|个月|月|年)前)", compact)
    if update_match:
        out["update_time"] = update_match.group(1).strip()
    view_match = re.search(r"(?:閱覽數|閲覧数|浏览数|閱讀數|阅读数)\s*[:：]\s*([0-9.]+\s*(?:w|k|万|萬|千)?)", compact, re.IGNORECASE)
    if view_match:
        out["view_count"] = view_match.group(1).strip()
    comment_match = re.search(r"(?:評論|评论)\s*[:：]\s*([0-9.]+\s*(?:w|k|万|萬|千)?)", compact, re.IGNORECASE)
    if comment_match:
        out["comment_count"] = comment_match.group(1).strip()
    return out


def _lightnovel_detail_text(soup: Any, *, limit: int = 160_000) -> str:
    parts: list[str] = []
    for selector in (
        ".article-content",
        "#ChapterBody",
        ".chapter-content",
        ".read-content",
        ".reading-content",
        ".novel-content",
        ".content_read",
    ):
        node = soup.select_one(selector)
        if node is not None:
            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
            if text:
                parts.append(text)
    joined = "\n".join(parts)
    return joined[:limit] if limit > 0 else joined


def _lightnovel_detail_description(soup: Any) -> str:
    for attrs in ({"name": re.compile("^description$", re.IGNORECASE)}, {"property": "og:description"}):
        node = soup.find("meta", attrs=attrs)
        if node is not None:
            value = _short_text(node.get("content"), limit=320)
            if value:
                return value
    for selector in (".introduce", ".book-intro", ".bookDesc", ".book-desc", ".summary", ".intro"):
        node = soup.select_one(selector)
        if node is not None:
            value = _short_text(node.get_text(" ", strip=True), limit=320)
            if value:
                return value
    return ""


def _lightnovel_detail_title(soup: Any, *, detail_url: str) -> str:
    for selector in (".article-title", "h1", ".book-title", ".title"):
        node = soup.select_one(selector)
        title = _short_text(node.get_text(" ", strip=True) if node is not None else "", limit=140)
        if title:
            return title
    if soup.title is not None:
        title = str(soup.title.string or "")
        for marker in ("-輕之國度", "-轻之国度", "- SF轻小说", "_17K小说网"):
            title = title.split(marker, 1)[0]
        return _short_text(title, limit=140)
    return _short_text(detail_url, limit=140)


def _lightnovel_page_looks_blocked(*, text: str, title: str) -> bool:
    compact = f"{title} {text[:1200]}".lower()
    blocked_tokens = (
        "access denied",
        "cloudflare",
        "captcha",
        "安全验证",
        "访问验证",
        "请开启javascript",
        "enable javascript",
        "登录后",
        "需要登录",
        "var arg1=",
    )
    return any(token.lower() in compact for token in blocked_tokens)


def _lightnovel_public_chapter_links(soup: Any, *, base_url: str, limit: int = 4) -> list[str]:
    urls: list[str] = []
    for anchor in soup.find_all("a"):
        text = str(anchor.get_text(" ", strip=True) or "")
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        if not re.search(r"(最新公众章节|最新公開章|第\s*[\d一二三四五六七八九十百〇零]+\s*[章节話话]|序章|chapter)", text, re.IGNORECASE):
            continue
        absolute = urljoin(base_url, href)
        if absolute not in urls:
            urls.append(absolute)
        if len(urls) >= limit:
            break
    return urls


def _probe_lightnovel_public_chapter_text(urls: list[str], *, timeout_s: int, referer: str) -> tuple[int, str]:
    if not urls:
        return 0, ""
    try:
        import requests  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except Exception:  # pragma: no cover - import guard
        return 0, ""
    best_len = 0
    best_url = ""
    for url in urls[:2]:
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Natsume-Ai-LightnovelRecommender/1.0",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.2",
                    "Referer": referer,
                },
                timeout=max(3, int(timeout_s)),
            )
            if response.status_code >= 400:
                continue
            if response.encoding and response.encoding.lower() in {"iso-8859-1", "ascii"}:
                response.encoding = response.apparent_encoding or response.encoding
            soup = BeautifulSoup(response.text or "", "html.parser")
            for node in soup(["script", "style", "noscript", "svg"]):
                node.decompose()
            text_len = len(_lightnovel_detail_text(soup, limit=120_000))
            if text_len > best_len:
                best_len = text_len
                best_url = url
        except Exception:
            continue
    return best_len, best_url


def _classify_lightnovel_content_access(
    *,
    soup: Any,
    title: str,
    description: str,
    detail_url: str,
    timeout_s: int,
) -> dict[str, Any]:
    page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    if _lightnovel_page_looks_blocked(text=page_text, title=title):
        return {
            "content_access_state": "BLOCKED",
            "content_access_reason": "detail_page_requires_verification_or_login",
            "content_char_count": 0,
        }
    content_text = _lightnovel_detail_text(soup)
    content_char_count = len(content_text)
    if content_char_count >= 500:
        return {
            "content_access_state": "READABLE",
            "content_access_reason": "detail_page_contains_public_body_text",
            "content_char_count": content_char_count,
        }
    chapter_links = _lightnovel_public_chapter_links(soup, base_url=detail_url)
    linked_char_count, readable_url = _probe_lightnovel_public_chapter_text(
        chapter_links,
        timeout_s=timeout_s,
        referer=detail_url,
    )
    if linked_char_count >= 500:
        return {
            "content_access_state": "READABLE",
            "content_access_reason": "public_chapter_body_is_readable",
            "content_char_count": linked_char_count,
            "readable_chapter_url": readable_url,
        }
    if description or chapter_links:
        return {
            "content_access_state": "SUMMARY_ONLY",
            "content_access_reason": "only_summary_or_chapter_index_available",
            "content_char_count": max(content_char_count, linked_char_count),
            "readable_chapter_url": readable_url,
        }
    return {
        "content_access_state": "UNKNOWN",
        "content_access_reason": "no_public_body_or_summary_detected",
        "content_char_count": content_char_count,
    }


def _fetch_lightnovel_detail_metadata(url: str, *, timeout_s: int) -> dict[str, Any]:
    detail_url = str(url or "").strip()
    if not detail_url.startswith(("http://", "https://")):
        return {}
    try:
        import requests  # noqa: PLC0415
        from bs4 import BeautifulSoup  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - import guard
        return {"detail_status": "FAILED", "detail_reason_code": "DETAIL_IMPORT_FAILED", "detail_error": str(exc)}

    try:
        response = requests.get(
            detail_url,
            headers={"User-Agent": "Natsume-Ai-LightnovelRecommender/1.0", "Accept": "text/html,application/xhtml+xml,*/*;q=0.2"},
            timeout=max(3, int(timeout_s)),
        )
    except Exception as exc:
        return {"detail_status": "FAILED", "detail_reason_code": "DETAIL_FETCH_FAILED", "detail_error": str(exc)}
    if response.status_code >= 400:
        return {
            "detail_status": "FAILED",
            "detail_reason_code": f"DETAIL_HTTP_{response.status_code}",
            "content_access_state": "BLOCKED" if response.status_code in {401, 403, 451} else "UNKNOWN",
            "content_access_reason": f"http_{response.status_code}",
        }
    if response.encoding and response.encoding.lower() in {"iso-8859-1", "ascii"}:
        response.encoding = response.apparent_encoding or response.encoding

    soup = BeautifulSoup(response.text or "", "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    title = _lightnovel_detail_title(soup, detail_url=detail_url)
    description = _lightnovel_detail_description(soup)
    info_node = soup.select_one(".article-infos")
    info_text = info_node.get_text(" ", strip=True) if info_node is not None else soup.get_text(" ", strip=True)[:800]
    access = _classify_lightnovel_content_access(
        soup=soup,
        title=title,
        description=description,
        detail_url=detail_url,
        timeout_s=timeout_s,
    )
    return {
        "detail_status": "OK",
        "title": title,
        "intro_snippet": description,
        "cover_url": _select_lightnovel_cover_url(soup, base_url=detail_url),
        **access,
        **_parse_lightnovel_detail_stats(info_text),
    }


def _enrich_lightnovel_recommendations_from_details(
    items: list[dict[str, Any]],
    *,
    max_items: int,
    timeout_s: int,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        out = dict(item)
        if index < max(0, int(max_items)):
            metadata = _fetch_lightnovel_detail_metadata(
                str(out.get("detail_url") or out.get("url") or out.get("source_url") or ""),
                timeout_s=timeout_s,
            )
            if metadata:
                out["detail_fetch"] = {
                    "status": metadata.get("detail_status"),
                    "reason_code": metadata.get("detail_reason_code"),
                }
                for key in (
                    "title",
                    "cover_url",
                    "intro_snippet",
                    "update_time",
                    "view_count",
                    "comment_count",
                    "content_access_state",
                    "content_access_reason",
                    "readable_chapter_url",
                ):
                    value = str(metadata.get(key) or "").strip()
                    if value:
                        if key in {
                            "view_count",
                            "comment_count",
                            "content_access_state",
                            "content_access_reason",
                            "readable_chapter_url",
                        } or not str(out.get(key) or "").strip():
                            out[key] = value
                if metadata.get("content_char_count") is not None:
                    out["content_char_count"] = int(metadata.get("content_char_count") or 0)
        enriched.append(out)
    return enriched


def _write_native_lightnovel_report(
    *,
    report_path: Path,
    ranked_items: list[dict[str, Any]],
    source_count: int,
    readable_source_count: int,
) -> str:
    lines = [
        "# 轻小说推荐报告",
        "",
        f"- 来源数：{source_count}，可读来源：{readable_source_count}",
        f"- 推荐候选：{len(ranked_items)}",
        "",
        "## 推荐列表",
        "",
    ]
    for item in ranked_items:
        rank = int(item.get("recommendation_rank") or 0)
        title = str(item.get("title") or item.get("title_display") or "未命名作品").strip()
        url = str(item.get("detail_url") or item.get("url") or item.get("source_url") or "").strip()
        score = item.get("recommendation_adjusted_score") or item.get("recommendation_score")
        reason = str(item.get("selection_reason") or item.get("rationale") or "").strip()
        lines.append(f"{rank}. {title}")
        if score is not None:
            lines.append(f"   - score: {score}")
        if reason:
            lines.append(f"   - reason: {reason}")
        if url:
            lines.append(f"   - url: {url}")
    text = "\n".join(lines).strip() + "\n"
    report_path.write_text(text, encoding="utf-8")
    return text


def _extract_lightnovel_intro(body_text: str) -> str:
    """Extract the novel synopsis from a lightnovel detail page body text.

    lightnovel.fun layout after rendering:
      ────────\n\nauthor info...\n────────\n\n\n\n\n簡介\n<INTRO>\n\n\nCONTENTS\n
    """
    if not body_text:
        return ""
    # Find 簡介 or 简介 marker
    i1 = body_text.find("簡介")
    i2 = body_text.find("简介")
    idx = i1 if i1 >= 0 else i2
    if idx < 0:
        return ""
    # Advance past the marker line
    nl = body_text.find("\n", idx)
    if nl < 0:
        return ""
    after = body_text[nl + 1:]
    # Find end boundary: CONTENTS, ───, or 5+ consecutive newlines
    end = after.find("\nCONTENTS")
    if end < 0:
        end = after.find("\n" + "─" * 3)
    if end < 0:
        end = after.find("\n\n\n\n\n")
    return after[:end if end >= 0 else 600].strip()[:500]


def _collect_lightnovel_candidates_via_playwright(
    *,
    list_urls: list[str] | None = None,
    max_detail_pages: int = 0,
    detail_timeout_s: int = 8,
) -> list[dict[str, Any]]:
    """Use Playwright to extract lightnovel candidates from public pages.

    Playwright (headless Chrome) navigates list pages, executes JavaScript
    to extract structured data directly from the DOM — no LLM in the loop,
    zero hallucination risk, deterministic output.

    Args:
        list_urls: URLs of catalogue/list pages to scrape
        max_detail_pages: if > 0, also visit that many detail pages for
            cover images, tag extraction, and intro enrichment
        detail_timeout_s: timeout per detail page in seconds
    """
    from host_brain.agent_runtime.browser_automation_service import (  # noqa: PLC0415
        get_browser_automation_service,
    )
    browser = get_browser_automation_service(
        session_id="natsume_lightnovel",
        auto_connect=_parse_bool_env("HOST_BROWSER_AUTO_CONNECT", default=True),
    )
    urls = list(list_urls or []) or ["https://lightnovel.fun/cn/"]
    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # --- list-page JS: detail links + view/comment counts ----------------------
    list_script = (
        "(() => JSON.stringify(Array.from(document.querySelectorAll(\"a\"))"
        ".map(function(a){"
        "  var t=(a.innerText||\"\").trim();"
        "  var title=t.split(\"\\\\n\")[0]?.trim()||\"\";"
        "  var m=t.match(/(\\d+(?:\\.\\d+)?[wW\\u4e07\\u842c\\u5343kK]?)\\s*\\n\\s*(\\d+)/);"
        "  return{text:t.slice(0,500),title:title,href:a.href,"
        "    view_count:m?m[1]:\"\",comment_count:m?m[2]:\"\"};"
        "})"
        ".filter(function(x){"
        "  return x.title.length>=4 && x.href.indexOf(\"detail/\")>=0;"
        "})))()"
    )

    # --- detail-page JS: raw body text + best cover image ---------------------
    detail_script = (
        "(()=>{"
        "  function abs(u){try{return new URL(u,location.href).href;}catch(e){return u||'';}}"
        "  function srcOf(i){return abs(i.currentSrc||i.src||i.getAttribute('data-src')||i.getAttribute('data-original')||i.getAttribute('data-lazy-src')||'');}"
        "  function ok(u){var s=(u||'').toLowerCase();var ln=s.indexOf('res.lightnovel.fun/images/')>=0&&/\\.(jpg|jpeg|png|webp)(?:[?#]|$)/.test(s);var noire=s.indexOf('i.noire.cc/image/')>=0;return /^https?:/.test(s)&&(ln||noire);}"
        "  var rows=[];"
        "  ['.article-infos img','.article-info img','.article-header img','.book-info img','.novel-info img','.cover img','.article-content img','main img','img'].forEach(function(sel,si){"
        "    Array.from(document.querySelectorAll(sel)).forEach(function(i,idx){"
        "      var s=srcOf(i);if(!ok(s))return;"
        "      var w=i.naturalWidth||parseInt(i.getAttribute('width')||i.getAttribute('img-width')||0)||0;"
        "      var h=i.naturalHeight||parseInt(i.getAttribute('height')||i.getAttribute('img-height')||0)||0;"
        "      if(w&&h&&w>h*1.35)return;"
        "      var portrait=(w&&h&&h>=w*1.15)?1:0;"
        "      rows.push({s:s,score:portrait*50+(20-si),idx:idx+si*1000});"
        "    });"
        "  });"
        "  rows.sort(function(a,b){return (b.score-a.score)||(a.idx-b.idx);});"
        "  var cover=rows.length?rows[0].s:'';"
        "  return JSON.stringify({body:document.body.innerText||'',cover:cover});"
        "})()"
    )

    # --- list pages -----------------------------------------------------------
    for url in urls:
        try:
            raw = browser.open_wait_eval(
                url,
                list_script,
                wait_expression="document.readyState === \"complete\"",
                open_timeout_ms=20000,
                wait_timeout_ms=15000,
                eval_timeout_ms=15000,
            )
            items = json.loads(_clean_playwright_eval_text(raw))

            for item in items:
                href = str(item.get("href") or "").strip()
                if not href or "detail/" not in href or href in seen_urls:
                    continue
                seen_urls.add(href)
                raw_title = str(item.get("title") or "").strip()
                clean_title = _normalize_lightnovel_candidate_title(raw_title)
                if not clean_title or len(clean_title) < 5:
                    continue
                # Reject garbage: pure numbers, pure special chars, no CJK
                if re.match(r'^[\d\s.，,。！!？?wWkK萬万千百]+$', clean_title):
                    continue
                ctx = str(item.get("text") or "")[:500]
                if not _is_lightnovel_recommendation_candidate(
                    title=clean_title, url=href, context=ctx,
                ):
                    continue
                candidates.append({
                    "title": clean_title,
                    "raw_title": raw_title,
                    "url": href,
                    "detail_url": href,
                    "source_url": href,
                    "cover_url": "",
                    "intro_snippet": "",
                    "view_count": str(item.get("view_count") or ""),
                    "comment_count": str(item.get("comment_count") or ""),
                    "context": ctx,
                    "source_ref": url,
                    "source_title": "lightnovel.fun",
                })
        except Exception as exc:
            logger.warning("Playwright list extraction failed for %s: %s", url, exc)

    # --- detail pages (cover + intro enrichment, intro parsed in Python) -----
    if max_detail_pages > 0 and candidates:
        enriched = 0
        for item in candidates:
            if enriched >= max(max_detail_pages, 1):
                break
            detail_url = item.get("detail_url", "")
            if not detail_url:
                continue
            try:
                tout_ms = max(3, int(detail_timeout_s)) * 1000
                raw = browser.open_wait_eval(
                    detail_url,
                    detail_script,
                    wait_expression="document.readyState === \"complete\" && document.body.innerText.length > 500",
                    open_timeout_ms=tout_ms,
                    wait_timeout_ms=min(tout_ms, 20000),
                    eval_timeout_ms=min(tout_ms, 10000),
                )
                detail = json.loads(_clean_playwright_eval_text(raw))
                detail_cover = str(detail.get("cover") or "").strip()
                if detail_cover and _looks_like_lightnovel_detail_cover(detail_cover, detail_url=detail_url):
                    item["cover_url"] = detail_cover
                # Extract intro in Python (JS regex unreliable due to Unicode escaping)
                body = str(detail.get("body") or "")
                if body:
                    intro = _extract_lightnovel_intro(body)
                    if intro:
                        item["intro_snippet"] = intro
                enriched += 1
            except Exception as exc:
                logger.debug("Playwright detail page failed for %s: %s", detail_url, exc)

    return candidates


def _clean_playwright_eval_text(value: Any) -> str:
    """Normalize Playwright evaluate output before JSON parsing.

    Playwright returns native JS values. The current recommender scripts return
    JSON strings explicitly; this helper only handles defensive string wrapping
    from older artifacts without importing legacy CLI helpers.
    """
    if not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            decoded = json.loads(text)
            if isinstance(decoded, str):
                return decoded
        except json.JSONDecodeError:
            return text[1:-1]
    return text


_DEFAULT_LIGHTNOVEL_SOURCE_URLS: tuple[str, ...] = (
    "https://www.lightnovel.fun/",
    "https://www.lightnovel.fun/cn/category/3/106?type=1",
    "https://book.sfacg.com/rank/",
)


def _lightnovel_source_urls_for_order(order: AgentWorkOrder) -> list[str]:
    input_pack = getattr(order, "input_pack", {}) or {}
    if not isinstance(input_pack, Mapping):
        input_pack = {}
    raw_urls = input_pack.get("urls")
    if not isinstance(raw_urls, (list, tuple)):
        raw_urls = []
    urls: list[str] = []
    seen: set[str] = set()
    for raw in [*raw_urls, *_DEFAULT_LIGHTNOVEL_SOURCE_URLS]:
        url = str(raw or "").strip()
        if not url or not re.match(r"^https?://", url, re.IGNORECASE):
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
    return urls or list(_DEFAULT_LIGHTNOVEL_SOURCE_URLS)


def execute_lightnovel_recommender_work_order(order: AgentWorkOrder, *, run_dir: Path) -> dict[str, Any]:
    """Build lightnovel recommendations via Playwright JS extraction.

    Uses Playwright (headless Chrome) to navigate list/detail pages and
    extract structured data via JavaScript directly from the DOM — no LLM in
    the data extraction loop, deterministic and URL-hallucination-free.

    The extracted candidates are then fed into the existing ranking engine
    (taste profile + freshness + heat + history dedup + diversity).
    """

    run_dir.mkdir(parents=True, exist_ok=True)
    max_items = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_MAX_ITEMS", default=3, min_value=1, max_value=8)
    detail_max_items = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_DETAIL_ENRICH_MAX", default=24, min_value=0, max_value=40)
    pre_rank_detail_max = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_PRE_RANK_DETAIL_ENRICH_MAX", default=24, min_value=0, max_value=40)
    detail_timeout_s = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_DETAIL_TIMEOUT_S", default=8, min_value=3, max_value=30)

    # 1. Playwright JS 提取 → 真实结构化数据（无 LLM）
    source_urls = _lightnovel_source_urls_for_order(order)
    candidate_items = _collect_lightnovel_candidates_via_playwright(
        list_urls=source_urls,
        max_detail_pages=0,
        detail_timeout_s=detail_timeout_s,
    )
    source_count = len(candidate_items)
    readable_count = source_count

    # 2. 排名引擎打分
    recommendations = _lightnovel_recommendations_from_browser_items(candidate_items)
    recommendations = _enrich_lightnovel_recommendations_from_details(
        recommendations,
        max_items=min(pre_rank_detail_max, max(detail_max_items, len(recommendations))),
        timeout_s=detail_timeout_s,
    )
    taste_profile = _lightnovel_taste_profile_for_order(order)
    pre_ranked_items = _rank_lightnovel_recommendations(
        recommendations,
        max_items=max_items,
        profile=taste_profile,
    )
    enriched_items = _enrich_lightnovel_recommendations_from_details(
        pre_ranked_items,
        max_items=max(max_items, detail_max_items),
        timeout_s=detail_timeout_s,
    )
    ranked_items = _rank_lightnovel_recommendations(
        enriched_items,
        max_items=max_items,
        profile=taste_profile,
    )

    recommendations_path = run_dir / "recommendations.json"
    cover_manifest_path = run_dir / "cover_manifest.json"
    report_path = run_dir / "recommendation_report.md"
    result_contract_path = run_dir / "result_contract.json"
    recommendations_payload = {
        "schema_version": "Natsume.lightnovel_recommendations.v1",
        "generated_ts_ms": now_ms(),
        "source": "browser_researcher",
        "recommendations": ranked_items,
    }
    recommendations_path.write_text(json.dumps(recommendations_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    cover_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "Natsume.lightnovel_cover_manifest.v1",
                "covers": [
                    {
                        "title": item.get("title") or item.get("title_display") or "",
                        "detail_url": item.get("detail_url") or item.get("url") or "",
                        "cover_url": item.get("cover_url") or "",
                    }
                    for item in ranked_items
                    if str(item.get("cover_url") or "").strip()
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_text = _write_native_lightnovel_report(
        report_path=report_path,
        ranked_items=ranked_items,
        source_count=source_count,
        readable_source_count=readable_count,
    )
    status = "EXECUTED" if ranked_items else "BLOCKED"
    reason_code = None if ranked_items else "LIGHTNOVEL_PLAYWRIGHT_NO_RECOMMENDATIONS"
    contract_payload = {
        "workorder_id": order.work_order_id,
        "status": "complete" if ranked_items else "blocked",
        "recommendation_count": len(ranked_items),
        "recommendations_ref": str(recommendations_path),
        "sources_ref": str(run_dir / "sources.jsonl"),
        "report_ref": str(report_path),
        "reason_code": reason_code,
    }
    result_contract_path.write_text(json.dumps(contract_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result_data = {
        "status": status,
        "recommendations": ranked_items,
        "recommendation_count": len(ranked_items),
        "source_count": source_count,
        "readable_source_count": readable_count,
        "summary": (
            f"Playwright lightnovel recommendations built from {source_count} candidate(s)."
            if ranked_items
            else "Playwright recommender found no usable recommendation candidates."
        ),
        "result_text": report_text,
        "reason_code": reason_code,
        "risk_flags": [] if ranked_items else ["lightnovel_browser_no_recommendations"],
        "metadata": {
            "browser_recommender": True,
            "workspace": str(run_dir),
            "recommendations_ref": str(recommendations_path),
            "result_contract_ref": str(result_contract_path),
            "report_path": str(report_path),
            "sources_path": str(run_dir / "sources.jsonl"),
            "cover_manifest_path": str(cover_manifest_path),
            "candidate_count": source_count,
            "recommendation_count": len(ranked_items),
            "detail_enrich_count": min(len(pre_ranked_items), max(max_items, detail_max_items)),
            "source_count": source_count,
            "readable_source_count": readable_count,
            "source_urls": source_urls,
        },
    }
    (run_dir / "result.json").write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return result_data




__all__ = ["execute_lightnovel_recommender_work_order"]
