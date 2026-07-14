from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import host_brain.app.observability_runtime.command_composition as composition_module
from host_brain.secure_ingress import SecureIngressMode


TEST_HMAC_SECRET = "0123456789abcdef" * 2


class _FakeStore:
    instances: list["_FakeStore"] = []

    def __init__(self, path) -> None:  # noqa: ANN001
        self.path = path
        self.close_count = 0
        self.__class__.instances.append(self)

    def close(self) -> None:
        self.close_count += 1

    def append(self, _event) -> str:  # noqa: ANN001
        return "secure-ingress-audit://fake"


class _FakeShadowStateStore:
    instances: list["_FakeShadowStateStore"] = []

    def __init__(self, *, audit_sink) -> None:  # noqa: ANN001
        self.audit_sink = audit_sink
        self.__class__.instances.append(self)


class _FakeOutcomeJournal:
    instances: list["_FakeOutcomeJournal"] = []
    init_error: str | None = None
    recovery_error: str | None = None

    def __init__(self, path) -> None:  # noqa: ANN001
        self.path = Path(path).resolve()
        self.recover_count = 0
        self.__class__.instances.append(self)
        if self.__class__.init_error is not None:
            raise RuntimeError(self.__class__.init_error)

    def recover_incomplete(self) -> tuple[()]:
        self.recover_count += 1
        if self.__class__.recovery_error is not None:
            raise RuntimeError(self.__class__.recovery_error)
        return ()


class _FakeHmacVerifier:
    instances: list["_FakeHmacVerifier"] = []

    def __init__(self, *, secret_resolver) -> None:  # noqa: ANN001
        self.secret_resolver = secret_resolver
        self.__class__.instances.append(self)


class _FakeVerifierRegistry:
    instances: list["_FakeVerifierRegistry"] = []

    def __init__(self, verifiers) -> None:  # noqa: ANN001
        self.verifiers = dict(verifiers)
        self.__class__.instances.append(self)


class _FakeEmergencyAudit:
    instances: list["_FakeEmergencyAudit"] = []

    def __init__(self, path) -> None:  # noqa: ANN001
        self.path = path
        self.__class__.instances.append(self)


class _FakeService:
    instances: list["_FakeService"] = []

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.kwargs = dict(kwargs)
        self.__class__.instances.append(self)


class _FakeAdapter:
    instances: list["_FakeAdapter"] = []

    def __init__(self, *, service, mode) -> None:  # noqa: ANN001
        self.service = service
        self.mode = mode
        self.__class__.instances.append(self)


class _FakeRouter:
    def __init__(self, registry) -> None:  # noqa: ANN001
        self.registry = registry


class _FakeIngress:
    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.kwargs = dict(kwargs)
        self.in_flight = 0
        self.admission_closed = False
        self.drain_count = 0

    def close_admission(self) -> None:
        self.admission_closed = True

    async def close_admission_and_drain(self) -> None:
        self.admission_closed = True
        self.drain_count += 1
        self.in_flight = 0


def _install_fakes(monkeypatch):  # noqa: ANN001
    for cls in (
        _FakeStore,
        _FakeShadowStateStore,
        _FakeOutcomeJournal,
        _FakeHmacVerifier,
        _FakeVerifierRegistry,
        _FakeEmergencyAudit,
        _FakeService,
        _FakeAdapter,
    ):
        cls.instances.clear()
    registry = SimpleNamespace(name="domain-registry")
    monkeypatch.setattr(composition_module, "SQLiteSecureIngressStore", _FakeStore)
    monkeypatch.setattr(
        composition_module,
        "ShadowIngressStateStore",
        _FakeShadowStateStore,
    )
    monkeypatch.setattr(
        composition_module,
        "ObsCommandOutcomeJournal",
        _FakeOutcomeJournal,
    )
    _FakeOutcomeJournal.init_error = None
    _FakeOutcomeJournal.recovery_error = None
    monkeypatch.setattr(composition_module, "HmacSha256CredentialVerifier", _FakeHmacVerifier)
    monkeypatch.setattr(composition_module, "CredentialVerifierRegistry", _FakeVerifierRegistry)
    monkeypatch.setattr(composition_module, "FileEmergencyAuditSink", _FakeEmergencyAudit)
    monkeypatch.setattr(composition_module, "SecureIngressService", _FakeService)
    monkeypatch.setattr(composition_module, "SecureIngressAdapter", _FakeAdapter)
    monkeypatch.setattr(composition_module, "ObsCommandRouter", _FakeRouter)
    monkeypatch.setattr(composition_module, "SecureObsCommandIngress", _FakeIngress)
    monkeypatch.setattr(
        composition_module,
        "build_default_obs_domain_command_registry",
        lambda: registry,
    )
    return registry


