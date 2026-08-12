"""Canonical, replay-safe relationship episode and aftereffect lifecycle.

The service has no mutable relationship cache and never persists a message or
reply body.  It only builds deterministic Life commands over a workspace and
relationship-subject scoped aggregate.  This makes a later user response an
evidence-bearing aftereffect rather than another increment to a Bond score.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import time
from typing import Any

from host_brain.digital_life.relationship_lifecycle_support import (
    _aftereffect_from_payload,
    _companion_target_id,
    _delivery_result_is_success,
    _digest,
    _mapping,
    _opaque_ref,
    _relationship_episode_from_payload,
    _text,
    _validate_delivery_receipt,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from natsume_contracts.life import (
    AffectiveRepairTargetV1,
    CompanionProactiveTargetV1,
    CompanionResponseFeedbackRequestV1,
    LifeCommand,
    LifeCommandReceipt,
    LifeDeliveryReceipt,
    LifeOutboxMessage,
    RelationshipEpisodeV1,
    RelationalAftereffectV1,
)


_BOUNDARY_MARKERS = (
    "别再",
    "不要再",
    "给我空间",
    "别问了",
    "别烦",
    "别来烦我",
    "少管我",
    "闭嘴",
    "滚开",
    "越界",
    "停止",
)
_MISUNDERSTANDING_MARKERS = (
    "你真烦",
    "你好烦",
    "你烦死了",
    "没听懂",
    "没有听懂",
    "没理解",
    "让我很不舒服",
    "你不懂",
    "不是这样",
    "你误会了",
    "没有理解",
    "别曲解",
    "弄反了",
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
_REPAIR_UNRESOLVED_MARKERS = (
    "还没原谅",
    "没有原谅",
    "没觉得这件事过去",
    "没有觉得这件事过去",
    "这件事还没过去",
    "这件事没有过去",
    "道歉不等于",
    "还没有翻篇",
    "还没翻篇",
)
_HELPFUL_MARKERS = ("谢谢", "有帮助", "好多了", "舒服些", "被理解")


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
        # 聚合身份按工作区与关系对象划分，session 仅作事件来源；这样跨会话的
        # 同一关系连续演化，同时不会把会话 ID 泄露或误当成新的关系主体。
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
    affect_resolution_receipt: LifeCommandReceipt | None = None
    affect_resolution_status: str = "NOT_REQUESTED"

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
        companion_profile_lifecycle_service_provider: Callable[[], Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._command_service_provider = command_service_provider
        self._snapshot_reader_provider = snapshot_reader_provider
        self._companion_profile_lifecycle_service_provider = (
            companion_profile_lifecycle_service_provider
        )
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
        # 主动联系权限只能来自结构化、可追溯的用户明确选择；温暖语气、亲密度
        # 或模型推断都不能升级为可打扰用户的授权。
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
        # 缺失 request_id 时以权限决定与证据引用派生稳定身份，使重复提交保持
        # 幂等；同一关系的相反决定因载荷不同而成为明确的新生命周期事件。
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

    def record_explicit_response_feedback(
        self,
        *,
        workspace_id: Any,
        relationship_subject_id: Any,
        session_id: Any,
        request_id: Any,
        response_receipt_ref: Any,
        feedback: Any,
        source_ref: Any,
    ) -> RelationshipAftereffectResult:
        """Record an authenticated, bounded evaluation of one open response.

        This is intentionally different from keyword detection in a normal
        chat turn.  A signed user explicitly evaluates the exact accepted
        response receipt; the canonical aggregate then verifies that the
        corresponding relationship episode is still active.  The only strong
        positive mapping is an explicit ``HELPFUL`` selection, and it can
        still only advance an already-running profile canary through the
        existing lifecycle gate.
        """

        control = CompanionResponseFeedbackRequestV1(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
            response_receipt_ref=response_receipt_ref,
            feedback=feedback,
        )
        # 强反馈绑定到一个已接受回复的 receipt，而不是扫描普通聊天关键词；这可
        # 防止随口抱怨或引用文本被误提升为关系状态的持久裁决。
        scope = RelationshipScope.from_values(
            workspace_id=control.workspace_id,
            relationship_subject_id=control.relationship_subject_id,
            session_id=control.session_id,
        )
        evidence_ref = _text(source_ref, limit=512)
        if not evidence_ref or "://" not in evidence_ref:
            raise ValueError("explicit response feedback requires an opaque source_ref")
        stable_request_id = _text(request_id) or _digest(
            {
                "schema": "relationship-explicit-feedback.v1",
                "scope": scope.aggregate_id,
                "response_receipt_ref": control.response_receipt_ref,
                "feedback": control.feedback,
                "source_ref": evidence_ref,
            }
        )[:32]
        replayed = self._aftereffect_by_evidence_ref(
            scope=scope,
            evidence_ref=evidence_ref,
        )
        if replayed is not None:
            aftereffect, receipt = replayed
            self._observe_profile_canary_aftereffect(
                scope=scope,
                request_id=stable_request_id,
                aftereffect=aftereffect,
            )
            return RelationshipAftereffectResult(
                scope=scope,
                aftereffect=aftereffect,
                receipt=receipt.as_replayed(),
                reason_code="RELATIONAL_EXPLICIT_FEEDBACK_REPLAYED",
            )

        reader = self._require_snapshot_reader()
        snapshot = reader.get_snapshot(scope.aggregate_id)
        relation = _mapping(_mapping(snapshot.state).get("relationship_v2"))
        episodes = tuple(
            _mapping(item)
            for item in relation.get("active_episodes") or ()
            if isinstance(item, Mapping)
        )
        episode = next(
            (
                item
                for item in episodes
                if _text(item.get("response_receipt_ref"), limit=512)
                == control.response_receipt_ref
            ),
            None,
        )
        if episode is None:
            raise ValueError("RELATIONSHIP_RESPONSE_RECEIPT_NOT_ACTIVE")
        outcome, helpfulness, boundary_respected, repair_acknowledged = (
            self._explicit_feedback_outcome(control.feedback)
        )
        now_ms = max(0, int(self._clock_ms()))
        user_input_ref = _opaque_ref(
            "relationship-user-feedback",
            {
                "scope": scope.aggregate_id,
                "request_id": stable_request_id,
                "source_ref": evidence_ref,
                "response_receipt_ref": control.response_receipt_ref,
                "feedback": control.feedback,
            },
        )
        identity = {
            "schema": "relational-explicit-feedback-aftereffect.v1",
            "episode_id": _text(episode.get("episode_id")),
            "source_ref": evidence_ref,
            "response_receipt_ref": control.response_receipt_ref,
        }
        aftereffect = RelationalAftereffectV1(
            aftereffect_id=f"aftereffect_{_digest(identity)[:32]}",
            episode_id=_text(episode.get("episode_id")),
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            source_receipt_ref=control.response_receipt_ref,
            user_input_ref=user_input_ref,
            outcome=outcome,
            helpfulness=helpfulness,
            boundary_respected=boundary_respected,
            repair_acknowledged=repair_acknowledged,
            observed_at_ms=now_ms,
            evidence_refs=(evidence_ref, control.response_receipt_ref),
        )
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="relational_aftereffect_recorded",
            ordinal=0,
            payload={"aftereffect": aftereffect.as_dict()},
            created_at_ms=now_ms,
        )
        self._observe_profile_canary_aftereffect(
            scope=scope,
            request_id=stable_request_id,
            aftereffect=aftereffect,
        )
        return RelationshipAftereffectResult(
            scope=scope,
            aftereffect=aftereffect,
            receipt=receipt,
            reason_code="RELATIONAL_EXPLICIT_FEEDBACK_RECORDED",
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
        # 正文只在本次保守分类中短暂使用；写入关系日志的是包含作用域与请求的
        # 不透明引用，避免长期关系状态保存可直接回放的用户私聊内容。
        # A relationship aftereffect is committed before the cross-aggregate
        # affect resolution.  Recover that deterministic second command before
        # accepting another turn, so a crash cannot leave a confirmed repair
        # permanently visible only on the relationship side.
        self._reconcile_last_affect_repair(scope=scope)
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
            # 找不到活跃 episode 时只承认相同输入引用的既有 aftereffect 为重放；
            # 任何普通新消息都不能凭关键词新建关系后效，避免无来源的亲密度变化。
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
                    affect_repair_target=(
                        None
                        if prior.get("affect_repair_target") is None
                        else AffectiveRepairTargetV1.from_mapping(
                            _mapping(prior.get("affect_repair_target"))
                        )
                    ),
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
                resolution_receipt, resolution_status = (
                    self._resolve_aftereffect_affect_repair(aftereffect=aftereffect)
                )
                return RelationshipAftereffectResult(
                    scope=scope,
                    aftereffect=aftereffect,
                    receipt=receipt,
                    reason_code="RELATIONAL_AFTEREFFECT_REPLAYED",
                    affect_resolution_receipt=resolution_receipt,
                    affect_resolution_status=resolution_status,
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
        # 最近 episode 仍须通过紧邻的 scope drift 检查；session 相同不构成跨主体
        # 复用关系事件的授权。
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
            affect_repair_target=(
                None
                if outcome != "REPAIR_ACCEPTED"
                or episode.get("affect_repair_target") is None
                else AffectiveRepairTargetV1.from_mapping(
                    _mapping(episode.get("affect_repair_target"))
                )
            ),
        )
        receipt = self._submit(
            scope=scope,
            request_id=stable_request_id,
            command_type="relational_aftereffect_recorded",
            ordinal=0,
            payload={"aftereffect": aftereffect.as_dict()},
            created_at_ms=now_ms,
        )
        self._observe_profile_canary_aftereffect(
            scope=scope,
            request_id=stable_request_id,
            aftereffect=aftereffect,
        )
        resolution_receipt, resolution_status = (
            self._resolve_aftereffect_affect_repair(aftereffect=aftereffect)
        )
        return RelationshipAftereffectResult(
            scope=scope,
            aftereffect=aftereffect,
            receipt=receipt,
            reason_code="RELATIONAL_AFTEREFFECT_RECORDED",
            affect_resolution_receipt=resolution_receipt,
            affect_resolution_status=resolution_status,
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
        affect_repair_target: Mapping[str, Any] | AffectiveRepairTargetV1 | None = None,
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
            affect_repair_target=affect_repair_target,
            source_kind=source_kind,
        )
        payload = self._accepted_response_payload(
            episode=episode,
            companion_target=companion_target,
        )
        # 只在回复已经拥有 canonical receipt 后开启暂定 episode；生成成功或文本
        # 看似合格均不足以证明用户真正收到，不能作为关系后效的来源事实。
        legacy = self._legacy_accepted_response_receipt(
            scope=scope,
            request_id=stable_request_id,
            episode=episode,
            companion_target=companion_target,
        )
        if legacy is not None:
            return legacy.as_replayed()
        existing = self._accepted_response_receipt_by_ref(
            scope=scope,
            response_receipt_ref=episode.response_receipt_ref,
            source_kind=episode.source_kind,
        )
        if existing is not None:
            return existing.as_replayed()
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
        affect_repair_target: Mapping[str, Any] | AffectiveRepairTargetV1 | None = None,
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
                affect_repair_target=affect_repair_target,
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
            if legacy is not None:
                return legacy.as_replayed()
            existing = self._accepted_response_receipt_by_ref(
                scope=scope,
                response_receipt_ref=episode.response_receipt_ref,
                source_kind=episode.source_kind,
            )
            if existing is not None:
                return existing.as_replayed()
            raise

    def provision_delivered_proactive(
        self,
        *,
        message: LifeOutboxMessage,
        delivery_receipt: LifeDeliveryReceipt,
    ) -> LifeCommandReceipt | None:
        """Open a proactive episode only after its durable delivery receipt.

        Creating a Life outbox intent does not mean a message reached the
        Gateway.  The immutable ``LifeDeliveryReceipt`` is therefore the
        source fact for a ``PROACTIVE_MESSAGE`` episode.  Reopening this
        method is exact-idempotent and never resends the external message.
        """

        if not isinstance(message, LifeOutboxMessage):
            raise TypeError("message must be a LifeOutboxMessage")
        if not isinstance(delivery_receipt, LifeDeliveryReceipt):
            raise TypeError("delivery_receipt must be a LifeDeliveryReceipt")
        context = _mapping(
            _mapping(message.payload).get("_companion_relationship_context")
        )
        if not context:
            # 没有受控关系上下文的历史/普通投递保持可读但不具备关系语义；不能因
            # 一条消息抵达 Gateway 就推断这是主动亲近或可等待用户回应。
            # Historical or non-companion deliveries have no relationship
            # semantics.  They remain readable but cannot invent an episode.
            return None
        if str(message.destination or "").strip().lower() != "proactive.gateway":
            raise ValueError("companion relationship context requires proactive.gateway")
        _validate_delivery_receipt(message=message, receipt=delivery_receipt)
        if not _delivery_result_is_success(delivery_receipt.result):
            raise ValueError("companion proactive delivery receipt is not successful")
        scope, target_id = self._companion_delivery_scope(
            message=message,
            context=context,
        )
        delivery_ref = f"life-delivery://{delivery_receipt.outbox_id}"
        result_ref = _opaque_ref(
            "life-delivery-result",
            {
                "outbox_id": delivery_receipt.outbox_id,
                "delivery_key": delivery_receipt.delivery_key,
                "payload_digest": delivery_receipt.payload_digest,
                "authorization_digest": delivery_receipt.authorization_digest,
                "result": dict(delivery_receipt.result),
            },
        )
        identity = {
            "schema": "relationship-proactive-delivery.v1",
            "scope": scope.aggregate_id,
            "target_id": target_id,
            "delivery_ref": delivery_ref,
            "delivery_key": delivery_receipt.delivery_key,
            "payload_digest": delivery_receipt.payload_digest,
        }
        episode = RelationshipEpisodeV1(
            episode_id=f"relationship_episode_{_digest(identity)[:32]}",
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            source_kind="PROACTIVE_MESSAGE",
            response_receipt_ref=delivery_ref,
            response_digest=(
                f"sha256:{_digest({'delivery': identity, 'result_ref': result_ref})}"
            ),
            opened_at_ms=delivery_receipt.completed_at_ms,
            evidence_refs=(delivery_ref, result_ref),
        )
        payload = {"episode": episode.as_dict()}
        request_id = f"proactive_delivery_{_digest(identity)[:32]}"
        return self._submit(
            scope=scope,
            request_id=request_id,
            command_type="relationship_proactive_delivered",
            ordinal=0,
            payload=payload,
            created_at_ms=delivery_receipt.completed_at_ms,
        )

    def reconcile_completed_proactive_deliveries(
        self,
        *,
        limit: int = 100,
    ) -> int:
        """Recover completed companion deliveries missing a relation event.

        The delivery receipt and original durable outbox row are the only
        inputs.  A crash after ``complete_delivery`` but before the relation
        command is committed is therefore recoverable on Host startup or the
        next Life I/O tick without a second Gateway submission.  The reader
        excludes delivery receipts that already have their immutable relation
        event, so a normal recurrent tick has no historical replay work.
        """

        # 恢复只扫描“投递已完成但关系事件缺失”的集合；它补齐内部生命周期，不会
        # 再次向外部 Gateway 发送内容，因而可安全用于启动和周期性恢复。
        reader = self._require_snapshot_reader()
        messages = reader.list_completed_companion_proactive_messages(
            limit=max(1, int(limit))
        )
        reconciled = 0
        for message in messages:
            receipt = reader.get_delivery_receipt(message.outbox_id)
            if receipt is None:
                raise RuntimeError(
                    "COMPANION_PROACTIVE_DELIVERY_RECEIPT_MISSING"
                )
            self.provision_delivered_proactive(
                message=message,
                delivery_receipt=receipt,
            )
            reconciled += 1
        return reconciled

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
        if any(marker in normalized for marker in _REPAIR_UNRESOLVED_MARKERS):
            # A user can explicitly say that an apology did not repair the
            # rupture even when this fresh session has no earlier canonical
            # rupture flag.  Treat that statement as the relationship fact;
            # do not wait for a later neutral turn to infer it.
            return "REPAIR_UNRESOLVED", 0.0, False, False
        if repair_required and any(
            marker in normalized for marker in _REPAIR_ACCEPTANCE_MARKERS
        ):
            return "REPAIR_ACCEPTED", 0.72, True, True
        if repair_required:
            # Only an explicit user acceptance closes a rupture.  Apologies,
            # neutral continuations and terse acknowledgements all preserve
            # the open repair thread instead of silently clearing it.
            return "REPAIR_UNRESOLVED", 0.0, False, False
        if any(marker in normalized for marker in _HELPFUL_MARKERS):
            return "HELPFUL", 0.72, True, False
        return "NEUTRAL", 0.0, True, False

    @staticmethod
    def _explicit_feedback_outcome(
        feedback: str,
    ) -> tuple[str, float, bool, bool]:
        """Map one bounded user selection without invoking text inference."""

        normalized = _text(feedback, limit=64).upper()
        if normalized == "HELPFUL":
            # A signed explicit selection is stronger than the conservative
            # textual classifier, but it remains scoped to exactly one
            # accepted response and one user-controlled request.
            return "HELPFUL", 0.95, True, False
        if normalized == "NEUTRAL":
            return "NEUTRAL", 0.0, True, False
        if normalized == "MISUNDERSTOOD":
            return "MISUNDERSTOOD", 0.0, False, False
        if normalized == "BOUNDARY":
            return "BOUNDARY", 0.0, False, False
        raise ValueError("explicit response feedback is invalid")

    def _aftereffect_by_evidence_ref(
        self,
        *,
        scope: RelationshipScope,
        evidence_ref: str,
    ) -> tuple[RelationalAftereffectV1, LifeCommandReceipt] | None:
        """Reopen an explicit feedback command after claim-result failure.

        The Secure Ingress claim can survive a process crash between the
        canonical Life command and claim completion.  The immutable audit ref
        is the replay key, so later relationship activity cannot hide the
        original result behind ``last_aftereffect``.
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
                return None
            for event in batch:
                sequence = int(event.sequence)
                if sequence <= after_sequence:
                    raise RuntimeError("RELATIONSHIP_EVENT_CURSOR_REGRESSION")
                after_sequence = sequence
                if event.event_type != "relational_aftereffect_recorded":
                    continue
                payload = _mapping(event.payload)
                aftereffect = _aftereffect_from_payload(
                    _mapping(payload.get("command_payload"))
                )
                if evidence_ref not in aftereffect.evidence_refs:
                    continue
                if (
                    aftereffect.workspace_id != scope.workspace_id
                    or aftereffect.relationship_subject_id
                    != scope.relationship_subject_id
                ):
                    raise RuntimeError("RELATIONSHIP_AFTEREFFECT_SCOPE_DRIFT")
                receipt = reader.get_receipt(event.command_id)
                if receipt is None:
                    raise RuntimeError("RELATIONSHIP_AFTEREFFECT_REPLAY_RECEIPT_MISSING")
                return aftereffect, receipt
            if len(batch) < 500:
                return None

    def _accepted_response_receipt_by_ref(
        self,
        *,
        scope: RelationshipScope,
        response_receipt_ref: str,
        source_kind: str,
    ) -> LifeCommandReceipt | None:
        """Find one prior accepted reply without recomputing a stale target.

        A repair can legitimately settle the original affect episode before a
        task-acceptance recovery runs.  In that state rebuilding the old plan
        would no longer find an *active* target, but the response receipt is
        still immutable evidence that the exact relationship command already
        exists.  Scan only this aggregate and fail closed if the source fact is
        ambiguous.
        """

        reader = self._require_snapshot_reader()
        after_sequence = 0
        matches: list[LifeCommandReceipt] = []
        while True:
            batch = reader.list_events(
                scope.aggregate_id,
                after_sequence=after_sequence,
                limit=500,
            )
            if not batch:
                break
            for event in batch:
                sequence = int(event.sequence)
                if sequence <= after_sequence:
                    raise RuntimeError("RELATIONSHIP_EVENT_CURSOR_REGRESSION")
                after_sequence = sequence
                if event.event_type != "relationship_response_accepted":
                    continue
                command_payload = _mapping(
                    _mapping(event.payload).get("command_payload")
                )
                episode = _relationship_episode_from_payload(
                    _mapping(command_payload.get("episode"))
                )
                if (
                    episode.response_receipt_ref != response_receipt_ref
                    or episode.source_kind != source_kind
                ):
                    continue
                receipt = reader.get_receipt(event.command_id)
                if receipt is None:
                    raise RuntimeError(
                        "RELATIONSHIP_RESPONSE_REPLAY_RECEIPT_MISSING"
                    )
                matches.append(receipt)
            if len(batch) < 500:
                break
        if len(matches) > 1:
            raise RuntimeError("RELATIONSHIP_RESPONSE_RECEIPT_NOT_UNIQUE")
        return matches[0] if matches else None

    def _reconcile_last_affect_repair(
        self,
        *,
        scope: RelationshipScope,
    ) -> tuple[LifeCommandReceipt | None, str]:
        """Finish the second half of an already accepted repair, if any.

        Relationship acceptance is intentionally durable before the linked
        affect aggregate is touched.  The exact aftereffect id below is also
        the idempotency seed for the second command, which lets a later user
        turn recover a crash between the two aggregates without recreating a
        relationship episode or replaying a visible reply.
        """

        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        relation = _mapping(_mapping(snapshot.state).get("relationship_v2"))
        prior = _mapping(relation.get("last_aftereffect"))
        if not prior or prior.get("affect_repair_target") is None:
            return None, "NOT_REQUESTED"
        aftereffect = _aftereffect_from_payload({"aftereffect": prior})
        return self._resolve_aftereffect_affect_repair(aftereffect=aftereffect)

    def _resolve_aftereffect_affect_repair(
        self,
        *,
        aftereffect: RelationalAftereffectV1,
    ) -> tuple[LifeCommandReceipt | None, str]:
        """Resolve one active self-affect episode after verified repair only."""

        target = aftereffect.affect_repair_target
        if target is None:
            return None, "NOT_REQUESTED"
        if (
            aftereffect.outcome != "REPAIR_ACCEPTED"
            or not aftereffect.repair_acknowledged
        ):
            raise RuntimeError("AFFECT_REPAIR_REQUIRES_ACKNOWLEDGED_REPAIR")
        command_id = self._affect_repair_command_id(
            aftereffect=aftereffect,
            target=target,
        )
        reader = self._require_snapshot_reader()
        existing = reader.get_receipt(command_id)
        if existing is not None:
            return existing.as_replayed(), "REPLAYED"
        snapshot = reader.get_snapshot(target.affect_aggregate_id)
        if not self._target_is_active_in_snapshot(target=target, snapshot=snapshot):
            # Natural decay or a prior independently reconciled closure is not
            # a licence to synthesize a second resolution receipt.
            return None, "TARGET_ALREADY_INACTIVE"
        resolution_ref = _opaque_ref(
            "relationship-aftereffect",
            {
                "aftereffect_id": aftereffect.aftereffect_id,
                "source_receipt_ref": aftereffect.source_receipt_ref,
                "target": target.as_dict(),
            },
        )
        service = self._require_command_service()

        def build(current: Any) -> LifeCommand:
            if not self._target_is_active_in_snapshot(target=target, snapshot=current):
                raise ValueError("AFFECT_REPAIR_TARGET_NOT_ACTIVE")
            return LifeCommand(
                command_id=command_id,
                person_id=target.affect_aggregate_id,
                command_type="affective_episode_resolved",
                payload={
                    "episode_id": target.affect_episode_id,
                    "resolution_ref": resolution_ref,
                },
                expected_version=int(current.version),
                created_at_ms=max(0, int(aftereffect.observed_at_ms)),
                correlation_id=aftereffect.aftereffect_id,
                metadata={
                    "source": "relationship_repair_bridge",
                    "workspace_id": target.workspace_id,
                    "relationship_subject_id": target.relationship_subject_id,
                    "session_id": target.session_id,
                    "aftereffect_id": aftereffect.aftereffect_id,
                },
            )

        try:
            return service.submit_next(target.affect_aggregate_id, build), "APPLIED"
        except ValueError as exc:
            if str(exc) != "AFFECT_REPAIR_TARGET_NOT_ACTIVE":
                raise
            existing = reader.get_receipt(command_id)
            if existing is not None:
                return existing.as_replayed(), "REPLAYED"
            return None, "TARGET_ALREADY_INACTIVE"

    def _active_affect_repair_target(
        self,
        *,
        scope: RelationshipScope,
        value: Mapping[str, Any] | AffectiveRepairTargetV1 | None,
        allow_pending: bool,
    ) -> AffectiveRepairTargetV1 | None:
        inherited = value is None
        if value is None:
            if not allow_pending:
                return None
            target = self._single_pending_affect_repair_target(scope=scope)
            if target is None:
                return None
        else:
            target = (
                value
                if isinstance(value, AffectiveRepairTargetV1)
                else AffectiveRepairTargetV1.from_mapping(_mapping(value))
            )
        if (
            target.workspace_id != scope.workspace_id
            or target.relationship_subject_id != scope.relationship_subject_id
        ):
            raise ValueError("AFFECT_REPAIR_TARGET_SCOPE_DRIFT")
        if not inherited and target.session_id != scope.session_id:
            raise ValueError("AFFECT_REPAIR_TARGET_SESSION_DRIFT")
        aggregate_seed = {
            "schema": "affective-life-scope.v1",
            "workspace_id": target.workspace_id,
            "relationship_subject_id": target.relationship_subject_id,
            "session_id": target.session_id,
        }
        expected_aggregate_id = f"affect::{_digest(aggregate_seed)[:40]}"
        if target.affect_aggregate_id != expected_aggregate_id:
            raise ValueError("AFFECT_REPAIR_TARGET_AGGREGATE_DRIFT")
        snapshot = self._require_snapshot_reader().get_snapshot(target.affect_aggregate_id)
        if not self._target_is_active_in_snapshot(target=target, snapshot=snapshot):
            return None
        return target

    def _single_pending_affect_repair_target(
        self,
        *,
        scope: RelationshipScope,
    ) -> AffectiveRepairTargetV1 | None:
        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        relation = _mapping(_mapping(snapshot.state).get("relationship_v2"))
        raw_targets = relation.get("pending_affect_repair_targets") or []
        if not isinstance(raw_targets, list):
            raise ValueError("PENDING_AFFECT_REPAIR_TARGETS_INVALID")
        targets = [
            AffectiveRepairTargetV1.from_mapping(_mapping(item))
            for item in raw_targets
            if isinstance(item, Mapping)
        ]
        if len(targets) != len(raw_targets):
            raise ValueError("PENDING_AFFECT_REPAIR_TARGETS_INVALID")
        # Multiple unsettled self-affect episodes require a deliberate future
        # arbitration; silently selecting one would make a later "we are
        # okay" claim settle the wrong feeling.
        return targets[0] if len(targets) == 1 else None

    @staticmethod
    def _target_is_active_in_snapshot(
        *,
        target: AffectiveRepairTargetV1,
        snapshot: Any,
    ) -> bool:
        state = _mapping(getattr(snapshot, "state", None))
        identity = _mapping(state.get("identity"))
        if (
            identity.get("self_id") != "natsume_ai"
            or _text(identity.get("relationship_subject_id"))
            != target.relationship_subject_id
        ):
            raise ValueError("AFFECT_REPAIR_TARGET_IDENTITY_DRIFT")
        affective = _mapping(state.get("affective"))
        if int(affective.get("revision") or 0) < target.self_affect_revision:
            raise ValueError("AFFECT_REPAIR_TARGET_REVISION_REGRESSION")
        return any(
            _text(_mapping(item).get("episode_id")) == target.affect_episode_id
            and _text(_mapping(item).get("relationship_subject_id"))
            == target.relationship_subject_id
            for item in list(affective.get("active_episodes") or [])
            if isinstance(item, Mapping)
        )

    @staticmethod
    def _affect_repair_command_id(
        *,
        aftereffect: RelationalAftereffectV1,
        target: AffectiveRepairTargetV1,
    ) -> str:
        return "affect_repair_" + _digest(
            {
                "schema": "relationship-affect-repair.v1",
                "aftereffect_id": aftereffect.aftereffect_id,
                "target": target.as_dict(),
            }
        )[:40]

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

    def _observe_profile_canary_aftereffect(
        self,
        *,
        scope: RelationshipScope,
        request_id: str,
        aftereffect: RelationalAftereffectV1,
    ) -> None:
        """Let a composed profile canary observe only strong safe evidence.

        Normal low-confidence text classification intentionally does not count
        as a preference-learning success.  The no-op path retains ordinary
        conversation availability; an injected profile lifecycle receives only
        the strong typed signal and owns its own durable idempotency.
        """

        provider = self._companion_profile_lifecycle_service_provider
        if provider is None:
            return
        if (
            aftereffect.outcome != "HELPFUL"
            or not aftereffect.boundary_respected
            or aftereffect.helpfulness < 0.85
        ):
            return
        service = provider()
        method = getattr(service, "record_eligible_canary_aftereffect", None)
        if not callable(method):
            raise RuntimeError("COMPANION_PROFILE_LIFECYCLE_SERVICE_NOT_INJECTED")
        method(
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            request_id=f"{request_id}::companion-profile",
            aftereffect_id=aftereffect.aftereffect_id,
        )

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
        affect_repair_target: Mapping[str, Any] | AffectiveRepairTargetV1 | None,
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
        repair_target = self._active_affect_repair_target(
            scope=scope,
            value=affect_repair_target,
            allow_pending=normalized_source == "CHAT_RESPONSE",
        )
        identity = {
            "schema": "relationship-episode.v1",
            "scope": scope.aggregate_id,
            "response_receipt_ref": response_receipt_ref,
            "request_id": stable_request_id,
            "source_kind": normalized_source,
            "affect_repair_target": (
                repair_target.as_dict() if repair_target is not None else None
            ),
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
            affect_repair_target=repair_target,
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
        target_id = _companion_target_id(
            scope=scope,
            platform=platform,
            conversation_id=conversation_id,
        )
        return CompanionProactiveTargetV1(
            target_id=target_id,
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            platform=platform,
            conversation_id=conversation_id,
            source_receipt_ref=response_receipt_ref,
            registered_at_ms=opened_at_ms,
        )

    @staticmethod
    def _companion_delivery_scope(
        *,
        message: LifeOutboxMessage,
        context: Mapping[str, Any],
    ) -> tuple[RelationshipScope, str]:
        required = {
            "schema_version",
            "target_id",
            "workspace_id",
            "relationship_subject_id",
            "session_id",
        }
        if frozenset(context) != required:
            raise ValueError("companion delivery context field set drift")
        if str(context.get("schema_version") or "") != (
            "companion_proactive_delivery_context.v1"
        ):
            raise ValueError("companion delivery context schema drift")
        scope = RelationshipScope.from_values(
            workspace_id=context.get("workspace_id"),
            relationship_subject_id=context.get("relationship_subject_id"),
            session_id=context.get("session_id"),
        )
        if message.person_id != scope.aggregate_id:
            raise ValueError("companion delivery aggregate scope drift")
        payload = _mapping(message.payload)
        platform = _text(payload.get("platform"), limit=80).lower()
        conversation_id = _text(payload.get("conversation_id"), limit=512)
        if not platform or not conversation_id:
            raise ValueError("companion delivery target route is required")
        target_id = _text(context.get("target_id"), limit=240)
        expected_target_id = _companion_target_id(
            scope=scope,
            platform=platform,
            conversation_id=conversation_id,
        )
        if target_id != expected_target_id:
            raise ValueError("companion delivery target identity drift")
        return scope, target_id

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
