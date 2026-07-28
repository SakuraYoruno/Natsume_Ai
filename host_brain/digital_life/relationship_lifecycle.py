"""Canonical, replay-safe relationship episode and aftereffect lifecycle.

The service has no mutable relationship cache and never persists a message or
reply body.  It only builds deterministic Life commands over a workspace and
relationship-subject scoped aggregate.  This makes a later user response an
evidence-bearing aftereffect rather than another increment to a Bond score.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from natsume_contracts.life import (
    CompanionProactiveTargetV1,
    LifeCommand,
    LifeCommandReceipt,
    RelationshipEpisodeV1,
    RelationalAftereffectV1,
)


_BOUNDARY_MARKERS = (
    "别再",
    "不要再",
    "给我空间",
    "别问了",
    "别烦",
    "不舒服",
    "越界",
    "停止",
)
_MISUNDERSTANDING_MARKERS = (
    "你不懂",
    "不是这样",
    "你误会了",
    "没有理解",
    "别曲解",
)
_REPAIR_ACCEPTANCE_MARKERS = (
    "没关系",
    "我接受",
    "愿意继续",
    "可以继续",
    "谢谢你愿意听",
    "好一些了",
    "我理解了",
)
_HELPFUL_MARKERS = ("谢谢", "有帮助", "好多了", "舒服些", "被理解")


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    resolved = str(value or "").strip()
    return (resolved[:limit] if resolved else default)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _opaque_ref(prefix: str, value: Any) -> str:
    return f"{prefix}://sha256/{_digest(value)}"


@dataclass(frozen=True, slots=True)
class RelationshipScope:
    """Separate workspace, subject and session without leaking them into a key."""

    workspace_id: str
    relationship_subject_id: str
    session_id: str
    aggregate_id: str

    @classmethod
    def from_request(cls, request_body: Any) -> "RelationshipScope":
        context = _mapping(getattr(request_body, "emotion_context", None))
        workspace_id = (
            _text(getattr(request_body, "workspace_id", None))
            or _text(context.get("workspace_id"))
            or "workspace::default"
        )
        subject = _text(getattr(request_body, "person_id", None), default="default")
        session_id = _text(
            getattr(request_body, "session_id", None),
            default="session::unknown",
        )
        return cls.from_values(
            workspace_id=workspace_id,
            relationship_subject_id=subject,
            session_id=session_id,
        )

    @classmethod
    def from_values(
        cls,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
    ) -> "RelationshipScope":
        workspace = _text(workspace_id, default="workspace::default")
        subject = _text(relationship_subject_id, default="default")
        session = _text(session_id, default="session::unknown")
        aggregate_seed = {
            "schema": "relationship-life-scope.v1",
            "workspace_id": workspace,
            "relationship_subject_id": subject,
        }
        return cls(
            workspace_id=workspace,
            relationship_subject_id=subject,
            session_id=session,
            aggregate_id=f"relationship::{_digest(aggregate_seed)[:40]}",
        )


@dataclass(frozen=True, slots=True)
class RelationshipAftereffectResult:
    """Result of relating the current user turn to a previous response."""

    scope: RelationshipScope
    aftereffect: RelationalAftereffectV1 | None
    receipt: LifeCommandReceipt | None
    reason_code: str

    @property
    def recorded(self) -> bool:
        return self.aftereffect is not None and self.receipt is not None


class RelationshipLifecycleService:
    """Submit durable relationship commands through the existing Life UoW."""

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

    def record_proactive_permission(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        proactive_permission: Any,
        source_ref: Any,
    ) -> LifeCommandReceipt:
        """Persist a user-explicit proactive-contact decision.

        The caller must supply an opaque evidence reference from a structured
        consent interaction.  This service intentionally has no natural
        language parser: warmth, Bond and inferred affection cannot become
        contact permission.
        """

        scope = RelationshipScope.from_values(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        permission = _text(proactive_permission, limit=64).upper()
        if permission not in {"ALLOWED", "DENIED"}:
            raise ValueError("proactive permission must be ALLOWED or DENIED")
        evidence_ref = _text(source_ref, limit=512)
        if not evidence_ref or "://" not in evidence_ref:
            raise ValueError("proactive permission requires an opaque source_ref")
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "relationship-proactive-permission.v1",
                "scope": scope.aggregate_id,
                "permission": permission,
                "source_ref": evidence_ref,
            }
        )[:32]
        created_at_ms = max(0, int(self._clock_ms()))
        return self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="relationship_proactive_permission_changed",
            ordinal=0,
            payload={
                "workspace_id": scope.workspace_id,
                "relationship_subject_id": scope.relationship_subject_id,
                "proactive_permission": permission,
                "source_kind": "USER_EXPLICIT",
                "source_ref": evidence_ref,
            },
            created_at_ms=created_at_ms,
        )

    def record_followup(
        self,
        *,
        request_body: Any,
        request_id: str | None,
    ) -> RelationshipAftereffectResult:
        """Bind the next user turn to the latest still-provisional response.

        No response episode means there is nothing to infer.  The user text is
        used transiently for a conservative classification, then reduced to an
        opaque digest reference before entering the journal.
        """

        scope = RelationshipScope.from_request(request_body)
        stable_request_id = _text(request_id) or _digest(
            {
                "scope": scope.aggregate_id,
                "session_id": scope.session_id,
                "message": _text(getattr(request_body, "message", None), limit=8_000),
            }
        )[:32]
        message = _text(getattr(request_body, "message", None), limit=8_000)
        user_input_ref = _opaque_ref(
            "relationship-user-input",
            {
                "scope": scope.aggregate_id,
                "request_id": stable_request_id,
                "message": message,
            },
        )
        reader = self._require_snapshot_reader()
        snapshot = reader.get_snapshot(scope.aggregate_id)
        relation = _mapping(_mapping(snapshot.state).get("relationship_v2"))
        raw_episodes = relation.get("active_episodes") or []
        episodes = [
            _mapping(item)
            for item in raw_episodes
            if isinstance(item, Mapping)
        ]
        if not episodes:
            prior = _mapping(relation.get("last_aftereffect"))
            if prior.get("user_input_ref") == user_input_ref:
                aftereffect = RelationalAftereffectV1(
                    aftereffect_id=prior.get("aftereffect_id"),
                    episode_id=prior.get("episode_id"),
                    workspace_id=prior.get("workspace_id"),
                    relationship_subject_id=prior.get("relationship_subject_id"),
                    session_id=prior.get("session_id"),
                    source_receipt_ref=prior.get("source_receipt_ref"),
                    user_input_ref=prior.get("user_input_ref"),
                    outcome=prior.get("outcome"),
                    helpfulness=prior.get("helpfulness"),
                    boundary_respected=prior.get("boundary_respected"),
                    repair_acknowledged=prior.get("repair_acknowledged"),
                    observed_at_ms=prior.get("observed_at_ms"),
                    evidence_refs=prior.get("evidence_refs") or (),
                )
                receipt = self._read_command_receipt(
                    self._command_id(
                        scope=scope,
                        request_id=stable_request_id,
                        command_type="relational_aftereffect_recorded",
                        ordinal=0,
                        payload={"aftereffect": aftereffect.as_dict()},
                    )
                )
                return RelationshipAftereffectResult(
                    scope=scope,
                    aftereffect=aftereffect,
                    receipt=receipt,
                    reason_code="RELATIONAL_AFTEREFFECT_REPLAYED",
                )
            return RelationshipAftereffectResult(
                scope=scope,
                aftereffect=None,
                receipt=None,
                reason_code="NO_PROVISIONAL_RELATIONSHIP_EPISODE",
            )
        episode = max(
            episodes,
            key=lambda item: (
                int(item.get("opened_at_ms") or 0),
                str(item.get("episode_id") or ""),
            ),
        )
        if _text(episode.get("workspace_id")) != scope.workspace_id or _text(
            episode.get("relationship_subject_id")
        ) != scope.relationship_subject_id:
            raise RuntimeError("RELATIONSHIP_EPISODE_SCOPE_DRIFT")
        outcome, helpfulness, boundary_respected, repair_acknowledged = (
            self._classify_followup(
                message=message,
                relationship_state=relation,
            )
        )
        now_ms = max(0, int(self._clock_ms()))
        identity = {
            "schema": "relational-aftereffect.v1",
            "episode_id": _text(episode.get("episode_id")),
            "request_id": stable_request_id,
            "user_input_ref": user_input_ref,
        }
        aftereffect = RelationalAftereffectV1(
            aftereffect_id=f"aftereffect_{_digest(identity)[:32]}",
            episode_id=_text(episode.get("episode_id")),
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            source_receipt_ref=_text(episode.get("response_receipt_ref")),
            user_input_ref=user_input_ref,
            outcome=outcome,
            helpfulness=helpfulness,
            boundary_respected=boundary_respected,
            repair_acknowledged=repair_acknowledged,
            observed_at_ms=now_ms,
            evidence_refs=(user_input_ref,),
        )
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="relational_aftereffect_recorded",
            ordinal=0,
            payload={"aftereffect": aftereffect.as_dict()},
            created_at_ms=now_ms,
        )
        return RelationshipAftereffectResult(
            scope=scope,
            aftereffect=aftereffect,
            receipt=receipt,
            reason_code="RELATIONAL_AFTEREFFECT_RECORDED",
        )

    def provision_accepted_response(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        response_receipt: Mapping[str, Any],
        response_text: str,
        proactive_target: Mapping[str, Any] | None = None,
        source_kind: str = "CHAT_RESPONSE",
    ) -> LifeCommandReceipt:
        """Open one provisional episode after a reply has canonical receipt."""

        (
            scope,
            stable_request_id,
            episode,
            companion_target,
            opened_at_ms,
        ) = self._accepted_response_episode(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
            request_id=request_id,
            response_receipt=response_receipt,
            response_text=response_text,
            proactive_target=proactive_target,
            source_kind=source_kind,
        )
        payload = self._accepted_response_payload(
            episode=episode,
            companion_target=companion_target,
        )
        legacy = self._legacy_accepted_response_receipt(
            scope=scope,
            request_id=stable_request_id,
            episode=episode,
            companion_target=companion_target,
        )
        if legacy is not None:
            return legacy.as_replayed()
        return self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="relationship_response_accepted",
            ordinal=0,
            payload=payload,
            created_at_ms=opened_at_ms,
        )

    def reconcile_accepted_response(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        response_receipt: Mapping[str, Any],
        response_text: str,
        proactive_target: Mapping[str, Any] | None = None,
        source_kind: str = "CHAT_RESPONSE",
    ) -> LifeCommandReceipt:
        """Reopen exactly one already-provisioned episode without writing.

        Acceptance-effect recovery may run after the Life foreground command
        committed but before its own effect receipt was persisted.  It must
        prove the episode exists from the exact deterministic command id,
        rather than assume a best-effort side effect succeeded.
        """

        scope, stable_request_id, episode, companion_target, _opened_at_ms = (
            self._accepted_response_episode(
                workspace_id=workspace_id,
                relationship_subject_id=relationship_subject_id,
                session_id=session_id,
                request_id=request_id,
                response_receipt=response_receipt,
                response_text=response_text,
                proactive_target=proactive_target,
                source_kind=source_kind,
            )
        )
        payload = self._accepted_response_payload(
            episode=episode,
            companion_target=companion_target,
        )
        try:
            return self._read_command_receipt(
                self._command_id(
                    scope=scope,
                    request_id=stable_request_id,
                    command_type="relationship_response_accepted",
                    ordinal=0,
                    payload=payload,
                )
            )
        except LookupError:
            legacy = self._legacy_accepted_response_receipt(
                scope=scope,
                request_id=stable_request_id,
                episode=episode,
                companion_target=companion_target,
            )
            if legacy is None:
                raise
            return legacy.as_replayed()

    def _classify_followup(
        self,
        *,
        message: str,
        relationship_state: Mapping[str, Any],
    ) -> tuple[str, float, bool, bool]:
        normalized = message.lower()
        if any(marker in normalized for marker in _BOUNDARY_MARKERS):
            return "BOUNDARY", 0.0, False, False
        if any(marker in normalized for marker in _MISUNDERSTANDING_MARKERS):
            return "MISUNDERSTOOD", 0.0, False, False
        repair_required = bool(relationship_state.get("repair_required"))
        if repair_required and any(
            marker in normalized for marker in _REPAIR_ACCEPTANCE_MARKERS
        ):
            return "REPAIR_ACCEPTED", 0.72, True, True
        if repair_required and "对不起" in normalized:
            # An apology is a signal to keep working, never an automatic cure.
            return "REPAIR_UNRESOLVED", 0.0, False, False
        if any(marker in normalized for marker in _HELPFUL_MARKERS):
            return "HELPFUL", 0.72, True, False
        return "NEUTRAL", 0.0, True, False

    def _require_command_service(self) -> LifeCommandService:
        service = self._command_service_provider()
        if not isinstance(service, LifeCommandService):
            raise RuntimeError("RELATIONSHIP_LIFE_COMMAND_SERVICE_NOT_INJECTED")
        return service

    def _require_snapshot_reader(self) -> LifeSnapshotReader:
        reader = self._snapshot_reader_provider()
        if not isinstance(reader, LifeSnapshotReader):
            raise RuntimeError("RELATIONSHIP_LIFE_SNAPSHOT_READER_NOT_INJECTED")
        return reader

    def _read_command_receipt(self, command_id: str) -> LifeCommandReceipt:
        receipt = self._require_snapshot_reader().get_receipt(command_id)
        if receipt is None:
            raise LookupError("exact relationship Life command receipt is absent")
        if (
            receipt.command_id != command_id
            or str(receipt.status.value).upper() != "APPLIED"
            or not receipt.event_id
        ):
            raise RuntimeError("RELATIONSHIP_LIFE_RECEIPT_INVALID")
        return receipt

    def _accepted_response_episode(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        response_receipt: Mapping[str, Any],
        response_text: str,
        proactive_target: Mapping[str, Any] | None,
        source_kind: str,
    ) -> tuple[
        RelationshipScope,
        str,
        RelationshipEpisodeV1,
        CompanionProactiveTargetV1 | None,
        int,
    ]:
        scope = RelationshipScope.from_values(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
        )
        receipt = _mapping(response_receipt)
        status = _text(receipt.get("status")).upper()
        event_id = _text(receipt.get("event_id"))
        command_id = _text(receipt.get("command_id"))
        if status != "APPLIED" or not event_id or not command_id:
            raise ValueError("accepted response requires an applied Life receipt")
        stable_request_id = _text(request_id) or command_id
        response_receipt_ref = f"life-event://{event_id}"
        response_digest = f"sha256:{_digest({'response_text': str(response_text or '')})}"
        normalized_source = _text(source_kind).upper() or "CHAT_RESPONSE"
        identity = {
            "schema": "relationship-episode.v1",
            "scope": scope.aggregate_id,
            "response_receipt_ref": response_receipt_ref,
            "request_id": stable_request_id,
            "source_kind": normalized_source,
        }
        opened_at_ms = max(0, int(receipt.get("created_at_ms") or self._clock_ms()))
        episode = RelationshipEpisodeV1(
            episode_id=f"relationship_episode_{_digest(identity)[:32]}",
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            source_kind=normalized_source,
            response_receipt_ref=response_receipt_ref,
            response_digest=response_digest,
            opened_at_ms=opened_at_ms,
            evidence_refs=(response_receipt_ref,),
        )
        companion_target = self._accepted_response_target(
            scope=scope,
            response_receipt_ref=response_receipt_ref,
            opened_at_ms=opened_at_ms,
            target=proactive_target,
        )
        return scope, stable_request_id, episode, companion_target, opened_at_ms

    @staticmethod
    def _accepted_response_payload(
        *,
        episode: RelationshipEpisodeV1,
        companion_target: CompanionProactiveTargetV1 | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"episode": episode.as_dict()}
        if companion_target is not None:
            payload["companion_proactive_target"] = companion_target.as_dict()
        return payload

    def _legacy_accepted_response_receipt(
        self,
        *,
        scope: RelationshipScope,
        request_id: str,
        episode: RelationshipEpisodeV1,
        companion_target: CompanionProactiveTargetV1 | None,
    ) -> LifeCommandReceipt | None:
        """Reopen a pre-registry episode without adding a second episode.

        Older accepted-reply commands did not carry a target.  A route retry
        after this cutover may now have target metadata available, but must
        not create a second command with the same episode identity merely to
        backfill it.  The next accepted response can register a new target.
        """

        if companion_target is None:
            return None
        legacy_payload = self._accepted_response_payload(
            episode=episode,
            companion_target=None,
        )
        legacy_command_id = self._command_id(
            scope=scope,
            request_id=request_id,
            command_type="relationship_response_accepted",
            ordinal=0,
            payload=legacy_payload,
        )
        try:
            return self._read_command_receipt(legacy_command_id)
        except LookupError:
            return None

    @staticmethod
    def _accepted_response_target(
        *,
        scope: RelationshipScope,
        response_receipt_ref: str,
        opened_at_ms: int,
        target: Mapping[str, Any] | None,
    ) -> CompanionProactiveTargetV1 | None:
        raw = _mapping(target)
        if not raw:
            return None
        platform = _text(raw.get("platform"), limit=80).lower()
        conversation_id = _text(raw.get("conversation_id"), limit=512)
        if not platform and not conversation_id:
            return None
        if not platform or not conversation_id:
            raise ValueError("companion proactive target requires platform and conversation_id")
        identity = {
            "schema": "companion-proactive-target.v1",
            "aggregate_id": scope.aggregate_id,
            "workspace_id": scope.workspace_id,
            "relationship_subject_id": scope.relationship_subject_id,
            "platform": platform,
            "conversation_id": conversation_id,
        }
        return CompanionProactiveTargetV1(
            target_id=f"companion_target_{_digest(identity)[:32]}",
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            platform=platform,
            conversation_id=conversation_id,
            source_receipt_ref=response_receipt_ref,
            registered_at_ms=opened_at_ms,
        )

    @staticmethod
    def _command_id(
        *,
        scope: RelationshipScope,
        request_id: str,
        command_type: str,
        ordinal: int,
        payload: Mapping[str, Any],
    ) -> str:
        command_seed = {
            "schema": "relationship-lifecycle-command.v1",
            "aggregate_id": scope.aggregate_id,
            "request_id": request_id,
            "command_type": command_type,
            "ordinal": ordinal,
            "payload": payload,
        }
        return f"relationship_{_digest(command_seed)[:40]}"

    def _submit(
        self,
        *,
        scope: RelationshipScope,
        request_id: str,
        command_type: str,
        ordinal: int,
        payload: Mapping[str, Any],
        created_at_ms: int,
    ) -> LifeCommandReceipt:
        command_id = self._command_id(
            scope=scope,
            request_id=request_id,
            command_type=command_type,
            ordinal=ordinal,
            payload=payload,
        )
        service = self._require_command_service()

        def build(snapshot: Any) -> LifeCommand:
            return LifeCommand(
                command_id=command_id,
                person_id=scope.aggregate_id,
                command_type=command_type,
                payload=dict(payload),
                expected_version=int(snapshot.version),
                created_at_ms=max(0, int(created_at_ms)),
                correlation_id=request_id,
                metadata={
                    "source": "relationship_lifecycle_service",
                    "workspace_id": scope.workspace_id,
                    "relationship_subject_id": scope.relationship_subject_id,
                    "session_id": scope.session_id,
                },
            )

        return service.submit_next(scope.aggregate_id, build)


__all__ = [
    "RelationshipAftereffectResult",
    "RelationshipLifecycleService",
    "RelationshipScope",
]
