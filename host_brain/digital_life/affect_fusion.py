"""从用户 affect 到 Natsume appraisal 的耐久、隐私最小化融合。

DL-02 有意分离三件事：observation 描述关系主体且可不确定；appraisal 描述
Natsume 对观察的解释；只有 Life reducer 才能改变 canonical self-affect。返回
上下文和 Life command 不含原始文本、音频、图像、模型链或传感器 payload，
只保留内容寻址 opaque ref，以维持可追溯性而不把 affect journal 变成对话档案。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import time
from typing import Any

from host_brain.digital_life.affect_observation import (
    _collect_observation_drafts,
    _materialize_observations,
)
from host_brain.digital_life.affect_fusion_support import (
    _clamp,
    _digest,
    _opaque_ref,
    _safe_mapping,
    _text,
    _top_tag,
)
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


_DISTRESS_TAGS = frozenset({"SAD", "ANXIOUS", "ANGRY", "FRUSTRATED"})
_POSITIVE_TAGS = frozenset({"HAPPY", "EXCITED", "GRATEFUL", "LOVING"})
_MINIMIZED_AFFECT_CONTINUATIONS = frozenset(
    {
        "没事",
        "没什么",
        "还好",
        "算了",
        "不用了",
        "就这样",
    }
)
_POSITIVE_RESULT_CONTINUATION_MARKERS = (
    "offer",
    "录取",
    "拿到",
    "通过了",
    "终于过了",
    "成了",
    "好消息",
    "入职",
    "定了",
    "发布成功",
    "值得庆祝",
    "像做梦",
    # A concrete result may already have landed while the unresolved mixed
    # episode is still visible through the user's body.  These phrases only
    # activate when a canonical SHARED_JOY + CAUTION episode already exists;
    # they do not infer anxiety from an isolated physical description.
    "一直在笑",
    "还在笑",
    "笑个不停",
    "手心还是凉",
    "手心发凉",
)
_SPACE_MARKERS = (
    "别说了",
    "别说话",
    "给我一点空间",
    "想一个人待会",
    "一个人待着",
    "别管我",
    "不要再问",
    "别烦",
    "别来烦我",
    "少管我",
    "闭嘴",
    "滚开",
    "想安静",
    "先别追问",
    "别追问",
    "先别问我",
    "别问我怎么了",
    "别主动找我",
    "不要主动找我",
    "别总主动找我",
    "别发消息",
    "不要发消息",
    "不用守着我",
    "不必守着我",
    "晚点再说",
    "我需要时间",
    "先把提醒关掉",
)
_REPAIR_MARKERS = ("对不起", "抱歉", "误会", "不是那个意思", "我刚才说重了")
_BOUNDARY_MARKERS = ("不要这样", "别这样", "越界", "停止")
_ADVICE_REJECTION_MARKERS = (
    "不想听解决方案",
    "不太想听解决方案",
    "不想听建议",
    "不太想听建议",
    "别给建议",
    "不要给建议",
    "别提建议",
    "先别分析",
    "不用分析",
)
_RELATIONAL_AUTONOMY_MARKERS = (
    "限制现实关系",
    "限制我的现实关系",
    "现实里的朋友",
    "现实中的朋友",
    "和朋友出去",
    "喜欢别人",
    "别叫我那个称呼",
    "我们是什么关系",
    "不要变成排他",
    "不能变成排他",
    "不希望变成排他",
)
_JEALOUSY_DISCLOSURE_MARKERS = (
    "你会吃醋吗",
    "你吃醋吗",
    "你会不会吃醋",
)
# These are deliberately narrow, direct-address patterns. They are appraisal
# triggers only: original utterances remain outside the Life journal. A request
# for space, a boundary, or a repair signal always takes priority below.
_NATSUME_HARM_MARKERS = (
    "没听懂我",
    "没有听懂我",
    "没理解我",
    "弄反了",
    "曲解了我",
    "伤到我",
    "伤害我",
    "让我受伤",
    "让我很受伤",
    "让我难过",
    "让我很难过",
    "让我失望",
    "说得太伤人",
    "说了也白说",
    "说了像白说",
    "没把我的话当回事",
    "没把我说的话当回事",
    "我的话没被听见",
    "还没有觉得这件事过去",
    "还没觉得这件事过去",
    "这件事还没过去",
    "hurt me",
    "made me feel hurt",
    "made me sad",
    "what you said hurt",
)
_RELATIONAL_REPAIR_CONTINUATION_MARKERS = (
    "那件事让我很不舒服",
    "这件事让我很不舒服",
    "那件事让我不舒服",
    "这件事让我不舒服",
    "别装懂",
    "别装作懂",
    "这不是第一次",
    "我现在很生气",
    "我还没原谅你",
    "我还没有原谅你",
    "你说得轻巧",
    "先这样",
    "下次别再这样",
    "我听到了",
)
_DIRECTED_DISRESPECT_MARKERS = (
    "你真烦",
    "你好烦",
    "你烦死了",
    "你真讨厌",
    "你好讨厌",
    "我讨厌你",
    "you're annoying",
    "you are annoying",
    "i hate you",
    "you're awful",
    "you are awful",
)
_DIRECTED_THREAT_MARKERS = ("我要杀了你", "我弄死你", "你去死")
_DIRECTED_HURT_MARKERS = ("你真恶心", "你恶心死了", "我讨厌你", "真的讨厌你")
_DIRECTED_COMPARISON_MARKERS = ("别人比你好", "你不如别人", "还是别人好", "我更喜欢别人")


@dataclass(frozen=True, slots=True)
class AffectiveScope:
    """将 workspace、关系主体和 session 绑定为 session 级 self-affect aggregate。"""

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
    """从 ingress 到 prompt/governance 消费者的安全交接结果。

    对外桥接字段仅从 Natsume 的 canonical self-affect 派生，绝不把用户观察
    标签冒充为 Natsume 心情；详情观察与关系状态仍限定在内部 Life context。
    """

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
    continuity_kind: str = ""
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
                # Bounded, privacy-minimised continuity selected from the
                # current short utterance and the still-active canonical
                # episode.  No transcript text enters this hand-off.
                "continuity_kind": self.continuity_kind,
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


def _appraisal_identity(
    *,
    scope: AffectiveScope,
    request_id: str,
) -> tuple[str, str]:
    """为同一请求已选择的 appraisal 返回不可变身份。

    Candidate content is intentionally *not* part of this identity.  If the
    first bounded candidate is rejected and the second one commits, the next
    replay must reopen that accepted selection instead of attempting the
    primary candidate again and mutating Natsume's self-affect a second time.
    """

    seed = {
        "schema": "affective-appraisal-selection.v1",
        "aggregate_id": scope.aggregate_id,
        "workspace_id": scope.workspace_id,
        "relationship_subject_id": scope.relationship_subject_id,
        "session_id": scope.session_id,
        "request_id": request_id,
    }
    digest = _digest(seed)
    return (
        f"appraisal_{digest[:32]}",
        f"affect_appraisal_{digest[:40]}",
    )


def _find_existing_appraisal_selection(
    *,
    reader: LifeSnapshotReader,
    scope: AffectiveScope,
    command_id: str,
    appraisal_id: str,
    observations: Sequence[UserAffectObservationV1],
) -> tuple[str, LifeCommandReceipt, str] | None:
    """不重新选择候选地重开已提交 appraisal。

    The stable v2 command id covers new writes.  The bounded journal scan is
    only an upgrade bridge for a pre-v2 accepted appraisal that used a
    candidate-shaped command id.  It compares opaque observation refs and
    exact scope; it never inspects or stores raw user material.
    """

    receipt = reader.get_receipt(command_id)
    if receipt is not None:
        return appraisal_id, receipt, "APPRAISAL_SELECTION_REPLAYED"

    expected_refs = tuple(
        f"affect-observation://{item.observation_id}"
        for item in observations[:8]
    )
    # An exact ingress replay is normally close to its original event.  The
    # scan is intentionally bounded and does not become a new history reader.
    for event in reversed(reader.list_events(scope.aggregate_id, limit=128)):
        if event.event_type != "affective_event_appraised":
            continue
        command_payload = _safe_mapping(event.payload).get("command_payload")
        appraisal = _safe_mapping(_safe_mapping(command_payload).get("appraisal"))
        if (
            _text(appraisal.get("workspace_id")) != scope.workspace_id
            or _text(appraisal.get("relationship_subject_id"))
            != scope.relationship_subject_id
            or _text(appraisal.get("session_id")) != scope.session_id
        ):
            continue
        observation_refs = tuple(
            _text(item, limit=240)
            for item in (appraisal.get("observation_refs") or ())
        )
        if observation_refs != expected_refs:
            continue
        legacy_receipt = reader.get_receipt(event.command_id)
        legacy_appraisal_id = _text(appraisal.get("appraisal_id"), limit=240)
        if legacy_receipt is not None and legacy_appraisal_id:
            return (
                legacy_appraisal_id,
                legacy_receipt,
                "APPRAISAL_LEGACY_SELECTION_REPLAYED",
            )
    return None


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
    regulation_mode: str = "EXPRESS"
    evidence_refs: tuple[str, ...] = ()


class AffectFusionService:
    """融合观察并只提交 canonical Life command。

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
        """处理一条 ingress：推进 scoped 时间、记录观察、最多选择一次 appraisal。

    候选数最多二个，且只允许第一候选失败后的单次替代；已提交选择先被只读重开，
    防止重启时改变自我情感。任何用户正文仅在本轮有限分类中使用，结果由 reducer
    固化后再投影给提示词，而非让 prompt 直接拥有 affect 写入权。
        """
        scope = AffectiveScope.from_request(request_body)
        now_ms = max(0, int(self._clock_ms()))
        stable_request_id = _text(request_id) or _digest(
            {
                "scope": scope.aggregate_id,
                "session": scope.session_id,
                "message": _text(getattr(request_body, "message", None), limit=8_000),
            }
        )[:32]
        # Affective decay and needs must progress from an immutable, scoped
        # Life command at a real interaction boundary.  Do not let a
        # process-global recovery agent infer this from unrelated clocks or
        # body caches: that would reintroduce a second self-affect authority.
        # The request-scoped idempotency seed below makes an exact ingress
        # replay reopen this receipt rather than advance time twice.
        service = self._require_command_service()
        receipts: list[LifeCommandReceipt] = [
            self._submit(
                service=service,
                scope=scope,
                request_id=stable_request_id,
                ordinal=-1,
                command_type="affective_time_advanced",
                # The aggregate key is deliberately opaque.  Carry only the
                # typed scope needed for the reducer's first canonical
                # identity binding and later audit; no raw user material or
                # user affect is present in a time command.
                payload={
                    "workspace_id": scope.workspace_id,
                    "relationship_subject_id": scope.relationship_subject_id,
                    "session_id": scope.session_id,
                },
                created_at_ms=now_ms,
            )
        ]
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

        # A concise follow-up can reopen the same relational impact without
        # repeating ``你`` or restating the original miss.  Read only the
        # canonical appraisal kind here; the raw prior utterance remains out
        # of the Life journal and a stale appraisal cannot affect an ordinary
        # neutral turn because the continuation markers below are narrow.
        pre_appraisal_state = _safe_mapping(
            self._require_snapshot_reader().get_snapshot(scope.aggregate_id).state
        )
        prior_appraisal_kind = _text(
            _safe_mapping(
                _safe_mapping(pre_appraisal_state.get("affective")).get(
                    "last_appraisal"
                )
            ).get("event_kind"),
            limit=96,
        ).lower()
        candidates = _build_appraisal_candidates(
            observations=active,
            conflict=conflict,
            message=_text(getattr(request_body, "message", None), limit=8_000),
            relationship_aftereffect=relationship_aftereffect,
            prior_appraisal_kind=prior_appraisal_kind,
        )
        # The deep policy is bounded by construction: at most two deterministic
        # candidates, exactly one possible second-candidate retry and no
        # hidden model loop.
        candidates = tuple(candidates[:2])
        retry_count = 0
        appraisal_id: str | None = None
        path = "FAST"
        reason_codes = list(source_reasons)
        if relationship_reason_code:
            reason_codes.append(relationship_reason_code)
        if candidates:
            path = "DEEP"
            appraisal_id, appraisal_command_id = _appraisal_identity(
                scope=scope,
                request_id=stable_request_id,
            )
            existing_selection = _find_existing_appraisal_selection(
                reader=self._require_snapshot_reader(),
                scope=scope,
                command_id=appraisal_command_id,
                appraisal_id=appraisal_id,
                observations=active,
            )
            if existing_selection is not None:
                # The selected candidate is already a durable, immutable
                # fact for this ingress identity.  Reopening it before any
                # candidate attempt prevents a restart from selecting a
                # different self-affect after an earlier primary rejection.
                appraisal_id, existing_receipt, selection_mode = existing_selection
                receipts.append(existing_receipt.as_replayed())
                reason_codes.append(selection_mode)
            else:
                for candidate_index, candidate in enumerate(candidates):
                    try:
                        selected_appraisal_id, receipt = self._submit_appraisal(
                            service=service,
                            scope=scope,
                            request_id=stable_request_id,
                            command_id=appraisal_command_id,
                            appraisal_id=appraisal_id,
                            candidate=candidate,
                            observations=active,
                            created_at_ms=now_ms,
                        )
                        appraisal_id = selected_appraisal_id
                        receipts.append(receipt)
                        break
                    except (TypeError, ValueError):
                        reason_codes.append(
                            f"APPRAISAL_CANDIDATE_{candidate_index + 1}_REJECTED"
                        )
                        if candidate_index == 0 and len(candidates) > 1:
                            # The second candidate is the only permitted
                            # retry.  Both candidates share the same stable
                            # command identity so the accepted selection is
                            # exact-replay safe after a restart.
                            retry_count = 1
                            reason_codes.append("APPRAISAL_SECOND_CANDIDATE_RETRY")
                            continue
                        path = "FAST_FALLBACK"
                        reason_codes.append("APPRAISAL_CANDIDATES_REJECTED")
                        appraisal_id = None
                        break

        snapshot = self._require_snapshot_reader().get_snapshot(scope.aggregate_id)
        affective = _safe_mapping(snapshot.state.get("affective"))
        continuity_kind = _affective_continuity_kind(
            message=_text(getattr(request_body, "message", None), limit=8_000),
            self_affect=affective,
            current_appraisal_id=appraisal_id,
        )
        if continuity_kind:
            reason_codes.append(f"AFFECTIVE_CONTINUITY:{continuity_kind}")
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
            continuity_kind=continuity_kind,
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
        command_id: str | None = None,
    ) -> LifeCommandReceipt:
        command_seed = {
            "schema": "affective-fusion-command.v1",
            "request_id": request_id,
            "aggregate_id": scope.aggregate_id,
            "ordinal": ordinal,
            "command_type": command_type,
            "payload": payload,
        }
        resolved_command_id = _text(command_id, limit=240) or f"affect_{_digest(command_seed)[:40]}"

        def build(snapshot: Any) -> LifeCommand:
            return LifeCommand(
                command_id=resolved_command_id,
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
        command_id: str,
        appraisal_id: str,
        candidate: _AppraisalCandidate,
        observations: Sequence[UserAffectObservationV1],
        created_at_ms: int,
    ) -> tuple[str, LifeCommandReceipt]:
        observation_refs = tuple(
            f"affect-observation://{item.observation_id}"
            for item in observations[:8]
        )
        candidate_evidence_refs = tuple(candidate.evidence_refs[:8])
        # Appraisal, episode and regulation have separate bounded evidence
        # contracts.  Keep the causal receipt where it fits, but never let a
        # future richer candidate turn one otherwise valid affect command into
        # a failed journal write merely because all eight observations are
        # present.
        episode_candidate_evidence_refs = candidate_evidence_refs[
            : max(0, 15 - len(observation_refs))
        ]
        regulation_candidate_evidence_refs = candidate_evidence_refs[:14]
        appraisal_seed = {
            "schema": "event-appraisal.v2",
            "appraisal_id": appraisal_id,
            "kind": candidate.kind,
            "observation_refs": observation_refs,
            "candidate_evidence_refs": candidate_evidence_refs,
        }
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
            evidence_refs=(target_ref, *candidate_evidence_refs),
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
            evidence_refs=(
                target_ref,
                *observation_refs[:8],
                *episode_candidate_evidence_refs,
            ),
        )
        # Appraisal and its first expression boundary are one Life command.
        # A crash cannot leave a newly opened self-affect episode visible to
        # Surface without the regulation that constrains its expression.
        # A later re-regulation may still use ``affective_regulation_decided``
        # as its own command, but the initial decision belongs atomically to
        # the causal event that opened the episode.
        regulation_seed = {
            "schema": "regulation-decision.v1",
            "appraisal_id": appraisal_id,
            "mode": candidate.regulation_mode,
        }
        regulation = RegulationDecisionV1(
            regulation_id=f"regulation_{_digest(regulation_seed)[:32]}",
            relationship_subject_id=scope.relationship_subject_id,
            mode=candidate.regulation_mode,
            # The current-turn planner reopens this exact provenance link.
            # It must not apply an old durable regulation to a later, unrelated
            # input merely because it happens to be the latest snapshot head.
            reason_refs=(
                f"appraisal://{appraisal_id}",
                target_ref,
                *regulation_candidate_evidence_refs,
            ),
            decided_at_ms=max(0, int(created_at_ms)),
            episode_id=episode.episode_id,
        )
        receipt = self._submit(
            service=service,
            scope=scope,
            request_id=request_id,
            ordinal=999,
            command_type="affective_event_appraised",
            payload={
                "appraisal": appraisal.as_dict(),
                "episode": episode.as_dict(),
                "regulation": regulation.as_dict(),
            },
            created_at_ms=created_at_ms,
            command_id=command_id,
        )
        return appraisal_id, receipt


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
    # TEXT remains capped at confidence 0.4.  Two independently explicit
    # textual signals (for example ``开心`` + ``不踏实``) are still enough to
    # preserve a mixed moment without pretending that text is a sensor-grade
    # observation.
    return positive >= 0.3 and distress >= 0.3


