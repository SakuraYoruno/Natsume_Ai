"""DL-06: retiring silence authority must not degrade governed tool seeding."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from host_brain.persona.emotional_silence import RetiredEmotionalSilenceEngine
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog


def test_retired_silence_adapter_has_stable_tool_binding_identity_without_behavior() -> None:
    """Fingerprinting a captured lifecycle port never reopens legacy silence."""

    catalog = ToolCatalog(
        [
            {
                "id": "dl06.retired_silence_probe",
                "name": "DL-06 retired silence probe",
                "desc": "tests the retired lifecycle adapter boundary",
                "schema_hash": "sha256:" + "a" * 64,
                "side_effect_level": "READ",
                "auth_level": "NONE",
                "domain_tags": ["digital-life"],
                "version": "1.0.0",
            }
        ]
    )
    broker = ToolBroker(
        catalog=catalog,
        token_service=MagicMock(),
        ledger=MagicMock(),
    )
    retired = RetiredEmotionalSilenceEngine()

    def silence_port() -> RetiredEmotionalSilenceEngine:
        return retired

    def lifecycle_bound_handler(_args: dict, _context: dict | None) -> object:
        return silence_port()

    broker.register_handler(
        "dl06.retired_silence_probe",
        lifecycle_bound_handler,
        adapter_version_id="dl06.retired-silence-probe.v1",
    )

    binding = broker.resolve_execution_binding("dl06.retired_silence_probe")
    assert binding["handler_adapter_id"].startswith("tool-handler://sha256/")
    assert retired.__natsume_adapter_version__ == "retired-emotional-silence-adapter.v1"
    with pytest.raises(RuntimeError, match="LEGACY_SILENCE_ENGINE_RETIRED"):
        retired.get_silence_state()
