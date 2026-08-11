"""Production composition for the single model-attempt authority."""

from __future__ import annotations

from typing import Any

from host_brain.model_runtime.composition import (
    ModelRuntimeComposition,
    build_model_runtime_composition,
)
from host_brain.model_runtime.live_prompts import build_live_production_prompt_registry
from host_brain.model_runtime.production_provider_adapters import (
    BaseUrlResolver,
    CredentialResolver,
    SingleAttemptTransport,
    build_vault_backed_provider_registry,
)
from host_brain.model_runtime.production_route_policy import (
    build_production_server_route_catalog,
    build_server_owned_production_route_policy,
)


_DEFAULT_ROUTE_CATALOG = build_production_server_route_catalog()
_DEFAULT_PROMPT_REGISTRY = build_live_production_prompt_registry()


def get_default_production_route_catalog():
    """Return the catalog shared by projections and the production route policy."""

    return _DEFAULT_ROUTE_CATALOG


def get_default_production_prompt_registry():
    """Return the one code-side registry shared by boot and composition."""

    return _DEFAULT_PROMPT_REGISTRY


def build_default_production_model_runtime(
    *,
    ledger: Any,
    credential_resolver: CredentialResolver,
    base_url_resolver: BaseUrlResolver,
    transport: SingleAttemptTransport,
) -> ModelRuntimeComposition:
    """Build the sole production prompt/route/provider/service object graph."""

    route_catalog = get_default_production_route_catalog()
    return _build_production_model_runtime(
        ledger=ledger,
        credential_resolver=credential_resolver,
        base_url_resolver=base_url_resolver,
        transport=transport,
        prompt_registry=get_default_production_prompt_registry(),
        route_catalog=route_catalog,
    )


def build_app_local_production_model_runtime(
    *,
    ledger: Any,
    credential_resolver: CredentialResolver,
    base_url_resolver: BaseUrlResolver,
    transport: SingleAttemptTransport,
) -> ModelRuntimeComposition:
    """Build a fresh prompt/route/provider/service graph for one Host epoch."""

    route_catalog = build_production_server_route_catalog()
    return _build_production_model_runtime(
        ledger=ledger,
        credential_resolver=credential_resolver,
        base_url_resolver=base_url_resolver,
        transport=transport,
        prompt_registry=build_live_production_prompt_registry(),
        route_catalog=route_catalog,
    )


def _build_production_model_runtime(
    *,
    ledger: Any,
    credential_resolver: CredentialResolver,
    base_url_resolver: BaseUrlResolver,
    transport: SingleAttemptTransport,
    prompt_registry: Any,
    route_catalog: Any,
) -> ModelRuntimeComposition:
    return build_model_runtime_composition(
        prompt_registry=prompt_registry,
        route_policy=build_server_owned_production_route_policy(
            route_catalog=route_catalog,
        ),
        route_catalog=route_catalog,
        provider_adapters=build_vault_backed_provider_registry(
            credential_resolver=credential_resolver,
            base_url_resolver=base_url_resolver,
            transport=transport,
        ),
        ledger=ledger,
    )


__all__ = [
    "build_app_local_production_model_runtime",
    "build_default_production_model_runtime",
    "get_default_production_prompt_registry",
    "get_default_production_route_catalog",
]
