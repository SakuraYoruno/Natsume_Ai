"""Production runtime for durable, server-owned Agent workspaces.

Lease secrets exist only in this process.  WorkOrders carry the logical
workspace id while the durable database stores only a token digest and logical
configured-root/source references.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
from typing import Any

from host_brain.agent_runtime.workspace_allocator import (
    DurableWorkspaceLease,
    WorkspaceAllocator,
    WorkspaceWriteCandidate,
)
from host_brain.agent_runtime.workspace_policy import WorkspaceResolution
from host_brain.agent_runtime.workspace_runtime_contracts import (
    DurableWorkspaceRuntimeError,
    WORKSPACE_CLEANUP_UNAVAILABLE,
    WORKSPACE_HEARTBEAT_LOST,
    WORKSPACE_RUNTIME_BINDING_INVALID,
    WorkspaceArtifactAbandonmentReceipt,
    WorkspaceArtifactPromotionReceipt,
    WorkspaceCleanupReceipt,
    required_workspace_value,
)
from host_brain.contracts.agent_runtime.work_order import (
    AgentWorkOrder,
    WorkOrderV3,
    canonicalize_work_order,
)
from host_brain.contracts.agent_runtime.workspace_lifecycle import WorkspaceLifecycleState
from host_brain.persistence.workspace_repository import (
    WORKSPACE_AUTHORITY_CONFLICT,
    WORKSPACE_AUTHORITY_INVALID,
    WORKSPACE_AUTHORITY_NOT_FOUND,
    WORKSPACE_LEASE_CONFLICT,
    WORKSPACE_STALE_FENCE,
    WorkspaceAuthority,
    WorkspaceMutation,
    WorkspaceRepository,
    WorkspaceRepositoryError,
)

class _WorkspaceHeartbeatScope(AbstractContextManager[None]):
    def __init__(
        self,
        runtime: "DurableWorkspaceRuntime",
        resolution: WorkspaceResolution,
    ) -> None:
        self._runtime = runtime
        self._resolution = resolution
        self._stop = threading.Event()
        self._failures: list[BaseException] = []
        self._thread: threading.Thread | None = None

    def __enter__(self) -> None:
        if self._resolution.source != "durable_workspace":
            return None
        try:
            self._runtime._enter_execution_scope(self._resolution)
        except DurableWorkspaceRuntimeError:
            raise
        except BaseException as exc:  # noqa: BLE001 - normalize the runtime boundary
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_HEARTBEAT_LOST,
                f"durable workspace lease is not live before execution: {exc}",
            ) from exc
        try:
            self._thread = threading.Thread(
                target=self._run,
                name=f"workspace-heartbeat-{self._resolution.workspace_id}",
                daemon=True,
            )
            self._thread.start()
        except BaseException:
            self._runtime._leave_execution_scope(
                self._resolution,
                handoff_ready=False,
            )
            raise
        return None

    def cancelled(self) -> bool:
        """Return true as soon as heartbeat authority is lost or scope stops."""

        return self._stop.is_set()

    def assert_can_start_action(self, action: str) -> WorkspaceAuthority:
        """Fail before a new cooperative action starts after known lease loss."""

        label = str(action or "workspace action").strip() or "workspace action"
        if self._stop.is_set():
            failure = self._failures[0] if self._failures else None
            detail = f"durable workspace cannot start {label} after lease loss"
            if failure is not None:
                detail = f"{detail}: {failure}"
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_HEARTBEAT_LOST,
                detail,
            ) from failure
        try:
            authority = self._runtime.assert_live(self._resolution)
        except BaseException as exc:  # noqa: BLE001 - normalize the scope boundary
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_HEARTBEAT_LOST,
                f"durable workspace cannot start {label}: {exc}",
            ) from exc
        # The heartbeat thread can fail while the exact durable read is in
        # flight.  Recheck its cooperative signal before returning authority
        # to the next action; this does not claim to pre-empt an action later.
        if self._stop.is_set():
            failure = self._failures[0] if self._failures else None
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_HEARTBEAT_LOST,
                f"durable workspace cannot start {label} after lease loss",
            ) from failure
        return authority

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self._stop.set()
        handoff_ready = False
        try:
            if self._thread is not None:
                self._thread.join(timeout=self._runtime.heartbeat_stop_timeout_s)
                if self._thread.is_alive():
                    raise DurableWorkspaceRuntimeError(
                        WORKSPACE_HEARTBEAT_LOST,
                        "durable workspace heartbeat did not stop; lease state is indeterminate",
                    )
            if self._failures:
                failure = self._failures[0]
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_HEARTBEAT_LOST,
                    f"durable workspace heartbeat failed: {failure}",
                ) from failure
            try:
                self._runtime.assert_live(self._resolution)
            except BaseException as final_exc:  # noqa: BLE001 - normalize the runtime boundary
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_HEARTBEAT_LOST,
                    f"durable workspace lease is not live after execution: {final_exc}",
                ) from final_exc
            # No heartbeat or worker is active now.  Only at this point may a
            # later node/attempt atomically replace the exact durable lease.
            handoff_ready = True
        finally:
            self._runtime._leave_execution_scope(
                self._resolution,
                handoff_ready=handoff_ready,
            )
        return False

    def _run(self) -> None:
        while not self._stop.wait(self._runtime.heartbeat_interval_s):
            try:
                self._runtime.heartbeat_once(
                    self._resolution,
                    cancelled=self._stop.is_set,
                )
            except BaseException as exc:  # noqa: BLE001 - transferred to caller thread
                if (
                    self._stop.is_set()
                    and isinstance(exc, WorkspaceRepositoryError)
                    and exc.reason_code == WORKSPACE_STALE_FENCE
                    and exc.detail
                    == "workspace heartbeat was cancelled before its durable write"
                ):
                    # Scope shutdown deliberately cancelled a heartbeat before
                    # it wrote.  That is a clean stop, not durable lease loss.
                    return
                self._failures.append(exc)
                self._stop.set()
                return


class DurableWorkspaceRuntime:
    """Allocate, lease, heartbeat and clean configured git worktrees."""

    _required = staticmethod(required_workspace_value)

    def __init__(
        self,
        *,
        repository: WorkspaceRepository,
        allocator: WorkspaceAllocator,
        runtime_owner_id: str,
        managed_root_ref: str,
        git_source_key: str,
        lease_ms: int = 120_000,
        heartbeat_interval_ms: int | None = None,
        token_secret: bytes | None = None,
    ) -> None:
        if not isinstance(repository, WorkspaceRepository):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "repository must be a WorkspaceRepository",
            )
        if not isinstance(allocator, WorkspaceAllocator) or allocator.repository is not repository:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "allocator must use the injected WorkspaceRepository",
            )
        self.repository = repository
        self.allocator = allocator
        self.runtime_owner_id = self._required(runtime_owner_id, "runtime_owner_id", 180)
        self.managed_root_ref = self._required(managed_root_ref, "managed_root_ref", 120)
        self.git_source_key = self._required(git_source_key, "git_source_key", 120)
        if isinstance(lease_ms, bool) or int(lease_ms) < 1_000 or int(lease_ms) > 86_400_000:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "lease_ms must be between 1000 and 86400000",
            )
        self.lease_ms = int(lease_ms)
        configured_interval = (
            int(heartbeat_interval_ms)
            if heartbeat_interval_ms is not None
            else max(250, self.lease_ms // 3)
        )
        if configured_interval < 100 or configured_interval >= self.lease_ms:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "heartbeat interval must be at least 100ms and shorter than the lease",
            )
        self.heartbeat_interval_s = configured_interval / 1000.0
        self.heartbeat_stop_timeout_s = max(
            1.0,
            self.heartbeat_interval_s * 2,
            repository.operation_timeout_s + 1.0,
        )
        # Cleanup is the longest operation owned by the continuation worker.
        # Outside one bounded allocator removal it can consume, in the worst
        # stale-cached ACTIVE case: inventory(1), initial read(1), cached lease
        # validation + takeover(3), begin-cleanup CAS(2), final read(1), and
        # receipt CAS(2) repository timeout windows.  The worker passes a stop
        # probe between rows, so shutdown waits for at most this one-row bound.
        self.operation_timeout_s = (
            allocator.operation_timeout_s
            + (10 * repository.operation_timeout_s)
            + 1.0
        )
        secret = token_secret if token_secret is not None else secrets.token_bytes(48)
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "workspace token secret must contain at least 32 bytes",
            )
        self._token_secret = bytes(secret)
        self._runtime_pid = os.getpid()
        self._runtime_instance_id = secrets.token_hex(24)
        self._leases: dict[str, DurableWorkspaceLease] = {}
        self._active_scopes: dict[tuple[str, str, int], int] = {}
        self._handoff_ready: dict[str, DurableWorkspaceLease] = {}
        self._cleanup_inflight: set[str] = set()
        self._lock = threading.RLock()

    @staticmethod
    def workspace_id_for(*, graph_id: str, graph_revision: int) -> str:
        graph = str(graph_id or "").strip()
        if not graph or isinstance(graph_revision, bool) or int(graph_revision) < 1:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "workspace id requires a canonical graph epoch",
            )
        digest = hashlib.sha256(f"{graph}\n{int(graph_revision)}".encode("utf-8")).hexdigest()[:48]
        return f"workspace-{digest}"

    def lease_for_order(
        self,
        order: AgentWorkOrder | WorkOrderV3,
        workspace_id: str,
        *,
        execution_authority: dict[str, Any] | None = None,
    ) -> DurableWorkspaceLease:
        """Server-side WorkspacePolicy provider; never mutates the WorkOrder."""

        self._ensure_process_instance()
        order = canonicalize_work_order(order)
        binding = self._order_binding(
            order,
            workspace_id=workspace_id,
            execution_authority=execution_authority,
        )
        # The first allocation freezes HEAD.  Every replay must reuse that
        # durable source_ref; sampling the moving source repository again
        # would turn an exact graph replay into a false authority conflict.
        try:
            existing = self.repository.get(binding["workspace_id"])
        except WorkspaceRepositoryError as exc:
            if exc.reason_code != WORKSPACE_AUTHORITY_NOT_FOUND:
                raise
            source_ref = self.allocator.snapshot_git_source(self.git_source_key)
        else:
            source_ref = existing.source_ref
        mutation = self._allocate_graph_workspace(
            binding=binding,
            source_ref=source_ref,
        )
        authority = mutation.authority
        lease_owner = binding["lease_owner"]
        token = self._lease_token(
            workspace_id=workspace_id,
            lease_owner=lease_owner,
            graph_id=binding["graph_id"],
            graph_revision=binding["graph_revision"],
        )
        # Serialize scope admission with lease replay/handoff.  Without this
        # local critical section an exact replay could enter its worker scope
        # after another thread observed the idle marker but before that thread
        # completed the durable handoff.
        with self._lock:
            # Allocation and source replay happen before this process-local
            # critical section.  Refresh the durable status here so cleanup
            # cannot transition ACTIVE -> CLEANUP_PENDING between the earlier
            # read and execution lease admission.
            authority = self.repository.get(workspace_id)
            if authority.status == "CLEANUP_PENDING":
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_CLEANUP_UNAVAILABLE,
                    "workspace cleanup has started and execution cannot resume",
                )
            if authority.status in {"CLEANED", "QUARANTINED"}:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_CLEANUP_UNAVAILABLE,
                    f"workspace is terminal: {authority.status}",
                )
            handoff = self._eligible_handoff(authority)
            if handoff is not None and (
                handoff.lease_owner != lease_owner or handoff.lease_token != token
            ):
                try:
                    acquired = self.repository.handoff_lease(
                        workspace_id,
                        lease_owner=handoff.lease_owner,
                        lease_token=handoff.lease_token,
                        lease_fencing_token=handoff.lease_fencing_token,
                        next_lease_owner=lease_owner,
                        next_lease_token=token,
                        lease_ms=self.lease_ms,
                        expected_status_version=authority.status_version,
                    ).authority
                except WorkspaceRepositoryError as exc:
                    if not self._expired_handoff_can_take_over(
                        exc,
                        authority=authority,
                        handoff=handoff,
                    ):
                        raise
                    # The exact idle lease expired after its local scope had
                    # quiesced.  Refreshing and preserving every durable field
                    # except wall-clock liveness lets acquire_lease perform the
                    # authoritative expiry check and monotonic takeover CAS.
                    refreshed = self.repository.get(workspace_id)
                    if not self._same_expired_handoff_epoch(
                        before=authority,
                        refreshed=refreshed,
                        handoff=handoff,
                    ):
                        raise
                    acquired = self.repository.acquire_lease(
                        workspace_id,
                        lease_owner=lease_owner,
                        lease_token=token,
                        lease_ms=self.lease_ms,
                        expected_status_version=refreshed.status_version,
                    ).authority
            else:
                acquired = self.repository.acquire_lease(
                    workspace_id,
                    lease_owner=lease_owner,
                    lease_token=token,
                    lease_ms=self.lease_ms,
                    expected_status_version=authority.status_version,
                ).authority
            lease = DurableWorkspaceLease(
                workspace_id=workspace_id,
                lease_owner=lease_owner,
                lease_token=token,
                lease_fencing_token=acquired.lease_fencing_token,
            )
            self._leases[workspace_id] = lease
            self._handoff_ready.pop(workspace_id, None)
        return lease

    def _allocate_graph_workspace(
        self,
        *,
        binding: dict[str, Any],
        source_ref: str,
    ) -> WorkspaceMutation:
        values = self._workspace_allocation_values(
            binding=binding,
            source_ref=source_ref,
        )
        try:
            return self.allocator.allocate(**values)
        except WorkspaceRepositoryError as conflict:
            if conflict.reason_code != WORKSPACE_AUTHORITY_CONFLICT:
                raise

            # A concurrent first admission may have frozen a different HEAD
            # after this caller observed a get-miss.  Only that server-derived
            # source race is recoverable: every other immutable binding must
            # already equal the exact graph epoch requested by this caller.
            try:
                durable = self.repository.get(binding["workspace_id"])
            except WorkspaceRepositoryError:
                raise conflict
            expected_workspace_id = self.workspace_id_for(
                graph_id=binding["graph_id"],
                graph_revision=binding["graph_revision"],
            )
            immutable_matches = (
                binding["workspace_id"] == expected_workspace_id
                and durable.workspace_id == expected_workspace_id
                and durable.root_work_order_id == binding["root_work_order_id"]
                and durable.graph_id == binding["graph_id"]
                and durable.graph_revision == binding["graph_revision"]
                and durable.owner_principal_id == binding["principal_id"]
                and durable.managed_root_ref == self.managed_root_ref
                and durable.workspace_relpath == expected_workspace_id
                and durable.isolation_mode == "DIRECTORY_FENCE"
                and durable.allowed_paths == (".",)
            )
            if not immutable_matches:
                raise conflict
            durable_source_ref = self.allocator.validate_git_source_ref(
                self.git_source_key,
                durable.source_ref,
            )
            return self.allocator.allocate(
                **self._workspace_allocation_values(
                    binding=binding,
                    source_ref=durable_source_ref,
                )
            )

    def _workspace_allocation_values(
        self,
        *,
        binding: dict[str, Any],
        source_ref: str,
    ) -> dict[str, Any]:
        return {
            "workspace_id": binding["workspace_id"],
            "root_work_order_id": binding["root_work_order_id"],
            "graph_id": binding["graph_id"],
            "graph_revision": binding["graph_revision"],
            "owner_principal_id": binding["principal_id"],
            "managed_root_ref": self.managed_root_ref,
            "source_ref": source_ref,
            "isolation_mode": "DIRECTORY_FENCE",
            "allowed_paths": (".",),
        }

    def _enter_execution_scope(self, resolution: WorkspaceResolution) -> WorkspaceAuthority:
        with self._lock:
            self._refresh_process_instance_locked()
            lease = self._lease_for_resolution(resolution)
            key = self._lease_key(lease)
            if self._workspace_active_scope_count(lease.workspace_id) > 0:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_LEASE_CONFLICT,
                    "workspace execution scope is already active in this process",
                )
            authority = self.assert_live(resolution)
            self._handoff_ready.pop(lease.workspace_id, None)
            self._active_scopes[key] = 1
        return authority

    def _leave_execution_scope(
        self,
        resolution: WorkspaceResolution,
        *,
        handoff_ready: bool,
    ) -> None:
        try:
            lease = self._lease_for_resolution(resolution)
        except DurableWorkspaceRuntimeError:
            return
        key = self._lease_key(lease)
        with self._lock:
            count = self._active_scopes.get(key, 0)
            if count <= 1:
                self._active_scopes.pop(key, None)
                if handoff_ready:
                    self._handoff_ready[lease.workspace_id] = lease
            else:
                self._active_scopes[key] = count - 1

    def _eligible_handoff(
        self,
        authority: WorkspaceAuthority,
    ) -> DurableWorkspaceLease | None:
        with self._lock:
            lease = self._handoff_ready.get(authority.workspace_id)
            if lease is None:
                return None
            if self._active_scopes.get(self._lease_key(lease), 0) != 0:
                return None
        if (
            authority.status != "ACTIVE"
            or authority.lease_owner != lease.lease_owner
            or authority.lease_fencing_token != lease.lease_fencing_token
        ):
            with self._lock:
                self._handoff_ready.pop(authority.workspace_id, None)
            return None
        return lease

    @staticmethod
    def _expired_handoff_can_take_over(
        exc: WorkspaceRepositoryError,
        *,
        authority: WorkspaceAuthority,
        handoff: DurableWorkspaceLease,
    ) -> bool:
        """Whitelist only an unchanged idle lease's expiry-shaped failure."""

        return (
            exc.reason_code == WORKSPACE_STALE_FENCE
            and exc.detail
            == "workspace lease is absent, expired, or fenced by another owner"
            and authority.status == "ACTIVE"
            and authority.lease_owner == handoff.lease_owner
            and authority.lease_fencing_token == handoff.lease_fencing_token
        )

    @staticmethod
    def _same_expired_handoff_epoch(
        *,
        before: WorkspaceAuthority,
        refreshed: WorkspaceAuthority,
        handoff: DurableWorkspaceLease,
    ) -> bool:
        """Reject every durable drift before delegating expiry to acquire CAS."""

        return (
            refreshed == before
            and refreshed.status == "ACTIVE"
            and refreshed.lease_owner == handoff.lease_owner
            and refreshed.lease_fencing_token == handoff.lease_fencing_token
        )

    def _workspace_active_scope_count(self, workspace_id: str) -> int:
        """Return active local execution scopes while the runtime lock is held."""

        return sum(
            count
            for (candidate_workspace_id, _owner, _fence), count in self._active_scopes.items()
            if candidate_workspace_id == workspace_id
        )

    @staticmethod
    def _lease_key(lease: DurableWorkspaceLease) -> tuple[str, str, int]:
        return (
            lease.workspace_id,
            lease.lease_owner,
            lease.lease_fencing_token,
        )

    def heartbeat_scope(self, resolution: WorkspaceResolution) -> _WorkspaceHeartbeatScope:
        return _WorkspaceHeartbeatScope(self, resolution)

    def heartbeat_once(
        self,
        resolution: WorkspaceResolution,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> WorkspaceAuthority:
        with self._lock:
            lease = self._lease_for_resolution(resolution)
            return self.repository.heartbeat_lease(
                lease.workspace_id,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
                lease_ms=self.lease_ms,
                cancelled=cancelled,
            )

    def assert_live(self, resolution: WorkspaceResolution) -> WorkspaceAuthority:
        with self._lock:
            lease = self._lease_for_resolution(resolution)
            authority = self.repository.authorize_lease(
                lease.workspace_id,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
            )
            if authority.status != "ACTIVE":
                raise WorkspaceRepositoryError(
                    WORKSPACE_STALE_FENCE,
                    "workspace execution requires ACTIVE authority",
                )
            return authority

    def capture_write_candidate(
        self,
        resolution: WorkspaceResolution,
    ) -> WorkspaceWriteCandidate | None:
        """Capture one fence-bound write proposal before the worker exits."""

        with self._lock:
            lease = self._lease_for_resolution(resolution)
        try:
            return self.allocator.capture_write_candidate(lease)
        except WorkspaceRepositoryError as exc:
            raise DurableWorkspaceRuntimeError(
                exc.reason_code,
                exc.detail,
            ) from exc

    def promote_no_change_artifact(
        self,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any],
        verification_ref: str,
        acceptance_ref: str,
        effect_ref: str,
        artifact_promoter: Callable[[], str],
        allow_start: bool = True,
    ) -> WorkspaceArtifactPromotionReceipt:
        """Promote one sealed read-only result through the 0116 lifecycle."""

        return self._promote_artifact(
            workspace_id=workspace_id,
            execution_authority=execution_authority,
            verification_ref=verification_ref,
            acceptance_ref=acceptance_ref,
            effect_ref=effect_ref,
            artifact_promoter=artifact_promoter,
            allow_start=allow_start,
            write_manifest=None,
            write_patch=None,
        )

    def promote_write_artifact(
        self,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any],
        verification_ref: str,
        acceptance_ref: str,
        effect_ref: str,
        write_manifest: dict[str, Any],
        write_patch: bytes,
        artifact_promoter: Callable[[], str],
        allow_start: bool = True,
    ) -> WorkspaceArtifactPromotionReceipt:
        """Promote an exact sealed write candidate through accepted HEAD."""

        return self._promote_artifact(
            workspace_id=workspace_id,
            execution_authority=execution_authority,
            verification_ref=verification_ref,
            acceptance_ref=acceptance_ref,
            effect_ref=effect_ref,
            artifact_promoter=artifact_promoter,
            allow_start=allow_start,
            write_manifest=dict(write_manifest),
            write_patch=write_patch,
        )

    def _promote_artifact(
        self,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any],
        verification_ref: str,
        acceptance_ref: str,
        effect_ref: str,
        artifact_promoter: Callable[[], str],
        allow_start: bool,
        write_manifest: dict[str, Any] | None,
        write_patch: bytes | None,
    ) -> WorkspaceArtifactPromotionReceipt:
        if not callable(artifact_promoter):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "artifact_promoter must be callable",
            )
        if (write_manifest is None) != (write_patch is None):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "Workspace write manifest and patch must be supplied together",
            )
        if write_patch is not None and not isinstance(write_patch, bytes):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "Workspace write patch must be bytes",
            )
        authority = self._promotion_authority(
            workspace_id=workspace_id,
            value=execution_authority,
        )
        verification = self._durable_ref(verification_ref, "verification_ref")
        acceptance = self._durable_ref(acceptance_ref, "acceptance_ref")
        effect = self._durable_ref(effect_ref, "effect_ref")
        identity = {
            "workspace_id": workspace_id,
            "execution_authority": authority,
            "verification_ref": verification,
            "acceptance_ref": acceptance,
            "effect_ref": effect,
        }
        if write_manifest is not None:
            identity["write_manifest"] = write_manifest
        identity_digest = hashlib.sha256(
            json.dumps(
                identity,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        accepted_event_id = f"workspace-accepted-{identity_digest}"
        promotion_event_id = f"workspace-promoted-{identity_digest}"

        snapshot = self.repository.get_lifecycle(workspace_id)
        if snapshot.state is WorkspaceLifecycleState.ACTIVE and not allow_start:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "reconciliation cannot initiate an unstarted Workspace promotion",
            )
        replayed = snapshot.state is not WorkspaceLifecycleState.ACTIVE
        workspace_receipt_ref = ""
        if snapshot.state is WorkspaceLifecycleState.ACTIVE:
            lease = self._continuation_lease(
                workspace_id,
                purpose=f"accept:{identity_digest}",
            )
            accepted = (
                self.allocator.capture_no_change_head(lease)
                if write_manifest is None
                else self.allocator.accept_write_candidate(
                    lease,
                    manifest=write_manifest,
                    patch_bytes=write_patch or b"",
                )
            )
            workspace_receipt_ref = accepted.receipt_ref
            mutation = self.repository.accept_workspace(
                workspace_id,
                event_id=accepted_event_id,
                expected_transition_revision=0,
                node_id=authority["node_id"],
                attempt_id=authority["attempt_id"],
                task_lease_id=authority["lease_id"],
                task_lease_fencing_token=authority["fencing_token"],
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
                base_commit=accepted.base_commit,
                accepted_head=accepted.accepted_head,
                tree_digest=accepted.tree_digest,
                diff_digest=accepted.diff_digest,
                verification_ref=verification,
                acceptance_ref=acceptance,
            )
            if mutation.event.event_id != accepted_event_id:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_RUNTIME_BINDING_INVALID,
                    "workspace acceptance returned a different event",
                )
            snapshot = self.repository.get_lifecycle(workspace_id)

        accepted_event = snapshot.accepted_event
        if (
            accepted_event is None
            or accepted_event.event_id != accepted_event_id
            or accepted_event.graph_id != authority["graph_id"]
            or accepted_event.graph_revision != authority["graph_revision"]
            or accepted_event.node_id != authority["node_id"]
            or accepted_event.attempt_id != authority["attempt_id"]
            or accepted_event.task_lease_id != authority["lease_id"]
            or accepted_event.task_lease_fencing_token != authority["fencing_token"]
            or accepted_event.verification_ref != verification
            or accepted_event.acceptance_ref != acceptance
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "workspace accepted receipt differs from the exact effect authority",
            )
        if not workspace_receipt_ref:
            if write_manifest is None:
                workspace_receipt_ref = (
                    "workspace-no-change://sha256/"
                    + hashlib.sha256(
                        (
                            f"{workspace_id}\n{accepted_event.base_commit}\n"
                            f"{accepted_event.accepted_head}\n"
                            f"{accepted_event.tree_digest}\n"
                            f"{accepted_event.diff_digest}"
                        ).encode("utf-8")
                    ).hexdigest()
                )
            else:
                candidate_ref = str(
                    write_manifest.get("receipt_ref") or ""
                ).strip()
                workspace_receipt_ref = (
                    "workspace-write://sha256/"
                    + hashlib.sha256(
                        (
                            f"{workspace_id}\n{accepted_event.base_commit}\n"
                            f"{accepted_event.accepted_head}\n"
                            f"{accepted_event.tree_digest}\n"
                            f"{accepted_event.diff_digest}\n{candidate_ref}"
                        ).encode("utf-8")
                    ).hexdigest()
                )

        artifact_ref = self._durable_ref(artifact_promoter(), "artifact_ref")
        snapshot = self.repository.get_lifecycle(workspace_id)
        if snapshot.state is WorkspaceLifecycleState.ACCEPTED:
            lease = self._continuation_lease(
                workspace_id,
                purpose=f"promote:{identity_digest}",
            )
            mutation = self.repository.promote_workspace(
                workspace_id,
                event_id=promotion_event_id,
                expected_transition_revision=1,
                acceptance_event_id=accepted_event_id,
                promotion_ref=artifact_ref,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
            )
            if mutation.event.event_id != promotion_event_id:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_RUNTIME_BINDING_INVALID,
                    "workspace promotion returned a different event",
                )
            snapshot = self.repository.get_lifecycle(workspace_id)
        terminal = snapshot.latest_event
        if (
            snapshot.state is not WorkspaceLifecycleState.PROMOTED
            or terminal is None
            or terminal.event_id != promotion_event_id
            or terminal.acceptance_event_id != accepted_event_id
            or terminal.terminal_ref != artifact_ref
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "workspace promotion is absent or belongs to another artifact",
            )
        return WorkspaceArtifactPromotionReceipt(
            workspace_id=workspace_id,
            accepted_event_id=accepted_event_id,
            promotion_event_id=promotion_event_id,
            artifact_ref=artifact_ref,
            no_change_receipt_ref=workspace_receipt_ref,
            replayed=replayed,
        )

    def abandon_terminal_artifact(
        self,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any],
        stage_ref: str,
        seal_digest: str,
        reason_code: str,
        artifact_planner: Callable[[], str],
        artifact_abandoner: Callable[[], str],
    ) -> WorkspaceArtifactAbandonmentReceipt:
        """Persist ACTIVE→ABANDONED before deleting one sealed stage.

        Both callbacks must be exact-replay operations owned by the artifact
        store.  The planner is read-only, so the 0116 lifecycle CAS always wins
        before a tombstone is written or stage bytes are removed.
        """

        if not callable(artifact_planner) or not callable(artifact_abandoner):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "artifact abandonment requires planner and abandoner callbacks",
            )
        authority = self._promotion_authority(
            workspace_id=workspace_id,
            value=execution_authority,
        )
        stage = self._durable_ref(stage_ref, "stage_ref")
        seal = str(seal_digest or "").strip().lower()
        if (
            len(seal) != 71
            or not seal.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in seal[7:])
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "seal_digest is not canonical",
            )
        reason = self._required(reason_code, "reason_code", 128).upper()
        if (
            reason != str(reason_code).strip()
            or not reason[0].isalpha()
            or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for character in reason)
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "reason_code is not canonical",
            )
        tombstone_ref = self._durable_ref(
            artifact_planner(),
            "tombstone_ref",
        )
        identity_digest = hashlib.sha256(
            json.dumps(
                {
                    "workspace_id": workspace_id,
                    "execution_authority": authority,
                    "stage_ref": stage,
                    "seal_digest": seal,
                    "tombstone_ref": tombstone_ref,
                    "reason_code": reason,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        event_id = f"workspace-abandoned-{identity_digest}"
        snapshot = self.repository.get_lifecycle(workspace_id)
        replayed = snapshot.state is WorkspaceLifecycleState.ABANDONED
        if snapshot.state is WorkspaceLifecycleState.ACTIVE:
            lease = self._continuation_lease(
                workspace_id,
                purpose=f"abandon:{identity_digest}",
            )
            mutation = self.repository.abandon_workspace(
                workspace_id,
                event_id=event_id,
                expected_transition_revision=0,
                node_id=authority["node_id"],
                attempt_id=authority["attempt_id"],
                task_lease_id=authority["lease_id"],
                task_lease_fencing_token=authority["fencing_token"],
                abandonment_ref=tombstone_ref,
                reason_code=reason,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
            )
            if mutation.event.event_id != event_id:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_RUNTIME_BINDING_INVALID,
                    "workspace abandonment returned a different event",
                )
            snapshot = self.repository.get_lifecycle(workspace_id)
        terminal = snapshot.latest_event
        if (
            snapshot.state is not WorkspaceLifecycleState.ABANDONED
            or snapshot.revision != 1
            or terminal is None
            or terminal.event_id != event_id
            or terminal.from_state is not WorkspaceLifecycleState.ACTIVE
            or terminal.graph_id != authority["graph_id"]
            or terminal.graph_revision != authority["graph_revision"]
            or terminal.node_id != authority["node_id"]
            or terminal.attempt_id != authority["attempt_id"]
            or terminal.task_lease_id != authority["lease_id"]
            or terminal.task_lease_fencing_token != authority["fencing_token"]
            or terminal.terminal_ref != tombstone_ref
            or terminal.reason_code != reason
            or terminal.acceptance_event_id is not None
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "workspace abandonment differs from the sealed terminal artifact",
            )
        actual_ref = self._durable_ref(
            artifact_abandoner(),
            "tombstone_ref",
        )
        if actual_ref != tombstone_ref:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "artifact tombstone differs from the durable lifecycle event",
            )
        return WorkspaceArtifactAbandonmentReceipt(
            workspace_id=workspace_id,
            abandonment_event_id=event_id,
            tombstone_ref=tombstone_ref,
            reason_code=reason,
            replayed=replayed,
        )

    def cleanup_terminal_workspace(
        self,
        *,
        workspace_id: str,
        expected_state: WorkspaceLifecycleState,
        expected_terminal_ref: str,
    ) -> WorkspaceCleanupReceipt:
        """Start 0101 cleanup only after reopening exact 0116 terminal truth."""

        state = WorkspaceLifecycleState(str(expected_state))
        if state not in {
            WorkspaceLifecycleState.PROMOTED,
            WorkspaceLifecycleState.ABANDONED,
        }:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_CLEANUP_UNAVAILABLE,
                "workspace cleanup requires a terminal lifecycle state",
            )
        terminal_ref = self._durable_ref(
            expected_terminal_ref,
            "expected_terminal_ref",
        )
        snapshot = self.repository.get_lifecycle(workspace_id)
        if (
            snapshot.state is not state
            or snapshot.latest_event is None
            or snapshot.latest_event.terminal_ref != terminal_ref
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_CLEANUP_UNAVAILABLE,
                "workspace cleanup lifecycle identity is missing or drifted",
            )
        return self.cleanup(workspace_id)

    def _continuation_lease(
        self,
        workspace_id: str,
        *,
        purpose: str,
    ) -> DurableWorkspaceLease:
        self._ensure_process_instance()
        reference = self._required(workspace_id, "workspace_id", 120)
        with self._lock:
            cached = self._leases.get(reference)
        if cached is not None:
            try:
                self.repository.authorize_lease(
                    reference,
                    lease_owner=cached.lease_owner,
                    lease_token=cached.lease_token,
                    lease_fencing_token=cached.lease_fencing_token,
                )
                return cached
            except WorkspaceRepositoryError as exc:
                if exc.reason_code != WORKSPACE_STALE_FENCE:
                    raise
        durable = self.repository.get(reference)
        owner_digest = hashlib.sha256(
            f"{purpose}\n{self._runtime_instance_id}".encode("utf-8")
        ).hexdigest()[:40]
        instance_digest = self._runtime_instance_digest()
        owner = (
            f"{self.runtime_owner_id}::workspace-effect::{instance_digest}::"
            f"{owner_digest}"
        )
        if len(owner) > 240:
            owner = f"runtime-effect::{instance_digest}::{owner_digest}"
        token = self._lease_token(
            workspace_id=reference,
            lease_owner=owner,
            graph_id=durable.graph_id,
            graph_revision=durable.graph_revision,
        )
        acquired = self.repository.acquire_lease(
            reference,
            lease_owner=owner,
            lease_token=token,
            lease_ms=self.lease_ms,
            expected_status_version=durable.status_version,
        ).authority
        lease = DurableWorkspaceLease(
            workspace_id=reference,
            lease_owner=owner,
            lease_token=token,
            lease_fencing_token=acquired.lease_fencing_token,
        )
        with self._lock:
            self._leases[reference] = lease
        return lease

    def _promotion_authority(
        self,
        *,
        workspace_id: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        authority = dict(value or {})
        required = {
            "root_work_order_id",
            "graph_id",
            "graph_revision",
            "node_id",
            "attempt_id",
            "lease_id",
            "command_id",
            "principal_id",
            "lease_owner_id",
            "fencing_token",
        }
        if set(authority) != required:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "artifact promotion authority fields drifted",
            )
        for field in required - {"graph_revision", "fencing_token"}:
            authority[field] = self._required(authority[field], field, 240)
        for field in ("graph_revision", "fencing_token"):
            raw = authority[field]
            if isinstance(raw, bool) or type(raw) is not int or raw < 1:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_RUNTIME_BINDING_INVALID,
                    f"artifact promotion {field} is invalid",
                )
        expected_workspace = self.workspace_id_for(
            graph_id=authority["graph_id"],
            graph_revision=authority["graph_revision"],
        )
        if workspace_id != expected_workspace:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "artifact promotion workspace differs from graph epoch",
            )
        return authority

    def _durable_ref(self, value: Any, field: str) -> str:
        reference = self._required(value, field, 512)
        if "://" not in reference or any(ch.isspace() for ch in reference):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                f"{field} is not a durable reference",
            )
        return reference

    def cleanup(self, workspace_id: str) -> WorkspaceCleanupReceipt:
        """Execute or continue an explicitly durable cleanup transition."""

        self._ensure_process_instance()
        with self._lock:
            authority = self.repository.get(workspace_id)
            if authority.status == "CLEANED":
                return WorkspaceCleanupReceipt(
                    workspace_id=authority.workspace_id,
                    receipt_ref=str(authority.cleanup_receipt_ref),
                    digest=str(authority.cleanup_digest),
                    replayed=True,
                )
            if authority.status not in {"ACTIVE", "CLEANUP_PENDING"}:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_CLEANUP_UNAVAILABLE,
                    f"workspace cannot enter cleanup from {authority.status}",
                )
            if self._workspace_active_scope_count(authority.workspace_id) > 0:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_CLEANUP_UNAVAILABLE,
                    "workspace cleanup is forbidden while execution scopes are active",
                )
            if authority.workspace_id in self._cleanup_inflight:
                raise DurableWorkspaceRuntimeError(
                    WORKSPACE_CLEANUP_UNAVAILABLE,
                    "workspace cleanup is already in progress",
                )
            self._cleanup_inflight.add(authority.workspace_id)
            try:
                lease = self._cleanup_lease(authority)
                if authority.status == "ACTIVE":
                    authority = self.repository.begin_cleanup(
                        workspace_id,
                        lease_owner=lease.lease_owner,
                        lease_token=lease.lease_token,
                        lease_fencing_token=lease.lease_fencing_token,
                        expected_status_version=authority.status_version,
                    )
                self._handoff_ready.pop(authority.workspace_id, None)
            except BaseException:
                self._cleanup_inflight.discard(authority.workspace_id)
                raise

        try:
            cleanup = self.allocator.remove_git_worktree(lease)
            evidence = {
                "schema": "Natsume.agent.workspace_cleanup_receipt.v1",
                "workspace_id": authority.workspace_id,
                "root_work_order_id": authority.root_work_order_id,
                "graph_id": authority.graph_id,
                "graph_revision": authority.graph_revision,
                "owner_principal_id": authority.owner_principal_id,
                "managed_root_ref": authority.managed_root_ref,
                "source_ref": cleanup.source_ref,
                "lease_fencing_token": lease.lease_fencing_token,
                "adapter": "git_worktree_remove",
                "already_absent": cleanup.already_absent,
            }
            encoded = json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            receipt_ref = (
                f"workspace-cleanup://{workspace_id}/{digest.removeprefix('sha256:')}"
            )
            latest = self.repository.get(workspace_id)
            cleaned = self.repository.record_cleanup(
                workspace_id,
                lease_owner=lease.lease_owner,
                lease_token=lease.lease_token,
                lease_fencing_token=lease.lease_fencing_token,
                expected_status_version=latest.status_version,
                cleanup_receipt_ref=receipt_ref,
                cleanup_digest=digest,
            )
            with self._lock:
                self._leases.pop(workspace_id, None)
            return WorkspaceCleanupReceipt(
                workspace_id=workspace_id,
                receipt_ref=str(cleaned.cleanup_receipt_ref),
                digest=str(cleaned.cleanup_digest),
                replayed=False,
            )
        finally:
            with self._lock:
                self._cleanup_inflight.discard(authority.workspace_id)

    def continue_pending_cleanups(
        self,
        *,
        limit: int = 100,
        cancelled: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if cancelled is not None and not callable(cancelled):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "cleanup continuation cancellation probe must be callable",
            )
        results: list[dict[str, Any]] = []
        for authority in self.repository.list_cleanup_pending(limit=limit):
            if cancelled is not None and cancelled():
                break
            try:
                receipt = self.cleanup(authority.workspace_id)
                results.append(
                    {
                        "workspace_id": authority.workspace_id,
                        "status": "CLEANED",
                        "receipt_ref": receipt.receipt_ref,
                        "digest": receipt.digest,
                    }
                )
            except (WorkspaceRepositoryError, DurableWorkspaceRuntimeError) as exc:
                results.append(
                    {
                        "workspace_id": authority.workspace_id,
                        "status": "DEFERRED",
                        "reason_code": str(getattr(exc, "reason_code", WORKSPACE_CLEANUP_UNAVAILABLE)),
                    }
                )
        return tuple(results)

    def _cleanup_lease(self, authority: WorkspaceAuthority) -> DurableWorkspaceLease:
        with self._lock:
            cached = self._leases.get(authority.workspace_id)
        if cached is not None:
            try:
                self.repository.authorize_lease(
                    cached.workspace_id,
                    lease_owner=cached.lease_owner,
                    lease_token=cached.lease_token,
                    lease_fencing_token=cached.lease_fencing_token,
                )
                return cached
            except WorkspaceRepositoryError as exc:
                if exc.reason_code != WORKSPACE_STALE_FENCE:
                    raise
        instance_digest = self._runtime_instance_digest()
        lease_owner = (
            f"{self.runtime_owner_id}::cleanup::{instance_digest}::"
            f"{authority.workspace_id}"
        )
        if len(lease_owner) > 240:
            digest = hashlib.sha256(lease_owner.encode("utf-8")).hexdigest()[:32]
            lease_owner = f"runtime-cleanup::{instance_digest}::{digest}"
        token = self._lease_token(
            workspace_id=authority.workspace_id,
            lease_owner=lease_owner,
            graph_id=authority.graph_id,
            graph_revision=authority.graph_revision,
        )
        acquired = self.repository.acquire_lease(
            authority.workspace_id,
            lease_owner=lease_owner,
            lease_token=token,
            lease_ms=self.lease_ms,
            expected_status_version=authority.status_version,
        ).authority
        lease = DurableWorkspaceLease(
            workspace_id=authority.workspace_id,
            lease_owner=lease_owner,
            lease_token=token,
            lease_fencing_token=acquired.lease_fencing_token,
        )
        with self._lock:
            self._leases[authority.workspace_id] = lease
        return lease

    def _lease_for_resolution(self, resolution: WorkspaceResolution) -> DurableWorkspaceLease:
        workspace_id = str(resolution.workspace_id or "").strip()
        if resolution.source != "durable_workspace" or not workspace_id:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "resolution is not a durable workspace",
            )
        with self._lock:
            self._refresh_process_instance_locked()
            lease = self._leases.get(workspace_id)
        if (
            lease is None
            or lease.lease_owner != resolution.lease_owner
            or lease.lease_fencing_token != resolution.lease_fencing_token
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "workspace resolution has no matching server-side lease",
            )
        return lease

    def _order_binding(
        self,
        order: WorkOrderV3,
        *,
        workspace_id: str,
        execution_authority: dict[str, Any] | None,
    ) -> dict[str, Any]:
        authority = dict(execution_authority or {})
        root_work_order_id = order.root_work_order_id
        graph_id = str(order.task_graph_id or "").strip()
        graph_revision = order.graph_revision
        expected_workspace_id = self.workspace_id_for(
            graph_id=graph_id,
            graph_revision=graph_revision,
        )
        if str(workspace_id or "").strip() != expected_workspace_id:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "workspace id is not the server-derived graph workspace",
            )
        authority_graph = self._required(authority.get("graph_id"), "authority.graph_id", 240)
        authority_root = self._required(
            authority.get("root_work_order_id"),
            "authority.root_work_order_id",
            240,
        )
        authority_node = self._required(authority.get("node_id"), "authority.node_id", 240)
        authority_attempt = self._required(
            authority.get("attempt_id"),
            "authority.attempt_id",
            240,
        )
        authority_lease = self._required(authority.get("lease_id"), "authority.lease_id", 240)
        authority_command = self._required(
            authority.get("command_id"),
            "authority.command_id",
            240,
        )
        authority_principal = self._required(
            authority.get("principal_id"),
            "authority.principal_id",
            240,
        )
        authority_lease_owner = self._required(
            authority.get("lease_owner_id"),
            "authority.lease_owner_id",
            240,
        )
        raw_authority_revision = authority.get("graph_revision")
        raw_fence = authority.get("fencing_token")
        if isinstance(raw_authority_revision, bool) or isinstance(raw_fence, bool):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "execution authority integer fields are invalid",
            )
        try:
            authority_revision = int(raw_authority_revision)
            authority_fence = int(raw_fence)
        except (TypeError, ValueError) as exc:
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "execution authority integer fields are invalid",
            ) from exc
        expected_node = str(order.task_node_id or "").strip()
        if (
            authority_graph != graph_id
            or authority_revision != graph_revision
            or authority_root != root_work_order_id
            or authority_node != expected_node
            or authority_lease_owner != self.runtime_owner_id
            or authority_fence < 1
        ):
            raise DurableWorkspaceRuntimeError(
                WORKSPACE_RUNTIME_BINDING_INVALID,
                "execution authority does not match the admitted graph attempt",
            )
        owner_digest = hashlib.sha256(
            json.dumps(
                {
                    "attempt_id": authority_attempt,
                    "command_id": authority_command,
                    "fencing_token": authority_fence,
                    "graph_id": authority_graph,
                    "graph_revision": authority_revision,
                    "lease_id": authority_lease,
                    "node_id": authority_node,
                    "runtime_instance_id": self._runtime_instance_id,
                    "runtime_owner_id": self.runtime_owner_id,
                    "runtime_pid": self._runtime_pid,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:40]
        instance_digest = self._runtime_instance_digest()
        lease_owner = (
            f"{self.runtime_owner_id}::workspace::{instance_digest}::{owner_digest}"
        )
        if len(lease_owner) > 240:
            lease_owner = f"runtime-workspace::{instance_digest}::{owner_digest}"
        return {
            "workspace_id": expected_workspace_id,
            "root_work_order_id": self._required(root_work_order_id, "root_work_order_id", 240),
            "graph_id": self._required(graph_id, "graph_id", 240),
            "graph_revision": graph_revision,
            "principal_id": authority_principal,
            "lease_owner": lease_owner,
        }

    def _lease_token(
        self,
        *,
        workspace_id: str,
        lease_owner: str,
        graph_id: str,
        graph_revision: int,
    ) -> str:
        message = (
            f"{workspace_id}\n{lease_owner}\n{graph_id}\n{graph_revision}\n"
            f"{self._runtime_pid}\n{self._runtime_instance_id}"
        ).encode("utf-8")
        digest = hmac.new(self._token_secret, message, hashlib.sha256).digest()
        return "workspace-lease-" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def _ensure_process_instance(self) -> None:
        with self._lock:
            self._refresh_process_instance_locked()

    def _refresh_process_instance_locked(self) -> None:
        """Invalidate inherited process-local credentials after a fork."""

        current_pid = os.getpid()
        if current_pid == self._runtime_pid:
            return
        self._runtime_pid = current_pid
        self._runtime_instance_id = secrets.token_hex(24)
        self._leases.clear()
        self._active_scopes.clear()
        self._handoff_ready.clear()
        self._cleanup_inflight.clear()

    def _runtime_instance_digest(self) -> str:
        binding = (
            f"{self.runtime_owner_id}\n{self._runtime_pid}\n"
            f"{self._runtime_instance_id}"
        )
        return hashlib.sha256(binding.encode("utf-8")).hexdigest()[:24]

__all__ = [
    "DurableWorkspaceRuntime",
    "DurableWorkspaceRuntimeError",
    "WORKSPACE_CLEANUP_UNAVAILABLE",
    "WORKSPACE_HEARTBEAT_LOST",
    "WORKSPACE_RUNTIME_BINDING_INVALID",
    "WorkspaceArtifactPromotionReceipt",
    "WorkspaceArtifactAbandonmentReceipt",
    "WorkspaceCleanupReceipt",
]
