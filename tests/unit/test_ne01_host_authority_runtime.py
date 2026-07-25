from __future__ import annotations

from types import SimpleNamespace

import pytest

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.composition.host_authority_runtime import (
    HostAuthorityRuntimeCloseError,
    HostAuthorityRuntimeOwner,
    HostAuthorityRuntimeSettings,
    load_host_authority_runtime_settings,
)


class _ModelAttemptService:
    def __init__(self) -> None:
        self.clear_calls = 0

    def clear_cache(self) -> None:
        self.clear_calls += 1


class _CapabilityBroker:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _ChromaClient:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _owner(
    *,
    ledger_factory=SQLiteLedger,
) -> HostAuthorityRuntimeOwner:
    def build_model_runtime(**kwargs):
        return SimpleNamespace(
            ledger=kwargs["ledger"],
            attempt_service=_ModelAttemptService(),
            route_catalog=object(),
            prompt_registry=object(),
        )

    def build_capability_runtime(*, execution_journal):
        return SimpleNamespace(
            journal=execution_journal,
            broker=_CapabilityBroker(),
        )

    def build_episodic_store(**kwargs):
        return SimpleNamespace(
            ledger=kwargs["ledger"],
            chroma=kwargs["chroma_client"],
        )

    return HostAuthorityRuntimeOwner(
        persistence_db_path_provider=lambda: ":memory:",
        model_credential_resolver=lambda _provider: None,
        model_base_url_resolver=lambda _provider: None,
        model_transport=lambda **_kwargs: {},
        settings_provider=lambda: HostAuthorityRuntimeSettings(
            persistence_db_path=":memory:"
        ),
        ledger_factory=ledger_factory,
        chroma_config_loader=lambda **_kwargs: SimpleNamespace(
            success=True,
            config=object(),
            as_dict=lambda: {"success": True},
        ),
        chroma_client_factory=lambda _config: _ChromaClient(),
        episodic_store_factory=build_episodic_store,
        life_memory_store_factory=lambda *, ledger: SimpleNamespace(
            ledger=ledger,
            write_background_episode=lambda **_kwargs: None,
        ),
        model_runtime_factory=build_model_runtime,
        capability_journal_factory=lambda ledger: SimpleNamespace(
            ledger=ledger
        ),
        capability_runtime_factory=build_capability_runtime,
    )


def test_owner_requires_active_epoch_and_binds_one_exact_authority_graph() -> None:
    owner = _owner()
    with pytest.raises(
        RuntimeError,
        match="HOST_AUTHORITY_RUNTIME_NOT_ACTIVE",
    ):
        owner.require_ledger()

    owner.begin()
    ledger = owner.require_ledger()
    episodic = owner.require_episodic_store()
    life_memory = owner.require_life_memory_store()
    model = owner.require_model_runtime()
    capability = owner.require_capability_runtime()

    assert owner.require_ledger() is ledger
    assert owner.require_episodic_store() is episodic
    assert owner.require_life_memory_store() is life_memory
    assert owner.require_model_runtime() is model
    assert owner.require_model_attempt_service() is model.attempt_service
    assert owner.require_model_route_catalog() is model.route_catalog
    assert owner.require_prompt_registry() is model.prompt_registry
    assert owner.require_capability_runtime() is capability
    assert owner.require_capability_broker() is capability.broker
    assert episodic.ledger is ledger
    assert life_memory.ledger is ledger
    assert model.ledger is ledger
    assert capability.journal.ledger is ledger

    first_model = model
    owner.close()
    assert model.attempt_service.clear_calls == 1
    assert capability.broker.close_calls == 1
    assert episodic.chroma.close_calls == 1

    owner.begin()
    try:
        assert owner.epoch == 2
        assert owner.require_ledger() is not ledger
        assert owner.require_model_runtime() is not first_model
    finally:
        owner.close()


def test_two_host_roots_never_share_authority_runtime_instances() -> None:
    first = _owner()
    second = _owner()
    first.begin()
    second.begin()
    try:
        assert first.require_ledger() is not second.require_ledger()
        assert (
            first.require_model_attempt_service()
            is not second.require_model_attempt_service()
        )
        assert (
            first.require_prompt_registry()
            is not second.require_prompt_registry()
        )
        assert (
            first.require_model_route_catalog()
            is not second.require_model_route_catalog()
        )
        assert (
            first.require_capability_broker()
            is not second.require_capability_broker()
        )
        assert (
            first.require_life_memory_store()
            is not second.require_life_memory_store()
        )
    finally:
        first.close()
        second.close()


def test_close_failure_keeps_exact_resource_and_fences_new_epoch() -> None:
    remaining_failures = [1]

    class FailingLedger:
        def __init__(self, **_kwargs) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            if remaining_failures[0] > 0:
                remaining_failures[0] -= 1
                raise RuntimeError("injected ledger close failure")

    owner = _owner(ledger_factory=FailingLedger)
    owner.begin()
    ledger = owner.require_ledger()
    model = owner.require_model_runtime()

    with pytest.raises(
        HostAuthorityRuntimeCloseError,
        match="LEDGER_CLOSE_FAILED",
    ):
        owner.close()
    with pytest.raises(
        RuntimeError,
        match="HOST_AUTHORITY_RUNTIME_CLOSE_RETRY_REQUIRED",
    ):
        owner.begin()
    with pytest.raises(
        RuntimeError,
        match="HOST_AUTHORITY_RUNTIME_CLOSE_RETRY_REQUIRED",
    ):
        owner.require_ledger()

    owner.close()
    assert ledger.close_calls == 2
    assert model.attempt_service.clear_calls == 1


def test_settings_loader_rejects_missing_canonical_path() -> None:
    assert load_host_authority_runtime_settings(
        persistence_db_path_provider=lambda: ":memory:"
    ) == HostAuthorityRuntimeSettings(persistence_db_path=":memory:")
    with pytest.raises(
        RuntimeError,
        match="HOST_AUTHORITY_RUNTIME_DB_PATH_REQUIRED",
    ):
        load_host_authority_runtime_settings(
            persistence_db_path_provider=lambda: ""
        )
