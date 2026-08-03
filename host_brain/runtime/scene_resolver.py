"""Single, workspace-scoped Scene resolver for NE-03.

The resolver is deliberately read-only with respect to World/Life.  Callers
provide trusted context signals, never a final scene.  The resolver derives a
single digest-bound identity and typed restrict-only constraints.  Only a
changed fingerprint appends a transition to the Runtime Event ledger;
repeated reads return the same revision and create no journal noise.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Mapping
from typing import Any

from host_brain.integrations.runtime_events import (
    RuntimeEvent,
    validate_runtime_event_record_envelope,
)

from .context_contracts import (
    ContextConstraintV1,
    SceneDecisionV2,
    SceneResolutionInputV1,
    SceneTransitionV1,
    WorldSnapshotV2,
)
from .runtime_guardian import GuardianSnapshotV1


class SceneResolverError(ValueError):
    """Scene input or transition replay is not safe to accept."""


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _scope_id(workspace_id: str, subject_id: str) -> str:
    return hashlib.sha256(f"{workspace_id}\x00{subject_id}".encode("utf-8")).hexdigest()


def _fingerprint(decision: SceneDecisionV2) -> str:
    encoded = json.dumps(
        decision.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _policy_constraint(
    code: str,
    disposition: str,
    *,
    scene_id: str,
    reason: str,
) -> ContextConstraintV1:
    return ContextConstraintV1(
        code=code,
        disposition=disposition,
        origin="SCENE",
        source_ref=f"policy://scene/v1/{scene_id}",
        reason=reason,
    )


def _derive_scene(
    context: SceneResolutionInputV1,
    guardian: GuardianSnapshotV1,
) -> tuple[str, tuple[ContextConstraintV1, ...]]:
    task_kind = context.task_kind
    guardian_status = guardian.status
    activity_state = context.activity_state
    user_state = context.user_state

    if guardian_status == "MAINTENANCE" or activity_state in {
        "MAINTENANCE",
        "LOCKED",
    } or task_kind in {
        "DEPLOYMENT",
        "MAINTENANCE",
        "RELEASE",
        "RELEASE_MAINTENANCE",
    }:
        scene_id = "scene.release-maintenance"
        derived = [
            _policy_constraint(
                "MAINTENANCE_EFFECT_REVIEW",
                "REVIEW",
                scene_id=scene_id,
                reason="发布或维护期间的外部效果必须复核",
            )
        ]
    elif activity_state in {"SLEEP", "SLEEPING", "QUIET_HOURS"}:
        scene_id = "scene.sleep-low-interruption"
        derived = [
            _policy_constraint(
                "LOW_INTERRUPTION",
                "RESTRICT",
                scene_id=scene_id,
                reason="休眠模式只允许低打扰交互",
            ),
            _policy_constraint(
                "PROACTIVE_ACTION_BLOCKED",
                "RESTRICT",
                scene_id=scene_id,
                reason="休眠模式禁止主动动作",
            ),
        ]
    elif activity_state in {"GAME", "GAMING", "GAME_LOW_INTERRUPTION"}:
        scene_id = "scene.game-low-interruption"
        derived = [
            _policy_constraint(
                "LOW_INTERRUPTION",
                "RESTRICT",
                scene_id=scene_id,
                reason="游戏活动期间只允许低打扰交互",
            )
        ]
    elif context.local_hour >= 23 or context.local_hour < 6:
        scene_id = "scene.late-night"
        derived = [
            _policy_constraint(
                "LOW_INTERRUPTION",
                "RESTRICT",
                scene_id=scene_id,
                reason="深夜场景降低打扰并限制主动行为",
            )
        ]
    elif user_state in {"ANGRY", "FRUSTRATED", "IRRITATED", "UPSET"}:
        scene_id = "scene.user-frustrated"
        derived = [
            _policy_constraint(
                "CONCISE_SUPPORTIVE_RESPONSE",
                "RESTRICT",
                scene_id=scene_id,
                reason="用户烦躁时采用简洁支持性表达",
            )
        ]
    elif task_kind in {"ARCHITECTURE", "ARCHITECTURE_REFACTOR", "REFACTOR"}:
        scene_id = "scene.architecture-refactor"
        derived = [
            _policy_constraint(
                "EVIDENCE_BOUND_CHANGE",
                "RESTRICT",
                scene_id=scene_id,
                reason="架构变更必须绑定代码和验证证据",
            )
        ]
    elif task_kind in {"DEBUG", "DEBUGGING", "INCIDENT"}:
        scene_id = "scene.debugging"
        derived = [
            _policy_constraint(
                "REPRODUCTION_BEFORE_MUTATION",
                "RESTRICT",
                scene_id=scene_id,
                reason="调试修改前必须先形成可验证复现",
            )
        ]
    elif task_kind in {"INVESTIGATION", "RESEARCH"}:
        scene_id = "scene.research"
        derived = [
            _policy_constraint(
                "SOURCE_EVIDENCE_REQUIRED",
                "RESTRICT",
                scene_id=scene_id,
                reason="研究结论必须绑定来源证据",
            )
        ]
    else:
        scene_id = "scene.casual-chat"
        derived = []

    if context.risk_level >= 4:
        derived.append(
            _policy_constraint(
                "HIGH_RISK_REVIEW",
                "REVIEW",
                scene_id=scene_id,
                reason="高风险动作必须经过 Review 和精确 Preview",
            )
        )
    if guardian_status in {"DEGRADED", "RECOVERY_PENDING", "SLO_BREACH"}:
        low_risk_companion_chat = (
            task_kind in {"CHAT", "CONVERSATION"}
            and context.risk_level <= 2
        )
        derived.append(
            ContextConstraintV1(
                code=(
                    "GUARDIAN_DEGRADED_RESTRICTION"
                    if low_risk_companion_chat
                    else "GUARDIAN_DEGRADED_REVIEW"
                ),
                disposition="RESTRICT" if low_risk_companion_chat else "REVIEW",
                origin="GUARDIAN",
                source_ref=(
                    "guardian://snapshot/sha256/"
                    f"{guardian.snapshot_digest}"
                ),
                reason=(
                    "低风险陪伴对话按可用通道降级，不阻塞文字回复"
                    if low_risk_companion_chat
                    else "运行期降级或 SLO 故障时必须复核效果"
                ),
            )
        )
    if guardian_status == "SLEEP":
        derived.append(
            ContextConstraintV1(
                code="PROACTIVE_ACTION_BLOCKED",
                disposition="RESTRICT",
                origin="GUARDIAN",
                source_ref=(
                    "guardian://snapshot/sha256/"
                    f"{guardian.snapshot_digest}"
                ),
                reason="休眠状态禁止主动动作",
            )
        )
    if context.relationship_state in {"RESTRICTED", "UNTRUSTED"}:
        derived.append(
            _policy_constraint(
                "RELATIONSHIP_BOUND_REVIEW",
                "REVIEW",
                scene_id=scene_id,
                reason="关系约束要求对当前动作进行复核",
            )
        )

    unique = {
        item.constraint_digest: item
        for item in (*context.policy_constraints, *derived)
    }
    return scene_id, tuple(
        sorted(unique.values(), key=lambda item: item.constraint_digest)
    )


class SceneResolver:
    """Resolve one shared scene identity for all Host consumers."""

    _EVENT_TYPE = "SCENE_TRANSITION_RECORDED"
    _AGGREGATE_TYPE = "SCENE_STATE"
    _PAGE_SIZE = 1000

    def __init__(
        self,
        *,
        event_store: Any | None = None,
        runtime_scope_id: str,
        runtime_authority_provider: Any,
        guardian_snapshot_provider: Any,
        clock: Any = _now_ms,
    ) -> None:
        self._event_store = event_store
        self._clock = clock
        self._runtime_scope_id = str(runtime_scope_id or "").strip()
        if not self._runtime_scope_id or not callable(runtime_authority_provider):
            raise SceneResolverError("scene runtime authority is required")
        if not callable(guardian_snapshot_provider):
            raise SceneResolverError("scene Guardian snapshot provider is required")
        self._runtime_authority_provider = runtime_authority_provider
        self._guardian_snapshot_provider = guardian_snapshot_provider
        self._lock = threading.RLock()
        self._latest: dict[tuple[str, str], tuple[str, SceneDecisionV2]] = {}
        self._decisions: dict[
            tuple[str, str], dict[int, SceneDecisionV2]
        ] = {}
        self._history: dict[tuple[str, str], list[SceneTransitionV1]] = {}
        self._loaded: set[tuple[str, str]] = set()
        self._cursor: dict[tuple[str, str], int] = {}
        self._authority_head: dict[tuple[str, str], tuple[str, str, int]] = {}

    def _read_runtime_authority(self, *, observed_at_ms: int) -> dict[str, Any]:
        try:
            raw = self._runtime_authority_provider()
        except Exception as exc:
            raise SceneResolverError("scene runtime authority unavailable") from exc
        if not isinstance(raw, Mapping):
            raise SceneResolverError("scene runtime authority unavailable")
        authority = dict(raw)
        scope = str(authority.get("runtime_scope_id") or "").strip()
        owner = str(authority.get("owner_id") or "").strip()
        lease = str(authority.get("lease_id") or "").strip()
        try:
            fence = int(authority.get("fencing_token"))
            issued = int(authority.get("issued_at_ms"))
            expires = int(authority.get("expires_at_ms"))
        except (TypeError, ValueError) as exc:
            raise SceneResolverError("scene runtime authority is invalid") from exc
        if (
            scope != self._runtime_scope_id
            or not owner
            or not lease
            or fence < 1
            or issued > observed_at_ms
            or expires <= observed_at_ms
            or authority.get("released_at_ms") is not None
        ):
            raise SceneResolverError("scene runtime authority is stale")
        return {
            **authority,
            "runtime_scope_id": scope,
            "owner_id": owner,
            "lease_id": lease,
            "fencing_token": fence,
        }

    def _read_guardian_snapshot(
        self,
        *,
        authority: Mapping[str, Any],
    ) -> GuardianSnapshotV1:
        try:
            snapshot = self._guardian_snapshot_provider()
        except Exception as exc:
            raise SceneResolverError("Guardian snapshot unavailable") from exc
        if not isinstance(snapshot, GuardianSnapshotV1):
            raise SceneResolverError("Guardian snapshot is not typed")
        if snapshot.status not in {
            "READY",
            "DEGRADED",
            "RECOVERY_PENDING",
            "MAINTENANCE",
            "SLEEP",
            "SLO_BREACH",
        }:
            raise SceneResolverError("Guardian snapshot status is unsupported")
        if (
            snapshot.runtime_scope_id != authority["runtime_scope_id"]
            or snapshot.runtime_owner_id != authority["owner_id"]
            or snapshot.runtime_lease_id != authority["lease_id"]
            or snapshot.fencing_token != authority["fencing_token"]
        ):
            raise SceneResolverError("Guardian snapshot runtime authority drift")
        return snapshot

    def _restore_scope(self, workspace_id: str, subject_id: str) -> None:
        scope = (workspace_id, subject_id)
        authority = self._read_runtime_authority(observed_at_ms=int(self._clock()))
        if self._event_store is None:
            self._loaded.add(scope)
            return
        aggregate_id = _scope_id(workspace_id, subject_id)
        records: list[dict[str, Any]] = []
        cursor = self._cursor.get(scope, 0)
        try:
            while True:
                page = self._event_store.list_events_for_aggregates(
                    aggregate_refs=((self._AGGREGATE_TYPE, aggregate_id),),
                    after_cursor=cursor,
                    limit=self._PAGE_SIZE,
                )
                if not page:
                    break
                next_cursor = int(page[-1].get("cursor") or 0)
                if next_cursor <= cursor:
                    raise SceneResolverError(
                        "scene transition journal cursor did not advance"
                    )
                records.extend(page)
                cursor = next_cursor
                if len(page) < self._PAGE_SIZE:
                    break
        except Exception as exc:  # pragma: no cover - store-specific failure
            if isinstance(exc, SceneResolverError):
                raise
            raise SceneResolverError("scene transition journal unavailable") from exc
        # Replay into isolated scratch state.  A corrupt record near the end of
        # a fetched tail must not install its valid prefix while leaving the
        # durable cursor behind.  Otherwise repairing the durable tail would
        # make this same resolver re-read the prefix against an already
        # advanced in-memory revision and fail forever.
        scratch_latest = self._latest.get(scope)
        scratch_decisions = dict(self._decisions.get(scope, {}))
        scratch_history = list(self._history.get(scope, ()))
        previous = scratch_latest
        previous_decision = previous[1] if previous is not None else None
        expected_revision = (
            previous_decision.revision + 1
            if previous_decision is not None
            else 1
        )
        previous_cursor = self._cursor.get(scope, 0)
        authority_head = self._authority_head.get(scope)
        previous_owner = authority_head[0] if authority_head is not None else None
        previous_lease = authority_head[1] if authority_head is not None else None
        previous_fence = authority_head[2] if authority_head is not None else 0
        for record in records:
            record_cursor = int(record.get("cursor") or 0)
            if record_cursor <= previous_cursor:
                raise SceneResolverError("scene transition journal cursor order is invalid")
            previous_cursor = record_cursor
            if (
                str(record.get("event_type") or "") != self._EVENT_TYPE
                or str(record.get("aggregate_type") or "") != self._AGGREGATE_TYPE
                or str(record.get("aggregate_id") or "") != aggregate_id
            ):
                raise SceneResolverError("scene transition aggregate identity mismatch")
            version = int(record.get("aggregate_version") or 0)
            if version != expected_revision:
                raise SceneResolverError("scene transition revision is not contiguous")
            payload = dict(record.get("payload") or {})
            runtime_scope = str(payload.get("runtime_scope_id") or "")
            runtime_owner = str(payload.get("runtime_owner_id") or "")
            runtime_lease = str(payload.get("runtime_lease_id") or "")
            runtime_fence = payload.get("fencing_token")
            if (
                runtime_scope != self._runtime_scope_id
                or not runtime_owner
                or not runtime_lease
                or isinstance(runtime_fence, bool)
                or not isinstance(runtime_fence, int)
                or runtime_fence < 1
                or runtime_fence < previous_fence
                or (
                    runtime_fence == previous_fence
                    and previous_owner is not None
                    and (
                        runtime_owner != previous_owner
                        or runtime_lease != previous_lease
                    )
                )
                or (
                    runtime_fence > previous_fence
                    and previous_lease is not None
                    and runtime_lease == previous_lease
                )
            ):
                raise SceneResolverError("scene runtime authority lineage is invalid")
            decision_payload = dict(payload.get("decision") or {})
            transition_payload = dict(payload.get("transition") or {})
            try:
                constraints = tuple(
                    ContextConstraintV1(
                        code=item["code"],
                        disposition=item["disposition"],
                        origin=item["origin"],
                        source_ref=item["source_ref"],
                        reason=item.get("reason", ""),
                    )
                    for item in decision_payload["constraints"]
                )
                decision = SceneDecisionV2(
                    workspace_id=decision_payload["workspace_id"],
                    subject_id=decision_payload["subject_id"],
                    scene_id=decision_payload["scene_id"],
                    revision=int(decision_payload["revision"]),
                    world_snapshot_digest=decision_payload["world_snapshot_digest"],
                    context_digest=decision_payload["context_digest"],
                    guardian_snapshot_digest=decision_payload[
                        "guardian_snapshot_digest"
                    ],
                    constraints=constraints,
                    decision_digest=decision_payload["decision_digest"],
                )
                transition = SceneTransitionV1(
                    workspace_id=transition_payload["workspace_id"],
                    subject_id=transition_payload["subject_id"],
                    scene_id=transition_payload["scene_id"],
                    from_revision=int(transition_payload["from_revision"]),
                    to_revision=int(transition_payload["to_revision"]),
                    from_digest=transition_payload.get("from_digest", ""),
                    to_digest=transition_payload["to_digest"],
                    world_snapshot_digest=transition_payload["world_snapshot_digest"],
                    context_digest=transition_payload["context_digest"],
                    observed_at_ms=int(transition_payload["observed_at_ms"]),
                    transition_digest=transition_payload["transition_digest"],
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SceneResolverError("scene transition journal contains invalid evidence") from exc
            try:
                validate_runtime_event_record_envelope(
                    record,
                    expected=RuntimeEvent.create(
                        event_type=self._EVENT_TYPE,
                        source_kind="SYSTEM",
                        source_ref=f"runtime://scene-resolver/{aggregate_id}",
                        aggregate_type=self._AGGREGATE_TYPE,
                        aggregate_id=aggregate_id,
                        aggregate_version=version,
                        payload_ref=(
                            f"scene://transition/{transition.transition_digest}"
                        ),
                        payload=payload,
                        evidence_refs=(
                            "world-snapshot://sha256/"
                            f"{decision.world_snapshot_digest}",
                            "scene-context://sha256/"
                            f"{decision.context_digest}",
                            "guardian-snapshot://sha256/"
                            f"{decision.guardian_snapshot_digest}",
                        ),
                        ts_ms=transition.observed_at_ms,
                    ),
                )
            except Exception as exc:
                raise SceneResolverError(
                    "scene RuntimeEvent envelope is invalid"
                ) from exc
            if (
                decision.workspace_id != workspace_id
                or decision.subject_id != subject_id
                or transition.workspace_id != workspace_id
                or transition.subject_id != subject_id
                or decision.revision != version
                or transition.scene_id != decision.scene_id
                or transition.from_revision != version - 1
                or transition.to_revision != decision.revision
                or transition.to_digest != decision.decision_digest
                or transition.world_snapshot_digest
                != decision.world_snapshot_digest
                or transition.context_digest != decision.context_digest
                or transition.from_digest
                != (
                    previous_decision.decision_digest
                    if previous_decision is not None
                    else ""
                )
            ):
                raise SceneResolverError("scene transition scope or digest mismatch")
            fingerprint = str(payload.get("fingerprint") or "").strip().lower()
            expected_fingerprint = _fingerprint(decision)
            if not _DIGEST_RE.fullmatch(fingerprint) or fingerprint != expected_fingerprint:
                raise SceneResolverError("scene transition fingerprint is invalid")
            scratch_latest = (fingerprint, decision)
            scratch_decisions[decision.revision] = decision
            scratch_history.append(transition)
            previous_decision = decision
            previous_owner = runtime_owner
            previous_lease = runtime_lease
            previous_fence = runtime_fence
            expected_revision += 1
        if previous_fence > int(authority["fencing_token"]) or (
            previous_fence == int(authority["fencing_token"])
            and previous_owner is not None
            and (
                previous_owner != authority["owner_id"]
                or previous_lease != authority["lease_id"]
            )
        ):
            raise SceneResolverError("scene runtime owner is fenced by durable history")
        # Install the complete validated tail and its cursor as one in-memory
        # commit.  Nothing above this point mutates the live projection.
        if scratch_latest is not None:
            self._latest[scope] = scratch_latest
        else:
            self._latest.pop(scope, None)
        if scratch_decisions:
            self._decisions[scope] = scratch_decisions
        else:
            self._decisions.pop(scope, None)
        if scratch_history:
            self._history[scope] = scratch_history
        else:
            self._history.pop(scope, None)
        self._cursor[scope] = previous_cursor
        if previous_owner is not None and previous_lease is not None:
            self._authority_head[scope] = (
                previous_owner,
                previous_lease,
                previous_fence,
            )
        self._loaded.add(scope)

    def _reload_scope(self, workspace_id: str, subject_id: str) -> None:
        scope = (workspace_id, subject_id)
        self._loaded.discard(scope)
        self._latest.pop(scope, None)
        self._decisions.pop(scope, None)
        self._history.pop(scope, None)
        self._cursor.pop(scope, None)
        self._authority_head.pop(scope, None)
        self._restore_scope(workspace_id, subject_id)

    def _append_transition(
        self,
        *,
        decision: SceneDecisionV2,
        transition: SceneTransitionV1,
        fingerprint: str,
        authority: Mapping[str, Any],
    ) -> tuple[SceneDecisionV2 | None, int | None]:
        if self._event_store is None:
            return None, None
        aggregate_id = _scope_id(decision.workspace_id, decision.subject_id)
        event = RuntimeEvent.create(
            event_type=self._EVENT_TYPE,
            source_kind="SYSTEM",
            source_ref=f"runtime://scene-resolver/{aggregate_id}",
            aggregate_type=self._AGGREGATE_TYPE,
            aggregate_id=aggregate_id,
            aggregate_version=decision.revision,
            payload_ref=f"scene://transition/{transition.transition_digest}",
            payload={
                "runtime_scope_id": authority["runtime_scope_id"],
                "runtime_owner_id": authority["owner_id"],
                "runtime_lease_id": authority["lease_id"],
                "fencing_token": authority["fencing_token"],
                "fingerprint": fingerprint,
                "decision": decision.to_dict(),
                "transition": transition.to_dict(),
            },
            evidence_refs=(
                f"world-snapshot://sha256/{decision.world_snapshot_digest}",
                f"scene-context://sha256/{decision.context_digest}",
                f"guardian-snapshot://sha256/{decision.guardian_snapshot_digest}",
            ),
            ts_ms=transition.observed_at_ms,
        )
        try:
            record = self._event_store.append_event(event)
            validate_runtime_event_record_envelope(record, expected=event)
            if (
                int(record.get("aggregate_version") or 0) != decision.revision
                or str(record.get("aggregate_id") or "") != aggregate_id
                or str(record.get("event_id") or "") != str(event.event_id)
            ):
                raise SceneResolverError(
                    "scene durable append returned a different event"
                )
        except Exception as exc:  # pragma: no cover - store-specific failure
            try:
                self._reload_scope(decision.workspace_id, decision.subject_id)
                winner = self._latest.get(
                    (decision.workspace_id, decision.subject_id)
                )
            except Exception:
                winner = None
            if winner is not None and winner[0] == fingerprint:
                return winner[1], None
            raise SceneResolverError("scene transition could not be durably appended") from exc
        return None, int(record.get("cursor") or 0)

    def resolve(
        self,
        *,
        snapshot: WorldSnapshotV2,
        context: SceneResolutionInputV1,
        observed_at_ms: int | None = None,
    ) -> SceneDecisionV2:
        if not isinstance(snapshot, WorldSnapshotV2):
            raise SceneResolverError("snapshot must be WorldSnapshotV2")
        if not isinstance(context, SceneResolutionInputV1):
            raise SceneResolverError("context must be SceneResolutionInputV1")
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        if observed < 0:
            raise SceneResolverError("observed_at_ms must be non-negative")
        scope = (snapshot.workspace_id, snapshot.subject_id)
        if (
            context.workspace_id != snapshot.workspace_id
            or context.subject_id != snapshot.subject_id
        ):
            raise SceneResolverError("scene input scope differs from World snapshot")
        authority = self._read_runtime_authority(observed_at_ms=observed)
        guardian = self._read_guardian_snapshot(authority=authority)
        scene_id, constraints = _derive_scene(context, guardian)
        with self._lock:
            self._restore_scope(*scope)
            previous = self._latest.get(scope)
            from_revision = previous[1].revision if previous is not None else 0
            decision = SceneDecisionV2(
                workspace_id=snapshot.workspace_id,
                subject_id=snapshot.subject_id,
                scene_id=scene_id,
                revision=from_revision + 1,
                world_snapshot_digest=snapshot.snapshot_digest,
                context_digest=context.context_digest,
                guardian_snapshot_digest=guardian.snapshot_digest,
                constraints=constraints,
            )
            fingerprint = _fingerprint(decision)
            if previous is not None:
                comparison = SceneDecisionV2(
                    workspace_id=decision.workspace_id,
                    subject_id=decision.subject_id,
                    scene_id=decision.scene_id,
                    revision=previous[1].revision,
                    world_snapshot_digest=decision.world_snapshot_digest,
                    context_digest=decision.context_digest,
                    guardian_snapshot_digest=decision.guardian_snapshot_digest,
                    constraints=decision.constraints,
                )
                if _fingerprint(comparison) == previous[0]:
                    return previous[1]
            transition = SceneTransitionV1(
                workspace_id=snapshot.workspace_id,
                subject_id=snapshot.subject_id,
                scene_id=scene_id,
                from_revision=from_revision,
                to_revision=decision.revision,
                from_digest=previous[1].decision_digest if previous is not None else "",
                to_digest=decision.decision_digest,
                world_snapshot_digest=snapshot.snapshot_digest,
                context_digest=context.context_digest,
                observed_at_ms=observed,
            )
            winner, durable_cursor = self._append_transition(
                decision=decision,
                transition=transition,
                fingerprint=fingerprint,
                authority=authority,
            )
            if winner is not None:
                return winner
            self._latest[scope] = (fingerprint, decision)
            self._decisions.setdefault(scope, {})[decision.revision] = decision
            self._history.setdefault(scope, []).append(transition)
            if durable_cursor is not None:
                if durable_cursor <= self._cursor.get(scope, 0):
                    raise SceneResolverError(
                        "scene durable append cursor did not advance"
                    )
                self._cursor[scope] = durable_cursor
            return decision

    def require_decision(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        scene_id: str,
        revision: int,
        decision_digest: str,
    ) -> SceneDecisionV2:
        """Resolve one exact durable Scene identity without selecting a new scene.

        Task admission and downstream projections use this read boundary to
        prove that a frozen Scene was emitted by this resolver.  It deliberately
        accepts historical revisions so exact command replay does not drift when
        a later observation advances the workspace Scene.
        """

        workspace = str(workspace_id or "").strip()
        subject = str(subject_id or "").strip()
        scene = str(scene_id or "").strip()
        digest = str(decision_digest or "").strip().lower()
        if (
            not workspace
            or not subject
            or not scene
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise SceneResolverError("scene identity lookup is invalid")
        scope = (workspace, subject)
        with self._lock:
            if self._event_store is None:
                self._restore_scope(workspace, subject)
            else:
                self._reload_scope(workspace, subject)
            decision = self._decisions.get(scope, {}).get(revision)
            if (
                decision is None
                or decision.scene_id != scene
                or decision.decision_digest != digest
            ):
                raise SceneResolverError(
                    "frozen Scene identity is absent or digest-drifted"
                )
            return decision

    def transition_history(
        self, *, workspace_id: str, subject_id: str
    ) -> tuple[dict[str, Any], ...]:
        scope = (str(workspace_id or "").strip(), str(subject_id or "").strip())
        with self._lock:
            self._restore_scope(*scope)
            return tuple(item.to_dict() for item in self._history.get(scope, ()))


__all__ = ["SceneResolver", "SceneResolverError"]
