"""Private command operations mixed into the durable RuntimeGuardian owner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from host_brain.runtime.guardian_contracts import (
    GuardianSnapshotV1,
    RuntimeGuardianError,
    _digest,
    _refs,
    _text,
)
from host_brain.observability.degradation_state import (
    DegradationStateMachine,
    normalize_service_observations,
    normalize_startup_observations,
)


class _RuntimeGuardianOperations:
    """Mutation commands that delegate every durable write to ``self._append``."""

    def _observe_normalized_batch(
        self,
        observations: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, Any], ...]:
        with self._lock:
            self._require_degradation_read_authority()
        transitions: list[dict[str, Any]] = []
        for observation in observations:
            before_revision = self.snapshot().revision
            snapshot = self.observe_dependency(
                dependency_id=str(observation["dependency_id"]),
                status=str(observation["status"]),
                evidence_refs=tuple(observation["evidence_refs"]),
                source_ref=str(observation["source_ref"]),
                observed_at_ms=int(observation["observed_at_ms"]),
                detail=str(observation["detail"]),
            )
            if snapshot.revision == before_revision:
                continue
            transitions.append(
                {
                    "dependency_id": str(observation["dependency_id"]),
                    "status": str(observation["status"]),
                    "evidence_refs": list(observation["evidence_refs"]),
                    "source_ref": str(observation["source_ref"]),
                    "observed_at_ms": int(observation["observed_at_ms"]),
                    "guardian_revision": snapshot.revision,
                    "guardian_status": snapshot.status,
                    "guardian_snapshot_digest": snapshot.snapshot_digest,
                }
            )
        return tuple(transitions)

    def observe_service_batch(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        source_ref: str,
        observed_at_ms: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Normalize and durably apply one service-health probe batch."""

        observed = self._clock() if observed_at_ms is None else observed_at_ms
        observations = normalize_service_observations(
            rows,
            source_ref=source_ref,
            now_ms=observed,
        )
        return self._observe_normalized_batch(observations)

    def observe_startup_batch(
        self,
        statuses: Mapping[str, Any],
        *,
        source_ref: str,
        observed_at_ms: int | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Normalize and durably apply one startup result batch."""

        observed = self._clock() if observed_at_ms is None else observed_at_ms
        observations = normalize_startup_observations(
            statuses,
            source_ref=source_ref,
            now_ms=observed,
        )
        return self._observe_normalized_batch(observations)

    def observe_dependency(
        self,
        *,
        dependency_id: str,
        status: str,
        evidence_refs: Sequence[str],
        source_ref: str,
        observed_at_ms: int | None = None,
        detail: str = "",
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        refs = _refs(evidence_refs)
        with self._lock:
            self._require_monotonic_observation(observed)
            current = self._capture_mutable_state()
            shadow = DegradationStateMachine(restore_from_store=False)
            shadow.restore_reducer_state(current["degradation_state"])
            transition = shadow.observe(
                dependency_id=dependency_id,
                status=status,
                evidence_refs=refs,
                source_ref=_text(source_ref, "source_ref"),
                now_ms=observed,
                detail=detail,
            )
            if transition is not None:
                self._append(
                    event_type="GUARDIAN_HEALTH_OBSERVED",
                    details={
                        "dependency_id": transition.dependency_id,
                        "dependency_status": transition.dependency_status,
                        "degradation_event_type": transition.event_type,
                        "degradation_transition_id": transition.transition_id,
                        "source_ref": transition.source_ref,
                        "evidence_refs": refs,
                    },
                    observed_at_ms=observed,
                    next_state=self._state_with(
                        current,
                        degradation_state=shadow.export_reducer_state(),
                    ),
                )
            return self.snapshot()

    def acknowledge_recovery(
        self,
        *,
        dependency_id: str,
        review_store: Any,
        review_id: str,
        resume_token_ref: str,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        with self._lock:
            self._require_monotonic_observation(observed)
            binding = self._degradation.prepare_recovery_acknowledgement(
                dependency_id=dependency_id,
                review_id=_text(review_id, "review_id"),
            )
            binding_identity = self._recovery_binding_identity(binding)
            operation_id = (
                "guardian-recovery-ack://sha256/"
                f"{_digest(binding_identity)}"
            )
            committed = next(
                (
                    item
                    for item in self._history
                    if item.event_type == "GUARDIAN_RECOVERY_ACK_COMMITTED"
                    and isinstance(item.payload.get("binding"), Mapping)
                    and self._same_recovery_binding(
                        item.payload["binding"],
                        binding,
                    )
                ),
                None,
            )
            if committed is not None:
                return self.snapshot()
            prepared = next(
                (
                    item
                    for item in self._history
                    if item.event_type == "GUARDIAN_RECOVERY_ACK_PREPARED"
                    and isinstance(item.payload.get("binding"), Mapping)
                    and self._same_recovery_binding(
                        item.payload["binding"],
                        binding,
                    )
                ),
                None,
            )
            if prepared is not None:
                operation_id = _text(
                    prepared.payload.get("operation_id"),
                    "prepared recovery operation_id",
                )
            if prepared is None:
                current = self._capture_mutable_state()
                self._append(
                    event_type="GUARDIAN_RECOVERY_ACK_PREPARED",
                    details={
                        "operation_id": operation_id,
                        "binding": binding,
                        "evidence_refs": tuple(binding.get("evidence_refs") or ()),
                    },
                    observed_at_ms=observed,
                    next_state=current,
                )
            try:
                resumed = review_store.resume_review(
                    str(binding["review_id"]),
                    resume_token_ref=_text(
                        resume_token_ref,
                        "resume_token_ref",
                    ),
                    subject_ref=binding["subject_ref"],
                    subject_version=binding["subject_version"],
                    resume_phase=binding["resume_phase"],
                    checkpoint_id=binding["checkpoint_id"],
                    resume_result_ref=binding["resume_result_ref"],
                )
                if bool(resumed.get("replay_side_effects")):
                    raise RuntimeGuardianError(
                        "ReviewStore attempted to replay recovery side effects"
                    )
                review_record = dict(resumed.get("review") or {})
            except Exception as resume_exc:
                try:
                    review_record = dict(
                        review_store.get_review(str(binding["review_id"]))
                    )
                except Exception:
                    raise RuntimeGuardianError(
                        "recovery review consume outcome is unknown"
                    ) from resume_exc
                review_item = dict(review_record.get("review") or {})
                if (
                    str(review_item.get("status") or "") != "CONSUMED"
                    or str(review_item.get("resume_result_ref") or "")
                    != str(binding["resume_result_ref"])
                ):
                    raise RuntimeGuardianError(
                        "recovery review was not consumed by this operation"
                    ) from resume_exc

            current = self._capture_mutable_state()
            shadow = DegradationStateMachine(restore_from_store=False)
            shadow.restore_reducer_state(current["degradation_state"])
            transition = shadow.apply_consumed_recovery_acknowledgement(
                binding=binding,
                review_record=review_record,
                now_ms=observed,
            )
            self._append(
                event_type="GUARDIAN_RECOVERY_ACK_COMMITTED",
                details={
                    "operation_id": operation_id,
                    "binding": binding,
                    "dependency_id": transition.dependency_id,
                    "degradation_transition_id": transition.transition_id,
                    "review_id": binding["review_id"],
                    "evidence_refs": tuple(transition.evidence_refs),
                },
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    degradation_state=shadow.export_reducer_state(),
                ),
            )
            return self.snapshot()

    def reconcile_prepared_recoveries(
        self,
        *,
        review_store: Any,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        """Finish only exact PREPARED operations whose Review is already consumed."""

        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        with self._lock:
            self._require_monotonic_observation(observed)
            committed_ids = {
                str(item.payload.get("operation_id") or "")
                for item in self._history
                if item.event_type == "GUARDIAN_RECOVERY_ACK_COMMITTED"
            }
            committed_binding_digests = {
                _digest(
                    self._recovery_binding_identity(item.payload["binding"])
                )
                for item in self._history
                if item.event_type == "GUARDIAN_RECOVERY_ACK_COMMITTED"
                and isinstance(item.payload.get("binding"), Mapping)
            }
            prepared = tuple(
                item
                for item in self._history
                if item.event_type == "GUARDIAN_RECOVERY_ACK_PREPARED"
                and str(item.payload.get("operation_id") or "")
                not in committed_ids
            )
            for item in prepared:
                prepared_binding = dict(item.payload.get("binding") or {})
                prepared_identity_digest = _digest(
                    self._recovery_binding_identity(prepared_binding)
                )
                if prepared_identity_digest in committed_binding_digests:
                    continue
                operation_id = str(item.payload.get("operation_id") or "")
                try:
                    review_record = dict(
                        review_store.get_review(str(prepared_binding["review_id"]))
                    )
                except Exception:
                    continue
                review_item = dict(review_record.get("review") or {})
                if (
                    str(review_item.get("status") or "") != "CONSUMED"
                    or str(review_item.get("resume_result_ref") or "")
                    != str(prepared_binding.get("resume_result_ref") or "")
                ):
                    continue
                binding = self._degradation.prepare_recovery_acknowledgement(
                    dependency_id=str(prepared_binding["dependency_id"]),
                    review_id=str(prepared_binding["review_id"]),
                )
                if not self._same_recovery_binding(prepared_binding, binding):
                    raise RuntimeGuardianError(
                        "prepared recovery binding drifted before reconciliation"
                    )
                current = self._capture_mutable_state()
                shadow = DegradationStateMachine(restore_from_store=False)
                shadow.restore_reducer_state(current["degradation_state"])
                transition = shadow.apply_consumed_recovery_acknowledgement(
                    binding=binding,
                    review_record=review_record,
                    now_ms=observed,
                )
                self._append(
                    event_type="GUARDIAN_RECOVERY_ACK_COMMITTED",
                    details={
                        "operation_id": operation_id,
                        "binding": binding,
                        "dependency_id": transition.dependency_id,
                        "degradation_transition_id": transition.transition_id,
                        "review_id": binding["review_id"],
                        "evidence_refs": tuple(transition.evidence_refs),
                    },
                    observed_at_ms=observed,
                    next_state=self._state_with(
                        current,
                        degradation_state=shadow.export_reducer_state(),
                    ),
                )
                committed_binding_digests.add(prepared_identity_digest)
            return self.snapshot()

    def enter_maintenance(self, *, reason_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        reason = _text(reason_ref, "reason_ref")
        with self._lock:
            self._require_monotonic_observation(observed)
            if self._mode == "MAINTENANCE" and self._maintenance_ref == reason:
                return self.snapshot()
            current = self._capture_mutable_state()
            self._append(
                event_type="GUARDIAN_MAINTENANCE_ENTERED",
                details={"reason_ref": reason, "evidence_refs": (reason,)},
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    mode="MAINTENANCE",
                    maintenance_ref=reason,
                    sleep_ref=None,
                ),
            )
            return self.snapshot()

    def exit_maintenance(self, *, acknowledgement_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        ack = _text(acknowledgement_ref, "acknowledgement_ref")
        with self._lock:
            self._require_monotonic_observation(observed)
            if self._mode != "MAINTENANCE":
                return self.snapshot()
            current = self._capture_mutable_state()
            self._append(
                event_type="GUARDIAN_MAINTENANCE_EXITED",
                details={"acknowledgement_ref": ack, "evidence_refs": (ack,)},
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    mode="ACTIVE",
                    maintenance_ref=None,
                    sleep_ref=None,
                ),
            )
            return self.snapshot()

    def enter_sleep(self, *, reason_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        reason = _text(reason_ref, "reason_ref")
        with self._lock:
            self._require_monotonic_observation(observed)
            if self._mode == "SLEEP" and self._sleep_ref == reason:
                return self.snapshot()
            current = self._capture_mutable_state()
            self._append(
                event_type="GUARDIAN_SLEEP_ENTERED",
                details={"reason_ref": reason, "evidence_refs": (reason,)},
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    mode="SLEEP",
                    sleep_ref=reason,
                    maintenance_ref=None,
                ),
            )
            return self.snapshot()

    def exit_sleep(self, *, acknowledgement_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        ack = _text(acknowledgement_ref, "acknowledgement_ref")
        with self._lock:
            self._require_monotonic_observation(observed)
            if self._mode != "SLEEP":
                return self.snapshot()
            current = self._capture_mutable_state()
            self._append(
                event_type="GUARDIAN_SLEEP_EXITED",
                details={"acknowledgement_ref": ack, "evidence_refs": (ack,)},
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    mode="ACTIVE",
                    sleep_ref=None,
                    maintenance_ref=None,
                ),
            )
            return self.snapshot()

    def record_slo_breach(
        self,
        *,
        slo_id: str,
        observed_value: float,
        threshold: float,
        evidence_ref: str,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        slo = _text(slo_id, "slo_id")
        evidence = _text(evidence_ref, "evidence_ref")
        if "://" not in evidence:
            raise RuntimeGuardianError("evidence_ref must be durable")
        breach_ref = f"slo-breach://{_digest({'slo_id': slo, 'observed': observed_value, 'threshold': threshold, 'evidence': evidence})}"
        with self._lock:
            self._require_monotonic_observation(observed)
            if breach_ref in self._slo_breaches:
                return self.snapshot()
            current = self._capture_mutable_state()
            next_breaches = set(current["slo_breaches"])
            next_breaches.add(breach_ref)
            self._append(
                event_type="GUARDIAN_SLO_BREACH",
                details={
                    "slo_id": slo,
                    "observed_value": float(observed_value),
                    "threshold": float(threshold),
                    "breach_ref": breach_ref,
                    "evidence_refs": (evidence,),
                },
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    slo_breaches=next_breaches,
                ),
            )
            return self.snapshot()

    def resolve_slo_breach(
        self,
        *,
        breach_ref: str,
        evidence_ref: str,
        observed_at_ms: int | None = None,
    ) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        breach = _text(breach_ref, "breach_ref")
        evidence = _text(evidence_ref, "evidence_ref")
        if "://" not in breach or "://" not in evidence:
            raise RuntimeGuardianError("SLO resolution requires durable references")
        with self._lock:
            self._require_monotonic_observation(observed)
            if breach not in self._slo_breaches:
                raise RuntimeGuardianError("cannot resolve an unknown SLO breach")
            current = self._capture_mutable_state()
            next_breaches = set(current["slo_breaches"])
            next_breaches.remove(breach)
            self._append(
                event_type="GUARDIAN_SLO_RESOLVED",
                details={
                    "breach_ref": breach,
                    "evidence_refs": (evidence,),
                },
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    slo_breaches=next_breaches,
                ),
            )
            return self.snapshot()

    def acknowledge_event(self, *, event_id: str, acknowledgement_ref: str, observed_at_ms: int | None = None) -> GuardianSnapshotV1:
        observed = int(self._clock() if observed_at_ms is None else observed_at_ms)
        target = _text(event_id, "event_id")
        ack = _text(acknowledgement_ref, "acknowledgement_ref")
        with self._lock:
            self._require_monotonic_observation(observed)
            known = {item.event_id for item in self._history}
            if target not in known:
                raise RuntimeGuardianError("cannot acknowledge an unknown guardian event")
            if target in self._acknowledged_events:
                return self.snapshot()
            current = self._capture_mutable_state()
            next_acknowledged = set(current["acknowledged_events"])
            next_acknowledged.add(target)
            self._append(
                event_type="GUARDIAN_EVENT_ACKNOWLEDGED",
                details={
                    "target_event_id": target,
                    "acknowledgement_ref": ack,
                    "evidence_refs": (ack,),
                },
                observed_at_ms=observed,
                next_state=self._state_with(
                    current,
                    acknowledged_events=next_acknowledged,
                ),
            )
            return self.snapshot()

    def event_history(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(item.to_dict() for item in self._history)



__all__ = []
