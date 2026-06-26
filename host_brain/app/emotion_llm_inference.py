"""方向G: LLM 情感推理级联管线（Hybrid Cascade）。

三级推理管线：
  Level 1: 关键词引擎（<1ms，现有 Phase 6 逻辑）
  Level 2: 轻量 LLM 推理（Gemini Flash，50-300ms，结构化 JSON 输出）
  Level 3: Fallback（LLM 超时/失败时回退关键词结果）

环境变量：
  HOST_EMOTION_LLM_INFERENCE_ENABLED: 主开关（默认 false）
  HOST_EMOTION_LLM_MODEL_ID: 推理用模型（默认 gemini-2.5-flash-lite）
  HOST_EMOTION_LLM_TIMEOUT_MS: LLM 推理超时毫秒（默认 300）
  HOST_EMOTION_LLM_CONFIDENCE_THRESHOLD: Level 1 跳过阈值（默认 0.7）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from host_brain.app.prompt_template_store import resolve_prompt_template_json_merged

logger = logging.getLogger(__name__)


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
    text = str(mapping.get(key, default) or "")
    if not kwargs:
        return text
    try:
        return text.format_map({str(k): "" if v is None else str(v) for k, v in kwargs.items()})
    except Exception:
        return text

# ---------------------------------------------------------------------------
# 环境变量解析
# ---------------------------------------------------------------------------

def _parse_bool_env(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int_env(name: str, *, default: int, min_value: int, max_value: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = int(raw)
        return max(min_value, min(max_value, value))
    except (TypeError, ValueError):
        return default


def _parse_float_env(name: str, *, default: float, min_value: float, max_value: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    try:
        value = float(raw)
        return max(min_value, min(max_value, value))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 有效情感标签白名单 — 导入自权威来源
# ---------------------------------------------------------------------------

from host_brain.core.emotion_constants import VALID_EMOTION_TAGS as _VALID_EMOTION_TAGS  # noqa: E402


# ---------------------------------------------------------------------------
# LLM 情感推理 Prompt 模板
# ---------------------------------------------------------------------------

def _build_inference_prompt(
    *,
    user_message: str,
    conversation_history: list[str] | None = None,
    current_emotion_tag: str = "NEUTRAL",
    mood_state: str = "steady",
) -> str:
    """构建 LLM 情感推理的用户侧 prompt。"""
    templates = _load_template_mapping("emotion_llm_inference.prompt_templates")
    parts: list[str] = []

    parts.append(
        _mapping_text(
            templates,
            "mood_state_line",
            default="",
            mood_state=mood_state,
        )
    )
    parts.append(
        _mapping_text(
            templates,
            "emotion_tag_line",
            default="",
            current_emotion_tag=current_emotion_tag,
        )
    )

    if conversation_history:
        recent = conversation_history[-6:]
        parts.append(
            "\n"
            + _mapping_text(
                templates,
                "recent_conversation_header",
                default="",
            )
        )
        for line in recent:
            parts.append(
                _mapping_text(
                    templates,
                    "recent_conversation_item",
                    default="",
                    line=line,
                )
            )

    parts.append(
        "\n"
        + _mapping_text(
            templates,
            "latest_user_message_line",
            default="",
            user_message=user_message,
        )
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM 情感推理结果数据类
# ---------------------------------------------------------------------------

@dataclass
class EmotionLlmResult:
    """Level 2 LLM 推理结果。"""
    user_emotion: str = "NEUTRAL"
    natsume_emotion: str = "NEUTRAL"
    reason: str = ""
    confidence: float = 0.5
    raw_json: dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return (
            self.error is None
            and self.user_emotion in _VALID_EMOTION_TAGS
            and self.natsume_emotion in _VALID_EMOTION_TAGS
            and 0.0 <= self.confidence <= 1.0
        )


# ---------------------------------------------------------------------------
# LLM 推理调用（Level 2）
# ---------------------------------------------------------------------------

async def infer_emotion_via_llm(
    *,
    user_message: str,
    conversation_history: list[str] | None = None,
    current_emotion_tag: str = "NEUTRAL",
    mood_state: str = "steady",
    timeout_ms: int = 300,
) -> EmotionLlmResult:
    """Level 2: 使用轻量 LLM 进行情感推理。

    通过 LangChain ChatOpenAI 调用 Gemini Flash（OpenAI 兼容接口），
    强制 JSON 输出格式，300ms 硬超时。

    Returns:
        EmotionLlmResult: 推理结果（含 user_emotion/natsume_emotion/reason/confidence）
    """
    t0 = time.monotonic()
    model_id = str(os.environ.get("HOST_EMOTION_LLM_MODEL_ID") or "gemini-2.5-flash-lite").strip()

    prompt_text = _build_inference_prompt(
        user_message=user_message,
        conversation_history=conversation_history,
        current_emotion_tag=current_emotion_tag,
        mood_state=mood_state,
    )
    templates = _load_template_mapping("emotion_llm_inference.prompt_templates")
    system_prompt = _mapping_text(
        templates,
        "system_prompt",
        default="",
    )

    try:
        # 在线程中执行同步 LLM 调用，设置硬超时
        raw_text = await asyncio.wait_for(
            asyncio.to_thread(
                _sync_llm_emotion_call,
                model_id=model_id,
                system_prompt=system_prompt,
                user_prompt=prompt_text,
                timeout_ms=timeout_ms,
            ),
            timeout=max(0.5, timeout_ms / 1000.0 + 0.2),  # 额外 200ms 缓冲
        )

        latency_ms = int((time.monotonic() - t0) * 1000)

        # 解析 JSON
        parsed = _parse_llm_emotion_json(raw_text)
        if parsed is None:
            return EmotionLlmResult(
                error=f"JSON_PARSE_FAILED:{raw_text[:80]}",
                latency_ms=latency_ms,
            )

        user_em = str(parsed.get("user_emotion") or "NEUTRAL").strip().upper()
        natsume_em = str(parsed.get("natsume_emotion") or "NEUTRAL").strip().upper()
        reason = str(parsed.get("reason") or "").strip()
        conf = 0.5
        try:
            conf = max(0.0, min(1.0, float(parsed.get("confidence") or 0.5)))
        except (TypeError, ValueError):
            pass

        # 校验标签合法性
        if user_em not in _VALID_EMOTION_TAGS:
            user_em = "NEUTRAL"
            conf = max(0.3, conf - 0.2)
        if natsume_em not in _VALID_EMOTION_TAGS:
            natsume_em = "NEUTRAL"
            conf = max(0.3, conf - 0.2)

        return EmotionLlmResult(
            user_emotion=user_em,
            natsume_emotion=natsume_em,
            reason=reason[:100],
            confidence=round(conf, 3),
            raw_json=parsed,
            latency_ms=latency_ms,
        )

    except asyncio.TimeoutError:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return EmotionLlmResult(
            error=f"TIMEOUT:{latency_ms}ms>{timeout_ms}ms",
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return EmotionLlmResult(
            error=f"{type(exc).__name__}:{str(exc)[:80]}",
            latency_ms=latency_ms,
        )


def _sync_llm_emotion_call(
    *,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    timeout_ms: int,
) -> str:
    """同步 LLM 调用（在独立线程中执行）。"""
    from host_brain.integrations.llm_helpers import (  # noqa: PLC0415
        _load_api_key_from_env,
    )

    provider_name = str(os.environ.get("HOST_EMOTION_LLM_PROVIDER") or "gemini").strip().lower()
    api_key = _load_api_key_from_env(provider_name)

    from host_brain.integrations.llm_helpers import complete_llm_with_pool_sync  # noqa: PLC0415

    base_url_override: str | None = None
    if provider_name == "gemini":
        base_url_override = str(
            os.environ.get("HOST_GEMINI_BASE_URL")
            or "https://generativelanguage.googleapis.com/v1beta/openai"
        ).strip()

    result = complete_llm_with_pool_sync(
        request_id=f"emotion_inference::{int(time.time() * 1000)}",
        scene_type="EMOTION",
        cognitive_tier="B",
        provider_name=provider_name,
        model_id=model_id,
        api_key=api_key,
        timeout_ms=timeout_ms,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        gen_params={"temperature": 0.1, "top_p": 0.8},
        response_format="json_object",
        base_url_override=base_url_override,
    )
    if result.pool_exhausted:
        raise RuntimeError("emotion llm pool exhausted")
    return str(result.content or "").strip()


def _parse_llm_emotion_json(raw_text: str) -> dict[str, Any] | None:
    """解析 LLM 返回的 JSON 文本。

    支持：
    - 纯 JSON
    - ```json ... ``` 包裹的 JSON
    - 包含前后缀文本的 JSON
    """
    text = str(raw_text or "").strip()
    if not text:
        return None

    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, TypeError):
        pass

    # 尝试提取 ```json ... ``` 包裹内容
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            try:
                result = json.loads(text[start:end].strip())
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

    # 尝试提取 { ... } 子串
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        try:
            result = json.loads(text[brace_start:brace_end + 1])
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# ---------------------------------------------------------------------------
# 三级级联管线
# ---------------------------------------------------------------------------

class EmotionInferenceCascade:
    """三级情感推理级联管线。

    Level 1 (关键词) confidence ≥ threshold → 直接采用
    Level 2 (LLM Flash) → timeout_ms 硬超时
    Level 3 (Fallback) → 使用 Level 1 结果 + 降级标记

    用法::

        cascade = EmotionInferenceCascade()
        result = await cascade.infer(
            user_message="我没事",
            keyword_result={...},          # Level 1 关键词引擎结果
            current_emotion_tag="NEUTRAL",
            mood_state="steady",
        )
    """

    def __init__(
        self,
        *,
        confidence_threshold: float | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self._confidence_threshold = (
            confidence_threshold
            if confidence_threshold is not None
            else _parse_float_env(
                "HOST_EMOTION_LLM_CONFIDENCE_THRESHOLD",
                default=0.7, min_value=0.3, max_value=1.0,
            )
        )
        self._timeout_ms = (
            timeout_ms
            if timeout_ms is not None
            else _parse_int_env(
                "HOST_EMOTION_LLM_TIMEOUT_MS",
                default=300, min_value=100, max_value=3000,
            )
        )

    async def infer(
        self,
        *,
        user_message: str,
        keyword_result: dict[str, Any],
        conversation_history: list[str] | None = None,
        current_emotion_tag: str = "NEUTRAL",
        mood_state: str = "steady",
    ) -> dict[str, Any]:
        """执行三级级联推理。

        Args:
            user_message: 用户消息文本
            keyword_result: Level 1 关键词引擎结果（必须包含 confidence 字段）
            conversation_history: 最近几轮对话文本列表
            current_emotion_tag: 当前情感标签
            mood_state: 当前心情状态

        Returns:
            增强的推理结果字典
        """
        kw_confidence = float(keyword_result.get("confidence") or 0.0)
        kw_user_emotion = str(keyword_result.get("user_emotion") or "NEUTRAL").strip().upper()
        kw_natsume_emotion = str(keyword_result.get("natsume_emotion") or current_emotion_tag).strip().upper()

        # ── Level 1: 关键词结果置信度足够高 → 直接采用 ─────────────
        if kw_confidence >= self._confidence_threshold:
            return {
                **keyword_result,
                "inference_level": 1,
                "inference_source": "keyword_engine",
                "keyword_result": dict(keyword_result),
                "latency_ms": 0,
                "llm_skipped_reason": f"keyword_confidence={kw_confidence:.2f}≥{self._confidence_threshold:.2f}",
            }

        # ── Level 2: LLM 推理 ───────────────────────────────────────
        llm_result = await infer_emotion_via_llm(
            user_message=user_message,
            conversation_history=conversation_history,
            current_emotion_tag=current_emotion_tag,
            mood_state=mood_state,
            timeout_ms=self._timeout_ms,
        )

        if llm_result.is_valid and llm_result.confidence >= 0.4:
            # LLM 推理成功且有效
            return {
                "mode": "ACTIVE",
                "inference_level": 2,
                "inference_source": "llm_flash",
                "user_emotion": llm_result.user_emotion,
                "natsume_emotion": llm_result.natsume_emotion,
                "reason": llm_result.reason,
                "confidence": llm_result.confidence,
                "keyword_result": dict(keyword_result),
                "latency_ms": llm_result.latency_ms,
                "llm_model": str(os.environ.get("HOST_EMOTION_LLM_MODEL_ID") or "gemini-2.5-flash-lite"),
                "signal_counts": keyword_result.get("signal_counts", {}),
            }

        # ── Level 3: Fallback（LLM 失败/低置信度 → 回退 Level 1）──
        fallback_reason = "LLM_LOW_CONFIDENCE"
        if llm_result.error:
            fallback_reason = f"LLM_ERROR:{llm_result.error[:50]}"
        elif not llm_result.is_valid:
            fallback_reason = "LLM_INVALID_RESULT"

        return {
            **keyword_result,
            "inference_level": 3,
            "inference_source": "keyword_fallback",
            "keyword_result": dict(keyword_result),
            "latency_ms": llm_result.latency_ms,
            "llm_fallback_reason": fallback_reason,
            "llm_raw": {
                "user_emotion": llm_result.user_emotion,
                "natsume_emotion": llm_result.natsume_emotion,
                "confidence": llm_result.confidence,
                "error": llm_result.error,
            },
        }


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def is_emotion_llm_inference_enabled() -> bool:
    """检查 LLM 情感推理是否启用。"""
    return _parse_bool_env("HOST_EMOTION_LLM_INFERENCE_ENABLED", default=False)
