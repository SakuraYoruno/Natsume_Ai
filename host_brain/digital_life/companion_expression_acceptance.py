"""编译并重开陪伴表达的 Host 专属 Acceptance Effect。

本模块刻意止步于应用内持久化 embodiment outbox：它不建立 Edge 连接，也
不将已入队命令误认为物理 TTS 或 Avatar 已完成。后续由 Host 持有的 dispatcher
可凭同一不可变命令身份协调物理投递；这把“获准表达”与“设备实际执行”分开。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from typing import Any

from host_brain.digital_life.affective_expression_delivery import (
    bounded_expression_segments,
    build_expression_delivery_controls,
    build_sentence_expression_delivery,
    parse_expression_delivery_intent,
    parse_expression_delivery_receipt,
)
from natsume_contracts.edge.embodiment_v1 import (
    EmbodimentCommand,
    build_embodiment_command,
    parse_embodiment_command,
)
from natsume_contracts.life import LifeCommand, digest_json


COMPANION_EXPRESSION_ENQUEUE_EFFECT = "host.companion_expression_enqueue"
COMPANION_EXPRESSION_EFFECT_SCHEMA = (
    "Natsume.companion_expression_acceptance_effect.v1"
)
COMPANION_EXPRESSION_BATCH_SCHEMA = (
    "Natsume.companion_expression_acceptance_batch.v1"
)
_SPEECH_TTL_MS = 30_000
# Release/cancel controls are compiled only after all sentence intents.  They
# share the speech window so a bounded multi-sentence reply cannot consume the
# old five-second TTL while Acceptance and the serial physical outbox are still
# delivering the preceding Voice/Avatar commands.
_CONTROL_TTL_MS = _SPEECH_TTL_MS


class CompanionExpressionEffectError(ValueError):
    """陪伴表达 effect 漂移的稳定失败关闭错误。"""


def compile_companion_expression_effect_payloads(
    *,
    graph_id: str,
    graph_revision: int,
    node_id: str,
    attempt_id: str,
    request_id: str,
    principal_id: str,
    relationship_subject_id: str,
    workspace_id: str,
    session_id: str,
    node_grant_ref: str,
    foreground: Mapping[str, Any],
) -> tuple[dict[str, Any], ...]:
    """从冻结的 foreground 命令重建 Host 的 embodiment 准入。

    Worker 给出的协议行不构成 authority。只有当它们与冻结情感计划及已接受
    叙述的确定性推导完全一致时，才会发出 Host-outbox effect；因此 worker
    不能借由伪造 Voice/Avatar 行、换用别的关系作用域或修改计划来扩权。
    """

    command_scope = _required_scope(
        graph_id=graph_id,
        graph_revision=graph_revision,
        node_id=node_id,
        attempt_id=attempt_id,
        request_id=request_id,
        principal_id=principal_id,
        relationship_subject_id=relationship_subject_id,
        workspace_id=workspace_id,
        session_id=session_id,
        node_grant_ref=node_grant_ref,
    )
    foreground_value = _foreground_command(foreground)
    payload = _mapping(foreground_value["payload"], "foreground.payload")
    raw_plan = payload.get("affective_turn_plan")
    if raw_plan is None:
        return ()
    plan = _mapping(raw_plan, "affective_turn_plan")
    _require_plan_scope(plan=plan, command_scope=command_scope)
    if foreground_value["person_id"] != command_scope["relationship_subject_id"]:
        raise CompanionExpressionEffectError("AFFECTIVE_TURN_PLAN_SCOPE_DRIFT")
    if str(payload.get("affective_turn_plan_digest") or "") != digest_json(plan):
        raise CompanionExpressionEffectError("AFFECTIVE_TURN_PLAN_DIGEST_DRIFT")
    foreground_created_at_ms = _non_negative_int(
        foreground_value.get("created_at_ms"), "foreground.created_at_ms"
    )
    narrative = _required_text(payload.get("assistant_reply"), "assistant_reply", 32_000)
    try:
        sentence_intents, controls, receipts = _rebuild_frozen_expression_protocol(
            affective_turn_plan=plan,
            narrative=narrative,
            foreground_created_at_ms=foreground_created_at_ms,
            payload=payload,
        )
    except CompanionExpressionEffectError:
        raise
    except ValueError as exc:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_INVALID") from exc
    if not sentence_intents and not controls and not receipts:
        return ()
    expected_intents = tuple((*sentence_intents, *controls))
    life_binding = _foreground_life_binding(foreground_value)
    values: list[dict[str, Any]] = []
    for sequence, (intent, receipt) in enumerate(zip(expected_intents, receipts, strict=True)):
        identity = _identity_digest(
            {
                **command_scope,
                "delivery_id": intent.delivery_id,
                "plan_id": intent.plan_id,
                "synchronization_id": intent.synchronization_id,
                "delivery_sequence": sequence,
            }
        )
        embodiment_command = _build_embodiment_command(
            intent=intent,
            request_id=command_scope["request_id"],
            node_grant_ref=command_scope["node_grant_ref"],
            identity_digest=identity,
        )
        values.append(
            {
                "schema_version": COMPANION_EXPRESSION_EFFECT_SCHEMA,
                "delivery_sequence": sequence,
                "life_foreground": life_binding,
                "expression_delivery_intent": intent.as_dict(),
                "expression_delivery_protocol_receipt": receipt.as_dict(),
                "embodiment_command": embodiment_command.as_dict(),
            }
        )
    # One accepted ExpressionPlan is one local admission decision.  Keeping
    # Voice/Avatar commands as separate Acceptance Effects forced the same
    # immutable plan through ActionPreview four or more times on every ordinary
    # reply.  The commands remain independently idempotent and independently
    # receipted by the embodiment outbox, while the batch itself crosses the
    # authorization boundary once.
    return (
        {
            "schema_version": COMPANION_EXPRESSION_BATCH_SCHEMA,
            "life_foreground": life_binding,
            "entries": values,
        },
    )


def parse_companion_expression_effect_payload(
    raw: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """无副作用验证一条精确的 Host-outbox 准入负载。

    校验 intent、协议回执和 embodiment 命令的同一性，但不执行设备动作；解析
    阶段保持纯函数，才能安全用于恢复、重放与发送前再次授权检查。
    """

    if not isinstance(raw, Mapping):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_EFFECT_INVALID")
    value = dict(raw)
    required = {
        "schema_version",
        "delivery_sequence",
        "life_foreground",
        "expression_delivery_intent",
        "expression_delivery_protocol_receipt",
        "embodiment_command",
    }
    if set(value) != required or value.get("schema_version") != COMPANION_EXPRESSION_EFFECT_SCHEMA:
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_EFFECT_INVALID")
    sequence = _non_negative_int(value.get("delivery_sequence"), "delivery_sequence")
    life_foreground = _parse_life_foreground(value.get("life_foreground"))
    try:
        intent = parse_expression_delivery_intent(value.get("expression_delivery_intent"))
        protocol_receipt = parse_expression_delivery_receipt(
            value.get("expression_delivery_protocol_receipt")
        )
        command = parse_embodiment_command(value.get("embodiment_command"))
    except (TypeError, ValueError) as exc:
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_EFFECT_INVALID") from exc
    _require_intent_receipt_pair(intent=intent, receipt=protocol_receipt)
    _require_embodiment_command(
        intent=intent,
        command=command,
        request_id=life_foreground["correlation_id"],
        identity_digest=None,
    )
    return {
        "delivery_sequence": sequence,
        "life_foreground": life_foreground,
        "intent": intent,
        "protocol_receipt": protocol_receipt,
        "command": command,
    }


def parse_companion_expression_effect_batch_payload(
    raw: Mapping[str, Any] | Any,
) -> tuple[dict[str, Any], ...]:
    """解析一个批次，同时保留旧版单行 effect 的精确回放。

    批内序号、Life 绑定和命令/幂等键必须唯一，防止把不同前台任务的表达混入
    同一投递批次；旧单行格式仅作为已持久化记录的兼容读取路径。
    """

    if not isinstance(raw, Mapping):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_EFFECT_INVALID")
    value = dict(raw)
    if value.get("schema_version") == COMPANION_EXPRESSION_EFFECT_SCHEMA:
        return (parse_companion_expression_effect_payload(value),)
    if (
        set(value) != {"schema_version", "life_foreground", "entries"}
        or value.get("schema_version") != COMPANION_EXPRESSION_BATCH_SCHEMA
    ):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_BATCH_INVALID")
    batch_life = _parse_life_foreground(value.get("life_foreground"))
    entries = _sequence(value.get("entries"), "entries")
    if not entries or len(entries) > 66:
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_BATCH_INVALID")
    parsed = tuple(parse_companion_expression_effect_payload(item) for item in entries)
    if tuple(item["delivery_sequence"] for item in parsed) != tuple(range(len(parsed))):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_BATCH_SEQUENCE_DRIFT")
    if any(item["life_foreground"] != batch_life for item in parsed):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_BATCH_LIFE_DRIFT")
    command_ids = [item["command"].command_id for item in parsed]
    idempotency_keys = [item["command"].idempotency_key for item in parsed]
    if len(set(command_ids)) != len(command_ids) or len(set(idempotency_keys)) != len(
        idempotency_keys
    ):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_BATCH_IDENTITY_DRIFT")
    return parsed


def verify_companion_expression_command(
    *,
    payload: Mapping[str, Any],
    command: EmbodimentCommand,
) -> bool:
    """判断 Preview 绑定的负载是否精确拥有该命令，而非只比较部分字段。"""

    try:
        values = parse_companion_expression_effect_batch_payload(payload)
    except CompanionExpressionEffectError:
        return False
    return any(value["command"].as_dict() == command.as_dict() for value in values)


def _required_scope(**value: Any) -> dict[str, Any]:
    """规范化冻结任务作用域；所有后续身份摘要都以此为输入。"""
    workspace_id = str(value.get("workspace_id") or "").strip()
    if len(workspace_id) > 240:
        raise CompanionExpressionEffectError("WORKSPACE_ID_INVALID")
    return {
        "graph_id": _required_text(value.get("graph_id"), "graph_id", 240),
        "graph_revision": _positive_int(value.get("graph_revision"), "graph_revision"),
        "node_id": _required_text(value.get("node_id"), "node_id", 240),
        "attempt_id": _required_text(value.get("attempt_id"), "attempt_id", 240),
        "request_id": _required_text(value.get("request_id"), "request_id", 240),
        "principal_id": _required_text(value.get("principal_id"), "principal_id", 240),
        "relationship_subject_id": _required_text(
            value.get("relationship_subject_id"),
            "relationship_subject_id",
            240,
        ),
        # Runtime Context v1 always freezes this.  The empty branch remains
        # only for the bounded pre-context replay window, where the plan still
        # has to carry a non-empty workspace of its own.
        "workspace_id": workspace_id,
        "session_id": _required_text(value.get("session_id"), "session_id", 240),
        "node_grant_ref": _required_text(value.get("node_grant_ref"), "node_grant_ref", 512),
    }


def _foreground_command(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = _mapping(raw, "foreground")
    required = {
        "command_type",
        "person_id",
        "command_id",
        "correlation_id",
        "payload",
        "created_at_ms",
        "metadata",
    }
    if set(value) != required or value.get("command_type") != "foreground_completed":
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_FOREGROUND_INVALID")
    _required_text(value.get("person_id"), "foreground.person_id", 240)
    _required_text(value.get("command_id"), "foreground.command_id", 240)
    _required_text(value.get("correlation_id"), "foreground.correlation_id", 240)
    _mapping(value.get("payload"), "foreground.payload")
    _mapping(value.get("metadata"), "foreground.metadata")
    _non_negative_int(value.get("created_at_ms"), "foreground.created_at_ms")
    return value


def _require_plan_scope(*, plan: Mapping[str, Any], command_scope: Mapping[str, Any]) -> None:
    """拒绝情感计划与任务/关系/会话/工作区任一维度的漂移。"""
    plan_workspace_id = str(plan.get("workspace_id") or "").strip()
    drifted: list[str] = []
    if plan.get("schema_version") != "affective_turn_plan.v1":
        drifted.append("schema")
    if str(plan.get("self_id") or "") != "natsume_ai":
        drifted.append("self")
    if str(plan.get("request_id") or "") != command_scope["request_id"]:
        drifted.append("request")
    if str(plan.get("relationship_subject_id") or "") != command_scope[
        "relationship_subject_id"
    ]:
        drifted.append("relationship_subject")
    if str(plan.get("session_id") or "") != command_scope["session_id"]:
        drifted.append("session")
    if not plan_workspace_id or (
        bool(command_scope["workspace_id"])
        and plan_workspace_id != command_scope["workspace_id"]
    ):
        drifted.append("workspace")
    if drifted:
        raise CompanionExpressionEffectError(
            "AFFECTIVE_TURN_PLAN_SCOPE_DRIFT:" + ",".join(drifted)
        )


def _rebuild_frozen_expression_protocol(
    *,
    affective_turn_plan: Mapping[str, Any],
    narrative: str,
    foreground_created_at_ms: int,
    payload: Mapping[str, Any],
) -> tuple[tuple[Any, ...], tuple[Any, ...], tuple[Any, ...]]:
    """Rebuild a governed protocol from its exact frozen sentence boundaries.

    Stable sentence boundaries can differ from generic text segmentation.  The
    raw worker projection is therefore never used as authority by itself: each
    sentence pair and terminal control is rebuilt from the immutable plan,
    exact frozen text and its persisted protocol timestamp.  This preserves
    already-visible task-owned sentences without collapsing them into a new
    buffered utterance at Acceptance.
    """

    raw_intents = _sequence(payload.get("expression_delivery_intents"), "expression_delivery_intents")
    raw_controls = _sequence(payload.get("expression_delivery_controls"), "expression_delivery_controls")
    raw_receipts = _sequence(payload.get("expression_delivery_receipts"), "expression_delivery_receipts")
    suppression_reason = str(
        payload.get("expression_delivery_suppressed_reason") or ""
    ).strip().upper()
    if suppression_reason:
        raw_suppressed_channels = payload.get(
            "expression_delivery_suppressed_channels"
        )
        suppressed_channels = ()
        if raw_suppressed_channels is not None:
            if not isinstance(raw_suppressed_channels, Sequence) or isinstance(
                raw_suppressed_channels, (str, bytes, bytearray)
            ):
                raise CompanionExpressionEffectError(
                    "AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT"
                )
            suppressed_channels = tuple(
                str(item or "").strip().upper()
                for item in raw_suppressed_channels
            )
        if (
            suppression_reason != "JAPANESE_SPEECH_TEXT_UNAVAILABLE"
            or str(payload.get("tts_text_ja") or "").strip()
        ):
            raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
        if not suppressed_channels:
            if raw_intents or raw_controls or raw_receipts:
                raise CompanionExpressionEffectError(
                    "AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT"
                )
            return (), (), ()
        if suppressed_channels != ("VOICE",):
            raise CompanionExpressionEffectError(
                "AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT"
            )
        try:
            parsed_intents = tuple(
                parse_expression_delivery_intent(item) for item in raw_intents
            )
            parsed_controls = tuple(
                parse_expression_delivery_intent(item) for item in raw_controls
            )
            parsed_receipts = tuple(
                parse_expression_delivery_receipt(item) for item in raw_receipts
            )
            first_segment = bounded_expression_segments(narrative)[0]
            pair, pair_receipts = build_sentence_expression_delivery(
                affective_turn_plan=affective_turn_plan,
                sentence=first_segment,
                sentence_index=0,
                created_at_ms=foreground_created_at_ms,
            )
            controls, control_receipts = build_expression_delivery_controls(
                affective_turn_plan=affective_turn_plan,
                action="RELEASE",
                sent_sentence_count=1,
                created_at_ms=foreground_created_at_ms,
            )
        except (IndexError, ValueError) as exc:
            raise CompanionExpressionEffectError(
                "AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT"
            ) from exc
        expected_intents = tuple(item for item in pair if item.channel == "AVATAR")
        expected_controls = tuple(
            item for item in controls if item.channel == "AVATAR"
        )
        expected_receipts = tuple(
            item
            for item in (*pair_receipts, *control_receipts)
            if item.channel == "AVATAR"
        )
        if (
            [item.as_dict() for item in parsed_intents]
            != [item.as_dict() for item in expected_intents]
            or [item.as_dict() for item in parsed_controls]
            != [item.as_dict() for item in expected_controls]
            or [item.as_dict() for item in parsed_receipts]
            != [item.as_dict() for item in expected_receipts]
        ):
            raise CompanionExpressionEffectError(
                "AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT"
            )
        return expected_intents, expected_controls, expected_receipts
    expected_tts_text_ja = str(payload.get("tts_text_ja") or "").strip()
    try:
        parsed_intents = tuple(
            parse_expression_delivery_intent(item) for item in raw_intents
        )
        parsed_controls = tuple(
            parse_expression_delivery_intent(item) for item in raw_controls
        )
        parsed_receipts = tuple(
            parse_expression_delivery_receipt(item) for item in raw_receipts
        )
    except ValueError as exc:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT") from exc

    if not parsed_intents or len(parsed_intents) % 2 or len(parsed_controls) != 2:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")

    expected_sentence_intents: list[Any] = []
    expected_sentence_receipts: list[Any] = []
    sentence_texts: list[str] = []
    sentence_count = len(parsed_intents) // 2
    if expected_tts_text_ja and sentence_count != 1:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
    for sentence_index in range(sentence_count):
        offset = sentence_index * 2
        raw_pair = parsed_intents[offset : offset + 2]
        voice, avatar = raw_pair
        if (
            voice.channel != "VOICE"
            or voice.action != "SPEAK"
            or avatar.channel != "AVATAR"
            or avatar.action != "TRANSITION"
            or voice.sentence_index != sentence_index
            or avatar.sentence_index != sentence_index
            or voice.created_at_ms != avatar.created_at_ms
        ):
            raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
        voice_payload = _mapping(voice.payload, "voice.payload")
        speech_sentence = _required_text(
            voice_payload.get("text"),
            "voice.payload.text",
            4_000,
        )
        sentence = _required_text(
            voice_payload.get("display_text") or speech_sentence,
            "voice.payload.display_text",
            2_000,
        )
        speech_language = str(voice_payload.get("lang_hint") or "zh").strip().lower()
        if expected_tts_text_ja:
            if (
                speech_sentence != expected_tts_text_ja
                or not speech_language.startswith("ja")
                or sentence != narrative
            ):
                raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
        elif speech_sentence != sentence or speech_language.startswith("ja"):
            raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
        expected_pair, expected_pair_receipts = build_sentence_expression_delivery(
            affective_turn_plan=affective_turn_plan,
            sentence=sentence,
            speech_sentence=(speech_sentence if expected_tts_text_ja else None),
            speech_language=(speech_language if expected_tts_text_ja else None),
            sentence_index=sentence_index,
            created_at_ms=voice.created_at_ms,
        )
        if [item.as_dict() for item in raw_pair] != [
            item.as_dict() for item in expected_pair
        ]:
            raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
        expected_sentence_intents.extend(expected_pair)
        expected_sentence_receipts.extend(expected_pair_receipts)
        sentence_texts.append(sentence)

    if "".join(sentence_texts).strip() != narrative:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")

    voice_control, avatar_control = parsed_controls
    if (
        voice_control.channel != "VOICE"
        or voice_control.action != "RELEASE"
        or avatar_control.channel != "AVATAR"
        or avatar_control.action != "RELEASE"
        or voice_control.sentence_index != sentence_count
        or avatar_control.sentence_index != sentence_count
        or voice_control.created_at_ms != avatar_control.created_at_ms
        or voice_control.created_at_ms != foreground_created_at_ms
    ):
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
    expected_controls, expected_control_receipts = build_expression_delivery_controls(
        affective_turn_plan=affective_turn_plan,
        action="RELEASE",
        sent_sentence_count=sentence_count,
        created_at_ms=foreground_created_at_ms,
    )
    if [item.as_dict() for item in parsed_controls] != [
        item.as_dict() for item in expected_controls
    ]:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")

    expected_receipts = tuple(
        (*expected_sentence_receipts, *expected_control_receipts)
    )
    if [item.as_dict() for item in parsed_receipts] != [
        item.as_dict() for item in expected_receipts
    ]:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_PROTOCOL_DRIFT")
    return tuple(expected_sentence_intents), tuple(expected_controls), expected_receipts


def _foreground_life_binding(foreground: Mapping[str, Any]) -> dict[str, str]:
    """把 foreground 命令标准化为可验证的 Life 身份绑定。"""
    try:
        command = LifeCommand(
            command_id=str(foreground["command_id"]),
            person_id=str(foreground["person_id"]),
            command_type="foreground_completed",
            payload=_mapping(foreground["payload"], "foreground.payload"),
            expected_version=0,
            created_at_ms=_non_negative_int(
                foreground["created_at_ms"], "foreground.created_at_ms"
            ),
            correlation_id=str(foreground["correlation_id"]),
            metadata=_mapping(foreground["metadata"], "foreground.metadata"),
        )
    except (TypeError, ValueError) as exc:
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_FOREGROUND_INVALID") from exc
    return {
        "command_id": command.command_id,
        "person_id": command.person_id,
        "correlation_id": str(command.correlation_id or ""),
        "command_hash": command.command_hash,
    }


def _parse_life_foreground(raw: Any) -> dict[str, str]:
    value = _mapping(raw, "life_foreground")
    required = {"command_id", "person_id", "correlation_id", "command_hash"}
    if set(value) != required:
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_LIFE_BINDING_INVALID")
    command_hash = _required_text(value.get("command_hash"), "life_foreground.command_hash", 71)
    if not _is_sha256(command_hash):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_LIFE_BINDING_INVALID")
    return {
        "command_id": _required_text(value.get("command_id"), "life_foreground.command_id", 240),
        "person_id": _required_text(value.get("person_id"), "life_foreground.person_id", 240),
        "correlation_id": _required_text(value.get("correlation_id"), "life_foreground.correlation_id", 240),
        "command_hash": command_hash,
    }


def _build_embodiment_command(
    *,
    intent: Any,
    request_id: str,
    node_grant_ref: str,
    identity_digest: str,
) -> EmbodimentCommand:
    """将已验证 intent 编译为短时、可幂等且受节点 grant 约束的设备命令。"""
    kind, action, ttl_ms = _channel_action(intent.channel, intent.action)
    expected_edge_action = {
        ("VOICE", "SPEAK"): "speak",
        ("VOICE", "CANCEL"): "barge_in",
        ("VOICE", "RELEASE"): "release",
        ("AVATAR", "TRANSITION"): "set_expression",
        ("AVATAR", "CANCEL"): "behavior",
        ("AVATAR", "RELEASE"): "release",
    }[(intent.channel, intent.action)]
    if str(intent.payload.get("edge_action") or "") != expected_edge_action:
        raise CompanionExpressionEffectError("AFFECTIVE_EXPRESSION_EDGE_ACTION_DRIFT")
    return build_embodiment_command(
        kind=kind,
        action=action,
        payload=dict(intent.payload),
        authority_ref=f"authority://task-acceptance/sha256/{identity_digest}",
        grant_ref=node_grant_ref,
        issued_at_ms=int(intent.created_at_ms),
        deadline_ms=int(intent.created_at_ms) + ttl_ms,
        idempotency_key=f"companion-expression:{identity_digest}",
        request_id=request_id,
        session_id=intent.session_id,
    )


def _require_intent_receipt_pair(*, intent: Any, receipt: Any) -> None:
    """确认协议回执只代表 Host 已暂存，绝不伪装成物理投递回执。"""
    expected_status = {
        "SPEAK": "HOST_PROTOCOL_STAGED",
        "TRANSITION": "HOST_PROTOCOL_STAGED",
        "CANCEL": "HOST_CANCEL_STAGED",
        "RELEASE": "HOST_RELEASE_STAGED",
    }[intent.action]
    if (
        receipt.delivery_id != intent.delivery_id
        or receipt.synchronization_id != intent.synchronization_id
        or receipt.channel != intent.channel
        or receipt.action != intent.action
        or receipt.status != expected_status
        or receipt.physical_receipt_ref is not None
        or receipt.created_at_ms != intent.created_at_ms
    ):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_RECEIPT_DRIFT")


def _require_embodiment_command(
    *,
    intent: Any,
    command: EmbodimentCommand,
    request_id: str,
    identity_digest: str | None,
) -> None:
    """复验命令与 intent、时限、grant 及 authority/幂等身份的不可分离绑定。"""
    kind, action, ttl_ms = _channel_action(intent.channel, intent.action)
    if (
        command.kind != kind
        or command.action != action
        or command.payload != dict(intent.payload)
        or command.request_id != request_id
        or command.session_id != intent.session_id
        or command.issued_at_ms != int(intent.created_at_ms)
        or command.deadline_ms != int(intent.created_at_ms) + ttl_ms
        or command.lease_id
        or command.fencing_token != 0
        or not command.grant_ref
        or not command.authority_ref.startswith("authority://task-acceptance/sha256/")
        or not command.idempotency_key.startswith("companion-expression:")
        or not _is_sha256(command.idempotency_key.split(":", 1)[1])
        or not _is_sha256(command.authority_ref.rsplit("/", 1)[-1])
    ):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_COMMAND_DRIFT")
    if identity_digest is not None and (
        command.idempotency_key != f"companion-expression:{identity_digest}"
        or command.authority_ref
        != f"authority://task-acceptance/sha256/{identity_digest}"
    ):
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_COMMAND_IDENTITY_DRIFT")


def _channel_action(channel: str, action: str) -> tuple[str, str, int]:
    mapping = {
        ("VOICE", "SPEAK"): ("voice", "speak", _SPEECH_TTL_MS),
        ("VOICE", "CANCEL"): ("voice", "barge_in", _CONTROL_TTL_MS),
        ("VOICE", "RELEASE"): ("voice", "release", _CONTROL_TTL_MS),
        ("AVATAR", "TRANSITION"): ("avatar", "set_expression", _SPEECH_TTL_MS),
        ("AVATAR", "CANCEL"): ("avatar", "behavior", _CONTROL_TTL_MS),
        ("AVATAR", "RELEASE"): ("avatar", "release", _CONTROL_TTL_MS),
    }
    try:
        return mapping[(str(channel), str(action))]
    except KeyError as exc:
        raise CompanionExpressionEffectError("COMPANION_EXPRESSION_CHANNEL_ACTION_INVALID") from exc


def _identity_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CompanionExpressionEffectError(f"{field_name.upper().replace('.', '_')}_INVALID")
    return dict(value)


def _sequence(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise CompanionExpressionEffectError(f"{field_name.upper()}_INVALID")
    values: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise CompanionExpressionEffectError(f"{field_name.upper()}_INVALID")
        values.append(dict(item))
    return values


def _required_text(value: Any, field_name: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum:
        raise CompanionExpressionEffectError(f"{field_name.upper().replace('.', '_')}_INVALID")
    return text


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise CompanionExpressionEffectError(f"{field_name.upper().replace('.', '_')}_INVALID")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CompanionExpressionEffectError(f"{field_name.upper().replace('.', '_')}_INVALID") from exc
    if result < 0:
        raise CompanionExpressionEffectError(f"{field_name.upper().replace('.', '_')}_INVALID")
    return result


def _positive_int(value: Any, field_name: str) -> int:
    result = _non_negative_int(value, field_name)
    if result < 1:
        raise CompanionExpressionEffectError(f"{field_name.upper().replace('.', '_')}_INVALID")
    return result


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


__all__ = [
    "COMPANION_EXPRESSION_BATCH_SCHEMA",
    "COMPANION_EXPRESSION_EFFECT_SCHEMA",
    "COMPANION_EXPRESSION_ENQUEUE_EFFECT",
    "CompanionExpressionEffectError",
    "compile_companion_expression_effect_payloads",
    "parse_companion_expression_effect_batch_payload",
    "parse_companion_expression_effect_payload",
    "verify_companion_expression_command",
]
