"""Production ``host.main`` adapter for durable Service Catalog activation."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import os
import socket
import sys
import threading
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from host_brain.service_catalog.startup_coordinator import (
    MODE_CANARY,
    MODE_ENFORCE,
    ServiceCatalogStartupCoordinator,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.service_catalog_rollout_repository import (
    SQLiteServiceCatalogRolloutRepository,
)
from host_brain.service_catalog.defaults import build_local_catalog
from host_brain.contracts.service_catalog import (
    CatalogMode,
    ServiceCatalogRolloutError,
    ServiceCatalogRolloutRepository,
    catalog_operation_identity_digest,
)
from host_brain.service_catalog.models import EndpointSpec, Transport
from host_brain.service_catalog.probes import HealthProbeResult, strict_socket_addresses
from host_brain.service_catalog.rollout_runtime import (
    CatalogActivationOutcomeV1,
    DurableServiceCatalogActivationRunner,
    IdentityCapturingHealthProbe,
)
from host_brain.service_catalog.socket_reservation import OwnedSocketReservationProbe
from host_brain.core.errors import ACCEPTANCE_PENDING_EXTERNAL


HOST_MAIN_ENDPOINT_ID = "host.main.http"
HOST_MAIN_OWNER_ID = "host.main"
HOST_MAIN_PROTOCOL_ID = "http"
HOST_MAIN_PROTOCOL_VERSION = "0.1"
HOST_CATALOG_OPERATION_DIGEST_HEADER = "x-natsume-catalog-operation-digest"
HOST_CATALOG_SNAPSHOT_DIGEST_HEADER = "x-natsume-catalog-snapshot-digest"
HOST_CATALOG_CANDIDATE_OWNER_HEADER = "x-natsume-catalog-candidate-owner"
HOST_CATALOG_STAGE_HEADER = "x-natsume-catalog-stage"
HOST_SERVICE_CATALOG_TARGET_MODE_ENV = "HOST_SERVICE_CATALOG_TARGET_MODE"
HOST_SERVICE_CATALOG_ROLLBACK_REF_ENV = "HOST_SERVICE_CATALOG_ROLLBACK_REF"
HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS_ENV = (
    "HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS"
)
DEFAULT_HOST_SERVICE_CATALOG_TARGET_MODE = CatalogMode.ENFORCE
DEFAULT_HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS = 60_000
_ROLLOUT_STAGES = (
    CatalogMode.AUDIT,
    CatalogMode.SHADOW,
    CatalogMode.PREFLIGHT,
    CatalogMode.CANARY,
    CatalogMode.ENFORCE,
)
_ROLLOUT_STAGE_RANK = {stage: index for index, stage in enumerate(_ROLLOUT_STAGES)}


def host_main_health_identity() -> dict[str, str]:
    return {
        "endpoint_id": HOST_MAIN_ENDPOINT_ID,
        "owner_id": HOST_MAIN_OWNER_ID,
        "protocol_id": HOST_MAIN_PROTOCOL_ID,
        "protocol_version": HOST_MAIN_PROTOCOL_VERSION,
    }


def host_candidate_loop_factory() -> asyncio.AbstractEventLoop:
    """Return the loop factory used by the transferred Host listener.

    Uvicorn selects ``ProactorEventLoop`` on Windows.  A reset client can make
    its overlapped ``accept`` callback detach the pre-bound socket, after which
    the otherwise healthy Service Catalog candidate silently loses its only
    listener.  The selector loop owns ordinary TCP listeners reliably here;
    blocking/subprocess work is already isolated behind Host adapters.
    """

    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


class HostHttpHealthProbe:
    """Read real HTTP health and retain exact Host/Guardian identity evidence."""

    def __init__(self, *, timeout_s: float = 2.0) -> None:
        self._timeout_s = float(timeout_s)
        self._payloads: dict[str, dict[str, Any]] = {}

    def inspect(self, endpoint: EndpointSpec) -> HealthProbeResult:
        host = endpoint.bind_host.strip("[]")
        if host in {"0.0.0.0", "::", "*"}:
            host = "127.0.0.1"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        path = endpoint.health.path
        separator = "&" if "?" in path else "?"
        url = f"http://{host}:{endpoint.port}{path}{separator}skip_dependency_probes=true"
        try:
            request = Request(url, headers={"Accept": "application/json"})
            with urlopen(request, timeout=self._timeout_s) as response:  # noqa: S310
                status_code = int(response.status)
                activation_identity = {
                    "operation_id_digest": str(
                        response.headers.get(HOST_CATALOG_OPERATION_DIGEST_HEADER)
                        or ""
                    ),
                    "catalog_snapshot_digest": str(
                        response.headers.get(HOST_CATALOG_SNAPSHOT_DIGEST_HEADER)
                        or ""
                    ),
                    "candidate_owner_id": str(
                        response.headers.get(HOST_CATALOG_CANDIDATE_OWNER_HEADER)
                        or ""
                    ),
                    "stage": str(
                        response.headers.get(HOST_CATALOG_STAGE_HEADER) or ""
                    ),
                }
                document = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return HealthProbeResult(
                endpoint_id=endpoint.endpoint_id,
                healthy=False,
                status_code=int(exc.code),
                detail="Host health returned HTTP error",
            )
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            return HealthProbeResult(
                endpoint_id=endpoint.endpoint_id,
                healthy=False,
                detail=type(exc).__name__,
            )
        details = document.get("details") if isinstance(document, Mapping) else None
        details = dict(details) if isinstance(details, Mapping) else {}
        health_identity = details.get("service_identity")
        health_identity = dict(health_identity) if isinstance(health_identity, Mapping) else {}
        degradation = details.get("degradation_state")
        degradation = dict(degradation) if isinstance(degradation, Mapping) else {}
        runtime_authority = degradation.get("guardian_authority")
        runtime_authority = dict(runtime_authority) if isinstance(runtime_authority, Mapping) else {}
        self._payloads[endpoint.endpoint_id] = {
            "health_identity": health_identity,
            "runtime_authority": runtime_authority,
            "activation_identity": activation_identity,
        }
        return HealthProbeResult(
            endpoint_id=endpoint.endpoint_id,
            healthy=(
                200 <= status_code < 300
                # External acceptance can remain pending while this exact
                # local Host candidate is alive and identity-valid.  Treating
                # that state as a dead candidate prevents the Host from
                # exposing the truthful NOT_TESTED readiness payload at all.
                and str(document.get("status") or "").upper()
                in {"OK", "DEGRADED", ACCEPTANCE_PENDING_EXTERNAL}
            ),
            status_code=status_code,
            owner_id=str(health_identity.get("owner_id") or ""),
            protocol_id=str(health_identity.get("protocol_id") or ""),
            protocol_version=str(health_identity.get("protocol_version") or ""),
            detail=str(details.get("reason_code") or ""),
        )

    def identity_payload(self, endpoint_id: str) -> dict[str, Any] | None:
        payload = self._payloads.get(str(endpoint_id))
        return dict(payload) if payload is not None else None


class HostUvicornCandidate:
    """Uvicorn candidate using exact transferred or locally prebound sockets."""

    def __init__(
        self,
        *,
        endpoint: EndpointSpec,
        reservation_probe: OwnedSocketReservationProbe,
        snapshot_digest: str,
        operation_id: str,
        stage: CatalogMode,
        use_reserved_sockets: bool,
        log_level: str,
        access_log: bool,
        app: Any = "host_brain.app.asgi:app",
        startup_timeout_s: float = 45.0,
    ) -> None:
        self._endpoint = endpoint
        self._reservation_probe = reservation_probe
        self._snapshot_digest = snapshot_digest
        self._operation_id = str(operation_id).strip()
        self._stage = stage if isinstance(stage, CatalogMode) else CatalogMode(str(stage))
        self._operation_id_digest = catalog_operation_identity_digest(
            self._operation_id
        )
        self._use_reserved = bool(use_reserved_sockets)
        self._log_level = str(log_level).lower()
        self._access_log = bool(access_log)
        self._app = app
        self._startup_timeout_s = float(startup_timeout_s)
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._sockets: tuple[socket.socket, ...] = ()
        self._failure: BaseException | None = None
        self.candidate_handle: HostUvicornCandidate | None = None

    def __call__(self) -> "HostUvicornCandidate":
        import uvicorn

        if self._thread is not None:
            raise ServiceCatalogRolloutError("Host candidate start was replayed")
        sockets = self._take_or_bind_sockets()
        for owned in sockets:
            if self._endpoint.transport is Transport.TCP:
                owned.listen(2048)
            owned.setblocking(False)
        config = uvicorn.Config(
            self._app,
            log_level=self._log_level,
            loop=(
                "host_brain.service_catalog.host_startup:"
                "host_candidate_loop_factory"
            ),
            access_log=self._access_log,
            headers=[
                (HOST_CATALOG_OPERATION_DIGEST_HEADER, self._operation_id_digest),
                (HOST_CATALOG_SNAPSHOT_DIGEST_HEADER, self._snapshot_digest),
                (HOST_CATALOG_CANDIDATE_OWNER_HEADER, self._endpoint.owner_id),
                (HOST_CATALOG_STAGE_HEADER, self._stage.value),
            ],
        )
        server = uvicorn.Server(config)
        self._server = server
        self._sockets = sockets

        def serve() -> None:
            try:
                server.run(sockets=list(sockets))
            except BaseException as exc:  # surfaced to the activation authority
                self._failure = exc

        thread = threading.Thread(target=serve, name="host-main-catalog-candidate", daemon=False)
        self._thread = thread
        thread.start()
        deadline = time.monotonic() + self._startup_timeout_s
        while time.monotonic() < deadline:
            if server.started:
                self.candidate_handle = self
                return self
            if self._failure is not None or not thread.is_alive():
                self.stop()
                raise ServiceCatalogRolloutError("Uvicorn candidate failed before startup") from self._failure
            time.sleep(0.025)
        self.stop()
        raise ServiceCatalogRolloutError("Uvicorn candidate startup timed out")

    def _take_or_bind_sockets(self) -> tuple[socket.socket, ...]:
        if self._use_reserved:
            transferred = self._reservation_probe.take_consumed_sockets(
                snapshot_digest=self._snapshot_digest,
                endpoint_ids=(self._endpoint.endpoint_id,),
                now_ms=int(time.time() * 1000),
            )
            if not transferred:
                raise ServiceCatalogRolloutError("exact socket reservation handoff failed")
            sockets = tuple(transferred.get(self._endpoint.endpoint_id) or ())
            if not sockets:
                raise ServiceCatalogRolloutError("Host socket reservation was empty")
            return sockets
        opened: list[socket.socket] = []
        try:
            socket_type = socket.SOCK_STREAM if self._endpoint.transport is Transport.TCP else socket.SOCK_DGRAM
            for family, address in strict_socket_addresses(self._endpoint.bind_host):
                owned = socket.socket(family, socket_type)
                exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
                if exclusive is not None:
                    owned.setsockopt(socket.SOL_SOCKET, exclusive, 1)
                bind_value: tuple[Any, ...] = (
                    (address, self._endpoint.port, 0, 0)
                    if family == socket.AF_INET6
                    else (address, self._endpoint.port)
                )
                owned.bind(bind_value)
                opened.append(owned)
            return tuple(opened)
        except BaseException:
            for owned in opened:
                owned.close()
            raise

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=15.0)
        for owned in self._sockets:
            try:
                owned.close()
            except OSError:
                pass
        self._sockets = ()

    def join(self) -> None:
        if self._thread is not None:
            self._thread.join()
        if self._failure is not None:
            raise ServiceCatalogRolloutError("Uvicorn candidate terminated with failure") from self._failure


def _host_catalog_target_mode(source: Mapping[str, str]) -> CatalogMode:
    raw = str(
        source.get(HOST_SERVICE_CATALOG_TARGET_MODE_ENV)
        or DEFAULT_HOST_SERVICE_CATALOG_TARGET_MODE.value
    ).strip().upper()
    try:
        return CatalogMode(raw)
    except ValueError as exc:
        raise ServiceCatalogRolloutError(
            "HOST_SERVICE_CATALOG_TARGET_MODE is invalid"
        ) from exc


def _configured_rollback_ref(source: Mapping[str, str]) -> str | None:
    raw = str(source.get(HOST_SERVICE_CATALOG_ROLLBACK_REF_ENV) or "").strip()
    if not raw:
        return None
    if len(raw) > 1024 or any(ord(character) < 32 for character in raw):
        raise ServiceCatalogRolloutError(
            "HOST_SERVICE_CATALOG_ROLLBACK_REF is invalid"
        )
    return raw


def _coordinator_lease_ms(source: Mapping[str, str]) -> int:
    raw = str(
        source.get(HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS_ENV)
        or DEFAULT_HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS
    ).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ServiceCatalogRolloutError(
            "HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS is invalid"
        ) from exc
    if value < 60_000 or value > 300_000:
        raise ServiceCatalogRolloutError(
            "HOST_SERVICE_CATALOG_COORDINATOR_LEASE_MS must be between 60000 and 300000"
        )
    return value


def _stage_operation_id(base_operation_id: str, stage: CatalogMode) -> str:
    operation_id = f"{base_operation_id}:{stage.value.lower()}"
    if len(operation_id) > 512:
        raise ServiceCatalogRolloutError("Service Catalog operation identity is too long")
    return operation_id


def _rollback_ref_for_head(
    configured: str | None,
    head: Mapping[str, Any] | None,
) -> str:
    if configured:
        return configured
    if head is None:
        return "service-catalog-rollout://host.main/bootstrap"
    return (
        "service-catalog-rollout://operation/"
        + str(head["activation_operation_id"])
    )


def _wait_until_bind_is_free(
    probe: OwnedSocketReservationProbe,
    endpoint: EndpointSpec,
    *,
    timeout_s: float = 10.0,
) -> bool:
    deadline = time.monotonic() + float(timeout_s)
    while time.monotonic() < deadline:
        if probe.inspect(endpoint).available is True:
            return True
        time.sleep(0.025)
    return probe.inspect(endpoint).available is True


@dataclass(slots=True)
class HostCatalogRuntime:
    outcome: CatalogActivationOutcomeV1
    stage_outcomes: tuple[CatalogActivationOutcomeV1, ...]
    target_stage: CatalogMode
    candidate: HostUvicornCandidate
    store: ServiceCatalogRolloutRepository
    authority: Any
    reservation_probe: OwnedSocketReservationProbe

    def close(self) -> None:
        # Shutdown is best-effort across three independent resources.  A
        # candidate adapter failure must not strand the durable coordinator
        # lease or the owned reservation sockets.  Nested ``finally`` blocks
        # preserve the first exception while still attempting both releases.
        try:
            self.candidate.stop()
        finally:
            try:
                self.store.release_authority(self.authority)
            finally:
                self.reservation_probe.close()


def start_host_through_service_catalog(
    *,
    db_path: str,
    host: str,
    port: int,
    log_level: str,
    access_log: bool,
    env: Mapping[str, str] | None = None,
    operation_id: str | None = None,
    app: Any = "host_brain.app.asgi:app",
) -> HostCatalogRuntime:
    source = dict(os.environ if env is None else env)
    resolved_operation_id = str(
        operation_id or "catalog-rollout-" + uuid4().hex
    ).strip()
    if not resolved_operation_id:
        raise ServiceCatalogRolloutError("Service Catalog operation identity is empty")
    target_stage = _host_catalog_target_mode(source)
    configured_rollback_ref = _configured_rollback_ref(source)
    coordinator_lease_ms = _coordinator_lease_ms(source)
    source[HOST_SERVICE_CATALOG_TARGET_MODE_ENV] = target_stage.value
    if env is None:
        os.environ[HOST_SERVICE_CATALOG_TARGET_MODE_ENV] = target_stage.value
    source["HOST_APP_HOST"] = str(host)
    source["HOST_APP_PORT"] = str(int(port))
    snapshot = build_local_catalog(source)
    endpoint = snapshot.by_id()[HOST_MAIN_ENDPOINT_ID]
    store = SQLiteServiceCatalogRolloutRepository(db_path)
    runtime_scope = EmbodimentOutboxRepository(db_path).runtime_scope_id
    authority = store.acquire_authority(
        runtime_scope_id=runtime_scope,
        owner_id=f"host.main.catalog::{os.getpid()}::{uuid4().hex}",
        lease_ms=coordinator_lease_ms,
    )
    if authority is None:
        raise ServiceCatalogRolloutError("another Service Catalog coordinator owns this runtime")
    active_candidate: HostUvicornCandidate | None = None
    active_reservation: OwnedSocketReservationProbe | None = None
    try:
        head = store.rollout_head(
            runtime_scope_id=runtime_scope,
            candidate_owner_id=HOST_MAIN_OWNER_ID,
        )
        target_rank = _ROLLOUT_STAGE_RANK[target_stage]
        if head is None:
            first_rank = 0
        else:
            head_rank = int(head["stage_rank"])
            if target_rank < head_rank:
                raise ServiceCatalogRolloutError(
                    "configured Service Catalog target regresses the durable head"
                )
            # A completed target is reactivated on process restart, including
            # when a legitimate runtime configuration change produces a new
            # catalog digest. The prior operation remains the rollback ref;
            # freezing one SQLite runtime to its first-ever endpoint snapshot
            # would make normal port and sidecar changes impossible.
            first_rank = target_rank if target_rank == head_rank else head_rank + 1

        outcomes: list[CatalogActivationOutcomeV1] = []
        for stage in _ROLLOUT_STAGES[first_rank : target_rank + 1]:
            renewed = store.renew_authority(
                authority,
                lease_ms=coordinator_lease_ms,
            )
            if renewed is None:
                raise ServiceCatalogRolloutError(
                    "Service Catalog coordinator authority was lost during rollout"
                )
            authority = renewed
            head_before = store.rollout_head(
                runtime_scope_id=runtime_scope,
                candidate_owner_id=HOST_MAIN_OWNER_ID,
            )
            stage_source = dict(source)
            stage_source["HOST_SERVICE_CATALOG_MODE"] = stage.value
            stage_operation_id = _stage_operation_id(resolved_operation_id, stage)
            existing = store.get_operation(stage_operation_id)
            if existing is not None and store.operation_state(stage_operation_id) in {
                "FAILED",
                "ROLLED_BACK",
            }:
                stage_operation_id = _stage_operation_id(
                    resolved_operation_id + ":retry-" + uuid4().hex,
                    stage,
                )
            stage_source["HOST_SERVICE_CATALOG_OPERATION_ID"] = stage_operation_id
            if env is None:
                # Each ASGI lifespan observes the immutable operation for its
                # own stage; the process-global value is only an input to that
                # app-local composition and is replaced before the next start.
                os.environ["HOST_SERVICE_CATALOG_MODE"] = stage.value
                os.environ["HOST_SERVICE_CATALOG_OPERATION_ID"] = stage_operation_id

            reservation_probe = OwnedSocketReservationProbe()
            active_reservation = reservation_probe
            health_probe = IdentityCapturingHealthProbe(HostHttpHealthProbe())
            coordinator = ServiceCatalogStartupCoordinator(
                snapshot=snapshot,
                env=stage_source,
                bind_probe=reservation_probe,
                health_probe=health_probe,
            )
            candidate = HostUvicornCandidate(
                endpoint=endpoint,
                reservation_probe=reservation_probe,
                snapshot_digest=snapshot.digest,
                operation_id=stage_operation_id,
                stage=stage,
                use_reserved_sockets=coordinator.mode in {MODE_CANARY, MODE_ENFORCE},
                log_level=log_level,
                access_log=access_log,
                app=app,
            )
            active_candidate = candidate
            runner = DurableServiceCatalogActivationRunner(
                snapshot=snapshot,
                coordinator=coordinator,
                store=store,
                authority=authority,
                health_probe=health_probe,
                candidate_owner_id=HOST_MAIN_OWNER_ID,
                start_candidate=candidate,
                stop_candidate=lambda handle: handle.stop(),
                bind_probe=reservation_probe,
                rollback_ref=_rollback_ref_for_head(
                    configured_rollback_ref,
                    head_before,
                ),
            )
            outcome = runner.activate(operation_id=stage_operation_id)
            if outcome.status == "ROLLED_BACK" and outcome.recovered:
                # A crash before the terminal receipt with a now-free listener
                # proves that the old effect is absent.  The rollback stays in
                # the ledger and a new operation identity performs the retry.
                stage_operation_id = _stage_operation_id(
                    resolved_operation_id + ":recovery-" + uuid4().hex,
                    stage,
                )
                if env is None:
                    os.environ["HOST_SERVICE_CATALOG_OPERATION_ID"] = stage_operation_id
                outcome = runner.activate(operation_id=stage_operation_id)
            if not outcome.started:
                raise ServiceCatalogRolloutError(
                    f"Service Catalog {stage.value} activation failed: "
                    f"{outcome.reason_code}"
                )
            if outcome.candidate_handle is not candidate:
                raise ServiceCatalogRolloutError(
                    "healthy candidate reconciliation has no controllable Host handle"
                )
            verified_head = store.rollout_head(
                runtime_scope_id=runtime_scope,
                candidate_owner_id=HOST_MAIN_OWNER_ID,
            )
            if (
                verified_head is None
                or str(verified_head["stage"]) != stage.value
                or str(verified_head["activation_operation_id"]) != outcome.operation_id
            ):
                raise ServiceCatalogRolloutError(
                    "Service Catalog durable head did not advance exactly"
                )
            outcomes.append(outcome)

            if stage is not target_stage:
                candidate.stop()
                active_candidate = None
                if not _wait_until_bind_is_free(reservation_probe, endpoint):
                    raise ServiceCatalogRolloutError(
                        "intermediate Service Catalog candidate did not release its listener"
                    )
                reservation_probe.close()
                active_reservation = None

        if active_candidate is None or active_reservation is None or not outcomes:
            raise ServiceCatalogRolloutError("Service Catalog produced no final Host candidate")
        return HostCatalogRuntime(
            outcome=outcomes[-1],
            stage_outcomes=tuple(outcomes),
            target_stage=target_stage,
            candidate=active_candidate,
            store=store,
            authority=authority,
            reservation_probe=active_reservation,
        )
    except BaseException:
        if active_candidate is not None:
            active_candidate.stop()
        store.release_authority(authority)
        if active_reservation is not None:
            active_reservation.close()
        raise


__all__ = [
    "HOST_MAIN_ENDPOINT_ID",
    "HOST_MAIN_OWNER_ID",
    "HOST_MAIN_PROTOCOL_ID",
    "HOST_MAIN_PROTOCOL_VERSION",
    "HOST_CATALOG_OPERATION_DIGEST_HEADER",
    "HOST_CATALOG_SNAPSHOT_DIGEST_HEADER",
    "HOST_CATALOG_CANDIDATE_OWNER_HEADER",
    "HOST_CATALOG_STAGE_HEADER",
    "HOST_SERVICE_CATALOG_ROLLBACK_REF_ENV",
    "HOST_SERVICE_CATALOG_TARGET_MODE_ENV",
    "DEFAULT_HOST_SERVICE_CATALOG_TARGET_MODE",
    "HostCatalogRuntime",
    "HostHttpHealthProbe",
    "HostUvicornCandidate",
    "host_candidate_loop_factory",
    "host_main_health_identity",
    "start_host_through_service_catalog",
]
