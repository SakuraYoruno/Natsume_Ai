from __future__ import annotations

import inspect
from pathlib import Path

import host_brain.app.route_catalog_runtime as route_catalog_runtime


ROOT = Path(__file__).parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_retired_model_construction_modules_are_deleted() -> None:
    assert not (ROOT / "host_brain/app/provider_factory.py").exists()
    assert not (ROOT / "host_brain/app/graph_config_llm_runtime.py").exists()
    assert not (ROOT / "host_brain/app/executive_runtime_core.py").exists()


def test_graph_builder_has_no_private_retry_or_provider_factory() -> None:
    source = _source("host_brain/app/graph_config_builder.py")

    for forbidden in (
        "provider_factory",
        "HOST_GEMINI_PRIMARY_RETRY_ATTEMPTS",
        "HOST_GEMINI_PRIMARY_RETRY_DELAY_MS",
        "_GEMINI_PRIMARY_RETRY_NODE_ID",
    ):
        assert forbidden not in source


def test_direct_text_compatibility_switch_and_bypasses_are_gone() -> None:
    source = "\n".join(
        _source(relative)
        for relative in (
            "host_brain/core/orchestration_settings.py",
        )
    )

    for forbidden in (
        "HOST_LEGACY_DIRECT_TEXT_ROUTES_ENABLED",
        "legacy_direct_text_routes_enabled",
        "try_handle_recent_turn_direct_route",
        "try_handle_task_progress_direct_route",
        "HOST_CHAT_LEGACY_REPLY_CONTRACT_PROMPT",
    ):
        assert forbidden not in source


def test_route_projection_uses_the_composition_catalog() -> None:
    source = inspect.getsource(route_catalog_runtime)
    assert "catalog.resolve(" in source
    assert "ServerRouteCatalog(" not in source
