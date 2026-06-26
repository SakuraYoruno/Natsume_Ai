"""Helper script to generate the executive/ sub-package files."""
import os

PKG = "host_brain/app/executive"
os.makedirs(PKG, exist_ok=True)

# ===== constants.py =====
with open(f"{PKG}/constants.py", "w", encoding="utf-8") as f:
    f.write('''"""Constants, dataclasses, and compiled regex for the executive runtime."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any

_RECENT_TURN_PATTERNS = (
    "刚刚", "上句", "上一句", "上一条",
    "刚才说", "我刚才说", "你刚才说", "我们刚才",
)
_TASK_PROGRESS_PATTERNS = (
    "进度", "做到哪", "做完了吗", "还在跑",
    "执行到哪", "任务状态", "现在怎么样", "还没好",
)
_MULTI_STEP_MARKERS = (
    "然后", "并且", "同时",
    "接着", "再把", "之后",
)
_ACTION_SPLIT_RE = re.compile(r"(?:然后|并且|同时|接着|之后|再把|再去|再来)")
_SIMPLE_TECH_ADVICE_TERMS = (
    "报错", "错误", "异常", "失败", "缺包",
    "导入", "端口", "日志", "启动",
    "连接不上", "连不上", "没响应", "无响应",
    "排查", "怎么查", "怎么排查",
    "该怎么", "怎么办", "如何", "先看", "看什么",
    "module not found", "modulenotfounderror", "no module named",
    "openai_api_key", "环境变量", "neo4j", "bolt://",
    "chroma", "chromadb", "http://", "https://",
    "why", "error", "port", "timeout",
)
_SIMPLE_TECH_ACTION_BLOCKERS = (
    "开始处理", "开始做", "继续做", "直接改", "帮我改",
    "修改", "修复", "实现", "重构", "删除", "清理",
    "整理仓库", "提交", "跑测试", "执行", "接入", "迁移", "写代码",
)
_SIMPLE_TASK_SPLIT_RE = re.compile(
    r"(?:下一步|接下来|后面|任务).{0,12}(?:拆成|分成|列成).{0,8}(?:三|3)件",
    re.IGNORECASE,
)
_SKILL_EXEC_FAIL_STATUS = {"failed", "rejected", "degraded", "error"}
_SKILL_EXEC_FAIL_CODES = {
    "SCHED_NO_CONTENT", "SKILL_EXECUTION_FAILED",
    "SKILL_EXECUTION_ERROR", "MEMORY_WRITER_UNAVAILABLE",
    "MEMORY_STORE_UNAVAILABLE",
}
_SKILL_EXEC_NO_EFFECT_MARKERS = (
    "no memory writer available", "no memory store available",
    "no memory backend available", "skipping diary",
    "no conversations on",
)

@dataclass(frozen=True)
class _RouteSelection:
    route_type: str
    target_id: str
    reason_code: str

@dataclass(frozen=True)
class _ExecutionOutcome:
    route_type: str
    target_id: str
    skill_family: str
    success: bool
    result_text: str
    error_text: str
    latency_ms: int
    payload: dict
''')

print("constants.py written")

