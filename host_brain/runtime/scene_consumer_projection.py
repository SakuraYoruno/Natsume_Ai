"""Read-only projections from the canonical NE-03 Scene decision.

Production consumers must never derive a second Scene from request metadata.
This module is the only compatibility boundary between ``SceneDecisionV2``
and the legacy Surface/proactive read models.  It verifies the complete frozen
identity before exposing a restrict-only projection.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from host_brain.contracts.runtime_context import ContextConstraintV1, SceneDecisionV2


SCENE_IDENTITY_SCHEMA_V2 = "natsume.scene-identity.v2"
SCENE_CONSUMER_PROJECTION_SCHEMA_V1 = "natsume.scene-consumer-projection.v1"

SCENE_PROJECTION_ALLOW = "ALLOW"
SCENE_PROJECTION_RESTRICT = "RESTRICT"
SCENE_PROJECTION_REVIEW = "REVIEW"
SCENE_PROJECTION_BLOCK = "BLOCK"

SCENE_PROJECTION_REQUIRED = "SCENE_DECISION_V2_REQUIRED"
SCENE_IDENTITY_REQUIRED = "SCENE_IDENTITY_V2_REQUIRED"
SCENE_PROJECTION_INVALID = "SCENE_DECISION_V2_INVALID"
SCENE_IDENTITY_MISMATCH = "SCENE_DECISION_V2_IDENTITY_MISMATCH"

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_GENERIC_PROACTIVE_SCENES = frozenset(
    {"late_night", "morning_greet", "holiday_greet"}
)
_LOW_INTERRUPTION_CODES = frozenset(
    {"LOW_INTERRUPTION", "PROACTIVE_ACTION_BLOCKED"}
)
_EXPRESSION_MODES = (
    (frozenset({"PROACTIVE_ACTION_BLOCKED"}), "quiet"),
    (frozenset({"LOW_INTERRUPTION"}), "companion_brief"),
    (frozenset({"CONCISE_SUPPORTIVE_RESPONSE"}), "concise_supportive"),
    (
        frozenset(
            {
                "EVIDENCE_BOUND_CHANGE",
                "REPRODUCTION_BEFORE_MUTATION",
                "SOURCE_EVIDENCE_REQUIRED",
            }
        ),
        "task_first",
    ),
)


class SceneConsumerProjectionError(ValueError):
    """Frozen Scene evidence is absent, malformed, or bound elsewhere."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = str(reason_code or SCENE_PROJECTION_INVALID)


@dataclass(frozen=True, slots=True)
class SceneIdentityV2:
    """Exact identity copied alongside a frozen Scene decision."""

    workspace_id: str
    subject_id: str
    scene_id: str
    revision: int
    decision_digest: str

    def __post_init__(self) -> None:
        for field_name in ("workspace_id", "subject_id", "scene_id"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise SceneConsumerProjectionError(
                    SCENE_PROJECTION_INVALID,
                    f"{field_name} is required in Scene identity",
                )
            object.__setattr__(self, field_name, value)
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 1
        ):
            raise SceneConsumerProjectionError(
                SCENE_PROJECTION_INVALID,
                "Scene identity revision must be positive",
            )
        digest = str(self.decision_digest or "").strip().lower()
        if _DIGEST_RE.fullmatch(digest) is None:
            raise SceneConsumerProjectionError(
                SCENE_PROJECTION_INVALID,
                "Scene identity decision_digest is invalid",
            )
        object.__setattr__(self, "decision_digest", digest)

    @classmethod
    def from_decision(cls, decision: SceneDecisionV2) -> "SceneIdentityV2":
        return cls(
            workspace_id=decision.workspace_id,
            subject_id=decision.subject_id,
            scene_id=decision.scene_id,
            revision=decision.revision,
            decision_digest=decision.decision_digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCENE_IDENTITY_SCHEMA_V2,
            "workspace_id": self.workspace_id,
            "subject_id": self.subject_id,
            "scene_id": self.scene_id,
            "revision": self.revision,
            "decision_digest": self.decision_digest,
        }


