"""Process-owned composition root for Host security services.

This module is intentionally independent from ``host_brain.app`` and any web
framework.  A worker supplies already-resolved process dependencies; the
container never reaches back into a service locator.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
import inspect
import os
from pathlib import Path
import threading
from types import MappingProxyType
from typing import Any

from host_brain.governor.tokens import TokenService
from host_brain.observability.command_composition import (
    OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED,
    ObsCommandComposition,
    build_obs_command_composition,
)
from host_brain.pentest.production_runtime import (
    PENTEST_PRODUCTION_CONSTRUCTION_FAILED,
    PentestSecureRunComposition,
    build_pentest_secure_run_composition,
)
from host_brain.skills.signed_intake_composition import (
    SKILL_PROVIDER_INTAKE_COMPOSITION_CONSTRUCTION_FAILED,
    SignedSkillProviderIntakeComposition,
    build_signed_skill_provider_intake_composition,
)


SECURITY_SERVICE_LEDGER_INVALID = "SECURITY_SERVICE_LEDGER_INVALID"
SECURITY_SERVICE_CANONICAL_DB_PATH_INVALID = (
    "SECURITY_SERVICE_CANONICAL_DB_PATH_INVALID"
)
SECURITY_SERVICE_CANONICAL_DB_PATH_MISMATCH = (
    "SECURITY_SERVICE_CANONICAL_DB_PATH_MISMATCH"
)
SECURITY_SERVICE_PROCESS_SCOPE_VIOLATION = (
    "SECURITY_SERVICE_PROCESS_SCOPE_VIOLATION"
)
SECURITY_SERVICE_CLOSE_ALREADY_RUNNING = (
    "SECURITY_SERVICE_CLOSE_ALREADY_RUNNING"
)
SECURITY_SERVICE_CLOSE_FAILED = "SECURITY_SERVICE_CLOSE_FAILED"
PENTEST_TOKEN_SERVICE_INVALID = "PENTEST_TOKEN_SERVICE_INVALID"


class SecurityServiceName(str, Enum):
    """Stable names for services owned by the process container."""

    OBS_COMMAND = "obs_command"
    PENTEST_SECURE_RUN = "pentest_secure_run"
    SIGNED_SKILL_PROVIDER_INTAKE = "signed_skill_provider_intake"


@dataclass(frozen=True, slots=True)
class SecurityServiceStatus:
    """Construction result without exposing an unstable exception string."""

    service: SecurityServiceName
    constructed: bool
    enabled: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class SecurityServiceCloseFailure:
    """Stable close failure returned for one composition slot."""

    service: SecurityServiceName
    reason_code: str


class SecurityServiceContainerCloseError(RuntimeError):
    """Raised after every eligible service has received a close attempt."""

    def __init__(self, failures: tuple[SecurityServiceCloseFailure, ...]) -> None:
        super().__init__(SECURITY_SERVICE_CLOSE_FAILED)
        self.reason_code = SECURITY_SERVICE_CLOSE_FAILED
        self.failures = failures


ObsBuilder = Callable[..., ObsCommandComposition]
PentestBuilder = Callable[..., PentestSecureRunComposition]
SignedIntakeBuilder = Callable[..., SignedSkillProviderIntakeComposition]


_CONSTRUCTION_ORDER = (
    SecurityServiceName.OBS_COMMAND,
    SecurityServiceName.PENTEST_SECURE_RUN,
    SecurityServiceName.SIGNED_SKILL_PROVIDER_INTAKE,
)
_CLOSE_REASON_CODES = {
    SecurityServiceName.OBS_COMMAND: "OBS_COMMAND_COMPOSITION_CLOSE_FAILED",
    SecurityServiceName.PENTEST_SECURE_RUN: "PENTEST_PRODUCTION_CLOSE_FAILED",
    SecurityServiceName.SIGNED_SKILL_PROVIDER_INTAKE: (
        "SKILL_PROVIDER_INTAKE_PRODUCTION_CLOSE_FAILED"
    ),
}


@dataclass(slots=True)
class ProcessSecurityServiceContainer:
    """One worker process' explicitly constructed security service graph.

    ``ledger`` and ``token_service`` are borrowed process dependencies.  This
    container closes only the three compositions it builds, never the shared
    ledger or token service.
    """

    ledger: Any
    canonical_db_path: str
    token_service: TokenService | None
    obs_command_composition: ObsCommandComposition | None
    pentest_secure_run_composition: PentestSecureRunComposition | None
    signed_skill_provider_intake_composition: (
        SignedSkillProviderIntakeComposition | None
    )
    statuses: Mapping[SecurityServiceName, SecurityServiceStatus]
    enabled_services: frozenset[SecurityServiceName]
    owner_process_id: int
    _closed_services: set[SecurityServiceName] = field(
        default_factory=set,
        init=False,
        repr=False,
    )
    _closed: bool = field(default=False, init=False, repr=False)
    _closing: bool = field(default=False, init=False, repr=False)
    _last_close_failures: tuple[SecurityServiceCloseFailure, ...] = field(
        default=(),
        init=False,
        repr=False,
    )
    _lifecycle_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def last_close_failures(self) -> tuple[SecurityServiceCloseFailure, ...]:
        return self._last_close_failures

    @property
    def construction_order(self) -> tuple[SecurityServiceName, ...]:
        """Return constructed slots in their deterministic ownership order."""

        return tuple(
            service
            for service in _CONSTRUCTION_ORDER
            if self._composition(service) is not None
        )

    def status(
        self,
        service: SecurityServiceName | str,
    ) -> SecurityServiceStatus:
        return self.statuses[SecurityServiceName(service)]

    def close(self) -> None:
        """Synchronously close compositions in reverse construction order.

        OBS closes admission before releasing its stores.  If an OBS command
        is still in flight, its native synchronous close fails with the stable
        drain-required contract; already-closed services stay closed and a
        later ``aclose`` may finish the drain.
        """

        if not self._begin_close():
            return
        failures: list[SecurityServiceCloseFailure] = []
        try:
            for service in reversed(self.construction_order):
                if service in self._closed_services:
                    continue
                composition = self._composition(service)
                try:
                    close = getattr(composition, "close", None)
                    if not callable(close):
                        raise RuntimeError("close unavailable")
                    result = close()
                    if inspect.isawaitable(result):
                        close_coroutine = getattr(result, "close", None)
                        if callable(close_coroutine):
                            close_coroutine()
                        raise RuntimeError("async close returned from sync API")
                except Exception:
                    failures.append(self._close_failure(service))
                else:
                    self._closed_services.add(service)
        finally:
            self._finish_close(failures)
        if failures:
            raise SecurityServiceContainerCloseError(tuple(failures))

    async def aclose(self) -> None:
        """Close in reverse order, awaiting OBS admission drain and release."""

        if not self._begin_close():
            return
        failures: list[SecurityServiceCloseFailure] = []
        try:
            for service in reversed(self.construction_order):
                if service in self._closed_services:
                    continue
                composition = self._composition(service)
                try:
                    if service is SecurityServiceName.OBS_COMMAND:
                        aclose = getattr(composition, "aclose", None)
                        if callable(aclose):
                            await aclose()
                        else:
                            close = getattr(composition, "close", None)
                            if not callable(close):
                                raise RuntimeError("close unavailable")
                            result = close()
                            if inspect.isawaitable(result):
                                await result
                    else:
                        close = getattr(composition, "close", None)
                        if not callable(close):
                            raise RuntimeError("close unavailable")
                        result = close()
                        if inspect.isawaitable(result):
                            await result
                except Exception:
                    failures.append(self._close_failure(service))
                else:
                    self._closed_services.add(service)
        finally:
            self._finish_close(failures)
        if failures:
            raise SecurityServiceContainerCloseError(tuple(failures))

    def _begin_close(self) -> bool:
        self._assert_process_owner()
        with self._lifecycle_lock:
            if self._closed:
                return False
            if self._closing:
                raise RuntimeError(SECURITY_SERVICE_CLOSE_ALREADY_RUNNING)
            self._closing = True
            return True

    def _finish_close(
        self,
        failures: list[SecurityServiceCloseFailure],
    ) -> None:
        with self._lifecycle_lock:
            self._last_close_failures = tuple(failures)
            constructed = set(self.construction_order)
            self._closed = constructed.issubset(self._closed_services)
            self._closing = False

    def _assert_process_owner(self) -> None:
        if os.getpid() != self.owner_process_id:
            raise RuntimeError(SECURITY_SERVICE_PROCESS_SCOPE_VIOLATION)

    def _composition(self, service: SecurityServiceName) -> Any | None:
        if service is SecurityServiceName.OBS_COMMAND:
            return self.obs_command_composition
        if service is SecurityServiceName.PENTEST_SECURE_RUN:
            return self.pentest_secure_run_composition
        return self.signed_skill_provider_intake_composition

    @staticmethod
    def _close_failure(
        service: SecurityServiceName,
    ) -> SecurityServiceCloseFailure:
        return SecurityServiceCloseFailure(
            service=service,
            reason_code=_CLOSE_REASON_CODES[service],
        )


def build_process_security_service_container(
    *,
    ledger: Any,
    canonical_db_path: str | Path,
    token_service: TokenService,
    env: Mapping[str, str] | None = None,
    obs_builder: ObsBuilder | None = None,
    pentest_builder: PentestBuilder | None = None,
    signed_intake_builder: SignedIntakeBuilder | None = None,
) -> ProcessSecurityServiceContainer:
    """Build independent service slots from explicit process dependencies.

    A failed slot is recorded with a stable reason and never prevents a later
    slot from being built.  A ledger/path authority failure disables every
    SQLite-backed slot because allowing a split audit authority is unsafe.
    """

    source = MappingProxyType(dict(os.environ if env is None else env))
    owner_process_id = os.getpid()
    resolved_path, authority_failure = _resolve_canonical_db_authority(
        ledger=ledger,
        canonical_db_path=canonical_db_path,
    )
    statuses: dict[SecurityServiceName, SecurityServiceStatus] = {}
    compositions: dict[SecurityServiceName, Any | None] = {
        service: None for service in _CONSTRUCTION_ORDER
    }

    if authority_failure is not None:
        for service in _CONSTRUCTION_ORDER:
            statuses[service] = SecurityServiceStatus(
                service=service,
                constructed=False,
                enabled=False,
                reason_code=authority_failure,
            )
        return _new_container(
            ledger=ledger,
            canonical_db_path=resolved_path,
            token_service=(
                token_service if type(token_service) is TokenService else None
            ),
            compositions=compositions,
            statuses=statuses,
            owner_process_id=owner_process_id,
        )

    obs_factory = obs_builder or build_obs_command_composition
    pentest_factory = pentest_builder or build_pentest_secure_run_composition
    intake_factory = (
        signed_intake_builder
        or build_signed_skill_provider_intake_composition
    )

    obs, obs_status = _construct_slot(
        service=SecurityServiceName.OBS_COMMAND,
        expected_type=ObsCommandComposition,
        failure_reason=OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED,
        factory=lambda: obs_factory(env=source, db_path=resolved_path),
    )
    compositions[SecurityServiceName.OBS_COMMAND] = obs
    statuses[SecurityServiceName.OBS_COMMAND] = obs_status

    if type(token_service) is TokenService:
        pentest, pentest_status = _construct_slot(
            service=SecurityServiceName.PENTEST_SECURE_RUN,
            expected_type=PentestSecureRunComposition,
            failure_reason=PENTEST_PRODUCTION_CONSTRUCTION_FAILED,
            factory=lambda: pentest_factory(
                token_service=token_service,
                env=source,
                db_path=resolved_path,
            ),
        )
    else:
        pentest = None
        pentest_status = SecurityServiceStatus(
            service=SecurityServiceName.PENTEST_SECURE_RUN,
            constructed=False,
            enabled=False,
            reason_code=PENTEST_TOKEN_SERVICE_INVALID,
        )
    compositions[SecurityServiceName.PENTEST_SECURE_RUN] = pentest
    statuses[SecurityServiceName.PENTEST_SECURE_RUN] = pentest_status

    intake, intake_status = _construct_slot(
        service=SecurityServiceName.SIGNED_SKILL_PROVIDER_INTAKE,
        expected_type=SignedSkillProviderIntakeComposition,
        failure_reason=SKILL_PROVIDER_INTAKE_COMPOSITION_CONSTRUCTION_FAILED,
        factory=lambda: intake_factory(env=source, db_path=resolved_path),
    )
    compositions[SecurityServiceName.SIGNED_SKILL_PROVIDER_INTAKE] = intake
    statuses[SecurityServiceName.SIGNED_SKILL_PROVIDER_INTAKE] = intake_status

    return _new_container(
        ledger=ledger,
        canonical_db_path=resolved_path,
        token_service=(
            token_service if type(token_service) is TokenService else None
        ),
        compositions=compositions,
        statuses=statuses,
        owner_process_id=owner_process_id,
    )


def _new_container(
    *,
    ledger: Any,
    canonical_db_path: str,
    token_service: TokenService | None,
    compositions: Mapping[SecurityServiceName, Any | None],
    statuses: Mapping[SecurityServiceName, SecurityServiceStatus],
    owner_process_id: int,
) -> ProcessSecurityServiceContainer:
    frozen_statuses = MappingProxyType(dict(statuses))
    enabled_services = frozenset(
        service for service, status in statuses.items() if status.enabled
    )
    return ProcessSecurityServiceContainer(
        ledger=ledger,
        canonical_db_path=canonical_db_path,
        token_service=token_service,
        obs_command_composition=compositions[SecurityServiceName.OBS_COMMAND],
        pentest_secure_run_composition=compositions[
            SecurityServiceName.PENTEST_SECURE_RUN
        ],
        signed_skill_provider_intake_composition=compositions[
            SecurityServiceName.SIGNED_SKILL_PROVIDER_INTAKE
        ],
        statuses=frozen_statuses,
        enabled_services=enabled_services,
        owner_process_id=owner_process_id,
    )


def _construct_slot(
    *,
    service: SecurityServiceName,
    expected_type: type[Any],
    failure_reason: str,
    factory: Callable[[], Any],
) -> tuple[Any | None, SecurityServiceStatus]:
    try:
        composition = factory()
        if type(composition) is not expected_type:
            raise TypeError("unexpected composition type")
        enabled = getattr(composition, "enabled", None)
        reason_code = str(getattr(composition, "reason_code", "") or "").strip()
        if type(enabled) is not bool or not reason_code:
            raise TypeError("invalid composition status")
    except Exception:
        return None, SecurityServiceStatus(
            service=service,
            constructed=False,
            enabled=False,
            reason_code=failure_reason,
        )
    return composition, SecurityServiceStatus(
        service=service,
        constructed=True,
        enabled=enabled,
        reason_code=reason_code,
    )


def _resolve_canonical_db_authority(
    *,
    ledger: Any,
    canonical_db_path: str | Path,
) -> tuple[str, str | None]:
    try:
        explicit = _canonicalize_db_path(canonical_db_path)
    except Exception:
        return "", SECURITY_SERVICE_CANONICAL_DB_PATH_INVALID
    if ledger is None or callable(ledger):
        return explicit, SECURITY_SERVICE_LEDGER_INVALID
    try:
        ledger_path = _canonicalize_db_path(
            getattr(ledger, "canonical_db_path")
        )
    except Exception:
        return explicit, SECURITY_SERVICE_LEDGER_INVALID
    if _path_identity(explicit) != _path_identity(ledger_path):
        return explicit, SECURITY_SERVICE_CANONICAL_DB_PATH_MISMATCH
    return explicit, None


def _canonicalize_db_path(value: str | Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("empty canonical database path")
    if raw == ":memory:":
        return raw
    return str(Path(raw).expanduser().resolve(strict=False))


def _path_identity(value: str) -> str:
    if value == ":memory:":
        return value
    return os.path.normcase(os.path.normpath(value))


__all__ = [
    "PENTEST_TOKEN_SERVICE_INVALID",
    "ProcessSecurityServiceContainer",
    "SECURITY_SERVICE_CANONICAL_DB_PATH_INVALID",
    "SECURITY_SERVICE_CANONICAL_DB_PATH_MISMATCH",
    "SECURITY_SERVICE_CLOSE_ALREADY_RUNNING",
    "SECURITY_SERVICE_CLOSE_FAILED",
    "SECURITY_SERVICE_LEDGER_INVALID",
    "SECURITY_SERVICE_PROCESS_SCOPE_VIOLATION",
    "SecurityServiceCloseFailure",
    "SecurityServiceContainerCloseError",
    "SecurityServiceName",
    "SecurityServiceStatus",
    "build_process_security_service_container",
]
