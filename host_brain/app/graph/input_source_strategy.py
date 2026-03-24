from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import logging

logger = logging.getLogger(__name__)


@dataclass
class InputSourceRuntimeContext:
    request_id: str
    source_upper: str
    payload: dict[str, Any]
    environ: Mapping[str, str]

    def parse_bool_env(self, name: str, default: bool) -> bool:
        raw = str(self.environ.get(name) or "").strip().lower()
        if not raw:
            return bool(default)
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def parse_int_env(self, name: str, *, default: int, min_value: int, max_value: int) -> int:
        raw = str(self.environ.get(name) or "").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = int(default)
        return max(min_value, min(max_value, value))

    def parse_text_env(self, name: str, default: str) -> str:
        value = str(self.environ.get(name) or default).strip()
        return value or default


@dataclass
class InputSourcePolicy:
    # TTS/VTS 抑制控制
    suppress_edge_tts: bool | None = None
    suppress_edge_vts: bool | None = None
    # Gateway 回复控制
    auto_reply_gateway: bool = False
    direct_reply_only: bool = False
    direct_reply_text: str = ""
    reply_channel: str | None = None        # "GATEWAY"
    reply_platform: str | None = None       # "wechat" / "qq" / "feishu" / "wecom"
    reply_conversation_id: str | None = None


@dataclass
class CommitResult:
    decision: str = ""
    reason_code: str = ""
    detail: str = ""
    reply_narrative: str = ""


