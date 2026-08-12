"""已认证 OBS 命令的领域无关类型化路由器。"""

from __future__ import annotations

import inspect
import re

from .command_ports import (
    ObsCommandEnvelope,
    ObsCommandExecutionResult,
    ObsCommandPortExecutionError,
    ObsCommandPortRegistry,
)


_RESULT_REF_RE = re.compile(r"^[a-z][a-z0-9+.-]{1,31}://[^\s]{1,2016}$")


class ObsCommandRoutingError(RuntimeError):
    def __init__(self, reason_code: str, *, side_effects_may_have_run: bool = False) -> None:
        self.reason_code = str(reason_code)
        self.side_effects_may_have_run = bool(side_effects_may_have_run)
        super().__init__(self.reason_code)


class ObsCommandRouter:
    """只调用显式注册的命令端口，绝不回退到遗留写入器。"""

    def __init__(self, registry: ObsCommandPortRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ObsCommandPortRegistry:
        return self._registry

    async def route(self, envelope: ObsCommandEnvelope) -> ObsCommandExecutionResult:
        # registry 是能力白名单；即使依赖对象碰巧提供 execute，也没有登记就没有
        # 领域写权限。未知异常一律标为“可能已执行”，避免重试放大副作用。
        if not isinstance(envelope, ObsCommandEnvelope):
            raise ObsCommandRoutingError("OBS_COMMAND_ENVELOPE_INVALID")
        port = self._registry.resolve_port(envelope.command_id)
        if port is None:
            raise ObsCommandRoutingError("OBS_COMMAND_DOMAIN_PORT_UNAVAILABLE")
        try:
            result = port.execute(envelope)
            if inspect.isawaitable(result):
                result = await result
        except ObsCommandPortExecutionError as exc:
            raise ObsCommandRoutingError(
                exc.reason_code,
                side_effects_may_have_run=exc.side_effects_may_have_run,
            ) from exc
        except Exception as exc:
            # Arbitrary dependencies are not an authority source.  Even an
            # exception with similarly named attributes remains uncertain
            # until a registered port translates it into the closed contract.
            raise ObsCommandRoutingError(
                "OBS_COMMAND_EXECUTION_UNCERTAIN",
                side_effects_may_have_run=True,
            ) from exc
        if not isinstance(result, ObsCommandExecutionResult):
            raise ObsCommandRoutingError(
                "OBS_COMMAND_RESULT_INVALID",
                side_effects_may_have_run=True,
            )
        if not _RESULT_REF_RE.fullmatch(str(result.result_ref or "").strip()):
            raise ObsCommandRoutingError(
                "OBS_COMMAND_RESULT_INVALID",
                side_effects_may_have_run=bool(result.side_effects_executed),
            )
        return result


__all__ = ["ObsCommandRouter", "ObsCommandRoutingError"]
