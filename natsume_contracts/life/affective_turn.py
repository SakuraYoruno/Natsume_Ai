"""Natsume 情感 Surface 的不可变回合计划。

模型只可选择措辞，不能编造角色当前情感、关系许可、调节决定或具身状态；这些决定在
文本生成前冻结进 ``AffectiveTurnPlanV1``，并可与 Life 事件安全持久化。契约只含有
界标识、修订与表达控制，原始用户消息、媒体、思维链和私人记忆正文均不属于这里。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .relationship import AffectiveRepairTargetV1


_SELF_ID = "natsume_ai"
_COMPANION_ACT_KINDS = frozenset(
    {
        "COMFORT",
        "PLAY",
        "REPAIR",
        "SET_BOUNDARY",
        "GIVE_SPACE",
        "SILENCE",
        "SOLVE",
        "ACKNOWLEDGE",
        "HOLD_COMPLEXITY",
        "CONTAINED_JEALOUSY",
        "LISTEN",
        "RESPECT_AUTONOMY",
    }
)
_TEXT_PACING = frozenset({"STEADY", "SOFT", "BRISK", "SPARSE"})
_GENERATION_MODES = frozenset({"STREAM_ONE", "BUFFERED_CANDIDATE_SELECTION"})
_AVATAR_GAZES = frozenset({"ATTENTIVE", "SOFT", "AVERTED", "OPEN", "STEADY"})
_AVATAR_POSTURES = frozenset(
    {"RELAXED", "OPEN_GENTLE", "CONTAINED", "GROUNDED", "LIGHT", "STILL"}
)
_PREVIOUS_EXPRESSION_SOURCES = frozenset(
    {"DURABLE_LAST_ACCEPTED", "FALLBACK_NEUTRAL", "UNAVAILABLE"}
)
_COMPANION_PROFILE_STAGES = frozenset(
    {"BASELINE", "CANARY", "ACTIVE", "COMPATIBILITY_UNBOUND"}
)
_UTTERANCE_SEMANTIC_FOCI = frozenset(
    {
        "CURRENT_TURN",
        "UNNAMED_USER_AFFECT",
        "MIXED_USER_AFFECT",
        "RELATIONAL_IMPACT",
        "RELATIONAL_MEANING",
        "TASK_RESULT",
    }
)
_UTTERANCE_SELF_STANCES = frozenset(
    {
        "WARM_ATTENTIVE",
        "SHARED_MIXED_RESONANCE",
        "QUIET_PRESENT",
        "OWN_REPAIR",
        "OWN_REPAIR_WITH_CONTAINED_HURT",
        "HONEST_BOUNDARY",
        "CONTAINED_JEALOUSY",
        "SHARED_JOY",
        "PRACTICAL_CARE",
    }
)
_UTTERANCE_RESPONSE_MOVES = frozenset(
    {
        "RESPOND_DIRECTLY",
        "COMFORT_WITHOUT_NAMING",
        "HOLD_BOTH_SIDES",
        "RESONATE_WITH_BOTH_SIDES",
        "OWN_IMPACT",
        "ACKNOWLEDGE_IMPACT_WITH_HONEST_HURT",
        "STATE_BOUNDARY",
        "RESPECT_SPACE",
        "SHARE_FEELING",
        "SHARE_JOY",
        "SOLVE_FIRST",
    }
)
_UTTERANCE_INFERENCE_POLICIES = frozenset(
    {
        "BOUNDED_BY_EVIDENCE",
        "PRESERVE_UNCERTAINTY",
        "FROZEN_FACTS_ONLY",
        "NO_NEW_RELATIONSHIP_CLAIMS",
    }
)
_UTTERANCE_RELATIONSHIP_POSTURES = frozenset(
    {
        "CLOSE_WITHOUT_CLAIM",
        "REPAIR_UNRESOLVED",
        "BOUNDARIED_REPAIR",
        "RESPECT_AUTONOMY",
        "BOUNDARIED_CARE",
        "MUTUAL_WARMTH",
        "TASK_COMPANION",
    }
)
_UTTERANCE_QUESTION_POLICIES = frozenset({"NONE", "OPTIONAL_ONE", "TASK_ONLY"})
_UTTERANCE_ENDING_MODES = frozenset(
    {"NATURAL_STOP", "QUIET_STOP", "OWNERSHIP_STOP", "HONEST_STOP", "TASK_NEXT_STEP"}
)
_UTTERANCE_FORBIDDEN_CLAIMS = frozenset(
    {
        "INVENTED_HISTORY",
        "INVENTED_EFFECT",
        "UNSUPPORTED_USER_EMOTION",
        "IMPLIED_WAITING",
        "PREMATURE_REPAIR",
        "RELATIONSHIP_SELF_ERASURE",
        "RESPONSE_OBLIGATION",
        "RECENT_REPLY_REPETITION",
        "POLICY_DISCLAIMER",
    }
)


def _required(value: Any, field_name: str, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > limit:
        raise ValueError(f"{field_name} exceeds {limit} characters")
    return text


def _optional(value: Any, field_name: str, *, limit: int = 240) -> str | None:
    if value in (None, ""):
        return None
    return _required(value, field_name, limit=limit)


def _unit(value: Any, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return round(normalized, 6)


def _signed_unit(value: Any, field_name: str) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not -1.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be between -1 and 1")
    return round(normalized, 6)


def _bounded_float(
    value: Any,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return round(normalized, 6)


def _bounded_int(
    value: Any,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    normalized = _non_negative_int(value, field_name)
    if not minimum <= normalized <= maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return normalized


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if normalized < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return normalized


def _refs(value: Any, field_name: str, *, maximum: int = 16) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{field_name} must be a list")
    output = tuple(
        _required(item, f"{field_name}[{index}]", limit=512)
        for index, item in enumerate(value)
    )
    if len(output) > maximum:
        raise ValueError(f"{field_name} cannot contain more than {maximum} items")
    if len(set(output)) != len(output):
        raise ValueError(f"{field_name} must not contain duplicates")
    return output


def _sha256(value: Any, field_name: str) -> str:
    text = _required(value, field_name, limit=64).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ValueError(f"{field_name} must be a sha256 hex digest")
    return text


@dataclass(frozen=True, slots=True)
class CompanionActV1:
    """生成文案前已选定的有界陪伴动作，模型只能在该动作范围内实现措辞。"""

    companion_act_id: str
    relationship_subject_id: str
    kind: str
    intensity: float
    reason_refs: Sequence[str]
    created_at_ms: int
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        kind = _required(self.kind, "kind", limit=64).upper()
        if kind not in _COMPANION_ACT_KINDS:
            raise ValueError(f"kind must be one of {sorted(_COMPANION_ACT_KINDS)}")
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        object.__setattr__(self, "companion_act_id", _required(self.companion_act_id, "companion_act_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "intensity", _unit(self.intensity, "intensity"))
        object.__setattr__(self, "reason_refs", _refs(self.reason_refs, "reason_refs"))
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_act.v1",
            "companion_act_id": self.companion_act_id,
            "self_id": self.self_id,
            "relationship_subject_id": self.relationship_subject_id,
            "kind": self.kind,
            "intensity": self.intensity,
            "reason_refs": list(self.reason_refs),
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True, slots=True)
class CompanionUtteranceFrameV1:
    """自然措辞的轻量语义信封，而非另一份状态日志。

    信封仅使用枚举，故可随既有回合计划传递而无需新增持久化或普通聊天审计；它排除
    原始用户消息、私有记忆和生成文案，并通过禁用声明约束模型不作无证关系推断。
    """

    frame_id: str
    companion_act_kind: str
    semantic_focus: str
    self_stance: str
    response_move: str
    inference_policy: str
    relationship_posture: str
    question_policy: str
    ending_mode: str
    forbidden_claims: Sequence[str]
    created_at_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_id", _required(self.frame_id, "frame_id"))
        act = _required(self.companion_act_kind, "companion_act_kind", limit=64).upper()
        if act not in _COMPANION_ACT_KINDS:
            raise ValueError(
                "companion_act_kind must be one of "
                f"{sorted(_COMPANION_ACT_KINDS)}"
            )
        object.__setattr__(self, "companion_act_kind", act)
        for name, allowed in (
            ("semantic_focus", _UTTERANCE_SEMANTIC_FOCI),
            ("self_stance", _UTTERANCE_SELF_STANCES),
            ("response_move", _UTTERANCE_RESPONSE_MOVES),
            ("inference_policy", _UTTERANCE_INFERENCE_POLICIES),
            ("relationship_posture", _UTTERANCE_RELATIONSHIP_POSTURES),
            ("question_policy", _UTTERANCE_QUESTION_POLICIES),
            ("ending_mode", _UTTERANCE_ENDING_MODES),
        ):
            normalized = _required(getattr(self, name), name, limit=64).upper()
            if normalized not in allowed:
                raise ValueError(f"{name} must be one of {sorted(allowed)}")
            object.__setattr__(self, name, normalized)
        claims = tuple(
            _required(item, f"forbidden_claims[{index}]", limit=64).upper()
            for index, item in enumerate(self.forbidden_claims or ())
        )
        if len(claims) > 8:
            raise ValueError("forbidden_claims cannot contain more than 8 items")
        if len(set(claims)) != len(claims):
            raise ValueError("forbidden_claims must not contain duplicates")
        unknown = sorted(set(claims).difference(_UTTERANCE_FORBIDDEN_CLAIMS))
        if unknown:
            raise ValueError(
                "forbidden_claims contains unsupported values: " + ", ".join(unknown)
            )
        object.__setattr__(self, "forbidden_claims", claims)
        object.__setattr__(
            self,
            "created_at_ms",
            _non_negative_int(self.created_at_ms, "created_at_ms"),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompanionUtteranceFrameV1":
        """从持久化映射恢复语义框架；未知 schema 立即失败以免放宽表达边界。"""
        payload = dict(value) if isinstance(value, Mapping) else {}
        schema = str(payload.get("schema_version") or "").strip()
        if schema and schema != "companion_utterance_frame.v1":
            raise ValueError("unsupported companion utterance frame schema")
        return cls(
            frame_id=payload.get("frame_id"),
            companion_act_kind=payload.get("companion_act_kind"),
            semantic_focus=payload.get("semantic_focus"),
            self_stance=payload.get("self_stance"),
            response_move=payload.get("response_move"),
            inference_policy=payload.get("inference_policy"),
            relationship_posture=payload.get("relationship_posture"),
            question_policy=payload.get("question_policy"),
            ending_mode=payload.get("ending_mode"),
            forbidden_claims=payload.get("forbidden_claims") or (),
            created_at_ms=payload.get("created_at_ms"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_utterance_frame.v1",
            "frame_id": self.frame_id,
            "companion_act_kind": self.companion_act_kind,
            "semantic_focus": self.semantic_focus,
            "self_stance": self.self_stance,
            "response_move": self.response_move,
            "inference_policy": self.inference_policy,
            "relationship_posture": self.relationship_posture,
            "question_policy": self.question_policy,
            "ending_mode": self.ending_mode,
            "forbidden_claims": list(self.forbidden_claims),
            "created_at_ms": self.created_at_ms,
        }


@dataclass(frozen=True, slots=True)
class ExpressionPlanV1:
    """文本、语音与头像共用的一次不可变表达修订。

    这是指令而非远端 TTS/头像已经应用的收据，投递结果另行记录。三种模态在同一计划
    内冻结，可防止模型输出、旧文本情绪解析器或通道失败悄悄改写既定的自我情感。
    """

    expression_plan_id: str
    synchronization_id: str
    self_affect_revision: int
    text_pacing: str
    voice_style: str
    avatar_expression: str
    regulation_mode: str
    created_at_ms: int
    voice_rate: float = 1.0
    voice_energy: float = 0.5
    voice_pitch: float = 0.0
    voice_pause_ms: int = 120
    avatar_gaze: str = "ATTENTIVE"
    avatar_posture: str = "RELAXED"
    avatar_transition_ms: int = 240
    avatar_behavior: str = "CALM_ATTENTIVE"
    avatar_motion_intensity: float = 0.5
    companion_profile_id: str = ""
    companion_profile_digest: str = ""
    previous_avatar_expression: str = "NEUTRAL"
    previous_expression_source: str = "FALLBACK_NEUTRAL"

    def __post_init__(self) -> None:
        # profile ID 与摘要必须成对，确保恢复后仍能证明表达使用了哪版陪伴配置。
        pacing = _required(self.text_pacing, "text_pacing", limit=48).upper()
        if pacing not in _TEXT_PACING:
            raise ValueError(f"text_pacing must be one of {sorted(_TEXT_PACING)}")
        object.__setattr__(self, "expression_plan_id", _required(self.expression_plan_id, "expression_plan_id"))
        object.__setattr__(self, "synchronization_id", _required(self.synchronization_id, "synchronization_id"))
        object.__setattr__(
            self,
            "self_affect_revision",
            _non_negative_int(self.self_affect_revision, "self_affect_revision"),
        )
        object.__setattr__(self, "text_pacing", pacing)
        object.__setattr__(self, "voice_style", _required(self.voice_style, "voice_style", limit=64).upper())
        object.__setattr__(
            self,
            "avatar_expression",
            _required(self.avatar_expression, "avatar_expression", limit=64).upper(),
        )
        object.__setattr__(
            self,
            "regulation_mode",
            _required(self.regulation_mode, "regulation_mode", limit=64).upper(),
        )
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(
            self,
            "voice_rate",
            _bounded_float(
                self.voice_rate,
                "voice_rate",
                minimum=0.7,
                maximum=1.3,
            ),
        )
        object.__setattr__(self, "voice_energy", _unit(self.voice_energy, "voice_energy"))
        object.__setattr__(self, "voice_pitch", _signed_unit(self.voice_pitch, "voice_pitch"))
        object.__setattr__(
            self,
            "voice_pause_ms",
            _bounded_int(
                self.voice_pause_ms,
                "voice_pause_ms",
                minimum=0,
                maximum=3_000,
            ),
        )
        gaze = _required(self.avatar_gaze, "avatar_gaze", limit=64).upper()
        if gaze not in _AVATAR_GAZES:
            raise ValueError(f"avatar_gaze must be one of {sorted(_AVATAR_GAZES)}")
        posture = _required(self.avatar_posture, "avatar_posture", limit=64).upper()
        if posture not in _AVATAR_POSTURES:
            raise ValueError(f"avatar_posture must be one of {sorted(_AVATAR_POSTURES)}")
        object.__setattr__(self, "avatar_gaze", gaze)
        object.__setattr__(self, "avatar_posture", posture)
        object.__setattr__(
            self,
            "avatar_transition_ms",
            _bounded_int(
                self.avatar_transition_ms,
                "avatar_transition_ms",
                minimum=0,
                maximum=5_000,
            ),
        )
        object.__setattr__(
            self,
            "avatar_behavior",
            _required(self.avatar_behavior, "avatar_behavior", limit=64).upper(),
        )
        object.__setattr__(
            self,
            "avatar_motion_intensity",
            _unit(self.avatar_motion_intensity, "avatar_motion_intensity"),
        )
        profile_id = str(self.companion_profile_id or "").strip()
        profile_digest = str(self.companion_profile_digest or "").strip()
        if bool(profile_id) != bool(profile_digest):
            raise ValueError("expression companion profile id and digest must appear together")
        if profile_id:
            object.__setattr__(
                self,
                "companion_profile_id",
                _required(profile_id, "companion_profile_id", limit=240),
            )
            object.__setattr__(
                self,
                "companion_profile_digest",
                _sha256(profile_digest, "companion_profile_digest"),
            )
        else:
            object.__setattr__(self, "companion_profile_id", "")
            object.__setattr__(self, "companion_profile_digest", "")
        object.__setattr__(
            self,
            "previous_avatar_expression",
            _required(
                self.previous_avatar_expression,
                "previous_avatar_expression",
                limit=64,
            ).upper(),
        )
        previous_source = _required(
            self.previous_expression_source,
            "previous_expression_source",
            limit=64,
        ).upper()
        if previous_source not in _PREVIOUS_EXPRESSION_SOURCES:
            raise ValueError(
                "previous_expression_source must be one of "
                f"{sorted(_PREVIOUS_EXPRESSION_SOURCES)}"
            )
        object.__setattr__(self, "previous_expression_source", previous_source)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "expression_plan.v1",
            "expression_plan_id": self.expression_plan_id,
            "synchronization_id": self.synchronization_id,
            "self_affect_revision": self.self_affect_revision,
            "text_pacing": self.text_pacing,
            "voice_style": self.voice_style,
            "avatar_expression": self.avatar_expression,
            "regulation_mode": self.regulation_mode,
            "created_at_ms": self.created_at_ms,
            "voice_rate": self.voice_rate,
            "voice_energy": self.voice_energy,
            "voice_pitch": self.voice_pitch,
            "voice_pause_ms": self.voice_pause_ms,
            "avatar_gaze": self.avatar_gaze,
            "avatar_posture": self.avatar_posture,
            "avatar_transition_ms": self.avatar_transition_ms,
            "avatar_behavior": self.avatar_behavior,
            "avatar_motion_intensity": self.avatar_motion_intensity,
            "companion_profile_id": self.companion_profile_id,
            "companion_profile_digest": self.companion_profile_digest,
            "previous_avatar_expression": self.previous_avatar_expression,
            "previous_expression_source": self.previous_expression_source,
        }


@dataclass(frozen=True, slots=True)
class AffectiveTurnPlanV1:
    """一次对用户可见回合的冻结因果计划。

    它把情感/关系修订、调节、陪伴动作、语义框架和多模态表达绑定为同一原子语义，
    使异步生成、候选选择和故障恢复均不能在中途混用另一次状态的决定。
    """

    plan_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    request_id: str
    self_affect_revision: int
    self_affect_digest: str
    self_emotion_tag: str
    relationship_revision: int
    regulation_ref: str | None
    companion_act: CompanionActV1
    utterance_frame: CompanionUtteranceFrameV1
    expression_plan: ExpressionPlanV1
    generation_mode: str
    candidate_count: int
    created_at_ms: int
    self_id: str = _SELF_ID
    companion_profile_id: str = ""
    companion_profile_digest: str = ""
    companion_profile_revision: int = 0
    companion_profile_stage: str = "COMPATIBILITY_UNBOUND"
    affect_repair_target: AffectiveRepairTargetV1 | None = None

    def __post_init__(self) -> None:
        # 生成模式与候选数成对校验，避免恢复路径改变原计划的选择/流式语义。
        mode = _required(self.generation_mode, "generation_mode", limit=64).upper()
        if mode not in _GENERATION_MODES:
            raise ValueError(f"generation_mode must be one of {sorted(_GENERATION_MODES)}")
        candidate_count = _non_negative_int(self.candidate_count, "candidate_count")
        if candidate_count not in {1, 2}:
            raise ValueError("candidate_count must be one or two")
        if mode == "STREAM_ONE" and candidate_count != 1:
            raise ValueError("STREAM_ONE requires candidate_count=1")
        if mode == "BUFFERED_CANDIDATE_SELECTION" and candidate_count != 2:
            raise ValueError("BUFFERED_CANDIDATE_SELECTION requires candidate_count=2")
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        if not isinstance(self.companion_act, CompanionActV1):
            raise ValueError("companion_act must be CompanionActV1")
        if not isinstance(self.utterance_frame, CompanionUtteranceFrameV1):
            raise ValueError("utterance_frame must be CompanionUtteranceFrameV1")
        if not isinstance(self.expression_plan, ExpressionPlanV1):
            raise ValueError("expression_plan must be ExpressionPlanV1")
        object.__setattr__(self, "plan_id", _required(self.plan_id, "plan_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "request_id", _required(self.request_id, "request_id"))
        object.__setattr__(
            self,
            "self_affect_revision",
            _non_negative_int(self.self_affect_revision, "self_affect_revision"),
        )
        object.__setattr__(self, "self_affect_digest", _sha256(self.self_affect_digest, "self_affect_digest"))
        object.__setattr__(
            self,
            "self_emotion_tag",
            _required(self.self_emotion_tag, "self_emotion_tag", limit=64).upper(),
        )
        object.__setattr__(
            self,
            "relationship_revision",
            _non_negative_int(self.relationship_revision, "relationship_revision"),
        )
        object.__setattr__(self, "regulation_ref", _optional(self.regulation_ref, "regulation_ref", limit=512))
        object.__setattr__(self, "generation_mode", mode)
        object.__setattr__(self, "candidate_count", candidate_count)
        object.__setattr__(self, "created_at_ms", _non_negative_int(self.created_at_ms, "created_at_ms"))
        object.__setattr__(self, "self_id", _SELF_ID)
        profile_id = str(self.companion_profile_id or "").strip()
        profile_digest = str(self.companion_profile_digest or "").strip()
        stage = _required(
            self.companion_profile_stage,
            "companion_profile_stage",
            limit=64,
        ).upper()
        if stage not in _COMPANION_PROFILE_STAGES:
            raise ValueError(
                "companion_profile_stage must be one of "
                f"{sorted(_COMPANION_PROFILE_STAGES)}"
            )
        revision = _non_negative_int(
            self.companion_profile_revision,
            "companion_profile_revision",
        )
        if bool(profile_id) != bool(profile_digest):
            raise ValueError("turn companion profile id and digest must appear together")
        if not profile_id and stage != "COMPATIBILITY_UNBOUND":
            raise ValueError("bound companion profile stage requires profile identity")
        if profile_id:
            object.__setattr__(
                self,
                "companion_profile_id",
                _required(profile_id, "companion_profile_id", limit=240),
            )
            object.__setattr__(
                self,
                "companion_profile_digest",
                _sha256(profile_digest, "companion_profile_digest"),
            )
        else:
            object.__setattr__(self, "companion_profile_id", "")
            object.__setattr__(self, "companion_profile_digest", "")
        object.__setattr__(self, "companion_profile_revision", revision)
        object.__setattr__(self, "companion_profile_stage", stage)
        repair_target = self.affect_repair_target
        if repair_target is not None:
            if not isinstance(repair_target, AffectiveRepairTargetV1):
                raise ValueError("affect_repair_target must be AffectiveRepairTargetV1")
            if (
                repair_target.workspace_id != self.workspace_id
                or repair_target.relationship_subject_id
                != self.relationship_subject_id
                or repair_target.session_id != self.session_id
                or repair_target.self_affect_revision != self.self_affect_revision
            ):
                raise ValueError("turn affect_repair_target scope or revision drift")
        if self.companion_act.relationship_subject_id != self.relationship_subject_id:
            raise ValueError("companion_act relationship subject differs from turn plan")
        if self.utterance_frame.companion_act_kind != self.companion_act.kind:
            raise ValueError("utterance frame companion act differs from turn plan")
        if self.expression_plan.self_affect_revision != self.self_affect_revision:
            raise ValueError("expression plan affect revision differs from turn plan")
        if (
            self.expression_plan.companion_profile_id
            != self.companion_profile_id
            or self.expression_plan.companion_profile_digest
            != self.companion_profile_digest
        ):
            raise ValueError("expression plan companion profile differs from turn plan")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "affective_turn_plan.v1",
            "plan_id": self.plan_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "self_affect_revision": self.self_affect_revision,
            "self_affect_digest": self.self_affect_digest,
            "self_emotion_tag": self.self_emotion_tag,
            "relationship_revision": self.relationship_revision,
            "regulation_ref": self.regulation_ref,
            "companion_act": self.companion_act.as_dict(),
            "utterance_frame": self.utterance_frame.as_dict(),
            "expression_plan": self.expression_plan.as_dict(),
            "generation_mode": self.generation_mode,
            "candidate_count": self.candidate_count,
            "created_at_ms": self.created_at_ms,
            "companion_profile_id": self.companion_profile_id,
            "companion_profile_digest": self.companion_profile_digest,
            "companion_profile_revision": self.companion_profile_revision,
            "companion_profile_stage": self.companion_profile_stage,
            "affect_repair_target": (
                self.affect_repair_target.as_dict()
                if self.affect_repair_target is not None
                else None
            ),
        }


__all__ = [
    "AffectiveTurnPlanV1",
    "CompanionActV1",
    "CompanionUtteranceFrameV1",
    "ExpressionPlanV1",
]
