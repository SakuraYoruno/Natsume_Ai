"""
Agent runtime post-processing pipeline: tone shaping, action masking,
length clamping, conciseness normalization, and output encoding.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Sequence


def _normalize_text(value: Any = "", lower: bool = False) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if lower:
        text = text.lower()
    return text


class _PostProcessContext:
    """Context fixture for post-processing decisions."""

    quality_gate_used: bool = False
    candidate_notes: list[str] = []
    substitute: str = ""

    def record_note(self, note: str) -> None:
        if note not in self.candidate_notes:
            self.candidate_notes.append(note)


def _post_process_text(
    text: str,
    *,
    control: dict[str, Any] | None = None,
    context: _PostProcessContext | None = None,
    quality_fn: Callable[..., Any] | None = None,
) -> str:
    """Apply the full post-processing pipeline to a candidate text.

    Pipeline order:
    1. Quality gate validation
    2. Conciseness normalization
    3. Length clamping
    4. Tone shaping
    5. Action masking
    6. Output encoding cleanup
    """
    if context is None:
        context = _PostProcessContext()
    control = dict(control or {})

    result = text or ""

    # 1. Quality gate
    if quality_fn:
        try:
            _ = quality_fn(result)
            context.quality_gate_used = True
        except Exception:
            context.record_note("post_process_quality_failed")

    # 2. Conciseness: collapse placeholder blocks
    result = re.sub(
        r"^\s*(嗯|恩|额|呃|啊|哦)\s*\n+",
        "",
        result,
        flags=re.MULTILINE | re.IGNORECASE,
    )

    # 3. Length clamping
    max_length = int(control.get("max_reply_length", 0) or 0)
    if max_length > 0 and len(result) > max_length:
        result = result[: max_length - 1].strip().rstrip(".,;:!?，。；：！？")
        result += "…"
        context.record_note("post_process_length_clamped")

    # 4. Tone shaping: remove empty interjections at start
    tone = _normalize_text(control.get("emotional_tone", "")).lower()
    if tone in ("soft", "calm", "warm"):
        result = re.sub(r"^\s*[\n\r]+", "", result)

    # 5. Action masking: replace internal action notes with action words
    if "action_masking" in control:
        result = re.sub(
            r"\[(?:行动|动作|act)\s*[:：]\s*([^\]]+)\]",
            lambda m: m.group(1).strip(),
            result,
            flags=re.IGNORECASE,
        )
        context.record_note("post_process_action_masked")

    # 6. Output encoding: strip accidental ASCII quotes in Chinese text
    result = re.sub(r'""', "「」", result)
    result = re.sub(r"''", "「」", result)

    return result


def _finalize_candidate(
    raw_text: str,
    *,
    control: dict[str, Any] | None = None,
    context: _PostProcessContext | None = None,
) -> tuple[str, list[str]]:
    """Run full post-processing and return (final_text, notes_list)."""
    context = context or _PostProcessContext()
    result = _post_process_text(raw_text, control=control, context=context)
    return result, list(context.candidate_notes)


def _normalize_narrative_payload(value: Any) -> str:
    """Coerce a sub-agent payload into a single display string."""
    if value is None:
        return ""
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned[:2048] if cleaned else ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    for key in ("narrative", "result_text", "result", "output", "text"):
        nested = getattr(value, key, None) or value.get(key) if isinstance(value, dict) else None
        if nested:
            return _normalize_narrative_payload(nested)
    return _normalize_text(str(value))
