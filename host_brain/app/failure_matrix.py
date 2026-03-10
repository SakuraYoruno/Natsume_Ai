"""
failure_matrix.py —— 统一失败语义矩阵

定义系统所有失败事件的标准化处理（需求 6），
替代各模块分散的 try/except 处理：
- GOVERNOR_BLOCK：Governor 拦截（需求 6.1）
- EVIDENCE_EMPTY：七路召回结果为空（需求 6.2）
- MODEL_FALLBACK：主模型失败但备用模型成功（需求 6.3）
- QA_REWRITE：QA Contract 重写输出（需求 6.4）
- EDGE_UNREACHABLE：Edge 不可达（需求 6.5）
- VISION_LOW_CONFIDENCE：Vision 识别置信度低（需求 6.6）
- MODULE_TIMEOUT：任意模块超时（需求 6.7）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 枚举：失败事件类型
# ---------------------------------------------------------------------------

class FailureEvent(str, Enum):
    """系统失败事件枚举，覆盖所有需要统一处理的异常场景。"""
    GOVERNOR_BLOCK = "GOVERNOR_BLOCK"
    """Governor R1-R7 任意规则触发拦截"""
    EVIDENCE_EMPTY = "EVIDENCE_EMPTY"
    """七路召回结果为空"""
    MODEL_FALLBACK = "MODEL_FALLBACK"
    """主模型失败，已降级到备用模型"""
    QA_REWRITE = "QA_REWRITE"
    """QA Contract 检测到违规并重写输出"""
    EDGE_UNREACHABLE = "EDGE_UNREACHABLE"
    """Edge 端不可达"""
    VISION_LOW_CONFIDENCE = "VISION_LOW_CONFIDENCE"
    """Vision 识别置信度低于阈值（0.80）"""
    MODULE_TIMEOUT = "MODULE_TIMEOUT"
    """任意模块超时"""


# ---------------------------------------------------------------------------
# 数据类：失败处理结果
# ---------------------------------------------------------------------------

@dataclass
class FailureResult:
    """FailureHandler.handle() 的返回值。"""
    event: FailureEvent
    """触发的失败事件"""
    user_response: str
    """向用户展示的标准化响应文本"""
    audit_tag: str
    """写入 triptych 的审计标签"""
    should_abort: bool = True
    """是否中止当前链路（False 表示可以继续，如 MODEL_FALLBACK）"""
    degraded_mode: Optional[str] = None
    """降级模式标识（如 'text_only'、'conservative_answer'）"""
    meta: Dict[str, Any] = field(default_factory=dict)
    """附加元数据（写入响应 meta 字段）"""


# ---------------------------------------------------------------------------
# 失败处理规则定义
# ---------------------------------------------------------------------------

# 各事件的标准化用户响应文本（不暴露内部规则细节）
USER_RESPONSES: Dict[FailureEvent, str] = {
    FailureEvent.GOVERNOR_BLOCK: (
        "抱歉，我无法处理这个请求。"
    ),
    FailureEvent.EVIDENCE_EMPTY: (
        "我目前没有足够的信息来回答这个问题，请提供更多背景。"
    ),
    FailureEvent.MODEL_FALLBACK: (
        # MODEL_FALLBACK 不向用户展示，由 meta 标注
        ""
    ),
    FailureEvent.QA_REWRITE: (
        # QA_REWRITE 对用户透明，展示重写后的内容
        ""
    ),
    FailureEvent.EDGE_UNREACHABLE: (
        "语音功能暂时不可用，已切换到文字模式。"
    ),
    FailureEvent.VISION_LOW_CONFIDENCE: (
        "我无法确认当前界面状态，请确认后再试，或告诉我您想要执行的操作。"
    ),
    FailureEvent.MODULE_TIMEOUT: (
        "处理超时，请稍后重试。"
    ),
}

# 各事件的审计标签
AUDIT_TAGS: Dict[FailureEvent, str] = {
    FailureEvent.GOVERNOR_BLOCK: "GOVERNOR_BLOCK",
    FailureEvent.EVIDENCE_EMPTY: "EVIDENCE_EMPTY",
    FailureEvent.MODEL_FALLBACK: "MODEL_FALLBACK",
    FailureEvent.QA_REWRITE: "QA_REWRITE",
    FailureEvent.EDGE_UNREACHABLE: "EDGE_UNREACHABLE",
    FailureEvent.VISION_LOW_CONFIDENCE: "VISION_LOW_CONFIDENCE",
    FailureEvent.MODULE_TIMEOUT: "MODULE_TIMEOUT",
}

# 各事件是否中止链路
ABORT_FLAGS: Dict[FailureEvent, bool] = {
    FailureEvent.GOVERNOR_BLOCK: True,
    FailureEvent.EVIDENCE_EMPTY: False,   # 可以继续，使用保守回答策略
    FailureEvent.MODEL_FALLBACK: False,   # 可以继续，已降级到备用模型
    FailureEvent.QA_REWRITE: False,       # 可以继续，使用重写后的输出
    FailureEvent.EDGE_UNREACHABLE: False, # 可以继续，降级为纯文本模式
    FailureEvent.VISION_LOW_CONFIDENCE: True,
    FailureEvent.MODULE_TIMEOUT: True,
}

# 各事件的降级模式
DEGRADED_MODES: Dict[FailureEvent, Optional[str]] = {
    FailureEvent.GOVERNOR_BLOCK: None,
    FailureEvent.EVIDENCE_EMPTY: "conservative_answer",
    FailureEvent.MODEL_FALLBACK: "fallback_model",
    FailureEvent.QA_REWRITE: "rewritten_output",
    FailureEvent.EDGE_UNREACHABLE: "text_only",
    FailureEvent.VISION_LOW_CONFIDENCE: None,
    FailureEvent.MODULE_TIMEOUT: None,
}


# ---------------------------------------------------------------------------
# 统一失败处理器
# ---------------------------------------------------------------------------

class FailureHandler:
    """
    统一失败语义矩阵处理器。

    所有模块的失败都通过此入口处理，确保：
    1. 标准化用户响应（不暴露内部细节）
    2. 审计标签写入 triptych
    3. 确定性的降级动作
    """

    def __init__(
        self,
        audit_writer: Optional[Callable] = None,
        tts_disabler: Optional[Callable] = None,
    ) -> None:
        """
        Args:
            audit_writer: 审计写入回调，签名为 (tag: str, detail: dict) -> None。
            tts_disabler: TTS/VTS 禁用回调（EDGE_UNREACHABLE 时调用）。
        """
        self._audit_writer = audit_writer
        self._tts_disabler = tts_disabler

    async def handle(
        self,
        event: str,
        context: Any,
        detail: Optional[Dict[str, Any]] = None,
    ) -> FailureResult:
        """
        统一失败处理入口。

        Args:
            event: 失败事件名称（FailureEvent 枚举值或字符串）
            context: 链路上下文（ChainContext 或任意 dict）
            detail: 附加失败详情

        Returns:
            FailureResult 处理结果。
        """
        detail = detail or {}

        # 解析事件类型
        try:
            failure_event = FailureEvent(event)
        except ValueError:
            logger.warning("未知的失败事件类型: %s，使用 MODULE_TIMEOUT 兜底", event)
            failure_event = FailureEvent.MODULE_TIMEOUT

        user_response = USER_RESPONSES[failure_event]
        audit_tag = AUDIT_TAGS[failure_event]
        should_abort = ABORT_FLAGS[failure_event]
        degraded_mode = DEGRADED_MODES[failure_event]

        # 构建 meta 信息
        meta: Dict[str, Any] = {}

        # 事件特定处理
        await self._handle_specific(failure_event, context, detail, meta)

        # 写入审计日志
        audit_detail = {
            "event": failure_event.value,
            "degraded_mode": degraded_mode,
            **detail,
        }
        await self._write_audit(audit_tag, audit_detail)

        result = FailureResult(
            event=failure_event,
            user_response=user_response,
            audit_tag=audit_tag,
            should_abort=should_abort,
            degraded_mode=degraded_mode,
            meta=meta,
        )

        logger.info(
            "FailureHandler 处理完成: event=%s, abort=%s, mode=%s",
            failure_event, should_abort, degraded_mode,
        )
        return result

    async def _handle_specific(
        self,
        event: FailureEvent,
        context: Any,
        detail: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> None:
        """各事件的特定处理逻辑。"""

        if event == FailureEvent.EVIDENCE_EMPTY:
            # 在响应 meta 中标注 evidence_empty（需求 6.2）
            meta["evidence_empty"] = True

        elif event == FailureEvent.MODEL_FALLBACK:
            # 在响应 meta 中标注 model_fallback（需求 6.3）
            meta["model_fallback"] = True
            meta["fallback_reason"] = detail.get("fallback_reason", "主模型不可用")
            meta["degraded"] = True
            meta["degraded_mode"] = "fallback_model"
            # 降级明显提示：写入 WARNING 日志，确保运维可感知
            logger.warning(
                "[MODEL_FALLBACK] ⚠️ 主模型不可用，已降级到备用模型。原因: %s",
                meta["fallback_reason"],
            )

        elif event == FailureEvent.QA_REWRITE:
            # triptych 同时保留原始输出和重写后输出（需求 6.4）
            meta["qa_rewrite"] = True
            meta["original_output"] = detail.get("original_output", "")
            meta["rewritten_output"] = detail.get("rewritten_output", "")

        elif event == FailureEvent.EDGE_UNREACHABLE:
            # 禁用 TTS/VTS 功能（需求 6.5）
            if self._tts_disabler:
                try:
                    await self._tts_disabler()
                except Exception as exc:
                    logger.error("TTS 禁用失败: %s", exc)
            meta["tts_disabled"] = True
            meta["vts_disabled"] = True

        elif event == FailureEvent.VISION_LOW_CONFIDENCE:
            # 记录置信度（需求 6.6）
            meta["confidence"] = detail.get("confidence", 0.0)
            meta["threshold"] = detail.get("threshold", 0.80)

        elif event == FailureEvent.MODULE_TIMEOUT:
            # 记录超时模块（需求 6.7）
            meta["timed_out_module"] = detail.get("module", "unknown")
            meta["timeout_seconds"] = detail.get("timeout", 0)

    async def _write_audit(self, tag: str, detail: dict) -> None:
        """写入审计事件到 triptych。"""
        if self._audit_writer is None:
            return
        try:
            import asyncio
            if asyncio.iscoroutinefunction(self._audit_writer):
                await self._audit_writer(tag, detail)
            else:
                self._audit_writer(tag, detail)
        except Exception as exc:
            logger.error("审计写入失败（tag=%s）: %s", tag, exc)
