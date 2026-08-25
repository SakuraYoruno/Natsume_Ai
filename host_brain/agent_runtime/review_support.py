"""Pure helpers shared by Agent Runtime evidence reviewers."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


def _clean_text(value: Any, *, limit: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()
    if limit > 0 and len(text) > limit:
        return text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _packet_needs_detail_fetch(packet: Mapping[str, Any]) -> bool:
    trace = packet.get("trace")
    if not isinstance(trace, Mapping):
        return False
    readiness = trace.get("recall_answer_readiness")
    if not isinstance(readiness, Mapping):
        return False
    return (
        str(readiness.get("response_mode") or "") == "fetch_or_request_detail_evidence"
        or str(readiness.get("surface_action") or "") == "fetch_more_evidence"
    )


__all__ = ["_clean_text", "_packet_needs_detail_fetch"]
