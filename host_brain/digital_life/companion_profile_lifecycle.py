"""陪伴表达偏好的持久化生命周期。

该服务处于数字生命的关系反馈与 Life 命令账本之间：它只把已成为
canonical 事实的正向关系后效转为低风险表达偏好，绝不改写 Soul、关系
同意状态或 Natsume 的自我情感。候选版本必须先经历五次独立交互的
canary，随后才会以账本事件精确激活或回滚；因此进程重启不会丢失试验
计数，也没有可绕过审计的内存 profile 缓存。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import RelationshipScope
from host_brain.digital_life.service import LifeCommandService
from natsume_contracts.life import (
    CompanionProfileCanaryInteractionV1,
    CompanionProfilePreferencesV1,
    CompanionProfileStateV1,
    CompanionProfileVersionV1,
    LifeCommand,
    LifeCommandReceipt,
    RelationalAftereffectV1,
    baseline_companion_profile,
    utc_day_from_ms,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any, *, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _opaque_ref(value: Any, *, field_name: str) -> str:
    reference = _text(value, limit=512)
    if not reference or "://" not in reference:
        raise ValueError(f"{field_name} must be an opaque reference")
    return reference


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _aftereffect_from_mapping(raw: Mapping[str, Any]) -> RelationalAftereffectV1:
    if not raw:
        raise ValueError("relational aftereffect is required")
    return RelationalAftereffectV1(
        aftereffect_id=raw.get("aftereffect_id"),
        episode_id=raw.get("episode_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        source_receipt_ref=raw.get("source_receipt_ref"),
        user_input_ref=raw.get("user_input_ref"),
        outcome=raw.get("outcome"),
        helpfulness=raw.get("helpfulness"),
        boundary_respected=raw.get("boundary_respected"),
        repair_acknowledged=raw.get("repair_acknowledged"),
        observed_at_ms=raw.get("observed_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        self_id=raw.get("self_id") or "natsume_ai",
    )


@dataclass(frozen=True, slots=True)
class CompanionProfileLifecycleResult:
    """一次生命周期迁移后的回执与受限 profile 头部。

    ``receipt`` 为 ``None`` 表示当前没有进行中的 canary，属于可预期的
    幂等无操作；调用方应以 ``reason_code`` 区分它与真正的拒绝或账本失败。
    """

    scope: RelationshipScope
    receipt: LifeCommandReceipt | None
    state: CompanionProfileStateV1
    reason_code: str


class CompanionProfileLifecycleService:
    """经由 canonical Life 命令 UoW 写入 profile 迁移。

    这是 profile 状态变化的应用层安全边界：外部调用只能提供关系作用域、
    偏好候选和已认证控制来源，实际证据、乐观版本和最终事件均由读取器与
    命令 UoW 决定，避免 HTTP 重试或本地并发直接篡改关系状态。
    """

    def __init__(
        self,
        *,
        command_service_provider: Callable[[], LifeCommandService],
        snapshot_reader_provider: Callable[[], LifeSnapshotReader],
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._command_service_provider = command_service_provider
        self._snapshot_reader_provider = snapshot_reader_provider
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def start_canary(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        preferences: CompanionProfilePreferencesV1 | Mapping[str, Any],
        evidence_aftereffect_ids: Sequence[Any] | None = None,
        source_control_ref: Any = None,
    ) -> CompanionProfileLifecycleResult:
        """Create one evidence-qualified profile and begin its five-turn canary.

        Candidate evidence is reopened from the canonical relationship event
        chain.  Callers cannot claim an arbitrary confidence score, evidence
        day, or clean boundary record.
        """

        scope = self._scope(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        current = self._read_state(scope)
        self._require_established_relationship(scope)
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-canary.v1",
                "scope": scope.aggregate_id,
                "preferences": (
                    preferences.as_dict()
                    if isinstance(preferences, CompanionProfilePreferencesV1)
                    else _mapping(preferences)
                ),
            }
        )[:32]
        control_ref = self._source_control_ref(
            source_control_ref,
            scope=scope,
            request_id=stable_request_id,
        )
        existing = self._existing_control_receipt(
            scope=scope,
            source_control_ref=control_ref,
        )
        if existing is not None:
            return CompanionProfileLifecycleResult(
                scope=scope,
                receipt=existing.as_replayed(),
                state=current,
                reason_code="COMPANION_PROFILE_CANARY_REPLAYED",
            )
        if current.canary_profile is not None:
            raise ValueError("COMPANION_PROFILE_CANARY_ALREADY_ACTIVE")
        preference_value = (
            preferences
            if isinstance(preferences, CompanionProfilePreferencesV1)
            else CompanionProfilePreferencesV1.from_mapping(_mapping(preferences))
        )
        evidence = (
            self._validated_aftereffects(
                scope,
                aftereffect_ids=evidence_aftereffect_ids,
                require_after_profile_ms=None,
            )
            if evidence_aftereffect_ids is not None
            else self._recent_eligible_aftereffects(scope)
        )
        refs = tuple(f"relationship-aftereffect://{item.aftereffect.aftereffect_id}" for item in evidence)
        days = tuple(sorted({utc_day_from_ms(item.aftereffect.observed_at_ms) for item in evidence}))
        confidence_lower_bound = min(item.aftereffect.helpfulness for item in evidence)
        if len(evidence) < 5 or len(days) < 3 or confidence_lower_bound < 0.85:
            raise ValueError("COMPANION_PROFILE_EVIDENCE_THRESHOLD_NOT_MET")
        profile = CompanionProfileVersionV1.create(
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            version=max(item.version for item in current.known_profiles) + 1,
            preferences=preference_value,
            source_aftereffect_refs=refs,
            evidence_days_utc=days,
            confidence_lower_bound=confidence_lower_bound,
            boundary_violation_count=0,
            rollback_profile_id=current.active_profile.profile_id,
            created_at_ms=self._next_created_at(scope),
            source_control_ref=control_ref,
        )
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_canary_started",
            ordinal=0,
            payload={"profile": profile.as_dict()},
            created_at_ms=profile.created_at_ms,
        )
        return CompanionProfileLifecycleResult(
            scope=scope,
            receipt=receipt,
            state=self._read_state(scope),
            reason_code="COMPANION_PROFILE_CANARY_STARTED",
        )

    def record_eligible_canary_aftereffect(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        aftereffect_id: Any,
    ) -> CompanionProfileLifecycleResult:
        """Record one helpful, boundary-safe, post-canary aftereffect.

        No active canary is a normal no-op.  Any attempt to use an earlier,
        failed, boundary-violating, or scope-drifted aftereffect fails closed.
        """

        scope = self._scope(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        current = self._read_state(scope)
        candidate = current.canary_profile
        if candidate is None:
            return CompanionProfileLifecycleResult(
                scope=scope,
                receipt=None,
                state=current,
                reason_code="COMPANION_PROFILE_NO_ACTIVE_CANARY",
            )
        evidence = self._validated_aftereffects(
            scope,
            aftereffect_ids=(aftereffect_id,),
            require_after_profile_ms=candidate.created_at_ms,
        )
        item = evidence[0]
        interaction = CompanionProfileCanaryInteractionV1.create(
            profile_id=candidate.profile_id,
            aftereffect_id=item.aftereffect.aftereffect_id,
            aftereffect_ref=f"relationship-aftereffect://{item.aftereffect.aftereffect_id}",
            observed_at_ms=item.aftereffect.observed_at_ms,
        )
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-canary-observation.v1",
                "scope": scope.aggregate_id,
                "interaction_id": interaction.interaction_id,
            }
        )[:32]
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_canary_interaction_recorded",
            ordinal=0,
            payload={
                "profile": candidate.as_dict(),
                "canary_interaction": interaction.as_dict(),
            },
            # The relationship aftereffect has already committed at the same
            # logical clock tick.  Advance the lifecycle event by one logical
            # millisecond when necessary so cursor pagination preserves the
            # causal order without changing the observed user timestamp.
            created_at_ms=max(
                max(0, int(self._clock_ms())),
                interaction.observed_at_ms + 1,
            ),
        )
        state = self._read_state(scope)
        if len(state.canary_accepted_interaction_ids) < 5:
            return CompanionProfileLifecycleResult(
                scope=scope,
                receipt=receipt,
                state=state,
                reason_code="COMPANION_PROFILE_CANARY_INTERACTION_RECORDED",
            )
        if (
            state.canary_profile is None
            or state.canary_profile.profile_id != candidate.profile_id
        ):
            raise RuntimeError("COMPANION_PROFILE_CANARY_STATE_DRIFT")
        # Promotion is deliberately driven by the fifth canonical post-canary
        # aftereffect, not by a second public route or an in-memory counter.
        # The deterministic request identity means a crash between this
        # interaction and promotion can only reopen the same activation.
        activation_request_id = _digest(
            {
                "schema": "companion-profile-auto-activate.v1",
                "scope": scope.aggregate_id,
                "profile_id": candidate.profile_id,
                "canary_interaction_ids": list(
                    state.canary_accepted_interaction_ids
                ),
            }
        )[:32]
        return self._activate_completed_canary(
            scope=scope,
            request_id=activation_request_id,
        )

    def _activate_completed_canary(
        self,
        *,
        scope: RelationshipScope,
        request_id: Any,
    ) -> CompanionProfileLifecycleResult:
        """Atomically promote the exact completed candidate after canary evidence.

        This is intentionally internal.  The only production trigger is
        ``record_eligible_canary_aftereffect`` after its fifth independent,
        strong and boundary-safe observation.  No HTTP body, natural-language
        classifier or caller-selected profile ID may request activation.
        """

        current = self._read_state(scope)
        candidate = current.canary_profile
        if candidate is None:
            raise ValueError("COMPANION_PROFILE_CANARY_NOT_ACTIVE")
        if len(current.canary_accepted_interaction_ids) != 5:
            raise ValueError("COMPANION_PROFILE_CANARY_NOT_COMPLETE")
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-auto-activate.v1",
                "scope": scope.aggregate_id,
                "profile_id": candidate.profile_id,
                "canary_interaction_ids": list(
                    current.canary_accepted_interaction_ids
                ),
            }
        )[:32]
        now_ms = self._next_created_at(scope)
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_activated",
            ordinal=0,
            payload={"profile": candidate.as_dict()},
            created_at_ms=now_ms,
        )
        return CompanionProfileLifecycleResult(
            scope=scope,
            receipt=receipt,
            state=self._read_state(scope),
            reason_code=(
                "COMPANION_PROFILE_CANARY_AUTO_ACTIVATION_REPLAYED"
                if receipt.replayed
                else "COMPANION_PROFILE_CANARY_AUTO_ACTIVATED"
            ),
        )

    def rollback_active(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        expected_active_profile_id: Any = None,
        source_control_ref: Any = None,
    ) -> CompanionProfileLifecycleResult:
        """Restore the exact predecessor of one active profile.

        Public Host controls bind both an authenticated opaque control ref and
        the profile identity currently visible to that relationship.  The
        caller cannot nominate a rollback target; stale views and response-loss
        retries reopen or reject against the immutable journal instead.
        """

        scope = self._scope(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        current = self._read_state(scope)
        expected = _text(expected_active_profile_id, limit=240)
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "companion-profile-rollback.v1",
                "scope": scope.aggregate_id,
                # Internal callers which do not supply a request id must still
                # get a distinct control origin for each active profile.  A
                # signed Host control always supplies its request id and its
                # explicit expected identity.
                "expected_active_profile_id": expected
                or current.active_profile.profile_id,
            }
        )[:32]
        control_ref = self._source_control_ref(
            source_control_ref,
            scope=scope,
            request_id=stable_request_id,
        )
        existing = self._existing_rollback_control_receipt(
            scope=scope,
            source_control_ref=control_ref,
        )
        if existing is not None:
            return CompanionProfileLifecycleResult(
                scope=scope,
                receipt=existing.as_replayed(),
                state=self._read_state(scope),
                reason_code="COMPANION_PROFILE_ROLLBACK_REPLAYED",
            )
        if expected and expected != current.active_profile.profile_id:
            raise ValueError("COMPANION_PROFILE_ROLLBACK_ACTIVE_PROFILE_DRIFT")
        target_id = current.active_profile.rollback_profile_id
        if not target_id:
            raise ValueError("COMPANION_PROFILE_ROLLBACK_NOT_AVAILABLE")
        known = {profile.profile_id: profile for profile in current.known_profiles}
        target = known.get(target_id)
        if target is None:
            raise ValueError("COMPANION_PROFILE_ROLLBACK_TARGET_MISSING")

        expected_active_id = current.active_profile.profile_id

        def _assert_current_predecessor(snapshot: Any) -> None:
            """Revalidate inside the aggregate claim before a write commits.

            Two independently signed requests can observe the same active
            profile before either reaches ``submit_next``.  The outer read
            rejects ordinary stale requests, but the canonical UoW must repeat
            that identity/predecessor check after it owns the aggregate; a
            second rollback must never be admitted from an obsolete head.
            """

            at_commit = self._state_from_snapshot(scope, snapshot)
            if at_commit.active_profile.profile_id != expected_active_id:
                raise ValueError("COMPANION_PROFILE_ROLLBACK_ACTIVE_PROFILE_DRIFT")
            if at_commit.active_profile.rollback_profile_id != target.profile_id:
                raise ValueError("COMPANION_PROFILE_ROLLBACK_TARGET_DRIFT")

        now_ms = self._next_created_at(scope)
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="companion_profile_rolled_back",
            ordinal=0,
            payload={
                "profile": target.as_dict(),
                "rollback_profile_id": current.active_profile.profile_id,
                "source_control_ref": control_ref,
            },
            created_at_ms=now_ms,
            snapshot_validator=_assert_current_predecessor,
        )
        return CompanionProfileLifecycleResult(
            scope=scope,
            receipt=receipt,
            state=self._read_state(scope),
            reason_code="COMPANION_PROFILE_ROLLED_BACK",
        )

    def _scope(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
    ) -> RelationshipScope:
        """把入口字段收敛为关系 aggregate 的唯一作用域键。"""
        return RelationshipScope.from_values(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )

    def _read_state(self, scope: RelationshipScope) -> CompanionProfileStateV1:
        """从当前 Life 快照读取 profile；不维护跨请求的可变副本。"""
        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        return self._state_from_snapshot(scope, snapshot)

    @staticmethod
    def _state_from_snapshot(
        scope: RelationshipScope,
        snapshot: Any,
    ) -> CompanionProfileStateV1:
        """Decode the profile head from one already-claimed Life snapshot."""

        raw_relationship = _mapping(snapshot.state.get("relationship_v2"))
        if raw_relationship and (
            _text(raw_relationship.get("workspace_id")) not in {
                "",
                "workspace::unbound",
                scope.workspace_id,
            }
            or _text(raw_relationship.get("relationship_subject_id"))
            not in {"", scope.relationship_subject_id}
        ):
            raise RuntimeError("COMPANION_PROFILE_RELATIONSHIP_SCOPE_DRIFT")
        raw = snapshot.state.get("companion_profile")
        if isinstance(raw, Mapping):
            return CompanionProfileStateV1.from_mapping(raw)
        baseline = baseline_companion_profile(
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
        )
        return CompanionProfileStateV1(
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            revision=0,
            active_profile=baseline,
            known_profiles=(baseline,),
        )

    def _require_established_relationship(self, scope: RelationshipScope) -> None:
        """确认 profile 只能附着到已建立且同作用域的关系事实上。"""
        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        relationship = _mapping(snapshot.state.get("relationship_v2"))
        if (
            _text(relationship.get("workspace_id")) != scope.workspace_id
            or _text(relationship.get("relationship_subject_id"))
            != scope.relationship_subject_id
        ):
            raise ValueError("COMPANION_PROFILE_RELATIONSHIP_SCOPE_NOT_ESTABLISHED")

    def _next_created_at(self, scope: RelationshipScope) -> int:
        """Keep lifecycle cursor time strictly after its durable predecessor."""

        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        return max(
            max(0, int(self._clock_ms())),
            max(0, int(snapshot.updated_at_ms)) + 1,
        )

    def _source_control_ref(
        self,
        raw: Any,
        *,
        scope: RelationshipScope,
        request_id: str,
    ) -> str:
        """Bind every candidate to a replay-stable opaque control origin.

        Signed user controls pass their Secure Ingress audit ref.  Internal
        callers are not allowed to omit an origin either; their deterministic
        request ref stays opaque and makes an interrupted command reopenable.
        """

        if raw not in (None, ""):
            return _opaque_ref(raw, field_name="source_control_ref")
        return "companion-profile-request://" + _digest(
            {
                "scope": scope.aggregate_id,
                "request_id": request_id,
            }
        )

    def _existing_control_receipt(
        self,
        *,
        scope: RelationshipScope,
        source_control_ref: str,
    ) -> LifeCommandReceipt | None:
        """Reopen an already-applied candidate after ingress claim loss.

        Secure Ingress can crash after the Life command commits but before its
        claim gets a result reference.  Looking up the immutable profile
        source ref avoids emitting a second candidate when that same signed
        request is retried.
        """

        reader = self._require_snapshot_reader()
        for event in self._iter_scope_events(scope):
            if event.event_type != "companion_profile_canary_started":
                continue
            profile = _mapping(
                _mapping(event.payload).get("command_payload")
            ).get("profile")
            if not isinstance(profile, Mapping):
                continue
            if _text(profile.get("source_control_ref"), limit=512) != source_control_ref:
                continue
            receipt = reader.get_receipt(event.command_id)
            if receipt is None:
                raise RuntimeError("COMPANION_PROFILE_REPLAY_RECEIPT_MISSING")
            return receipt
        return None

    def _existing_rollback_control_receipt(
        self,
        *,
        scope: RelationshipScope,
        source_control_ref: str,
    ) -> LifeCommandReceipt | None:
        """Reopen one rollback that committed before its ingress claim result.

        Once rollback succeeds the active profile no longer exposes its former
        rollback target, so recomputing from the current head would incorrectly
        report ``NOT_AVAILABLE``.  The immutable Secure Ingress audit ref is
        the only replay key; it neither exposes user content nor lets a caller
        choose a historical profile.
        """

        reader = self._require_snapshot_reader()
        for event in self._iter_scope_events(scope):
            if event.event_type != "companion_profile_rolled_back":
                continue
            payload = _mapping(_mapping(event.payload).get("command_payload"))
            if (
                _text(payload.get("source_control_ref"), limit=512)
                != source_control_ref
            ):
                continue
            receipt = reader.get_receipt(event.command_id)
            if receipt is None:
                raise RuntimeError("COMPANION_PROFILE_REPLAY_RECEIPT_MISSING")
            return receipt
        return None

    def _recent_eligible_aftereffects(
        self,
        scope: RelationshipScope,
    ) -> tuple["_AftereffectEvent", ...]:
        """Choose a bounded, deterministic history for a direct control.

        The external request cannot nominate event IDs.  We only take the
        most recent distinct-user-input aftereffects which already satisfy the
        canonical positive/boundary gate; the caller later verifies the
        five-record and three-day threshold.
        """

        eligible = [
            item
            for item in self._all_aftereffects(scope)
            if item.aftereffect.outcome == "HELPFUL"
            and item.aftereffect.boundary_respected
            and item.aftereffect.helpfulness >= 0.85
        ]
        selected_reverse: list[_AftereffectEvent] = []
        seen_user_refs: set[str] = set()
        for item in reversed(eligible):
            user_ref = item.aftereffect.user_input_ref
            if user_ref in seen_user_refs:
                continue
            seen_user_refs.add(user_ref)
            selected_reverse.append(item)
            if len(selected_reverse) >= 16:
                break
        return tuple(reversed(selected_reverse))

    def _validated_aftereffects(
        self,
        scope: RelationshipScope,
        *,
        aftereffect_ids: Sequence[Any],
        require_after_profile_ms: int | None,
    ) -> tuple["_AftereffectEvent", ...]:
        """重开请求指定的账本后效，并逐项执行证据资格门。

        这里同时拒绝伪造 ID、重复用户输入、负向/越界结果及早于 canary 的
        观察；不能仅因调用者声称“有帮助”就获得 profile 演进权限。
        """
        if isinstance(aftereffect_ids, (str, bytes, bytearray)) or not isinstance(
            aftereffect_ids, Sequence
        ):
            raise ValueError("evidence_aftereffect_ids must be a sequence")
        requested = tuple(_text(item) for item in aftereffect_ids)
        if not requested or any(not item for item in requested):
            raise ValueError("evidence_aftereffect_ids are required")
        if len(set(requested)) != len(requested):
            raise ValueError("evidence_aftereffect_ids must not contain duplicates")
        by_id = {
            item.aftereffect.aftereffect_id: item
            for item in self._all_aftereffects(scope)
            if item.aftereffect.aftereffect_id in requested
        }
        missing = [item for item in requested if item not in by_id]
        if missing:
            raise ValueError("COMPANION_PROFILE_AFTEREFFECT_NOT_CANONICAL")
        selected = tuple(by_id[item] for item in requested)
        if any(
            item.aftereffect.outcome != "HELPFUL"
            or not item.aftereffect.boundary_respected
            or item.aftereffect.helpfulness < 0.85
            for item in selected
        ):
            raise ValueError("COMPANION_PROFILE_AFTEREFFECT_NOT_ELIGIBLE")
        user_refs = tuple(item.aftereffect.user_input_ref for item in selected)
        if len(set(user_refs)) != len(user_refs):
            raise ValueError("COMPANION_PROFILE_EVIDENCE_NOT_INDEPENDENT")
        if require_after_profile_ms is not None and any(
            item.aftereffect.observed_at_ms < require_after_profile_ms
            for item in selected
        ):
            raise ValueError("COMPANION_PROFILE_AFTEREFFECT_PRECEDES_CANARY")
        return selected

    def _all_aftereffects(
        self,
        scope: RelationshipScope,
    ) -> tuple["_AftereffectEvent", ...]:
        """从该关系 aggregate 的不可变事件流投影后效，发现作用域漂移即失败。"""
        rows: list[_AftereffectEvent] = []
        for event in self._iter_scope_events(scope):
            if event.event_type != "relational_aftereffect_recorded":
                continue
            payload = _mapping(event.payload)
            aftereffect = _aftereffect_from_mapping(
                _mapping(_mapping(payload.get("command_payload")).get("aftereffect"))
            )
            if (
                aftereffect.workspace_id != scope.workspace_id
                or aftereffect.relationship_subject_id != scope.relationship_subject_id
            ):
                raise RuntimeError("COMPANION_PROFILE_AFTEREFFECT_SCOPE_DRIFT")
            rows.append(
                _AftereffectEvent(
                    aftereffect=aftereffect,
                    event_sequence=event.sequence,
                    event_created_at_ms=event.created_at_ms,
                )
            )
        return tuple(rows)

    def _iter_scope_events(self, scope: RelationshipScope):
        """Read the complete immutable aggregate through bounded pages.

        A direct preference request can arrive after more than one thousand
        relationship events.  The evidence threshold must not silently turn
        into a first-page threshold, and interrupted Secure Ingress claims
        must still find their earlier canary command.  Pagination remains
        read-only and detects a cursor regression rather than looping or
        accepting a reordered journal.
        """

        reader = self._require_snapshot_reader()
        after_sequence = 0
        while True:
            batch = reader.list_events(
                scope.aggregate_id,
                after_sequence=after_sequence,
                limit=500,
            )
            if not batch:
                return
            for event in batch:
                sequence = int(event.sequence)
                if sequence <= after_sequence:
                    raise RuntimeError("COMPANION_PROFILE_EVENT_CURSOR_REGRESSION")
                after_sequence = sequence
                yield event
            if len(batch) < 500:
                return

    def _require_command_service(self) -> LifeCommandService:
        """延迟取得写入端口，并拒绝被错误依赖替换的运行时组合。"""
        service = self._command_service_provider()
        if not isinstance(service, LifeCommandService):
            raise RuntimeError("COMPANION_PROFILE_LIFE_COMMAND_SERVICE_NOT_INJECTED")
        return service

    def _require_snapshot_reader(self) -> LifeSnapshotReader:
        """延迟取得只读端口；读取与写入均必须落在同一 Life 模型。"""
        reader = self._snapshot_reader_provider()
        if not isinstance(reader, LifeSnapshotReader):
            raise RuntimeError("COMPANION_PROFILE_SNAPSHOT_READER_NOT_INJECTED")
        return reader

    def _submit(
        self,
        *,
        scope: RelationshipScope,
        request_id: str,
        command_type: str,
        ordinal: int,
        payload: Mapping[str, Any],
        created_at_ms: int,
        snapshot_validator: Callable[[Any], None] | None = None,
    ) -> LifeCommandReceipt:
        """以确定性命令 ID 提交一次 CAS 写入。

        ``request_id``、命令类型、序号和负载共同派生命令 ID，使网络重试及
        “已提交但尚未返回”的恢复重新命中同一账本记录。``snapshot_validator``
        在 UoW 取得 aggregate 后复验并发前提，补足入口读快照与提交之间的
        竞态窗口。
        """
        command_seed = {
            "schema": "companion-profile-life-command.v1",
            "aggregate_id": scope.aggregate_id,
            "request_id": request_id,
            "command_type": command_type,
            "ordinal": ordinal,
            "payload": dict(payload),
        }
        command_id = f"companion_profile_{_digest(command_seed)[:40]}"

        def build(snapshot: Any) -> LifeCommand:
            if snapshot_validator is not None:
                snapshot_validator(snapshot)
            return LifeCommand(
                command_id=command_id,
                person_id=scope.aggregate_id,
                command_type=command_type,
                payload=dict(payload),
                expected_version=int(snapshot.version),
                created_at_ms=max(0, int(created_at_ms)),
                correlation_id=request_id,
                metadata={
                    "source": "companion_profile_lifecycle",
                    "workspace_id": scope.workspace_id,
                    "relationship_subject_id": scope.relationship_subject_id,
                    "session_id": scope.session_id,
                },
            )

        return self._require_command_service().submit_next(scope.aggregate_id, build)


@dataclass(frozen=True, slots=True)
class _AftereffectEvent:
    aftereffect: RelationalAftereffectV1
    event_sequence: int
    event_created_at_ms: int


__all__ = [
    "CompanionProfileLifecycleResult",
    "CompanionProfileLifecycleService",
]
