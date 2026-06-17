"""Async enqueue executor for Agent production skills."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
from host_brain.core.subprocess_utils import run as _subprocess_run
import threading
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

from host_brain.agent_runtime.artifact_store import AgentArtifactStore
from host_brain.agent_runtime.browser_researcher import execute_browser_research_work_order
from host_brain.agent_runtime.executor import AgentRuntimeExecutor
from host_brain.agent_runtime.lightnovel_epub_builder import execute_lightnovel_epub_work_order
from host_brain.agent_runtime.production_studio import build_persona_boundary_contract
from host_brain.agent_runtime.result_contract import AgentResult
from host_brain.agent_runtime.work_order import AgentWorkOrder
from host_brain.core.ids import new_event_id, now_ms

logger = logging.getLogger(__name__)

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"


@dataclass(slots=True)
class AgentProductionJob:
    job_id: str
    work_order_id: str
    skill_id: str
    status: str
    artifacts_dir: str
    result_contract_ref: str
    request_id: str | None = None
    detail: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    created_ts_ms: int = field(default_factory=now_ms)
    updated_ts_ms: int = field(default_factory=now_ms)
    finished_ts_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_JOBS_LOCK = threading.RLock()
_JOBS: dict[str, AgentProductionJob] = {}


def _result_contract(
    *,
    status: str,
    order: AgentWorkOrder,
    run_dir: Path,
    detail: str = "",
    error: str | None = None,
    result: Mapping[str, Any] | None = None,
) -> AgentResult:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    metadata = {
        "skill_id": skill_id,
        "agent_production": True,
        "agent_background": True,
        "surface_main_chain_required": True,
        "persona_boundary": dict(order.metadata.get("persona_boundary") or build_persona_boundary_contract()),
        "result_contract": dict(order.metadata.get("result_contract") or {}),
        "trigger_policy": dict(order.metadata.get("trigger_policy") or {}),
    }
    if result:
        metadata["worker_result_status"] = str(result.get("status") or "")
        metadata["worker_reason_code"] = result.get("reason_code")
    return AgentResult(
        status=status,
        work_order_id=order.work_order_id,
        summary=f"Agent production WorkOrder {status.lower()}.",
        result_text="",
        artifacts_dir=str(run_dir),
        verification={"status": "NOT_RUN", "reason": "background production queue"},
        risk_flags=[] if not error else ["agent_production_error"],
        review_required=False,
        detail=detail or None,
        reason_code=None if not error else "AGENT_PRODUCTION_ERROR",
        metadata=metadata,
    )


def _persist_job(store: AgentArtifactStore, run_dir: Path, job: AgentProductionJob) -> None:
    store.write_json(run_dir / "production_job.json", job.to_dict())


def _set_job(job_id: str, **fields: Any) -> AgentProductionJob | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        for name, value in fields.items():
            if hasattr(job, name):
                setattr(job, name, value)
        job.updated_ts_ms = now_ms()
        if job.status in {STATUS_COMPLETED, STATUS_FAILED} and job.finished_ts_ms is None:
            job.finished_ts_ms = job.updated_ts_ms
        return AgentProductionJob(**job.to_dict())


def _resolve_order_chat_context(order: AgentWorkOrder) -> dict[str, str]:
    metadata = dict(order.metadata or {})
    request_id = str(metadata.get("request_id") or "").strip()
    context = {
        "request_id": request_id,
        "intent_id": str(metadata.get("intent_id") or "").strip(),
        "session_id": str(metadata.get("session_id") or "").strip(),
        "person_id": str(metadata.get("person_id") or "").strip(),
    }
    if not request_id:
        return context
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        rows = list(getattr(get_ledger(), "intent_log", []) or [])
    except Exception:
        rows = []
    for raw in reversed(rows):
        row = dict(raw) if isinstance(raw, Mapping) else {}
        if str(row.get("request_id") or "").strip() != request_id:
            continue
        context["intent_id"] = context["intent_id"] or str(row.get("intent_id") or "").strip()
        context["session_id"] = context["session_id"] or str(row.get("session_id") or "").strip()
        normalized_raw = row.get("normalized_json")
        if isinstance(normalized_raw, str):
            try:
                normalized = json.loads(normalized_raw)
            except Exception:
                normalized = {}
        else:
            normalized = dict(normalized_raw) if isinstance(normalized_raw, Mapping) else {}
        context["person_id"] = context["person_id"] or str(normalized.get("person_id") or "").strip()
        context["session_id"] = context["session_id"] or str(normalized.get("session_id") or "").strip()
        break
    return context


def _append_production_memory_event(
    *,
    order: AgentWorkOrder,
    source_kind: str,
    event_kind: str,
    status: str,
    summary: str,
    refs: Mapping[str, Any] | None = None,
    causality: Mapping[str, Any] | None = None,
    salience: float = 0.55,
    valence: float = 0.0,
    uncertainty: float = 0.08,
    created_at_ms: int | None = None,
) -> dict[str, Any]:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.app.memory_event_store import MemoryEventStore  # noqa: PLC0415

        context = _resolve_order_chat_context(order)
        ledger = get_ledger()
        store = MemoryEventStore(ledger)
        event = store.append_event(
            person_id=str(context.get("person_id") or "default").strip() or "default",
            session_id=str(context.get("session_id") or "").strip(),
            conversation_id=str(context.get("session_id") or "").strip(),
            source_kind=source_kind,
            event_kind=event_kind,
            visibility="system",
            role="",
            content_summary=_short_text(summary, limit=1000),
            causality={
                "status": str(status or "").strip(),
                **dict(causality or {}),
            },
            refs={
                "request_id": str(context.get("request_id") or order.metadata.get("request_id") or "").strip(),
                "intent_id": str(context.get("intent_id") or order.metadata.get("intent_id") or "").strip(),
                "work_order_id": order.work_order_id,
                "skill_id": str(order.metadata.get("skill_id") or "").strip(),
                **dict(refs or {}),
            },
            salience=salience,
            valence=valence,
            uncertainty=uncertainty,
            confidence=1.0,
            created_at_ms=int(created_at_ms or now_ms()),
        )
        life = store.project_life_experience_from_event(str(event.get("event_id") or ""), consume=True)
        if life.get("event_id"):
            store.merge_event_refs(str(event.get("event_id") or ""), {"life_experience_event_id": life.get("event_id")})
        store.update_projection_status(
            str(event.get("event_id") or ""),
            {"life_experience": {"status": life.get("status", "OK"), "event_id": life.get("event_id")}},
        )
        return {"status": "OK", "memory_event_id": event.get("event_id"), "life_experience_event_id": life.get("event_id")}
    except Exception as exc:
        return {"status": "FAILED", "reason_code": type(exc).__name__, "detail": str(exc)[:160]}


def _gateway_result_success(payload: Mapping[str, Any] | None) -> bool:
    if not isinstance(payload, Mapping):
        return False
    status = str(payload.get("status") or "").strip().lower()
    return bool(payload.get("success", True)) and status not in {"failed", "error"}


def _conversation_session_id_for_gateway(*, context: Mapping[str, str], platform: str, conversation_id: str) -> str:
    session_id = str(context.get("session_id") or "").strip()
    if session_id:
        return session_id
    clean_conversation = str(conversation_id or "").strip()
    if not clean_conversation:
        return ""
    if clean_conversation.startswith(("qq_", "wx_", "telegram_", "discord_", "feishu_", "wecom_")):
        return clean_conversation
    if str(platform or "").strip().lower() == "qq":
        return f"qq_dm_{clean_conversation}"
    return clean_conversation


def _visible_gateway_content(*, content: Any, msg_type: str, media_path: str = "") -> str:
    text_value = str(content or "").strip()
    if text_value:
        return text_value
    clean_type = str(msg_type or "text").strip().lower() or "text"
    clean_media = str(media_path or "").strip()
    if clean_media:
        return f"[{clean_type}:{Path(clean_media).name or clean_media}]"
    if clean_type and clean_type != "text":
        return f"[{clean_type}:message]"
    return ""


def _record_gateway_visible_turn(
    *,
    order: AgentWorkOrder,
    request_id: str,
    platform: str,
    conversation_id: str,
    content: Any,
    msg_type: str = "text",
    media_path: str = "",
    source_kind: str,
    gateway_result: Mapping[str, Any] | None,
    refs: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    created_at_ms: int | None = None,
) -> dict[str, Any]:
    if not _gateway_result_success(gateway_result):
        return {"status": "SKIPPED", "reason_code": "GATEWAY_NOT_SENT"}
    context = _resolve_order_chat_context(order)
    session_id = _conversation_session_id_for_gateway(context=context, platform=platform, conversation_id=conversation_id)
    visible_content = _visible_gateway_content(content=content, msg_type=msg_type, media_path=media_path)
    if not session_id or not visible_content:
        return {"status": "SKIPPED", "reason_code": "VISIBLE_GATEWAY_CONTEXT_MISSING"}
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415
        from host_brain.app.memory_event_store import MemoryEventStore  # noqa: PLC0415

        store = MemoryEventStore(get_ledger())
        return store.append_visible_assistant_message(
            request_id=str(request_id or "").strip() or f"gateway_visible_{new_event_id()}",
            session_id=session_id,
            person_id=str(context.get("person_id") or "").strip() or "default",
            content=visible_content,
            ts_ms=int(created_at_ms or now_ms()),
            source_kind=source_kind,
            refs={
                "gateway_message": True,
                "platform": platform,
                "conversation_id": conversation_id,
                **dict(refs or {}),
            },
            metadata={
                "message_kind": "final_reply",
                "reply_source": source_kind,
                "reply_channel": platform,
                "reply_target": conversation_id,
                "msg_type": str(msg_type or "text").strip().lower(),
                "media_path": str(media_path or "").strip(),
                "gateway_result": dict(gateway_result or {}),
                **dict(metadata or {}),
            },
            lang="zh",
        )
    except Exception as exc:
        return {"status": "FAILED", "reason_code": type(exc).__name__, "detail": str(exc)[:160]}


def _persist_production_job_memory(
    *,
    job: AgentProductionJob,
    order: AgentWorkOrder,
    result: Mapping[str, Any] | None = None,
    error: str | None = None,
) -> None:
    try:
        from host_brain.app.observability_persist import persist_background_event_memory  # noqa: PLC0415
    except Exception:
        persist_background_event_memory = None

    skill_id = str(order.metadata.get("skill_id") or job.skill_id or "").strip()
    result_map = dict(result or {})
    reason_code = str(result_map.get("reason_code") or job.error or error or "").strip()
    result_text = str(result_map.get("result_text") or "").strip()
    context = _resolve_order_chat_context(order)
    detail_parts = [
        f"Agent production job {skill_id or job.work_order_id} status={job.status}",
        f"summary={str(result_map.get('summary') or job.detail or '').strip()}",
        f"reason={reason_code}" if reason_code else "",
        f"result_text={result_text[:600]}" if result_text else "",
        f"artifacts={job.artifacts_dir}",
    ]
    detail = "；".join(part for part in detail_parts if part)
    _append_production_memory_event(
        order=order,
        source_kind="agent_production",
        event_kind="agent_production_job",
        status=job.status,
        summary=detail,
        refs={
            "job_id": job.job_id,
            "work_order_id": job.work_order_id,
            "skill_id": skill_id,
            "artifacts_dir": job.artifacts_dir,
            "result_contract_ref": job.result_contract_ref,
        },
        causality={
            "reason_code": reason_code,
            "result_status": str(result_map.get("status") or ""),
            "result_summary": str(result_map.get("summary") or job.detail or "").strip(),
        },
        salience=0.72 if job.status in {STATUS_COMPLETED, STATUS_FAILED} else 0.48,
        valence=-0.35 if job.status == STATUS_FAILED else 0.2 if job.status == STATUS_COMPLETED else 0.0,
        uncertainty=0.06,
        created_at_ms=int(job.finished_ts_ms or job.updated_ts_ms or now_ms()),
    )
    if persist_background_event_memory is None:
        return
    try:
        persist_background_event_memory(
            event_source="agent_production",
            event_id=job.job_id,
            request_id=context.get("request_id") or job.request_id,
            intent_id=context.get("intent_id") or None,
            person_id=context.get("person_id") or None,
            session_id=context.get("session_id") or None,
            task_id=skill_id or job.work_order_id,
            status=job.status,
            reason_code=reason_code or None,
            detail=detail,
            ts_ms=int(job.finished_ts_ms or job.updated_ts_ms or now_ms()),
            metadata={
                "job_id": job.job_id,
                "work_order_id": job.work_order_id,
                "skill_id": skill_id,
                "artifacts_dir": job.artifacts_dir,
                "result_contract_ref": job.result_contract_ref,
                "result_status": str(result_map.get("status") or ""),
                "result_summary": str(result_map.get("summary") or job.detail or "").strip(),
                "result_metadata": dict(result_map.get("metadata") or {}) if isinstance(result_map.get("metadata"), Mapping) else {},
            },
        )
    except Exception:
        return


def get_agent_production_job(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(str(job_id or "").strip())
        return None if job is None else job.to_dict()


def list_agent_production_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with _JOBS_LOCK:
        jobs = [job.to_dict() for job in _JOBS.values()]
    jobs.sort(key=lambda item: int(item.get("updated_ts_ms") or 0), reverse=True)
    return jobs[: max(1, int(limit))]


def _validate_production_boundary(order: AgentWorkOrder) -> None:
    metadata = dict(order.metadata or {})
    if not bool(metadata.get("agent_production")):
        raise ValueError("agent production executor requires metadata.agent_production=true")
    boundary = metadata.get("persona_boundary")
    if not isinstance(boundary, Mapping):
        raise ValueError("agent production WorkOrder requires metadata.persona_boundary")
    if str(boundary.get("surface_owner") or "") != "host_main_persona":
        raise ValueError("persona_boundary.surface_owner must be host_main_persona")
    if not bool(metadata.get("surface_main_chain_required")):
        raise ValueError("agent production WorkOrder must require surface main chain rendering")
    forbidden = set(order.forbidden_actions or [])
    for action in {
        "write_long_term_memory",
        "write_bond_state",
        "write_persona_core",
        "send_external_message",
    }:
        if action not in forbidden:
            raise ValueError(f"agent production WorkOrder must forbid {action}")


def _purchase_confirmation_missing(order: AgentWorkOrder) -> bool:
    policy = dict(order.approval_policy or {})
    return (
        bool(policy.get("require_confirm"))
        and not bool(policy.get("confirmed"))
        and str(policy.get("confirm_scope") or "").strip() == "spend_account_points"
    )


def _queued_detail(*, order: AgentWorkOrder, blocked_until_confirmation: bool, start_background: bool) -> str:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id == "agent_epub_builder" and blocked_until_confirmation:
        return "Agent EPUB purchase plan queued; waiting for explicit purchase confirmation before starting worker."
    if start_background:
        return "Agent production worker queued."
    return "Agent production worker queued without background start."


def _queued_result_text(*, order: AgentWorkOrder, blocked_until_confirmation: bool, start_background: bool) -> str:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id == "agent_epub_builder":
        if blocked_until_confirmation:
            return (
                "需要登录，但不要把账号、密码、验证码或 Cookie 发到聊天里。"
                "已创建 EPUB 购买计划任务；在你通过本地浏览器完成登录会话交接，并明确确认书名、链接和最高积分前，"
                "后台 worker 不会启动，也不会点击购买或消耗积分。"
            )
        return (
            "已创建 EPUB 封装任务。它只会使用你的本地已登录浏览器会话或授权内容，"
            "不会要求你把明文账号密码发到聊天里。"
        )
    if skill_id == "agent_lightnovel_recommender":
        return "已创建轻小说推荐任务，只采集多个公开榜单/最新资源的标题、封面、简介和来源，不读取付费正文。"
    if skill_id == "browser_research":
        return "已创建网页资料研究任务，只读取公开网页或你提供的本地材料；完成后会生成来源明确的研究简报。"
    if start_background:
        return "已创建后台生产技能任务，结果会通过主链重新整理后再给用户。"
    return "已创建生产技能任务，当前仅排队落盘，未启动后台 worker。"


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
    try:
        from host_brain.app.user_preference_store import build_lightnovel_dynamic_taste_profile

        context = _resolve_order_chat_context(order)
        return build_lightnovel_dynamic_taste_profile(
            base_profile,
            person_id=str(context.get("person_id") or "").strip(),
        )
    except Exception:
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


def _looks_like_content_image(url: str) -> bool:
    lower = str(url or "").strip().lower()
    if not lower.startswith(("http://", "https://")):
        return False
    if "res.lightnovel.fun/images/" in lower:
        return any(token in lower for token in (".jpg", ".jpeg", ".png", ".webp"))
    if "i.noire.cc/image/" in lower:
        return True
    return False


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


def _short_text(value: Any, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").strip().split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)]}…"


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
    *,
    job_id: str,
    order: AgentWorkOrder,
    ranked_items: list[dict[str, Any]],
    max_items: int,
    gateway_delivery: Mapping[str, Any],
    message: str,
    cards: list[dict[str, str]],
) -> dict[str, Any]:
    if not _parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_DELIVERY_MEMORY_ENABLED", default=True):
        return {"status": "SKIPPED", "reason_code": "LIGHTNOVEL_DELIVERY_MEMORY_DISABLED"}
    if not bool(gateway_delivery.get("success")):
        return {"status": "SKIPPED", "reason_code": "LIGHTNOVEL_DELIVERY_NOT_SUCCESSFUL"}

    platform = str(gateway_delivery.get("platform") or "").strip()
    conversation_id = str(gateway_delivery.get("conversation_id") or "").strip()
    memory_items = _lightnovel_memory_items(ranked_items, max_items=max_items)
    detail = _lightnovel_delivery_memory_detail(
        ranked_items,
        max_items=max_items,
        platform=platform,
        conversation_id=conversation_id,
    )
    if not detail:
        return {"status": "SKIPPED", "reason_code": "LIGHTNOVEL_DELIVERY_MEMORY_EMPTY"}

    context = _resolve_order_chat_context(order)
    session_id = context.get("session_id") or conversation_id
    person_id = context.get("person_id") or None
    request_id = context.get("request_id") or str(order.metadata.get("request_id") or job_id)
    try:
        from host_brain.app.observability_persist import persist_life_action_event_memory  # noqa: PLC0415

        return persist_life_action_event_memory(
            event_source="agent_production",
            action_kind="lightnovel_recommendation_delivery",
            action_id=f"{job_id}:lightnovel_gateway_delivery",
            actor_id="natsume",
            request_id=request_id,
            intent_id=context.get("intent_id") or "agent_lightnovel_recommender",
            person_id=person_id,
            session_id=session_id,
            status="COMPLETED",
            action_summary=detail,
            ts_ms=now_ms(),
            evidence_refs=[
                f"gateway://message/{job_id}/lightnovel/overview",
                *[
                    f"gateway://message/{job_id}/lightnovel/card/{index}"
                    for index, _item in enumerate(memory_items, start=1)
                ],
            ],
            metadata={
                "domain_event_kind": "lightnovel_recommendation_delivery",
                "reason_code": "LIGHTNOVEL_RECOMMENDATION_DELIVERED",
                "platform": platform,
                "conversation_id": conversation_id,
                "selected_count": len(memory_items),
                "selected_recommendations": memory_items,
                "overview_message": message,
                "detail_messages": [str(card.get("caption") or "") for card in cards],
                "gateway_delivery": dict(gateway_delivery),
            },
        )
    except Exception as exc:  # pragma: no cover - defensive memory guard
        return {
            "status": "FAILED",
            "reason_code": "LIGHTNOVEL_DELIVERY_MEMORY_WRITE_FAILED",
            "detail": str(exc)[:160],
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
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

    async def _send() -> Any:
        return await post_gateway_reply(**kwargs)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_send())

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(_send())
        except Exception as exc:  # pragma: no cover - defensive cross-loop guard
            box["error"] = exc

    thread = threading.Thread(target=_runner, name="agent-production-gateway-send", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


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
        delivery_log_result: dict[str, Any] | None = None
        if bool(result_dict.get("success")):
            try:
                from host_brain.app.user_preference_store import (
                    LIGHTNOVEL_DOMAIN,
                    record_recommendation_delivery,
                )

                context = _resolve_order_chat_context(order)
                delivery_log_result = record_recommendation_delivery(
                    person_id=str(context.get("person_id") or "").strip() or None,
                    session_id=str(context.get("session_id") or "").strip() or conversation_id,
                    domain=LIGHTNOVEL_DOMAIN,
                    platform=platform,
                    conversation_id=conversation_id,
                    request_id=str(context.get("request_id") or order.metadata.get("request_id") or "").strip(),
                    job_id=job_id,
                    recommendations_ref=str(recommendations_path),
                    ranked_recommendations_ref=str(ranked_ref or ""),
                    items=delivery_items,
                    message_payload={
                        "gateway_result": result_dict,
                        "card_results": card_results,
                        "overview_message": message,
                    },
                )
            except Exception as pref_exc:
                delivery_log_result = {
                    "status": "FAILED",
                    "reason_code": "RECOMMENDATION_DELIVERY_LOG_FAILED",
                    "detail": str(pref_exc)[:160],
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

def _execute_worker(order: AgentWorkOrder, *, store: AgentArtifactStore, run_dir: Path) -> dict[str, Any]:
    skill_id = str(order.metadata.get("skill_id") or "").strip()
    if skill_id == "browser_research":
        return execute_browser_research_work_order(order, run_dir=run_dir)
    if skill_id == "agent_lightnovel_recommender":
        try:
            return execute_lightnovel_recommender_work_order(order, run_dir=run_dir)
        finally:
            if _parse_bool_env("HOST_LIGHTNOVEL_RECOMMENDER_BROWSER_CLOSE_AFTER_RUN", default=False):
                try:
                    from host_brain.agent_runtime.browser_automation_service import get_browser_automation_service  # noqa: PLC0415

                    get_browser_automation_service(session_id="natsume_lightnovel").close(target="browser")
                except Exception as exc:
                    logger.debug("lightnovel recommender browser close skipped: %s", exc)
    if skill_id == "agent_epub_builder":
        return execute_lightnovel_epub_work_order(order, run_dir=run_dir)
    result = AgentRuntimeExecutor(artifact_store=store).execute(order)
    return _recover_lightnovel_recommender_result(order=order, result=result, run_dir=run_dir)


def _run_background_job(
    *,
    job_id: str,
    order: AgentWorkOrder,
    store: AgentArtifactStore,
    run_dir: Path,
) -> None:
    running = _set_job(job_id, status=STATUS_RUNNING, detail="Agent production worker running.")
    if running is not None:
        _persist_job(store, run_dir, running)
        store.write_json(
            run_dir / "result_contract.json",
            _result_contract(status="RUNNING", order=order, run_dir=run_dir, detail=running.detail).to_dict(),
        )
    try:
        result = _execute_worker(order, store=store, run_dir=run_dir)
        store.write_json(run_dir / "result.json", result)
        result = _recover_lightnovel_recommender_result(order=order, result=result, run_dir=run_dir)
        store.write_json(run_dir / "result.json", result)
        final_status = STATUS_COMPLETED if str(result.get("status") or "").upper() == "EXECUTED" else STATUS_FAILED
        detail = str(result.get("summary") or "Agent production worker finished.")
        completed = _set_job(
            job_id,
            status=final_status,
            detail=detail,
            result=dict(result),
            error=None if final_status == STATUS_COMPLETED else str(result.get("detail") or result.get("reason_code") or ""),
        )
        if completed is None:
            completed = AgentProductionJob(
                job_id=job_id,
                work_order_id=order.work_order_id,
                skill_id=str(order.metadata.get("skill_id") or "").strip(),
                status=final_status,
                artifacts_dir=str(run_dir),
                result_contract_ref=str(run_dir / "result_contract.json"),
                request_id=str(order.metadata.get("request_id") or "").strip() or None,
                detail=detail,
                result=dict(result),
                error=None if final_status == STATUS_COMPLETED else str(result.get("detail") or result.get("reason_code") or ""),
                finished_ts_ms=now_ms(),
            )
            with _JOBS_LOCK:
                _JOBS[job_id] = completed
        _persist_job(store, run_dir, completed)
        store.write_json(
            run_dir / "result_contract.json",
            _result_contract(
                status="EXECUTED" if final_status == STATUS_COMPLETED else "DEGRADED",
                order=order,
                run_dir=run_dir,
                detail=detail,
                error=completed.error,
                result=result,
            ).to_dict(),
        )
        _persist_production_job_memory(job=completed, order=order, result=result)
        gateway_delivery = _maybe_push_browser_research_to_gateway(
            job_id=job_id,
            order=order,
            result=result,
            run_dir=run_dir,
        )
        if gateway_delivery is None:
            gateway_delivery = _maybe_push_lightnovel_recommendations_to_gateway(
                job_id=job_id,
                order=order,
                result=result,
                run_dir=run_dir,
            )
        if gateway_delivery is None:
            gateway_delivery = _maybe_push_lightnovel_epub_to_gateway(
                job_id=job_id,
                order=order,
                result=result,
                run_dir=run_dir,
            )
        if gateway_delivery is not None:
            updated_result = dict(result)
            updated_result["gateway_delivery"] = gateway_delivery
            delivered = _set_job(job_id, result=updated_result)
            if delivered is None:
                completed.result = updated_result
                completed.updated_ts_ms = now_ms()
                delivered = completed
            _persist_job(store, run_dir, delivered)
            store.write_json(run_dir / "result.json", updated_result)
    except Exception as exc:  # pragma: no cover - defensive background guard
        failed = _set_job(
            job_id,
            status=STATUS_FAILED,
            detail="Agent production worker crashed.",
            error=str(exc),
        )
        if failed is not None:
            _persist_job(store, run_dir, failed)
            store.write_json(
                run_dir / "result_contract.json",
                _result_contract(status="FAILED", order=order, run_dir=run_dir, detail=failed.detail, error=str(exc)).to_dict(),
            )
            _persist_production_job_memory(job=failed, order=order, error=str(exc))


def recover_interrupted_lightnovel_recommendation_jobs(
    *,
    artifacts_root: str | Path | None = None,
    limit: int = 3,
    max_age_hours: int = 72,
    push_gateway: bool = True,
) -> dict[str, Any]:
    root_value = str(
        artifacts_root
        or os.environ.get("HOST_LIGHTNOVEL_RECOMMENDER_ARTIFACTS_ROOT")
        or "artifacts/codex_runs/lightnovel_recommender"
    ).strip()
    if not root_value:
        return {"status": "SKIPPED", "reason_code": "LIGHTNOVEL_RECOVERY_ROOT_MISSING", "recovered": 0}
    root = Path(root_value).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    if not root.exists() or not root.is_dir():
        return {"status": "SKIPPED", "reason_code": "LIGHTNOVEL_RECOVERY_ROOT_NOT_FOUND", "root": str(root), "recovered": 0}

    now_value = now_ms()
    max_age_ms = max(1, int(max_age_hours)) * 60 * 60 * 1000
    job_paths = sorted(
        root.glob("*/production_job.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    recovered_count = 0
    scanned_count = 0
    results: list[dict[str, Any]] = []
    for job_path in job_paths:
        if recovered_count >= max(1, int(limit)):
            break
        run_dir = job_path.parent
        job_payload = _read_json_file(job_path)
        if not isinstance(job_payload, Mapping):
            continue
        skill_id = str(job_payload.get("skill_id") or "").strip()
        if skill_id != "agent_lightnovel_recommender":
            continue
        status = str(job_payload.get("status") or "").strip().upper()
        if status not in {STATUS_QUEUED, STATUS_RUNNING, "RECOVERING"}:
            continue
        updated_ts = int(job_payload.get("updated_ts_ms") or job_payload.get("created_ts_ms") or 0)
        if updated_ts and now_value - updated_ts > max_age_ms:
            continue
        result_payload = _read_json_file(run_dir / "result.json")
        work_order_payload = _read_json_file(run_dir / "work_order.json")
        if not isinstance(result_payload, Mapping) or not isinstance(work_order_payload, Mapping):
            continue
        if isinstance(result_payload.get("gateway_delivery"), Mapping) and bool(result_payload["gateway_delivery"].get("success")):
            continue

        scanned_count += 1
        try:
            order = AgentWorkOrder.from_mapping(dict(work_order_payload))
            recovered_result = _recover_lightnovel_recommender_result(
                order=order,
                result=result_payload,
                run_dir=run_dir,
            )
            final_status = STATUS_COMPLETED if str(recovered_result.get("status") or "").upper() == "EXECUTED" else STATUS_FAILED
            detail = str(recovered_result.get("summary") or "Recovered interrupted lightnovel recommendation job.")
            job_id = str(job_payload.get("job_id") or f"agent_job_recovered_{new_event_id()}").strip()
            gateway_delivery = None
            if push_gateway and final_status == STATUS_COMPLETED:
                gateway_delivery = _maybe_push_lightnovel_recommendations_to_gateway(
                    job_id=job_id,
                    order=order,
                    result=recovered_result,
                    run_dir=run_dir,
                )
                if gateway_delivery is not None:
                    recovered_result = dict(recovered_result)
                    recovered_result["gateway_delivery"] = gateway_delivery
            recovered_job = AgentProductionJob(
                job_id=job_id,
                work_order_id=str(job_payload.get("work_order_id") or order.work_order_id),
                skill_id=skill_id,
                status=final_status,
                artifacts_dir=str(run_dir),
                result_contract_ref=str(run_dir / "result_contract.json"),
                request_id=str(job_payload.get("request_id") or order.metadata.get("request_id") or "").strip() or None,
                detail=detail,
                result=dict(recovered_result),
                error=None if final_status == STATUS_COMPLETED else str(recovered_result.get("detail") or recovered_result.get("reason_code") or ""),
                created_ts_ms=int(job_payload.get("created_ts_ms") or now_value),
                updated_ts_ms=now_ms(),
                finished_ts_ms=now_ms(),
            )
            with _JOBS_LOCK:
                _JOBS[job_id] = recovered_job
            job_path.write_text(json.dumps(recovered_job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            (run_dir / "result.json").write_text(json.dumps(recovered_result, ensure_ascii=False, indent=2), encoding="utf-8")
            (run_dir / "result_contract.json").write_text(
                json.dumps(
                    _result_contract(
                        status="EXECUTED" if final_status == STATUS_COMPLETED else "DEGRADED",
                        order=order,
                        run_dir=run_dir,
                        detail=detail,
                        error=recovered_job.error,
                        result=recovered_result,
                    ).to_dict(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            _persist_production_job_memory(job=recovered_job, order=order, result=recovered_result)
            recovered_count += 1
            results.append(
                {
                    "job_id": job_id,
                    "work_order_id": order.work_order_id,
                    "status": final_status,
                    "gateway_delivery": gateway_delivery,
                    "artifacts_dir": str(run_dir),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "job_id": str(job_payload.get("job_id") or ""),
                    "status": "FAILED",
                    "reason_code": "LIGHTNOVEL_INTERRUPTED_JOB_RECOVERY_FAILED",
                    "detail": str(exc)[:240],
                    "artifacts_dir": str(run_dir),
                }
            )
    return {
        "status": "OK",
        "root": str(root),
        "scanned": scanned_count,
        "recovered": recovered_count,
        "results": results,
    }


def execute_agent_production_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload or {})
    raw_order = data.get("work_order")
    if not isinstance(raw_order, dict):
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_WORKORDER_REQUIRED",
            "detail": "payload.work_order is required",
        }

    artifacts_root = str(
        data.get("artifacts_root")
        or data.get("runs_root")
        or dict(raw_order.get("metadata") or {}).get("artifacts_root")
        or ""
    ).strip()
    store = AgentArtifactStore(artifacts_root or None)
    order = AgentWorkOrder.from_mapping(raw_order)
    run_dir = store.prepare_run_dir(order.work_order_id)
    try:
        order.validate()
        _validate_production_boundary(order)
    except Exception as exc:
        contract = _result_contract(status="FAILED", order=order, run_dir=run_dir, detail=str(exc), error=str(exc))
        store.write_json(run_dir / "work_order.json", order.to_dict())
        store.write_json(run_dir / "result_contract.json", contract.to_dict())
        return {
            "status": "FAILED",
            "reason_code": "AGENT_PRODUCTION_CONTRACT_INVALID",
            "detail": str(exc),
            "manifest_ref": f"agent_production::{order.work_order_id}",
            "work_order_id": order.work_order_id,
            "artifacts_dir": str(run_dir),
        }

    skill_id = str(order.metadata.get("skill_id") or "").strip()
    blocked_until_confirmation = bool(data.get("blocked_until_confirmation")) or _purchase_confirmation_missing(order)
    start_background = bool(data.get("start_background", True)) and not blocked_until_confirmation
    job_id = str(data.get("job_id") or f"agent_job_{new_event_id()}").strip()
    queued_detail = _queued_detail(
        order=order,
        blocked_until_confirmation=blocked_until_confirmation,
        start_background=start_background,
    )
    job = AgentProductionJob(
        job_id=job_id,
        work_order_id=order.work_order_id,
        skill_id=skill_id,
        status=STATUS_QUEUED,
        artifacts_dir=str(run_dir),
        result_contract_ref=str(run_dir / "result_contract.json"),
        request_id=str(order.metadata.get("request_id") or "").strip() or None,
        detail=queued_detail,
    )
    with _JOBS_LOCK:
        _JOBS[job_id] = job

    store.write_json(run_dir / "work_order.json", order.to_dict())
    store.write_json(run_dir / "result_contract.json", _result_contract(status="QUEUED", order=order, run_dir=run_dir, detail=job.detail).to_dict())
    _persist_job(store, run_dir, job)

    if start_background:
        thread = threading.Thread(
            target=_run_background_job,
            kwargs={"job_id": job_id, "order": order, "store": store, "run_dir": run_dir},
            name=f"agent-production-{job_id[:16]}",
            daemon=True,
        )
        thread.start()
    result_text = _queued_result_text(
        order=order,
        blocked_until_confirmation=blocked_until_confirmation,
        start_background=start_background,
    )

    return {
        "status": "OK",
        "result_code": "AGENT_PRODUCTION_QUEUED",
        "result_text": result_text,
        "manifest_ref": f"agent_production::{order.work_order_id}",
        "action": "enqueue",
        "job_id": job_id,
        "work_order_id": order.work_order_id,
        "skill_id": skill_id,
        "queued": True,
        "agent_background": True,
        "surface_main_chain_required": True,
        "start_background": start_background,
        "blocked_until_confirmation": blocked_until_confirmation,
        "approval_policy": dict(order.approval_policy or {}),
        "artifacts_dir": str(run_dir),
        "result_contract_ref": str(run_dir / "result_contract.json"),
        "persona_boundary": dict(order.metadata.get("persona_boundary") or {}),
        "trigger_policy": dict(order.metadata.get("trigger_policy") or {}),
        "detail": job.to_dict(),
    }


__all__ = [
    "AgentProductionJob",
    "_build_browser_research_gateway_message",
    "_build_lightnovel_epub_gateway_notice",
    "_build_lightnovel_gateway_message",
    "_coerce_recommendation_items",
    "_execute_worker",
    "_find_lightnovel_epub_file",
    "_find_lightnovel_recommendations_file",
    "_maybe_push_browser_research_to_gateway",
    "_maybe_push_lightnovel_epub_to_gateway",
    "_maybe_push_lightnovel_recommendations_to_gateway",
    "_parse_lightnovel_count",
    "_parse_lightnovel_update_age_days",
    "_rank_lightnovel_recommendations",
    "_recover_lightnovel_recommender_result",
    "execute_lightnovel_recommender_work_order",
    "execute_agent_production_payload",
    "get_agent_production_job",
    "list_agent_production_jobs",
    "recover_interrupted_lightnovel_recommendation_jobs",
]

