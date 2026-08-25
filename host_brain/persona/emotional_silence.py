"""Fail-closed boundary for the retired mutable silence engine.

Production response posture is owned by canonical Life and Persona paths.  The
old process-local ``EmotionalSilenceEngine`` is intentionally absent; this
small adapter remains only because the Host lifecycle contract still exposes a
stable, read-only binding for retirement checks.
"""

from __future__ import annotations

from typing import Any


class RetiredEmotionalSilenceEngine:
    """Fail closed without recreating a second response-posture authority."""

    status = "RETIRED_CANONICAL_LIFE"
    authority = "CANONICAL_LIFE_JOURNAL"
    reason_code = "LEGACY_SILENCE_ENGINE_RETIRED"
    __natsume_adapter_version__ = "retired-emotional-silence-adapter.v1"

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"{self.reason_code}:{str(name or '').strip() or 'operation'}")


__all__ = ["RetiredEmotionalSilenceEngine"]