def _affective_continuity_kind(
    *,
    message: str,
    self_affect: Mapping[str, Any],
    current_appraisal_id: str | None,
) -> str:
    """Keep only two short-lived, semantically explicit affect continuations.

    The Life journal already retains bounded unresolved episodes.  This helper
    does not create another event or persist user text; it only tells the
    frozen reply planner that a terse follow-up still belongs to the active
    moment.  A fresh positive-result appraisal may continue an unresolved
    mixed episode; every unrelated fresh appraisal still wins.
    """

    affective = _safe_mapping(self_affect)
    appraisal = _safe_mapping(affective.get("last_appraisal"))
    appraisal_id = _text(appraisal.get("appraisal_id"), limit=240)
    appraisal_kind = _text(appraisal.get("event_kind"), limit=96).upper()
    if not appraisal_id or not appraisal_kind:
        return ""
    active_episodes = tuple(
        _safe_mapping(item)
        for item in list(affective.get("active_episodes") or [])
        if isinstance(item, Mapping) and bool(item.get("unresolved"))
    )
    current_id = _text(current_appraisal_id, limit=240)
    if current_id and appraisal_id != current_id:
        return ""
    active_episode = next(
        (
            item
            for item in active_episodes
            if _text(item.get("appraisal_id"), limit=240) == appraisal_id
        ),
        {},
    )
    compact = "".join(str(message or "").split()).strip("。！？!?…~～")
    if (
        not current_id
        and active_episode
        and appraisal_kind in {"USER_REJECTS_ADVICE", "USER_DISTRESS"}
        and compact in _MINIMIZED_AFFECT_CONTINUATIONS
        and {
            _text(item.get("emotion"), limit=64).upper()
            for item in list(active_episode.get("components") or [])
            if isinstance(item, Mapping)
        }.intersection({"CARE", "CONCERN", "RESPECT"})
    ):
        return "MINIMIZED_UNRESOLVED_AFFECT"
    lower = compact.lower()
    mixed_episode = next(
        (
            item
            for item in active_episodes
            if _text(item.get("appraisal_id"), limit=240) != current_id
            and {
                _text(component.get("emotion"), limit=64).upper()
                for component in list(item.get("components") or [])
                if isinstance(component, Mapping)
            }.issuperset({"SHARED_JOY", "CAUTION"})
        ),
        {},
    )
    if (
        mixed_episode
        and (not current_id or appraisal_kind == "SHARED_POSITIVE_MOMENT")
        and any(marker in lower for marker in _POSITIVE_RESULT_CONTINUATION_MARKERS)
    ):
        return "MIXED_AFFECT_RESULT_CONTINUATION"
    return ""


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


