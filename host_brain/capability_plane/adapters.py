"""Transport-only capability adapters with no policy, retry, clock or audit ownership."""

from __future__ import annotations

from typing import Any


def object_tool_adapter(tool: Any):
    """Adapt a LangChain-style or plain callable tool for CapabilityBroker."""

    def _invoke(request, _grant):  # noqa: ANN001
        # Broker 已在进入适配器前完成授权、幂等与截止时间裁决；适配器只做一次传输，
        # 不应自行重试、重新判定权限或持有审计时钟。
        args = dict(request.args)
        # 该标记是给受控工具的调用上下文提示，不是可由工具据此伪造的授权凭据。
        args["_governor_approved"] = True
        if callable(getattr(tool, "func", None)):
            return tool.func(**args)
        if callable(getattr(tool, "invoke", None)):
            return tool.invoke(args)
        if callable(tool):
            return tool(**args)
        # 适配失败交由 Broker 归档为失败回执，避免静默降级为“似乎执行成功”。
        raise TypeError(f"tool {request.capability_id!r} is not callable")

    return _invoke


__all__ = ["object_tool_adapter"]