def _env(relocation: str, global_mode: str) -> dict[str, str]:
    del relocation
    return {
        composition_module.HOST_SECURE_INGRESS_MODE_ENV: global_mode,
        composition_module.HOST_SECURE_INGRESS_HMAC_SECRETS_ENV: json.dumps({
            "obs-key": TEST_HMAC_SECRET,
        }),
        # These credentials must never be consulted by the OBS resolver.
        "HOST_EDGE_GATEWAY_HMAC_SECRET": "edge-secret",
        "EDGE_HMAC_SHARED_KEY": "bridge-secret",
        "HOST_GATEWAY_REPLY_HMAC_SECRET": "reply-secret",
    }


@pytest.mark.parametrize(
    ("relocation", "global_mode", "expected_mode", "execution_enabled"),
    (
        ("SHADOW", "SHADOW", SecureIngressMode.SHADOW, False),
        ("SHADOW", "ENFORCE", SecureIngressMode.ENFORCE, True),
        ("QUERY_ONLY", "ENFORCE", SecureIngressMode.ENFORCE, True),
    ),
)
def test_active_matrix_builds_one_central_store_and_explicit_mode(
    tmp_path,
    monkeypatch,
    relocation,
    global_mode,
    expected_mode,
    execution_enabled,
) -> None:
    registry = _install_fakes(monkeypatch)
    composition = composition_module.build_obs_command_composition(
        env=_env(relocation, global_mode),
        db_path=tmp_path / "audit.sqlite3",
        emergency_audit_path=tmp_path / "emergency.jsonl",
    )

    assert composition.enabled is True
    assert composition.execution_enabled is execution_enabled
    assert composition.effective_mode is expected_mode
    assert len(_FakeStore.instances) == 1
    assert len(_FakeOutcomeJournal.instances) == 1
    store = _FakeStore.instances[0]
    outcome_journal = _FakeOutcomeJournal.instances[0]
    service = _FakeService.instances[0]
    assert outcome_journal.path == (
        tmp_path / "audit.obs-command-outcomes.jsonl"
    ).resolve()
    assert outcome_journal.recover_count == 1
    assert service.kwargs["principal_store"] is store
    assert service.kwargs["attempt_limiter"] is store
    assert service.kwargs["rate_limiter"] is store
    if expected_mode is SecureIngressMode.SHADOW:
        assert len(_FakeShadowStateStore.instances) == 1
        shadow_state = _FakeShadowStateStore.instances[0]
        assert shadow_state.audit_sink is store
        assert service.kwargs["state_store"] is shadow_state
    else:
        assert _FakeShadowStateStore.instances == []
        assert service.kwargs["state_store"] is store
    assert composition.ingress.kwargs["claim_result_store"] is store
    assert composition.ingress.kwargs["outcome_journal"] is outcome_journal
    assert composition.outcome_journal is outcome_journal
    assert composition.registry is registry
    assert composition.router.registry is registry
    assert composition.adapter.mode is expected_mode

    composition.close()
    composition.close()
    assert store.close_count == 1
    assert composition.ingress.admission_closed is True


def test_explicit_outcome_journal_path_is_recovered_once_and_injected(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fakes(monkeypatch)
    journal_path = tmp_path / "wal" / "explicit.jsonl"

    composition = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "audit.sqlite3",
        outcome_journal_path=journal_path,
    )

    assert composition.enabled is True
    assert len(_FakeOutcomeJournal.instances) == 1
    journal = _FakeOutcomeJournal.instances[0]
    assert journal.path == journal_path.resolve()
    assert journal.recover_count == 1
    assert composition.outcome_journal is journal
    assert composition.ingress.kwargs["outcome_journal"] is journal
    composition.close()


