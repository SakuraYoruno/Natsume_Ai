"""Strict, non-persisting parser for D20 Research citation proposals.

External-model text is untrusted and must not be copied into the durable
Research ledger.  This parser accepts only a versioned JSON envelope and
returns citation spans for a separately constructed frozen-corpus verifier.
The answer body is deliberately discarded at this boundary.
"""

from __future__ import annotations

import hashlib
import json

from host_brain.contracts.planning_search.research_benchmark_contracts import (
    ResearchCitationSpanV1,
)


RESEARCH_CITATION_OUTPUT_SCHEMA_V1 = "natsume.research.citation-output.v1"
"""Stable output-envelope identity for D20c profile binding."""

_OUTPUT_SCHEMA = {
    "schema": RESEARCH_CITATION_OUTPUT_SCHEMA_V1,
    "type": "object",
    "required": ["answer", "citations"],
    "additional_properties": False,
    "answer": {"type": "string"},
    "citations": {
        "type": "array",
        "max_items": 128,
        "item": {
            "type": "object",
            "required": ["source_id", "start_byte", "end_byte", "span_digest"],
            "additional_properties": False,
        },
    },
}


def _schema_digest() -> str:
    encoded = json.dumps(
        _OUTPUT_SCHEMA,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1 = _schema_digest()
"""Digest that must equal ``ResearchExecutionProfileV1.output_schema_digest``."""


class ResearchCitationOutputParseError(ValueError):
    """The untrusted model output cannot be treated as citation proposals."""


class ResearchCitationOutputParserV1:
    """Parse only the frozen V1 envelope and discard its private answer text."""

    output_schema_digest = RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1

    def __init__(self, *, max_output_bytes: int = 262_144) -> None:
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 1 <= max_output_bytes <= 1_048_576
        ):
            raise ValueError("max_output_bytes must be a bounded positive integer")
        self._max_output_bytes = max_output_bytes

    def __call__(self, output_text: str) -> tuple[ResearchCitationSpanV1, ...]:
        if not isinstance(output_text, str):
            raise ResearchCitationOutputParseError("Research output must be text")
        encoded = output_text.encode("utf-8")
        if not encoded or len(encoded) > self._max_output_bytes:
            raise ResearchCitationOutputParseError("Research output size is invalid")
        try:
            payload = json.loads(output_text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ResearchCitationOutputParseError(
                "Research output must use the citation envelope JSON"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {"answer", "citations"}:
            raise ResearchCitationOutputParseError("Research citation envelope shape is invalid")
        if not isinstance(payload["answer"], str):
            raise ResearchCitationOutputParseError("Research citation envelope answer is invalid")
        citations = payload["citations"]
        if not isinstance(citations, list) or len(citations) > 128:
            raise ResearchCitationOutputParseError("Research citation envelope citations are invalid")
        parsed: list[ResearchCitationSpanV1] = []
        for raw in citations:
            if not isinstance(raw, dict) or set(raw) != {
                "source_id",
                "start_byte",
                "end_byte",
                "span_digest",
            }:
                raise ResearchCitationOutputParseError("Research citation item shape is invalid")
            try:
                parsed.append(
                    ResearchCitationSpanV1(
                        source_id=raw["source_id"],
                        start_byte=raw["start_byte"],
                        end_byte=raw["end_byte"],
                        span_digest=raw["span_digest"],
                    )
                )
            except (TypeError, ValueError) as exc:
                raise ResearchCitationOutputParseError(
                    "Research citation item is invalid"
                ) from exc
        identities = {
            (item.source_id, item.start_byte, item.end_byte, item.span_digest)
            for item in parsed
        }
        if len(identities) != len(parsed):
            raise ResearchCitationOutputParseError("Research citation items must be unique")
        return tuple(
            sorted(
                parsed,
                key=lambda item: (
                    item.source_id,
                    item.start_byte,
                    item.end_byte,
                    item.span_digest,
                ),
            )
        )


__all__ = [
    "RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1",
    "RESEARCH_CITATION_OUTPUT_SCHEMA_V1",
    "ResearchCitationOutputParseError",
    "ResearchCitationOutputParserV1",
]
