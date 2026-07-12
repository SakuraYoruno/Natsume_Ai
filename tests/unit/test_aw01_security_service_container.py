from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import host_brain.composition.security_services as container_module
from host_brain.composition.security_services import (
    ProcessSecurityServiceContainer,
    SECURITY_SERVICE_CANONICAL_DB_PATH_MISMATCH,
    SECURITY_SERVICE_PROCESS_SCOPE_VIOLATION,
    SecurityServiceContainerCloseError,
    SecurityServiceName,
    build_process_security_service_container,
)
from host_brain.governor.tokens import TokenService
from host_brain.observability.command_composition import (
    OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED,
    ObsCommandComposition,
)
from host_brain.observability.command_ports import ObsCommandPortRegistry
from host_brain.observability.command_relocation import (
    ObsCommandRelocationMode,
)
from host_brain.pentest.production_runtime import (
    PENTEST_EXECUTOR_DISABLED,
    PentestSecureRunComposition,
)
from host_brain.secure_ingress import SecureIngressMode
from host_brain.skills.signed_intake_composition import (
    SignedSkillProviderIntakeComposition,
)
from host_brain.skills.signed_intake_ingress import (
    SecureSignedSkillProviderIntake,
)
from host_brain.skills.supply_chain import SupplyChainActivationMode


class _Ledger:
    def __init__(self, canonical_db_path: str | Path) -> None:
        self.canonical_db_path = str(Path(canonical_db_path).resolve())


class _CloseStore:
    def __init__(self, events: list[str], event: str) -> None:
        self._events = events
        self._event = event

    def close(self) -> None:
        self._events.append(self._event)


class _ObsIngress:
    def __init__(self, events: list[str], *, in_flight: int = 0) -> None:
        self._events = events
        self.in_flight = in_flight

    def close_admission(self) -> None:
        self._events.append("obs-admission-close")

    async def close_admission_and_drain(self) -> None:
        self.close_admission()
        self._events.append("obs-drain")


def _obs_composition(
    events: list[str],
    *,
    in_flight: int = 0,
    enabled: bool = True,
) -> ObsCommandComposition:
    return ObsCommandComposition(
        enabled=enabled,
        execution_enabled=enabled,
        effective_mode=(
            SecureIngressMode.ENFORCE if enabled else SecureIngressMode.OFF
        ),
        relocation_mode=ObsCommandRelocationMode.QUERY_ONLY,
        global_mode=SecureIngressMode.ENFORCE,
        reason_code=(
            "OBS_COMMAND_COMPOSITION_ENFORCE"
            if enabled
            else "OBS_COMMAND_GLOBAL_INGRESS_DISABLED"
        ),
        ingress=_ObsIngress(events, in_flight=in_flight),  # type: ignore[arg-type]
        registry=ObsCommandPortRegistry(),
        store=_CloseStore(events, "obs-store-close"),  # type: ignore[arg-type]
    )


def _pentest_composition(
    events: list[str],
    *,
    enabled: bool = True,
) -> PentestSecureRunComposition:
    return PentestSecureRunComposition(
        enabled=enabled,
        execution_enabled=False,
        reason_code=(
            "PENTEST_PRODUCTION_EXECUTION_DISABLED"
            if enabled
            else "PENTEST_SECURE_RUN_LAB_ENFORCED_REQUIRED"
        ),
        executor_mode=PENTEST_EXECUTOR_DISABLED,
        ingress_store=_CloseStore(  # type: ignore[arg-type]
            events,
            "pentest-close",
        ),
    )


def _signed_intake_composition(
    events: list[str],
    *,
    enabled: bool = True,
) -> SignedSkillProviderIntakeComposition:
    return SignedSkillProviderIntakeComposition(
        enabled=enabled,
        reason_code=(
            "SKILL_PROVIDER_INTAKE_COMPOSITION_ENFORCE"
            if enabled
            else "SKILL_PROVIDER_INTAKE_COMPOSITION_DISABLED"
        ),
        activation_mode=SupplyChainActivationMode.QUARANTINE,
        secure_ingress_mode=SecureIngressMode.ENFORCE,
        ingress=SecureSignedSkillProviderIntake(
            ingress_adapter=None,
            ingress_service=None,
            claim_store=None,
            supply_chain_service=None,
            source_resolver=None,
        ),
        secure_store=_CloseStore(  # type: ignore[arg-type]
            events,
            "signed-intake-close",
        ),
    )


def _build_enabled_container(
    tmp_path: Path,
    events: list[str],
    *,
    obs: ObsCommandComposition | None = None,
) -> ProcessSecurityServiceContainer:
    db_path = tmp_path / "canonical.sqlite3"
    return build_process_security_service_container(
        ledger=_Ledger(db_path),
        canonical_db_path=db_path,
        token_service=TokenService(),
        env={"CONFIG_SNAPSHOT": "aw01"},
        obs_builder=lambda **_kwargs: obs or _obs_composition(events),
        pentest_builder=lambda **_kwargs: _pentest_composition(events),
        signed_intake_builder=lambda **_kwargs: (
            _signed_intake_composition(events)
        ),
    )


