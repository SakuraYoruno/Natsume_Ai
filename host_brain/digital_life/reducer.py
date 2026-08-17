"""Digital Life 命令的纯确定性 reducer。

reducer 从已有快照和完整命令同步计算新状态、事件、projection intent 与投递意图；
不读取时钟、随机数、I/O 或全局状态，因此 journal 可被独立重放并校验。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from natsume_contracts.life import (
    LifeCommand,
    LifeCommandPreview,
    LifeDeliveryIntent,
    LifeDeliveryAuthorization,
    LifeEvent,
    LifeIntentKind,
    LifeProjectionIntent,
    LifeSnapshot,
    digest_json,
    normalize_intent_rows,
)
from host_brain.digital_life.domain_reducer import reduce_life_domains


class DeterministicLifeReducer:
    """无时钟、随机数、I/O 或全局状态地归约一条命令。"""

    def reduce(self, before: LifeSnapshot, command: LifeCommand) -> LifeCommandPreview:
        """在同一 aggregate 内派生原子 preview。

        ``applied_at_ms`` 取命令、快照与先前集成时间的最大值，保证时间单调且
        重放稳定；事件摘要绑定状态前后、因果链及命令哈希，防止投影被单独篡改。
        """
        if before.person_id != command.person_id:
            raise ValueError("command and snapshot person_id differ")

        prior_clock = dict(before.state.get("clock") or {}) if isinstance(
            before.state.get("clock"), Mapping
        ) else {}
        prior_runtime = dict(before.state.get("runtime") or {}) if isinstance(
            before.state.get("runtime"), Mapping
        ) else {}
        applied_at_ms = max(
            command.created_at_ms,
            before.updated_at_ms,
            int(prior_clock.get("last_integrated_ms") or 0),
            int(prior_runtime.get("last_integrated_ms") or 0),
        )
        next_state = reduce_life_domains(
            before.state,
            person_id=command.person_id,
            command_id=command.command_id,
            command_type=command.command_type,
            payload=command.payload,
            created_at_ms=applied_at_ms,
        )
        next_version = before.version + 1
        next_state.update(
            {
                "person_id": command.person_id,
                "version": next_version,
                "last_command_id": command.command_id,
                "last_command_type": command.command_type,
                "last_command_at_ms": applied_at_ms,
            }
        )
        state_digest = digest_json(next_state)
        event_id = f"life_evt_{digest_json({'command_id': command.command_id})[:32]}"
        event_payload = {
            "command_hash": command.command_hash,
            "command_type": command.command_type,
            "command_payload": dict(command.payload),
            "state_before": dict(before.state),
            "state_after": dict(next_state),
            "correlation_id": command.correlation_id,
            "causation_id": command.causation_id,
        }
        event_digest = digest_json(
            {
                "event_id": event_id,
                "command_id": command.command_id,
                "person_id": command.person_id,
                "sequence": next_version,
                "event_type": command.command_type,
                "payload": event_payload,
                "state_before_digest": before.state_digest,
                "state_after_digest": state_digest,
                "previous_event_digest": before.last_event_digest,
                "created_at_ms": applied_at_ms,
            }
        )
        event = LifeEvent(
            event_id=event_id,
            command_id=command.command_id,
            person_id=command.person_id,
            sequence=next_version,
            event_type=command.command_type,
            payload=event_payload,
            state_before_digest=before.state_digest,
            state_after_digest=state_digest,
            previous_event_digest=before.last_event_digest,
            event_digest=event_digest,
            created_at_ms=applied_at_ms,
        )
        after = LifeSnapshot(
            person_id=command.person_id,
            version=next_version,
            state=next_state,
            state_digest=state_digest,
            last_event_id=event_id,
            last_event_digest=event_digest,
            updated_at_ms=applied_at_ms,
        )
        intents = _projection_intents(command, event)
        deliveries = _delivery_intents(command)
        return LifeCommandPreview(
            command=command,
            before=before,
            after=after,
            event=event,
            projection_intents=intents,
            deliveries=deliveries,
        )


def _projection_intents(
    command: LifeCommand,
    event: LifeEvent,
) -> tuple[LifeProjectionIntent, ...]:
    """将域内 organ/projection 行规范化为有稳定身份的后处理 intent。

    intent ID 绑定事件、顺序、目标与负载，使 handler 重试可复用同一条持久化
    工作，而不是由 reducer 执行外部副作用。
    """
    output: list[LifeProjectionIntent] = []
    groups = (
        ("organ_intents", LifeIntentKind.ORGAN),
        ("projection_intents", LifeIntentKind.PROJECTION),
    )
    for payload_key, kind in groups:
        for index, row in enumerate(normalize_intent_rows(command.payload.get(payload_key))):
            target = str(
                row.get("target")
                or row.get("organ_id")
                or row.get("projection_id")
                or ""
            ).strip()
            if not target:
                raise ValueError(f"{payload_key}[{index}] requires target")
            raw_payload = row.get("payload") or {}
            if not isinstance(raw_payload, Mapping):
                raise ValueError(f"{payload_key}[{index}].payload must be an object")
            payload = dict(raw_payload)
            payload_digest = digest_json(payload)
            intent_seed = {
                "event_id": event.event_id,
                "kind": kind.value,
                "index": index,
                "target": target,
                "payload": payload,
            }
            intent_id = f"life_int_{digest_json(intent_seed)[:32]}"
            output.append(
                LifeProjectionIntent(
                    intent_id=intent_id,
                    event_id=event.event_id,
                    command_id=command.command_id,
                    person_id=command.person_id,
                    intent_kind=kind,
                    target=target,
                    payload=payload,
                    payload_digest=payload_digest,
                    created_at_ms=event.created_at_ms,
                )
            )
    return tuple(output)


def _delivery_intents(command: LifeCommand) -> tuple[LifeDeliveryIntent, ...]:
    """提取外部投递意图并要求每条都携带可验证授权。

    缺少或无效 authorization 时在 reducer 阶段拒绝命令，避免 outbox 接收到仅
    凭 destination/payload 就能发送到外部通道的无权限工作。
    """
    output: list[LifeDeliveryIntent] = []
    for index, row in enumerate(normalize_intent_rows(command.payload.get("deliveries"))):
        destination = str(row.get("destination") or row.get("target") or "").strip()
        if not destination:
            raise ValueError(f"deliveries[{index}] requires destination")
        payload = row.get("payload") or {}
        if not isinstance(payload, Mapping):
            raise ValueError(f"deliveries[{index}].payload must be an object")
        authorization_payload = row.get("authorization")
        if not isinstance(authorization_payload, Mapping):
            raise ValueError(
                f"deliveries[{index}].authorization is required for external delivery"
            )
        try:
            authorization = LifeDeliveryAuthorization.from_mapping(
                authorization_payload
            )
        except ValueError as exc:
            raise ValueError(f"deliveries[{index}].authorization invalid: {exc}") from exc
        output.append(
            LifeDeliveryIntent(
                destination=destination,
                payload=dict(payload),
                authorization=authorization,
            )
        )
    return tuple(output)
__all__ = ["DeterministicLifeReducer"]