def _read_identity(value: Any) -> SceneIdentityV2:
    if isinstance(value, SceneIdentityV2):
        return value
    if not isinstance(value, Mapping):
        raise SceneConsumerProjectionError(
            SCENE_IDENTITY_REQUIRED,
            "frozen scene_identity_v2 is required",
        )
    payload = dict(value)
    schema = str(payload.get("schema") or "").strip()
    if schema != SCENE_IDENTITY_SCHEMA_V2:
        raise SceneConsumerProjectionError(
            SCENE_PROJECTION_INVALID,
            "scene_identity_v2 schema is invalid",
        )
    try:
        revision = payload["revision"]
        return SceneIdentityV2(
            workspace_id=payload["workspace_id"],
            subject_id=payload["subject_id"],
            scene_id=payload["scene_id"],
            revision=revision,
            decision_digest=payload["decision_digest"],
        )
    except KeyError as exc:
        raise SceneConsumerProjectionError(
            SCENE_PROJECTION_INVALID,
            "scene_identity_v2 is incomplete",
        ) from exc


def _read_decision(value: Any) -> SceneDecisionV2:
    if isinstance(value, SceneDecisionV2):
        return value
    if not isinstance(value, Mapping):
        raise SceneConsumerProjectionError(
            SCENE_PROJECTION_REQUIRED,
            "frozen scene_decision_v2 is required",
        )
    payload = dict(value)
    if str(payload.get("schema") or "").strip() != "natsume.scene-decision.v2":
        raise SceneConsumerProjectionError(
            SCENE_PROJECTION_INVALID,
            "scene_decision_v2 schema is invalid",
        )
    supplied_digest = str(payload.get("decision_digest") or "").strip().lower()
    if _DIGEST_RE.fullmatch(supplied_digest) is None:
        raise SceneConsumerProjectionError(
            SCENE_PROJECTION_INVALID,
            "scene_decision_v2 decision_digest is required",
        )
    raw_constraints = payload.get("constraints")
    if not isinstance(raw_constraints, (list, tuple)):
        raise SceneConsumerProjectionError(
            SCENE_PROJECTION_INVALID,
            "scene_decision_v2 constraints are invalid",
        )
    try:
        constraints = tuple(
            item
            if isinstance(item, ContextConstraintV1)
            else ContextConstraintV1(
                code=item["code"],
                disposition=item["disposition"],
                origin=item["origin"],
                source_ref=item["source_ref"],
                reason=item.get("reason", ""),
            )
            for item in raw_constraints
        )
        return SceneDecisionV2(
            workspace_id=payload["workspace_id"],
            subject_id=payload["subject_id"],
            scene_id=payload["scene_id"],
            revision=payload["revision"],
            world_snapshot_digest=payload["world_snapshot_digest"],
            context_digest=payload["context_digest"],
            guardian_snapshot_digest=payload["guardian_snapshot_digest"],
            constraints=constraints,
            decision_digest=supplied_digest,
        )
    except SceneConsumerProjectionError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise SceneConsumerProjectionError(
            SCENE_PROJECTION_INVALID,
            "scene_decision_v2 does not bind valid typed evidence",
        ) from exc


def _expression_mode(codes: frozenset[str], disposition: str) -> str:
    if disposition == SCENE_PROJECTION_REVIEW:
        return "review_restrained"
    for candidates, mode in _EXPRESSION_MODES:
        if codes.intersection(candidates):
            return mode
    if disposition == SCENE_PROJECTION_BLOCK:
        return "quiet"
    if disposition == SCENE_PROJECTION_RESTRICT:
        return "task_first"
    return "natural"


