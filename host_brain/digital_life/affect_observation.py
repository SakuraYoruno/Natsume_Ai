"""隐私最小化的用户情感观察收集与物化。

本模块只把短生命周期、去原文的用户侧观察整理成可审计输入；它不是 Natsume
自我情感的写入者。观察会在 AffectFusion 中被谨慎使用，不能单独授权关系或
表达状态变更。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from host_brain.core.emotion_constants import normalize_emotion_tag
from host_brain.digital_life.affect_fusion_support import (
    _clamp,
    _contains_raw_material,
    _digest,
    _distribution,
    _distribution_vad,
    _normalise_modality,
    _opaque_ref,
    _safe_mapping,
    _safe_sequence,
    _signed,
    _text,
)
from natsume_contracts.life import UserAffectObservationV1


_TTL_MS = {
    "TEXT": 30_000,
    "SELF_REPORT": 30_000,
    "ACOUSTIC": 5_000,
    "VISUAL": 3_000,
}


@dataclass(frozen=True, slots=True)
class _ObservationDraft:
    """尚未绑定关系作用域的内部观察草稿，仅保存摘要化证据引用。"""
    modality: str
    distribution: Mapping[str, float]
    valence: float
    arousal: float
    confidence: float
    observed_at_ms: int
    expires_at_ms: int
    evidence_ref: str
    source_order: int


def _collect_observation_drafts(
    *,
    request_body: Any,
    now_ms: int,
) -> tuple[list[_ObservationDraft], list[str]]:
    """从请求中收集允许的观察草稿，并剔除任何原始模态材料。

    自述、文本和结构化多模态输入可并存；无法安全归档的字段只产生原因码，
    不阻断正常对话也不把原始音视频/文本带入 Life。
    """
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
        draft = _draft_from_structured(raw, now_ms=now_ms, source_order=len(drafts))
        if draft is None:
            reason_codes.append("AFFECT_OBSERVATION_INVALID")
            continue
        drafts.append(draft)
    return drafts, reason_codes


def _text_draft(*, message: str, now_ms: int) -> _ObservationDraft:
    # 关键词识别器有意只是低置信度的用户侧观察，不能直接改变 Natsume 的
    # self-affect；其证据引用只由消息摘要派生，不保存对话原文。
    from host_brain.digital_life.text_affect_observer import (  # noqa: PLC0415
        infer_bootstrap_emotion_from_text,
    )

    inferred = infer_bootstrap_emotion_from_text(message)
    tag = normalize_emotion_tag(inferred.emotion_tag)
    distribution = {
        normalize_emotion_tag(raw_tag): max(0.05, _clamp(weight, default=0.35))
        for raw_tag, weight in inferred.emotion_distribution
    } or {tag: max(0.05, _clamp(inferred.emotion_intensity, default=0.35))}
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
    """把用户显式标签转为短 TTL 自述观察，而不是作为系统推断事实。"""
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
    """规范化已摘要的结构化观察，并把时间与置信度限制在安全范围内。"""
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
    scope: Any,
    request_id: str,
    drafts: Sequence[_ObservationDraft],
) -> tuple[tuple[UserAffectObservationV1, ...], bool]:
    """将草稿确定性绑定到本次关系作用域并标注跨模态冲突。

    稳定 observation ID 让重试不会制造重复输入；冲突只作为证据引用传递，
    供融合层选择保留不确定性，而非在收集层强行裁决用户情绪。
    """
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
    """检测高置信不同模态的实质矛盾，用于触发保守融合而非覆盖任一来源。"""
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
            } & {"HAPPY", "EXCITED", "GRATEFUL", "LOVING"} and {
                first_tag,
                second_tag,
            } & {"SAD", "ANXIOUS", "ANGRY", "FRUSTRATED"}:
                return True
    return False


def _top_distribution_tag(distribution: Mapping[str, float]) -> str:
    if not distribution:
        return "NEUTRAL"
    return sorted(distribution.items(), key=lambda item: (-item[1], item[0]))[0][0]
