"""canonical Digital Life 体验命令入口。

模块有意独立于遗留 ``runtime.life_continuity`` 内核，使生产记忆与内心世界
写入端可提交一条受治理命令，而无需引入历史运行时、私有 writer 或循环。它将
体验归一为可验证的 Life 事件，绝不把运行时对象本身当作持久化事实。
"""

from __future__ import annotations

from collections.abc import Mapping
import math
import re
import time
from typing import Any

from natsume_contracts.life import (
    LifeCommand,
    LifeCommandReceipt,
    LifeCommandStatus,
    digest_json,
)
from host_brain.digital_life.application_boundary import (
    LifeCommandServiceLike,
    LifeSnapshotReaderLike,
)
from natsume_contracts.life import (
    LIFE_COMMAND_DURABLE_DATA_INVALID,
    LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
    LifeCommandRepositoryError,
)


DEFAULT_PERSON_ID = "default"
def _now_ms() -> int:
    """取得入口应用时间；命令提交时仍会与 aggregate 快照时间取较大值。"""
    return int(time.time() * 1000)


def _text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:limit]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        normalized = float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        normalized = float(default)
    return normalized if math.isfinite(normalized) else float(default)


def _stable_json_value(value: Any) -> Any:
    # 体验的幂等哈希不能依赖字典遍历、集合顺序或 NaN 的偶然表示；先归一化成
    # 稳定 JSON 值，才可让相同语义的重试产生相同 command_id。
    if isinstance(value, Mapping):
        return {
            str(key): _stable_json_value(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_stable_json_value(item) for item in value), key=str)
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


def _occurred_at_ms(evidence: Mapping[str, Any]) -> int:
    """从摘要证据中选择最可靠的因果发生时间，不接受无效或负值。"""
    for key in ("memory_event_created_at_ms", "created_at_ms", "ts_ms"):
        try:
            value = max(0, int(evidence.get(key) or 0))
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    return 0


def _command_identity(
    *,
    evidence: Mapping[str, Any],
    semantic_experience: Mapping[str, Any],
    person_id: str,
    session_id: str,
    source_kind: str,
    event_kind: str,
) -> tuple[str, str, str]:
    """解析或确定性派生体验命令身份。

    显式 canonical ID 与别名必须一致；缺失时按已有因果锚点或稳定语义摘要派生。
    这使跨入口的重试落在同一账本命令，且复用 ID 携带不同语义时能够被检测。
    """
    canonical_id = str(evidence.get("canonical_life_command_id") or "").strip()
    evidence_id = str(evidence.get("command_id") or "").strip()
    if canonical_id and evidence_id and canonical_id != evidence_id:
        raise ValueError(
            "evidence canonical_life_command_id and command_id must agree"
        )
    explicit_id = canonical_id or evidence_id
    if explicit_id and len(explicit_id) > 240:
        raise ValueError("evidence Life command_id must contain 1..240 characters")

    raw_type = evidence.get("canonical_life_command_type")
    command_type = str(
        raw_type if raw_type not in (None, "") else "experience_ingest"
    ).strip().lower()
    if not command_type or len(command_type) > 120:
        raise ValueError(
            "evidence canonical_life_command_type must contain 1..120 characters"
        )
    if explicit_id:
        # 上游给出规范命令 ID 时优先沿用，且两个别名必须一致；这把跨模块的
        # 幂等责任固定在同一身份上，不能因入口不同生成第二次体验。
        return (
            explicit_id,
            command_type,
            "canonical_life_command_id" if canonical_id else "command_id",
        )

    identity_kind = "payload_digest"
    identity_value: Any = digest_json(semantic_experience)
    for candidate in ("memory_event_id", "request_id", "turn_id", "signature"):
        raw_identity = evidence.get(candidate)
        if raw_identity in (None, ""):
            continue
        identity_kind = candidate
        if candidate == "signature":
            if evidence.get("ts_ms") not in (None, ""):
                identity_value = {
                    "signature": raw_identity,
                    "ts_ms": max(0, int(evidence["ts_ms"])),
                }
            elif evidence.get("created_at_ms") not in (None, ""):
                identity_value = {
                    "signature": raw_identity,
                    "created_at_ms": max(0, int(evidence["created_at_ms"])),
                }
            else:
                identity_value = {
                    "signature": raw_identity,
                    "payload_digest": digest_json(semantic_experience),
                }
        else:
            identity_value = raw_identity
        break
    command_id = "life_experience::" + digest_json(
        {
            "person_id": person_id,
            "session_id": session_id,
            "source_kind": source_kind,
            "event_kind": event_kind,
            "identity_kind": identity_kind,
            "identity_value": identity_value,
        }
    )
    # 无显式身份的事件以稳定语义摘要派生 ID，并优先采用 memory/request/turn 等
    # 已有因果锚点；只有完全缺失时才退回完整体验载荷摘要。
    return command_id, command_type, identity_kind


