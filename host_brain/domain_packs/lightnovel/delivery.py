"""User-visible delivery for browser research and lightnovel artifacts."""

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

from host_brain.domain_packs.production_jobs import (
    _append_production_memory_event, _parse_bool_env, _parse_env_text, _parse_int_env,
    _read_json_file, _record_gateway_visible_turn, _resolve_order_chat_context, _short_text,
)
from host_brain.domain_packs.lightnovel.ranking import (
    _coerce_recommendation_items, _lightnovel_gateway_cover_url,
    _lightnovel_taste_profile_for_order, _rank_lightnovel_recommendations,
    _record_lightnovel_recommendation_history, _visible_lightnovel_ranked_items,
)
from host_brain.domain_packs.lightnovel.research import (
    _download_lightnovel_gateway_cover, _find_lightnovel_recommendations_file,
    _materialize_lightnovel_recommendations_from_result,
    _write_lightnovel_ranked_recommendations,
)



def _public_lightnovel_reason(value: Any, *, limit: int = 76) -> str:
    text = _short_text(value, limit=limit)
    if not text:
        return ""
    replacements = {
        "命中偏好：奇幻身份钩子": "偏向异世界/特殊身份设定",
        "命中偏好：": "偏向",
        "公开信号较好：更新、热度或资料完整度较高": "更新/热度/资料完整度还可以",
        "奇幻身份钩子明确": "异世界/特殊身份设定比较明确",
        "奇幻身份钩子更明确": "异世界/特殊身份设定更明确",
        "奇幻身份钩子": "异世界/特殊身份设定",
        "身份钩子": "特殊身份设定",
        "、设定新鲜感": "，设定有新鲜感",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _build_lightnovel_gateway_message(items: list[dict[str, Any]], *, max_items: int = 3) -> str:
    visible_items = _visible_lightnovel_ranked_items(items, max_items=max_items)
    if not visible_items:
        return ""
    return f"今日轻小说推荐来了，共 {len(visible_items)} 本。下面按书分别发详情。"


def _build_lightnovel_gateway_card(item: Mapping[str, Any], *, index: int) -> str:
    title = _short_text(item.get("title") or item.get("title_display") or "未命名作品", limit=72)
    update_time = _short_text(item.get("update_time"), limit=32)
    view_count = _short_text(item.get("view_count"), limit=24)
    comment_count = _short_text(item.get("comment_count"), limit=24)
    rationale = _public_lightnovel_reason(
        item.get("selection_reason") or item.get("rationale") or item.get("intro_snippet"),
        limit=76,
    )
    intro = _short_text(item.get("intro_snippet") or item.get("intro") or item.get("description"), limit=90)
    detail_url = _short_text(item.get("detail_url") or item.get("url") or item.get("source_url"), limit=180)
    heat_parts = []
    if view_count:
        heat_parts.append(f"{view_count} 浏览")
    if comment_count:
        heat_parts.append(f"{comment_count} 评论")
    parts = [f"{index}. {title}"]
    meta = "｜".join(part for part in [f"更新：{update_time}" if update_time else "", f"热度：{' / '.join(heat_parts)}" if heat_parts else ""] if part)
    if meta:
        parts.append(meta)
    if rationale:
        parts.append(f"看点：{rationale}")
    if intro and intro != rationale:
        parts.append(f"简介：{intro}")
    if detail_url:
        parts.append(f"详情：{detail_url}")
    return "\n".join(part for part in parts if part).strip()


def _lightnovel_delivery_memory_detail(items: list[dict[str, Any]], *, max_items: int, platform: str, conversation_id: str) -> str:
    visible_items = _visible_lightnovel_ranked_items(items, max_items=max_items)
    if not visible_items:
        return ""
    titles = []
    for index, item in enumerate(visible_items, start=1):
        title = _short_text(item.get("title") or item.get("title_display") or "未命名作品", limit=90)
        titles.append(f"{index}. {title}")
    platform_text = _short_text(platform or "gateway", limit=24)
    conversation_text = _short_text(conversation_id or "unknown", limit=80)
    return (
        f"我刚才通过 {platform_text} 给 {conversation_text} 推荐了 {len(visible_items)} 本轻小说："
        f"{'；'.join(titles)}。"
    )


def _lightnovel_memory_items(items: list[dict[str, Any]], *, max_items: int) -> list[dict[str, str]]:
    memory_items: list[dict[str, str]] = []
    for item in _visible_lightnovel_ranked_items(items, max_items=max_items):
        memory_items.append(
            {
                "title": _short_text(item.get("title") or item.get("title_display") or "未命名作品", limit=180),
                "detail_url": _short_text(item.get("detail_url") or item.get("url") or item.get("source_url"), limit=240),
                "selection_reason": _short_text(item.get("selection_reason") or item.get("rationale") or item.get("reason"), limit=180),
                "intro_snippet": _short_text(item.get("intro_snippet") or item.get("intro") or item.get("description"), limit=240),
                "update_time": _short_text(item.get("update_time"), limit=60),
                "view_count": _short_text(item.get("view_count"), limit=40),
                "comment_count": _short_text(item.get("comment_count"), limit=40),
                "content_access_state": _short_text(item.get("content_access_state"), limit=40),
                "content_access_reason": _short_text(item.get("content_access_reason"), limit=120),
                "readable_chapter_url": _short_text(item.get("readable_chapter_url"), limit=240),
            }
        )
    return memory_items


def _persist_lightnovel_gateway_delivery_memory(
    **_delivery: Any,
) -> dict[str, Any]:
    """Retired projection; canonical ExperienceIngress owns memory writes."""

    return {
        "status": "SKIPPED",
        "reason_code": "CANONICAL_EXPERIENCE_INGRESS_REQUIRED",
    }


def _lightnovel_gateway_cards(
    items: list[dict[str, Any]], *, max_items: int, include_covers: bool = True
) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for index, item in enumerate(_visible_lightnovel_ranked_items(items, max_items=max_items), start=1):
        cover_url = _lightnovel_gateway_cover_url(item)
        card_text = _build_lightnovel_gateway_card(item, index=index)
        if not card_text:
            continue
        has_cover = cover_url.startswith("http://") or cover_url.startswith("https://")
        card = {"cover_url": "", "caption": card_text, "msg_type": "text"}
        if include_covers and has_cover:
            card["cover_url"] = cover_url
            card["msg_type"] = "image"
        cards.append(card)
    return cards


def _resolve_lightnovel_gateway_route() -> tuple[bool, str, str, str]:
    if not _parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_ENABLED", default=True):
        return False, "", "", "lightnovel Gateway push disabled by env"
    platform = (
        str(
            os.environ.get("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_PLATFORM")
            or os.environ.get("HOST_PRODUCTION_GATEWAY_PLATFORM")
            or "qq"
        )
        .strip()
        .lower()
    )
    conversation_id = str(
        os.environ.get("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_CONVERSATION_ID")
        or os.environ.get("HOST_PRODUCTION_GATEWAY_CONVERSATION_ID")
        or os.environ.get("HOST_GATEWAY_DEFAULT_QQ_CONVERSATION_ID")
        or ""
    ).strip()
    if not platform or not conversation_id:
        return False, platform, conversation_id, "Gateway route missing platform or conversation_id"
    return True, platform, conversation_id, ""


def _resolve_lightnovel_epub_gateway_route() -> tuple[bool, str, str, str]:
    if not _parse_bool_env("HOST_LIGHTNOVEL_EPUB_GATEWAY_ENABLED", default=True):
        return False, "", "", "lightnovel EPUB Gateway push disabled by env"
    platform = (
        _parse_env_text(
            "HOST_LIGHTNOVEL_EPUB_GATEWAY_PLATFORM",
            default=_parse_env_text(
                "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_PLATFORM",
                default=_parse_env_text("HOST_PRODUCTION_GATEWAY_PLATFORM", default="qq"),
            ),
        )
        .strip()
        .lower()
    )
    conversation_id = _parse_env_text(
        "HOST_LIGHTNOVEL_EPUB_GATEWAY_CONVERSATION_ID",
        default=_parse_env_text(
            "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_CONVERSATION_ID",
            default=_parse_env_text(
                "HOST_PRODUCTION_GATEWAY_CONVERSATION_ID",
                default=_parse_env_text("HOST_GATEWAY_DEFAULT_QQ_CONVERSATION_ID"),
            ),
        ),
    )
    if not platform or not conversation_id:
        return False, platform, conversation_id, "Gateway route missing platform or conversation_id"
    return True, platform, conversation_id, ""


def _resolve_browser_research_gateway_route() -> tuple[bool, str, str, str]:
    if not _parse_bool_env("HOST_BROWSER_RESEARCH_GATEWAY_ENABLED", default=False):
        return False, "", "", "browser research Gateway push disabled by env"
    platform = (
        _parse_env_text(
            "HOST_BROWSER_RESEARCH_GATEWAY_PLATFORM",
            default=_parse_env_text(
                "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_PLATFORM",
                default=_parse_env_text("HOST_PRODUCTION_GATEWAY_PLATFORM", default="qq"),
            ),
        )
        .strip()
        .lower()
    )
    conversation_id = _parse_env_text(
        "HOST_BROWSER_RESEARCH_GATEWAY_CONVERSATION_ID",
        default=_parse_env_text(
            "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_CONVERSATION_ID",
            default=_parse_env_text(
                "HOST_PRODUCTION_GATEWAY_CONVERSATION_ID",
                default=_parse_env_text("HOST_GATEWAY_DEFAULT_QQ_CONVERSATION_ID"),
            ),
        ),
    )
    if not platform or not conversation_id:
        return False, platform, conversation_id, "Gateway route missing platform or conversation_id"
    return True, platform, conversation_id, ""


def _find_lightnovel_epub_file(*, result: Mapping[str, Any], run_dir: Path) -> Path | None:
    candidates: list[Path] = []
    for container in (result, result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}):
        if not isinstance(container, Mapping):
            continue
        for key in ("epub_path", "file_path", "artifact_path"):
            value = str(container.get(key) or "").strip()
            if value:
                candidates.append(Path(value))
        workspace = str(container.get("workspace") or "").strip()
        if workspace:
            candidates.extend(Path(workspace).glob("*.epub"))
    artifacts_dir = str(result.get("artifacts_dir") or "").strip()
    if artifacts_dir:
        candidates.extend(Path(artifacts_dir).glob("**/*.epub"))
    candidates.extend(run_dir.glob("**/*.epub"))

    seen: set[str] = set()
    for path in candidates:
        resolved = str(path.expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        if path.exists() and path.is_file() and path.suffix.lower() == ".epub":
            return path.expanduser()
    return None


def _remote_epub_path(local_path: Path) -> str:
    remote_root = _parse_env_text("HOST_LIGHTNOVEL_EPUB_GATEWAY_EDGE_ROOT", default=str(Path.cwd()))
    root = Path(remote_root)
    try:
        rel = local_path.resolve().relative_to(Path.cwd().resolve())
    except Exception:
        rel = Path("artifacts") / "agent_production" / local_path.name
    return str(root / rel).replace("\\", "/")


def _copy_epub_for_gateway_if_configured(epub_path: Path) -> dict[str, Any]:
    target = _parse_env_text("HOST_LIGHTNOVEL_EPUB_GATEWAY_EDGE_SSH_TARGET")
    if not target:
        return {
            "status": "SKIPPED",
            "success": True,
            "reason_code": "LIGHTNOVEL_EPUB_EDGE_COPY_NOT_CONFIGURED",
            "gateway_media_path": str(epub_path),
        }
    if shutil.which("ssh") is None or shutil.which("scp") is None:
        return {
            "status": "FAILED",
            "success": False,
            "reason_code": "LIGHTNOVEL_EPUB_EDGE_COPY_TOOL_MISSING",
            "detail": "ssh or scp executable not found",
            "gateway_media_path": str(epub_path),
        }

    remote_path = _remote_epub_path(epub_path)
    remote_dir = str(Path(remote_path).parent).replace("\\", "/")
    quoted_dir = remote_dir.replace("'", "''")
    try:
        remote_mkdir = f'powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path \'{quoted_dir}\' | Out-Null"'
        _subprocess_run(
            [
                "ssh",
                target,
                remote_mkdir,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        _subprocess_run(
            ["scp", str(epub_path), f"{target}:{remote_path}"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return {
            "status": "FAILED",
            "success": False,
            "reason_code": "LIGHTNOVEL_EPUB_EDGE_COPY_FAILED",
            "detail": detail or str(exc),
            "gateway_media_path": remote_path,
            "source_path": str(epub_path),
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "success": False,
            "reason_code": "LIGHTNOVEL_EPUB_EDGE_COPY_FAILED",
            "detail": str(exc),
            "gateway_media_path": remote_path,
            "source_path": str(epub_path),
        }
    return {
        "status": "COPIED",
        "success": True,
        "target": target,
        "source_path": str(epub_path),
        "gateway_media_path": remote_path,
    }


def _copy_lightnovel_cover_for_gateway_if_configured(cover_path: Path) -> dict[str, Any]:
    target = _parse_env_text(
        "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_EDGE_SSH_TARGET",
        default=_parse_env_text("HOST_LIGHTNOVEL_EPUB_GATEWAY_EDGE_SSH_TARGET"),
    )
    if not target:
        return {
            "status": "SKIPPED",
            "success": True,
            "reason_code": "LIGHTNOVEL_COVER_EDGE_COPY_NOT_CONFIGURED",
            "gateway_media_path": str(cover_path.resolve()),
        }
    if shutil.which("ssh") is None or shutil.which("scp") is None:
        return {
            "status": "FAILED",
            "success": False,
            "reason_code": "LIGHTNOVEL_COVER_EDGE_COPY_TOOL_MISSING",
            "detail": "ssh or scp executable not found",
            "gateway_media_path": str(cover_path.resolve()),
        }
    remote_root = _parse_env_text(
        "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_EDGE_ROOT",
        default=_parse_env_text("HOST_LIGHTNOVEL_EPUB_GATEWAY_EDGE_ROOT", default=str(Path.cwd())),
    )
    root = Path(remote_root)
    try:
        rel = cover_path.resolve().relative_to(Path.cwd().resolve())
    except Exception:
        rel = Path("artifacts") / "agent_production" / cover_path.name
    remote_path = str(root / rel).replace("\\", "/")
    remote_dir = str(Path(remote_path).parent).replace("\\", "/")
    quoted_dir = remote_dir.replace("'", "''")
    try:
        remote_mkdir = f'powershell -NoProfile -Command "New-Item -ItemType Directory -Force -Path \'{quoted_dir}\' | Out-Null"'
        _subprocess_run(
            ["ssh", target, remote_mkdir],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        _subprocess_run(
            ["scp", str(cover_path.resolve()), f"{target}:{remote_path}"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        return {
            "status": "FAILED",
            "success": False,
            "reason_code": "LIGHTNOVEL_COVER_EDGE_COPY_FAILED",
            "detail": detail or str(exc),
            "gateway_media_path": remote_path,
            "source_path": str(cover_path.resolve()),
        }
    except Exception as exc:
        return {
            "status": "FAILED",
            "success": False,
            "reason_code": "LIGHTNOVEL_COVER_EDGE_COPY_FAILED",
            "detail": str(exc),
            "gateway_media_path": remote_path,
            "source_path": str(cover_path.resolve()),
        }
    return {
        "status": "COPIED",
        "success": True,
        "target": target,
        "source_path": str(cover_path.resolve()),
        "gateway_media_path": remote_path,
    }


def _build_lightnovel_epub_gateway_notice(result: Mapping[str, Any], epub_path: Path) -> str:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    title = _short_text(metadata.get("title") or result.get("title") or epub_path.stem, limit=90)
    size = metadata.get("epub_size_bytes") or result.get("epub_size_bytes")
    sha256 = _short_text(metadata.get("sha256") or result.get("sha256"), limit=16)
    chapters = metadata.get("chapter_count") or result.get("chapter_count")
    images = metadata.get("image_count") or result.get("image_count")
    parts = [f"EPUB 已封装完成：{title}"]
    detail = []
    if chapters:
        detail.append(f"{chapters} 个目录节点")
    if images:
        detail.append(f"{images} 张图片")
    if size:
        detail.append(f"{int(size) / 1024 / 1024:.2f} MB")
    if detail:
        parts.append(" / ".join(detail))
    if sha256:
        parts.append(f"SHA256: {sha256}...")
    return "\n".join(parts)


def _post_gateway_reply_sync(**kwargs: Any) -> Any:
    from host_brain.channels.edge_transport import submit_gateway_reply as post_gateway_reply  # noqa: PLC0415

    async def _send() -> Any:
        return await post_gateway_reply(**kwargs)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_send())
    raise RuntimeError(
        "synchronous Domain Pack delivery cannot own an event-loop dispatcher"
    )


def _build_browser_research_gateway_message(result: Mapping[str, Any], *, max_sources: int = 3) -> str:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    source_summaries = metadata.get("source_summaries") if isinstance(metadata.get("source_summaries"), list) else []
    candidate_items = metadata.get("candidate_items") if isinstance(metadata.get("candidate_items"), list) else []
    price_mentions = metadata.get("price_mentions") if isinstance(metadata.get("price_mentions"), list) else []
    readable_count = int(metadata.get("readable_source_count") or 0)
    source_count = int(metadata.get("source_count") or len(source_summaries))
    lines = [f"资料研究简报完成：可读来源 {readable_count}/{source_count}"]
    summary = _short_text(result.get("summary") or "", limit=120)
    if summary:
        lines.append(summary)
    if price_mentions:
        lines.append(f"价格线索：{'、'.join(_short_text(item, limit=32) for item in price_mentions[:5])}")
    if candidate_items:
        lines.append("可直接继续的候选：")
    for index, item in enumerate(candidate_items[: max(1, int(max_sources))], start=1):
        if not isinstance(item, Mapping):
            continue
        title = _short_text(item.get("title") or item.get("source_ref") or f"来源 {index}", limit=70)
        source_ref = _short_text(item.get("url") or item.get("source_ref") or "", limit=120)
        excerpt = _short_text(item.get("context") or item.get("description") or item.get("excerpt") or "", limit=100)
        if len(re.findall(r"(?:^|\s)\d{1,2}\s", excerpt)) >= 3:
            excerpt = ""
        lines.append(f"{index}. {title}")
        if excerpt:
            lines.append(f"   {excerpt}")
        if source_ref:
            lines.append(f"   {source_ref}")
    if not candidate_items:
        for index, item in enumerate(source_summaries[: max(1, int(max_sources))], start=1):
            if not isinstance(item, Mapping):
                continue
            title = _short_text(item.get("title") or item.get("source_ref") or f"来源 {index}", limit=70)
            source_ref = _short_text(item.get("source_ref") or "", limit=120)
            excerpt = _short_text(item.get("description") or item.get("excerpt") or "", limit=130)
            lines.append(f"{index}. {title}")
            if excerpt:
                lines.append(f"   {excerpt}")
            if source_ref:
                lines.append(f"   {source_ref}")
    report_path = _short_text(metadata.get("report_path") or "", limit=160)
    if report_path:
        lines.append(f"报告：{report_path}")
    if candidate_items:
        lines.append("回复“选第 N 个继续查/封装/比价”就能接下一步。")
    return "\n".join(lines)


def _maybe_push_browser_research_to_gateway(
    *,
    job_id: str,
    order: AgentWorkOrder,
    result: Mapping[str, Any],
    run_dir: Path,
) -> dict[str, Any] | None:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id != "browser_research":
        return None
    if str(result.get("status") or "").upper() != "EXECUTED":
        return {
            "status": "SKIPPED",
            "success": False,
            "reason_code": "BROWSER_RESEARCH_NOT_EXECUTED",
            "detail": str(result.get("reason_code") or result.get("summary") or ""),
        }
    enabled, platform, conversation_id, route_detail = _resolve_browser_research_gateway_route()
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    if not enabled:
        return {
            "status": "SKIPPED",
            "success": False,
            "reason_code": "BROWSER_RESEARCH_GATEWAY_DISABLED",
            "detail": route_detail,
            "report_ref": str(metadata.get("report_path") or run_dir / "browser_research_report.md"),
        }

    wait_result = _parse_bool_env("HOST_BROWSER_RESEARCH_GATEWAY_WAIT_RESULT", default=True)
    timeout_ms = _parse_int_env("HOST_BROWSER_RESEARCH_GATEWAY_TIMEOUT_MS", default=45000, min_value=3000, max_value=180000)
    try:
        gateway_message = _build_browser_research_gateway_message(result)
        reply_result = _post_gateway_reply_sync(
            platform=platform,
            conversation_id=conversation_id,
            content=gateway_message,
            msg_type="text",
            wait_result=wait_result,
            timeout_ms=timeout_ms,
            raw_extra={
                "reply_source": "browser_research",
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "report_ref": str(metadata.get("report_path") or ""),
                "sources_ref": str(metadata.get("sources_path") or ""),
            },
        )
        payload = reply_result.as_dict() if hasattr(reply_result, "as_dict") else dict(reply_result or {})
        success = bool(payload.get("success", True)) and str(payload.get("status") or "").lower() not in {"failed", "error"}
        delivery = {
            "status": "SENT" if success else "FAILED",
            "success": success,
            "platform": platform,
            "conversation_id": conversation_id,
            "wait_result": wait_result,
            "report_ref": str(metadata.get("report_path") or ""),
            "gateway_result": payload,
        }
        visible_projection = _record_gateway_visible_turn(
            order=order,
            request_id=f"{job_id}::browser_research::summary",
            platform=platform,
            conversation_id=conversation_id,
            content=gateway_message,
            msg_type="text",
            source_kind="browser_research.gateway",
            gateway_result=payload,
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "report_ref": str(metadata.get("report_path") or ""),
                "sources_ref": str(metadata.get("sources_path") or ""),
            },
            metadata={"job_id": job_id, "work_order_id": order.work_order_id},
        )
        delivery["conversation_turn_projection"] = visible_projection
        _append_production_memory_event(
            order=order,
            source_kind="browser_research",
            event_kind="browser_research_gateway_delivery",
            status=str(delivery.get("status") or ""),
            summary=f"Browser research Gateway delivery {delivery.get('status')}; report={delivery.get('report_ref')}",
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "report_ref": str(metadata.get("report_path") or ""),
                "sources_ref": str(metadata.get("sources_path") or ""),
                "platform": platform,
                "conversation_id": conversation_id,
            },
            causality={"gateway_result": payload, "conversation_turn_projection": visible_projection},
            salience=0.64,
            valence=0.18 if success else -0.28,
        )
        return delivery
    except Exception as exc:  # pragma: no cover - external Gateway guard
        failure = {
            "status": "FAILED",
            "success": False,
            "platform": platform,
            "conversation_id": conversation_id,
            "reason_code": "BROWSER_RESEARCH_GATEWAY_PUSH_FAILED",
            "detail": str(exc),
            "report_ref": str(metadata.get("report_path") or ""),
        }
        _append_production_memory_event(
            order=order,
            source_kind="browser_research",
            event_kind="browser_research_gateway_delivery",
            status="FAILED",
            summary=f"Browser research Gateway delivery failed: {str(exc)[:180]}",
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "report_ref": str(metadata.get("report_path") or ""),
                "platform": platform,
                "conversation_id": conversation_id,
            },
            causality={"reason_code": "BROWSER_RESEARCH_GATEWAY_PUSH_FAILED", "detail": str(exc)[:300]},
            salience=0.68,
            valence=-0.35,
        )
        return failure


def _extract_gateway_message_ids(payload: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    id_keys = {
        "msg_id",
        "message_id",
        "gateway_message_id",
        "target_msg_id",
        "target_message_id",
        "id",
    }

    def _add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            found.append(text)

    def _walk(value: Any, *, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                clean_key = str(key or "").strip().lower()
                if clean_key in id_keys:
                    _add(item)
                elif clean_key in {"result", "data", "payload", "message", "raw", "response"}:
                    _walk(item, depth=depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in list(value)[:8]:
                _walk(item, depth=depth + 1)

    _walk(payload)
    return found


def _attach_lightnovel_card_message_ids(
    items: Sequence[Mapping[str, Any]],
    *,
    card_results: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, item in enumerate(list(items or [])):
        row = dict(item)
        message_ids = [str(value or "").strip() for value in list(row.get("message_ids") or []) if str(value or "").strip()]
        if index < len(card_results):
            for message_id in _extract_gateway_message_ids(card_results[index]):
                if message_id not in message_ids:
                    message_ids.append(message_id)
        if message_ids:
            row["message_ids"] = message_ids
        enriched.append(row)
    return enriched


def _maybe_push_lightnovel_recommendations_to_gateway(
    *,
    job_id: str,
    order: AgentWorkOrder,
    result: Mapping[str, Any],
    run_dir: Path,
    delivery_recorder: Callable[..., Mapping[str, Any] | None] | None = None,
) -> dict[str, Any] | None:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id != "agent_lightnovel_recommender":
        return None
    if str(result.get("status") or "").upper() != "EXECUTED":
        return None

    enabled, platform, conversation_id, route_detail = _resolve_lightnovel_gateway_route()
    recommendations_path = _find_lightnovel_recommendations_file(result=result, run_dir=run_dir)
    if recommendations_path is None:
        recommendations_path = _materialize_lightnovel_recommendations_from_result(
            order=order,
            result=result,
            run_dir=run_dir,
        )
    if recommendations_path is None:
        return {
            "status": "SKIPPED",
            "success": False,
            "reason_code": "LIGHTNOVEL_RECOMMENDATIONS_MISSING",
            "detail": "recommendations.json not found",
        }
    if not enabled:
        return {
            "status": "SKIPPED",
            "success": False,
            "reason_code": "LIGHTNOVEL_GATEWAY_ROUTE_MISSING",
            "detail": route_detail,
            "recommendations_ref": str(recommendations_path),
        }

    payload = _read_json_file(recommendations_path)
    items = _coerce_recommendation_items(payload)
    max_items = _parse_int_env("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_MAX_ITEMS", default=3, min_value=1, max_value=8)
    taste_profile = _lightnovel_taste_profile_for_order(order)
    ranked_items = _rank_lightnovel_recommendations(
        items,
        max_items=max_items,
        profile=taste_profile,
    )
    visible_ranked_items = _visible_lightnovel_ranked_items(ranked_items, max_items=max_items)
    ranked_ref = _write_lightnovel_ranked_recommendations(
        recommendations_path=recommendations_path,
        ranked_items=ranked_items,
        max_items=max_items,
    )
    message = _build_lightnovel_gateway_message(
        ranked_items,
        max_items=max_items,
    )
    if not message:
        return {
            "status": "SKIPPED",
            "success": False,
            "reason_code": "LIGHTNOVEL_GATEWAY_MESSAGE_EMPTY",
            "detail": "recommendation payload did not contain sendable items",
            "recommendations_ref": str(recommendations_path),
            "ranked_recommendations_ref": str(ranked_ref or ""),
        }

    wait_result = _parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_WAIT_RESULT", default=True)
    timeout_ms = _parse_int_env(
        "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_TIMEOUT_MS",
        default=65_000,
        min_value=3_000,
        max_value=120_000,
    )
    try:
        reply_result = _post_gateway_reply_sync(
            platform=platform,
            conversation_id=conversation_id,
            content=message,
            msg_type="text",
            wait_result=wait_result,
            timeout_ms=timeout_ms,
            raw_extra={
                "request_id": str(order.metadata.get("request_id") or "").strip(),
                "work_order_id": order.work_order_id,
                            "job_id": job_id,
                            "reply_source": "agent_lightnovel_recommender",
                            "workflow_owner": str(order.input_pack.get("workflow_owner") or "natsume"),
                            "recommendations_ref": str(recommendations_path),
                            "ranked_recommendations_ref": str(ranked_ref or ""),
                        },
        )
        result_dict = reply_result.as_dict() if hasattr(reply_result, "as_dict") else dict(getattr(reply_result, "__dict__", {}) or {})
        card_results: list[dict[str, Any]] = []
        cards = _lightnovel_gateway_cards(ranked_items, max_items=max_items, include_covers=_parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_SEND_COVERS", default=True))
        if _parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_SEND_DETAILS", default=True):
            image_timeout_ms = _parse_int_env(
                "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_IMAGE_TIMEOUT_MS",
                default=30_000,
                min_value=3_000,
                max_value=90_000,
            )
            text_timeout_ms = _parse_int_env(
                "HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_DETAIL_TIMEOUT_MS",
                default=30_000,
                min_value=3_000,
                max_value=90_000,
            )
            for card_index, card in enumerate(cards, start=1):
                cover_url = card.get("cover_url", "")
                caption = card["caption"]
                msg_type = card.get("msg_type") or ("image" if cover_url else "text")
                detail_timeout_ms = image_timeout_ms if msg_type == "image" else text_timeout_ms
                local_cover_path: Path | None = None
                cover_copy_result: dict[str, Any] | None = None
                media_path = cover_url
                if msg_type == "image" and cover_url:
                    local_cover_path = _download_lightnovel_gateway_cover(cover_url, run_dir=run_dir, index=card_index)
                    if local_cover_path is not None:
                        cover_copy_result = _copy_lightnovel_cover_for_gateway_if_configured(local_cover_path)
                        if bool(cover_copy_result.get("success")):
                            media_path = str(cover_copy_result.get("gateway_media_path") or local_cover_path.resolve())
                gateway_kwargs: dict[str, Any] = {
                    "platform": platform,
                    "conversation_id": conversation_id,
                    "content": caption,
                    "msg_type": msg_type,
                    "wait_result": wait_result,
                    "timeout_ms": detail_timeout_ms,
                    "raw_extra": {
                        "request_id": str(order.metadata.get("request_id") or "").strip(),
                        "work_order_id": order.work_order_id,
                        "job_id": job_id,
                        "reply_source": "agent_lightnovel_recommender.card",
                        "workflow_owner": str(order.input_pack.get("workflow_owner") or "natsume"),
                        "recommendations_ref": str(recommendations_path),
                        "ranked_recommendations_ref": str(ranked_ref or ""),
                        "caption": caption,
                        "remote_cover_url": cover_url,
                        "local_cover_path": str(local_cover_path) if local_cover_path is not None else "",
                        "cover_copy_result": cover_copy_result or {},
                    },
                }
                if msg_type == "image" and cover_url:
                    gateway_kwargs["media_path"] = media_path
                try:
                    image_result = _post_gateway_reply_sync(**gateway_kwargs)
                    image_result_dict = (
                        image_result.as_dict()
                        if hasattr(image_result, "as_dict")
                        else dict(getattr(image_result, "__dict__", {}) or {})
                    )
                    if bool(image_result_dict.get("success")) or msg_type != "image" or not cover_url:
                        if local_cover_path is not None:
                            image_result_dict["local_cover_path"] = str(local_cover_path)
                        if cover_copy_result is not None:
                            image_result_dict["cover_copy_result"] = cover_copy_result
                        card_results.append(image_result_dict)
                        continue
                    local_cover = local_cover_path or _download_lightnovel_gateway_cover(cover_url, run_dir=run_dir, index=card_index)
                    if local_cover is not None:
                        retry_copy_result = cover_copy_result or _copy_lightnovel_cover_for_gateway_if_configured(local_cover)
                        retry_kwargs = dict(gateway_kwargs)
                        retry_kwargs["media_path"] = str(
                            retry_copy_result.get("gateway_media_path")
                            if bool(retry_copy_result.get("success"))
                            else local_cover.resolve()
                        )
                        retry_kwargs["raw_extra"] = {
                            **dict(gateway_kwargs.get("raw_extra") or {}),
                            "cover_retry": "local_or_edge_file",
                            "remote_cover_url": cover_url,
                            "cover_copy_result": retry_copy_result,
                        }
                        retry_result = _post_gateway_reply_sync(**retry_kwargs)
                        retry_result_dict = (
                            retry_result.as_dict()
                            if hasattr(retry_result, "as_dict")
                            else dict(getattr(retry_result, "__dict__", {}) or {})
                        )
                        retry_result_dict["cover_retry"] = "local_or_edge_file"
                        retry_result_dict["remote_cover_url"] = cover_url
                        retry_result_dict["local_cover_path"] = str(local_cover)
                        retry_result_dict["cover_copy_result"] = retry_copy_result
                        retry_result_dict["remote_attempt"] = image_result_dict
                        if bool(retry_result_dict.get("success")):
                            card_results.append(retry_result_dict)
                            continue
                    text_kwargs = dict(gateway_kwargs)
                    text_kwargs.pop("media_path", None)
                    text_kwargs["msg_type"] = "text"
                    text_kwargs["timeout_ms"] = text_timeout_ms
                    text_kwargs["raw_extra"] = {
                        **dict(gateway_kwargs.get("raw_extra") or {}),
                        "cover_fallback": "text_after_image_failed",
                        "remote_cover_url": cover_url,
                    }
                    fallback_result = _post_gateway_reply_sync(**text_kwargs)
                    fallback_result_dict = (
                        fallback_result.as_dict()
                        if hasattr(fallback_result, "as_dict")
                        else dict(getattr(fallback_result, "__dict__", {}) or {})
                    )
                    fallback_result_dict["cover_fallback"] = "text_after_image_failed"
                    fallback_result_dict["remote_cover_url"] = cover_url
                    fallback_result_dict["remote_attempt"] = image_result_dict
                    card_results.append(fallback_result_dict)
                except Exception as exc:  # pragma: no cover - external image Gateway guard
                    if msg_type == "image" and cover_url:
                        local_cover = local_cover_path or _download_lightnovel_gateway_cover(cover_url, run_dir=run_dir, index=card_index)
                        if local_cover is not None:
                            try:
                                retry_copy_result = cover_copy_result or _copy_lightnovel_cover_for_gateway_if_configured(local_cover)
                                retry_kwargs = dict(gateway_kwargs)
                                retry_kwargs["media_path"] = str(
                                    retry_copy_result.get("gateway_media_path")
                                    if bool(retry_copy_result.get("success"))
                                    else local_cover.resolve()
                                )
                                retry_kwargs["raw_extra"] = {
                                    **dict(gateway_kwargs.get("raw_extra") or {}),
                                    "cover_retry": "local_or_edge_file_after_exception",
                                    "remote_cover_url": cover_url,
                                    "cover_copy_result": retry_copy_result,
                                }
                                retry_result = _post_gateway_reply_sync(**retry_kwargs)
                                retry_result_dict = (
                                    retry_result.as_dict()
                                    if hasattr(retry_result, "as_dict")
                                    else dict(getattr(retry_result, "__dict__", {}) or {})
                                )
                                retry_result_dict["cover_retry"] = "local_or_edge_file_after_exception"
                                retry_result_dict["remote_cover_url"] = cover_url
                                retry_result_dict["local_cover_path"] = str(local_cover)
                                retry_result_dict["cover_copy_result"] = retry_copy_result
                                retry_result_dict["remote_exception"] = str(exc)
                                if bool(retry_result_dict.get("success")):
                                    card_results.append(retry_result_dict)
                                    continue
                            except Exception as retry_exc:  # pragma: no cover - external Gateway guard
                                exc = retry_exc
                    card_results.append(
                        {
                            "success": False,
                            "status": "failed",
                            "reason_code": "LIGHTNOVEL_GATEWAY_CARD_PUSH_FAILED",
                            "detail": str(exc),
                            "cover_url": cover_url,
                        }
                    )
        history_ref: Path | None = None
        if bool(result_dict.get("success")) and _parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_HISTORY_ENABLED", default=True):
            history_ref = _record_lightnovel_recommendation_history(
                visible_ranked_items,
                job_id=job_id,
                recommendations_path=recommendations_path,
            )
        delivery_items = _attach_lightnovel_card_message_ids(
            visible_ranked_items,
            card_results=card_results,
        )
        delivery_log_result: dict[str, Any] = {
            "status": "PERSISTED",
            "reason_code": "DURABLE_DELIVERY_RECEIPT",
            "delivery_id": f"delivery://agent-production/{job_id}/lightnovel",
            "item_count": len(delivery_items),
        }
        if bool(result_dict.get("success")) and callable(delivery_recorder):
            try:
                delivery = delivery_recorder(
                    person_id=str(order.metadata.get("person_id") or order.input_pack.get("person_id") or ""),
                    session_id=str(order.metadata.get("session_id") or order.input_pack.get("session_id") or ""),
                    domain="lightnovel_recommendation",
                    platform=platform,
                    conversation_id=conversation_id,
                    request_id=str(order.metadata.get("request_id") or order.work_order_id),
                    job_id=job_id,
                    recommendations_ref=str(recommendations_path),
                    ranked_recommendations_ref=str(ranked_ref or ""),
                    items=delivery_items,
                    message_payload={"message": message, "card_count": len(card_results)},
                )
                if isinstance(delivery, Mapping):
                    delivery_log_result = {
                        "status": "PERSISTED",
                        "reason_code": "DURABLE_DELIVERY_RECEIPT",
                        "delivery_id": str(delivery.get("delivery_id") or ""),
                        "item_count": len(delivery_items),
                    }
            except Exception:
                delivery_log_result = {
                    "status": "DEGRADED",
                    "reason_code": "RECOMMENDATION_DELIVERY_PERSISTENCE_UNAVAILABLE",
                    "delivery_id": "",
                    "item_count": len(delivery_items),
                }
        delivery_for_memory = {
            "success": bool(result_dict.get("success")),
            "status": "SENT" if bool(result_dict.get("success")) else "FAILED",
            "platform": platform,
            "conversation_id": conversation_id,
            "gateway_result": result_dict,
            "card_results": card_results,
        }
        memory_result = _persist_lightnovel_gateway_delivery_memory(
            job_id=job_id,
            order=order,
            ranked_items=ranked_items,
            max_items=max_items,
            gateway_delivery=delivery_for_memory,
            message=message,
            cards=cards,
        )
        delivery_payload = {
            "status": "SENT" if bool(result_dict.get("success")) else "FAILED",
            "success": bool(result_dict.get("success")),
            "platform": platform,
            "conversation_id": conversation_id,
            "wait_result": wait_result,
            "message_chars": len(message),
            "recommendation_count": len(items),
            "selected_recommendation_count": len(visible_ranked_items),
            "recommendations_ref": str(recommendations_path),
            "ranked_recommendations_ref": str(ranked_ref or ""),
            "history_ref": str(history_ref or ""),
            "ranking_profile_version": str(ranked_items[0].get("ranking_profile_version") or "") if ranked_items else "",
            "gateway_result": result_dict,
            "card_results": card_results,
            "cover_results": card_results,
            "delivery_log_result": delivery_log_result,
            "memory_result": memory_result,
        }
        delivery_id = ""
        if isinstance(delivery_log_result, Mapping):
            delivery_id = str(delivery_log_result.get("delivery_id") or delivery_log_result.get("id") or "").strip()
        conversation_turn_projections: list[dict[str, Any]] = []
        overview_projection = _record_gateway_visible_turn(
            order=order,
            request_id=f"{job_id}::lightnovel::overview",
            platform=platform,
            conversation_id=conversation_id,
            content=message,
            msg_type="text",
            source_kind="agent_lightnovel_recommender.overview",
            gateway_result=result_dict,
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "delivery_id": delivery_id,
                "recommendations_ref": str(recommendations_path),
                "ranked_recommendations_ref": str(ranked_ref or ""),
                "message_part": "overview",
            },
            metadata={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "delivery_id": delivery_id,
                "recommendation_id": delivery_id,
                "message_part": "overview",
            },
        )
        conversation_turn_projections.append(overview_projection)
        for card_index, card in enumerate(cards, start=1):
            card_result = card_results[card_index - 1] if card_index - 1 < len(card_results) else {}
            item = visible_ranked_items[card_index - 1] if card_index - 1 < len(visible_ranked_items) else {}
            card_projection = _record_gateway_visible_turn(
                order=order,
                request_id=f"{job_id}::lightnovel::card::{card_index}",
                platform=platform,
                conversation_id=conversation_id,
                content=str(card.get("caption") or ""),
                msg_type=str(card.get("msg_type") or "text"),
                media_path=str(card_result.get("media_path") or card_result.get("local_cover_path") or ""),
                source_kind="agent_lightnovel_recommender.card",
                gateway_result=card_result,
                refs={
                    "job_id": job_id,
                    "work_order_id": order.work_order_id,
                    "delivery_id": delivery_id,
                    "recommendations_ref": str(recommendations_path),
                    "ranked_recommendations_ref": str(ranked_ref or ""),
                    "message_part": "card",
                    "rank": card_index,
                    "title": str(item.get("title") or item.get("title_display") or ""),
                    "detail_url": str(item.get("detail_url") or item.get("url") or item.get("source_url") or ""),
                },
                metadata={
                    "job_id": job_id,
                    "work_order_id": order.work_order_id,
                    "delivery_id": delivery_id,
                    "recommendation_id": delivery_id,
                    "message_part": "card",
                    "rank": card_index,
                    "cover_url": str(card.get("cover_url") or ""),
                },
            )
            conversation_turn_projections.append(card_projection)
        delivery_payload["conversation_turn_projection"] = conversation_turn_projections
        _append_production_memory_event(
            order=order,
            source_kind="agent_lightnovel_recommender",
            event_kind="lightnovel_gateway_delivery",
            status=str(delivery_payload.get("status") or ""),
            summary=(
                f"Lightnovel recommendation Gateway delivery {delivery_payload.get('status')}; "
                f"items={delivery_payload.get('selected_recommendation_count')}; ref={recommendations_path}"
            ),
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "delivery_id": delivery_id,
                "recommendations_ref": str(recommendations_path),
                "ranked_recommendations_ref": str(ranked_ref or ""),
                "history_ref": str(history_ref or ""),
                "platform": platform,
                "conversation_id": conversation_id,
            },
            causality={
                "gateway_result": result_dict,
                "card_count": len(card_results),
                "selected_recommendation_count": len(visible_ranked_items),
                "conversation_turn_projection": conversation_turn_projections,
            },
            salience=0.78,
            valence=0.24 if bool(result_dict.get("success")) else -0.25,
        )
        return delivery_payload
    except Exception as exc:  # pragma: no cover - external Gateway guard
        failure = {
            "status": "FAILED",
            "success": False,
            "platform": platform,
            "conversation_id": conversation_id,
            "reason_code": "LIGHTNOVEL_GATEWAY_PUSH_FAILED",
            "detail": str(exc),
            "recommendations_ref": str(recommendations_path),
        }
        _append_production_memory_event(
            order=order,
            source_kind="agent_lightnovel_recommender",
            event_kind="lightnovel_gateway_delivery",
            status="FAILED",
            summary=f"Lightnovel recommendation Gateway delivery failed: {str(exc)[:180]}",
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "recommendations_ref": str(recommendations_path),
                "platform": platform,
                "conversation_id": conversation_id,
            },
            causality={"reason_code": "LIGHTNOVEL_GATEWAY_PUSH_FAILED", "detail": str(exc)[:300]},
            salience=0.72,
            valence=-0.36,
        )
        return failure


def _maybe_push_lightnovel_epub_to_gateway(
    *,
    job_id: str,
    order: AgentWorkOrder,
    result: Mapping[str, Any],
    run_dir: Path,
) -> dict[str, Any] | None:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id != "agent_epub_builder":
        return None
    if str(result.get("status") or "").upper() != "EXECUTED":
        return None

    epub_path = _find_lightnovel_epub_file(result=result, run_dir=run_dir)
    if epub_path is None:
        return {
            "status": "SKIPPED",
            "success": False,
            "reason_code": "LIGHTNOVEL_EPUB_FILE_MISSING",
            "detail": "EPUB artifact not found",
        }
    enabled, platform, conversation_id, route_detail = _resolve_lightnovel_epub_gateway_route()
    if not enabled:
        return {
            "status": "SKIPPED",
            "success": False,
            "reason_code": "LIGHTNOVEL_EPUB_GATEWAY_ROUTE_MISSING",
            "detail": route_detail,
            "epub_ref": str(epub_path),
        }

    copy_result = _copy_epub_for_gateway_if_configured(epub_path)
    if not bool(copy_result.get("success")):
        return {
            "status": "FAILED",
            "success": False,
            "platform": platform,
            "conversation_id": conversation_id,
            "reason_code": str(copy_result.get("reason_code") or "LIGHTNOVEL_EPUB_EDGE_COPY_FAILED"),
            "detail": str(copy_result.get("detail") or ""),
            "epub_ref": str(epub_path),
            "copy_result": copy_result,
        }

    wait_result = _parse_bool_env("HOST_LIGHTNOVEL_EPUB_GATEWAY_WAIT_RESULT", default=True)
    timeout_ms = _parse_int_env(
        "HOST_LIGHTNOVEL_EPUB_GATEWAY_TIMEOUT_MS",
        default=90_000,
        min_value=3_000,
        max_value=180_000,
    )
    media_path = str(copy_result.get("gateway_media_path") or epub_path)
    try:
        file_reply = _post_gateway_reply_sync(
            platform=platform,
            conversation_id=conversation_id,
            content="",
            msg_type="file",
            media_path=media_path,
            wait_result=wait_result,
            timeout_ms=timeout_ms,
            raw_extra={
                "request_id": str(order.metadata.get("request_id") or "").strip(),
                "work_order_id": order.work_order_id,
                "job_id": job_id,
                "reply_source": "agent_epub_builder",
                "workflow_owner": str(order.input_pack.get("workflow_owner") or "natsume"),
                "epub_ref": str(epub_path),
                "file_name": epub_path.name,
            },
        )
        file_result = file_reply.as_dict() if hasattr(file_reply, "as_dict") else dict(getattr(file_reply, "__dict__", {}) or {})
        notice_result: dict[str, Any] | None = None
        notice_content = ""
        if _parse_bool_env("HOST_LIGHTNOVEL_EPUB_GATEWAY_SEND_NOTICE", default=True):
            notice_content = _build_lightnovel_epub_gateway_notice(result, epub_path)
            notice_reply = _post_gateway_reply_sync(
                platform=platform,
                conversation_id=conversation_id,
                content=notice_content,
                msg_type="text",
                wait_result=wait_result,
                timeout_ms=min(timeout_ms, 30_000),
                raw_extra={
                    "request_id": str(order.metadata.get("request_id") or "").strip(),
                    "work_order_id": order.work_order_id,
                    "job_id": job_id,
                    "reply_source": "agent_epub_builder.notice",
                    "workflow_owner": str(order.input_pack.get("workflow_owner") or "natsume"),
                    "epub_ref": str(epub_path),
                },
            )
            notice_result = (
                notice_reply.as_dict()
                if hasattr(notice_reply, "as_dict")
                else dict(getattr(notice_reply, "__dict__", {}) or {})
            )
        delivery = {
            "status": "SENT" if bool(file_result.get("success")) else "FAILED",
            "success": bool(file_result.get("success")),
            "platform": platform,
            "conversation_id": conversation_id,
            "wait_result": wait_result,
            "epub_ref": str(epub_path),
            "media_path": media_path,
            "copy_result": copy_result,
            "gateway_result": file_result,
            "notice_result": notice_result,
        }
        conversation_turn_projections = [
            _record_gateway_visible_turn(
                order=order,
                request_id=f"{job_id}::epub::file",
                platform=platform,
                conversation_id=conversation_id,
                content="",
                msg_type="file",
                media_path=media_path,
                source_kind="agent_epub_builder.file",
                gateway_result=file_result,
                refs={
                    "job_id": job_id,
                    "work_order_id": order.work_order_id,
                    "epub_ref": str(epub_path),
                    "file_name": epub_path.name,
                    "message_part": "file",
                },
                metadata={
                    "job_id": job_id,
                    "work_order_id": order.work_order_id,
                    "epub_ref": str(epub_path),
                    "file_name": epub_path.name,
                    "message_part": "file",
                },
            )
        ]
        if notice_result:
            conversation_turn_projections.append(
                _record_gateway_visible_turn(
                    order=order,
                    request_id=f"{job_id}::epub::notice",
                    platform=platform,
                    conversation_id=conversation_id,
                    content=notice_content,
                    msg_type="text",
                    source_kind="agent_epub_builder.notice",
                    gateway_result=notice_result,
                    refs={
                        "job_id": job_id,
                        "work_order_id": order.work_order_id,
                        "epub_ref": str(epub_path),
                        "file_name": epub_path.name,
                        "message_part": "notice",
                    },
                    metadata={
                        "job_id": job_id,
                        "work_order_id": order.work_order_id,
                        "epub_ref": str(epub_path),
                        "file_name": epub_path.name,
                        "message_part": "notice",
                    },
                )
            )
        delivery["conversation_turn_projection"] = conversation_turn_projections
        _append_production_memory_event(
            order=order,
            source_kind="agent_epub_builder",
            event_kind="lightnovel_epub_gateway_delivery",
            status=str(delivery.get("status") or ""),
            summary=f"Lightnovel EPUB Gateway delivery {delivery.get('status')}; epub={epub_path.name}",
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "epub_ref": str(epub_path),
                "file_name": epub_path.name,
                "media_path": media_path,
                "platform": platform,
                "conversation_id": conversation_id,
            },
            causality={"gateway_result": file_result, "notice_result": notice_result or {}, "copy_result": copy_result, "conversation_turn_projection": conversation_turn_projections},
            salience=0.76,
            valence=0.28 if bool(file_result.get("success")) else -0.25,
        )
        return delivery
    except Exception as exc:  # pragma: no cover - external Gateway guard
        failure = {
            "status": "FAILED",
            "success": False,
            "platform": platform,
            "conversation_id": conversation_id,
            "reason_code": "LIGHTNOVEL_EPUB_GATEWAY_PUSH_FAILED",
            "detail": str(exc),
            "epub_ref": str(epub_path),
            "copy_result": copy_result,
        }
        _append_production_memory_event(
            order=order,
            source_kind="agent_epub_builder",
            event_kind="lightnovel_epub_gateway_delivery",
            status="FAILED",
            summary=f"Lightnovel EPUB Gateway delivery failed: {str(exc)[:180]}",
            refs={
                "job_id": job_id,
                "work_order_id": order.work_order_id,
                "epub_ref": str(epub_path),
                "platform": platform,
                "conversation_id": conversation_id,
            },
            causality={"reason_code": "LIGHTNOVEL_EPUB_GATEWAY_PUSH_FAILED", "detail": str(exc)[:300], "copy_result": copy_result},
            salience=0.72,
            valence=-0.36,
        )
        return failure

# Tests may inject a repository-backed verifier. Production always performs
# the durable command/attempt/grant lookup below.
_PRODUCTION_AUTHORITY_VERIFIER_OVERRIDE: Callable[[Mapping[str, str], AgentWorkOrder], str | None] | None = None




__all__ = [
    "deliver_browser_research", "deliver_lightnovel_epub", "deliver_lightnovel_recommendations",
]
deliver_browser_research = _maybe_push_browser_research_to_gateway
deliver_lightnovel_epub = _maybe_push_lightnovel_epub_to_gateway
deliver_lightnovel_recommendations = _maybe_push_lightnovel_recommendations_to_gateway