@dataclass(frozen=True, slots=True)
class SceneConsumerProjectionV1:
    """Restrict-only read model shared by all legacy Scene consumers."""

    decision: SceneDecisionV2
    identity: SceneIdentityV2
    disposition: str
    expression_mode: str
    proactive_policy: str
    reason_codes: tuple[str, ...]

    def _legacy_payload(self, *, user_goal_ref: str = "") -> dict[str, Any]:
        """Return a fresh compatibility mapping without mutable authority."""

        codes = tuple(self.reason_codes)
        return {
            "schema_version": SCENE_CONSUMER_PROJECTION_SCHEMA_V1,
            "schema": SCENE_CONSUMER_PROJECTION_SCHEMA_V1,
            "decision_id": f"scene_v2_{self.decision.decision_digest[:24]}",
            "scene_type": self.decision.scene_id,
            "scene_id": self.decision.scene_id,
            "revision": self.decision.revision,
            "decision_digest": self.decision.decision_digest,
            "world_snapshot_digest": self.decision.world_snapshot_digest,
            "context_digest": self.decision.context_digest,
            "guardian_snapshot_digest": self.decision.guardian_snapshot_digest,
            "scene_identity_v2": self.identity.to_dict(),
            "proactive_policy": self.proactive_policy,
            "expression_mode": self.expression_mode,
            "reason_codes": codes,
            "constraints": {
                "proactive_policy": self.proactive_policy,
                "expression_mode": self.expression_mode,
                "typed_constraint_codes": codes,
                "can_change_user_goal": False,
                "can_expand_scope": False,
                "can_reduce_risk": False,
                "can_bypass_token": False,
                "permission_effect": "RESTRICT_ONLY",
            },
            "source_refs": tuple(
                dict.fromkeys(item.source_ref for item in self.decision.constraints)
            ),
            "user_goal_ref": str(user_goal_ref or "").strip(),
            "user_goal_preserved": True,
            "read_only": True,
            "permission_effect": "RESTRICT_ONLY",
            "expired": False,
            "validation_status": "VERIFIED",
        }

    def expression_projection(self, *, user_goal_ref: str = "") -> dict[str, Any]:
        """Project exact Scene evidence for the expression-only Surface path."""

        return self._legacy_payload(user_goal_ref=user_goal_ref)

    def surface_guardrails(self) -> tuple[str, ...]:
        if self.disposition == SCENE_PROJECTION_ALLOW:
            return ()
        if self.disposition == SCENE_PROJECTION_BLOCK:
            prefix = "SceneDecisionV2(BLOCK)：保持简短、安静、任务优先，不得追加主动话题。"
        elif self.disposition == SCENE_PROJECTION_REVIEW:
            prefix = "SceneDecisionV2(REVIEW)：仅收紧表达并提示必要复核，不得自行扩大行动。"
        else:
            prefix = (
                f"SceneDecisionV2({self.expression_mode})：表达必须服从当前场景约束，"
                "最多一次必要追问。"
            )
        return (
            prefix,
            "SceneDecisionV2 只能收紧表达，绝不能改写、取消或替换用户已经明确的任务目标。",
        )

    def evaluate_proactive(self, proactive_scene_type: str) -> tuple[bool, str]:
        if self.proactive_policy == SCENE_PROJECTION_BLOCK:
            joined = ",".join(self.reason_codes) or "SCENE_V2_REVIEW_OR_BLOCK"
            return False, f"SceneDecisionV2 blocks proactive: {joined}"
        normalized = str(proactive_scene_type or "").strip().lower()
        if (
            self.proactive_policy == "CONTEXTUAL"
            and normalized in _GENERIC_PROACTIVE_SCENES
        ):
            return False, "SceneDecisionV2 low-interruption context blocks generic proactive"
        return True, "SceneDecisionV2 verified"


