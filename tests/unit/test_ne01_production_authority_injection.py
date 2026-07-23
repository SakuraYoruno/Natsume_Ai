from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from host_brain.app.production_authority import (
    build_durable_production_authority_verifier,
)


def test_production_authority_verifier_uses_its_explicit_ledger_provider() -> None:
    ledger = SimpleNamespace(canonical_db_path=":memory:")
    calls: list[str] = []

    def ledger_provider():  # noqa: ANN201
        calls.append("ledger")
        return ledger

    verifier = build_durable_production_authority_verifier(
        ledger_provider=ledger_provider,
    )

    result = verifier(
        {"command_id": "command-test"},
        SimpleNamespace(metadata={}, work_order_id="work-order-test"),
    )

    assert result == "AGENT_PRODUCTION_DURABLE_AUTHORITY_UNAVAILABLE"
    assert calls == ["ledger"]


def test_production_authority_verifier_rejects_non_callable_provider() -> None:
    with pytest.raises(TypeError, match="ledger_provider must be callable"):
        build_durable_production_authority_verifier(ledger_provider=None)  # type: ignore[arg-type]


def test_production_authority_verifier_fails_closed_when_provider_raises() -> None:
    def unavailable_provider():  # noqa: ANN201
        raise RuntimeError("ledger unavailable")

    verifier = build_durable_production_authority_verifier(
        ledger_provider=unavailable_provider,
    )

    result = verifier(
        {"command_id": "command-test"},
        SimpleNamespace(metadata={}, work_order_id="work-order-test"),
    )

    assert result == "AGENT_PRODUCTION_DURABLE_AUTHORITY_INVALID"


def test_production_authority_module_has_no_process_service_locator() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "host_brain"
        / "app"
        / "production_authority.py"
    ).read_text(encoding="utf-8")

    assert "host_brain.composition.process_services" not in source
    assert "get_process_service_container" not in source
    assert "get_ledger" not in source
