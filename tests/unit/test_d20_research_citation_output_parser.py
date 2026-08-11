from __future__ import annotations

import json

import pytest

from host_brain.planning_search.research_citation_output_parser import (
    RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1,
    ResearchCitationOutputParseError,
    ResearchCitationOutputParserV1,
)


def _payload(*, citations: list[dict] | None = None, **extra):  # noqa: ANN001
    result = {
        "answer": "private answer body that must not enter the ledger",
        "citations": citations
        if citations is not None
        else [
            {
                "source_id": "primary",
                "start_byte": 0,
                "end_byte": 3,
                "span_digest": "a" * 64,
            }
        ],
    }
    result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def test_d20c_parser_returns_only_sorted_citation_spans() -> None:
    parser = ResearchCitationOutputParserV1()
    citations = parser(
        _payload(
            citations=[
                {
                    "source_id": "secondary",
                    "start_byte": 2,
                    "end_byte": 4,
                    "span_digest": "b" * 64,
                },
                {
                    "source_id": "primary",
                    "start_byte": 0,
                    "end_byte": 3,
                    "span_digest": "a" * 64,
                },
            ]
        )
    )

    assert parser.output_schema_digest == RESEARCH_CITATION_OUTPUT_SCHEMA_DIGEST_V1
    assert [item.source_id for item in citations] == ["primary", "secondary"]
    assert all("private answer" not in repr(item) for item in citations)


@pytest.mark.parametrize(
    "output",
    (
        "not-json",
        json.dumps({"answer": "x", "citations": [], "unexpected": True}),
        json.dumps({"answer": "x", "citations": "not-a-list"}),
        json.dumps(
            {
                "answer": "x",
                "citations": [
                    {
                        "source_id": "primary",
                        "start_byte": 0,
                        "end_byte": 3,
                        "span_digest": "a" * 64,
                        "untrusted": "field",
                    }
                ],
            }
        ),
    ),
)
def test_d20c_parser_rejects_untrusted_envelope_shapes(output: str) -> None:
    parser = ResearchCitationOutputParserV1()

    with pytest.raises(ResearchCitationOutputParseError) as error:
        parser(output)

    assert "not-json" not in str(error.value)


def test_d20c_parser_rejects_duplicate_spans_and_oversized_output() -> None:
    parser = ResearchCitationOutputParserV1(max_output_bytes=96)
    duplicate = _payload(
        citations=[
            {
                "source_id": "primary",
                "start_byte": 0,
                "end_byte": 3,
                "span_digest": "a" * 64,
            },
            {
                "source_id": "primary",
                "start_byte": 0,
                "end_byte": 3,
                "span_digest": "a" * 64,
            },
        ]
    )

    with pytest.raises(ResearchCitationOutputParseError, match="size"):
        parser(duplicate)
    with pytest.raises(ResearchCitationOutputParseError, match="size"):
        parser("x" * 97)

    normal = ResearchCitationOutputParserV1()
    with pytest.raises(ResearchCitationOutputParseError, match="unique"):
        normal(duplicate)
