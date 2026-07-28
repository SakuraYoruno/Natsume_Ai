"""Feedback policy staging and activation for NE-06.

The mixin constructs deterministic proposal-only Persona, expression and
runtime feedback versions.  The public service is assembled in
``version_activation.py``.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from host_brain.contracts.evolution import FeedbackProposalV1
from host_brain.persistence.evolution_activation_v2_evidence import (
    EvolutionActivationV2Error,
    bounded_text,
    payload_digest,
)
from host_brain.persona.personality_traits import TRAIT_NAMES


PERSONA_POLICY_KIND = "PERSONA_TRAITS"
EXPRESSION_PREFERENCE_POLICY_KIND = "EXPRESSION_PREFERENCES"
FEEDBACK_SIGNAL_POLICY_KIND = "FEEDBACK_SIGNAL_LEDGER"


class EvolutionVersionFeedbackMixin:
    """Stage and activate versioned feedback without direct trait writes."""

    def stage_persona_feedback(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        source_kind: str,
        current_traits: Mapping[str, Any],
        candidate_delta: Mapping[str, Any],
        evidence_refs: tuple[str, ...] = (),
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Persist a deterministic proposal without changing active traits."""

        workspace = bounded_text(workspace_id, "workspace_id", 128)
        subject = bounded_text(subject_id, "subject_id", 255)
        source = bounded_text(source_kind, "source_kind", 80)
        try:
            active = self._repository.active_policy(
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=PERSONA_POLICY_KIND,
            )
            if active is None:
                baseline = _trait_payload(current_traits)
                baseline_digest = payload_digest(baseline)
                baseline_version = (
                    f"persona-baseline-{baseline_digest[:24]}"
                )
                self._repository.register_policy_version(
                    workspace_id=workspace,
                    subject_id=subject,
                    policy_kind=PERSONA_POLICY_KIND,
                    version=baseline_version,
                    payload=baseline,
                    provenance_ref=(
                        "persona-baseline://sha256/"
                        f"{baseline_digest}"
                    ),
                )
                baseline_receipt = (
                    self._repository.backfill_policy_projection(
                        workspace_id=workspace,
                        subject_id=subject,
                        policy_kind=PERSONA_POLICY_KIND,
                        version=baseline_version,
                        migration_ref=(
                            "evolution-migration://0142/"
                            "persona-baseline"
                        ),
                        idempotency_key=(
                            "persona-baseline:"
                            f"{workspace}:{subject}:{baseline_digest}"
                        ),
                    )
                )
                active = self._repository.active_policy(
                    workspace_id=workspace,
                    subject_id=subject,
                    policy_kind=PERSONA_POLICY_KIND,
                )
                if active is None:
                    raise EvolutionActivationV2Error(
                        "EVOLUTION_PERSONA_BASELINE_BACKFILL_FAILED"
                    )
            else:
                baseline_receipt = None

            delta = _trait_delta(candidate_delta)
            target = _apply_trait_delta(active["payload"], delta)
            target_digest = payload_digest(target)
            if target_digest == str(active["active_digest"]):
                return {
                    "status": "NO_CHANGE",
                    "workspace_id": workspace,
                    "subject_id": subject,
                    "active_version": str(active["active_version"]),
                    "active_digest": str(active["active_digest"]),
                    "activation_generation": int(
                        active["activation_generation"]
                    ),
                }

            target_version = f"persona-{target_digest[:24]}"
            trace_payload = {
                "schema": "natsume.persona-feedback-trace.v1",
                "workspace_id": workspace,
                "subject_id": subject,
                "source_kind": source,
                "active_digest": str(active["active_digest"]),
                "target_digest": target_digest,
                "candidate_delta": delta,
                "evidence_refs": sorted(
                    {
                        bounded_text(item, "evidence_ref", 1_024)
                        for item in evidence_refs
                    }
                ),
            }
            trace_digest = payload_digest(trace_payload)
            proposal_id = (
                "feedback-proposal://sha256/"
                f"{payload_digest({**trace_payload, 'target': target})}"
            )
            self._repository.register_policy_version(
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=PERSONA_POLICY_KIND,
                version=target_version,
                payload=target,
                provenance_ref=(
                    f"feedback-source://sha256/{trace_digest}"
                ),
            )
            proposal = FeedbackProposalV1(
                proposal_id=proposal_id,
                source_kind=source,
                trace_digest=trace_digest,
                outcome_digest=target_digest,
                confidence=float(confidence),
                target_version=target_version,
                change=target,
                rollback_ref=(
                    "evolution-policy-version://sha256/"
                    f"{active['active_digest']}"
                ),
            )
            self._repository.register_feedback_proposal(
                proposal,
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=PERSONA_POLICY_KIND,
            )
            return {
                "status": "STAGED",
                "workspace_id": workspace,
                "subject_id": subject,
                "policy_kind": PERSONA_POLICY_KIND,
                "proposal": proposal.to_dict(),
                "active_version": str(active["active_version"]),
                "active_digest": str(active["active_digest"]),
                "activation_generation": int(
                    active["activation_generation"]
                ),
                "baseline_receipt": (
                    None
                    if baseline_receipt is None
                    else baseline_receipt.to_dict()
                ),
            }
        except Exception as exc:
            self._record_and_raise(
                workspace_id=workspace,
                subject_kind="FEEDBACK_POLICY",
                subject_id=subject,
                operation="STAGE_PERSONA_FEEDBACK",
                exc=exc,
            )

    def resolve_persona_traits(
        self,
        *,
        workspace_id: str,
        subject_id: str,
    ) -> dict[str, Any] | None:
        active = self._repository.active_policy(
            workspace_id=workspace_id,
            subject_id=subject_id,
            policy_kind=PERSONA_POLICY_KIND,
        )
        return None if active is None else dict(active["payload"])

    def resolve_expression_preferences(
        self,
        *,
        workspace_id: str,
        subject_id: str,
    ) -> dict[str, Any] | None:
        active = self._repository.active_policy(
            workspace_id=workspace_id,
            subject_id=subject_id,
            policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
        )
        return None if active is None else dict(active["payload"])

    def stage_expression_feedback(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        target_request_id: str,
        target_text_hash: str,
        feedback_score: float,
        feedback_tags: tuple[str, ...],
        feedback_source: str,
    ) -> dict[str, Any]:
        """Turn explicit response feedback into a review-only policy proposal.

        Only digests and bounded tags enter the version authority.  The user
        message and assistant body remain outside this durable proposal path.
        """

        workspace = bounded_text(workspace_id, "workspace_id", 128)
        subject = bounded_text(subject_id, "subject_id", 255)
        request_id = bounded_text(
            target_request_id,
            "target_request_id",
            255,
        )
        text_hash = bounded_text(
            target_text_hash,
            "target_text_hash",
            128,
        )
        source = bounded_text(
            feedback_source,
            "feedback_source",
            80,
        )
        score = float(feedback_score)
        if not math.isfinite(score) or score == 0.0 or abs(score) > 1.0:
            raise EvolutionActivationV2Error(
                "EVOLUTION_EXPRESSION_FEEDBACK_SCORE_INVALID"
            )
        tags = tuple(
            sorted(
                {
                    bounded_text(tag, "feedback_tag", 80)
                    for tag in feedback_tags
                }
            )
        )
        if not tags:
            raise EvolutionActivationV2Error(
                "EVOLUTION_EXPRESSION_FEEDBACK_TAGS_REQUIRED"
            )
        try:
            active = self._repository.active_policy(
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
            )
            baseline_receipt = None
            if active is None:
                baseline = {
                    "schema": (
                        "natsume.expression-preference-policy.v1"
                    ),
                    "positive_feedback_count": 0,
                    "negative_feedback_count": 0,
                    "tag_scores": {},
                }
                baseline_digest = payload_digest(baseline)
                baseline_version = (
                    f"expression-baseline-{baseline_digest[:24]}"
                )
                self._repository.register_policy_version(
                    workspace_id=workspace,
                    subject_id=subject,
                    policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
                    version=baseline_version,
                    payload=baseline,
                    provenance_ref=(
                        "expression-policy-baseline://sha256/"
                        f"{baseline_digest}"
                    ),
                )
                baseline_receipt = (
                    self._repository.backfill_policy_projection(
                        workspace_id=workspace,
                        subject_id=subject,
                        policy_kind=(
                            EXPRESSION_PREFERENCE_POLICY_KIND
                        ),
                        version=baseline_version,
                        migration_ref=(
                            "evolution-migration://0142/"
                            "expression-preference-baseline"
                        ),
                        idempotency_key=(
                            "expression-preference-baseline:"
                            f"{workspace}:{subject}:{baseline_digest}"
                        ),
                    )
                )
                active = self._repository.active_policy(
                    workspace_id=workspace,
                    subject_id=subject,
                    policy_kind=(
                        EXPRESSION_PREFERENCE_POLICY_KIND
                    ),
                )
                if active is None:
                    raise EvolutionActivationV2Error(
                        "EVOLUTION_EXPRESSION_BASELINE_BACKFILL_FAILED"
                    )

            current = _expression_policy_payload(active["payload"])
            tag_scores = dict(current["tag_scores"])
            direction = 1 if score > 0.0 else -1
            for tag in tags:
                tag_scores[tag] = max(
                    -1_000,
                    min(
                        1_000,
                        int(tag_scores.get(tag, 0)) + direction,
                    ),
                )
            target = {
                "schema": current["schema"],
                "positive_feedback_count": (
                    int(current["positive_feedback_count"])
                    + (1 if direction > 0 else 0)
                ),
                "negative_feedback_count": (
                    int(current["negative_feedback_count"])
                    + (1 if direction < 0 else 0)
                ),
                "tag_scores": {
                    key: tag_scores[key]
                    for key in sorted(tag_scores)
                },
            }
            target_digest = payload_digest(target)
            target_version = f"expression-{target_digest[:24]}"
            signal = {
                "schema": "natsume.expression-feedback-trace.v1",
                "workspace_id": workspace,
                "subject_id": subject,
                "target_request_id_digest": payload_digest(
                    {"target_request_id": request_id}
                ),
                "target_text_hash": text_hash,
                "feedback_score": score,
                "feedback_tags": list(tags),
                "feedback_source": source,
                "active_digest": str(active["active_digest"]),
                "target_digest": target_digest,
            }
            trace_digest = payload_digest(signal)
            proposal_id = (
                "feedback-proposal://sha256/"
                f"{payload_digest({**signal, 'target': target})}"
            )
            self._repository.register_policy_version(
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
                version=target_version,
                payload=target,
                provenance_ref=(
                    "response-feedback://sha256/"
                    f"{trace_digest}"
                ),
            )
            proposal = FeedbackProposalV1(
                proposal_id=proposal_id,
                source_kind="response_feedback",
                trace_digest=trace_digest,
                outcome_digest=target_digest,
                confidence=min(1.0, abs(score)),
                target_version=target_version,
                change=target,
                rollback_ref=(
                    "evolution-policy-version://sha256/"
                    f"{active['active_digest']}"
                ),
            )
            self._repository.register_feedback_proposal(
                proposal,
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
            )
            return {
                "status": "STAGED",
                "workspace_id": workspace,
                "subject_id": subject,
                "policy_kind": (
                    EXPRESSION_PREFERENCE_POLICY_KIND
                ),
                "proposal": proposal.to_dict(),
                "active_version": str(active["active_version"]),
                "active_digest": str(active["active_digest"]),
                "activation_generation": int(
                    active["activation_generation"]
                ),
                "baseline_receipt": (
                    None
                    if baseline_receipt is None
                    else baseline_receipt.to_dict()
                ),
            }
        except Exception as exc:
            self._record_and_raise(
                workspace_id=workspace,
                subject_kind="FEEDBACK_POLICY",
                subject_id=subject,
                operation="STAGE_EXPRESSION_FEEDBACK",
                exc=exc,
            )

    def stage_feedback_signal(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        source_kind: str,
        outcome: str,
        evidence_refs: tuple[str, ...],
        context: Mapping[str, Any] | None = None,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Normalize a bounded runtime outcome into FeedbackProposalV1.

        The active policy is only a ledger of signals that completed
        Lab/Review.  Staging never changes that projection.
        """

        workspace = bounded_text(workspace_id, "workspace_id", 128)
        subject = bounded_text(subject_id, "subject_id", 255)
        source = bounded_text(source_kind, "source_kind", 80).lower()
        if source not in {
            "task_outcome",
            "verification",
            "user_rejection",
            "tool_reliability",
        }:
            raise EvolutionActivationV2Error(
                "EVOLUTION_FEEDBACK_SIGNAL_SOURCE_INVALID"
            )
        result = bounded_text(outcome, "outcome", 80).upper()
        refs = tuple(
            sorted(
                {
                    bounded_text(item, "evidence_ref", 1_024)
                    for item in evidence_refs
                }
            )
        )
        if not refs:
            raise EvolutionActivationV2Error(
                "EVOLUTION_FEEDBACK_SIGNAL_EVIDENCE_REQUIRED"
            )
        confidence_value = float(confidence)
        if (
            not math.isfinite(confidence_value)
            or not 0.0 <= confidence_value <= 1.0
        ):
            raise EvolutionActivationV2Error(
                "EVOLUTION_FEEDBACK_SIGNAL_CONFIDENCE_INVALID"
            )
        context_digest = payload_digest(dict(context or {}))
        try:
            active = self._repository.active_policy(
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=FEEDBACK_SIGNAL_POLICY_KIND,
            )
            baseline_receipt = None
            if active is None:
                baseline = {
                    "schema": "natsume.feedback-signal-ledger.v1",
                    "accepted_signal_digests": [],
                }
                baseline_digest = payload_digest(baseline)
                baseline_version = (
                    f"feedback-signal-baseline-"
                    f"{baseline_digest[:24]}"
                )
                self._repository.register_policy_version(
                    workspace_id=workspace,
                    subject_id=subject,
                    policy_kind=FEEDBACK_SIGNAL_POLICY_KIND,
                    version=baseline_version,
                    payload=baseline,
                    provenance_ref=(
                        "feedback-signal-baseline://sha256/"
                        f"{baseline_digest}"
                    ),
                )
                baseline_receipt = (
                    self._repository.backfill_policy_projection(
                        workspace_id=workspace,
                        subject_id=subject,
                        policy_kind=FEEDBACK_SIGNAL_POLICY_KIND,
                        version=baseline_version,
                        migration_ref=(
                            "evolution-migration://0142/"
                            "feedback-signal-baseline"
                        ),
                        idempotency_key=(
                            "feedback-signal-baseline:"
                            f"{workspace}:{subject}:{baseline_digest}"
                        ),
                    )
                )
                active = self._repository.active_policy(
                    workspace_id=workspace,
                    subject_id=subject,
                    policy_kind=FEEDBACK_SIGNAL_POLICY_KIND,
                )
                if active is None:
                    raise EvolutionActivationV2Error(
                        "EVOLUTION_FEEDBACK_SIGNAL_BASELINE_FAILED"
                    )
            current = _feedback_signal_policy_payload(
                active["payload"]
            )
            trace = {
                "schema": "natsume.feedback-signal-trace.v1",
                "workspace_id": workspace,
                "subject_id": subject,
                "source_kind": source,
                "outcome": result,
                "evidence_refs": list(refs),
                "context_digest": context_digest,
                "active_digest": str(active["active_digest"]),
            }
            trace_digest = payload_digest(trace)
            accepted = list(current["accepted_signal_digests"])
            if trace_digest in accepted:
                return {
                    "status": "NO_CHANGE",
                    "workspace_id": workspace,
                    "subject_id": subject,
                    "active_version": str(active["active_version"]),
                    "active_digest": str(active["active_digest"]),
                    "activation_generation": int(
                        active["activation_generation"]
                    ),
                }
            target = {
                "schema": current["schema"],
                "accepted_signal_digests": sorted(
                    {*accepted[-127:], trace_digest}
                ),
            }
            target_digest = payload_digest(target)
            target_version = f"feedback-signal-{target_digest[:24]}"
            proposal_id = (
                "feedback-proposal://sha256/"
                f"{payload_digest({**trace, 'target': target})}"
            )
            self._repository.register_policy_version(
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=FEEDBACK_SIGNAL_POLICY_KIND,
                version=target_version,
                payload=target,
                provenance_ref=(
                    f"runtime-feedback://sha256/{trace_digest}"
                ),
            )
            proposal = FeedbackProposalV1(
                proposal_id=proposal_id,
                source_kind=source,
                trace_digest=trace_digest,
                outcome_digest=target_digest,
                confidence=confidence_value,
                target_version=target_version,
                change=target,
                rollback_ref=(
                    "evolution-policy-version://sha256/"
                    f"{active['active_digest']}"
                ),
            )
            self._repository.register_feedback_proposal(
                proposal,
                workspace_id=workspace,
                subject_id=subject,
                policy_kind=FEEDBACK_SIGNAL_POLICY_KIND,
            )
            return {
                "status": "STAGED",
                "workspace_id": workspace,
                "subject_id": subject,
                "policy_kind": FEEDBACK_SIGNAL_POLICY_KIND,
                "proposal": proposal.to_dict(),
                "active_version": str(active["active_version"]),
                "active_digest": str(active["active_digest"]),
                "activation_generation": int(
                    active["activation_generation"]
                ),
                "baseline_receipt": (
                    None
                    if baseline_receipt is None
                    else baseline_receipt.to_dict()
                ),
            }
        except Exception as exc:
            self._record_and_raise(
                workspace_id=workspace,
                subject_kind="FEEDBACK_POLICY",
                subject_id=subject,
                operation="STAGE_FEEDBACK_SIGNAL",
                exc=exc,
            )

    def apply_feedback(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        proposal_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="FEEDBACK_POLICY",
            subject_id=subject_id,
            operation="APPLY_FEEDBACK",
            callback=lambda: self._repository.apply_feedback(
                proposal_id=proposal_id,
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                expected_generation=expected_generation,
                idempotency_key=idempotency_key,
            ),
        )

    def rollback_feedback(
        self,
        *,
        workspace_id: str,
        subject_id: str,
        proposal_id: str,
        lab_report_digest: str,
        review_id: str,
        review_status_version: int,
        expected_generation: int,
        idempotency_key: str,
    ):
        return self._mutate(
            workspace_id=workspace_id,
            subject_kind="FEEDBACK_POLICY",
            subject_id=subject_id,
            operation="ROLLBACK_FEEDBACK",
            callback=lambda: self._repository.rollback_feedback(
                proposal_id=proposal_id,
                lab_report_digest=lab_report_digest,
                review_id=review_id,
                review_status_version=review_status_version,
                expected_generation=expected_generation,
                idempotency_key=idempotency_key,
            ),
        )


def _trait_payload(value: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise EvolutionActivationV2Error(
            "EVOLUTION_PERSONA_TRAITS_INVALID"
        )
    if set(value) != set(TRAIT_NAMES):
        raise EvolutionActivationV2Error(
            "EVOLUTION_PERSONA_TRAIT_DIMENSIONS_INVALID"
        )
    result: dict[str, float] = {}
    for name in TRAIT_NAMES:
        number = float(value[name])
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise EvolutionActivationV2Error(
                "EVOLUTION_PERSONA_TRAIT_VALUE_INVALID",
                name,
            )
        result[name] = round(number, 6)
    return result


def _trait_delta(value: Mapping[str, Any]) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise EvolutionActivationV2Error(
            "EVOLUTION_PERSONA_DELTA_INVALID"
        )
    if not set(value).issubset(set(TRAIT_NAMES)):
        raise EvolutionActivationV2Error(
            "EVOLUTION_PERSONA_DELTA_DIMENSIONS_INVALID"
        )
    result: dict[str, float] = {}
    for name in TRAIT_NAMES:
        number = float(value.get(name, 0.0))
        if not math.isfinite(number) or abs(number) > 0.25:
            raise EvolutionActivationV2Error(
                "EVOLUTION_PERSONA_DELTA_VALUE_INVALID",
                name,
            )
        result[name] = round(number, 6)
    if not any(result.values()):
        raise EvolutionActivationV2Error(
            "EVOLUTION_PERSONA_DELTA_EMPTY"
        )
    return result


def _apply_trait_delta(
    active: Mapping[str, Any],
    delta: Mapping[str, float],
) -> dict[str, float]:
    source = _trait_payload(active)
    return {
        name: round(
            max(0.0, min(1.0, source[name] + float(delta[name]))),
            6,
        )
        for name in TRAIT_NAMES
    }


def _expression_policy_payload(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvolutionActivationV2Error(
            "EVOLUTION_EXPRESSION_POLICY_INVALID"
        )
    schema = str(value.get("schema") or "").strip()
    if schema != "natsume.expression-preference-policy.v1":
        raise EvolutionActivationV2Error(
            "EVOLUTION_EXPRESSION_POLICY_SCHEMA_INVALID"
        )
    try:
        positive = int(value.get("positive_feedback_count") or 0)
        negative = int(value.get("negative_feedback_count") or 0)
    except (TypeError, ValueError) as exc:
        raise EvolutionActivationV2Error(
            "EVOLUTION_EXPRESSION_POLICY_COUNT_INVALID"
        ) from exc
    if positive < 0 or negative < 0:
        raise EvolutionActivationV2Error(
            "EVOLUTION_EXPRESSION_POLICY_COUNT_INVALID"
        )
    raw_scores = value.get("tag_scores")
    if not isinstance(raw_scores, Mapping):
        raise EvolutionActivationV2Error(
            "EVOLUTION_EXPRESSION_POLICY_TAG_SCORES_INVALID"
        )
    tag_scores: dict[str, int] = {}
    for raw_tag, raw_score in raw_scores.items():
        tag = bounded_text(raw_tag, "feedback_tag", 80)
        try:
            number = int(raw_score)
        except (TypeError, ValueError) as exc:
            raise EvolutionActivationV2Error(
                "EVOLUTION_EXPRESSION_POLICY_TAG_SCORE_INVALID"
            ) from exc
        if not -1_000 <= number <= 1_000:
            raise EvolutionActivationV2Error(
                "EVOLUTION_EXPRESSION_POLICY_TAG_SCORE_INVALID"
            )
        tag_scores[tag] = number
    return {
        "schema": schema,
        "positive_feedback_count": positive,
        "negative_feedback_count": negative,
        "tag_scores": {
            key: tag_scores[key]
            for key in sorted(tag_scores)
        },
    }


def _feedback_signal_policy_payload(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise EvolutionActivationV2Error(
            "EVOLUTION_FEEDBACK_SIGNAL_POLICY_INVALID"
        )
    schema = str(value.get("schema") or "").strip()
    if schema != "natsume.feedback-signal-ledger.v1":
        raise EvolutionActivationV2Error(
            "EVOLUTION_FEEDBACK_SIGNAL_POLICY_SCHEMA_INVALID"
        )
    raw_digests = value.get("accepted_signal_digests")
    if not isinstance(raw_digests, list):
        raise EvolutionActivationV2Error(
            "EVOLUTION_FEEDBACK_SIGNAL_DIGESTS_INVALID"
        )
    digests: list[str] = []
    for item in raw_digests:
        digest = str(item or "").strip().lower()
        if (
            len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise EvolutionActivationV2Error(
                "EVOLUTION_FEEDBACK_SIGNAL_DIGEST_INVALID"
            )
        digests.append(digest)
    if len(digests) > 128 or len(set(digests)) != len(digests):
        raise EvolutionActivationV2Error(
            "EVOLUTION_FEEDBACK_SIGNAL_DIGESTS_INVALID"
        )
    return {
        "schema": schema,
        "accepted_signal_digests": sorted(digests),
    }



__all__ = [
    "EXPRESSION_PREFERENCE_POLICY_KIND",
    "EvolutionVersionFeedbackMixin",
    "FEEDBACK_SIGNAL_POLICY_KIND",
    "PERSONA_POLICY_KIND",
]
