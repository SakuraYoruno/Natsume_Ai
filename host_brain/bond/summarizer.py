"""Read-only Bond summary derivation helpers."""

from __future__ import annotations

from typing import Any, Iterable

from host_brain.core.runtime_helpers import _optional_text as _as_optional_text


DEFAULT_STAGE = "UNKNOWN"
DEFAULT_TREND = "STABLE"
DEFAULT_CAPABILITY_HINT = "UNKNOWN"

BOND_SUMMARY_ALLOWED_KEYS = frozenset(
    {
        "stage",
        "trend",
        "capability_hint",
        "event_id",
        "evidence_refs",
    }
)


def derive_bond_summary(
    event: dict[str, Any] | None,
    *,
    bond_score_state: Any | None = None,
) -> dict[str, Any]:
    """Build a context-pack-safe Bond summary from a bond event record.

    M27-F（OPT-27）：优先从 BondScoreState 读取 stage/trend，修复从 event["stage"] 读取的 Bug。

    Args:
        event: Bond 事件记录（dict）。
        bond_score_state: 可选 BondScoreState 实例，提供时优先从其读取 stage/trend。
    """
    if not isinstance(event, dict):
        event = {}

    # M27-F：优先从 BondScoreState 读取 stage/trend（修复 Bug）
    if bond_score_state is not None:
        stage = getattr(bond_score_state, "current_stage", DEFAULT_STAGE)
        trend = bond_score_state.trend() if callable(getattr(bond_score_state, "trend", None)) else DEFAULT_TREND
        capability_hint = (
            bond_score_state.capability_hint()
            if callable(getattr(bond_score_state, "capability_hint", None))
            else DEFAULT_CAPABILITY_HINT
        )
    else:
        # 兼容旧版：从 event 读取（但 event["stage"] 通常是 "S_BOND_UPDATE"，不是阶段名）
        stage = _as_text_bucket(event.get("stage"), default=DEFAULT_STAGE)
        trend = _as_text_bucket(event.get("trend"), default=DEFAULT_TREND)
        capability_hint = _as_text_bucket(
            event.get("capability_hint"),
            default=DEFAULT_CAPABILITY_HINT,
        )

    return {
        "stage": stage,
        "trend": trend,
        "capability_hint": capability_hint,
        "event_id": _as_optional_text(event.get("event_id")),
        "evidence_refs": _normalize_evidence_refs(event.get("evidence_refs")),
    }


def normalize_bond_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize externally provided summary payload to canonical contract shape."""
    if not isinstance(summary, dict):
        return derive_bond_summary(None)

    return {
        "stage": _as_text_bucket(summary.get("stage"), default=DEFAULT_STAGE),
        "trend": _as_text_bucket(summary.get("trend"), default=DEFAULT_TREND),
        "capability_hint": _as_text_bucket(
            summary.get("capability_hint"),
            default=DEFAULT_CAPABILITY_HINT,
        ),
        "event_id": _as_optional_text(summary.get("event_id")),
        "evidence_refs": _normalize_evidence_refs(summary.get("evidence_refs")),
    }


def summary_has_numeric_value(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    for value in _iter_values(summary):
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return True
    return False


def _as_text_bucket(value: Any, *, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _normalize_evidence_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    refs: list[str] = []
    for item in value:
        ref = str(item or "").strip()
        if ref:
            refs.append(ref)
    return refs


def _iter_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_values(item)
        return
    yield value
