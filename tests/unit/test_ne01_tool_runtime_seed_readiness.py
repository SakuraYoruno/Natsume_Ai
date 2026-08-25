from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from host_brain.composition.tool_runtime import seed_production_tool_runtime
from host_brain.skills.file_registry import FileSkillProvider
from host_brain.skills.registry import SkillRegistry
from host_brain.tools.broker import ToolBroker
from host_brain.tools.catalog import ToolCatalog


def _file_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register_decorated(FileSkillProvider())
    return registry


def _broker(catalog: ToolCatalog) -> ToolBroker:
    token_service = MagicMock()
    ledger = MagicMock()
    ledger.log_skill_exec = MagicMock()
    return ToolBroker(catalog=catalog, token_service=token_service, ledger=ledger)


def test_production_seed_is_exact_but_execution_disabled_until_real_adapters() -> None:
    catalog = ToolCatalog()
    broker = _broker(catalog)

    receipt = seed_production_tool_runtime(
        catalog=catalog,
        broker=broker,
        skill_registry=_file_registry(),
        lifecycle_ports=SimpleNamespace(),
    )

    readiness = broker.readiness_snapshot()
    assert receipt.count > 0
    assert tuple(readiness["catalog_ids"]) == receipt.registered_tool_ids
    assert readiness["registered_ids"] == readiness["catalog_ids"]
    assert readiness["missing_handler_ids"] == ()
    assert readiness["orphan_handler_ids"] == ()
    assert readiness["drifted_ids"] == ()
    assert readiness["governed_executable_ids"] == ()
    assert readiness["status"] == "FAIL_CLOSED_NO_GOVERNED_EXECUTOR"
    assert (
        broker.resolve_execution_binding("file_read")["execution_safety"]
        == "RECONCILIATION_REQUIRED"
    )


def test_catalog_only_partial_seed_is_never_healthy() -> None:
    catalog = ToolCatalog(
        [
            {
                "id": "metadata_only",
                "name": "metadata only",
                "desc": "no executable handler",
                "schema_hash": "sha256:" + "a" * 64,
                "side_effect_level": "READ",
                "auth_level": "NONE",
                "domain_tags": ["test"],
                "version": "1",
            }
        ]
    )
    readiness = _broker(catalog).readiness_snapshot()
    assert readiness["status"] == "PARTIAL_SEED"
    assert readiness["missing_handler_ids"] == ("metadata_only",)
    assert readiness["governed_executable_ids"] == ()
