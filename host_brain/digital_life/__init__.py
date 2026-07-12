"""Public AW04-B Digital Life command foundation."""

from __future__ import annotations

from host_brain.digital_life.contracts import (
    LifeCommand,
    LifeCommandClaim,
    LifeCommandPreview,
    LifeCommandReceipt,
    LifeCommandStatus,
    LifeDeliveryIntent,
    LifeDeliveryStatus,
    LifeDispatchSummary,
    LifeEvent,
    LifeIntentKind,
    LifeOutboxMessage,
    LifeProjectionIntent,
    LifeProjectionMessage,
    LifeProjectionReceipt,
    LifeSnapshot,
)


def __getattr__(name: str):
    if name == "LifeCommandService":
        from host_brain.digital_life.service import LifeCommandService

        return LifeCommandService
    if name == "LifeSnapshotReader":
        from host_brain.digital_life.reader import LifeSnapshotReader

        return LifeSnapshotReader
    if name == "LifeOutboxDispatcher":
        from host_brain.digital_life.outbox import LifeOutboxDispatcher

        return LifeOutboxDispatcher
    if name == "LifeProjectionDispatcher":
        from host_brain.digital_life.projection import LifeProjectionDispatcher

        return LifeProjectionDispatcher
    if name == "DeterministicLifeReducer":
        from host_brain.digital_life.reducer import DeterministicLifeReducer

        return DeterministicLifeReducer
    if name == "record_life_experience":
        from host_brain.digital_life.experience_ingress import record_life_experience

        return record_life_experience
    raise AttributeError(name)


__all__ = [
    "DeterministicLifeReducer",
    "LifeCommand",
    "LifeCommandClaim",
    "LifeCommandPreview",
    "LifeCommandReceipt",
    "LifeCommandService",
    "LifeCommandStatus",
    "LifeDeliveryIntent",
    "LifeDeliveryStatus",
    "LifeDispatchSummary",
    "LifeEvent",
    "LifeIntentKind",
    "LifeOutboxDispatcher",
    "LifeOutboxMessage",
    "LifeProjectionIntent",
    "LifeProjectionDispatcher",
    "LifeProjectionMessage",
    "LifeProjectionReceipt",
    "LifeSnapshot",
    "LifeSnapshotReader",
    "record_life_experience",
]
