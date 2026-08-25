"""关系 episode、主动联系节奏与陪伴 profile reducer。

该模块是关系状态变化的唯一 reducer：受理回复、用户后效、显式主动许可与 profile
canary 都必须在同一 Life 事务中验证作用域、回执和版本。它不把沉默、网络失败或
调度推测误写为用户拒绝或关系同意变更。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.digital_life.affective_domain_reducer import _reject_raw_affective_material
from host_brain.digital_life.companion_proactive import (
    PROACTIVE_SILENCE_GRACE_MS,
    proactive_silence_backoff_ms,
)
from host_brain.digital_life.domain_reducer_support import (
    _clamp,
    _integer,
    _mapping,
    _required_text,
)
from natsume_contracts.life import (
    AffectiveRepairTargetV1,
    CompanionProfileCanaryInteractionV1,
    CompanionProfileStateV1,
    CompanionProfileVersionV1,
    CompanionProactiveTargetV1,
    RelationalAftereffectV1,
    RelationshipEpisodeV1,
    RelationshipStateV2,
    baseline_companion_profile,
    digest_json,
)


_COMPANION_PROFILE_COMMAND_TYPES = frozenset(
    {
        "companion_profile_canary_started",
        "companion_profile_canary_interaction_recorded",
        "companion_profile_activated",
        "companion_profile_rolled_back",
    }
)
_PROACTIVE_COMPANION_BASE_CADENCE_MS = 4 * 60 * 60 * 1_000


def _apply_proactive_attempt(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """记录兼容主动联系尝试头；是否真正投递仍以持久 outbox/receipt 为准。"""
    proactive = _mapping(state.get("proactive"))
    has_delivery = bool(list(payload.get("deliveries") or []))
    proactive.update(
        {
            "last_attempt_ms": now_ms,
            "last_status": "QUEUED" if has_delivery else "SKIPPED",
            "last_reason_code": "LIFE_DELIVERY_OUTBOX" if has_delivery else "NO_DELIVERY_INTENT",
            "last_motive": str(payload.get("motive") or "manual_proactive")[:120],
        }
    )
    state["proactive"] = proactive


def _apply_companion_proactive_silence_aftereffect(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """Close an old unanswered proactive episode without inventing rejection.

    The scheduler may observe that an already delivered proactive message has
    remained unanswered past a full grace window.  This is enough to slow the
    next contact attempt, but deliberately not enough to change trust, safety,
    consent, or ``last_user_seen_ms``.  The exact aftereffect is journaled in
    the tick command and is replay-safe like every other reducer transition.
    """

    raw = payload.get("companion_proactive_silence_aftereffect")
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ValueError("companion proactive silence aftereffect must be an object")
    aftereffect = _relational_aftereffect_from_mapping(dict(raw))
    if aftereffect.outcome != "SILENCE":
        raise ValueError("companion proactive silence aftereffect outcome must be SILENCE")
    if not aftereffect.user_input_ref.startswith("scheduler-proactive-silence://sha256/"):
        raise ValueError("companion proactive silence aftereffect source is invalid")
    current = _relationship_scope_matches(
        state=_relationship_state_from_state(state),
        workspace_id=aftereffect.workspace_id,
        relationship_subject_id=aftereffect.relationship_subject_id,
    )
    episode = next(
        (item for item in current.active_episodes if item.episode_id == aftereffect.episode_id),
        None,
    )
    if episode is None:
        raise ValueError("companion proactive silence requires an active episode")
    if episode.source_kind != "PROACTIVE_MESSAGE":
        raise ValueError("companion proactive silence cannot close a foreground episode")
    if aftereffect.source_receipt_ref != episode.response_receipt_ref:
        raise ValueError("companion proactive silence receipt drift")
    if aftereffect.observed_at_ms < episode.opened_at_ms + PROACTIVE_SILENCE_GRACE_MS:
        raise ValueError("companion proactive silence grace has not elapsed")
    if aftereffect.observed_at_ms > now_ms:
        raise ValueError("companion proactive silence cannot be after command time")
    _apply_relational_aftereffect(
        state,
        payload={"aftereffect": aftereffect.as_dict()},
        now_ms=now_ms,
    )


def _apply_companion_proactive_decision(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
    source_affective_digest: str,
) -> None:
    """持久化一条经 Scheduler 授权的陪伴联系节奏决策。

    policy compiler 可以提出决策，却不能通过 ``state_patch`` 改写 canonical
    lifecycle head。reducer 会验证冻结的作用域/revision、内容摘要和表达计划，
    再与已提交的 delivery outbox intent 原子写入有界状态。
    """

    raw = payload.get("companion_proactive_decision")
    if raw is None:
        return
    if not isinstance(raw, Mapping):
        raise ValueError("companion proactive decision must be an object")
    decision = dict(raw)
    expected = {
        "schema_version",
        "decision_id",
        "target_id",
        "workspace_id",
        "relationship_subject_id",
        "session_id",
        "self_affect_revision",
        "relationship_revision",
        "reason_code",
        "motive",
        "action",
        "content_digest",
        "affective_turn_plan_id",
        "synchronization_id",
        "next_allowed_at_ms",
        "consecutive_unanswered",
        "created_at_ms",
    }
    if set(decision) != expected:
        raise ValueError("companion proactive decision field set drift")
    if decision.get("schema_version") != "companion_proactive_decision.v2":
        raise ValueError("companion proactive decision schema is invalid")
    relationship = _relationship_state_from_state(state)
    target = _mapping(state.get("companion_proactive_target"))
    if not target:
        raise ValueError("companion proactive decision target is unavailable")
    scope_fields = {
        "target_id": target.get("target_id"),
        "workspace_id": relationship.workspace_id,
        "relationship_subject_id": relationship.relationship_subject_id,
        "session_id": target.get("session_id"),
    }
    for field_name, expected_value in scope_fields.items():
        if str(decision.get(field_name) or "").strip() != str(expected_value or "").strip():
            raise ValueError(f"companion proactive decision {field_name} drift")
    affective = _mapping(state.get("affective"))
    if _integer(decision.get("self_affect_revision")) != _integer(
        affective.get("revision")
    ):
        raise ValueError("companion proactive decision self affect revision drift")
    if _integer(decision.get("relationship_revision")) != relationship.revision:
        raise ValueError("companion proactive decision relationship revision drift")
    created_at_ms = _integer(decision.get("created_at_ms"))
    if created_at_ms > now_ms:
        raise ValueError("companion proactive decision cannot be after command time")
    next_allowed_at_ms = _integer(decision.get("next_allowed_at_ms"))
    if next_allowed_at_ms < created_at_ms:
        raise ValueError("companion proactive decision cadence regressed")
    content_digest = str(decision.get("content_digest") or "").strip().lower()
    if (
        not content_digest.startswith("sha256:")
        or len(content_digest) != 71
        or any(character not in "0123456789abcdef" for character in content_digest[7:])
    ):
        raise ValueError("companion proactive decision content digest is invalid")
    for field_name in (
        "decision_id",
        "reason_code",
        "motive",
        "action",
        "affective_turn_plan_id",
        "synchronization_id",
    ):
        if not str(decision.get(field_name) or "").strip():
            raise ValueError(f"companion proactive decision {field_name} is required")
    deliveries = [
        dict(item)
        for item in list(payload.get("deliveries") or [])
        if isinstance(item, Mapping)
        and str(item.get("destination") or "").strip().lower()
        == "proactive.gateway"
    ]
    if len(deliveries) != 1:
        raise ValueError("companion proactive decision requires gateway delivery")
    delivery_payload = _mapping(deliveries[0].get("payload"))
    raw_extra = _mapping(delivery_payload.get("raw_extra"))
    if _mapping(raw_extra.get("companion_proactive_decision")) != decision:
        raise ValueError("companion proactive delivery decision drift")
    plan = _mapping(raw_extra.get("affective_turn_plan"))
    expression = _mapping(plan.get("expression_plan"))
    if (
        plan.get("schema_version") != "affective_turn_plan.v1"
        or str(plan.get("plan_id") or "") != decision["affective_turn_plan_id"]
        or str(expression.get("synchronization_id") or "")
        != decision["synchronization_id"]
        or _integer(plan.get("self_affect_revision"))
        != _integer(decision.get("self_affect_revision"))
        or _integer(plan.get("relationship_revision"))
        != _integer(decision.get("relationship_revision"))
        or str(plan.get("workspace_id") or "") != relationship.workspace_id
        or str(plan.get("relationship_subject_id") or "")
        != relationship.relationship_subject_id
        or str(plan.get("session_id") or "") != str(target.get("session_id") or "")
        or str(plan.get("self_id") or "") != "natsume_ai"
        or str(plan.get("self_affect_digest") or "") != source_affective_digest
        or _integer(plan.get("created_at_ms")) != created_at_ms
    ):
        raise ValueError("companion proactive affective turn plan drift")
    profile_stage = str(plan.get("companion_profile_stage") or "").strip().upper()
    profile_id = str(plan.get("companion_profile_id") or "").strip()
    profile_digest = str(plan.get("companion_profile_digest") or "").strip()
    if profile_stage == "COMPATIBILITY_UNBOUND":
        if profile_id or profile_digest or _integer(plan.get("companion_profile_revision")):
            raise ValueError("companion proactive compatibility profile drift")
    else:
        profile_state = _companion_profile_state_from_state(state)
        profile = (
            profile_state.canary_profile
            if profile_state.canary_profile is not None
            else profile_state.active_profile
        )
        expected_stage = (
            "CANARY"
            if profile_state.canary_profile is not None
            else ("BASELINE" if profile.is_baseline else "ACTIVE")
        )
        if (
            profile_id != profile.profile_id
            or profile_digest != profile.profile_digest
            or _integer(plan.get("companion_profile_revision"))
            != _integer(profile_state.revision)
            or profile_stage != expected_stage
            or str(expression.get("companion_profile_id") or "") != profile_id
            or str(expression.get("companion_profile_digest") or "") != profile_digest
        ):
            raise ValueError("companion proactive profile plan drift")
    if f"sha256:{digest_json({'content': str(delivery_payload.get('content') or '')})}" != content_digest:
        raise ValueError("companion proactive delivery content digest drift")

    prior = _normalize_companion_proactive_head(state.get("companion_proactive"))
    unanswered = min(8, _integer(decision.get("consecutive_unanswered")))
    if unanswered != prior["consecutive_unanswered"]:
        raise ValueError("companion proactive decision unanswered count drift")
    head = {
        **prior,
        "last_decision_id": str(decision["decision_id"])[:240],
        "last_status": "QUEUED",
        "last_reason_code": str(decision["reason_code"])[:120],
        "last_motive": str(decision["motive"])[:120],
        "last_content_digest": content_digest,
        "last_plan_id": str(decision["affective_turn_plan_id"])[:240],
        "last_synchronization_id": str(decision["synchronization_id"])[:240],
        "last_attempt_ms": created_at_ms,
        "next_allowed_at_ms": next_allowed_at_ms,
        "consecutive_unanswered": unanswered,
        "updated_at_ms": now_ms,
    }
    state["companion_proactive"] = head
    # ``proactive`` remains a compatibility projection for legacy read paths;
    # it is now reducer-owned and carries no independent contact authority.
    state["proactive"] = {
        **_mapping(state.get("proactive")),
        "last_attempt_ms": created_at_ms,
        "last_status": "QUEUED",
        "last_reason_code": head["last_reason_code"],
        "last_motive": head["last_motive"],
    }


def _normalize_companion_proactive_head(raw: Any) -> dict[str, Any]:
    """规范化有界 cadence head；它仅记录频率，不携带独立联系授权。"""
    source = _mapping(raw)
    return {
        "schema_version": "companion_proactive_state.v1",
        "last_decision_id": str(source.get("last_decision_id") or "")[:240],
        "last_episode_id": str(source.get("last_episode_id") or "")[:240],
        "last_status": str(source.get("last_status") or "")[:64],
        "last_reason_code": str(source.get("last_reason_code") or "")[:120],
        "last_motive": str(source.get("last_motive") or "")[:120],
        "last_content_digest": str(source.get("last_content_digest") or "")[:80],
        "last_plan_id": str(source.get("last_plan_id") or "")[:240],
        "last_synchronization_id": str(source.get("last_synchronization_id") or "")[:240],
        "last_attempt_ms": _integer(source.get("last_attempt_ms")),
        "next_allowed_at_ms": _integer(source.get("next_allowed_at_ms")),
        "consecutive_unanswered": min(8, _integer(source.get("consecutive_unanswered"))),
        "updated_at_ms": _integer(source.get("updated_at_ms")),
    }


def _canonical_identity(person_id: str) -> dict[str, str]:
    """Return the non-negotiable self/relationship-subject separation."""

    return {
        "self_id": "natsume_ai",
        "relationship_subject_id": str(person_id),
    }


def _resolve_canonical_relationship_subject(
    state: Mapping[str, Any],
    *,
    aggregate_person_id: str,
    event_type: str,
    payload: Mapping[str, Any],
) -> str:
    """保持 Life aggregate key 与关系主体分离。

    DL-02 按 workspace/session 作用域 affect aggregate，DL-03 按 workspace
    作用域 relationship aggregate。aggregate key 是不透明键，canonical affect/
    relationship 契约保留真实主体；既有非 canonical Life 命令继续使用历史身份。
    """

    identity = _mapping(state.get("identity"))
    existing = str(identity.get("relationship_subject_id") or "").strip()
    candidate = ""
    if event_type == "affective_observation_recorded":
        candidate = str(
            _mapping(payload.get("observation")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "affective_event_appraised":
        candidate = str(
            _mapping(payload.get("appraisal")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "affective_regulation_decided":
        candidate = str(
            _mapping(payload.get("regulation")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "affective_time_advanced":
        # A real ingress turn advances the scoped Life clock before it
        # records fresh observations.  The opaque aggregate id can never be
        # mistaken for the relationship subject during that first command.
        candidate = str(payload.get("relationship_subject_id") or "").strip()
    elif event_type in {
        "relationship_response_accepted",
        "relationship_proactive_delivered",
    }:
        candidate = str(
            _mapping(payload.get("episode")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "relational_aftereffect_recorded":
        candidate = str(
            _mapping(payload.get("aftereffect")).get(
                "relationship_subject_id"
            )
            or ""
        ).strip()
    elif event_type == "relationship_proactive_permission_changed":
        candidate = str(payload.get("relationship_subject_id") or "").strip()
    elif event_type in _COMPANION_PROFILE_COMMAND_TYPES:
        candidate = str(
            _mapping(payload.get("profile")).get("relationship_subject_id") or ""
        ).strip()

    if not candidate:
        return existing or str(aggregate_person_id)
    # An untouched seed uses the aggregate key as its placeholder.  Replacing
    # that placeholder is reducer-owned and deterministic; state_patch still
    # cannot write either identity or affective domains.
    if existing and existing not in {str(aggregate_person_id), candidate}:
        raise ValueError("affective relationship subject drift")
    return candidate


def _relationship_episode_from_mapping(raw: Mapping[str, Any]) -> RelationshipEpisodeV1:
    if not raw:
        raise ValueError("relationship episode is required")
    return RelationshipEpisodeV1(
        episode_id=raw.get("episode_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        source_kind=raw.get("source_kind"),
        response_receipt_ref=raw.get("response_receipt_ref"),
        response_digest=raw.get("response_digest"),
        opened_at_ms=raw.get("opened_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        affect_repair_target=(
            None
            if raw.get("affect_repair_target") is None
            else AffectiveRepairTargetV1.from_mapping(
                _mapping(raw.get("affect_repair_target"))
            )
        ),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _companion_proactive_target_from_mapping(
    raw: Mapping[str, Any],
) -> CompanionProactiveTargetV1:
    if not raw:
        raise ValueError("companion proactive target is required")
    return CompanionProactiveTargetV1(
        target_id=raw.get("target_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        platform=raw.get("platform"),
        conversation_id=raw.get("conversation_id"),
        source_receipt_ref=raw.get("source_receipt_ref"),
        registered_at_ms=raw.get("registered_at_ms"),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _normalize_companion_proactive_target(
    raw: Any,
    *,
    relationship_state: Mapping[str, Any],
) -> dict[str, Any] | None:
    """验证目标与当前关系 workspace/subject 一致；目标本身不是联系许可。"""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("companion proactive target must be an object")
    target = _companion_proactive_target_from_mapping(raw)
    relationship = _mapping(relationship_state)
    if (
        target.workspace_id != str(relationship.get("workspace_id") or "")
        or target.relationship_subject_id
        != str(relationship.get("relationship_subject_id") or "")
    ):
        raise ValueError("companion proactive target scope drift")
    return target.as_dict()


def _companion_profile_from_mapping(raw: Mapping[str, Any]) -> CompanionProfileVersionV1:
    if not raw:
        raise ValueError("companion profile is required")
    return CompanionProfileVersionV1.from_mapping(raw)


def _companion_profile_state_from_mapping(raw: Mapping[str, Any]) -> CompanionProfileStateV1:
    if not raw:
        raise ValueError("companion profile state is required")
    return CompanionProfileStateV1.from_mapping(raw)


def _normalize_companion_profile_state(
    raw: Any,
    *,
    relationship_state: Mapping[str, Any],
) -> dict[str, Any]:
    """保持一份有界且绑定作用域的表达 profile head。

    只有全新 unbound 基线可随首次关系绑定迁移；任何已学习或 canary profile 的
    workspace/subject 漂移均失败关闭，避免把一位用户的偏好带入另一关系。
    """

    relationship = _mapping(relationship_state)
    workspace_id = str(relationship.get("workspace_id") or "workspace::unbound").strip()
    subject_id = str(relationship.get("relationship_subject_id") or "").strip()
    if not workspace_id or not subject_id:
        raise ValueError("companion profile requires a canonical relationship scope")
    if raw is None:
        baseline = baseline_companion_profile(
            workspace_id=workspace_id,
            relationship_subject_id=subject_id,
        )
        return CompanionProfileStateV1(
            workspace_id=workspace_id,
            relationship_subject_id=subject_id,
            revision=0,
            active_profile=baseline,
            known_profiles=(baseline,),
        ).as_dict()
    if not isinstance(raw, Mapping):
        raise ValueError("companion profile state must be an object")
    state = _companion_profile_state_from_mapping(raw)
    if (
        state.workspace_id != workspace_id
        or state.relationship_subject_id != subject_id
    ):
        if (
            state.workspace_id == "workspace::unbound"
            and state.relationship_subject_id == subject_id
            and state.revision == 0
            and state.active_profile.is_baseline
            and state.canary_profile is None
            and len(state.known_profiles) == 1
        ):
            baseline = baseline_companion_profile(
                workspace_id=workspace_id,
                relationship_subject_id=subject_id,
            )
            return CompanionProfileStateV1(
                workspace_id=workspace_id,
                relationship_subject_id=subject_id,
                revision=0,
                active_profile=baseline,
                known_profiles=(baseline,),
            ).as_dict()
        raise ValueError("companion profile scope drift")
    return state.as_dict()


def _companion_profile_state_from_state(state: Mapping[str, Any]) -> CompanionProfileStateV1:
    return _companion_profile_state_from_mapping(_mapping(state.get("companion_profile")))


def _assert_companion_profile_scope(
    profile: CompanionProfileVersionV1,
    *,
    state: Mapping[str, Any],
) -> RelationshipStateV2:
    """要求 profile 依附于已建立关系，不能在 unbound 默认 scope 中演进。"""
    relationship = _relationship_state_from_state(state)
    if relationship.workspace_id == "workspace::unbound":
        raise ValueError("companion profile requires an established relationship scope")
    return _relationship_scope_matches(
        state=relationship,
        workspace_id=profile.workspace_id,
        relationship_subject_id=profile.relationship_subject_id,
    )


def _profile_known_with(
    current: CompanionProfileStateV1,
    profile: CompanionProfileVersionV1,
) -> tuple[CompanionProfileVersionV1, ...]:
    """将精确版本加入有限历史；同 ID 但内容不同视为不可恢复的身份漂移。"""
    existing = {item.profile_id: item for item in current.known_profiles}
    prior = existing.get(profile.profile_id)
    if prior is not None and prior.as_dict() != profile.as_dict():
        raise ValueError("companion profile id digest drift")
    if prior is None:
        existing[profile.profile_id] = profile
    ordered = tuple(
        sorted(existing.values(), key=lambda item: (item.version, item.profile_id))
    )
    if len(ordered) > 8:
        raise ValueError("companion profile history limit exceeded")
    return ordered


def _relational_aftereffect_from_mapping(
    raw: Mapping[str, Any],
) -> RelationalAftereffectV1:
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
        affect_repair_target=(
            None
            if raw.get("affect_repair_target") is None
            else AffectiveRepairTargetV1.from_mapping(
                _mapping(raw.get("affect_repair_target"))
            )
        ),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _normalize_relationship_head(
    raw: Any,
    *,
    relationship_subject_id: str,
    aggregate_person_id: str,
) -> dict[str, Any]:
    """规范化 canonical 关系 head，且只允许首次命令替换种子 aggregate 占位主体。"""
    source = _mapping(raw)
    workspace_id = str(source.get("workspace_id") or "workspace::unbound").strip()
    if not workspace_id:
        workspace_id = "workspace::unbound"
    subject = str(source.get("relationship_subject_id") or "").strip()
    # A just-seeded aggregate initially uses its opaque aggregate id as a
    # placeholder.  The first typed relationship command is allowed to bind
    # the real user subject exactly once; an existing non-empty head may never
    # silently switch subjects.
    if (
        not subject
        or (
            subject == str(aggregate_person_id)
            and _integer(source.get("revision")) == 0
            and not list(source.get("active_episodes") or [])
            and source.get("last_aftereffect") is None
        )
    ):
        subject = str(relationship_subject_id)
    if subject != str(relationship_subject_id):
        raise ValueError("relationship_v2 relationship subject drift")
    raw_episodes = source.get("active_episodes") or []
    if not isinstance(raw_episodes, list):
        raise ValueError("relationship_v2.active_episodes must be a list")
    episodes = tuple(
        _relationship_episode_from_mapping(_mapping(item))
        for item in raw_episodes
        if isinstance(item, Mapping)
    )
    if len(episodes) != len(raw_episodes):
        raise ValueError("relationship_v2.active_episodes must contain objects")
    last_aftereffect_raw = source.get("last_aftereffect")
    last_aftereffect = (
        _relational_aftereffect_from_mapping(_mapping(last_aftereffect_raw))
        if isinstance(last_aftereffect_raw, Mapping)
        else None
    )
    raw_repair_targets = source.get("pending_affect_repair_targets") or []
    if not isinstance(raw_repair_targets, list):
        raise ValueError("relationship_v2.pending_affect_repair_targets must be a list")
    repair_targets = tuple(
        AffectiveRepairTargetV1.from_mapping(_mapping(item))
        for item in raw_repair_targets
        if isinstance(item, Mapping)
    )
    if len(repair_targets) != len(raw_repair_targets):
        raise ValueError(
            "relationship_v2.pending_affect_repair_targets must contain objects"
        )
    return RelationshipStateV2(
        workspace_id=workspace_id,
        relationship_subject_id=subject,
        revision=max(0, _integer(source.get("revision"))),
        trust=_clamp(source.get("trust"), 0.5),
        safety=_clamp(source.get("safety"), 0.58),
        attunement=_clamp(source.get("attunement"), 0.42),
        rupture_load=_clamp(source.get("rupture_load"), 0.0),
        repair_required=bool(source.get("repair_required", False)),
        proactive_permission=str(source.get("proactive_permission") or "UNKNOWN"),
        romantic_consent=str(source.get("romantic_consent") or "NOT_GRANTED"),
        exclusive_consent=str(source.get("exclusive_consent") or "NOT_GRANTED"),
        dependency_consent=str(source.get("dependency_consent") or "NOT_GRANTED"),
        active_episodes=episodes,
        last_aftereffect=last_aftereffect,
        pending_affect_repair_targets=repair_targets,
    ).as_dict()


def _relationship_state_from_state(state: Mapping[str, Any]) -> RelationshipStateV2:
    raw = _mapping(state.get("relationship_v2"))
    return RelationshipStateV2(
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        revision=raw.get("revision"),
        trust=raw.get("trust"),
        safety=raw.get("safety"),
        attunement=raw.get("attunement"),
        rupture_load=raw.get("rupture_load"),
        repair_required=raw.get("repair_required"),
        proactive_permission=raw.get("proactive_permission"),
        romantic_consent=raw.get("romantic_consent"),
        exclusive_consent=raw.get("exclusive_consent"),
        dependency_consent=raw.get("dependency_consent"),
        active_episodes=tuple(
            _relationship_episode_from_mapping(_mapping(item))
            for item in list(raw.get("active_episodes") or [])
        ),
        last_aftereffect=(
            _relational_aftereffect_from_mapping(_mapping(raw.get("last_aftereffect")))
            if isinstance(raw.get("last_aftereffect"), Mapping)
            else None
        ),
        pending_affect_repair_targets=tuple(
            AffectiveRepairTargetV1.from_mapping(_mapping(item))
            for item in list(raw.get("pending_affect_repair_targets") or [])
        ),
    )


def _relationship_scope_matches(
    *,
    state: RelationshipStateV2,
    workspace_id: str,
    relationship_subject_id: str,
) -> RelationshipStateV2:
    """确认 workspace/subject 与现有关系一致；unbound workspace 只可首次绑定。"""
    if state.relationship_subject_id != relationship_subject_id:
        raise ValueError("relationship subject drift")
    if state.workspace_id == "workspace::unbound":
        return RelationshipStateV2(
            workspace_id=workspace_id,
            relationship_subject_id=state.relationship_subject_id,
            revision=state.revision,
            trust=state.trust,
            safety=state.safety,
            attunement=state.attunement,
            rupture_load=state.rupture_load,
            repair_required=state.repair_required,
            proactive_permission=state.proactive_permission,
            romantic_consent=state.romantic_consent,
            exclusive_consent=state.exclusive_consent,
            dependency_consent=state.dependency_consent,
            active_episodes=state.active_episodes,
            last_aftereffect=state.last_aftereffect,
            pending_affect_repair_targets=state.pending_affect_repair_targets,
        )
    if state.workspace_id != workspace_id:
        raise ValueError("relationship workspace drift")
    return state


def _apply_companion_profile_canary_started(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
) -> None:
    """启动一次精确的五交互表达 profile canary。

    这是 Life 命令而非进程内试验计数器。新 profile 必须指向当前 active 版本作为
    回滚目标，因此后续回滚不能挑选一个方便却无关的历史版本。
    """

    _reject_raw_affective_material(payload)
    profile = _companion_profile_from_mapping(_mapping(payload.get("profile")))
    if profile.is_baseline:
        raise ValueError("baseline companion profile cannot enter canary")
    current = _companion_profile_state_from_state(state)
    _assert_companion_profile_scope(profile, state=state)
    if current.canary_profile is not None:
        raise ValueError("companion profile canary is already active")
    if profile.rollback_profile_id != current.active_profile.profile_id:
        raise ValueError("companion profile rollback target must equal active profile")
    max_version = max(item.version for item in current.known_profiles)
    if profile.version != max_version + 1:
        raise ValueError("companion profile version must advance exactly once")
    known = _profile_known_with(current, profile)
    state["companion_profile"] = CompanionProfileStateV1(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        active_profile=current.active_profile,
        canary_profile=profile,
        canary_accepted_interaction_ids=(),
        known_profiles=known,
    ).as_dict()


def _apply_companion_profile_canary_interaction(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
) -> None:
    """记录固定 canary 中一次独立接受的后效；重复或第六次记录均不被接受。"""

    _reject_raw_affective_material(payload)
    profile = _companion_profile_from_mapping(_mapping(payload.get("profile")))
    interaction = CompanionProfileCanaryInteractionV1.from_mapping(
        _mapping(payload.get("canary_interaction"))
    )
    current = _companion_profile_state_from_state(state)
    _assert_companion_profile_scope(profile, state=state)
    candidate = current.canary_profile
    if candidate is None or candidate.profile_id != profile.profile_id:
        raise ValueError("companion profile canary is not active for profile")
    if candidate.as_dict() != profile.as_dict():
        raise ValueError("companion profile canary payload drift")
    if interaction.profile_id != candidate.profile_id:
        raise ValueError("companion profile canary interaction profile drift")
    if len(current.canary_accepted_interaction_ids) >= 5:
        raise ValueError("companion profile canary is already complete")
    if interaction.interaction_id in current.canary_accepted_interaction_ids:
        raise ValueError("companion profile canary interaction already recorded")
    state["companion_profile"] = CompanionProfileStateV1(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        active_profile=current.active_profile,
        canary_profile=candidate,
        canary_accepted_interaction_ids=(
            *current.canary_accepted_interaction_ids,
            interaction.interaction_id,
        ),
        known_profiles=current.known_profiles,
    ).as_dict()


def _apply_companion_profile_activation(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
) -> None:
    """只将完整观察到五次的候选提升为 active expression head。"""

    _reject_raw_affective_material(payload)
    profile = _companion_profile_from_mapping(_mapping(payload.get("profile")))
    current = _companion_profile_state_from_state(state)
    _assert_companion_profile_scope(profile, state=state)
    candidate = current.canary_profile
    if candidate is None or candidate.profile_id != profile.profile_id:
        raise ValueError("companion profile activation requires the active canary")
    if candidate.as_dict() != profile.as_dict():
        raise ValueError("companion profile activation payload drift")
    if len(current.canary_accepted_interaction_ids) != 5:
        raise ValueError("companion profile activation requires five accepted canary interactions")
    if profile.rollback_profile_id != current.active_profile.profile_id:
        raise ValueError("companion profile activation rollback target drift")
    state["companion_profile"] = CompanionProfileStateV1(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        active_profile=candidate,
        canary_profile=None,
        canary_accepted_interaction_ids=(),
        known_profiles=current.known_profiles,
    ).as_dict()


def _apply_companion_profile_rollback(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
) -> None:
    """仅恢复当前版本精确的 pre-canary profile，不能选择任意历史偏好。"""

    _reject_raw_affective_material(payload)
    target = _companion_profile_from_mapping(_mapping(payload.get("profile")))
    current = _companion_profile_state_from_state(state)
    _assert_companion_profile_scope(target, state=state)
    current_active = current.active_profile
    if current_active.is_baseline:
        raise ValueError("baseline companion profile has no earlier rollback target")
    if target.profile_id != current_active.rollback_profile_id:
        raise ValueError("companion profile rollback must restore exact previous profile")
    known_by_id = {item.profile_id: item for item in current.known_profiles}
    known_target = known_by_id.get(target.profile_id)
    if known_target is None or known_target.as_dict() != target.as_dict():
        raise ValueError("companion profile rollback target is not an exact known version")
    state["companion_profile"] = CompanionProfileStateV1(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        active_profile=known_target,
        canary_profile=None,
        canary_accepted_interaction_ids=(),
        known_profiles=current.known_profiles,
    ).as_dict()


def _apply_relationship_response_accepted(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """打开一条有回执的关系 episode，并可原子注册后续主动联系目标。

    直接影响 Natsume 的 reply 会形成有限修复上下文，而不是对用户的指责或任何
    同意撤销；只有之后有确认的用户后效才可结清关联 affect repair target。
    """
    _reject_raw_affective_material(payload)
    episode = _relationship_episode_from_mapping(_mapping(payload.get("episode")))
    raw_target = payload.get("companion_proactive_target")
    target = (
        _companion_proactive_target_from_mapping(_mapping(raw_target))
        if raw_target is not None
        else None
    )
    if episode.opened_at_ms > now_ms:
        raise ValueError("relationship episode cannot open after command time")
    if target is not None and (
        target.workspace_id != episode.workspace_id
        or target.relationship_subject_id != episode.relationship_subject_id
        or target.session_id != episode.session_id
        or target.source_receipt_ref != episode.response_receipt_ref
        or target.registered_at_ms != episode.opened_at_ms
    ):
        raise ValueError("companion proactive target response receipt drift")
    current = _relationship_scope_matches(
        state=_relationship_state_from_state(state),
        workspace_id=episode.workspace_id,
        relationship_subject_id=episode.relationship_subject_id,
    )
    if any(item.episode_id == episode.episode_id for item in current.active_episodes):
        raise ValueError("relationship episode already active")
    episodes = (*current.active_episodes, episode)
    if len(episodes) > 4:
        raise ValueError("relationship active episode limit exceeded")
    # A reply that was causally constrained by a direct impact on Natsume is
    # not an accusation and does not revoke any consent.  It does mean the
    # relationship now has a small, explicit repair context.  Only a later
    # acknowledged user response may settle the linked self-affect episode.
    rupture = current.rupture_load
    repair_required = current.repair_required
    pending_targets = tuple(current.pending_affect_repair_targets)
    if (
        episode.affect_repair_target is not None
        and all(
            (
                item.affect_aggregate_id,
                item.affect_episode_id,
            )
            != (
                episode.affect_repair_target.affect_aggregate_id,
                episode.affect_repair_target.affect_episode_id,
            )
            for item in pending_targets
        )
    ):
        pending_targets = (*pending_targets, episode.affect_repair_target)
        if len(pending_targets) > 4:
            raise ValueError("pending affect repair target limit exceeded")
        rupture = _clamp(rupture + 0.08)
        repair_required = True
    state["relationship_v2"] = RelationshipStateV2(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        trust=current.trust,
        safety=current.safety,
        attunement=current.attunement,
        rupture_load=rupture,
        repair_required=repair_required,
        proactive_permission=current.proactive_permission,
        romantic_consent=current.romantic_consent,
        exclusive_consent=current.exclusive_consent,
        dependency_consent=current.dependency_consent,
        active_episodes=episodes,
        last_aftereffect=current.last_aftereffect,
        pending_affect_repair_targets=pending_targets,
    ).as_dict()
    if target is not None:
        state["companion_proactive_target"] = target.as_dict()
    if episode.source_kind == "CHAT_RESPONSE":
        _record_relationship_user_presence(state, observed_at_ms=episode.opened_at_ms)


def _apply_relational_aftereffect(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """用关联 episode 的用户后效更新关系指标并关闭 episode。

    ``NEUTRAL``、``SILENCE`` 与 ``DELIVERY_FAILED`` 不改变信任、安全、许可或
    consent：缺少互动/网络完成从不等同用户拒绝。修复接受还必须精确匹配待结清
    的 affect target，防止泛化确认关闭不相干的伤害 episode。
    """
    _reject_raw_affective_material(payload)
    aftereffect = _relational_aftereffect_from_mapping(
        _mapping(payload.get("aftereffect"))
    )
    if aftereffect.observed_at_ms > now_ms:
        raise ValueError("relational aftereffect cannot be after command time")
    current = _relationship_scope_matches(
        state=_relationship_state_from_state(state),
        workspace_id=aftereffect.workspace_id,
        relationship_subject_id=aftereffect.relationship_subject_id,
    )
    episode = next(
        (item for item in current.active_episodes if item.episode_id == aftereffect.episode_id),
        None,
    )
    if episode is None:
        raise ValueError("relational aftereffect requires an active episode")
    if episode.response_receipt_ref != aftereffect.source_receipt_ref:
        raise ValueError("relational aftereffect response receipt drift")

    trust = current.trust
    safety = current.safety
    attunement = current.attunement
    rupture = current.rupture_load
    repair_required = current.repair_required
    permission = current.proactive_permission
    pending_targets = list(current.pending_affect_repair_targets)
    outcome = aftereffect.outcome
    if outcome == "HELPFUL":
        trust = _clamp(trust + 0.012 * aftereffect.helpfulness)
        safety = _clamp(safety + 0.009 * aftereffect.helpfulness)
        attunement = _clamp(attunement + 0.02 * aftereffect.helpfulness)
    elif outcome == "MISUNDERSTOOD":
        trust = _clamp(trust - 0.035)
        safety = _clamp(safety - 0.05)
        rupture = _clamp(rupture + 0.11)
        repair_required = True
    elif outcome == "BOUNDARY":
        trust = _clamp(trust - 0.06)
        safety = _clamp(safety - 0.11)
        rupture = _clamp(rupture + 0.2)
        repair_required = True
        permission = "DENIED"
    elif outcome == "REPAIR_UNRESOLVED":
        safety = _clamp(safety - 0.025)
        rupture = _clamp(rupture + 0.05)
        repair_required = True
    elif outcome == "REPAIR_ACCEPTED":
        if not current.repair_required or not aftereffect.repair_acknowledged:
            raise ValueError("repair acceptance requires a pending rupture and user acknowledgement")
        target = aftereffect.affect_repair_target
        if pending_targets:
            if target is None:
                raise ValueError(
                    "repair acceptance requires an exact pending affect target"
                )
            target_key = (
                target.affect_aggregate_id,
                target.affect_episode_id,
            )
            matching = [
                item
                for item in pending_targets
                if (item.affect_aggregate_id, item.affect_episode_id) == target_key
            ]
            if len(matching) != 1:
                raise ValueError("repair acceptance affect target drift")
            pending_targets = [
                item
                for item in pending_targets
                if (item.affect_aggregate_id, item.affect_episode_id) != target_key
            ]
        elif target is not None:
            raise ValueError("repair acceptance has no pending affect target")
        safety = _clamp(safety + 0.035)
        attunement = _clamp(attunement + 0.015)
        rupture = _clamp(rupture - 0.18)
        repair_required = bool(pending_targets) or rupture > 0.05
    # NEUTRAL, SILENCE and DELIVERY_FAILED intentionally do not alter trust,
    # safety, permission or consent. Lack of interaction is not rejection.
    remaining = tuple(
        item for item in current.active_episodes if item.episode_id != episode.episode_id
    )
    state["relationship_v2"] = RelationshipStateV2(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        trust=trust,
        safety=safety,
        attunement=attunement,
        rupture_load=rupture,
        repair_required=repair_required,
        proactive_permission=permission,
        romantic_consent=current.romantic_consent,
        exclusive_consent=current.exclusive_consent,
        dependency_consent=current.dependency_consent,
        active_episodes=remaining,
        last_aftereffect=aftereffect,
        pending_affect_repair_targets=tuple(pending_targets),
    ).as_dict()
    _apply_companion_proactive_aftereffect_head(
        state,
        episode=episode,
        aftereffect=aftereffect,
        now_ms=now_ms,
    )
    if aftereffect.outcome not in {"SILENCE", "DELIVERY_FAILED"}:
        _record_relationship_user_presence(
            state,
            observed_at_ms=aftereffect.observed_at_ms,
        )


def _apply_companion_proactive_aftereffect_head(
    state: dict[str, Any],
    *,
    episode: RelationshipEpisodeV1,
    aftereffect: RelationalAftereffectV1,
    now_ms: int,
) -> None:
    """仅对真实主动 episode 的后效更新联系节奏，不重写关系语义。"""

    if episode.source_kind != "PROACTIVE_MESSAGE":
        return
    prior = _normalize_companion_proactive_head(state.get("companion_proactive"))
    outcome = aftereffect.outcome
    unanswered = prior["consecutive_unanswered"]
    next_allowed_at_ms = prior["next_allowed_at_ms"]
    if outcome == "SILENCE":
        unanswered = min(8, unanswered + 1)
        next_allowed_at_ms = max(
            next_allowed_at_ms,
            aftereffect.observed_at_ms + proactive_silence_backoff_ms(unanswered),
        )
    elif outcome not in {"DELIVERY_FAILED", "BOUNDARY"}:
        # A real response, even a neutral one, is not an ignored contact.  It
        # resets only the frequency calibration; relationship semantics were
        # already handled above by the typed aftereffect outcome.
        unanswered = 0
        next_allowed_at_ms = max(
            next_allowed_at_ms,
            aftereffect.observed_at_ms + _PROACTIVE_COMPANION_BASE_CADENCE_MS,
        )
    state["companion_proactive"] = {
        **prior,
        "last_episode_id": episode.episode_id,
        "last_status": outcome,
        "last_reason_code": f"RELATIONAL_AFTEREFFECT_{outcome}",
        "consecutive_unanswered": unanswered,
        "next_allowed_at_ms": next_allowed_at_ms,
        "updated_at_ms": now_ms,
    }


def _apply_relationship_proactive_permission(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """只接受同作用域用户显式证据来改变主动联系许可。"""
    _reject_raw_affective_material(payload)
    workspace_id = _required_text(
        payload.get("workspace_id"), "relationship permission workspace_id", limit=240
    )
    subject_id = _required_text(
        payload.get("relationship_subject_id"), "relationship permission relationship_subject_id", limit=240
    )
    permission = _required_text(
        payload.get("proactive_permission"), "relationship proactive_permission", limit=64
    ).upper()
    _required_text(
        payload.get("source_ref"), "relationship permission source_ref", limit=512
    )
    if permission not in {"ALLOWED", "DENIED"}:
        raise ValueError("relationship proactive permission must be ALLOWED or DENIED")
    source_kind = _required_text(
        payload.get("source_kind"), "relationship permission source_kind", limit=64
    ).upper()
    if source_kind != "USER_EXPLICIT":
        raise ValueError("relationship proactive permission requires USER_EXPLICIT evidence")
    current = _relationship_scope_matches(
        state=_relationship_state_from_state(state),
        workspace_id=workspace_id,
        relationship_subject_id=subject_id,
    )
    state["relationship_v2"] = RelationshipStateV2(
        workspace_id=current.workspace_id,
        relationship_subject_id=current.relationship_subject_id,
        revision=current.revision + 1,
        trust=current.trust,
        safety=current.safety,
        attunement=current.attunement,
        rupture_load=current.rupture_load,
        repair_required=current.repair_required,
        proactive_permission=permission,
        romantic_consent=current.romantic_consent,
        exclusive_consent=current.exclusive_consent,
        dependency_consent=current.dependency_consent,
        active_episodes=current.active_episodes,
        last_aftereffect=current.last_aftereffect,
        pending_affect_repair_targets=current.pending_affect_repair_targets,
    ).as_dict()
    _record_relationship_user_presence(state, observed_at_ms=now_ms)


def _record_relationship_user_presence(
    state: dict[str, Any],
    *,
    observed_at_ms: int,
) -> None:
    """只从类型化用户事件推进关系时钟，调度与网关活动不能伪造用户在线。"""

    clock = _mapping(state.get("clock"))
    timestamp = max(0, int(observed_at_ms))
    clock["last_user_seen_ms"] = max(
        _integer(clock.get("last_user_seen_ms")),
        timestamp,
    )
    clock["idle_minutes"] = 0
    state["clock"] = clock