# ===== text_utils.py =====
with open(f"{PKG}/text_utils.py", "w", encoding="utf-8") as f:
    f.write('''"""Text and numeric utility functions for the executive runtime."""
from __future__ import annotations
import asyncio
import os
import re
import threading
from typing import Any
from host_brain.app.executive.constants import (
    _SKILL_EXEC_FAIL_CODES, _SKILL_EXEC_FAIL_STATUS, _SKILL_EXEC_NO_EFFECT_MARKERS,
)

def _parse_bool_env(*, name: str, default: bool) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return bool(default)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)

def _parse_int_env(*, name: str, default: int, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(raw)
    except Exception:
        return int(default)
    return max(int(minimum), min(int(maximum), value))

def _normalize_text(value: Any, *, lower: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lower else text

def _run_awaitable_sync(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    result_box: dict = {}
    error_box: dict = {}
    done = threading.Event()
    def _worker() -> None:
        try:
            result_box["value"] = asyncio.run(awaitable)
        except BaseException as exc:
            error_box["error"] = exc
        finally:
            done.set()
    thread = threading.Thread(target=_worker, name="memory-skill-awaitable", daemon=True)
    thread.start()
    done.wait()
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value")

def _coerce_mapping(value: Any) -> dict:
    return dict(value or {}) if isinstance(value, dict) else {}

def _iter_candidate_keys() -> tuple:
    return ("route", "target_id", "action")

def _flatten_result_text(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, (int, float, bool)):
        return str(payload)
    if isinstance(payload, list):
        parts = [_flatten_result_text(item) for item in payload]
        return "\n".join(part for part in parts if part)
    if isinstance(payload, dict):
        for key in ("result_text", "result", "detail", "message", "summary", "output", "text"):
            text = _flatten_result_text(payload.get(key))
            if text:
                return text
        narrative = payload.get("narrative")
        if isinstance(narrative, dict):
            text = _flatten_result_text(narrative.get("text"))
            if text:
                return text
        return str(payload)
    return str(payload)

def _format_elapsed(elapsed_ms: int) -> str:
    seconds = max(0, int(elapsed_ms // 1000))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"

def _extract_result_code(error_text: str | None) -> str | None:
    text = str(error_text or "").strip()
    if not text:
        return None
    match = re.match(r"([A-Z0-9_]+)\s*:", text)
    if match:
        return match.group(1).strip()
    if text.isupper() and " " not in text:
        return text
    return None

def _is_effective_skill_success(*, raw_status: str, raw_code: str, raw_result: Any, result_text: str) -> bool:
    status_value = _normalize_text(raw_status, lower=True)
    if status_value in _SKILL_EXEC_FAIL_STATUS:
        return False
    code_value = _normalize_text(raw_code).upper()
    if code_value in _SKILL_EXEC_FAIL_CODES:
        return False
    digest = f"{str(raw_result or '')} {str(result_text or '')}".lower()
    if any(marker in digest for marker in _SKILL_EXEC_NO_EFFECT_MARKERS):
        return False
    return True
''')

print("text_utils.py written")

# ===== memory_context.py =====
with open(f"{PKG}/memory_context.py", "w", encoding="utf-8") as f:
    f.write('''"""Memory execution context builder for the executive runtime."""
from __future__ import annotations
import logging
from typing import Any
from host_brain.app.executive.text_utils import _normalize_text, _run_awaitable_sync

_logger = logging.getLogger("Natsume.executive_runtime_core")

def _build_memory_exec_context(
    *, request_id: str, session_id: str, intent_id: str, emotion_context: dict,
) -> dict:
    from host_brain.app.dependencies import get_episodic_store, get_semantic_recaller
    try:
        episodic_store = get_episodic_store()
    except Exception as exc:
        episodic_store = None
        _logger.debug("memory skill episodic_store unavailable: %s", exc)
    try:
        semantic_recaller = get_semantic_recaller()
    except Exception as exc:
        semantic_recaller = None
        _logger.debug("memory skill semantic_recaller unavailable: %s", exc)
    if episodic_store is None and semantic_recaller is None:
        return {}
    default_emotion_tag = _normalize_text(dict(emotion_context or {}).get("emotion_tag")).upper() or "NEUTRAL"

    def _episodic_writer(*, content: str, tags=None, importance=None, source=None,
                         emotion_tag=None, session_id=None) -> str:
        _ = importance
        if episodic_store is None:
            raise RuntimeError("episodic store unavailable")
        evidence_refs = [f"request:{request_id}"]
        session_ref = str(session_id or "").strip()
        if session_ref:
            evidence_refs.append(f"session:{session_ref}")
        source_ref = str(source or "").strip()
        if source_ref:
            evidence_refs.append(f"source:{source_ref}")
        fo