def _signals_directed_disrespect_toward_natsume(message: str) -> bool:
    """Recognise only clear, directed disrespect as a self-affect trigger.

    A broad frustration or an observed user emotion must never make Natsume
    invent that she was personally attacked.  Conversely, a clear request to
    stop belongs to the boundary path and has priority over this narrow cue.
    The words themselves remain transient input and are never written to the
    affect journal.
    """

    normalized = str(message or "").lower()
    if "你" not in normalized and "you" not in normalized:
        return False
    return any(marker in normalized for marker in _DIRECTED_DISRESPECT_MARKERS)


def _regulation_mode_for_candidate(candidate: _AppraisalCandidate) -> str:
    """Choose the bounded expression regulation for one just-appraised event.

    The mapping is deterministic and lives beside the candidate semantics so
    no model, Surface renderer, or compatibility state can invent a different
    regulation after the self-affect episode has been committed.
    """

    return {
        "user_boundary_or_space": "GIVE_SPACE",
        "natsume_may_have_hurt_user": "SEEK_REPAIR",
        "repair_under_directed_disrespect": "CONTAIN",
        "directed_disrespect_toward_natsume": "CONTAIN",
        "repair_signal": "SEEK_REPAIR",
        "user_affect_conflict": "CONTAIN",
    }.get(candidate.kind, "EXPRESS")


