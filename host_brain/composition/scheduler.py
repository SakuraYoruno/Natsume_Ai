"""Process-owned composition for the governed durable scheduler."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import datetime as dt
import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from natsume_contracts.identity import (
    CredentialProof,
    CredentialProofKind,
    CredentialProofMetadata,
    IdentitySchemaVersion,
    IngressIdentityEnvelope,
    Principal,
    PrincipalKind,
)

from host_brain.scheduler.command_ingress import (
    SchedulerClockSigningContext,
    SchedulerCommandIngress,
)
from host_brain.scheduler.contracts import validate_definition_payload
from host_brain.scheduler.governed_runtime import (
    GovernedSchedulerRuntime,
    SchedulerCommandService,
    SchedulerDefinitionPolicyEvaluator,
    build_governed_scheduler_runtime,
)
from host_brain.scheduler.worker_registry import (
    SchedulerHandler,
    SchedulerWorkerRegistry,
)
from host_brain.secure_ingress import (
    IngressSurface,
    SECURE_INGRESS_SURFACE_POLICIES,
    SecureIngressAuthorityEvaluator,
    build_hmac_sha256_signature,
)


SCHEDULER_COMPOSITION_INVALID = "SCHEDULER_COMPOSITION_INVALID"
SCHEDULER_CLOCK_IDENTITY_INVALID = "SCHEDULER_CLOCK_IDENTITY_INVALID"


class SchedulerCompositionError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = str(reason_code or SCHEDULER_COMPOSITION_INVALID)
        self.detail = str(detail or "scheduler composition is invalid")
        super().__init__(f"{self.reason_code}: {self.detail}")


@dataclass(frozen=True, slots=True)
class SchedulerClockIdentityConfig:
    principal_id: str
    tenant_id: str
    key_id: str
    secret: str = field(repr=False)
    proof_ttl_seconds: int = 300

    def validate(self) -> None:
        required = {
            "principal_id": self.principal_id,
            "tenant_id": self.tenant_id,
            "key_id": self.key_id,
            "secret": self.secret,
        }
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise SchedulerCompositionError(
                SCHEDULER_CLOCK_IDENTITY_INVALID,
                f"clock identity lacks explicit {','.join(missing)}",
            )
        if int(self.proof_ttl_seconds) < 30:
            raise SchedulerCompositionError(
                SCHEDULER_CLOCK_IDENTITY_INVALID,
                "clock proof TTL must be at least 30 seconds",
            )


class HmacSchedulerClockCredentialProvider:
    """Issue clock proofs only from one explicitly configured service identity."""

    def __init__(self, config: SchedulerClockIdentityConfig) -> None:
        config.validate()
        self._config = config

    def identity_for(
        self,
        *,
        context: SchedulerClockSigningContext,
    ) -> IngressIdentityEnvelope:
        issued_at = int(context.received_at_epoch_ms)
        metadata = CredentialProofMetadata(
            kind=CredentialProofKind.HMAC_SHA256,
            key_id=self._config.key_id,
            algorithm="hmac-sha256",
            proof_id=f"scheduler.clock.proof.{context.body_sha256[:40]}",
            issued_at_epoch_ms=issued_at,
            expires_at_epoch_ms=(
                issued_at + int(self._config.proof_ttl_seconds) * 1_000
            ),
        )
        identity = IngressIdentityEnvelope(
            schema_version=IdentitySchemaVersion.V1,
            principal=Principal(
                principal_id=self._config.principal_id,
                kind=PrincipalKind.SERVICE,
                tenant_id=self._config.tenant_id,
                granted_scopes=context.required_scopes,
            ),
            credential_proof=metadata,
            audience=context.audience,
            request_id=context.request_id,
            requested_scopes=context.required_scopes,
        )
        return identity

    def issue_proof(self, *, request: Any) -> CredentialProof:
        identity = getattr(request, "identity", None)
        if not isinstance(identity, IngressIdentityEnvelope) or (
            identity.principal.principal_id != self._config.principal_id
            or identity.principal.kind is not PrincipalKind.SERVICE
            or identity.credential_proof.key_id != self._config.key_id
        ):
            raise SchedulerCompositionError(
                SCHEDULER_CLOCK_IDENTITY_INVALID,
                "clock proof request differs from configured service identity",
            )
        return CredentialProof(
            metadata=identity.credential_proof,
            raw_proof=build_hmac_sha256_signature(request, self._config.secret),
        )


@dataclass(slots=True)
class ProcessSchedulerContainer:
    runtime: GovernedSchedulerRuntime
    command_service: SchedulerCommandService
    command_ingress: SchedulerCommandIngress
    worker_registry: SchedulerWorkerRegistry
    definition_repository: Any
    clock: Any | None
    seeded_task_ids: tuple[str, ...]
    _clock_active: bool = False

    async def activate_clock(self) -> None:
        if self.clock is None or self._clock_active:
            return
        await self.clock.activate()
        self._clock_active = True

    async def stop_clock(self) -> None:
        if self.clock is None or not self._clock_active:
            return
        await self.clock.aclose()
        self._clock_active = False

    def health(self) -> dict[str, Any]:
        result = dict(self.command_service.health())
        result.update(
            {
                "clock_configured": self.clock is not None,
                "clock_active": self._clock_active,
                "seeded_task_ids": list(self.seeded_task_ids),
            }
        )
        return result


def build_process_scheduler_container(
    *,
    task_runtime_container: Any,
    ledger: Any,
    token_service: Any,
    evaluator: SecureIngressAuthorityEvaluator,
    claim_result_store: Any,
    intent_engine: Any,
    runtime_router: Any,
    owner_id: str,
    current_mode: Callable[[], str],
    default_handler: SchedulerHandler,
    task_handlers: Mapping[str, SchedulerHandler] | None = None,
    seed_definitions: Iterable[Any] = (),
    seed_worker_keys: Mapping[str, str] | None = None,
    resource_gate: Callable[[Any, str], Mapping[str, Any] | bool] | None = None,
    clock_enabled: bool = False,
    clock_identity: SchedulerClockIdentityConfig | None = None,
    clock_poll_interval_seconds: float = 1.0,
    clock_batch_limit: int = 64,
    now_ms: Callable[[], int] | None = None,
    cancellation_authority_verifier: Any | None = None,
    runtime_context_application: Any | None = None,
    action_preview_service: Any | None = None,
    worker_registry: SchedulerWorkerRegistry | None = None,
    acceptance_effect_dispatcher: Any | None = None,
) -> ProcessSchedulerContainer:
    definitions = getattr(
        task_runtime_container,
        "scheduler_definition_repository",
        None,
    )
    results = getattr(task_runtime_container, "scheduler_result_repository", None)
    if definitions is None or results is None:
        raise SchedulerCompositionError(
            SCHEDULER_COMPOSITION_INVALID,
            "Task Runtime lacks scheduler repositories",
        )
    clock_now = now_ms or (lambda: int(time.time() * 1_000))
    workers = worker_registry or SchedulerWorkerRegistry()
    workers.register("scheduler_production", default_handler)
    for worker_key, handler in dict(task_handlers or {}).items():
        workers.register(str(worker_key), handler)
    policy = SchedulerDefinitionPolicyEvaluator(
        current_mode=current_mode,
        resource_gate=resource_gate,
    )
    runtime = build_governed_scheduler_runtime(
        task_runtime_container=task_runtime_container,
        ledger=ledger,
        token_service=token_service,
        result_repository=results,
        runtime_router=runtime_router,
        worker_registry=workers,
        owner_id=owner_id,
        definition_repository=definitions,
        cancellation_authority_verifier=cancellation_authority_verifier,
        runtime_context_application=runtime_context_application,
        action_preview_service=action_preview_service,
        acceptance_effect_dispatcher=acceptance_effect_dispatcher,
    )
    service = SchedulerCommandService(
        runtime=runtime,
        intent_engine=intent_engine,
        claim_result_store=claim_result_store,
        definition_repository=definitions,
        policy_evaluator=policy,
        worker_registry=workers,
    )

    seeded: list[str] = []
    seed_now = int(clock_now())
    for candidate in seed_definitions:
        task_id, definition, next_at = scheduler_seed_payload(candidate, now_ms=seed_now)
        worker_key = str(dict(seed_worker_keys or {}).get(task_id) or "").strip()
        if worker_key:
            definition = validate_definition_payload(
                task_id=task_id,
                definition={**definition, "worker_key": worker_key},
            )
        registered_worker = str(definition.get("worker_key") or "").strip().lower()
        if registered_worker not in workers.worker_keys:
            raise SchedulerCompositionError(
                SCHEDULER_COMPOSITION_INVALID,
                f"scheduler seed uses an unregistered worker: {registered_worker}",
            )
        existing = definitions.get(task_id)
        if existing is None:
            definitions.publish_definition(
                task_id=task_id,
                definition=definition,
                expected_head_version=0,
                next_scheduled_at_ms=next_at,
            )
        seeded.append(task_id)

    credential_provider = None
    if clock_enabled:
        if clock_identity is None:
            raise SchedulerCompositionError(
                SCHEDULER_CLOCK_IDENTITY_INVALID,
                "enabled scheduler clock requires an explicit identity",
            )
        clock_identity.validate()
        _validate_registered_clock_identity(
            authority_store=claim_result_store,
            config=clock_identity,
            at_ms=seed_now,
        )
        credential_provider = HmacSchedulerClockCredentialProvider(clock_identity)
    ingress = SchedulerCommandIngress(
        evaluator=evaluator,
        clock_credential_provider=credential_provider,
    )
    clock = None
    if clock_enabled:
        from host_brain.scheduler.clock import SchedulerClock  # noqa: PLC0415

        clock = SchedulerClock(
            definition_repository=definitions,
            command_ingress=ingress,
            command_service=service,
            now_ms=clock_now,
            poll_interval_s=float(clock_poll_interval_seconds),
            limit=int(clock_batch_limit),
        )
    return ProcessSchedulerContainer(
        runtime=runtime,
        command_service=service,
        command_ingress=ingress,
        worker_registry=workers,
        definition_repository=definitions,
        clock=clock,
        seeded_task_ids=tuple(seeded),
    )


def scheduler_seed_payload(
    candidate: Any,
    *,
    now_ms: int,
) -> tuple[str, dict[str, Any], int | None]:
    """Convert legacy declarative config into one frozen durable definition."""

    if isinstance(candidate, Mapping):
        raw = dict(candidate)
        task_id = str(raw.get("task_id") or "").strip()
        definition = dict(raw.get("definition") or raw)
        next_at = raw.get("next_scheduled_at_ms")
    else:
        task_id = str(getattr(candidate, "task_id", "") or "").strip()
        trigger_mode = getattr(candidate, "trigger_mode", "")
        priority = getattr(candidate, "priority", "MEDIUM")
        definition = {
            "task_id": task_id,
            "trigger_mode": str(getattr(trigger_mode, "value", trigger_mode)).lower(),
            "trigger_config": dict(getattr(candidate, "trigger_config", {}) or {}),
            "intent_template": dict(getattr(candidate, "intent_template", {}) or {}),
            "priority": str(getattr(priority, "value", priority)).upper(),
            "mode_allowlist": getattr(candidate, "mode_allowlist", None),
            "enabled": bool(getattr(candidate, "enabled", True)),
            "retry_policy": {
                "max_attempts": max(
                    1,
                    int(getattr(candidate, "max_consecutive_failures", 1) or 1),
                ),
                "retry_backoff_ms": 0,
            },
            "description": str(getattr(candidate, "description", "") or ""),
            "resource_profile": dict(
                getattr(candidate, "resource_profile", {}) or {}
            ),
            "worker_key": "scheduler_production",
        }
        next_at = None
    definition.setdefault("task_id", task_id)
    definition.setdefault("worker_key", "scheduler_production")
    definition = validate_definition_payload(task_id=task_id, definition=definition)
    if next_at is None and bool(definition.get("enabled", True)):
        next_at = _first_schedule_at(definition, now_ms=now_ms)
    return task_id, definition, int(next_at) if next_at is not None else None


def _first_schedule_at(definition: Mapping[str, Any], *, now_ms: int) -> int | None:
    mode = str(definition.get("trigger_mode") or "").strip().lower()
    config = dict(definition.get("trigger_config") or {})
    if mode == "interval":
        seconds = float(config.get("seconds") or 0)
        return int(now_ms + seconds * 1_000) if seconds > 0 else None
    if mode == "once":
        explicit = config.get("run_at_ms", config.get("scheduled_at_ms"))
        if explicit is not None:
            return max(int(now_ms), int(explicit))
        delay = float(config.get("delay_seconds") or 0)
        return int(now_ms + max(0.0, delay) * 1_000)
    if mode != "cron":
        return None
    hour = int(config.get("hour", 0))
    minute = int(config.get("minute", 0))
    timezone_name = str(config.get("timezone") or "UTC").strip() or "UTC"
    if timezone_name.upper() in {"UTC", "ETC/UTC", "Z"}:
        # UTC is part of Python itself and must not depend on an optional IANA
        # tzdata installation (notably absent in a clean Windows venv).
        timezone = dt.timezone.utc
    else:
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise SchedulerCompositionError(
                SCHEDULER_COMPOSITION_INVALID,
                f"scheduler timezone is unavailable: {timezone_name}",
            ) from exc
    now = dt.datetime.fromtimestamp(now_ms / 1_000, tz=timezone)
    weekdays_raw = config.get("day_of_week")
    weekdays = (
        {int(item) for item in weekdays_raw}
        if isinstance(weekdays_raw, (list, tuple, set))
        else ({int(weekdays_raw)} if weekdays_raw is not None else None)
    )
    catchup = max(0, int(config.get("catch_up_window_minutes") or 0))
    for back in range(0, 8):
        day = now - dt.timedelta(days=back)
        if weekdays is not None and day.weekday() not in weekdays:
            continue
        scheduled = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled <= now <= scheduled + dt.timedelta(minutes=catchup):
            return int(scheduled.timestamp() * 1_000)
        break
    for ahead in range(0, 8):
        day = now + dt.timedelta(days=ahead)
        if weekdays is not None and day.weekday() not in weekdays:
            continue
        scheduled = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled > now:
            return int(scheduled.timestamp() * 1_000)
    return None


def _validate_registered_clock_identity(
    *,
    authority_store: Any,
    config: SchedulerClockIdentityConfig,
    at_ms: int,
) -> None:
    policy = SECURE_INGRESS_SURFACE_POLICIES[IngressSurface.SCHEDULER_ADMIN]
    resolve = getattr(authority_store, "resolve", None)
    if not callable(resolve):
        raise SchedulerCompositionError(
            SCHEDULER_CLOCK_IDENTITY_INVALID,
            "clock authority store has no read-only credential resolver",
        )
    authority = resolve(
        principal_id=config.principal_id,
        key_id=config.key_id,
        at_ms=int(at_ms),
    )
    if not bool(
        authority is not None
        and getattr(authority, "enabled", False)
        and getattr(authority, "kind", None) is PrincipalKind.SERVICE
        and str(getattr(authority, "tenant_id", "")) == config.tenant_id
        and getattr(authority, "credential_kind", None)
        is CredentialProofKind.HMAC_SHA256
        and str(getattr(authority, "algorithm", "")) == "hmac-sha256"
        and set(policy.required_scopes).issubset(
            set(getattr(authority, "granted_scopes", ()) or ())
        )
        and policy.audience
        in set(getattr(authority, "allowed_audiences", ()) or ())
        and policy.action in set(getattr(authority, "allowed_actions", ()) or ())
    ):
        raise SchedulerCompositionError(
            SCHEDULER_CLOCK_IDENTITY_INVALID,
            "scheduler clock service credential is not durably registered",
        )


__all__ = [
    "HmacSchedulerClockCredentialProvider",
    "ProcessSchedulerContainer",
    "SCHEDULER_CLOCK_IDENTITY_INVALID",
    "SCHEDULER_COMPOSITION_INVALID",
    "SchedulerClockIdentityConfig",
    "SchedulerCompositionError",
    "build_process_scheduler_container",
    "scheduler_seed_payload",
]
