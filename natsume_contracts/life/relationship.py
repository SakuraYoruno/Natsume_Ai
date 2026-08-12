"""Seven Seas 关系生命系统的版本化、隐私有界契约。

契约刻意将关系证据与自动“亲密度分数”分开：一次回应只能开启暂定片段，只有后续的
用户交互才能形成后效。沉默和技术投递失败既不证明信任，也不构成同意。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_SELF_ID = "natsume_ai"
_MAX_EVIDENCE_REFS = 16
_MAX_ACTIVE_EPISODES = 4
_EPISODE_SOURCES = frozenset({"CHAT_RESPONSE", "PROACTIVE_MESSAGE"})
_AFTEREFFECT_OUTCOMES = frozenset(
    {
        "HELPFUL",
        "MISUNDERSTOOD",
        "BOUNDARY",
        "REPAIR_ACCEPTED",
        "REPAIR_UNRESOLVED",
        "NEUTRAL",
        "SILENCE",
        "DELIVERY_FAILED",
    }
)
_PROACTIVE_PERMISSIONS = frozenset({"UNKNOWN", "ALLOWED", "DENIED"})
_CONSENT_STATES = frozenset({"NOT_GRANTED", "REVIEW_REQUIRED", "GRANTED"})
_COMPANION_PROACTIVE_PERMISSION_REQUEST_SCHEMA = (
    "companion_proactive_permission_request.v1"
)
_COMPANION_RESPONSE_FEEDBACK_REQUEST_SCHEMA = (
    "companion_response_feedback_request.v1"
)
_EXPLICIT_RESPONSE_FEEDBACK = frozenset(
    {"HELPFUL", "NEUTRAL", "MISUNDERSTOOD", "BOUNDARY"}
)


def _required(value: Any, name: str, *, limit: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > limit:
        raise ValueError(f"{name} exceeds {limit} characters")
    return text


def _unit(value: Any, name: str, *, default: float = 0.0) -> float:
    try:
        number = float(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return round(number, 6)


def _non_negative_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if number < 0:
        raise ValueError(f"{name} must be non-negative")
    return number


def _refs(value: Any, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be a list")
    result = tuple(_required(item, f"{name}[{index}]", limit=512) for index, item in enumerate(value))
    if len(result) > _MAX_EVIDENCE_REFS:
        raise ValueError(f"{name} cannot contain more than {_MAX_EVIDENCE_REFS} items")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    return result


@dataclass(frozen=True)
class CompanionProactivePermissionRequestV1:
    """一次经用户认证的结构化主动联系决定。

    窄契约排除自由文本、消息正文、推断感受和路由目标；Host 从签名的 Secure Ingress
    claim 取得证据，规范关系聚合独占由此产生的状态迁移权。
    """

    workspace_id: str
    relationship_subject_id: str
    session_id: str
    proactive_permission: str
    schema_version: str = _COMPANION_PROACTIVE_PERMISSION_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _COMPANION_PROACTIVE_PERMISSION_REQUEST_SCHEMA:
            raise ValueError("companion proactive permission request schema is invalid")
        permission = _required(
            self.proactive_permission,
            "proactive_permission",
            limit=64,
        ).upper()
        if permission not in {"ALLOWED", "DENIED"}:
            raise ValueError("proactive_permission must be ALLOWED or DENIED")
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "proactive_permission", permission)
        object.__setattr__(
            self,
            "schema_version",
            _COMPANION_PROACTIVE_PERMISSION_REQUEST_SCHEMA,
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CompanionProactivePermissionRequestV1":
        """以精确字段集恢复用户决定，拒绝空白规范化带来的签名语义歧义。"""
        if not isinstance(value, Mapping):
            raise ValueError("companion proactive permission request must be an object")
        expected = {
            "schema_version",
            "workspace_id",
            "relationship_subject_id",
            "session_id",
            "proactive_permission",
        }
        if set(value) != expected:
            raise ValueError("companion proactive permission request field set is invalid")
        for field_name in expected:
            if not isinstance(value.get(field_name), str):
                raise ValueError(
                    f"companion proactive permission request {field_name} must be a string"
                )
            if value[field_name] != value[field_name].strip():
                raise ValueError(
                    f"companion proactive permission request {field_name} cannot have outer whitespace"
                )
        if value["proactive_permission"] not in {"ALLOWED", "DENIED"}:
            raise ValueError("companion proactive permission request permission must be exact")
        return cls(
            schema_version=value["schema_version"],
            workspace_id=value["workspace_id"],
            relationship_subject_id=value["relationship_subject_id"],
            session_id=value["session_id"],
            proactive_permission=value["proactive_permission"],
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "proactive_permission": self.proactive_permission,
        }


@dataclass(frozen=True)
class CompanionResponseFeedbackRequestV1:
    """一次经用户认证、无正文的已接受回应评价。

    请求指向既有规范回应收据，而非复制助手文本或接受任意评分。关系聚合必须先确认
    收据仍拥有活跃的暂定片段，才可将评价转为关系后效。
    """

    workspace_id: str
    relationship_subject_id: str
    session_id: str
    response_receipt_ref: str
    feedback: str
    schema_version: str = _COMPANION_RESPONSE_FEEDBACK_REQUEST_SCHEMA

    def __post_init__(self) -> None:
        if self.schema_version != _COMPANION_RESPONSE_FEEDBACK_REQUEST_SCHEMA:
            raise ValueError("companion response feedback request schema is invalid")
        workspace = _required(self.workspace_id, "workspace_id")
        subject = _required(
            self.relationship_subject_id,
            "relationship_subject_id",
        )
        session = _required(self.session_id, "session_id")
        receipt_ref = _required(
            self.response_receipt_ref,
            "response_receipt_ref",
            limit=512,
        )
        if not receipt_ref.startswith(("life-event://", "life-delivery://")):
            raise ValueError(
                "response_receipt_ref must reference one accepted Life response"
            )
        feedback = _required(self.feedback, "feedback", limit=64).upper()
        if feedback not in _EXPLICIT_RESPONSE_FEEDBACK:
            raise ValueError(
                "feedback must be one of "
                f"{sorted(_EXPLICIT_RESPONSE_FEEDBACK)}"
            )
        object.__setattr__(self, "workspace_id", workspace)
        object.__setattr__(self, "relationship_subject_id", subject)
        object.__setattr__(self, "session_id", session)
        object.__setattr__(self, "response_receipt_ref", receipt_ref)
        object.__setattr__(self, "feedback", feedback)
        object.__setattr__(
            self,
            "schema_version",
            _COMPANION_RESPONSE_FEEDBACK_REQUEST_SCHEMA,
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "CompanionResponseFeedbackRequestV1":
        """保持字段和值的精确性，使入口认证与聚合匹配共享同一请求语义。"""
        if not isinstance(value, Mapping):
            raise ValueError("companion response feedback request must be an object")
        expected = {
            "schema_version",
            "workspace_id",
            "relationship_subject_id",
            "session_id",
            "response_receipt_ref",
            "feedback",
        }
        if set(value) != expected:
            raise ValueError("companion response feedback request field set is invalid")
        for field_name in expected:
            raw = value.get(field_name)
            if not isinstance(raw, str) or raw != raw.strip():
                raise ValueError(
                    "companion response feedback request "
                    f"{field_name} must be an exact string"
                )
        if value["feedback"] not in _EXPLICIT_RESPONSE_FEEDBACK:
            raise ValueError("companion response feedback must be exact")
        return cls(
            schema_version=value["schema_version"],
            workspace_id=value["workspace_id"],
            relationship_subject_id=value["relationship_subject_id"],
            session_id=value["session_id"],
            response_receipt_ref=value["response_receipt_ref"],
            feedback=value["feedback"],
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "response_receipt_ref": self.response_receipt_ref,
            "feedback": self.feedback,
        }


@dataclass(frozen=True)
class AffectiveRepairTargetV1:
    # 关系后效跨会话，而自我情感按会话隔离；此对象是两聚合间唯一的有界修复桥梁。
    # 它只包含标识与修订，不能携带用户话语、助手回复或私有证据正文。
    """One exact, internal-only affect episode that a repair may settle.

    A relationship aftereffect and Natsume's self-affect live in separate
    aggregates: the former is continuous across sessions while the latter is
    deliberately session-scoped.  This bounded reference is the only bridge
    between them.  It contains identifiers and revisions only—never a user
    utterance, an assistant reply, or a private evidence body.
    """

    affect_aggregate_id: str
    affect_episode_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    self_affect_revision: int
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        aggregate_id = _required(
            self.affect_aggregate_id,
            "affect_aggregate_id",
            limit=240,
        )
        if not aggregate_id.startswith("affect::"):
            raise ValueError("affect_aggregate_id must reference an affect aggregate")
        object.__setattr__(self, "affect_aggregate_id", aggregate_id)
        object.__setattr__(
            self,
            "affect_episode_id",
            _required(self.affect_episode_id, "affect_episode_id", limit=240),
        )
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(
            self,
            "self_affect_revision",
            _non_negative_int(self.self_affect_revision, "self_affect_revision"),
        )
        object.__setattr__(self, "self_id", _SELF_ID)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AffectiveRepairTargetV1":
        if not isinstance(value, Mapping):
            raise ValueError("affect repair target must be an object")
        expected = {
            "schema_version",
            "self_id",
            "affect_aggregate_id",
            "affect_episode_id",
            "workspace_id",
            "relationship_subject_id",
            "session_id",
            "self_affect_revision",
        }
        if set(value) != expected:
            raise ValueError("affect repair target field set is invalid")
        if value.get("schema_version") != "affective_repair_target.v1":
            raise ValueError("affect repair target schema is invalid")
        return cls(
            affect_aggregate_id=value.get("affect_aggregate_id"),
            affect_episode_id=value.get("affect_episode_id"),
            workspace_id=value.get("workspace_id"),
            relationship_subject_id=value.get("relationship_subject_id"),
            session_id=value.get("session_id"),
            self_affect_revision=value.get("self_affect_revision"),
            self_id=value.get("self_id") or _SELF_ID,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "affective_repair_target.v1",
            "self_id": self.self_id,
            "affect_aggregate_id": self.affect_aggregate_id,
            "affect_episode_id": self.affect_episode_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "self_affect_revision": self.self_affect_revision,
        }


@dataclass(frozen=True)
class RelationshipEpisodeV1:
    """等待用户侧证据的暂定已接受回应。

    片段绑定回应摘要和作用域，避免同一反馈被挪用于另一对象/会话；它不是关系分数
    的立即更新，更不授予主动联系或亲密同意。
    """

    episode_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    source_kind: str
    response_receipt_ref: str
    response_digest: str
    opened_at_ms: int
    evidence_refs: Sequence[str] = ()
    affect_repair_target: AffectiveRepairTargetV1 | None = None
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        # 修复目标只能留在同一工作区和关系对象，防止跨聚合结清错误的情感片段。
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        source_kind = _required(self.source_kind, "source_kind", limit=64).upper()
        if source_kind not in _EPISODE_SOURCES:
            raise ValueError(f"source_kind must be one of {sorted(_EPISODE_SOURCES)}")
        object.__setattr__(self, "episode_id", _required(self.episode_id, "episode_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "relationship_subject_id", _required(self.relationship_subject_id, "relationship_subject_id"))
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "response_receipt_ref", _required(self.response_receipt_ref, "response_receipt_ref", limit=512))
        object.__setattr__(self, "response_digest", _required(self.response_digest, "response_digest", limit=128))
        object.__setattr__(self, "opened_at_ms", _non_negative_int(self.opened_at_ms, "opened_at_ms"))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))
        target = self.affect_repair_target
        if target is not None:
            if not isinstance(target, AffectiveRepairTargetV1):
                raise ValueError("affect_repair_target must be AffectiveRepairTargetV1")
            if (
                target.workspace_id != self.workspace_id
                or target.relationship_subject_id != self.relationship_subject_id
            ):
                raise ValueError("affect_repair_target scope drift")
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "relationship_episode.v1",
            "episode_id": self.episode_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "source_kind": self.source_kind,
            "response_receipt_ref": self.response_receipt_ref,
            "response_digest": self.response_digest,
            "opened_at_ms": self.opened_at_ms,
            "evidence_refs": list(self.evidence_refs),
            "affect_repair_target": (
                self.affect_repair_target.as_dict()
                if self.affect_repair_target is not None
                else None
            ),
        }


@dataclass(frozen=True)
class CompanionProactiveTargetV1:
    """由已接受前台回应登记的一条作用域内投递目的地。

    它只是关系聚合的路由能力，不是亲密证据或联系许可；调度器仍须取得独立的
    ``proactive_permission``。这里不保留用户或助手正文。
    """

    target_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    platform: str
    conversation_id: str
    source_receipt_ref: str
    registered_at_ms: int
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        platform = _required(self.platform, "platform", limit=80).lower()
        object.__setattr__(self, "target_id", _required(self.target_id, "target_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(
            self,
            "relationship_subject_id",
            _required(self.relationship_subject_id, "relationship_subject_id"),
        )
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "platform", platform)
        object.__setattr__(
            self,
            "conversation_id",
            _required(self.conversation_id, "conversation_id", limit=512),
        )
        object.__setattr__(
            self,
            "source_receipt_ref",
            _required(self.source_receipt_ref, "source_receipt_ref", limit=512),
        )
        object.__setattr__(
            self,
            "registered_at_ms",
            _non_negative_int(self.registered_at_ms, "registered_at_ms"),
        )
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "companion_proactive_target.v1",
            "target_id": self.target_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "platform": self.platform,
            "conversation_id": self.conversation_id,
            "source_receipt_ref": self.source_receipt_ref,
            "registered_at_ms": self.registered_at_ms,
        }


@dataclass(frozen=True)
class RelationalAftereffectV1:
    """绑定某一暂定回应片段的、随后发生的用户侧结果。

    只有明确接受修复的结果才可携带情感修复目标；沉默/投递失败强制 helpfulness 为零，
    从而不把缺少用户反馈误读为正面关系证据。
    """

    aftereffect_id: str
    episode_id: str
    workspace_id: str
    relationship_subject_id: str
    session_id: str
    source_receipt_ref: str
    user_input_ref: str
    outcome: str
    helpfulness: float
    boundary_respected: bool
    repair_acknowledged: bool
    observed_at_ms: int
    evidence_refs: Sequence[str] = ()
    affect_repair_target: AffectiveRepairTargetV1 | None = None
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        # 修复认可与 REPAIR_ACCEPTED 必须原子对应，避免事后以泛化结果结清情感债务。
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        outcome = _required(self.outcome, "outcome", limit=64).upper()
        if outcome not in _AFTEREFFECT_OUTCOMES:
            raise ValueError(f"outcome must be one of {sorted(_AFTEREFFECT_OUTCOMES)}")
        helpfulness = _unit(self.helpfulness, "helpfulness")
        if outcome in {"SILENCE", "DELIVERY_FAILED"} and helpfulness != 0.0:
            raise ValueError("silence or delivery failure cannot claim helpfulness")
        if self.repair_acknowledged and outcome != "REPAIR_ACCEPTED":
            raise ValueError("repair_acknowledged requires REPAIR_ACCEPTED")
        target = self.affect_repair_target
        if target is not None:
            if not isinstance(target, AffectiveRepairTargetV1):
                raise ValueError("affect_repair_target must be AffectiveRepairTargetV1")
            if outcome != "REPAIR_ACCEPTED" or not self.repair_acknowledged:
                raise ValueError(
                    "affect_repair_target requires acknowledged repair acceptance"
                )
            if (
                target.workspace_id != self.workspace_id
                or target.relationship_subject_id != self.relationship_subject_id
            ):
                raise ValueError("affect_repair_target scope drift")
        object.__setattr__(self, "aftereffect_id", _required(self.aftereffect_id, "aftereffect_id"))
        object.__setattr__(self, "episode_id", _required(self.episode_id, "episode_id"))
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "relationship_subject_id", _required(self.relationship_subject_id, "relationship_subject_id"))
        object.__setattr__(self, "session_id", _required(self.session_id, "session_id"))
        object.__setattr__(self, "source_receipt_ref", _required(self.source_receipt_ref, "source_receipt_ref", limit=512))
        object.__setattr__(self, "user_input_ref", _required(self.user_input_ref, "user_input_ref", limit=512))
        object.__setattr__(self, "outcome", outcome)
        object.__setattr__(self, "helpfulness", helpfulness)
        object.__setattr__(self, "boundary_respected", bool(self.boundary_respected))
        object.__setattr__(self, "repair_acknowledged", bool(self.repair_acknowledged))
        object.__setattr__(self, "observed_at_ms", _non_negative_int(self.observed_at_ms, "observed_at_ms"))
        object.__setattr__(self, "evidence_refs", _refs(self.evidence_refs, "evidence_refs"))
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "relational_aftereffect.v1",
            "aftereffect_id": self.aftereffect_id,
            "episode_id": self.episode_id,
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "session_id": self.session_id,
            "source_receipt_ref": self.source_receipt_ref,
            "user_input_ref": self.user_input_ref,
            "outcome": self.outcome,
            "helpfulness": self.helpfulness,
            "boundary_respected": self.boundary_respected,
            "repair_acknowledged": self.repair_acknowledged,
            "observed_at_ms": self.observed_at_ms,
            "evidence_refs": list(self.evidence_refs),
            "affect_repair_target": (
                self.affect_repair_target.as_dict()
                if self.affect_repair_target is not None
                else None
            ),
        }


@dataclass(frozen=True)
class RelationshipStateV2:
    """有界、具备同意识别的关系投影。

    信任、安全和默契与主动联系许可、全部亲密同意状态相互独立；后者均不能从评分或
    温暖回应推断。revision 和作用域检查使并发恢复不能把别的关系状态混入当前投影。
    """

    workspace_id: str
    relationship_subject_id: str
    revision: int
    trust: float
    safety: float
    attunement: float
    rupture_load: float
    repair_required: bool
    proactive_permission: str
    active_episodes: Sequence[RelationshipEpisodeV1] = ()
    last_aftereffect: RelationalAftereffectV1 | None = None
    pending_affect_repair_targets: Sequence[AffectiveRepairTargetV1] = ()
    romantic_consent: str = "NOT_GRANTED"
    exclusive_consent: str = "NOT_GRANTED"
    dependency_consent: str = "NOT_GRANTED"
    self_id: str = _SELF_ID

    def __post_init__(self) -> None:
        # 同意状态逐项验证，避免用一个综合关系分数隐式提升任何敏感许可。
        if _required(self.self_id, "self_id", limit=64) != _SELF_ID:
            raise ValueError("self_id must be natsume_ai")
        permission = _required(self.proactive_permission, "proactive_permission", limit=64).upper()
        if permission not in _PROACTIVE_PERMISSIONS:
            raise ValueError(f"proactive_permission must be one of {sorted(_PROACTIVE_PERMISSIONS)}")
        episodes = tuple(self.active_episodes)
        if len(episodes) > _MAX_ACTIVE_EPISODES:
            raise ValueError(f"active_episodes cannot exceed {_MAX_ACTIVE_EPISODES}")
        if len({episode.episode_id for episode in episodes}) != len(episodes):
            raise ValueError("active_episodes must not contain duplicate episode ids")
        for consent_name in ("romantic_consent", "exclusive_consent", "dependency_consent"):
            value = _required(getattr(self, consent_name), consent_name, limit=64).upper()
            if value not in _CONSENT_STATES:
                raise ValueError(f"{consent_name} must be one of {sorted(_CONSENT_STATES)}")
            object.__setattr__(self, consent_name, value)
        for episode in episodes:
            if episode.workspace_id != str(self.workspace_id) or episode.relationship_subject_id != str(self.relationship_subject_id):
                raise ValueError("active episode scope drift")
        repair_targets = tuple(self.pending_affect_repair_targets)
        if len(repair_targets) > _MAX_ACTIVE_EPISODES:
            raise ValueError(
                "pending_affect_repair_targets cannot exceed active episode limit"
            )
        if len(
            {
                (target.affect_aggregate_id, target.affect_episode_id)
                for target in repair_targets
            }
        ) != len(repair_targets):
            raise ValueError("pending_affect_repair_targets must not repeat a target")
        for target in repair_targets:
            if not isinstance(target, AffectiveRepairTargetV1):
                raise ValueError(
                    "pending_affect_repair_targets must contain AffectiveRepairTargetV1"
                )
            if (
                target.workspace_id != str(self.workspace_id)
                or target.relationship_subject_id
                != str(self.relationship_subject_id)
            ):
                raise ValueError("pending affect repair target scope drift")
        if self.last_aftereffect is not None and (
            self.last_aftereffect.workspace_id != str(self.workspace_id)
            or self.last_aftereffect.relationship_subject_id != str(self.relationship_subject_id)
        ):
            raise ValueError("last aftereffect scope drift")
        object.__setattr__(self, "workspace_id", _required(self.workspace_id, "workspace_id"))
        object.__setattr__(self, "relationship_subject_id", _required(self.relationship_subject_id, "relationship_subject_id"))
        object.__setattr__(self, "revision", _non_negative_int(self.revision, "revision"))
        object.__setattr__(self, "trust", _unit(self.trust, "trust"))
        object.__setattr__(self, "safety", _unit(self.safety, "safety"))
        object.__setattr__(self, "attunement", _unit(self.attunement, "attunement"))
        object.__setattr__(self, "rupture_load", _unit(self.rupture_load, "rupture_load"))
        object.__setattr__(self, "repair_required", bool(self.repair_required))
        object.__setattr__(self, "proactive_permission", permission)
        object.__setattr__(self, "active_episodes", episodes)
        object.__setattr__(self, "pending_affect_repair_targets", repair_targets)
        object.__setattr__(self, "self_id", _SELF_ID)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "relationship_state.v2",
            "self_id": self.self_id,
            "workspace_id": self.workspace_id,
            "relationship_subject_id": self.relationship_subject_id,
            "revision": self.revision,
            "trust": self.trust,
            "safety": self.safety,
            "attunement": self.attunement,
            "rupture_load": self.rupture_load,
            "repair_required": self.repair_required,
            "proactive_permission": self.proactive_permission,
            "romantic_consent": self.romantic_consent,
            "exclusive_consent": self.exclusive_consent,
            "dependency_consent": self.dependency_consent,
            "active_episodes": [episode.as_dict() for episode in self.active_episodes],
            "pending_affect_repair_targets": [
                target.as_dict() for target in self.pending_affect_repair_targets
            ],
            "last_aftereffect": (
                self.last_aftereffect.as_dict()
                if self.last_aftereffect is not None
                else None
            ),
        }


__all__ = [
    "CompanionProactivePermissionRequestV1",
    "CompanionProactiveTargetV1",
    "CompanionResponseFeedbackRequestV1",
    "AffectiveRepairTargetV1",
    "RelationshipEpisodeV1",
    "RelationshipStateV2",
    "RelationalAftereffectV1",
]