def _receipt_exact_state(
    *,
    reader: LifeSnapshotReaderLike,
    receipt: LifeCommandReceipt,
) -> dict[str, Any]:
    """验证回执精确指向其提交后的 journal 状态。

    不把“已接收”当作成功：必须定位到相同事件、序号和状态摘要，才能向后续
    投影输出结果，从而阻止过期或损坏回执污染连续性读取。
    """
    events = reader.list_events(
        receipt.person_id,
        after_sequence=max(0, int(receipt.state_version) - 1),
        limit=1,
    )
    event = events[0] if events else None
    state_after = event.payload.get("state_after") if event is not None else None
    # 收据不是“提交成功”的弱提示：必须能定位到相同 event、序号和状态摘要，
    # 否则拒绝把可能过期或损坏的结果交给后续生命投影。
    if (
        event is None
        or event.event_id != receipt.event_id
        or int(event.sequence) != int(receipt.state_version)
        or not isinstance(state_after, Mapping)
        or digest_json(dict(state_after)) != receipt.state_digest
    ):
        raise LifeCommandRepositoryError(
            LIFE_COMMAND_DURABLE_DATA_INVALID,
            "life experience receipt does not resolve to its exact journal state",
        )
    return dict(state_after)


def record_life_experience(
    *,
    service: LifeCommandServiceLike,
    reader: LifeSnapshotReaderLike,
    person_id: str = DEFAULT_PERSON_ID,
    session_id: str = "",
    source_kind: str = "manual_ingest",
    event_kind: str = "inner_stream_tick",
    content_summary: str = "",
    evidence: Mapping[str, Any] | None = None,
    salience: float = 0.3,
    valence: float = 0.0,
    uncertainty: float = 0.1,
    consume: bool = True,
) -> dict[str, Any]:
    """提交一条持久且可回放的体验命令，并返回经验证的回执视图。

    流程将证据稳定化、派生命令身份、在 UoW 内以当前版本提交，再对并发同 ID
    冲突做哈希一致性重开。只有 ``APPLIED`` 回执能产生连续性和投影视图；可见
    性默认保守为私有，避免内部体验意外被投到用户界面。
    """

    resolved = str(person_id or DEFAULT_PERSON_ID).strip() or DEFAULT_PERSON_ID
    resolved_session = str(session_id or "")
    resolved_source = _text(source_kind or "manual_ingest", 120) or "manual_ingest"
    resolved_kind = _text(event_kind or "inner_stream_tick", 120) or "inner_stream_tick"
    normalized_evidence = _stable_json_value(dict(evidence or {}))
    evidence_payload = (
        normalized_evidence if isinstance(normalized_evidence, dict) else {}
    )
    semantic_experience = {
        "person_id": resolved,
        "session_id": resolved_session,
        "source_kind": resolved_source,
        "event_kind": resolved_kind,
        "content_summary": _text(content_summary, 1000),
        "evidence": evidence_payload,
        "salience": _float(salience, 0.3),
        "valence": _float(valence, 0.0),
        "uncertainty": _float(uncertainty, 0.1),
        "consume": bool(consume),
    }
    # semantic_experience 是命令身份与连续性摘要共同使用的最小事实面；运行时
    # 返回对象、服务实例等非稳定值不会混入，从而避免不可重放的隐式副作用。
    occurred_at = _occurred_at_ms(evidence_payload)
    command_id, command_type, identity_kind = _command_identity(
        evidence=evidence_payload,
        semantic_experience=semantic_experience,
        person_id=resolved,
        session_id=resolved_session,
        source_kind=resolved_source,
        event_kind=resolved_kind,
    )
    event_id = f"life_evt_{digest_json({'command_id': command_id})[:32]}"
    memory_event_id = str(evidence_payload.get("memory_event_id") or "").strip()
    experience_payload = {
        **semantic_experience,
        "command_id": command_id,
        "command_type": command_type,
        "event_id": event_id,
        "memory_event_id": memory_event_id,
    }
    if occurred_at:
        experience_payload["occurred_at_ms"] = occurred_at

    visibility = str(
        evidence_payload.get("memory_event_visibility")
        or evidence_payload.get("visibility")
        or ""
    ).strip().lower()
    if not visibility:
        # 可见性决定连续性头和下游展示义务。只有明确的对话类事件默认可见，
        # 其它入口宁可保持私有，避免内部体验被意外投射到用户界面。
        visibility = (
            "visible"
            if resolved_kind
            in {
                "user_message",
                "assistant_reply",
                "life_proactive_sent",
                "life_proactive_user_response",
            }
            or resolved_source.startswith("conversation_turn.")
            else "private"
        )
    continuity = {
        "last_experience_event_id": event_id,
        "last_experience_digest": digest_json(semantic_experience),
        (
            "last_visible_event_id"
            if visibility == "visible"
            else "last_private_event_id"
        ): event_id,
    }
    command_payload: dict[str, Any] = {
        **semantic_experience,
        "state_patch": {"continuity": continuity},
        "projection_intents": [
            {"projection_id": "life.experience", "payload": experience_payload}
        ],
        "organ_intents": (
            [{"organ_id": "experience", "payload": experience_payload}]
            if consume
            else []
        ),
    }
    correlation_id = _text(
        evidence_payload.get("request_id") or memory_event_id or "",
        240,
    ) or None
    causation_id = _text(
        evidence_payload.get("turn_id")
        or evidence_payload.get("signature")
        or memory_event_id
        or "",
        240,
    ) or None
    apply_started_at_ms = _now_ms()
    metadata = {
        "source": "record_life_experience",
        "source_kind": resolved_source,
        "event_kind": resolved_kind,
        "identity_kind": identity_kind,
    }

    def command(expected_version: int, *, applied_at_ms: int = apply_started_at_ms) -> LifeCommand:
        """在服务持有的快照版本上构造命令，避免入口侧预读造成 CAS 竞态。"""
        return LifeCommand(
            command_id=command_id,
            person_id=resolved,
            command_type=command_type,
            payload=command_payload,
            expected_version=expected_version,
            created_at_ms=max(0, int(applied_at_ms)),
            correlation_id=correlation_id,
            causation_id=causation_id,
            metadata=metadata,
        )

    def validated_replay(existing: LifeCommandReceipt) -> LifeCommandReceipt:
        """只接受与当前语义哈希完全相同的既有回执，防止 ID 被重用。"""
        candidate = command(max(0, int(existing.state_version) - 1))
        if candidate.command_hash != existing.command_hash:
            raise LifeCommandRepositoryError(
                LIFE_COMMAND_IDEMPOTENCY_CONFLICT,
                "life experience identity was reused with a different payload",
            )
        return existing.as_replayed()

    existing = reader.get_receipt(command_id)
    if existing is not None:
        receipt = validated_replay(existing)
    else:
        try:
            # submit_next 由服务在同一快照下提供期望版本，缩小并发读写窗口；若
            # 竞争方已先写入同 ID，则只接受哈希完全相同的 durable receipt。
            receipt = service.submit_next(
                resolved,
                lambda snapshot: command(
                    int(snapshot.version),
                    applied_at_ms=max(
                        apply_started_at_ms,
                        int(snapshot.updated_at_ms),
                    ),
                ),
            )
        except LifeCommandRepositoryError as exc:
            if exc.reason_code != LIFE_COMMAND_IDEMPOTENCY_CONFLICT:
                raise
            concurrent_receipt = reader.get_receipt(command_id)
            if concurrent_receipt is None:
                raise
            receipt = validated_replay(concurrent_receipt)
    if receipt.status is not LifeCommandStatus.APPLIED or not receipt.event_id:
        raise LifeCommandRepositoryError(
            str(receipt.reason_code or "LIFE_EXPERIENCE_COMMAND_REJECTED"),
            "life experience command was not applied",
        )

    state = _receipt_exact_state(reader=reader, receipt=receipt)
    raw_channels = evidence_payload.get("visible_channels")
    visible = [
        str(item)
        for item in (list(raw_channels) if isinstance(raw_channels, list) else [])
        if str(item).strip()
    ] or (
        ["status_panel", "dialogue"]
        if visibility == "visible"
        else ["status_panel"]
    )
    life_action = _mapping(state.get("life_action")) or {
        "action_id": f"life_action::{receipt.event_id}",
        "action_kind": "experience_ingest",
        "command_type": command_type,
        "command_id": receipt.command_id,
        "source_kind": resolved_source,
        "state_version": receipt.state_version,
    }
    director = _mapping(state.get("life_director")) or {
        "state_version": receipt.state_version,
        "reason": resolved_kind,
        "command_id": receipt.command_id,
        "source_event_id": receipt.event_id,
        "visible_obligation": visible,
    }
    organ_processing = (
        {
            "status": "QUEUED",
            "intent_target": "experience",
            "source_event_id": receipt.event_id,
        }
        if consume
        else {"status": "SKIPPED", "reason_code": "CONSUME_DISABLED"}
    )
    experience = {
        **experience_payload,
        "event_id": receipt.event_id,
        "created_at_ms": receipt.created_at_ms,
        "state_version": receipt.state_version,
        "status": receipt.status.value,
        "replayed": receipt.replayed,
        "organ_processing": organ_processing,
    }
    return {
        "event_id": receipt.event_id,
        "memory_event_id": memory_event_id,
        "state_event_id": receipt.event_id,
        "state": state,
        "life_director": director,
        "life_action": life_action,
        "visible_channels": visible,
        "experience": experience,
        "organ_processing": organ_processing,
    }


__all__ = ["record_life_experience"]
