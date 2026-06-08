from __future__ import annotations

import asyncio
import json

from tools.validate_live_service_boundaries import (
    build_arg_parser,
    run_live_service_boundary_validation,
)


def test_live_service_boundary_parser_defaults_to_live() -> None:
    args = build_arg_parser().parse_args([])

    assert args.mode == "live"
    assert args.allow_hash_embedding is False


def test_live_service_boundary_fixture_marks_mocks_and_writes_report(tmp_path) -> None:
    args = build_arg_parser().parse_args(
        ["--mode", "fixture", "--out-dir", str(tmp_path), "--run-id", "unit-boundary"]
    )

    result = asyncio.run(run_live_service_boundary_validation(args))

    summary = result["summary"]
    assert summary["passed"] is True
    assert summary["components"]["chroma"]["runtime_class"] == "mock"
    assert summary["components"]["neo4j"]["runtime_class"] == "mock"
    assert summary["components"]["llm"]["runtime_class"] == "mock"
    assert summary["components"]["vts_config"]["runtime_class"] == "live"
    assert summary["checks"]["service_boundary.fixture_marks_mock_components"] is True
    assert summary["checks"]["service_boundary.fixture_does_not_claim_live_services"] is True

    saved_summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert saved_summary["passed"] is True
    assert (tmp_path / "details.json").exists()
    assert (tmp_path / "report.md").exists()
