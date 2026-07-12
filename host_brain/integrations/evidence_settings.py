"""Typed configuration boundary for Graph Evidence persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path


EVIDENCE_DB_PATH_ENV = "EVIDENCE_DB_PATH"
DEFAULT_EVIDENCE_DB_PATH = Path("data/evidence/evidence.db")


@dataclass(frozen=True, slots=True)
class EvidenceSettings:
    db_path: str | Path = DEFAULT_EVIDENCE_DB_PATH
    timeout_ms: int = 3_000

    def __post_init__(self) -> None:
        raw_path = str(self.db_path or "").strip()
        if not raw_path:
            raise ValueError("evidence db_path is required")
        if int(self.timeout_ms) < 1:
            raise ValueError("evidence timeout_ms must be positive")
        object.__setattr__(
            self,
            "db_path",
            raw_path if raw_path == ":memory:" else Path(raw_path),
        )
        object.__setattr__(self, "timeout_ms", int(self.timeout_ms))


def load_evidence_settings(
    *,
    db_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
    timeout_ms: int = 3_000,
) -> EvidenceSettings:
    source = os.environ if environ is None else environ
    env_path = str(source.get(EVIDENCE_DB_PATH_ENV, "") or "").strip()
    resolved = db_path if db_path is not None else env_path or DEFAULT_EVIDENCE_DB_PATH
    return EvidenceSettings(db_path=resolved, timeout_ms=timeout_ms)


__all__ = [
    "DEFAULT_EVIDENCE_DB_PATH",
    "EVIDENCE_DB_PATH_ENV",
    "EvidenceSettings",
    "load_evidence_settings",
]