def project_scene_decision_v2(
    scene_decision_v2: Any,
    *,
    expected_identity: Any,
) -> SceneConsumerProjectionV1:
    """Verify a frozen Scene and produce the sole legacy consumer projection."""

    decision = _read_decision(scene_decision_v2)
    expected = _read_identity(expected_identity)
    actual = SceneIdentityV2.from_decision(decision)
    if actual != expected:
        raise SceneConsumerProjectionError(
            SCENE_IDENTITY_MISMATCH,
            "scene_decision_v2 differs from its frozen exact identity",
        )

    rank = {
        SCENE_PROJECTION_ALLOW: 0,
        SCENE_PROJECTION_RESTRICT: 1,
        SCENE_PROJECTION_REVIEW: 2,
        SCENE_PROJECTION_BLOCK: 3,
    }
    dispositions = tuple(item.disposition for item in decision.constraints)
    disposition = max(
        dispositions or (SCENE_PROJECTION_ALLOW,),
        key=rank.__getitem__,
    )
    codes = frozenset(item.code for item in decision.constraints)
    if "PROACTIVE_ACTION_BLOCKED" in codes:
        proactive_policy = SCENE_PROJECTION_BLOCK
    elif disposition in {SCENE_PROJECTION_BLOCK, SCENE_PROJECTION_REVIEW}:
        proactive_policy = SCENE_PROJECTION_BLOCK
    elif disposition == SCENE_PROJECTION_RESTRICT:
        proactive_policy = "CONTEXTUAL"
    else:
        proactive_policy = SCENE_PROJECTION_ALLOW
    if codes.intersection(_LOW_INTERRUPTION_CODES) and disposition == SCENE_PROJECTION_ALLOW:
        proactive_policy = "CONTEXTUAL"

    return SceneConsumerProjectionV1(
        decision=decision,
        identity=actual,
        disposition=disposition,
        expression_mode=_expression_mode(codes, disposition),
        proactive_policy=proactive_policy,
        reason_codes=tuple(sorted(codes)),
    )


def surface_fail_closed_projection(
    error: SceneConsumerProjectionError,
    *,
    user_goal_ref: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Preserve an explicit task while making missing Scene evidence visible."""

    reason_code = str(error.reason_code or SCENE_PROJECTION_INVALID)
    return (
        {
            "schema_version": SCENE_CONSUMER_PROJECTION_SCHEMA_V1,
            "schema": SCENE_CONSUMER_PROJECTION_SCHEMA_V1,
            "decision_id": "",
            "scene_type": "unverified",
            "scene_id": "",
            "revision": 0,
            "decision_digest": "",
            "proactive_policy": SCENE_PROJECTION_BLOCK,
            "expression_mode": "task_restrained",
            "reason_codes": (reason_code,),
            "constraints": {
                "proactive_policy": SCENE_PROJECTION_BLOCK,
                "expression_mode": "task_restrained",
                "can_change_user_goal": False,
                "can_expand_scope": False,
                "can_reduce_risk": False,
                "can_bypass_token": False,
                "permission_effect": "RESTRICT_ONLY",
            },
            "source_refs": (),
            "user_goal_ref": str(user_goal_ref or "").strip(),
            "user_goal_preserved": True,
            "read_only": True,
            "permission_effect": "RESTRICT_ONLY",
            "expired": False,
            "validation_status": "REJECTED",
        },
        (
            "SceneDecisionV2 未通过冻结身份校验：保持简短、任务优先，不得追加主动话题或行动。",
            "SceneDecisionV2 缺失或异常只能收紧表达，绝不能改写、取消或替换用户明确目标。",
        ),
    )


__all__ = [
    "SCENE_CONSUMER_PROJECTION_SCHEMA_V1",
    "SCENE_IDENTITY_MISMATCH",
    "SCENE_IDENTITY_REQUIRED",
    "SCENE_IDENTITY_SCHEMA_V2",
    "SCENE_PROJECTION_ALLOW",
    "SCENE_PROJECTION_BLOCK",
    "SCENE_PROJECTION_INVALID",
    "SCENE_PROJECTION_REQUIRED",
    "SCENE_PROJECTION_RESTRICT",
    "SCENE_PROJECTION_REVIEW",
    "SceneConsumerProjectionError",
    "SceneConsumerProjectionV1",
    "SceneIdentityV2",
    "project_scene_decision_v2",
    "surface_fail_closed_projection",
]
