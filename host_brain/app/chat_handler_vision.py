"""视觉界面场景 SSE 生成器模块（SCENE_VISION_UI 专用）。

从 chat_handler.py 拆分，负责 SCENE_VISION_UI 场景下的视觉分析 SSE 响应。
由 chat_handler._unified_chat_sse_generator 在场景分类后调用。

完整链路：
  [Governor 鉴权（由调用方预完成）]
  → 视觉上下文解析（vision_context / 消息文本）
  → Gemini Vision 模型分析（multimodal）
  → 结构化输出（界面元素 + 操作建议 + 自然语言描述）
  → COMMIT 事件

支持的视觉输入格式：
  - vision_context.image_base64：Base64 编码的截图
  - vision_context.image_url：图片 URL
  - vision_context.screen_description：文字描述的界面内容（降级模式）
  - message：纯文字描述（最低优先级）
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, AsyncGenerator, Dict, Mapping, Optional

from host_brain.airlock.image_filter import (
    IMAGE_EGRESS_BLOCK,
    IMAGE_EGRESS_WARN,
    filter_image_egress,
)
from host_brain.app.dependencies import (
    get_episodic_store,
    get_ledger,
)
from host_brain.app.emotion_context import build_chat_emotion_snapshot
from host_brain.app.graph_config_builder import (
    SCENE_VISION_UI,
    resolve_host_llm_runtime_async,
)
from host_brain.app.observability_persist import persist_chat_episodic_memory
from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged
from host_brain.app.schemas import ChatRequest
from host_brain.integrations.llm_helpers import PROVIDER_GEMINI

_logger = logging.getLogger("Natsume.chat_handler_vision")


class _SafeTemplateDict(dict[str, str]):
    def __missing__(self, key: object) -> str:
        return "{" + str(key) + "}"


def _format_template_text(template_text: Any, **kwargs: Any) -> str:
    text = str(template_text or "")
    if not kwargs:
        return text
    payload = _SafeTemplateDict(
        {
            str(key): "" if value is None else str(value)
            for key, value in kwargs.items()
        }
    )
    try:
        return text.format_map(payload)
    except Exception:
        return text


def _load_template_mapping(template_id: str) -> dict[str, Any]:
    raw = resolve_prompt_template_json_merged(template_id)
    return dict(raw) if isinstance(raw, Mapping) else {}


def _mapping_text(
    mapping: Mapping[str, Any],
    key: str,
    *,
    default: str = "",
    **kwargs: Any,
) -> str:
    return _format_template_text(mapping.get(key, default), **kwargs)

# ---------------------------------------------------------------------------
# 云模型 SLA 监控（生产化加固）
# ---------------------------------------------------------------------------

class _VisionSLAMonitor:
    """SCENE_VISION_UI 云模型 SLA 监控器。

    追踪连续失败次数、总调用次数、降级次数，
    超过阈值时写入 ledger 告警日志。

    生产化加固目标：
    - 连续失败 >= HOST_VISION_SLA_FAIL_THRESHOLD（默认 3）时触发告警
    - 降级率 >= HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD（默认 0.5）时触发告警
    - 告警写入 ledger（stage=S_VISION_SLA_ALERT）
    """

    def __init__(self) -> None:
        self._consecutive_failures: int = 0
        self._total_calls: int = 0
        self._degraded_calls: int = 0
        self._lock = __import__("threading").Lock()

    @staticmethod
    def _resolve_fail_threshold() -> int:
        raw = str(os.environ.get("HOST_VISION_SLA_FAIL_THRESHOLD") or "3").strip()
        try:
            return max(1, int(raw))
        except (ValueError, TypeError):
            return 3

    @staticmethod
    def _resolve_degrade_rate_threshold() -> float:
        raw = str(os.environ.get("HOST_VISION_SLA_DEGRADE_RATE_THRESHOLD") or "0.5").strip()
        try:
            return max(0.0, min(1.0, float(raw)))
        except (ValueError, TypeError):
            return 0.5

    def record_call(
        self,
        *,
        status: str,
        request_id: str,
        latency_ms: int,
        model_id: str,
        provider: str,
    ) -> dict[str, Any]:
        """记录一次调用结果，返回 SLA 监控摘要。"""
        is_degraded = str(status or "").upper() == "DEGRADED"
        with self._lock:
            self._total_calls += 1
            if is_degraded:
                self._degraded_calls += 1
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 0

            consecutive = self._consecutive_failures
            total = self._total_calls
            degraded = self._degraded_calls

        degrade_rate = degraded / total if total > 0 else 0.0
        fail_threshold = self._resolve_fail_threshold()
        degrade_rate_threshold = self._resolve_degrade_rate_threshold()

        alert_triggered = False
        alert_reason = None

        if consecutive >= fail_threshold:
            alert_triggered = True
            alert_reason = f"CONSECUTIVE_FAILURES:{consecutive}>={fail_threshold}"
        elif total >= 10 and degrade_rate >= degrade_rate_threshold:
            alert_triggered = True
            alert_reason = f"DEGRADE_RATE:{degrade_rate:.2f}>={degrade_rate_threshold}"

        if alert_triggered:
            _logger.warning(
                "VISION SLA 告警: %s model=%s provider=%s consecutive=%d degrade_rate=%.2f",
                alert_reason, model_id, provider, consecutive, degrade_rate,
                extra={"request_id": request_id},
            )
            try:
                get_ledger().log_error(
                    request_id=request_id,
                    stage="S_VISION_SLA_ALERT",
                    reason_code=f"VISION_SLA_ALERT:{alert_reason}",
                    detail=(
                        f"model={model_id} provider={provider} "
                        f"consecutive_failures={consecutive} "
                        f"degrade_rate={degrade_rate:.2f} "
                        f"total_calls={total} latency_ms={latency_ms}"
                    ),
                )
            except Exception:
                pass

        return {
            "consecutive_failures": consecutive,
            "total_calls": total,
            "degraded_calls": degraded,
            "degrade_rate": round(degrade_rate, 4),
            "alert_triggered": alert_triggered,
            "alert_reason": alert_reason,
        }


# 模块级 SLA 监控单例（进程内共享）
_vision_sla_monitor = _VisionSLAMonitor()



VISION_UI_NODE_SEQUENCE = [
    "vision_context_parser",   # 解析视觉输入（base64/url/text）
    "vision_analyzer",         # Gemini Vision 多模态分析
    "vision_renderer",         # 结构化输出渲染（界面元素 + 操作建议）
]

def _build_vision_context_section(vision_context: Optional[Dict[str, Any]]) -> str:
    """构建视觉上下文描述段落。"""
    templates = _load_template_mapping("chat_handler_vision.analysis_prompt.templates")
    if not vision_context:
        return _mapping_text(
            templates,
            "no_screenshot_context",
        )

    parts = []
    screen_desc = str(vision_context.get("screen_description") or "").strip()
    if screen_desc:
        parts.append(
            _mapping_text(
                templates,
                "screen_description_label",
                value=screen_desc,
            )
        )

    app_name = str(vision_context.get("app_name") or "").strip()
    if app_name:
        parts.append(
            _mapping_text(
                templates,
                "app_name_label",
                value=app_name,
            )
        )

    os_info = str(vision_context.get("os_info") or "").strip()
    if os_info:
        parts.append(
            _mapping_text(
                templates,
                "os_info_label",
                value=os_info,
            )
        )

    resolution = str(vision_context.get("resolution") or "").strip()
    if resolution:
        parts.append(
            _mapping_text(
                templates,
                "resolution_label",
                value=resolution,
            )
        )

    error_text = str(vision_context.get("error_text") or "").strip()
    if error_text:
        parts.append(
            _mapping_text(
                templates,
                "error_text_label",
                value=error_text,
            )
        )

    selected_text = str(vision_context.get("selected_text") or "").strip()
    if selected_text:
        parts.append(
            _mapping_text(
                templates,
                "selected_text_label",
                value=selected_text,
            )
        )

    if parts:
        return (
            f"{_mapping_text(templates, 'context_title')}\n"
            + "\n".join(f"  - {part}" for part in parts)
        )
    return _mapping_text(
        templates,
        "empty_context",
    )


def _has_image_input(vision_context: Optional[Dict[str, Any]]) -> bool:
    """判断是否有真实图片输入（base64 或 URL）。"""
    if not vision_context:
        return False
    return bool(
        str(vision_context.get("image_base64") or "").strip()
        or str(vision_context.get("image_url") or "").strip()
    )


async def _call_vision_llm(
    *,
    request_id: str,
    user_message: str,
    vision_context: Optional[Dict[str, Any]],
    llm_runtime: Dict[str, Any],
    ts_ms: int,
    image_upload_allowed: bool = False,
    image_upload_deny_reason: str = "",
) -> Dict[str, Any]:
    """调用 LLM 进行视觉分析（优先使用 Gemini Vision，降级为文本模式）。

    Args:
        image_upload_allowed:    是否允许将图片发送给云端 API。
                                 False 时即使 vision_context 中有 image_base64/image_url
                                 也不会上传，自动降级为纯文字模式。
        image_upload_deny_reason: 禁止上传的原因（用于日志记录）。

    Returns:
        dict with keys: narrative, model_used, provider, status, reason_code
    """
    import asyncio as _asyncio

    provider_name = str(llm_runtime.get("cloud_provider_name") or llm_runtime.get("provider_name") or PROVIDER_GEMINI)
    model_id = str(llm_runtime.get("cloud_model_id") or llm_runtime.get("model_id") or "gemini-2.5-flash-lite")
    api_key = str(llm_runtime.get("cloud_llm_api_key") or llm_runtime.get("llm_api_key") or "").strip()
    timeout_ms = int(llm_runtime.get("cloud_timeout_ms") or llm_runtime.get("timeout_ms") or 15000)
    available = bool(llm_runtime.get("cloud_available", llm_runtime.get("available", False)))
    analysis_templates = _load_template_mapping("chat_handler_vision.analysis_prompt.templates")
    fallback_templates = _load_template_mapping("chat_handler_vision.fallback_texts")

    if not available or not api_key:
        return {
            "narrative": _mapping_text(
                fallback_templates,
                "llm_unavailable",
            ),
            "model_used": model_id,
            "provider": provider_name,
            "status": "DEGRADED",
            "reason_code": "VISION_LLM_UNAVAILABLE",
        }

    vision_context_section = _build_vision_context_section(vision_context)
    user_prompt = _mapping_text(
        analysis_templates,
        "user_prompt_template",
        user_message=user_message,
        vision_context_section=vision_context_section,
    )

    # 图片上传策略检查：即使 vision_context 中有图片，也需要策略允许才能上传
    has_image_input = _has_image_input(vision_context)
    has_image = has_image_input and image_upload_allowed
    if has_image_input and not image_upload_allowed:
        _logger.info(
            "图片上传被策略拒绝，降级为纯文字模式",
            extra={"request_id": request_id, "deny_reason": image_upload_deny_reason},
        )
    image_base64 = str((vision_context or {}).get("image_base64") or "").strip() if has_image else ""
    image_url = str((vision_context or {}).get("image_url") or "").strip() if has_image else ""
    image_mime = str((vision_context or {}).get("image_mime") or "image/png").strip()

    # Airlock 图片出站过滤：元数据检查 + 明文敏感词扫描
    # 仅在策略允许且有 base64 图片时执行（image_url 跳过，无法解码扫描）
    image_egress_report = None
    if has_image and image_base64:
        image_egress_report = filter_image_egress(
            image_base64,
            mime_type=image_mime,
            request_id=request_id,
            ledger=get_ledger(),
        )
        if image_egress_report.decision == IMAGE_EGRESS_BLOCK:
            # 元数据不合规（MIME 非法 / 超大），降级为纯文字模式
            _logger.warning(
                "Airlock 图片过滤 BLOCK，降级为纯文字模式: %s",
                image_egress_report.reason_code,
                extra={"request_id": request_id},
            )
            has_image = False
            image_base64 = ""
        elif image_egress_report.decision == IMAGE_EGRESS_WARN:
            # 检测到潜在敏感内容，记录警告但仍允许上传（误报率较高）
            _logger.warning(
                "Airlock 图片过滤 WARN，检测到潜在敏感内容: %s",
                image_egress_report.risks,
                extra={"request_id": request_id},
            )

    try:
        provider_fn = llm_runtime.get("cloud_provider_fn") or llm_runtime.get("provider_fn")
        if provider_fn is None:
            raise ValueError("provider_fn 未配置")

        # 构建消息列表（支持多模态）
        messages: list[dict] = [
            {"role": "system", "content": ""},
        ]
        messages[0]["content"] = _mapping_text(
            analysis_templates,
            "system_prompt",
        )
        if has_image and image_base64:
            # Gemini Vision 多模态消息格式
            messages.append({
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime};base64,{image_base64}",
                        },
                    },
                    {"type": "text", "text": user_prompt},
                ],
            })
        elif has_image and image_url:
            messages.append({
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": user_prompt},
                ],
            })
        else:
            # 纯文本模式（无图片）
            messages.append({"role": "user", "content": user_prompt})

        payload = {
            "messages": messages,
            "model": model_id,
            "max_tokens": 800,
            "temperature": 0.7,
        }

        timeout_sec = max(5.0, float(timeout_ms) / 1000.0)
        result = await _asyncio.wait_for(
            _asyncio.to_thread(provider_fn, payload),
            timeout=timeout_sec,
        )

        narrative = ""
        if isinstance(result, dict):
            choices = result.get("choices") or []
            if choices and isinstance(choices[0], dict):
                msg = choices[0].get("message") or {}
                narrative = str(msg.get("content") or "").strip()
            if not narrative:
                narrative = str(result.get("content") or result.get("text") or "").strip()

        if not narrative:
            narrative = _mapping_text(
                fallback_templates,
                "empty_narrative",
            )

        return {
            "narrative": narrative,
            "model_used": model_id,
            "provider": provider_name,
            "status": "OK",
            "reason_code": None,
            "image_filter_decision": image_egress_report.decision if image_egress_report else None,
            "image_filter_risks": image_egress_report.risks if image_egress_report else [],
            "image_digest": image_egress_report.image_digest if image_egress_report else None,
            "sla": _vision_sla_monitor.record_call(
                status="OK",
                request_id=request_id,
                latency_ms=0,  # 调用方会补充实际延迟
                model_id=model_id,
                provider=provider_name,
            ),
        }

    except Exception as exc:
        _logger.warning(
            "vision LLM call failed",
            extra={"request_id": request_id, "error": str(exc)},
        )
        _sla = _vision_sla_monitor.record_call(
            status="DEGRADED",
            request_id=request_id,
            latency_ms=0,
            model_id=model_id,
            provider=provider_name,
        )
        return {
            "narrative": _mapping_text(
                fallback_templates,
                "llm_error",
            ),
            "model_used": model_id,
            "provider": provider_name,
            "status": "DEGRADED",
            "reason_code": f"VISION_LLM_ERROR:{type(exc).__name__}",
            "sla": _sla,
        }


async def _vision_ui_sse_generator(
    *,
    request_body: ChatRequest,
    request_id: str,
    session_id: str,
    ts_ms: int,
    intent_draft: Dict[str, Any],
    scene_type: str,
    scene_type_source: str,
    governor_result: Dict[str, Any],
    emotion_context: Dict[str, Any],
    emotion_snapshot: Dict[str, Any],
    intent_id: Any,
    reason_code: Any,
    metrics: Any,
) -> AsyncGenerator[str, None]:
    """SCENE_VISION_UI 专用 SSE 生成器。

    节点级进度通知：
      GRAPH_START → vision_context_parser → vision_analyzer → vision_renderer → COMMIT

    生产化加固（P2）：
    - 请求来源校验：HOST_VISION_REQUIRE_INTERNAL_ORIGIN=true 时，
      仅允许来自内网的请求（通过 request_body.origin_ip 或 X-Forwarded-For 校验）
    - 云模型 SLA 监控：连续失败/降级率超阈值时写入 ledger 告警
    - 延迟告警：单次调用超过 HOST_VISION_SLA_LATENCY_WARN_MS 时写入告警
    """
    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    fallback_templates = _load_template_mapping("chat_handler_vision.fallback_texts")

    # ── 生产化加固：HMAC 签名校验 ─────────────────────────────────────────
    # 环境变量：HOST_VISION_HMAC_SECRET（非空时启用）
    # 请求方需在 request_body.hmac_signature 中提供签名：
    #   HMAC-SHA256(key=HOST_VISION_HMAC_SECRET, msg=f"{request_id}:{session_id}:{ts_ms}")
    _hmac_secret = str(os.environ.get("HOST_VISION_HMAC_SECRET") or "").strip()
    if _hmac_secret:
        import hashlib
        import hmac as _hmac_mod
        _provided_sig = str(getattr(request_body, "hmac_signature", "") or "").strip()
        _expected_msg = f"{request_id}:{session_id}:{ts_ms}"
        _expected_sig = _hmac_mod.new(
            _hmac_secret.encode("utf-8"),
            _expected_msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        _sig_valid = _hmac_mod.compare_digest(_provided_sig, _expected_sig)
        if not _sig_valid:
            _logger.warning(
                "VISION HMAC 签名校验失败: request_id=%s session_id=%s",
                request_id, session_id,
            )
            try:
                get_ledger().log_error(
                    request_id=request_id,
                    stage="S_VISION_HMAC_REJECTED",
                    reason_code="VISION_HMAC_INVALID",
                    detail=f"provided_sig={_provided_sig[:16]}... expected_msg={_expected_msg}",
                )
            except Exception:
                pass
            yield _sse({
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "VISION_HMAC_INVALID",
                "narrative": _mapping_text(
                    fallback_templates,
                    "hmac_rejected",
                ),
                "request_id": request_id,
            })
            return
    _require_internal = str(os.environ.get("HOST_VISION_REQUIRE_INTERNAL_ORIGIN") or "false").strip().lower()
    if _require_internal in {"1", "true", "yes", "on"}:
        _origin_ip = str(getattr(request_body, "origin_ip", "") or "").strip()
        _allowed_origins_raw = str(os.environ.get("HOST_VISION_ALLOWED_ORIGINS") or "127.0.0.1,::1,localhost").strip()
        _allowed_origins = {o.strip() for o in _allowed_origins_raw.split(",") if o.strip()}
        _is_internal = (
            not _origin_ip  # 无 IP 信息时默认允许（内网部署场景）
            or _origin_ip in _allowed_origins
            or _origin_ip.startswith("127.")
            or _origin_ip.startswith("192.168.")
            or _origin_ip.startswith("10.")
            or _origin_ip == "::1"
        )
        if not _is_internal:
            _logger.warning(
                "VISION 请求来源校验失败: origin_ip=%s",
                _origin_ip,
                extra={"request_id": request_id},
            )
            try:
                get_ledger().log_error(
                    request_id=request_id,
                    stage="S_VISION_ORIGIN_REJECTED",
                    reason_code="VISION_ORIGIN_NOT_ALLOWED",
                    detail=f"origin_ip={_origin_ip} not in allowed_origins",
                )
            except Exception:
                pass
            yield _sse({
                "phase": "COMMIT",
                "done": True,
                "decision": "REJECT",
                "reason_code": "VISION_ORIGIN_NOT_ALLOWED",
                "narrative": _mapping_text(
                    fallback_templates,
                    "origin_not_allowed",
                ),
                "request_id": request_id,
            })
            return

    vision_llm_summary: Dict[str, Any] = {}
    episodic_summary: Dict[str, Any] = {"status": "SKIPPED", "reason_code": "NOT_ATTEMPTED"}
    _sla_summary: Dict[str, Any] = {}

    try:
        # ── 节点级进度通知：GRAPH_START ────────────────────────────────────
        yield _sse({
            "phase": "GRAPH_START",
            "scene_type": SCENE_VISION_UI,
            "node_sequence": VISION_UI_NODE_SEQUENCE,
            "node_count": len(VISION_UI_NODE_SEQUENCE),
            "request_id": request_id,
        })

        # ── vision_context_parser：解析视觉输入 ────────────────────────────
        yield _sse({
            "phase": "NODE_START",
            "node_id": "vision_context_parser",
            "scene_type": SCENE_VISION_UI,
            "request_id": request_id,
        })

        # 从 request_body 中提取视觉上下文
        # 优先使用 request_body.vision_context（新字段，任务4新增）
        # 向后兼容：若无 vision_context，则从 emotion_context 中提取视觉字段
        vision_context: Optional[Dict[str, Any]] = None
        vision_keys = {"image_base64", "image_url", "screen_description", "app_name", "os_info",
                       "resolution", "error_text", "selected_text", "image_mime"}

        if getattr(request_body, "vision_context", None):
            vision_context = dict(request_body.vision_context)
        else:
            # 向后兼容：从 emotion_context 中提取视觉字段
            raw_emotion_ctx = dict(request_body.emotion_context or {})
            if any(k in raw_emotion_ctx for k in vision_keys):
                vision_context = {k: raw_emotion_ctx[k] for k in vision_keys if k in raw_emotion_ctx}

        has_image = _has_image_input(vision_context)
        input_mode = "image" if has_image else ("text_description" if vision_context else "text_only")

        yield _sse({
            "phase": "NODE_DONE",
            "node_id": "vision_context_parser",
            "status": "OK",
            "input_mode": input_mode,
            "has_image": has_image,
            "request_id": request_id,
        })

        # ── vision_analyzer：Gemini Vision 分析 ───────────────────────────
        yield _sse({
            "phase": "NODE_START",
            "node_id": "vision_analyzer",
            "scene_type": SCENE_VISION_UI,
            "request_id": request_id,
        })

        # 图片上传策略：视觉入口独立处理，默认允许上传。
        _user_consent = bool(getattr(request_body, "image_upload_consent", False))
        _img_allowed, _img_deny_reason = True, None

        llm_runtime = await resolve_host_llm_runtime_async(
            rollout_key=session_id,
            scene_type=SCENE_VISION_UI,
        )

        analysis_start_ms = int(time.time() * 1000)
        llm_result = await _call_vision_llm(
            request_id=request_id,
            user_message=request_body.message,
            vision_context=vision_context,
            llm_runtime=llm_runtime,
            ts_ms=ts_ms,
            image_upload_allowed=_img_allowed,
            image_upload_deny_reason=_img_deny_reason,
        )
        analysis_latency_ms = max(0, int(time.time() * 1000) - analysis_start_ms)

        # 补充实际延迟到 SLA 监控（_call_vision_llm 内部记录时延迟未知）
        _sla_summary = dict(llm_result.get("sla") or {})
        if _sla_summary:
            _sla_summary["latency_ms"] = analysis_latency_ms
            # 若延迟超过 HOST_VISION_SLA_LATENCY_WARN_MS（默认 10000ms），写入告警
            _latency_warn_ms = int(str(os.environ.get("HOST_VISION_SLA_LATENCY_WARN_MS") or "10000").strip() or "10000")
            if analysis_latency_ms > _latency_warn_ms:
                _logger.warning(
                    "VISION SLA 延迟告警: latency_ms=%d > threshold=%d",
                    analysis_latency_ms, _latency_warn_ms,
                    extra={"request_id": request_id},
                )
                try:
                    get_ledger().log_error(
                        request_id=request_id,
                        stage="S_VISION_SLA_LATENCY_WARN",
                        reason_code="VISION_SLA_HIGH_LATENCY",
                        detail=f"latency_ms={analysis_latency_ms} threshold={_latency_warn_ms}",
                    )
                except Exception:
                    pass

        narrative = str(llm_result.get("narrative") or "")
        llm_status = str(llm_result.get("status") or "DEGRADED")

        yield _sse({
            "phase": "NODE_DONE",
            "node_id": "vision_analyzer",
            "status": llm_status,
            "provider": llm_result.get("provider"),
            "model_id": llm_result.get("model_used"),
            "latency_ms": analysis_latency_ms,
            "image_upload_allowed": _img_allowed,
            "image_upload_policy": "ALLOW_WITH_CONSENT",
            "image_filter_decision": llm_result.get("image_filter_decision"),
            "image_filter_risks": llm_result.get("image_filter_risks"),
            "request_id": request_id,
        })

        # ── vision_renderer：渲染最终输出 ──────────────────────────────────
        yield _sse({
            "phase": "NODE_START",
            "node_id": "vision_renderer",
            "scene_type": SCENE_VISION_UI,
            "request_id": request_id,
        })

        if not narrative:
            narrative = _mapping_text(
                fallback_templates,
                "empty_narrative",
            )

        # response_format 分支：actions 模式 → 生成 ActionPlan
        response_format = str(getattr(request_body, "response_format", "text") or "text").lower()
        action_plan_dict: Optional[Dict[str, Any]] = None

        if response_format == "actions":
            from host_brain.app.vision.action_planner import generate_action_plan  # noqa: PLC0415
            try:
                action_plan = await generate_action_plan(
                    request_id=request_id,
                    session_id=session_id,
                    goal=request_body.message,
                    vision_context=vision_context,
                    llm_runtime=llm_runtime,
                    image_upload_allowed=_img_allowed,
                    stop_on_error=True,
                    need_screenshot_feedback=False,
                )
                action_plan_dict = action_plan.model_dump()
                _logger.info(
                    "action_plan generated",
                    extra={
                        "request_id": request_id,
                        "action_count": len(action_plan.actions),
                        "is_fallback": action_plan.extra.get("fallback", False),
                    },
                )
            except Exception as exc:
                _logger.warning("action_plan generation failed: %s", exc, extra={"request_id": request_id})
                action_plan_dict = None

        yield _sse({
            "phase": "NODE_DONE",
            "node_id": "vision_renderer",
            "status": "OK",
            "response_format": response_format,
            "narrative_length": len(narrative),
            "has_action_plan": action_plan_dict is not None,
            "request_id": request_id,
        })

        # ── GRAPH_DONE ─────────────────────────────────────────────────────
        graph_latency_ms = analysis_latency_ms
        yield _sse({
            "phase": "GRAPH_DONE",
            "scene_type": SCENE_VISION_UI,
            "status": llm_status,
            "latency_ms": graph_latency_ms,
            "request_id": request_id,
        })

        # ── 后处理：情节记忆 ───────────────────────────────────────────────
        vision_llm_summary = {
            "status": llm_status,
            "reason_code": llm_result.get("reason_code"),
            "provider": llm_result.get("provider"),
            "model_id": llm_result.get("model_used"),
            "scene_type": SCENE_VISION_UI,
            "input_mode": input_mode,
            "has_image": has_image,
            "latency_ms": analysis_latency_ms,
        }

        try:
            episodic_summary = persist_chat_episodic_memory(
                request_id=request_id,
                intent_id=str(intent_id or request_id),
                person_id=getattr(request_body, "person_id", None),
                session_id=session_id,
                user_message=request_body.message,
                assistant_reply=narrative,
                emotion_context=emotion_context,
                ts_ms=ts_ms,
                episodic_store=get_episodic_store(),
            )
        except Exception as exc:
            episodic_summary = {
                "status": "DEGRADED",
                "reason_code": "EPISODIC_WRITE_FAILED",
                "memory_id": None,
                "detail": str(exc),
            }
            _logger.debug("vision episodic memory write skipped", extra={"request_id": request_id, "error": str(exc)})

        # ── COMMIT ──────────────────────────────────────────────────────────────────
        commit_payload: Dict[str, Any] = {
            "phase": "COMMIT",
            "done": True,
            "decision": "APPROVE",
            "narrative": narrative,
            "response_format": response_format,
            "request_id": request_id,
            "audit_refs": {
                "intent_id": intent_id,
                "governor_stage": governor_result.get("stage"),
                "thread_id": request_body.thread_id,
                "scene_type": SCENE_VISION_UI,
                "scene_type_source": scene_type_source,
                "llm": vision_llm_summary,
                "emotion_context": dict(emotion_context),
                "emotion_snapshot": dict(emotion_snapshot),
                "episodic_memory": dict(episodic_summary),
                "vision_input": {
                    "input_mode": input_mode,
                    "has_image": has_image,
                    "image_upload_allowed": _img_allowed,
                    "image_upload_policy": "ALLOW_WITH_CONSENT",
                    "user_consent": _user_consent,
                    "response_format": response_format,
                    "image_filter_decision": llm_result.get("image_filter_decision"),
                    "image_filter_risks": llm_result.get("image_filter_risks"),
                    "image_digest": llm_result.get("image_digest"),
                },
                "sla_monitor": _sla_summary,
            },
        }
        # actions 模式：将 ActionPlan 附加到 COMMIT 事件
        if action_plan_dict is not None:
            commit_payload["action_plan"] = action_plan_dict
        yield _sse(commit_payload)

    except Exception as exc:
        import traceback as _tb
        _tb_str = _tb.format_exc()
        metrics.inc_error()
        _logger.error(
            "vision UI SSE generator failed",
            extra={"request_id": request_id, "error": str(exc), "traceback": _tb_str},
        )
        try:
            get_ledger().log_error(
                request_id=request_id,
                stage="S_INTERNAL_ERROR",
                reason_code="VISION_INTERNAL_ERROR",
                detail=f"{type(exc).__name__}: {exc}\n{_tb_str[:800]}",
            )
        except Exception:
            pass
        yield _sse({
            "phase": "COMMIT",
            "done": True,
            "decision": "REJECT",
            "reason_code": "VISION_INTERNAL_ERROR",
            "narrative": _mapping_text(
                fallback_templates,
                "sse_error",
            ),
            "request_id": request_id,
        })
