"""canonical self-affect 的观察、评估与 episode reducer。

本模块区分用户侧观察和 Natsume 自我情感：观察只进入有界 head；只有携带已验证
appraisal/episode 的命令才可形成可调节、可衰减的 self-affect 状态。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from host_brain.digital_life.domain_reducer_support import (
    _approach,
    _approach_signed,
    _clamp,
    _float,
    _integer,
    _mapping,
    _required_text,
)
from natsume_contracts.life import (
    EmotionComponentV1,
    EmotionEpisodeV1,
    EventAppraisalV1,
    MoodStateV1,
    NeedStateV1,
    RegulationDecisionV1,
    SelfAffectStateV2,
    UserAffectObservationV1,
)


_AFFECTIVE_RAW_MATERIAL_KEYS = frozenset(
    {
        "assistant_reply", "audio", "image", "model_chain", "raw_audio",
        "raw_image", "raw_text", "transcript", "user_message", "video",
    }
)
_AFFECTIVE_COMPONENT_LIMIT = 8
_AFFECTIVE_EPISODE_LIMIT = 4


def _apply_affective_observation(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """记录隐私安全的用户观察，不改变组件、mood、needs 或 self-affect revision。"""
    _reject_raw_affective_material(payload)
    observation = _observation_from_mapping(_mapping(payload.get("observation")))
    _assert_affective_subject(
        observation.relationship_subject_id,
        state=state,
        field_name="observation.relationship_subject_id",
    )
    if observation.observed_at_ms > now_ms:
        raise ValueError("observation.observed_at_ms cannot be after command time")

    # The observation has its own bounded head. It does not change
    # affective components, mood, needs or the self-affect revision.
    # Only a later appraised event command may cross that boundary.
    observation_head = observation.as_dict()
    observations = _mapping(state.get("observations"))
    observations.update(
        {
            "last_user_affect": observation_head,
            "last_user_affect_at_ms": observation.observed_at_ms,
        }
    )
    state["observations"] = observations
    affective = _mapping(state.get("affective"))
    affective["last_observation"] = observation_head
    state["affective"] = affective


def _apply_affective_appraisal(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """原子地打开 appraisal episode，并在存在时绑定其首个 regulation。

    appraisal、episode、regulation 必须同属 aggregate、时间不在命令之后且引用
    相互对应；否则拒绝形成没有因果出处或无法被 Surface 约束的情感迁移。
    """
    _reject_raw_affective_material(payload)
    appraisal = _appraisal_from_mapping(_mapping(payload.get("appraisal")))
    episode = _episode_from_mapping(_mapping(payload.get("episode")))
    regulation_payload = _mapping(payload.get("regulation"))
    regulation = (
        _regulation_from_mapping(regulation_payload)
        if regulation_payload
        else None
    )
    _assert_affective_subject(
        appraisal.relationship_subject_id,
        state=state,
        field_name="appraisal.relationship_subject_id",
    )
    _assert_affective_subject(
        episode.relationship_subject_id,
        state=state,
        field_name="episode.relationship_subject_id",
    )
    if episode.appraisal_id != appraisal.appraisal_id:
        raise ValueError("episode.appraisal_id must match appraisal.appraisal_id")
    if episode.opened_at_ms > now_ms:
        raise ValueError("episode.opened_at_ms cannot be after command time")
    if not episode.unresolved:
        raise ValueError("a newly appraised episode must be unresolved")
    if regulation is not None:
        _assert_affective_subject(
            regulation.relationship_subject_id,
            state=state,
            field_name="regulation.relationship_subject_id",
        )
        if regulation.decided_at_ms > now_ms:
            raise ValueError("regulation.decided_at_ms cannot be after command time")
        if regulation.episode_id != episode.episode_id:
            raise ValueError("appraisal regulation must bind the opened episode")

    affective = _mapping(state.get("affective"))
    _decay_affective_episodes(affective, now_ms=now_ms)
    active_rows = list(affective.get("active_episodes") or [])
    if any(str(row.get("episode_id") or "") == episode.episode_id for row in active_rows):
        raise ValueError("episode_id is already active")
    active_rows.append(_episode_state_from_contract(episode, now_ms=now_ms))
    # The opening regulation is atomically bound to this episode.  When the
    # bounded head is already full, retain the new causal episode and evict the
    # weakest older one rather than persisting a regulation that points outside
    # the visible self-affect head.
    affective["active_episodes"] = _bounded_episode_rows(
        active_rows,
        pinned_episode_id=episode.episode_id if regulation is not None else None,
    )
    affective["last_appraisal"] = appraisal.as_dict()
    if regulation is not None:
        # The initial regulation is carried atomically with the causal
        # appraisal.  This avoids a crash-visible self-affect transition that
        # has no durable expression boundary for Surface to consume.
        affective["last_regulation"] = regulation.as_dict()
    affective["last_integrated_ms"] = now_ms
    affective["revision"] = _integer(affective.get("revision")) + 1
    _apply_appraisal_needs(affective, appraisal=appraisal, now_ms=now_ms)
    _refresh_affective_head(affective, now_ms=now_ms, mood_weight=0.34)
    state["affective"] = affective

    # This is a read-compatible legacy appraisal projection, not a second
    # self-affect writer. The canonical mixed components remain exclusively
    # under state.affective.
    legacy_appraisal = _mapping(state.get("appraisal"))
    legacy_appraisal.update(
        {
            "event_type": appraisal.event_kind,
            "salience": round(
                max(
                    appraisal.relationship_significance,
                    appraisal.boundary_relevance,
                    appraisal.risk,
                ),
                4,
            ),
            "valence": appraisal.goal_congruence,
            "uncertainty": appraisal.uncertainty,
            "appraisal_id": appraisal.appraisal_id,
            "appraised_at_ms": now_ms,
        }
    )
    state["appraisal"] = legacy_appraisal


def _apply_affective_time_advance(
    state: dict[str, Any],
    *,
    now_ms: int,
) -> None:
    """按命令时间衰减 active episode 并推进 needs；无变化时不虚增 revision。"""
    affective = _mapping(state.get("affective"))
    changed = _decay_affective_episodes(affective, now_ms=now_ms)
    previous_integrated = _integer(affective.get("last_integrated_ms"))
    elapsed_ms = max(0, now_ms - previous_integrated) if previous_integrated else 0
    if elapsed_ms:
        affective["last_integrated_ms"] = now_ms
        _advance_affective_needs(affective, elapsed_ms=elapsed_ms, now_ms=now_ms)
        _refresh_affective_head(
            affective,
            now_ms=now_ms,
            mood_weight=min(0.45, elapsed_ms / 7_200_000.0),
        )
        changed = True
    if changed:
        affective["revision"] = _integer(affective.get("revision")) + 1
    state["affective"] = affective


def _apply_affective_episode_resolution(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """用可追溯 resolution 引用关闭仍活跃的 episode，不能关闭不存在的历史项。"""
    _reject_raw_affective_material(payload)
    episode_id = _required_text(payload.get("episode_id"), "episode_id", limit=240)
    resolution_ref = _required_text(
        payload.get("resolution_ref"),
        "resolution_ref",
        limit=512,
    )
    affective = _mapping(state.get("affective"))
    _decay_affective_episodes(affective, now_ms=now_ms)
    active_rows = list(affective.get("active_episodes") or [])
    remaining = [
        row
        for row in active_rows
        if str(row.get("episode_id") or "") != episode_id
    ]
    if len(remaining) == len(active_rows):
        raise ValueError("episode_id is not active")
    affective["active_episodes"] = remaining
    affective["last_resolution"] = {
        "episode_id": episode_id,
        "resolution_ref": resolution_ref,
        "resolved_at_ms": now_ms,
    }
    affective["last_integrated_ms"] = now_ms
    affective["revision"] = _integer(affective.get("revision")) + 1
    _refresh_affective_head(affective, now_ms=now_ms, mood_weight=0.28)
    state["affective"] = affective


def _apply_affective_regulation(
    state: dict[str, Any],
    *,
    payload: Mapping[str, Any],
    now_ms: int,
) -> None:
    """写入最近调节决定；若绑定 episode，必须仍在 canonical 活跃集合中。"""
    _reject_raw_affective_material(payload)
    decision = _regulation_from_mapping(_mapping(payload.get("regulation")))
    _assert_affective_subject(
        decision.relationship_subject_id,
        state=state,
        field_name="regulation.relationship_subject_id",
    )
    if decision.decided_at_ms > now_ms:
        raise ValueError("regulation.decided_at_ms cannot be after command time")
    affective = _mapping(state.get("affective"))
    active_ids = {
        str(row.get("episode_id") or "")
        for row in list(affective.get("active_episodes") or [])
    }
    if decision.episode_id is not None and decision.episode_id not in active_ids:
        raise ValueError("regulation.episode_id must reference an active episode")
    affective["last_regulation"] = decision.as_dict()
    affective["last_integrated_ms"] = now_ms
    affective["revision"] = _integer(affective.get("revision")) + 1
    state["affective"] = affective


def _normalize_affective_head(
    raw: Any,
    *,
    person_id: str,
    now_ms: int,
) -> dict[str, Any]:
    """把快照中的 affective head 规范化为契约对象并删除已失效 episode。

    该步骤会校验 subject 一致性、组件上限和 head 结构；不会把 ``now_ms`` 写入
    新空 head，保留“尚无情感事件”的历史事实。
    """
    source = _mapping(raw)
    default_mood = MoodStateV1(
        label="calm",
        valence=0.18,
        arousal=0.24,
        energy=0.62,
        updated_at_ms=0,
    )
    default_needs = NeedStateV1(
        connection=0.12,
        rest=0.12,
        autonomy=0.25,
        play=0.18,
        repair=0.0,
        safety=0.08,
        updated_at_ms=0,
    )
    mood = _mood_from_mapping(_mapping(source.get("mood")), default=default_mood)
    needs = _needs_from_mapping(_mapping(source.get("needs")), default=default_needs)
    active_rows: list[dict[str, Any]] = []
    raw_episodes = source.get("active_episodes") or []
    if not isinstance(raw_episodes, list):
        raise ValueError("affective.active_episodes must be a list")
    for raw_episode in raw_episodes:
        if not isinstance(raw_episode, Mapping):
            raise ValueError("affective.active_episodes must contain objects")
        episode = _episode_from_mapping(_mapping(raw_episode))
        if episode.relationship_subject_id != str(person_id):
            raise ValueError("affective episode relationship subject drift")
        if episode.unresolved:
            active_rows.append(
                _episode_state_from_contract(
                    episode,
                    now_ms=max(
                        episode.opened_at_ms,
                        _integer(_mapping(raw_episode).get("last_decay_at_ms")),
                    ),
                )
            )
    active_rows = _bounded_episode_rows(active_rows)
    last_regulation = _regulation_from_mapping_or_none(
        _mapping(source.get("last_regulation"))
    )
    if (
        last_regulation is not None
        and last_regulation.relationship_subject_id != str(person_id)
    ):
        raise ValueError("affective regulation relationship subject drift")
    components = _components_from_episode_rows(active_rows)
    validated = SelfAffectStateV2(
        relationship_subject_id=str(person_id),
        revision=max(0, _integer(source.get("revision"))),
        components=tuple(_component_from_mapping(row) for row in components),
        active_episodes=tuple(
            _episode_from_mapping(row) for row in active_rows
        ),
        mood=mood,
        needs=needs,
        last_integrated_ms=max(0, _integer(source.get("last_integrated_ms"))),
        last_regulation=last_regulation,
    )
    normalized = validated.as_dict()
    normalized["active_episodes"] = active_rows
    normalized["last_observation"] = _safe_observation_head(
        _mapping(source.get("last_observation"))
    )
    normalized["last_appraisal"] = _safe_appraisal_head(
        _mapping(source.get("last_appraisal"))
    )
    normalized["last_resolution"] = _safe_resolution_head(
        _mapping(source.get("last_resolution"))
    )
    # now_ms is intentionally not written into a fresh empty head. That
    # preserves the fact that no affective event has yet been integrated.
    return normalized


def _observation_from_mapping(raw: Mapping[str, Any]) -> UserAffectObservationV1:
    if not raw:
        raise ValueError("observation is required")
    return UserAffectObservationV1(
        observation_id=raw.get("observation_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        modality=raw.get("modality"),
        emotion_distribution=_mapping(raw.get("emotion_distribution")),
        valence=raw.get("valence"),
        arousal=raw.get("arousal"),
        confidence=raw.get("confidence"),
        observed_at_ms=raw.get("observed_at_ms"),
        expires_at_ms=raw.get("expires_at_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        conflict_refs=raw.get("conflict_refs") or (),
    )


def _appraisal_from_mapping(raw: Mapping[str, Any]) -> EventAppraisalV1:
    if not raw:
        raise ValueError("appraisal is required")
    return EventAppraisalV1(
        appraisal_id=raw.get("appraisal_id"),
        workspace_id=raw.get("workspace_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        session_id=raw.get("session_id"),
        event_kind=raw.get("event_kind"),
        target_ref=raw.get("target_ref"),
        goal_congruence=raw.get("goal_congruence"),
        responsibility=raw.get("responsibility"),
        controllability=raw.get("controllability"),
        relationship_significance=raw.get("relationship_significance"),
        boundary_relevance=raw.get("boundary_relevance"),
        risk=raw.get("risk"),
        uncertainty=raw.get("uncertainty"),
        evidence_refs=raw.get("evidence_refs") or (),
        observation_refs=raw.get("observation_refs") or (),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _component_from_mapping(raw: Mapping[str, Any]) -> EmotionComponentV1:
    return EmotionComponentV1(
        emotion=raw.get("emotion"),
        intensity=raw.get("intensity"),
        valence=raw.get("valence"),
        arousal=raw.get("arousal"),
        action_tendency=raw.get("action_tendency") or "observe",
    )


def _episode_from_mapping(raw: Mapping[str, Any]) -> EmotionEpisodeV1:
    if not raw:
        raise ValueError("episode is required")
    components = raw.get("components")
    if not isinstance(components, list):
        raise ValueError("episode.components must be a list")
    return EmotionEpisodeV1(
        episode_id=raw.get("episode_id"),
        appraisal_id=raw.get("appraisal_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        trigger_ref=raw.get("trigger_ref"),
        components=tuple(
            _component_from_mapping(_mapping(component))
            for component in components
        ),
        opened_at_ms=raw.get("opened_at_ms"),
        decay_half_life_ms=raw.get("decay_half_life_ms"),
        evidence_refs=raw.get("evidence_refs") or (),
        unresolved=bool(raw.get("unresolved", True)),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _regulation_from_mapping(raw: Mapping[str, Any]) -> RegulationDecisionV1:
    if not raw:
        raise ValueError("regulation is required")
    return RegulationDecisionV1(
        regulation_id=raw.get("regulation_id"),
        relationship_subject_id=raw.get("relationship_subject_id"),
        mode=raw.get("mode"),
        reason_refs=raw.get("reason_refs") or (),
        decided_at_ms=raw.get("decided_at_ms"),
        episode_id=raw.get("episode_id"),
        self_id=raw.get("self_id") or "natsume_ai",
    )


def _regulation_from_mapping_or_none(
    raw: Mapping[str, Any],
) -> RegulationDecisionV1 | None:
    return None if not raw else _regulation_from_mapping(raw)


def _mood_from_mapping(
    raw: Mapping[str, Any],
    *,
    default: MoodStateV1,
) -> MoodStateV1:
    if not raw:
        return default
    return MoodStateV1(
        label=raw.get("label") or default.label,
        valence=raw.get("valence", default.valence),
        arousal=raw.get("arousal", default.arousal),
        energy=raw.get("energy", default.energy),
        updated_at_ms=raw.get("updated_at_ms", default.updated_at_ms),
    )


def _needs_from_mapping(
    raw: Mapping[str, Any],
    *,
    default: NeedStateV1,
) -> NeedStateV1:
    if not raw:
        return default
    return NeedStateV1(
        connection=raw.get("connection", default.connection),
        rest=raw.get("rest", default.rest),
        autonomy=raw.get("autonomy", default.autonomy),
        play=raw.get("play", default.play),
        repair=raw.get("repair", default.repair),
        safety=raw.get("safety", default.safety),
        updated_at_ms=raw.get("updated_at_ms", default.updated_at_ms),
    )


def _episode_state_from_contract(
    episode: EmotionEpisodeV1,
    *,
    now_ms: int,
) -> dict[str, Any]:
    return {
        **episode.as_dict(),
        "last_decay_at_ms": max(episode.opened_at_ms, int(now_ms)),
    }


def _bounded_episode_rows(
    rows: list[Mapping[str, Any]],
    *,
    pinned_episode_id: str | None = None,
) -> list[dict[str, Any]]:
    """按峰值与时间确定性裁剪 episode；携带首 regulation 的新 episode 不能被挤掉。"""
    normalized = [dict(row) for row in rows]
    normalized.sort(
        key=lambda row: (
            -_episode_peak_intensity(row),
            -_integer(row.get("opened_at_ms")),
            str(row.get("episode_id") or ""),
        )
    )
    bounded = normalized[:_AFFECTIVE_EPISODE_LIMIT]
    if (
        pinned_episode_id
        and normalized
        and not any(row.get("episode_id") == pinned_episode_id for row in bounded)
    ):
        pinned = next(
            (
                row
                for row in normalized
                if row.get("episode_id") == pinned_episode_id
            ),
            None,
        )
        if pinned is not None:
            bounded = [*bounded[: max(0, _AFFECTIVE_EPISODE_LIMIT - 1)], pinned]
            bounded.sort(
                key=lambda row: (
                    -_episode_peak_intensity(row),
                    -_integer(row.get("opened_at_ms")),
                    str(row.get("episode_id") or ""),
                )
            )
    return bounded


def _episode_peak_intensity(row: Mapping[str, Any]) -> float:
    components = row.get("components")
    if not isinstance(components, list):
        return 0.0
    return max(
        [_clamp(_mapping(component).get("intensity")) for component in components]
        or [0.0]
    )


def _components_from_episode_rows(
    rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """从活跃 episode 聚合有限组件，每种 emotion 只保留最强且排序稳定的一项。"""
    by_emotion: dict[str, EmotionComponentV1] = {}
    for row in rows:
        components = row.get("components")
        if not isinstance(components, list):
            raise ValueError("affective episode components must be a list")
        for raw_component in components:
            component = _component_from_mapping(_mapping(raw_component))
            current = by_emotion.get(component.emotion)
            if current is None or (
                component.intensity,
                component.action_tendency,
            ) > (
                current.intensity,
                current.action_tendency,
            ):
                by_emotion[component.emotion] = component
    ordered = sorted(
        by_emotion.values(),
        key=lambda component: (-component.intensity, component.emotion),
    )[:_AFFECTIVE_COMPONENT_LIMIT]
    return [component.as_dict() for component in ordered]


def _decay_affective_episodes(
    affective: dict[str, Any],
    *,
    now_ms: int,
) -> bool:
    """按各 episode 半衰期衰减强度，低于阈值后移除以限制快照与提示面。"""
    changed = False
    active_rows: list[dict[str, Any]] = []
    for raw_row in list(affective.get("active_episodes") or []):
        row = dict(raw_row)
        last_decay_at_ms = max(
            _integer(row.get("opened_at_ms")),
            _integer(row.get("last_decay_at_ms")),
        )
        elapsed_ms = max(0, int(now_ms) - last_decay_at_ms)
        if elapsed_ms:
            half_life_ms = max(1_000, _integer(row.get("decay_half_life_ms"), 60_000))
            factor = 0.5 ** (elapsed_ms / half_life_ms)
            components: list[dict[str, Any]] = []
            for raw_component in list(row.get("components") or []):
                component = _component_from_mapping(_mapping(raw_component))
                components.append(
                    {
                        **component.as_dict(),
                        "intensity": round(component.intensity * factor, 6),
                    }
                )
            row["components"] = components
            row["last_decay_at_ms"] = int(now_ms)
            changed = True
        if _episode_peak_intensity(row) >= 0.015:
            active_rows.append(row)
        else:
            changed = True
    bounded = _bounded_episode_rows(active_rows)
    if bounded != list(affective.get("active_episodes") or []):
        changed = True
    affective["active_episodes"] = bounded
    return changed


def _apply_appraisal_needs(
    affective: dict[str, Any],
    *,
    appraisal: EventAppraisalV1,
    now_ms: int,
) -> None:
    """由已验证 appraisal 有界调整修复、安全、联系与自主 needs。"""
    needs = _mapping(affective.get("needs"))
    needs["repair"] = round(
        _clamp(
            max(
                _float(needs.get("repair")) * 0.82,
                appraisal.boundary_relevance * 0.68 + appraisal.risk * 0.22,
            )
        ),
        6,
    )
    needs["safety"] = round(
        _clamp(
            max(
                _float(needs.get("safety")) * 0.88,
                appraisal.risk * 0.72 + appraisal.uncertainty * 0.16,
            )
        ),
        6,
    )
    needs["connection"] = round(
        _clamp(
            _float(needs.get("connection"), 0.12) * 0.92
            + appraisal.relationship_significance * 0.08
        ),
        6,
    )
    needs["autonomy"] = round(
        _clamp(
            _float(needs.get("autonomy"), 0.25) * 0.94
            + appraisal.boundary_relevance * 0.08
        ),
        6,
    )
    needs["updated_at_ms"] = now_ms
    affective["needs"] = needs


def _advance_affective_needs(
    affective: dict[str, Any],
    *,
    elapsed_ms: int,
    now_ms: int,
) -> None:
    """随经过时间平滑推进 needs，最大时长受限以避免离线跳跃放大状态。"""
    hours = min(72.0, max(0, elapsed_ms) / 3_600_000.0)
    needs = _mapping(affective.get("needs"))
    needs["rest"] = round(
        _clamp(_float(needs.get("rest"), 0.12) + hours * 0.012),
        6,
    )
    needs["connection"] = round(
        _clamp(_float(needs.get("connection"), 0.12) + hours * 0.006),
        6,
    )
    needs["repair"] = round(
        _clamp(_float(needs.get("repair")) * (0.985 ** hours)),
        6,
    )
    needs["safety"] = round(
        _clamp(_float(needs.get("safety")) * (0.99 ** hours)),
        6,
    )
    needs["updated_at_ms"] = now_ms
    affective["needs"] = needs


def _refresh_affective_head(
    affective: dict[str, Any],
    *,
    now_ms: int,
    mood_weight: float,
) -> None:
    """由 active episode 重新计算组件与 mood；mood 是投影而非独立写入来源。"""
    components = _components_from_episode_rows(
        list(affective.get("active_episodes") or [])
    )
    affective["components"] = components
    mood = _mapping(affective.get("mood"))
    if components:
        total = sum(_float(component.get("intensity")) for component in components)
        target_valence = (
            sum(
                _float(component.get("intensity"))
                * _float(component.get("valence"))
                for component in components
            )
            / total
            if total
            else 0.18
        )
        target_arousal = (
            sum(
                _float(component.get("intensity"))
                * _float(component.get("arousal"))
                for component in components
            )
            / total
            if total
            else 0.24
        )
        activation = min(1.0, total / max(1, len(components)))
        label = str(components[0].get("emotion") or "calm").lower()
    else:
        target_valence = 0.18
        target_arousal = 0.24
        activation = 0.0
        label = "calm"
    weight = _clamp(mood_weight)
    mood["label"] = label
    mood["valence"] = _approach_signed(
        _float(mood.get("valence"), 0.18),
        target_valence,
        weight,
    )
    mood["arousal"] = _approach(
        _float(mood.get("arousal"), 0.24),
        target_arousal,
        weight,
    )
    mood["energy"] = round(
        _clamp(
            _approach(
                _float(mood.get("energy"), 0.62),
                max(0.15, 0.62 - activation * 0.18),
                min(0.5, weight * 0.75),
            )
        ),
        6,
    )
    mood["updated_at_ms"] = now_ms
    affective["mood"] = mood


def _safe_observation_head(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {} if not raw else _observation_from_mapping(raw).as_dict()


def _safe_appraisal_head(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {} if not raw else _appraisal_from_mapping(raw).as_dict()


def _safe_resolution_head(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    return {
        "episode_id": _required_text(raw.get("episode_id"), "last_resolution.episode_id", limit=240),
        "resolution_ref": _required_text(
            raw.get("resolution_ref"),
            "last_resolution.resolution_ref",
            limit=512,
        ),
        "resolved_at_ms": max(0, _integer(raw.get("resolved_at_ms"))),
    }


def _assert_affective_subject(
    relationship_subject_id: str,
    *,
    state: Mapping[str, Any],
    field_name: str,
) -> None:
    """拒绝把另一关系主体的观察、appraisal 或 regulation 混入本 aggregate。"""
    identity = _mapping(state.get("identity"))
    expected = str(identity.get("relationship_subject_id") or "")
    if str(relationship_subject_id) != expected:
        raise ValueError(f"{field_name} must match the aggregate relationship subject")


def _reject_raw_affective_material(value: Any) -> None:
    """递归拒绝原文、媒体与模型链，确保 affective journal 只保存引用化事实。"""
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower()
            if key in _AFFECTIVE_RAW_MATERIAL_KEYS:
                raise ValueError(f"affective command cannot contain {key}")
            _reject_raw_affective_material(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_raw_affective_material(nested)
