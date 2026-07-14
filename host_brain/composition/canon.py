"""Process-owned composition for the single Canon application boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from host_brain.canon.application import CanonApplicationService
from host_brain.memory.canon_apply_service import CanonApplyService
from host_brain.persistence.canon_apply_repository import CanonApplyRepository
from host_brain.persistence.canon_repository import CanonRepository


class CanonCompositionError(RuntimeError):
    """Raised when Canon cannot bind to the process persistence authority."""


@dataclass(frozen=True, slots=True)
class ProcessCanonContainer:
    canonical_db_path: str
    repository: CanonRepository
    apply_repository: CanonApplyRepository
    apply_service: CanonApplyService
    application: CanonApplicationService


def build_process_canon_container(
    *,
    ledger: Any,
    canonical_db_path: str | Path,
    world_root: str | Path,
) -> ProcessCanonContainer:
    raw = str(canonical_db_path or "").strip()
    ledger_raw = str(getattr(ledger, "canonical_db_path", "") or "").strip()
    if not raw or raw == ":memory:" or not ledger_raw or ledger_raw == ":memory:":
        raise CanonCompositionError("Canon requires a durable canonical database")
    canonical = str(Path(raw).expanduser().resolve())
    ledger_path = str(Path(ledger_raw).expanduser().resolve())
    if Path(canonical) != Path(ledger_path):
        raise CanonCompositionError("Canon and ledger database authorities differ")

    repository = CanonRepository(canonical)
    apply_repository = CanonApplyRepository(canonical)
    apply_service = CanonApplyService(
        world_root=world_root,
        ledger=ledger,
        db_path=canonical,
        repository=apply_repository,
    )
    application = CanonApplicationService(
        repository=repository,
        world_root=world_root,
        apply_service=apply_service,
    )
    return ProcessCanonContainer(
        canonical_db_path=canonical,
        repository=repository,
        apply_repository=apply_repository,
        apply_service=apply_service,
        application=application,
    )


__all__ = [
    "CanonCompositionError",
    "ProcessCanonContainer",
    "build_process_canon_container",
]
