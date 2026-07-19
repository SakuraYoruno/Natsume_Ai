"""Application-local ownership for the Host's immutable hard state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import logging
from threading import Lock
import time
from typing import Any

from host_brain.core.metabolism import (
    ALLOWED_SYS_MODES,
    HardState,
    evolve_hard_state,
    normalize_hard_state,
)


EndocrineResolver = Callable[..., Mapping[str, Any]]
ClockMs = Callable[[], int]

_logger = logging.getLogger("Natsume.host_hard_state")
_ENDOCRINE_FALLBACK = {
    "dopamine_level": 0.5,
    "serotonin_level": 0.5,
    "cortisol_level": 0.25,
    "energy_reserve": 0.6,
    "gate_state": "open",
    "source": "endocrine_fallback",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _resolve_endocrine_state(**kwargs: Any) -> Mapping[str, Any]:
    """Resolve through the existing Tribe adapter without owning its lifecycle."""

    from host_brain.tribe.endocrine_agent import get_endocrine_agent  # noqa: PLC0415

    return get_endocrine_agent().resolve_endocrine_state(**kwargs)


@dataclass(slots=True)
class HostHardStateOwner:
    """Linearizable, application-local owner of the immutable ``HardState``.

    The owner never reads or writes process environment state.  A composition
    root must supply the bootstrap mode explicitly.  External endocrine work is
    deliberately invoked after taking a frozen state snapshot and releasing
    the owner lock.
    """

    _state: HardState
    _endocrine_resolver: EndocrineResolver = field(repr=False)
    _clock_ms: ClockMs = field(repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def bootstrap(
        cls,
        *,
        initial_sys_mode: str,
        endocrine_resolver: EndocrineResolver = _resolve_endocrine_state,
        clock_ms: ClockMs = _now_ms,
    ) -> "HostHardStateOwner":
        """Create one owner from an explicit, bootstrap-only system mode."""

        normalized_mode = str(initial_sys_mode or "").strip().lower()
        if normalized_mode not in ALLOWED_SYS_MODES:
            _logger.error(
                "非法 bootstrap sys_mode=%r，Host 以 maintenance 失败关闭",
                initial_sys_mode,
            )
            normalized_mode = "maintenance"
        state = normalize_hard_state(
            {
                "energy_level": 60,
                "mood_state": "steady",
                "sys_mode": normalized_mode,
                "safety_flag": False,
                "ts_ms": int(clock_ms()),
            }
        )
        return cls(
            _state=state,
            _endocrine_resolver=endocrine_resolver,
            _clock_ms=clock_ms,
        )

    def get_hard_state(self) -> HardState:
        """Return the current immutable snapshot."""

        with self._lock:
            return self._state

    def set_sys_mode(self, target_mode: str) -> dict[str, Any]:
        """Atomically replace the state without recursive lock acquisition."""

        normalized_target = str(target_mode or "").strip().lower()
        if normalized_target not in ALLOWED_SYS_MODES:
            raise ValueError(
                "invalid sys_mode: "
                f"{target_mode!r}; expected one of "
                f"{', '.join(sorted(ALLOWED_SYS_MODES))}"
            )

        with self._lock:
            current = self._state
            ts_ms = int(self._clock_ms())
            next_state = evolve_hard_state(
                current,
                sys_mode=normalized_target,
                event_kind="mode_switch",
                ts_ms=ts_ms,
            )
            self._state = next_state

        _logger.info("sys_mode 已切换: %s → %s", current.sys_mode, next_state.sys_mode)
        return {
            "from_mode": current.sys_mode,
            "to_mode": next_state.sys_mode,
            "ts_ms": ts_ms,
        }

    def get_endocrine_state(
        self,
        *,
        emotion_tag: str | None = None,
        emotion_intensity: float | None = None,
    ) -> dict[str, Any]:
        """Resolve endocrine state from a lock-free immutable snapshot."""

        hard_state = self.get_hard_state()
        try:
            resolved = self._endocrine_resolver(
                hard_state=hard_state,
                emotion_tag=emotion_tag,
                emotion_intensity=emotion_intensity,
            )
            return dict(resolved)
        except Exception as exc:
            _logger.warning("EndocrineState 计算失败（降级）: %s", exc)
            return dict(_ENDOCRINE_FALLBACK)


__all__ = ["HostHardStateOwner"]
