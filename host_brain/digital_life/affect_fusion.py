"""Durable, privacy-minimized fusion from user affect to Natsume appraisal.

DL-02 deliberately keeps three concerns apart:

* observations describe a relationship subject and may be uncertain;
* appraisal describes Natsume's interpretation of those observations; and
* the Life reducer alone changes Natsume's canonical self-affect.

There is no raw transcript, audio, image, model chain, or sensor payload in
the returned context or in a Life command.  Opaque, content-addressed refs
retain traceability without turning the affect journal into a conversation
archive.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import time
from typing import Any

from host_brain.core.emotion_constants import get_vad, normalize_emotion_tag
from host_brain.digital_life.service import LifeCommandService
from host_brain.digital_life.reader import LifeSnapshotReader
from natsume_contracts.life import (
    EmotionComponentV1,
    EmotionEpisodeV1,
    EventAppraisalV1,
    LifeCommand,
    LifeCommandReceipt,
    RegulationDecisionV1,
    UserAffectObservationV1,
)


_RAW_MATERIAL_KEYS = frozenset(
    {
        "assistant_reply",
        "audio",
        "audio_base64",
        "image",
        "image_base64",
        "model_chain",
        "raw_audio",
        "raw_image",
        "raw_text",
        "transcript",
        "user_message",
        "video",
    }
)
_MODALITY_ALIASES = {
    "TEXT": "TEXT",
    "SELF_REPORT": "SELF_REPORT",
    "SELF-REPORT": "SELF_REPORT",
    "ACOUSTIC": "ACOUSTIC",
    "VOICE": "ACOUSTIC",
    "AUDIO": "ACOUSTIC",
    "VISUAL": "VISUAL",
    "FACE": "VISUAL",
    "CAMERA": "VISUAL",
}
_TTL_MS = {
    "TEXT": 30_000,
    "SELF_REPORT": 30_000,
    "ACOUSTIC": 5_000,
    "VISUAL": 3_000,
}
_DISTRESS_TAGS = frozenset({"SAD", "ANXIOUS", "ANGRY", "FRUSTRATED"})
_POSITIVE_TAGS = frozenset({"HAPPY", "EXCITED", "GRATEFUL", "LOVING"})
_SPACE_MARKERS = ("别说了", "给我一点空间", "想一个人待会", "不要再问", "别烦")
_REPAIR_MARKERS = ("对不起", "抱歉", "误会", "不是那个意思", "我刚才说重了")
_BOUNDARY_MARKERS = ("不要这样", "别这样", "不舒服", "越界", "停止")
_NATSUME_HARM_MARKERS = (
    "伤到我",
    "伤害我",
    "让我受伤",
    "让我很受伤",
    "让我难过",
    "让我很难过",
    "说得太伤人",
    "hurt me",
    "made me feel hurt",
    "made me sad",
    "what you said hurt",
)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _text(value: Any, *, default: str = "", limit: int = 240) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return default
    return normalized[:limit]


def _clamp(value: Any, *, default: float = 0.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return round(max(0.0, min(1.0, resolved)), 6)


def _signed(value: Any, *, default: float = 0.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = float(default)
    return round(max(-1.0, min(1.0, resolved)), 6)


def _opaque_ref(prefix: str, value: Any) -> str:
    return f"{prefix}://sha256/{_digest(value)}"


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _safe_sequence(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _contains_raw_material(value: Any) -> bool:
    if isinstance(value, Mapping):
        for raw_key, nested in value.items():
            if str(raw_key or "").strip().lower() in _RAW_MATERIAL_KEYS:
                return True
            if _contains_raw_material(nested):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_raw_material(item) for item in value)
    return False


def _normalise_modality(value: Any) -> str | None:
    key = _text(value, limit=48).upper().replace(" ", "_")
    return _MODALITY_ALIASES.get(key)


def _distribution(value: Any, *, fallback_tag: str = "NEUTRAL") -> dict[str, float]:
    source = _safe_mapping(value)
    result: dict[str, float] = {}
    for raw_tag in sorted(source, key=lambda item: str(item)):
        tag = normalize_emotion_tag(str(raw_tag))
        if not tag:
            continue
        result[tag] = _clamp(source[raw_tag])
    if result:
        return result
    return {normalize_emotion_tag(fallback_tag) or "NEUTRAL": 1.0}


def _distribution_vad(distribution: Mapping[str, float]) -> tuple[float, float]:
    total = sum(_clamp(weight) for weight in distribution.values())
    if total <= 0:
        return 0.0, 0.5
    valence = 0.0
    arousal = 0.0
    for tag, weight in distribution.items():
        tag_valence, tag_arousal, _ = get_vad(str(tag))
        normalized = _clamp(weight) / total
        valence += tag_valence * normalized
        # The legacy VAD catalog uses [-1, 1]; public affect contracts use
        # [0, 1] arousal to avoid a second ambiguous convention.
        arousal += ((tag_arousal + 1.0) / 2.0) * normalized
    return _signed(valence), _clamp(arousal, default=0.5)


def _top_tag(observations: Sequence[UserAffectObservationV1]) -> str:
    weighted: dict[str, float] = {}
    for observation in observations:
        for tag, probability in observation.emotion_distribution.items():
            weighted[tag] = weighted.get(tag, 0.0) + (
                _clamp(probability) * observation.confidence
            )
    if not weighted:
        return "NEUTRAL"
    return sorted(weighted.items(), key=lambda item: (-item[1], item[0]))[0][0]


@dataclass(frozen=True, slots=True)
class AffectiveScope:
    """Separate user, session and workspace identity from the Life key."""

    workspace_id: str
    relationship_subject_id: str
    session_id: str
    aggregate_id: str

    @classmethod
    def from_request(cls, request_body: Any) -> "AffectiveScope":
        context = _safe_mapping(getattr(request_body, "emotion_context", None))
        workspace_id = (
            _text(getattr(request_body, "workspace_id", None))
            or _text(context.get("workspace_id"))
            or "workspace::default"
        )
        relationship_subject_id = _text(
            getattr(request_body, "person_id", None),
            default="default",
        )
        session_id = _text(
            getattr(request_body, "session_id", None),
            default="session::unknown",
        )
        aggregate_seed = {
            "schema": "affective-life-scope.v1",
            "workspace_id": workspace_id,
            "relationship_subject_id": relationship_subject_id,
            # Affective observations are intentionally session-bounded.
            # Relationship continuity gets its own cross-session authority in
            # DL-03; sharing this mutable self-affect head across concurrent
            # sessions would violate the input scope boundary.
            "session_id": session_id,
        }
        return cls(
            workspace_id=workspace_id,
            relationship_subject_id=relationship_subject_id,
            session_id=session_id,
            aggregate_id=f"affect::{_digest(aggregate_seed)[:40]}",
        )


@dataclass(frozen=True, slots=True)
class AffectFusionResult:
    """Safe hand-off from ingress to prompt/governance consumers."""

    scope: AffectiveScope
    observations: tuple[UserAffectObservationV1, ...]
    active_observations: tuple[UserAffectObservationV1, ...]
    conflict: bool
    path: str
    candidate_count: int
    retry_count: int
    appraisal_id: str | None
    self_affect: Mapping[str, Any]
    receipts: tuple[LifeCommandReceipt, ...]
    reason_codes: tuple[str, ...]
    relationship_state: Mapping[str, Any] | None = None
    relationship_aftereffect: Mapping[str, Any] | None = None
    companion_profile: Mapping[str, Any] | None = None

    def as_emotion_context(self) -> dict[str, Any]:
        components = list(_safe_mapping(self.self_affect).get("components") or [])
        top_component = _safe_mapping(components[0]) if components else {}
        self_tag = _self_component_to_transport_tag(top_component.get("emotion"))
        intensity = _clamp(top_component.get("intensity"), default=0.18)
        mood = _safe_mapping(self.self_affect).get("mood")
        mood_mapping = _safe_mapping(mood)
        return {
            "_affective_life_internal": True,
            "affective_life": {
                "schema_version": "affective_turn_context.v1",
                "self_id": "natsume_ai",
                "workspace_id": self.scope.workspace_id,
                "relationship_subject_id": self.scope.relationship_subject_id,
                "session_id": self.scope.session_id,
                "aggregate_id": self.scope.aggregate_id,
                "self_affect": dict(self.self_affect),
                "user_observations": [item.as_dict() for item in self.active_observations],
                "conflict": self.conflict,
                "path": self.path,
                "candidate_count": self.candidate_count,
                "retry_count": self.retry_count,
                "appraisal_id": self.appraisal_id,
                "reason_codes": list(self.reason_codes),
                "receipt_refs": [
                    f"life-command://{receipt.command_id}"
                    for receipt in self.receipts
                ],
                "relationship_life": {
                    "schema_version": "relationship_turn_context.v1",
                    "workspace_id": self.scope.workspace_id,
                    "relationship_subject_id": self.scope.relationship_subject_id,
                    "state": dict(self.relationship_state or {}),
                    "aftereffect": dict(self.relationship_aftereffect or {}),
                    # The profile is a canonical relationship-scoped
                    # expression preference head. It is never supplied by an
                    # external ChatRequest and cannot change self-affect.
                    "companion_profile": dict(self.companion_profile or {}),
                },
            },
            # These bridge keys are intentionally derived from Natsume's
            # canonical self-affect, never from the user's observation tag.
            "mood_state": _text(mood_mapping.get("label"), default="calm"),
            "emotion_tag": self_tag,
            "emotion_intensity": intensity,
            "emotion_inference_source": "affective_life_fusion",
            "emotion_inference_confidence": 1.0,
            "emotion_inference_degraded": self.path == "FAST_FALLBACK",
            "emotion_bootstrap_reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True, slots=True)
class _ObservationDraft:
    modality: str
    distribution: Mapping[str, float]
    valence: float
    arousal: float
    confidence: float
    observed_at_ms: int
    expires_at_ms: int
    evidence_ref: str
    source_order: int


@dataclass(frozen=True, slots=True)
class _AppraisalCandidate:
    kind: str
    score: float
    goal_congruence: float
    responsibility: float
    controllability: float
    relationship_significance: float
    boundary_relevance: float
    risk: float
    uncertainty: float
    components: tuple[EmotionComponentV1, ...]
    action: str


class AffectFusionService:
    """Fuse observations and submit only canonical Life commands.

    The service is intentionally app-local and owns no mutable affect cache:
    every request reopens its exact scoped aggregate after writing.  That
    makes restart and process boundaries semantics-preserving.
    """

    def __init__(
        self,
        *,
        command_service_provider: Callable[[], LifeCommandService],
        snapshot_reader_provider: Callable[[], LifeSnapshotReader],
        relationship_lifecycle_service_provider: Callable[[], Any] | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._command_service_provider = command_service_provider
        self._snapshot_reader_provider = snapshot_reader_provider
        self._relationship_lifecycle_service_provider = (
            relationship_lifecycle_service_provider
        )
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def resolve_request(
        self,
        *,
        request_body: Any,
        request_id: str | None,
    ) -> AffectFusionResult:
        scope = AffectiveScope.from_request(request_body)
        now_ms = max(0, int(self._clock_ms()))
        stable_request_id = _text(request_id) or _digest(
            {
                "scope": scope.aggregate_id,
                "session": scope.session_id,
                "message": _text(getattr(request_body, "message", None), limit=8_000),
            }
        )[:32]
        relationship_state: Mapping[str, Any] | None = None
        relationship_aftereffect: Mapping[str, Any] | None = None
        companion_profile: Mapping[str, Any] | None = None
        relationship_reason_code = ""
        relationship_service = self._relationship_lifecycle_service()
        if relationship_service is not None:
            record_followup = getattr(relationship_service, "record_followup", None)
            if not callable(record_followup):
                raise RuntimeError("RELATIONSHIP_LIFECYCLE_SERVICE_NOT_INJECTED")
            relationship_result = record_followup(
                request_body=request_body,
                request_id=stable_request_id,
            )
            relationship_reason_code = str(
                getattr(relationship_result, "reason_code", "") or ""
            ).strip()
            raw_aftereffect = getattr(relationship_result, "aftereffect", None)
            if callable(getattr(raw_aftereffect, "as_dict", None)):
                relationship_aftereffect = raw_aftereffect.as_dict()
            relationship_scope = getattr(relationship_result, "scope", None)
            aggregate_id = str(
                getattr(relationship_scope, "aggregate_id", "") or ""
            ).strip()
            if aggregate_id:
                relationship_snapshot = self._require_snapshot_reader().get_snapshot(
                    aggregate_id
                )
                relationship_state = _safe_mapping(
                    _safe_mapping(relationship_snapshot.state).get("relationship_v2")
                )
                companion_profile = _safe_mapping(
                    _safe_mapping(relationship_snapshot.state).get(
                        "companion_profile"
                    )
                )
        drafts, source_reasons = _collect_observation_drafts(
            request_body=request_body,
            now_ms=now_ms,
        )
        observations, conflict = _materialize_observations(
            scope=scope,
            request_id=stable_request_id,
            drafts=drafts,
        )
        active = tuple(
            item for item in observations if item.expires_at_ms >= now_ms
        )
        service = self._require_command_service()
        receipts: list[LifeCommandReceipt] = []
        for index, observation in enumerate(observations):
            receipts.append(
                self._submit(
                    service=service,
                    scope=scope,
                    request_id=stable_request_id,
                    ordinal=index,
                    command_type="affective_observation_recorded",
                    payload={"observation": observation.as_dict()},
                    created_at_ms=observation.observed_at_ms,
                )
            )

        candidates = _build_appraisal_candidates(
            observations=active,
            conflict=conflict,
            message=_text(getattr(request_body, "message", None), limit=8_000),
        )
        # The deep policy is bounded by construction: at most two deterministic
        # candidates and no hidden model loop. A future model-backed selector
        # may use one retry but must retain this same public ceiling.
        candidates = tuple(candidates[:2])
        retry_count = 0
        appraisal_id: str | None = None
        path = "FAST"
        reason_codes = list(source_reasons)
        if relationship_reason_code:
            reason_codes.append(relationship_reason_code)
        if candidates:
            path = "DEEP"
            selected = candidates[0]
            try:
                appraisal_id, appraisal_receipts = self._submit_appraisal(
                    service=service,
                    scope=scope,
                    request_id=stable_request_id,
                    candidate=selected,
                    observations=active,
                    created_at_ms=now_ms,
                )
                receipts.extend(appraisal_receipts)
            except (TypeError, ValueError):
                # Do not retry an invalid candidate indefinitely. The turn
                # still has its durable observations and explicitly takes the
                # uncertainty-aware fast path.
                path = "FAST_FALLBACK"
                reason_codes.append("APPRAISAL_CANDIDATE_REJECTED")

        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        affective = _safe_mapping(snapshot.state.get("affective"))
        return AffectFusionResult(
            scope=scope,
            observations=tuple(observations),
            active_observations=active,
            conflict=conflict,
            path=path,
            candidate_count=len(candidates),
            retry_count=retry_count,
            appraisal_id=appraisal_id,
            self_affect=affective,
            receipts=tuple(receipts),
            reason_codes=tuple(dict.fromkeys(reason_codes))[:8],
            relationship_state=relationship_state,
            relationship_aftereffect=relationship_aftereffect,
            companion_profile=companion_profile,
        )

    def _require_command_service(self) -> LifeCommandService:
        service = self._command_service_provider()
        if not isinstance(service, LifeCommandService):
            raise RuntimeError("AFFECTIVE_LIFE_COMMAND_SERVICE_NOT_INJECTED")
        return service

    def _require_snapshot_reader(self) -> LifeSnapshotReader:
        reader = self._snapshot_reader_provider()
        if not isinstance(reader, LifeSnapshotReader):
            raise RuntimeError("AFFECTIVE_LIFE_SNAPSHOT_READER_NOT_INJECTED")
        return reader

    def _relationship_lifecycle_service(self) -> Any | None:
        if self._relationship_lifecycle_service_provider is None:
            return None
        service = self._relationship_lifecycle_service_provider()
        if service is None:
            raise RuntimeError("RELATIONSHIP_LIFECYCLE_SERVICE_NOT_INJECTED")
        return service

    def _submit(
        self,
        *,
        service: LifeCommandService,
        scope: AffectiveScope,
        request_id: str,
        ordinal: int,
        command_type: str,
        payload: Mapping[str, Any],
        created_at_ms: int,
    ) -> LifeCommandReceipt:
        command_seed = {
            "schema": "affective-fusion-command.v1",
            "request_id": request_id,
            "aggregate_id": scope.aggregate_id,
            "ordinal": ordinal,
            "command_type": command_type,
            "payload": payload,
        }
        command_id = f"affect_{_digest(command_seed)[:40]}"

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
                    "source": "affect_fusion_service",
                    "workspace_id": scope.workspace_id,
                    "relationship_subject_id": scope.relationship_subject_id,
                    "session_id": scope.session_id,
                },
            )

        return service.submit_next(scope.aggregate_id, build)

    def _submit_appraisal(
        self,
        *,
        service: LifeCommandService,
        scope: AffectiveScope,
        request_id: str,
        candidate: _AppraisalCandidate,
        observations: Sequence[UserAffectObservationV1],
        created_at_ms: int,
    ) -> tuple[str, tuple[LifeCommandReceipt, LifeCommandReceipt]]:
        observation_refs = tuple(
            f"affect-observation://{item.observation_id}"
            for item in observations[:8]
        )
        appraisal_seed = {
            "schema": "event-appraisal.v1",
            "request_id": request_id,
            "kind": candidate.kind,
            "observation_refs": observation_refs,
        }
        appraisal_id = f"appraisal_{_digest(appraisal_seed)[:32]}"
        target_ref = _opaque_ref("affect-turn", appraisal_seed)
        appraisal = EventAppraisalV1(
            appraisal_id=appraisal_id,
            workspace_id=scope.workspace_id,
            relationship_subject_id=scope.relationship_subject_id,
            session_id=scope.session_id,
            event_kind=candidate.kind,
            target_ref=target_ref,
            goal_congruence=candidate.goal_congruence,
            responsibility=candidate.responsibility,
            controllability=candidate.controllability,
            relationship_significance=candidate.relationship_significance,
            boundary_relevance=candidate.boundary_relevance,
            risk=candidate.risk,
            uncertainty=candidate.uncertainty,
            evidence_refs=(target_ref,),
            observation_refs=observation_refs,
        )
        episode = EmotionEpisodeV1(
            episode_id=f"episode_{_digest({'appraisal_id': appraisal_id, 'action': candidate.action})[:32]}",
            appraisal_id=appraisal_id,
            relationship_subject_id=scope.relationship_subject_id,
            trigger_ref=target_ref,
            components=candidate.components,
            opened_at_ms=max(0, int(created_at_ms)),
            decay_half_life_ms=600_000,
            evidence_refs=(target_ref, *observation_refs[:8]),
        )
        regulation_mode = _regulation_mode_for_candidate(candidate)
        regulation_seed = {
            "schema": "regulation-decision.v1",
            "appraisal_id": appraisal_id,
            "mode": regulation_mode,
        }
        regulation = RegulationDecisionV1(
            regulation_id=f"regulation_{_digest(regulation_seed)[:32]}",
            relationship_subject_id=scope.relationship_subject_id,
            mode=regulation_mode,
            # The current-turn planner reopens this exact provenance link.
            # It must not apply an old durable regulation to a later, unrelated
            # input merely because it happens to be the latest snapshot head.
            reason_refs=(f"appraisal://{appraisal_id}", target_ref),
            decided_at_ms=max(0, int(created_at_ms)),
            # The affective head keeps only four active episodes.  An
            # appraisal can therefore be journaled even when its low-salience
            # episode is immediately pruned; binding regulation to that
            # optional active id would turn a valid current-turn decision into
            # a partial-write failure.  The immutable appraisal ref remains
            # the authoritative causal link.
            episode_id=None,
        )
        appraisal_receipt = self._submit(
            service=service,
            scope=scope,
            request_id=request_id,
            ordinal=999,
            command_type="affective_event_appraised",
            payload={"appraisal": appraisal.as_dict(), "episode": episode.as_dict()},
            created_at_ms=created_at_ms,
        )
        regulation_receipt = self._submit(
            service=service,
            scope=scope,
            request_id=request_id,
            ordinal=1_000,
            command_type="affective_regulation_decided",
            payload={"regulation": regulation.as_dict()},
            created_at_ms=created_at_ms,
        )
        return appraisal_id, (appraisal_receipt, regulation_receipt)


def _collect_observation_drafts(
    *,
    request_body: Any,
    now_ms: int,
) -> tuple[list[_ObservationDraft], list[str]]:
    context = _safe_mapping(getattr(request_body, "emotion_context", None))
    multimodal = _safe_mapping(getattr(request_body, "multimodal_observations", None))
    message = _text(getattr(request_body, "message", None), limit=8_000)
    drafts: list[_ObservationDraft] = [_text_draft(message=message, now_ms=now_ms)]
    reason_codes: list[str] = []

    explicit_tag = (
        _text(getattr(request_body, "emotion_tag", None))
        or _text(context.get("user_emotion_tag"))
        or _text(context.get("emotion_tag"))
    )
    if explicit_tag and normalize_emotion_tag(explicit_tag) != "NEUTRAL":
        drafts.append(
            _draft_from_tag(
                modality="SELF_REPORT",
                tag=explicit_tag,
                intensity=(
                    getattr(request_body, "emotion_intensity", None)
                    if getattr(request_body, "emotion_intensity", None) is not None
                    else context.get("emotion_intensity")
                ),
                confidence=(
                    context.get("user_emotion_confidence")
                    or context.get("emotion_inference_confidence")
                    or 0.78
                ),
                observed_at_ms=now_ms,
                source_order=len(drafts),
                evidence_seed={"source": "request_self_report", "tag": explicit_tag},
            )
        )

    structured: list[Mapping[str, Any]] = []
    for container in (context, multimodal):
        for key in ("user_affect_observations", "affect_observations"):
            structured.extend(_safe_sequence(container.get(key)))
    for raw in structured:
        if _contains_raw_material(raw):
            reason_codes.append("RAW_MODALITY_OMITTED")
            continue
        draft = _draft_from_structured(
            raw,
            now_ms=now_ms,
            source_order=len(drafts),
        )
        if draft is None:
            reason_codes.append("AFFECT_OBSERVATION_INVALID")
            continue
        drafts.append(draft)
    return drafts, reason_codes


def _text_draft(*, message: str, now_ms: int) -> _ObservationDraft:
    # Import lazily to avoid making the pure life package depend on Host app
    # composition. The old keyword recognizer is deliberately only a low
    # confidence observer now; it cannot directly change self-affect.
    from host_brain.app.emotion_context import infer_bootstrap_emotion_from_text

    inferred = infer_bootstrap_emotion_from_text(message)
    tag = normalize_emotion_tag(inferred.emotion_tag)
    distribution = {tag: max(0.05, _clamp(inferred.emotion_intensity, default=0.35))}
    valence, arousal = _distribution_vad(distribution)
    return _ObservationDraft(
        modality="TEXT",
        distribution=distribution,
        valence=valence,
        arousal=arousal,
        confidence=min(0.4, _clamp(inferred.confidence)),
        observed_at_ms=now_ms,
        expires_at_ms=now_ms + _TTL_MS["TEXT"],
        evidence_ref=_opaque_ref("affect-text", {"message": message}),
        source_order=0,
    )


def _draft_from_tag(
    *,
    modality: str,
    tag: Any,
    intensity: Any,
    confidence: Any,
    observed_at_ms: int,
    source_order: int,
    evidence_seed: Any,
) -> _ObservationDraft:
    normalized_tag = normalize_emotion_tag(str(tag))
    distribution = {normalized_tag: max(0.05, _clamp(intensity, default=0.55))}
    valence, arousal = _distribution_vad(distribution)
    return _ObservationDraft(
        modality=modality,
        distribution=distribution,
        valence=valence,
        arousal=arousal,
        confidence=_clamp(confidence, default=0.72),
        observed_at_ms=observed_at_ms,
        expires_at_ms=observed_at_ms + _TTL_MS[modality],
        evidence_ref=_opaque_ref("affect-observation", evidence_seed),
        source_order=source_order,
    )


def _draft_from_structured(
    raw: Mapping[str, Any],
    *,
    now_ms: int,
    source_order: int,
) -> _ObservationDraft | None:
    modality = _normalise_modality(raw.get("modality"))
    if modality is None:
        return None
    observed_at_ms = max(0, min(now_ms, int(raw.get("observed_at_ms") or now_ms)))
    distribution = _distribution(
        raw.get("emotion_distribution"),
        fallback_tag=_text(raw.get("emotion_tag"), default="NEUTRAL"),
    )
    default_valence, default_arousal = _distribution_vad(distribution)
    confidence = _clamp(raw.get("confidence"), default=0.55)
    if modality == "TEXT":
        confidence = min(0.4, confidence)
    expires_at_ms = observed_at_ms + _TTL_MS[modality]
    requested_expiry = raw.get("expires_at_ms")
    if requested_expiry not in (None, ""):
        try:
            expires_at_ms = min(expires_at_ms, max(observed_at_ms, int(requested_expiry)))
        except (TypeError, ValueError):
            pass
    return _ObservationDraft(
        modality=modality,
        distribution=distribution,
        valence=_signed(raw.get("valence"), default=default_valence),
        arousal=_clamp(raw.get("arousal"), default=default_arousal),
        confidence=confidence,
        observed_at_ms=observed_at_ms,
        expires_at_ms=expires_at_ms,
        evidence_ref=_opaque_ref(
            "affect-observation",
            {
                "modality": modality,
                "distribution": distribution,
                "confidence": confidence,
                "observed_at_ms": observed_at_ms,
                "provider": _text(raw.get("provider"), limit=96),
            },
        ),
        source_order=source_order,
    )


def _materialize_observations(
    *,
    scope: AffectiveScope,
    request_id: str,
    drafts: Sequence[_ObservationDraft],
) -> tuple[tuple[UserAffectObservationV1, ...], bool]:
    conflict = _has_material_conflict(drafts)
    conflict_ref = (
        _opaque_ref(
            "affect-conflict",
            [
                {
                    "modality": item.modality,
                    "distribution": dict(item.distribution),
                    "valence": item.valence,
                    "confidence": item.confidence,
                }
                for item in drafts
            ],
        )
        if conflict
        else None
    )
    observations: list[UserAffectObservationV1] = []
    for draft in sorted(drafts, key=lambda item: (item.source_order, item.modality)):
        identity = {
            "request_id": request_id,
            "workspace_id": scope.workspace_id,
            "relationship_subject_id": scope.relationship_subject_id,
            "session_id": scope.session_id,
            "modality": draft.modality,
            "source_order": draft.source_order,
            "evidence_ref": draft.evidence_ref,
        }
        observations.append(
            UserAffectObservationV1(
                observation_id=f"observation_{_digest(identity)[:32]}",
                workspace_id=scope.workspace_id,
                relationship_subject_id=scope.relationship_subject_id,
                session_id=scope.session_id,
                modality=draft.modality,
                emotion_distribution=draft.distribution,
                valence=draft.valence,
                arousal=draft.arousal,
                confidence=draft.confidence,
                observed_at_ms=draft.observed_at_ms,
                expires_at_ms=draft.expires_at_ms,
                evidence_refs=(draft.evidence_ref,),
                conflict_refs=((conflict_ref,) if conflict_ref else ()),
            )
        )
    return tuple(observations), conflict


def _has_material_conflict(drafts: Sequence[_ObservationDraft]) -> bool:
    credible = [item for item in drafts if item.confidence >= 0.55]
    for index, first in enumerate(credible):
        for second in credible[index + 1 :]:
            if first.modality == second.modality:
                continue
            if abs(first.valence - second.valence) >= 0.9:
                return True
            first_tag = _top_distribution_tag(first.distribution)
            second_tag = _top_distribution_tag(second.distribution)
            if first_tag != second_tag and {
                first_tag,
                second_tag,
            }.intersection(_DISTRESS_TAGS | _POSITIVE_TAGS):
                return True
    return False


def _top_distribution_tag(distribution: Mapping[str, float]) -> str:
    return sorted(distribution.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _affect_support(
    observations: Sequence[UserAffectObservationV1],
    tags: frozenset[str],
) -> float:
    """Return confidence-weighted support without collapsing a mixed report.

    A single SELF_REPORT can honestly contain both relief and grief.  It is
    not a sensor conflict merely because those valences differ, so Appraisal
    needs access to both supports before choosing Natsume's own response.
    """

    return max(
        (
            max(
                (
                    _clamp(item.emotion_distribution.get(tag)) * item.confidence
                    for tag in tags
                ),
                default=0.0,
            )
            for item in observations
        ),
        default=0.0,
    )


def _has_mixed_user_affect(observations: Sequence[UserAffectObservationV1]) -> bool:
    """Recognise coexisting positive and painful user affect, not disagreement.

    The conflict branch still wins when modalities contradict each other.  This
    branch only preserves sufficiently supported positive and distress signals
    in the same trusted observation set.
    """

    positive = _affect_support(observations, _POSITIVE_TAGS)
    distress = _affect_support(observations, _DISTRESS_TAGS)
    return positive >= 0.32 and distress >= 0.32


def _signals_natsume_may_have_hurt_user(message: str) -> bool:
    """Detect a bounded, directed repair signal without persisting its words.

    This is intentionally narrower than a general sadness classifier: the
    message must explicitly address Natsume (``你``/``you``) and contain a
    harm marker.  A user being hurt by a film, colleague, or unrelated event
    remains a user-affect observation and must not make Natsume invent blame.
    """

    normalized = str(message or "").lower()
    if "你" not in normalized and "you" not in normalized:
        return False
    return any(marker in normalized for marker in _NATSUME_HARM_MARKERS)


def _regulation_mode_for_candidate(candidate: _AppraisalCandidate) -> str:
    """Choose the bounded expression regulation for one just-appraised event.

    The mapping is deterministic and lives beside the candidate semantics so
    no model, Surface renderer, or compatibility state can invent a different
    regulation after the self-affect episode has been committed.
    """

    return {
        "user_boundary_or_space": "GIVE_SPACE",
        "natsume_may_have_hurt_user": "SEEK_REPAIR",
        "repair_signal": "SEEK_REPAIR",
        "user_affect_conflict": "CONTAIN",
    }.get(candidate.kind, "EXPRESS")


def _build_appraisal_candidates(
    *,
    observations: Sequence[UserAffectObservationV1],
    conflict: bool,
    message: str,
) -> tuple[_AppraisalCandidate, ...]:
    if not observations:
        return ()
    tag = _top_tag(observations)
    confidence = max(item.confidence for item in observations)
    lower = message.lower()
    has_space = any(marker in lower for marker in _SPACE_MARKERS)
    has_repair = any(marker in lower for marker in _REPAIR_MARKERS)
    has_boundary = any(marker in lower for marker in _BOUNDARY_MARKERS)
    natsume_may_have_hurt_user = _signals_natsume_may_have_hurt_user(message)
    candidates: list[_AppraisalCandidate] = []
    if conflict:
        candidates.append(
            _candidate(
                kind="user_affect_conflict",
                score=0.95,
                goal=-0.04,
                significance=0.56,
                boundary=0.18,
                risk=0.16,
                uncertainty=0.86,
                components=(
                    EmotionComponentV1("CARE", 0.34, 0.35, 0.26, "comfort"),
                    EmotionComponentV1("CAUTION", 0.3, -0.08, 0.3, "clarify"),
                ),
                action="clarify_gently",
            )
        )
    if has_space or has_boundary:
        candidates.append(
            _candidate(
                kind="user_boundary_or_space",
                # An explicit request for space is always stronger than a
                # simultaneous repair cue.  Respecting distance comes before
                # trying to explain, apologise, or seek reassurance.
                score=0.98,
                goal=-0.08,
                significance=0.62,
                boundary=0.85,
                risk=0.2,
                uncertainty=0.24,
                components=(
                    EmotionComponentV1("RESPECT", 0.54, 0.25, 0.18, "give_space"),
                    EmotionComponentV1("CAUTION", 0.3, -0.04, 0.25, "contain"),
                ),
                action="give_space",
            )
        )
    if natsume_may_have_hurt_user:
        candidates.append(
            _candidate(
                kind="natsume_may_have_hurt_user",
                score=0.93,
                goal=-0.18,
                responsibility=0.78,
                controllability=0.74,
                significance=0.78,
                boundary=0.62,
                risk=0.3,
                uncertainty=0.34,
                components=(
                    EmotionComponentV1("CARE", 0.52, 0.3, 0.26, "repair"),
                    EmotionComponentV1("OPENNESS", 0.38, 0.14, 0.18, "listen"),
                    EmotionComponentV1("CAUTION", 0.22, -0.06, 0.24, "slow_down"),
                ),
                action="repair_open",
            )
        )
    if has_repair:
        candidates.append(
            _candidate(
                kind="repair_signal",
                score=0.82,
                goal=0.05,
                significance=0.7,
                boundary=0.38,
                risk=0.14,
                uncertainty=0.36,
                components=(
                    EmotionComponentV1("CARE", 0.44, 0.3, 0.2, "repair"),
                    EmotionComponentV1("OPENNESS", 0.28, 0.22, 0.16, "listen"),
                ),
                action="repair_open",
            )
        )
    if not conflict and _has_mixed_user_affect(observations):
        candidates.append(
            _candidate(
                kind="user_mixed_affect",
                score=0.84,
                goal=0.08,
                significance=0.68,
                boundary=0.1,
                risk=0.1,
                uncertainty=0.3,
                components=(
                    # These are Natsume's own concurrent responses to the
                    # user's mixed moment.  They deliberately do not copy
                    # the user's HAPPY/SAD labels into self-affect.
                    EmotionComponentV1("CARE", 0.42, 0.28, 0.24, "attend"),
                    EmotionComponentV1("SHARED_JOY", 0.39, 0.66, 0.44, "share"),
                    EmotionComponentV1("CAUTION", 0.26, -0.04, 0.22, "hold"),
                ),
                action="hold_complexity",
            )
        )
    # Keyword text is intentionally low confidence, but a clear distress
    # marker still deserves the bounded deep path so Natsume can choose a
    # careful, uncertainty-aware companion response. The appraisal produces
    # CARE/CONCERN rather than copying the user's SAD/ANGRY state.
    if confidence >= 0.28 and tag in _DISTRESS_TAGS:
        candidates.append(
            _candidate(
                kind="user_distress",
                score=0.76,
                goal=-0.12,
                significance=0.66,
                boundary=0.14,
                risk=0.34 if tag in {"SAD", "ANXIOUS"} else 0.2,
                uncertainty=max(0.08, 1.0 - confidence),
                components=(
                    EmotionComponentV1("CARE", 0.58, 0.38, 0.28, "comfort"),
                    EmotionComponentV1("CONCERN", 0.38, -0.18, 0.42, "support"),
                ),
                action="comfort",
            )
        )
    if confidence >= 0.6 and tag in _POSITIVE_TAGS:
        candidates.append(
            _candidate(
                kind="shared_positive_moment",
                score=0.62,
                goal=0.36,
                significance=0.44,
                boundary=0.04,
                risk=0.02,
                uncertainty=max(0.06, 1.0 - confidence),
                components=(
                    EmotionComponentV1("SHARED_JOY", 0.48, 0.72, 0.5, "play"),
                    EmotionComponentV1("CARE", 0.2, 0.25, 0.14, "share"),
                ),
                action="share_joy",
            )
        )
    candidates.sort(key=lambda item: (-item.score, item.kind))
    return tuple(candidates[:2])


def _candidate(
    *,
    kind: str,
    score: float,
    goal: float,
    responsibility: float = 0.0,
    controllability: float = 0.58,
    significance: float,
    boundary: float,
    risk: float,
    uncertainty: float,
    components: tuple[EmotionComponentV1, ...],
    action: str,
) -> _AppraisalCandidate:
    return _AppraisalCandidate(
        kind=kind,
        score=score,
        goal_congruence=goal,
        responsibility=responsibility,
        controllability=controllability,
        relationship_significance=significance,
        boundary_relevance=boundary,
        risk=risk,
        uncertainty=uncertainty,
        components=components,
        action=action,
    )


def _self_component_to_transport_tag(value: Any) -> str:
    component = _text(value, default="CALM", limit=64).upper()
    mapping = {
        "CARE": "CALM",
        "CAUTION": "THINKING",
        "CONCERN": "THINKING",
        "RESPECT": "CALM",
        "OPENNESS": "CALM",
        "SHARED_JOY": "HAPPY",
        "HURT": "SAD",
        "ANGER": "ANGRY",
    }
    return mapping.get(component, "CALM")


__all__ = [
    "AffectiveScope",
    "AffectFusionResult",
    "AffectFusionService",
]
