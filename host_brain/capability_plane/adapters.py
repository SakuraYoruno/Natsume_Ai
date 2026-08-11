"""Transport-only capability adapters with no policy, retry, clock or audit ownership."""

from __future__ import annotations

from typing import Any


def object_tool_adapter(tool: Any):
    """Adapt a LangChain-style or plain callable tool for CapabilityBroker."""

    def _invoke(request, _grant):  # noqa: ANN001
        args = dict(request.args)
        args["_governor_approved"] = True
        if callable(getattr(tool, "func", None)):
            return tool.func(**args)
        if callable(getattr(tool, "invoke", None)):
            return tool.invoke(args)
        if callable(tool):
            return tool(**args)
        raise TypeError(f"tool {request.capability_id!r} is not callable")

    return _invoke


__all__ = ["object_tool_adapter"]