async def _send_gateway_reply(
    *,
    context: InputSourceRuntimeContext,
    policy: InputSourcePolicy,
    content: str,
    log_prefix: str,
) -> None:
    """通过 OutboundDispatcher 向对应平台发送回复。"""
    from host_brain.channels.edge_rpc_client import post_gateway_reply  # noqa: PLC0415

    wait_result = context.parse_bool_env("HOST_INPUT_GATEWAY_REPLY_WAIT_RESULT", True)
    timeout_ms = context.parse_int_env(
        "HOST_INPUT_GATEWAY_REPLY_TIMEOUT_MS",
        default=12000,
        min_value=300,
        max_value=120000,
    )
    send_result = await post_gateway_reply(
        platform=policy.reply_platform,
        conversation_id=policy.reply_conversation_id,
        content=content,
        request_id=context.request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    if send_result.success:
        logger.info(
            "run_main_chain_from_input: %s sent request_id=%s platform=%s conversation_id=%s status=%s",
            log_prefix,
            context.request_id,
            (policy.reply_platform or ""),
            (policy.reply_conversation_id or ""),
            send_result.status,
        )
        return
    logger.warning(
        "run_main_chain_from_input: %s failed request_id=%s platform=%s conversation_id=%s reason=%s detail=%s",
        log_prefix,
        context.request_id,
        (policy.reply_platform or ""),
        (policy.reply_conversation_id or ""),
        (send_result.reason_code or ""),
        send_result.detail,
    )


class InputSourceStrategy:
    def build_policy(self, *, context: InputSourceRuntimeContext) -> InputSourcePolicy:
        _ = context
        return InputSourcePolicy()

    async def pre_chain(
        self,
        *,
        context: InputSourceRuntimeContext,
        policy: InputSourcePolicy,
    ) -> bool:
        _ = context, policy
        return False

    async def post_chain(
        self,
        *,
        context: InputSourceRuntimeContext,
        policy: InputSourcePolicy,
        commit_result: CommitResult,
    ) -> None:
        _ = context, policy, commit_result


class AsrInputSourceStrategy(InputSourceStrategy):
    """ASR 语音输入策略。

    行为约束：
    - 允许 TTS 语音播放（suppress_edge_tts=False）
    - 允许 VTS 表情口型驱动（suppress_edge_vts=False）
    - 禁止任何聊天软件回复行为
    """

    def build_policy(self, *, context: InputSourceRuntimeContext) -> InputSourcePolicy:
        policy = InputSourcePolicy(
            suppress_edge_tts=False,
            suppress_edge_vts=False,
            auto_reply_gateway=False,
        )
        logger.info(
            "run_main_chain_from_input: asr policy source=%s suppress_tts=%s suppress_vts=%s",
            context.source_upper,
            policy.suppress_edge_tts,
            policy.suppress_edge_vts,
        )
        return policy


class GatewayInputSourceStrategy(InputSourceStrategy):
    """统一消息网关输入策略（微信/QQ/飞书/企微等外部平台）。

    行为约束：
    - 禁止 TTS 语音播放（suppress_edge_tts=True）
    - 禁止 VTS 表情口型驱动（suppress_edge_vts=True）
    - 通过 OutboundDispatcher 回复对应平台聊天窗口
    """

    def build_policy(self, *, context: InputSourceRuntimeContext) -> InputSourcePolicy:
        policy = InputSourcePolicy(
            suppress_edge_tts=context.parse_bool_env("HOST_INPUT_GATEWAY_SUPPRESS_EDGE_TTS", True),
            suppress_edge_vts=context.parse_bool_env("HOST_INPUT_GATEWAY_SUPPRESS_EDGE_VTS", True),
            auto_reply_gateway=context.parse_bool_env("HOST_INPUT_GATEWAY_AUTO_REPLY", True),
            direct_reply_only=context.parse_bool_env("HOST_INPUT_GATEWAY_DIRECT_REPLY_ONLY", False),
        )
        policy.direct_reply_text = context.parse_text_env("HOST_INPUT_GATEWAY_DIRECT_REPLY_TEXT", "收到")
        policy.reply_platform = str(context.payload.get("platform") or "").strip().lower() or None
        policy.reply_conversation_id = str(context.payload.get("conversation_id") or "").strip() or None
        if policy.auto_reply_gateway and policy.reply_platform and policy.reply_conversation_id:
            policy.reply_channel = "GATEWAY"
        logger.info(
            "run_main_chain_from_input: gateway policy source=%s suppress_tts=%s suppress_vts=%s "
            "auto_reply=%s direct_reply_only=%s platform=%s conversation_id=%s",
            context.source_upper,
            policy.suppress_edge_tts,
            policy.suppress_edge_vts,
            policy.auto_reply_gateway,
            policy.direct_reply_only,
            (policy.reply_platform or ""),
            (policy.reply_conversation_id or ""),
        )
        return policy

    async def pre_chain(
        self,
        *,
        context: InputSourceRuntimeContext,
        policy: InputSourcePolicy,
    ) -> bool:
        if not policy.direct_reply_only:
            return False
        if not policy.auto_reply_gateway:
            logger.info(
                "run_main_chain_from_input: gateway direct reply skipped (auto_reply disabled) request_id=%s",
                context.request_id,
            )
            return True
        if not policy.reply_conversation_id or not policy.reply_platform:
            logger.warning(
                "run_main_chain_from_input: gateway direct reply skipped (missing platform/conversation_id) request_id=%s",
                context.request_id,
            )
            return True
        try:
            await _send_gateway_reply(
                context=context,
                policy=policy,
                content=policy.direct_reply_text,
                log_prefix="gateway direct reply",
            )
        except Exception as exc:
            logger.error(
                "run_main_chain_from_input: gateway direct reply exception request_id=%s platform=%s error=%s",
                context.request_id,
                (policy.reply_platform or ""),
                exc,
            )
        return True

    async def post_chain(
        self,
        *,
        context: InputSourceRuntimeContext,
        policy: InputSourcePolicy,
        commit_result: CommitResult,
    ) -> None:
        if not policy.auto_reply_gateway:
            return
        if not policy.reply_conversation_id or not policy.reply_platform:
            logger.warning(
                "run_main_chain_from_input: gateway auto reply skipped (missing platform/conversation_id) request_id=%s",
                context.request_id,
            )
            return
        reply_narrative = commit_result.reply_narrative
        fallback_on_reject = context.parse_bool_env("HOST_INPUT_GATEWAY_FALLBACK_ON_REJECT", True)
        fallback_text = context.parse_text_env(
            "HOST_INPUT_GATEWAY_REJECT_FALLBACK_TEXT",
            "刚刚处理超时了，请再发一次，我这边立即重试。",
        )
        if commit_result.decision != "APPROVE":
            if not fallback_on_reject:
                logger.info(
                    "run_main_chain_from_input: gateway auto reply skipped (decision=%s fallback_disabled) request_id=%s",
                    (commit_result.decision or "UNKNOWN"),
                    context.request_id,
                )
                return
            if not reply_narrative:
                reply_narrative = fallback_text
            logger.warning(
                "run_main_chain_from_input: gateway auto reply fallback enabled request_id=%s decision=%s reason=%s detail=%s",
                context.request_id,
                (commit_result.decision or "UNKNOWN"),
                (commit_result.reason_code or ""),
                commit_result.detail,
            )
        if not reply_narrative:
            logger.info(
                "run_main_chain_from_input: gateway auto reply skipped (empty narrative) request_id=%s",
                context.request_id,
            )
            return
        try:
            await _send_gateway_reply(
                context=context,
                policy=policy,
                content=reply_narrative,
                log_prefix="gateway auto reply",
            )
        except Exception as exc:
            logger.error(
                "run_main_chain_from_input: gateway auto reply exception request_id=%s platform=%s error=%s",
                context.request_id,
                (policy.reply_platform or ""),
                exc,
            )


_DEFAULT_INPUT_SOURCE_STRATEGY = InputSourceStrategy()
_INPUT_SOURCE_STRATEGY_REGISTRY: dict[str, InputSourceStrategy] = {
    "ASR": AsrInputSourceStrategy(),
    "GATEWAY": GatewayInputSourceStrategy(),
}


def resolve_input_source_strategy(source_upper: str) -> InputSourceStrategy:
    return _INPUT_SOURCE_STRATEGY_REGISTRY.get(source_upper, _DEFAULT_INPUT_SOURCE_STRATEGY)
