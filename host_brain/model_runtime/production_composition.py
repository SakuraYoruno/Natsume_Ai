"""Production composition for the single model-attempt authority."""

from __future__ import annotations

from typing import Any

from host_brain.model_runtime.composition import (
    ModelRuntimeComposition,
    build_model_runtime_composition,
)
from host_brain.model_runtime.live_prompts import build_live_production_prompt_registry
from host_brain.model_runtime.production_provider_adapters import (
    build_vault_backed_provider_registry,
)
from host_brain.model_runtime.production_route_policy import (
    build_server_owned_production_route_policy,
)


def build_default_production_model_runtime(
    *,
    ledger: Any,
) -> ModelRuntimeComposition:
    """Build the sole production prompt/route/provider/service object graph."""

    return build_model_runtime_composition(
        prompt_registry=build_live_production_prompt_registry(),
        route_policy=build_server_owned_production_route_policy(),
        provider_adapters=build_vault_backed_provider_registry(),
        ledger=ledger,
    )


__all__ = ["build_default_production_model_runtime"]
