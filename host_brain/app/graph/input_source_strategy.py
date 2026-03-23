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
    suppress_edge_tts: bool | None = None
    suppress_edge_vts: bool | None = None
    auto_reply_wechat: bool = False
    direct_reply_only: bool = False
    direct_reply_text: str = ""
    reply_channel: str | None = None
    reply_target: str | None = None
    reply_target_candidates: list[str] = field(default_factory=list)


@dataclass
class CommitResult:
    decision: str = ""
    reason_code: str = ""
    detail: str = ""
    reply_narrative: str = ""


def _normalize_target_key(value: str) -> str:
    raw = str(value or "").strip().lower()
    return raw.replace(" ", "").replace("_", "")


def _is_file_helper_target(value: str) -> bool:
    key = _normalize_target_key(value)
    return key in {"filehelper", "文件传输助手", "filetransferhelper"}


def _is_wxid_like(value: str) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return False
    if raw.startswith(("wxid_", "gh_", "v1_", "v2_")):
        return True
    if raw.endswith("@chatroom"):
        return True
    return False


def _is_active_chat_target(value: str) -> bool:
    key = _normalize_target_key(value)
    return key in {"activechat"}


def _append_target_candidate(container: list[str], value: Any) -> None:
    text_value = str(value or "").strip()
    if not text_value:
        return
    key = _normalize_target_key(text_value)
    if not key:
        return
    if key in {"localwechat", "local_wechat"}:
        return
    if any(_normalize_target_key(item) == key for item in container):
        return
    container.append(text_value)


def _collect_wechat_reply_targets(raw_payload: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    _append_target_candidate(candidates, raw_payload.get("room_wxid"))
    _append_target_candidate(candidates, raw_payload.get("from_wxid"))
    for key in (
        "sender",
        "from_name",
        "from_nickname",
        "talker",
        "chat_name",
        "conversation",
        "nickname",
        "remark",
        "reply_target",
        "sender_display",
        "contact_name",
    ):
        _append_target_candidate(candidates, raw_payload.get(key))
    raw_extra = raw_payload.get("raw_extra")
    if isinstance(raw_extra, dict):
        for key in (
            "sender",
            "sender_name",
            "from_name",
            "from_nickname",
            "nickname",
            "remark",
            "display_name",
            "talker",
            "chat_name",
            "conversation",
            "contact_name",
            "member_nick",
        ):
            _append_target_candidate(candidates, raw_extra.get(key))
    return candidates


def _pick_wechat_reply_target(candidates: list[str]) -> str | None:
    non_wxid_candidates = [
        item for item in candidates
        if (not _is_wxid_like(item)) and (not _is_file_helper_target(item))
    ]
    non_file_helper_candidates = [
        item for item in candidates
        if not _is_file_helper_target(item)
    ]
    if non_wxid_candidates:
        return str(non_wxid_candidates[0] or "").strip() or None
    if non_file_helper_candidates:
        return str(non_file_helper_candidates[0] or "").strip() or None
    if candidates:
        return str(candidates[0] or "").strip() or None
    return None


async def _send_wechat_text(
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
        target=policy.reply_target,
        target_candidates=policy.reply_target_candidates,
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
    pass


class WechatInputSourceStrategy(InputSourceStrategy):
    def build_policy(self, *, context: InputSourceRuntimeContext) -> InputSourcePolicy:
        policy = InputSourcePolicy(
            suppress_edge_tts=context.parse_bool_env("HOST_INPUT_WECHAT_SUPPRESS_EDGE_TTS", True),
            suppress_edge_vts=context.parse_bool_env("HOST_INPUT_WECHAT_SUPPRESS_EDGE_VTS", True),
            auto_reply_wechat=context.parse_bool_env("HOST_INPUT_WECHAT_AUTO_REPLY", True),
            direct_reply_only=context.parse_bool_env("HOST_INPUT_WECHAT_DIRECT_REPLY_ONLY", True),
        )
        policy.direct_reply_text = context.parse_text_env("HOST_INPUT_WECHAT_DIRECT_REPLY_TEXT", "收到")
        policy.reply_target_candidates = _collect_wechat_reply_targets(context.payload)
        policy.reply_target = _pick_wechat_reply_target(policy.reply_target_candidates)
        if policy.auto_reply_wechat and policy.reply_target:
            policy.reply_channel = "WECHAT"
        logger.info(
            "run_main_chain_from_input: wechat policy source=%s suppress_tts=%s suppress_vts=%s auto_reply=%s direct_reply_only=%s target=%s candidates=%s",
            context.source_upper,
            policy.suppress_edge_tts,
            policy.suppress_edge_vts,
            policy.auto_reply_wechat,
            policy.direct_reply_only,
            (policy.reply_target or ""),
            ",".join(policy.reply_target_candidates[:8]),
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
        allow_active_target_reply = context.parse_bool_env("HOST_INPUT_WECHAT_ALLOW_ACTIVE_CHAT_REPLY", True)
        if _is_active_chat_target(policy.reply_target) and (not allow_active_target_reply):
            logger.info(
                "run_main_chain_from_input: wechat direct reply skipped (active_chat target) request_id=%s",
                context.request_id,
            )
            return True
        block_filehelper = context.parse_bool_env("HOST_INPUT_WECHAT_BLOCK_FILEHELPER", True)
        if block_filehelper and _is_file_helper_target(policy.reply_target):
            logger.warning(
                "run_main_chain_from_input: wechat direct reply skipped (filehelper blocked) request_id=%s target=%s",
                context.request_id,
                policy.reply_target,
            )
            return True
        try:
            await _send_wechat_text(
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
        if not policy.reply_target:
            logger.warning(
                "run_main_chain_from_input: wechat auto reply skipped (missing target) request_id=%s",
                context.request_id,
            )
            return
        allow_active_target_reply = context.parse_bool_env("HOST_INPUT_WECHAT_ALLOW_ACTIVE_CHAT_REPLY", True)
        if _is_active_chat_target(policy.reply_target) and (not allow_active_target_reply):
            logger.info(
                "run_main_chain_from_input: wechat auto reply skipped (active_chat target) request_id=%s",
                context.request_id,
            )
            return
        block_filehelper = context.parse_bool_env("HOST_INPUT_WECHAT_BLOCK_FILEHELPER", True)
        if block_filehelper and _is_file_helper_target(policy.reply_target):
            logger.warning(
                "run_main_chain_from_input: wechat auto reply skipped (filehelper blocked) request_id=%s target=%s",
                context.request_id,
                policy.reply_target,
            )
            return
        reply_narrative = commit_result.reply_narrative
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
            logger.warning(
                "run_main_chain_from_input: wechat auto reply fallback enabled request_id=%s decision=%s reason=%s detail=%s",
                context.request_id,
                (commit_result.decision or "UNKNOWN"),
                (commit_result.reason_code or ""),
                commit_result.detail,
            )
        if not reply_narrative:
            logger.info(
                "run_main_chain_from_input: wechat auto reply skipped (empty narrative) request_id=%s",
                context.request_id,
            )
            return
        try:
            await _send_wechat_text(
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


_DEFAULT_INPUT_SOURCE_STRATEGY = InputSourceStrategy()
_INPUT_SOURCE_STRATEGY_REGISTRY: dict[str, InputSourceStrategy] = {
    "ASR": AsrInputSourceStrategy(),
    "WECHAT": WechatInputSourceStrategy(),
}


def resolve_input_source_strategy(source_upper: str) -> InputSourceStrategy:
    return _INPUT_SOURCE_STRATEGY_REGISTRY.get(source_upper, _DEFAULT_INPUT_SOURCE_STRATEGY)
