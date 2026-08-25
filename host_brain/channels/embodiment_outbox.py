"""Composition-ready durable Host embodiment outbox and single dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable

from natsume_contracts.edge.embodiment_v1 import (
    EmbodimentCommand,
    EmbodimentCommandPort,
    EmbodimentReceipt,
    parse_embodiment_command,
)

from host_brain.channels.embodiment import (
    EmbodimentOutboxAck,
)
from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
    EmbodimentRuntimeAuthority,
)


class DurableEmbodimentOutbox:
    def __init__(
        self,
        repository: EmbodimentOutboxRepository,
        *,
        authority_provider: Callable[[], EmbodimentRuntimeAuthority],
    ) -> None:
        self._repository = repository
        if not callable(authority_provider):
            raise TypeError("authority_provider must be callable")
        self._authority_provider = authority_provider

    def enqueue(self, command: EmbodimentCommand) -> EmbodimentOutboxAck:
        # 写入先经运行时权威并由仓储绑定幂等键；调用方只得到排队确认而非物理动作已完成的声明。
        row = self._repository.enqueue(
            command.as_dict(),
            authority=self._authority_provider(),
        )
        return EmbodimentOutboxAck(
            outbox_id=str(row["outbox_id"]),
            command_id=str(row["command_id"]),
            idempotency_key=str(row["idempotency_key"]),
            status=str(row["status"]),
            reason_code=(str(row["reason_code"]) if row.get("reason_code") else None),
        )

    def enqueue_many(
        self,
        commands: tuple[EmbodimentCommand, ...],
    ) -> tuple[EmbodimentOutboxAck, ...]:
        rows = self._repository.enqueue_many(
            tuple(command.as_dict() for command in commands),
            authority=self._authority_provider(),
        )
        return tuple(
            EmbodimentOutboxAck(
                outbox_id=str(row["outbox_id"]),
                command_id=str(row["command_id"]),
                idempotency_key=str(row["idempotency_key"]),
                status=str(row["status"]),
                reason_code=(
                    str(row["reason_code"]) if row.get("reason_code") else None
                ),
            )
            for row in rows
        )


@dataclass(frozen=True, slots=True)
class EmbodimentDispatchBatch:
    claimed: int
    applied: int
    rejected: int
    in_doubt: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "status": "SUCCEEDED" if self.rejected == 0 and self.in_doubt == 0 else "DEGRADED",
            "claimed": self.claimed,
            "applied": self.applied,
            "rejected": self.rejected,
            "in_doubt": self.in_doubt,
        }


@dataclass(frozen=True, slots=True)
class EmbodimentReconciliationBatch:
    """Result of read-only reconciliation of uncertain physical commands."""

    expired: int
    scanned: int
    applied: int
    rejected: int
    still_in_doubt: int
    not_found: int
    unavailable: int
    skipped: int

    def as_dict(self) -> dict[str, int | str]:
        unresolved = self.still_in_doubt + self.not_found + self.unavailable
        return {
            "status": "SUCCEEDED" if unresolved == 0 else "DEGRADED",
            "expired": self.expired,
            "scanned": self.scanned,
            "applied": self.applied,
            "rejected": self.rejected,
            "still_in_doubt": self.still_in_doubt,
            "not_found": self.not_found,
            "unavailable": self.unavailable,
            "skipped": self.skipped,
        }


class EmbodimentOutboxDispatcher:
    def __init__(
        self,
        repository: EmbodimentOutboxRepository,
        *,
        worker_id: str,
        command_port: EmbodimentCommandPort | None = None,
        submitter: Callable[[EmbodimentCommand], Awaitable[EmbodimentReceipt]] | None = None,
        receipt_lookup: Callable[[EmbodimentCommand], Awaitable[EmbodimentReceipt | None]] | None = None,
        receipt_observer: Callable[[EmbodimentCommand, EmbodimentReceipt], None] | None = None,
        authority_provider: Callable[[], EmbodimentRuntimeAuthority],
    ) -> None:
        self._repository = repository
        self._worker_id = str(worker_id or "host-embodiment-dispatcher")
        if command_port is not None and submitter is not None:
            raise TypeError("command_port and submitter are mutually exclusive")
        if submitter is None:
            if command_port is None:
                raise TypeError("an explicit command_port or submitter is required")
            submitter = command_port.submit
        if not callable(submitter):
            raise TypeError("submitter must be callable")
        if receipt_lookup is not None and not callable(receipt_lookup):
            raise TypeError("receipt_lookup must be callable")
        if receipt_observer is not None and not callable(receipt_observer):
            raise TypeError("receipt_observer must be callable")
        if not callable(authority_provider):
            raise TypeError("authority_provider must be callable")
        self._submitter = submitter
        self._receipt_lookup = receipt_lookup
        self._receipt_observer = receipt_observer
        self._authority_provider = authority_provider

    async def dispatch_pending_async(self, *, limit: int = 100) -> EmbodimentDispatchBatch:
        # 每次领取一项持久 outbox；命令提交没有本地重试，未知结果留给后续只读对账。
        claimed = applied = rejected = in_doubt = 0
        for _ in range(max(0, int(limit))):
            row = self._repository.claim_next(
                worker_id=self._worker_id,
                authority=self._authority_provider(),
            )
            if row is None:
                break
            claimed += 1
            command = parse_embodiment_command(dict(row["command"]))
            receipt = await self._submitter(command)
            self._repository.complete(
                outbox_id=str(row["outbox_id"]),
                claim_token=str(row["claim_token"]),
                receipt=receipt.as_dict(),
                authority=self._authority_provider(),
            )
            status = str(receipt.status or "").strip().upper()
            if receipt.accepted and status in {"APPLIED", "COMPLETED", "SUCCEEDED"}:
                applied += 1
            elif receipt.accepted and status in {"ACCEPTED", "QUEUED", "PENDING"}:
                # A durable Edge queue acknowledgement is intentionally
                # non-terminal.  The next reconciliation pass performs an
                # identity-bound receipt lookup and never replays the command.
                in_doubt += 1
            elif status == "IN_DOUBT":
                in_doubt += 1
            else:
                rejected += 1
        return EmbodimentDispatchBatch(claimed, applied, rejected, in_doubt)

    async def reconcile_in_doubt_async(
        self,
        *,
        limit: int = 100,
    ) -> EmbodimentReconciliationBatch:
        """Read durable Edge receipts for uncertainty; never submit or retry.

        Every scanned Host record remains durable.  ``NOT_FOUND`` and lookup
        failures are deliberately non-transitions, while a found receipt is
        applied only through the repository's identity- and digest-bound CAS.
        """

        # 对账只查询 Edge 的精确回执并作摘要 CAS；绝不将 IN_DOUBT 解释为可安全再次驱动物理设备。
        expired = self._repository.expire_dispatching_claims(
            authority=self._authority_provider(),
        )
        rows = self._repository.list_in_doubt(limit=max(0, int(limit)))
        scanned = applied = rejected = still_in_doubt = not_found = unavailable = skipped = 0
        for row in rows:
            scanned += 1
            try:
                command = parse_embodiment_command(dict(row["command"]))
            except (TypeError, ValueError):
                unavailable += 1
                continue
            if self._receipt_lookup is None:
                unavailable += 1
                continue
            try:
                receipt = await self._receipt_lookup(command)
            except Exception:  # read failure must preserve IN_DOUBT and never replay
                unavailable += 1
                continue
            if receipt is None:
                not_found += 1
                continue
            if (
                receipt.command_id != command.command_id
                or receipt.idempotency_key != command.idempotency_key
            ):
                unavailable += 1
                continue
            try:
                reconciled = self._repository.reconcile_in_doubt(
                    outbox_id=str(row["outbox_id"]),
                    command_id=command.command_id,
                    idempotency_key=command.idempotency_key,
                    expected_receipt_digest=(
                        str(row["receipt_digest"])
                        if row.get("receipt_digest") is not None
                        else None
                    ),
                    receipt=receipt.as_dict(),
                    authority=self._authority_provider(),
                )
            except (TypeError, ValueError):
                unavailable += 1
                continue
            status = str(receipt.status or "").strip().upper()
            if not reconciled:
                if (
                    (receipt.accepted and status in {"ACCEPTED", "QUEUED", "PENDING"})
                    or status == "IN_DOUBT"
                ):
                    # The exact non-terminal Edge receipt was already stored.
                    # It remains unresolved and must stay visible as such,
                    # even though the digest-bound CAS correctly made no write.
                    still_in_doubt += 1
                else:
                    skipped += 1
                continue
            if receipt.accepted and status in {"APPLIED", "COMPLETED", "SUCCEEDED"}:
                applied += 1
            elif receipt.accepted and status in {"ACCEPTED", "QUEUED", "PENDING"}:
                still_in_doubt += 1
            elif status == "IN_DOUBT":
                still_in_doubt += 1
            else:
                rejected += 1
            if self._receipt_observer is not None:
                try:
                    self._receipt_observer(command, receipt)
                except Exception:
                    # Observability may degrade, but the canonical receipt CAS
                    # succeeded and must not be retried through the actuator.
                    pass
        return EmbodimentReconciliationBatch(
            expired=expired,
            scanned=scanned,
            applied=applied,
            rejected=rejected,
            still_in_doubt=still_in_doubt,
            not_found=not_found,
            unavailable=unavailable,
            skipped=skipped,
        )


__all__ = [
    "DurableEmbodimentOutbox",
    "EmbodimentDispatchBatch",
    "EmbodimentReconciliationBatch",
    "EmbodimentOutboxDispatcher",
]
