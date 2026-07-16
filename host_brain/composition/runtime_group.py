"""Lifecycle group for multiple governed surface runtimes sharing one spine."""

from __future__ import annotations

from collections.abc import Mapping
import inspect
from typing import Any


class GovernedRuntimeGroup:
    def __init__(self, runtimes: Mapping[str, Any]) -> None:
        normalized = {
            str(name).strip(): runtime
            for name, runtime in dict(runtimes or {}).items()
            if str(name).strip()
        }
        if not normalized:
            raise ValueError("at least one governed runtime is required")
        for name, runtime in normalized.items():
            for method_name in ("activate", "close_admission", "drain", "aclose"):
                if not callable(getattr(runtime, method_name, None)):
                    raise TypeError(f"runtime {name} lacks {method_name}")
        self._runtimes = normalized
        self._active_names: list[str] = []

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._runtimes)

    def get(self, name: str) -> Any:
        return self._runtimes[str(name)]

    async def activate(self) -> None:
        if self._active_names:
            return
        try:
            for name, runtime in self._runtimes.items():
                await _await_call(runtime.activate())
                self._active_names.append(name)
        except BaseException:
            await self.aclose()
            raise

    async def close_admission(self) -> None:
        for name in tuple(self._active_names):
            await _await_call(self._runtimes[name].close_admission())

    async def drain(self) -> None:
        for name in tuple(self._active_names):
            await _await_call(self._runtimes[name].drain())

    async def aclose(self) -> None:
        failures: list[BaseException] = []
        for name in reversed(tuple(self._active_names)):
            try:
                await _await_call(self._runtimes[name].aclose())
            except BaseException as exc:
                failures.append(exc)
            else:
                self._active_names.remove(name)
        if failures:
            raise RuntimeError("GOVERNED_RUNTIME_GROUP_CLOSE_FAILED") from failures[0]


async def _await_call(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


__all__ = ["GovernedRuntimeGroup"]