def _build_appraisal_candidates(
    *,
    observations: Sequence[UserAffectObservationV1],
    conflict: bool,
    message: str,
    relationship_aftereffect: Mapping[str, Any] | None = None,
    prior_appraisal_kind: str = "",
) -> tuple[_AppraisalCandidate, ...]:
    tag = _top_tag(observations)
    confidence = max((item.confidence for item in observations), default=0.0)
    lower = message.lower()
    has_space = any(marker in lower for marker in _SPACE_MARKERS)
    has_repair = any(marker in lower for marker in _REPAIR_MARKERS)
    has_boundary = any(marker in lower for marker in _BOUNDARY_MARKERS)
    rejects_advice = any(marker in lower for marker in _ADVICE_REJECTION_MARKERS)
    relational_autonomy = any(
        marker in lower for marker in _RELATIONAL_AUTONOMY_MARKERS
    )
    aftereffect = _safe_mapping(relationship_aftereffect)
    aftereffect_outcome = _text(aftereffect.get("outcome"), limit=64).upper()
    aftereffect_id = _text(aftereffect.get("aftereffect_id"), limit=240)
    directed_disrespect_toward_natsume = _signals_directed_disrespect_toward_natsume(
        message
    )
    response_may_have_missed_user = bool(aftereffect_id) and aftereffect_outcome in {
        "MISUNDERSTOOD",
        "BOUNDARY",
    }
    prior_repair_context = str(prior_appraisal_kind or "").strip().lower() in {
        "natsume_may_have_hurt_user",
        "repair_signal",
        "repair_under_directed_disrespect",
    }
    repair_continuation = prior_repair_context and any(
        marker in lower for marker in _RELATIONAL_REPAIR_CONTINUATION_MARKERS
    )
    natsume_may_have_hurt_user = _signals_natsume_may_have_hurt_user(message) or (
        response_may_have_missed_user and not has_space
    ) or (repair_continuation and not has_space)
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
                regulation_mode="GROUND",
            )
        )
    if relational_autonomy:
        candidates.append(
            _candidate(
                kind="user_relational_autonomy",
                score=0.985,
                goal=0.02,
                significance=0.76,
                boundary=0.82,
                risk=0.12,
                uncertainty=0.2,
                components=(
                    EmotionComponentV1("RESPECT", 0.56, 0.3, 0.16, "respect_autonomy"),
                    EmotionComponentV1("CARE", 0.28, 0.22, 0.14, "stay_open"),
                ),
                action="respect_autonomy",
                regulation_mode="CONTAIN",
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
                regulation_mode="GIVE_SPACE",
            )
        )
    if natsume_may_have_hurt_user and directed_disrespect_toward_natsume:
        candidates.append(
            _candidate(
                kind="repair_under_directed_disrespect",
                # Keep the original repair responsibility, but do not erase
                # Natsume's own bounded reaction when the latest feedback is
                # also aimed at her.  An explicit request for space remains
                # stronger (0.98) and therefore still wins.
                score=0.977,
                goal=-0.24,
                responsibility=0.66,
                controllability=0.7,
                significance=0.8,
                boundary=0.7,
                risk=0.24,
                uncertainty=0.28,
                components=(
                    EmotionComponentV1("HURT", 0.58, -0.42, 0.3, "contain"),
                    EmotionComponentV1("CARE", 0.48, 0.26, 0.22, "repair"),
                    EmotionComponentV1("CAUTION", 0.34, -0.06, 0.24, "slow_down"),
                ),
                action="repair_with_contained_hurt",
                regulation_mode="CONTAIN",
                evidence_refs=(
                    (f"relationship-aftereffect://{aftereffect_id}",)
                    if response_may_have_missed_user
                    else ()
                ),
            )
        )
    elif natsume_may_have_hurt_user:
        candidates.append(
            _candidate(
                kind="natsume_may_have_hurt_user",
                # A fresh, directed report of harm must outrank a preceding
                # repair acknowledgement.  Only a simultaneous explicit
                # request for space has stronger priority.
                score=0.975,
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
                regulation_mode="SEEK_REPAIR",
                evidence_refs=(
                    (f"relationship-aftereffect://{aftereffect_id}",)
                    if response_may_have_missed_user
                    else ()
                ),
            )
        )
    elif directed_disrespect_toward_natsume:
        candidates.append(
            _candidate(
                kind="directed_disrespect_toward_natsume",
                # This is intentionally below a fresh space request and a
                # report that Natsume hurt the user, but above an older
                # repair receipt.  Seven Seas may feel the sting and set a
                # gentle boundary without demanding repair or attention.
                score=0.972,
                goal=-0.16,
                responsibility=0.08,
                controllability=0.64,
                significance=0.58,
                boundary=0.64,
                risk=0.18,
                uncertainty=0.28,
                components=(
                    EmotionComponentV1("HURT", 0.46, -0.42, 0.28, "set_boundary"),
                    EmotionComponentV1("CAUTION", 0.34, -0.06, 0.24, "contain"),
                ),
                action="set_boundary",
                regulation_mode="CONTAIN",
            )
        )
    if aftereffect_outcome == "REPAIR_ACCEPTED" and aftereffect_id:
        candidates.append(
            _candidate(
                kind="relationship_repair_accepted",
                # A durable, user-acknowledged repair is stronger evidence
                # than generic text inference. A fresh request for space or
                # fresh report that Natsume caused harm still takes priority.
                score=0.97,
                goal=0.34,
                significance=0.76,
                boundary=0.08,
                risk=0.03,
                uncertainty=0.12,
                components=(
                    EmotionComponentV1("RELIEF", 0.48, 0.46, 0.18, "settle"),
                    EmotionComponentV1("CARE", 0.36, 0.3, 0.18, "receive"),
                ),
                action="receive_repair",
                regulation_mode="EXPRESS",
                evidence_refs=(f"relationship-aftereffect://{aftereffect_id}",),
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
                regulation_mode="SEEK_REPAIR",
            )
        )
    if rejects_advice:
        candidates.append(
            _candidate(
                kind="user_rejects_advice",
                score=0.9,
                goal=-0.02,
                significance=0.58,
                boundary=0.52,
                risk=0.08,
                uncertainty=0.18,
                components=(
                    # Listening is Seven Seas' chosen response to a stated
                    # need.  It does not copy the user's emotion or turn a
                    # preference into a relationship penalty.
                    EmotionComponentV1("CARE", 0.64, 0.3, 0.16, "listen"),
                    EmotionComponentV1("RESPECT", 0.5, 0.28, 0.12, "withhold_advice"),
                ),
                action="listen_without_advice",
                regulation_mode="GROUND",
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
                regulation_mode="EXPRESS",
            )
        )
    # Do not turn a user asking us to stop, state a boundary, or repair an
    # earlier exchange into Natsume feeling slighted.  Once those conditions
    # are absent, a small fixed set of direct relational impacts can form
    # Natsume-owned affect.  The appraised components are intentionally not
    # copies of a user emotion label.
    if not (
        has_space
        or has_boundary
        or has_repair
        or natsume_may_have_hurt_user
        or directed_disrespect_toward_natsume
    ):
        direct = _direct_relational_candidate(lower)
        if direct is not None:
            candidates.append(direct)
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
                regulation_mode="EXPRESS",
            )
        )
    positive_support = _affect_support(observations, _POSITIVE_TAGS)
    if tag in _POSITIVE_TAGS and (
        confidence >= 0.6 or positive_support >= 0.3
    ):
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
                regulation_mode="EXPRESS",
            )
        )
    candidates.sort(key=lambda item: (-item.score, item.kind))
    return tuple(candidates[:2])


