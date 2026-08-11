"""Immutable TASK-0495 profile for the isolated M48-B P0 matrix.

This module is test-only.  It deliberately has no live/prod mode and delegates
all attempted effects to the recording-only Entry Gate harnesses.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import traceback
from typing import Mapping
import weakref

try:  # pragma: no cover - platform-specific process primitive
    import _winapi  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - non-Windows
    _winapi = None  # type: ignore[assignment]

from tests.fixtures.m48b_isolation import (
    M48B_ISOLATION_DOMAINS,
    M48BIsolationHarness,
    build_m48b_isolation_harness,
)


M48B_P0_VALIDATION_PROFILE_ENV = "M48B_P0_VALIDATION_PROFILE"
M48B_P0_ISOLATED_ONLY = "ISOLATED_ONLY"
M48B_FORBIDDEN_REAL_IO = "M48B_FORBIDDEN_REAL_IO"


class M48BForbiddenIOAttempt(RuntimeError):
    """Stable failure raised before a test can perform real external I/O."""

    reason_code = M48B_FORBIDDEN_REAL_IO

    def __init__(self, operation: str) -> None:
        self.operation = str(operation or "unknown")
        super().__init__(f"{self.reason_code}:{self.operation}")


class NoRealIOGuard:
    """Temporarily deny network listeners/egress and child processes.

    Only the operation name is retained.  Arguments are deliberately omitted
    because they may contain credentials, message bodies or local paths.
    """

    def __init__(self, attempts: list[str] | None = None) -> None:
        self._attempts = attempts if attempts is not None else []
        self._originals: list[tuple[object, str, object]] = []
        self._asyncio_control_sockets: weakref.WeakSet[socket.socket] = (
            weakref.WeakSet()
        )
        self._active = False

    @property
    def attempted_operations(self) -> tuple[str, ...]:
        return tuple(self._attempts)

    @staticmethod
    def _is_asyncio_self_pipe(*, require_socketpair_frame: bool = True) -> bool:
        """Allow only asyncio's private wakeup socketpair, never app traffic.

        On Windows, constructing a Proactor event loop implements
        ``socketpair()`` with a loopback listener.  It carries no domain data,
        but it reaches the same Python socket methods guarded below.  The
        exemption is therefore bound to both the stdlib ``socketpair`` frame
        and asyncio's private ``_make_self_pipe`` frame.
        """

        frames = traceback.extract_stack(limit=20)
        has_socketpair = any(
            frame.name == "socketpair"
            and Path(frame.filename).name.casefold() == "socket.py"
            for frame in frames
        )
        has_asyncio_self_pipe = any(
            frame.name == "_make_self_pipe"
            and "asyncio" in {part.casefold() for part in Path(frame.filename).parts}
            for frame in frames
        )
        return (
            has_asyncio_self_pipe
            and (has_socketpair or not require_socketpair_frame)
        )

    def _deny(self, operation: str):  # noqa: ANN202
        def denied(*_args: object, **_kwargs: object) -> object:
            if (
                operation == "socket.socketpair"
                and self._is_asyncio_self_pipe(require_socketpair_frame=False)
            ):
                original = self._original_for(operation)
                pair = original(*_args, **_kwargs)  # type: ignore[operator]
                if isinstance(pair, tuple):
                    for item in pair:
                        if isinstance(item, socket.socket):
                            self._asyncio_control_sockets.add(item)
                return pair
            socket_instance = (
                _args[0]
                if _args and isinstance(_args[0], socket.socket)
                else None
            )
            if operation.startswith("socket.") and self._is_asyncio_self_pipe():
                if socket_instance is not None:
                    self._asyncio_control_sockets.add(socket_instance)
                original = self._original_for(operation)
                return original(*_args, **_kwargs)  # type: ignore[operator]
            if (
                socket_instance is not None
                and socket_instance in self._asyncio_control_sockets
                and operation
                in {
                    "socket.send",
                    "socket.sendall",
                    "socket.sendmsg",
                    "socket.sendfile",
                }
            ):
                original = self._original_for(operation)
                return original(*_args, **_kwargs)  # type: ignore[operator]
            self._attempts.append(operation)
            raise M48BForbiddenIOAttempt(operation)

        return denied

    def _original_for(self, operation: str) -> object:
        return next(
            original
            for owner, name, original in reversed(self._originals)
            if operation == f"{getattr(owner, '__name__', '')}.{name}"
            or (
                owner is socket.socket
                and operation == f"socket.{name}"
            )
        )

    def _patch(self, owner: object, name: str, operation: str) -> None:
        original = getattr(owner, name)
        self._originals.append((owner, name, original))
        setattr(owner, name, self._deny(operation))

    def _restore(self) -> None:
        restore_errors: list[Exception] = []
        while self._originals:
            owner, name, original = self._originals.pop()
            try:
                setattr(owner, name, original)
            except Exception as exc:  # pragma: no cover - defensive runtime guard
                restore_errors.append(exc)
        self._asyncio_control_sockets.clear()
        self._active = False
        if restore_errors:
            raise RuntimeError(
                "M48B_NO_REAL_IO_GUARD_RESTORE_FAILED"
            ) from restore_errors[0]

    def __enter__(self) -> "NoRealIOGuard":
        if self._active:
            raise RuntimeError("M48B_NO_REAL_IO_GUARD_ALREADY_ACTIVE")
        self._active = True
        try:
            for name in (
                "connect",
                "connect_ex",
                "bind",
                "listen",
                "send",
                "sendall",
                "sendto",
                "sendmsg",
                "sendfile",
            ):
                if callable(getattr(socket.socket, name, None)):
                    self._patch(socket.socket, name, f"socket.{name}")
            for name in (
                "create_connection",
                "getaddrinfo",
                "gethostbyaddr",
                "gethostbyname",
                "gethostbyname_ex",
                "getnameinfo",
                "socketpair",
            ):
                self._patch(socket, name, f"socket.{name}")
            for name in (
                "Popen",
                "run",
                "call",
                "check_call",
                "check_output",
                "getoutput",
                "getstatusoutput",
            ):
                if callable(getattr(subprocess, name, None)):
                    self._patch(subprocess, name, f"subprocess.{name}")
            for name in ("create_subprocess_exec", "create_subprocess_shell"):
                self._patch(asyncio, name, f"asyncio.{name}")
            self._patch(
                multiprocessing.Process,
                "start",
                "multiprocessing.Process.start",
            )
            for name in ("system", "popen", "startfile", "fork", "forkpty"):
                if callable(getattr(os, name, None)):
                    self._patch(os, name, f"os.{name}")
            spawn_names = {
                name
                for name in dir(os)
                if name.startswith("spawn") or name in {"posix_spawn", "posix_spawnp"}
            }
            for name in sorted(spawn_names):
                if callable(getattr(os, name, None)):
                    self._patch(os, name, f"os.{name}")
            exec_names = {
                name
                for name in dir(os)
                if name.startswith("exec") and callable(getattr(os, name, None))
            }
            for name in sorted(exec_names):
                self._patch(os, name, f"os.{name}")
            if _winapi is not None and callable(
                getattr(_winapi, "CreateProcess", None)
            ):
                self._patch(_winapi, "CreateProcess", "_winapi.CreateProcess")
        except Exception:
            self._restore()
            raise
        return self

    def __exit__(
        self,
        _exc_type: object,
        _exc: object,
        _traceback: object,
    ) -> bool:
        self._restore()
        return False


@dataclass(frozen=True, slots=True)
class M48BP0ValidationProfile:
    name: str = M48B_P0_ISOLATED_ONLY
    domains: tuple[str, ...] = tuple(sorted(M48B_ISOLATION_DOMAINS))
    network_policy: str = "loopback_only"
    process_policy: str = "deny"
    physical_effects_allowed: bool = False
    production_credentials_allowed: bool = False
    production_state_allowed: bool = False

    def assert_safe(self) -> None:
        if self.name != M48B_P0_ISOLATED_ONLY:
            raise ValueError("M48B_P0_VALIDATION_PROFILE_INVALID")
        if set(self.domains) != M48B_ISOLATION_DOMAINS:
            raise ValueError("M48B_P0_VALIDATION_DOMAINS_INVALID")
        if self.network_policy != "loopback_only" or self.process_policy != "deny":
            raise ValueError("M48B_P0_VALIDATION_ISOLATION_INVALID")
        if (
            self.physical_effects_allowed
            or self.production_credentials_allowed
            or self.production_state_allowed
        ):
            raise ValueError("M48B_P0_VALIDATION_LIVE_EFFECT_INVALID")


@dataclass(frozen=True, slots=True)
class M48BP0ValidationRun:
    profile: M48BP0ValidationProfile
    state_root: Path
    harnesses: tuple[M48BIsolationHarness, ...]
    _forbidden_io_attempts: list[str] = field(
        default_factory=list,
        compare=False,
        repr=False,
    )

    def harness(self, domain: str) -> M48BIsolationHarness:
        normalized = str(domain or "").strip().lower()
        matches = tuple(item for item in self.harnesses if item.domain == normalized)
        if len(matches) != 1:
            raise ValueError(f"M48B_P0_VALIDATION_DOMAIN_UNAVAILABLE:{normalized}")
        return matches[0]

    @property
    def forbidden_io_attempts(self) -> tuple[str, ...]:
        return tuple(self._forbidden_io_attempts)

    def no_real_io_guard(self) -> NoRealIOGuard:
        return NoRealIOGuard(self._forbidden_io_attempts)

    def assert_isolated(
        self,
        expected_recordings: Mapping[str, int] | None = None,
    ) -> None:
        self.profile.assert_safe()
        if tuple(sorted(item.domain for item in self.harnesses)) != self.profile.domains:
            raise ValueError("M48B_P0_VALIDATION_HARNESSES_INVALID")
        if self._forbidden_io_attempts:
            raise ValueError(
                "M48B_P0_VALIDATION_FORBIDDEN_IO_ATTEMPT:"
                + ",".join(self._forbidden_io_attempts)
            )
        resolved_root = self.state_root.resolve()
        normalized_expected: dict[str, int] = {}
        for domain, count in dict(expected_recordings or {}).items():
            normalized_domain = str(domain or "").strip().lower()
            if normalized_domain not in self.profile.domains:
                raise ValueError(
                    f"M48B_P0_VALIDATION_RECORDING_DOMAIN_INVALID:{normalized_domain}"
                )
            if type(count) is not int or count < 0:
                raise ValueError(
                    f"M48B_P0_VALIDATION_RECORDING_COUNT_INVALID:{normalized_domain}"
                )
            normalized_expected[normalized_domain] = count

        for harness in self.harnesses:
            harness.assert_safe()
            try:
                common_root = os.path.commonpath(
                    (resolved_root, harness.state_root.resolve())
                )
            except ValueError as exc:
                raise ValueError("M48B_P0_VALIDATION_STATE_ESCAPE") from exc
            if common_root != str(resolved_root):
                raise ValueError("M48B_P0_VALIDATION_STATE_ESCAPE")
            for receipt in harness.sink.effects:
                if (
                    not isinstance(receipt, Mapping)
                    or receipt.get("executed") is not False
                    or receipt.get("channel") != "recording-only"
                ):
                    raise ValueError("M48B_P0_VALIDATION_REAL_EFFECT_RECORDED")
            expected_count = normalized_expected.get(harness.domain, 0)
            if len(harness.sink.effects) != expected_count:
                raise ValueError(
                    "M48B_P0_VALIDATION_RECORDING_COUNT_MISMATCH:"
                    f"{harness.domain}:expected={expected_count}:"
                    f"actual={len(harness.sink.effects)}"
                )

    def assert_safe(self) -> None:
        """Backward-compatible assertion for a run expecting zero recordings."""

        self.assert_isolated()


def resolve_m48b_p0_validation_profile(
    environ: Mapping[str, str] | None = None,
) -> M48BP0ValidationProfile:
    source: Mapping[str, str] = os.environ if environ is None else environ
    configured = str(
        source.get(M48B_P0_VALIDATION_PROFILE_ENV, M48B_P0_ISOLATED_ONLY)
        or M48B_P0_ISOLATED_ONLY
    ).strip().upper()
    if configured != M48B_P0_ISOLATED_ONLY:
        raise ValueError("M48B_P0_VALIDATION_PROFILE_INVALID")
    profile = M48BP0ValidationProfile()
    profile.assert_safe()
    return profile


def resolve_m48b_owned_state_root(
    *,
    harness: M48BIsolationHarness,
    state_root: Path,
) -> Path:
    """Resolve one test state root and reject paths outside its domain owner."""

    harness.assert_safe()
    owned_root = harness.state_root.resolve()
    resolved = Path(state_root).resolve()
    try:
        common_root = os.path.commonpath((owned_root, resolved))
    except ValueError as exc:
        raise ValueError("M48B_P0_VALIDATION_STATE_ESCAPE") from exc
    if common_root != str(owned_root):
        raise ValueError("M48B_P0_VALIDATION_STATE_ESCAPE")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def build_m48b_p0_validation_run(
    *,
    state_root: Path,
    environ: Mapping[str, str] | None = None,
) -> M48BP0ValidationRun:
    profile = resolve_m48b_p0_validation_profile(environ)
    resolved_root = Path(state_root).resolve()
    harnesses = tuple(
        build_m48b_isolation_harness(
            domain=domain,
            state_root=resolved_root / domain,
        )
        for domain in profile.domains
    )
    run = M48BP0ValidationRun(
        profile=profile,
        state_root=resolved_root,
        harnesses=harnesses,
    )
    run.assert_safe()
    return run


__all__ = [
    "M48B_FORBIDDEN_REAL_IO",
    "M48BForbiddenIOAttempt",
    "M48B_P0_ISOLATED_ONLY",
    "M48B_P0_VALIDATION_PROFILE_ENV",
    "M48BP0ValidationProfile",
    "M48BP0ValidationRun",
    "NoRealIOGuard",
    "build_m48b_p0_validation_run",
    "resolve_m48b_p0_validation_profile",
    "resolve_m48b_owned_state_root",
]
