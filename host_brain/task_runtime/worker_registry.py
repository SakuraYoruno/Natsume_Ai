"""Fail-closed governed worker routing for chat/skill/delegate TaskNodes."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from urllib.parse import unquote


GOVERNED_WORKER_ROUTE_INVALID = "GOVERNED_WORKER_ROUTE_INVALID"
GOVERNED_WORKER_ROUTE_UNBOUND = "GOVERNED_WORKER_ROUTE_UNBOUND"
GOVERNED_WORKER_EXECUTOR_UNSUPPORTED = "GOVERNED_WORKER_EXECUTOR_UNSUPPORTED"

_ROUTE_KINDS = frozenset({"chat", "skill", "delegate", "tool"})
_TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,254}$")


class GovernedWorkerRegistryError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or GOVERNED_WORKER_ROUTE_INVALID)
        self.detail = str(detail or "governed worker route is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class GovernedWorkerRoute:
    kind: str
    target_id: str | None
    canonical: str

    @classmethod
    def parse(cls, value: str) -> "GovernedWorkerRoute":
        raw = str(value or "").strip()
        kind, separator, encoded_target = raw.partition(":")
        kind = kind.strip().lower()
        if kind not in _ROUTE_KINDS:
            raise GovernedWorkerRegistryError(
                GOVERNED_WORKER_ROUTE_INVALID,
                f"unsupported governed worker route kind: {kind or 'missing'}",
            )
        if kind == "chat" and not separator:
            return cls(kind="chat", target_id=None, canonical="chat")
        if not separator or not encoded_target:
            raise GovernedWorkerRegistryError(
                GOVERNED_WORKER_ROUTE_INVALID,
                f"{kind} route requires an encoded target_id",
            )
        try:
            target = unquote(encoded_target, errors="strict").strip()
        except (UnicodeDecodeError, ValueError) as exc:
            raise GovernedWorkerRegistryError(
                GOVERNED_WORKER_ROUTE_INVALID,
                f"{kind} target_id encoding is invalid",
            ) from exc
        if not _TARGET.fullmatch(target):
            raise GovernedWorkerRegistryError(
                GOVERNED_WORKER_ROUTE_INVALID,
                f"{kind} target_id is invalid",
            )
        return cls(kind=kind, target_id=target, canonical=f"{kind}:{encoded_target}")


class GovernedWorkerRegistry:
    """Resolve a frozen node route to exactly one registered worker."""

    def __init__(
        self,
        *,
        chat: Any | None = None,
        skill: Any | None = None,
        delegate: Any | None = None,
        tool: Any | None = None,
        allowed_executors: tuple[str, ...] = ("canonical",),
    ) -> None:
        self._workers = {
            "chat": chat,
            "skill": skill,
            "delegate": delegate,
            "tool": tool,
        }
        self._allowed_executors = frozenset(
            str(value or "").strip().lower()
            for value in allowed_executors
            if str(value or "").strip()
        )
        if not self._allowed_executors:
            raise ValueError("allowed_executors must not be empty")
        for kind, worker in self._workers.items():
            if worker is not None and not callable(getattr(worker, "execute_stream", None)):
                raise ValueError(f"{kind} worker must expose execute_stream")

    def supports(self, worker_route: str) -> bool:
        try:
            parsed = GovernedWorkerRoute.parse(worker_route)
        except GovernedWorkerRegistryError:
            return False
        return self._workers.get(parsed.kind) is not None

    def resolve(self, *, executor: str, worker_route: str) -> Any:
        normalized_executor = str(executor or "").strip().lower()
        if normalized_executor not in self._allowed_executors:
            raise GovernedWorkerRegistryError(
                GOVERNED_WORKER_EXECUTOR_UNSUPPORTED,
                f"unsupported governed executor: {normalized_executor or 'missing'}",
            )
        parsed = GovernedWorkerRoute.parse(worker_route)
        worker = self._workers.get(parsed.kind)
        if worker is None:
            raise GovernedWorkerRegistryError(
                GOVERNED_WORKER_ROUTE_UNBOUND,
                f"no governed worker is registered for route kind: {parsed.kind}",
            )
        return worker


__all__ = [
    "GOVERNED_WORKER_EXECUTOR_UNSUPPORTED",
    "GOVERNED_WORKER_ROUTE_INVALID",
    "GOVERNED_WORKER_ROUTE_UNBOUND",
    "GovernedWorkerRegistry",
    "GovernedWorkerRegistryError",
    "GovernedWorkerRoute",
]