def test_outcome_journal_path_can_be_owned_by_environment(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fakes(monkeypatch)
    journal_path = tmp_path / "operator-owned" / "outcomes.jsonl"
    env = _env("QUERY_ONLY", "ENFORCE")
    env[composition_module.HOST_OBS_COMMAND_OUTCOME_JOURNAL_PATH_ENV] = str(
        journal_path
    )

    composition = composition_module.build_obs_command_composition(
        env=env,
        db_path=tmp_path / "audit.sqlite3",
    )

    assert composition.enabled is True
    assert _FakeOutcomeJournal.instances[0].path == journal_path.resolve()
    composition.close()


def test_outcome_recovery_requires_exclusive_lifetime_owner(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fakes(monkeypatch)
    journal_path = tmp_path / "shared" / "outcomes.jsonl"

    first = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "first.sqlite3",
        outcome_journal_path=journal_path,
    )
    second = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "second.sqlite3",
        outcome_journal_path=journal_path,
    )

    assert first.enabled is True
    assert second.enabled is False
    assert (
        second.reason_code
        == composition_module.OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED
    )
    # The losing runtime must not recover records owned by the live runtime.
    assert _FakeOutcomeJournal.instances[0].recover_count == 1
    assert _FakeOutcomeJournal.instances[1].recover_count == 0
    assert _FakeStore.instances[1].close_count == 1

    first.close()
    third = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "third.sqlite3",
        outcome_journal_path=journal_path,
    )
    assert third.enabled is True
    assert _FakeOutcomeJournal.instances[2].recover_count == 1
    third.close()


def test_sync_close_keeps_owner_until_in_flight_commands_are_drained(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fakes(monkeypatch)
    journal_path = tmp_path / "drain" / "outcomes.jsonl"
    composition = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "audit.sqlite3",
        outcome_journal_path=journal_path,
    )
    composition.ingress.in_flight = 1

    with pytest.raises(RuntimeError, match="INGRESS_DRAIN_REQUIRED"):
        composition.close()

    assert composition.store.close_count == 0
    contender = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "contender.sqlite3",
        outcome_journal_path=journal_path,
    )
    assert contender.enabled is False
    assert (
        contender.reason_code
        == composition_module.OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED
    )

    asyncio.run(composition.aclose())
    assert composition.ingress.drain_count == 1
    assert composition.store.close_count == 1


@pytest.mark.parametrize(
    ("failure_stage", "reason_code"),
    (
        ("init", "OBS_COMMAND_OUTCOME_CORRUPT"),
        ("init", "OBS_COMMAND_OUTCOME_LOCK_UNAVAILABLE"),
        ("recover", "OBS_COMMAND_OUTCOME_RECOVERY_FAILED"),
    ),
)
def test_outcome_journal_failure_closes_store_and_returns_terminal_off(
    tmp_path,
    monkeypatch,
    failure_stage,
    reason_code,
) -> None:
    _install_fakes(monkeypatch)
    if failure_stage == "init":
        _FakeOutcomeJournal.init_error = reason_code
    else:
        _FakeOutcomeJournal.recovery_error = reason_code

    composition = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "audit.sqlite3",
        outcome_journal_path=tmp_path / "outcomes.jsonl",
    )

    assert composition.enabled is False
    assert composition.execution_enabled is False
    assert composition.effective_mode is SecureIngressMode.OFF
    assert (
        composition.reason_code
        == composition_module.OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED
    )
    assert composition.outcome_journal is None
    assert len(_FakeStore.instances) == 1
    assert _FakeStore.instances[0].close_count == 1
    assert len(_FakeOutcomeJournal.instances) == 1
    assert _FakeService.instances == []


def test_disabled_composition_creates_no_outcome_wal(tmp_path, monkeypatch) -> None:
    _install_fakes(monkeypatch)
    journal_path = tmp_path / "disabled-outcomes.jsonl"

    composition = composition_module.build_obs_command_composition(
        env=_env("RETIRED", "OFF"),
        db_path=tmp_path / "must-not-open.sqlite3",
        outcome_journal_path=journal_path,
    )

    assert composition.enabled is False
    assert composition.outcome_journal is None
    assert composition.ingress.kwargs["outcome_journal"] is None
    assert _FakeStore.instances == []
    assert _FakeOutcomeJournal.instances == []
    assert not journal_path.exists()
    assert not journal_path.with_suffix(".jsonl.lock").exists()


@pytest.mark.parametrize(
    ("retired_relocation", "global_mode"),
    (
        ("SHADOW", "OFF"),
        ("QUERY_ONLY", "invalid"),
    ),
)
def test_invalid_or_disabled_global_mode_is_off_without_store(
    monkeypatch,
    retired_relocation,
    global_mode,
) -> None:
    _install_fakes(monkeypatch)
    composition = composition_module.build_obs_command_composition(
        env=_env(retired_relocation, global_mode),
        db_path="must-not-open.sqlite3",
    )

    assert composition.enabled is False
    assert composition.execution_enabled is False
    assert composition.effective_mode is SecureIngressMode.OFF
    assert composition.router is None
    assert _FakeStore.instances == []
    assert _FakeOutcomeJournal.instances == []


