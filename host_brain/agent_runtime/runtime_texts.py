"""Canonical runtime helpers shared by the active agent runtime modules."""

from __future__ import annotations

import asyncio
import concurrent.futures
from functools import partial
import logging
from typing import Any, Callable

from host_brain.core.orchestration_settings import get_orchestration_settings
from host_brain.persona.response_form_contract import derive_render_profile_for_response_form

_logger = logging.getLogger("Natsume.agent_runtime_shared")


_POST_PROCESS_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=get_orchestration_settings().post_process_executor_workers,
    thread_name_prefix="postprocess",
)


async def _run_in_post_process_executor(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _POST_PROCESS_EXECUTOR,
        partial(func, *args, **kwargs),
    )


def _resolve_text_render_target_lang(
    *,
    request_body: Any,
    sanitized_message: str,
) -> str:
    """Resolve language for the active output profile.

    Platform is not the language boundary: text and the current voice model render
    as Chinese, media uses a structured action envelope. Gateway/QQ is only a
    transport layer.
    """
    _ = sanitized_message
    profile = derive_render_profile_for_response_form(str(getattr(request_body, "response_form", None) or ""))
    if profile == "ja_voice":
        return "ja"
    if profile == "media_action":
        return "media"
    return "zh"