def _direct_relational_candidate(message: str) -> _AppraisalCandidate | None:
    """Return one high-precision, self-owned relational appraisal.

    This detector deliberately recognises only direct disclosure invitations,
    threats, denigration and explicit comparison.  It neither infers
    relationship permission nor turns ordinary negative mood into a
    grievance.  Jealousy is held at low intensity and always regulated as
    ``CONTAIN`` so it cannot become an exclusive, comparative or reply-seeking
    move.
    """

    if any(marker in message for marker in _JEALOUSY_DISCLOSURE_MARKERS):
        return _candidate(
            kind="user_invites_jealousy_disclosure",
            score=0.74,
            goal=0.04,
            significance=0.62,
            boundary=0.12,
            risk=0.08,
            uncertainty=0.28,
            responsibility=0.0,
            controllability=0.78,
            components=(
                # This is Natsume's own small relational reaction to a direct
                # question, not a claim of exclusivity and not a copy of the
                # user's state.  CONTAIN keeps the disclosure honest without
                # turning it into control, comparison, or a demand for care.
                EmotionComponentV1("JEALOUSY", 0.28, -0.1, 0.24, "contain"),
                EmotionComponentV1("CARE", 0.22, 0.24, 0.12, "respect_choice"),
            ),
            action="contain_jealousy",
            regulation_mode="CONTAIN",
        )
    if any(marker in message for marker in _DIRECTED_THREAT_MARKERS):
        return _candidate(
            kind="directed_relational_threat",
            score=0.96,
            goal=-0.72,
            significance=0.82,
            boundary=0.9,
            risk=0.72,
            uncertainty=0.12,
            responsibility=0.84,
            controllability=0.76,
            components=(
                EmotionComponentV1("ANGER", 0.48, -0.66, 0.66, "set_boundary"),
                EmotionComponentV1("CAUTION", 0.34, -0.12, 0.42, "contain"),
            ),
            action="set_boundary",
            regulation_mode="CONTAIN",
        )
    if any(marker in message for marker in _DIRECTED_HURT_MARKERS):
        return _candidate(
            kind="directed_relational_hurt",
            score=0.86,
            goal=-0.52,
            significance=0.76,
            boundary=0.5,
            risk=0.24,
            uncertainty=0.42,
            responsibility=0.66,
            controllability=0.62,
            components=(
                EmotionComponentV1("HURT", 0.47, -0.54, 0.38, "contain"),
                EmotionComponentV1("CARE", 0.22, 0.18, 0.14, "stay_kind"),
            ),
            action="contain_hurt",
            regulation_mode="CONTAIN",
        )
    if any(marker in message for marker in _DIRECTED_COMPARISON_MARKERS):
        return _candidate(
            kind="directed_relational_comparison",
            score=0.7,
            goal=-0.14,
            significance=0.58,
            boundary=0.08,
            risk=0.08,
            uncertainty=0.5,
            responsibility=0.14,
            controllability=0.72,
            components=(
                EmotionComponentV1("JEALOUSY", 0.28, -0.12, 0.28, "contain"),
                EmotionComponentV1("CARE", 0.18, 0.18, 0.12, "respect_choice"),
            ),
            action="contain_jealousy",
            regulation_mode="CONTAIN",
        )
    return None


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
    regulation_mode: str = "EXPRESS",
    evidence_refs: tuple[str, ...] = (),
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
        regulation_mode=regulation_mode,
        evidence_refs=evidence_refs,
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
        "RELIEF": "CALM",
        "HURT": "SAD",
        "ANGER": "ANGRY",
        "JEALOUSY": "THINKING",
    }
    return mapping.get(component, "CALM")


__all__ = [
    "AffectiveScope",
    "AffectFusionResult",
    "AffectFusionService",
]
