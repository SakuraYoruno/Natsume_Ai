from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import logging
import time

logger = logging.getLogger(__name__)


def _normalize_gateway_reply_content(raw_content: str) -> str:
    from host_brain.app.prompt_builder import (  # noqa: PLC0415
        extract_gateway_narrative,
        extract_json_object_from_text,
        repair_utf8_latin1_mojibake,
    )

    normalized = repair_utf8_latin1_mojibake(str(raw_content or "").strip())
    if not normalized:
        return ""
    try:
        payload = extract_json_object_from_text(normalized)
    except Exception:
        return normalized
    extracted = extract_gateway_narrative(payload)
    return extracted or normalized


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
    auto_reply_wechat: bool = False
    direct_reply_only: bool = False
    direct_reply_text: str = ""
    reply_channel: str | None = None        # "GATEWAY"
    reply_platform: str | None = None       # "wechat" / "qq" / "feishu" / "wecom"
    reply_conversation_id: str | None = None
    reply_target: str | None = None         # 回复目标标识（conversation_id 别名）
    reply_target_candidates: list[str] = field(default_factory=list)


@dataclass
class CommitResult:
    decision: str = ""
    reason_code: str = ""
    detail: str = ""
    reply_narrative: str = ""
    tts_already_fired: bool = False  # SSE 生成器内部是否已执行 TTS（用于 ASR 兜底判断）


def _audit_gateway_reply(
    *,
    request_id: str,
    platform: str | None,
    conversation_id: str | None,
    status: str,
    reason_code: str | None,
    detail: str,
) -> None:
    try:
        from host_brain.app.dependencies import get_ledger  # noqa: PLC0415

        ledger = get_ledger()
        if ledger is None:
            return
        normalized_platform = str(platform or "").strip().lower()
        normalized_conversation = str(conversation_id or "").strip()
        ledger.log_channel_io(
            {
                "request_id": request_id,
                "channel": normalized_platform,
                "direction": "EGRESS",
                "thread_key": normalized_conversation,
                "message_id": f"gateway_reply::{request_id}",
                "user_id": normalized_conversation or None,
                "status": status,
                "reason_code": reason_code,
                "stage": "S_GATEWAY_REPLY",
                "detail": detail[:400] if detail else None,
                "payload_digest": "",
                "ts_ms": int(time.time() * 1000),
            }
        )
    except Exception as exc:
        logger.debug("gateway reply audit skipped request_id=%s error=%s", request_id, exc)


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
        _audit_gateway_reply(
            request_id=context.request_id,
            platform=policy.reply_platform,
            conversation_id=policy.reply_conversation_id,
            status=str(send_result.status or "SENT").strip().upper() or "SENT",
            reason_code=send_result.reason_code,
            detail=send_result.detail or f"{log_prefix} sent",
        )
        logger.info(
            "run_main_chain_from_input: %s sent request_id=%s platform=%s conversation_id=%s status=%s",
            log_prefix,
            context.request_id,
            (policy.reply_platform or ""),
            (policy.reply_conversation_id or ""),
            send_result.status,
        )
        return
    _audit_gateway_reply(
        request_id=context.request_id,
        platform=policy.reply_platform,
        conversation_id=policy.reply_conversation_id,
        status="FAILED",
        reason_code=send_result.reason_code,
        detail=send_result.detail or f"{log_prefix} failed",
    )
    logger.warning(
        "run_main_chain_from_input: %s failed request_id=%s platform=%s conversation_id=%s reason=%s detail=%s",
        log_prefix,
        context.request_id,
        (policy.reply_platform or ""),
        (policy.reply_conversation_id or ""),
        (send_result.reason_code or ""),
        send_result.detail,
    )