def test_build_uses_explicit_authority_shared_tokens_and_one_env_snapshot(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    calls: list[tuple[str, str, int, Any | None]] = []
    db_path = tmp_path / "canonical.sqlite3"
    ledger = _Ledger(db_path)
    tokens = TokenService()

    def obs_builder(*, env: Any, db_path: str) -> ObsCommandComposition:
        calls.append(("obs", db_path, id(env), None))
        return _obs_composition(events)

    def pentest_builder(
        *,
        token_service: TokenService,
        env: Any,
        db_path: str,
    ) -> PentestSecureRunComposition:
        calls.append(("pentest", db_path, id(env), token_service))
        return _pentest_composition(events)

    def intake_builder(
        *, env: Any, db_path: str
    ) -> SignedSkillProviderIntakeComposition:
        calls.append(("signed", db_path, id(env), None))
        return _signed_intake_composition(events)

    container = build_process_security_service_container(
        ledger=ledger,
        canonical_db_path=db_path,
        token_service=tokens,
        env={"CONFIG_SNAPSHOT": "aw01"},
        obs_builder=obs_builder,
        pentest_builder=pentest_builder,
        signed_intake_builder=intake_builder,
    )

    canonical = str(db_path.resolve())
    assert [call[0] for call in calls] == ["obs", "pentest", "signed"]
    assert {call[1] for call in calls} == {canonical}
    assert len({call[2] for call in calls}) == 1
    assert calls[1][3] is tokens
    assert container.ledger is ledger
    assert container.canonical_db_path == canonical
    assert container.token_service is tokens
    assert container.enabled_services == frozenset(SecurityServiceName)
    assert container.construction_order == tuple(SecurityServiceName)
    assert all(status.constructed for status in container.statuses.values())

    container.close()


def test_partial_construction_failure_is_stable_and_does_not_block_later_slots(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    built: list[str] = []
    db_path = tmp_path / "canonical.sqlite3"

    def fail_obs(**_kwargs: Any) -> ObsCommandComposition:
        built.append("obs")
        raise OSError("unstable operating-system detail")

    def build_pentest(**_kwargs: Any) -> PentestSecureRunComposition:
        built.append("pentest")
        return _pentest_composition(events)

    def build_intake(**_kwargs: Any) -> SignedSkillProviderIntakeComposition:
        built.append("signed")
        return _signed_intake_composition(events)

    container = build_process_security_service_container(
        ledger=_Ledger(db_path),
        canonical_db_path=db_path,
        token_service=TokenService(),
        env={},
        obs_builder=fail_obs,
        pentest_builder=build_pentest,
        signed_intake_builder=build_intake,
    )

    assert built == ["obs", "pentest", "signed"]
    assert container.obs_command_composition is None
    assert container.pentest_secure_run_composition is not None
    assert container.signed_skill_provider_intake_composition is not None
    assert container.status(SecurityServiceName.OBS_COMMAND).reason_code == (
        OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED
    )
    assert container.enabled_services == {
        SecurityServiceName.PENTEST_SECURE_RUN,
        SecurityServiceName.SIGNED_SKILL_PROVIDER_INTAKE,
    }

    container.close()
    assert events == ["signed-intake-close", "pentest-close"]


def test_authority_mismatch_fails_all_slots_without_calling_builders(
    tmp_path: Path,
) -> None:
    called: list[str] = []

    def forbidden(**_kwargs: Any) -> Any:
        called.append("called")
        raise AssertionError("builder must not run")

    container = build_process_security_service_container(
        ledger=_Ledger(tmp_path / "ledger.sqlite3"),
        canonical_db_path=tmp_path / "different.sqlite3",
        token_service=TokenService(),
        env={},
        obs_builder=forbidden,
        pentest_builder=forbidden,
        signed_intake_builder=forbidden,
    )

    assert called == []
    assert container.enabled_services == frozenset()
    assert container.construction_order == ()
    assert {
        status.reason_code for status in container.statuses.values()
    } == {SECURITY_SERVICE_CANONICAL_DB_PATH_MISMATCH}
    container.close()
    assert container.closed


def test_async_close_is_reverse_order_and_obs_admission_precedes_drain(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    container = _build_enabled_container(tmp_path, events)

    asyncio.run(container.aclose())

    assert events == [
        "signed-intake-close",
        "pentest-close",
        "obs-admission-close",
        "obs-drain",
        "obs-admission-close",
        "obs-store-close",
    ]
    assert container.closed
    asyncio.run(container.aclose())
    assert len(events) == 6


def test_sync_drain_failure_closes_other_slots_and_async_retry_finishes(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    obs = _obs_composition(events, in_flight=1)
    ingress = obs.ingress
    container = _build_enabled_container(tmp_path, events, obs=obs)

    with pytest.raises(SecurityServiceContainerCloseError) as caught:
        container.close()

    assert [failure.service for failure in caught.value.failures] == [
        SecurityServiceName.OBS_COMMAND
    ]
    assert not container.closed
    assert events == [
        "signed-intake-close",
        "pentest-close",
        "obs-admission-close",
    ]

    ingress.in_flight = 0
    asyncio.run(container.aclose())
    assert events[3:] == [
        "obs-admission-close",
        "obs-drain",
        "obs-admission-close",
        "obs-store-close",
    ]
    assert container.closed


def test_inherited_container_rejects_use_outside_owner_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = _build_enabled_container(tmp_path, [])
    monkeypatch.setattr(
        container_module.os,
        "getpid",
        lambda: container.owner_process_id + 1,
    )

    with pytest.raises(RuntimeError, match=SECURITY_SERVICE_PROCESS_SCOPE_VIOLATION):
        container.close()