@pytest.mark.parametrize(
    "secret_value",
    (
        None,
        "not-json",
        "{}",
        '{"obs-key":""}',
        json.dumps({"obs-key": "x" * 31}),
        json.dumps({"obs-key": " " * 32}),
        json.dumps({"obs-key": ("x" * 32) + "\n"}),
        json.dumps({"k" * 256: TEST_HMAC_SECRET}),
        json.dumps({"obs-key": "x" * 4_097}),
        (
            '{"obs-key":"'
            + TEST_HMAC_SECRET
            + '","obs-key":"'
            + ("z" * 32)
            + '"}'
        ),
        json.dumps({
            "obs-key": TEST_HMAC_SECRET,
            " obs-key ": "z" * 32,
        }),
    ),
)
def test_secret_configuration_failure_is_off_before_store(
    monkeypatch,
    secret_value,
) -> None:
    _install_fakes(monkeypatch)
    env = _env("QUERY_ONLY", "ENFORCE")
    if secret_value is None:
        env.pop(composition_module.HOST_SECURE_INGRESS_HMAC_SECRETS_ENV)
    else:
        env[composition_module.HOST_SECURE_INGRESS_HMAC_SECRETS_ENV] = secret_value

    composition = composition_module.build_obs_command_composition(env=env)

    assert composition.enabled is False
    assert composition.effective_mode is SecureIngressMode.OFF
    assert _FakeStore.instances == []
    assert _FakeOutcomeJournal.instances == []


def test_secret_mapping_provider_exception_is_sanitized_before_store(
    monkeypatch,
) -> None:
    _install_fakes(monkeypatch)
    private_detail = "private-secret-provider-detail-must-not-escape"

    class _ExplodingSecretMapping(dict):
        def get(self, key, default=None):  # noqa: ANN001
            if key == composition_module.HOST_SECURE_INGRESS_HMAC_SECRETS_ENV:
                raise RuntimeError(private_detail)
            return super().get(key, default)

    composition = composition_module.build_obs_command_composition(
        env=_ExplodingSecretMapping(_env("QUERY_ONLY", "ENFORCE")),
    )

    assert composition.enabled is False
    assert (
        composition.reason_code
        == composition_module.OBS_COMMAND_HMAC_SECRETS_INVALID
    )
    assert private_detail not in composition.reason_code
    assert _FakeStore.instances == []
    assert _FakeOutcomeJournal.instances == []


def test_hmac_resolver_uses_only_dedicated_json_mapping(tmp_path, monkeypatch) -> None:
    _install_fakes(monkeypatch)
    composition = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "fake-audit.sqlite3",
    )
    resolver = _FakeHmacVerifier.instances[0].secret_resolver

    assert resolver(SimpleNamespace(key_id="obs-key")) == TEST_HMAC_SECRET
    assert resolver(SimpleNamespace(key_id="edge.gateway.hmac.v1")) is None
    assert resolver(SimpleNamespace(key_id="bridge-key")) is None
    composition.close()


def test_construction_failure_closes_partial_store_and_returns_off(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fakes(monkeypatch)

    class _ExplodingService:
        def __init__(self, **_kwargs) -> None:  # noqa: ANN003
            raise RuntimeError("schema/service construction failed")

    monkeypatch.setattr(composition_module, "SecureIngressService", _ExplodingService)
    composition = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path=tmp_path / "fake-audit.sqlite3",
    )

    assert composition.enabled is False
    assert composition.effective_mode is SecureIngressMode.OFF
    assert (
        composition.reason_code
        == composition_module.OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED
    )
    assert len(_FakeStore.instances) == 1
    assert _FakeStore.instances[0].close_count == 1


def test_schema_store_failure_returns_off_without_legacy_path(monkeypatch) -> None:
    _install_fakes(monkeypatch)

    class _SchemaFailureStore:
        def __init__(self, _path) -> None:  # noqa: ANN001
            raise RuntimeError("SECURE_INGRESS_MIGRATION_DRIFT")

    monkeypatch.setattr(
        composition_module,
        "SQLiteSecureIngressStore",
        _SchemaFailureStore,
    )
    composition = composition_module.build_obs_command_composition(
        env=_env("QUERY_ONLY", "ENFORCE"),
        db_path="fake-audit.sqlite3",
    )

    assert composition.enabled is False
    assert composition.execution_enabled is False
    assert (
        composition.reason_code
        == composition_module.OBS_COMMAND_COMPOSITION_CONSTRUCTION_FAILED
    )