def _build_reply_target_candidates(*candidates: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = str(item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


def _is_blocked_wechat_target(*, context: InputSourceRuntimeContext, target: str | None) -> bool:
    if not context.parse_bool_env("HOST_INPUT_WECHAT_BLOCK_FILEHELPER", False):
        return False
    normalized_target = str(target or "").strip().lower()
    from_wxid = str(context.payload.get("from_wxid") or "").strip().lower()
    return normalized_target == "filehelper" or from_wxid == "filehelper"


async def _send_wechat_reply(
    *,
    context: InputSourceRuntimeContext,
    policy: InputSourcePolicy,
    content: str,
    log_prefix: str,
) -> None:
    from host_brain.channels.edge_rpc_client import post_wechat_command  # noqa: PLC0415

    wait_result = context.parse_bool_env("HOST_INPUT_WECHAT_REPLY_WAIT_RESULT", True)
    timeout_ms = context.parse_int_env(
        "HOST_INPUT_WECHAT_REPLY_TIMEOUT_MS",
        default=12000,
        min_value=300,
        max_value=120000,
    )
    send_result = await post_wechat_command(
        action="send_text",
        target=str(policy.reply_target or "").strip(),
        target_candidates=list(policy.reply_target_candidates or []),
        content=content,
        request_id=context.request_id,
        wait_result=wait_result,
        timeout_ms=timeout_ms,
    )
    if send_result.success:
        logger.info(
            "run_main_chain_from_input: %s sent request_id=%s target=%s status=%s",
            log_prefix,
            context.request_id,
            (policy.reply_target or ""),
            send_result.status,
        )
        return
    logger.warning(
        "run_main_chain_from_input: %s failed request_id=%s target=%s reason=%s detail=%s",
        log_prefix,
        context.request_id,
        (policy.reply_target or ""),
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
    - post_chain 兜底：当 SSE 生成器内部未执行 TTS 时，补充 TTS 播报（安全网）
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

    async def post_chain(
        self,
        *,
        context: InputSourceRuntimeContext,
        policy: InputSourcePolicy,
        commit_result: CommitResult,
    ) -> None:
        """ASR 兜底 TTS 安全网。

        当 SSE 生成器内部未执行 TTS（如复杂场景遗漏）时，
        在 post_chain 中补充 TTS 播报，确保用户语音输入一定能听到回复。

        检测逻辑：
        - commit_result.tts_already_fired == True → 已播报，跳过
        - policy.suppress_edge_tts == True → 抑制，跳过
        - narrative 为空 → 无内容可播，跳过
        - 环境变量 HOST_ASR_POST_CHAIN_TTS_FALLBACK=false → 手动禁用，跳过
        """
        narrative = (commit_result.reply_narrative or "").strip()
        if not narrative:
            return
        if commit_result.tts_already_fired:
            logger.debug(
                "run_main_chain_from_input: asr post_chain TTS skip (already fired) request_id=%s",
                context.request_id,
            )
            return
        if policy.suppress_edge_tts:
            return

        # 环境变量控制：默认开启兜底 TTS
        fallback_enabled = context.parse_bool_env("HOST_ASR_POST_CHAIN_TTS_FALLBACK", True)
        if not fallback_enabled:
            logger.debug(
                "run_main_chain_from_input: asr post_chain TTS fallback disabled request_id=%s",
                context.request_id,
            )
            return

        tts_enabled = context.parse_bool_env("HOST_STREAM_TTS_ENABLED", True)
        if not tts_enabled:
            return

        logger.info(
            "run_main_chain_from_input: asr post_chain TTS fallback firing request_id=%s narrative_len=%d",
            context.request_id,
            len(narrative),
        )
        try:
            from host_brain.channels.edge_rpc_client import post_speak  # noqa: PLC0415

            await post_speak(
                text=narrative,
                request_id=context.request_id,
                emotion_tag="NEUTRAL",
            )
            logger.info(
                "run_main_chain_from_input: asr post_chain TTS fallback OK request_id=%s",
                context.request_id,
            )
        except Exception as exc:
            logger.warning(
                "run_main_chain_from_input: asr post_chain TTS fallback failed request_id=%s error=%s",
                context.request_id,
                exc,
            )


class WechatInputSourceStrategy(InputSourceStrategy):
    def build_policy(self, *, context: InputSourceRuntimeContext) -> InputSourcePolicy:
        from_wxid = str(context.payload.get("from_wxid") or "").strip()
        raw_extra = context.payload.get("raw_extra")
        raw_extra_dict = dict(raw_extra) if isinstance(raw_extra, Mapping) else {}
        display_name = str(
            context.payload.get("sender_name")
            or raw_extra_dict.get("sender_name")
            or raw_extra_dict.get("display_name")
            or ""
        ).strip()
        reply_target_candidates = _build_reply_target_candidates(from_wxid, display_name)
        reply_target = display_name or from_wxid or None
        policy = InputSourcePolicy(
            suppress_edge_tts=context.parse_bool_env("HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS", True),
            suppress_edge_vts=context.parse_bool_env("HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS", True),
            auto_reply_wechat=context.parse_bool_env("HOST_INPUT_WECHAT_AUTO_REPLY", True),
            direct_reply_only=context.parse_bool_env("HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY", True),
            direct_reply_text=context.parse_text_env("HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT", "收到"),
            reply_channel="WECHAT" if reply_target else None,
            reply_target=reply_target,
            reply_target_candidates=reply_target_candidates,
        )
        logger.info(
            "run_main_chain_from_input: wechat policy source=%s suppress_tts=%s suppress_vts=%s "
            "auto_reply=%s direct_reply_only=%s target=%s candidates=%d",
            context.source_upper,
            policy.suppress_edge_tts,
            policy.suppress_edge_vts,
            policy.auto_reply_wechat,
            policy.direct_reply_only,
            (policy.reply_target or ""),
            len(policy.reply_target_candidates),
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
        if _is_blocked_wechat_target(context=context, target=policy.reply_target):
            logger.info(
                "run_main_chain_from_input: wechat direct reply blocked request_id=%s target=%s",
                context.request_id,
                (policy.reply_target or ""),
            )
            return True
        if not policy.auto_reply_wechat:
            logger.info(
                "run_main_chain_from_input: wechat direct reply skipped (auto_reply disabled) request_id=%s",
                context.request_id,
            )
            return True
        if not policy.reply_target:
            logger.warning(
                "run_main_chain_from_input: wechat direct reply skipped (missing target) request_id=%s",
                context.request_id,
            )
            return True
        try:
            await _send_wechat_reply(
                context=context,
                policy=policy,
                content=policy.direct_reply_text,
                log_prefix="wechat direct reply",
            )
        except Exception as exc:
            logger.error(
                "run_main_chain_from_input: wechat direct reply exception request_id=%s target=%s error=%s",
                context.request_id,
                (policy.reply_target or ""),
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
        if not policy.auto_reply_wechat:
            return
        if _is_blocked_wechat_target(context=context, target=policy.reply_target):
            logger.info(
                "run_main_chain_from_input: wechat auto reply blocked request_id=%s target=%s",
                context.request_id,
                (policy.reply_target or ""),
            )
            return
        if not policy.reply_target:
            logger.warning(
                "run_main_chain_from_input: wechat auto reply skipped (missing target) request_id=%s",
                context.request_id,
            )
            return
        reply_narrative = _normalize_gateway_reply_content(commit_result.reply_narrative)
        fallback_on_reject = context.parse_bool_env("HOST_INPUT_WECHAT_FALLBACK_ON_REJECT", True)
        fallback_text = context.parse_text_env(
            "HOST_INPUT_WECHAT_REJECT_FALLBACK_TEXT",
            "刚刚处理超时了，请再发一次，我这边立即重试。",
        )
        if commit_result.decision != "APPROVE":
            if not fallback_on_reject:
                logger.info(
                    "run_main_chain_from_input: wechat auto reply skipped (decision=%s fallback_disabled) request_id=%s",
                    (commit_result.decision or "UNKNOWN"),
                    context.request_id,
                )
                return
            if not reply_narrative:
                reply_narrative = fallback_text
        if not reply_narrative:
            logger.info(
                "run_main_chain_from_input: wechat auto reply skipped (empty narrative) request_id=%s",
                context.request_id,
            )
            return
        try:
            await _send_wechat_reply(
                context=context,
                policy=policy,
                content=reply_narrative,
                log_prefix="wechat auto reply",
            )
        except Exception as exc:
            logger.error(
                "run_main_chain_from_input: wechat auto reply exception request_id=%s target=%s error=%s",
                context.request_id,
                (policy.reply_target or ""),
                exc,
            )


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
        policy.reply_target = policy.reply_conversation_id  # reply_target 作为 conversation_id 的别名
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
        reply_narrative = _normalize_gateway_reply_content(commit_result.reply_narrative)
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
    "WECHAT": WechatInputSourceStrategy(),
}


def resolve_input_source_strategy(source_upper: str) -> InputSourceStrategy:
    return _INPUT_SOURCE_STRATEGY_REGISTRY.get(source_upper, _DEFAULT_INPUT_SOURCE_STRATEGY)
