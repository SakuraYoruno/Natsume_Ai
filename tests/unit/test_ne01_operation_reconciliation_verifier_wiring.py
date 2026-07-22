from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from host_brain.app.settings.core import (
    CoreSettings,
    parse_task_reconciliation_public_keys,
)
from host_brain.composition.task_runtime import (
    _build_task_store_with_reconciliation_authority,
)
from host_brain.persistence.task_repository import (
    Ed25519ReconciliationProofVerifier,
)


def _public_key_config(*key_ids: str) -> tuple[str, dict[str, Ed25519PrivateKey]]:
    authorities = {key_id: Ed25519PrivateKey.generate() for key_id in key_ids}
    encoded = {
        key_id: "ed25519:"
        + base64.urlsafe_b64encode(
            private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        )
        .decode("ascii")
        .rstrip("=")
        for key_id, private_key in authorities.items()
    }
    return json.dumps(encoded, separators=(",", ":")), authorities


def test_empty_keyring_is_valid_only_while_reconciliation_operators_are_off() -> None:
    settings = CoreSettings.model_validate({})

    assert settings.task_reconciliation_public_keys() == {}
    assert settings.task_graph_cancellation_operator_enabled is False
    assert settings.task_operation_reconciliation_operator_enabled is False
    assert settings.task_effect_reconciliation_operator_enabled is False
    assert settings.task_terminal_reconciliation_operator_enabled is False
    assert settings.task_terminal_reconciliation_auto_enabled is True

    for enabled_flag in (
        "HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED",
        "HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED",
        "HOST_TASK_EFFECT_RECONCILIATION_OPERATOR_ENABLED",
        "HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED",
    ):
        with pytest.raises(
            ValueError,
            match="TASK_RECONCILIATION_OPERATOR_REQUIRES_PUBLIC_KEYS",
        ):
            CoreSettings.model_validate({enabled_flag: True})

    automatic = CoreSettings.model_validate(
        {"HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED": True}
    )
    assert automatic.task_terminal_reconciliation_auto_enabled is True
    assert automatic.task_reconciliation_public_keys() == {}

    rollback = CoreSettings.model_validate(
        {"HOST_TASK_TERMINAL_RECONCILIATION_AUTO_ENABLED": False}
    )
    assert rollback.task_terminal_reconciliation_auto_enabled is False


def test_cancellation_operator_requires_operation_reconciliation_before_startup() -> None:
    raw_keyring, _ = _public_key_config("reconciler-v1")

    with pytest.raises(
        ValueError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_OPERATION_RECONCILIATION",
    ):
        CoreSettings.model_validate(
            {
                "HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED": True,
                "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON": raw_keyring,
            }
        )


def test_cancellation_operator_requires_effect_reconciliation_before_startup() -> None:
    raw_keyring, _ = _public_key_config("reconciler-v1")

    with pytest.raises(
        ValueError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_EFFECT_RECONCILIATION",
    ):
        CoreSettings.model_validate(
            {
                "HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED": True,
                "HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED": True,
                "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON": raw_keyring,
            }
        )


def test_cancellation_operator_requires_terminal_reconciliation_before_startup() -> None:
    raw_keyring, _ = _public_key_config("reconciler-v1")

    with pytest.raises(
        ValueError,
        match="TASK_GRAPH_CANCELLATION_OPERATOR_REQUIRES_TERMINAL_RECONCILIATION",
    ):
        CoreSettings.model_validate(
            {
                "HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED": True,
                "HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED": True,
                "HOST_TASK_EFFECT_RECONCILIATION_OPERATOR_ENABLED": True,
                "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON": raw_keyring,
            }
        )


def test_cancellation_operator_accepts_complete_reconciliation_bundle() -> None:
    raw_keyring, _ = _public_key_config("reconciler-v1")

    settings = CoreSettings.model_validate(
        {
            "HOST_TASK_GRAPH_CANCELLATION_OPERATOR_ENABLED": True,
            "HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_EFFECT_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON": raw_keyring,
        }
    )

    assert settings.task_graph_cancellation_operator_enabled is True
    assert settings.task_operation_reconciliation_operator_enabled is True
    assert settings.task_effect_reconciliation_operator_enabled is True
    assert settings.task_terminal_reconciliation_operator_enabled is True


def test_terminal_reconciliation_operator_accepts_shared_ed25519_keyring() -> None:
    raw_keyring, _ = _public_key_config("terminal-reconciler-v1")

    settings = CoreSettings.model_validate(
        {
            "HOST_TASK_TERMINAL_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON": raw_keyring,
        }
    )

    assert settings.task_terminal_reconciliation_operator_enabled is True
    assert tuple(settings.task_reconciliation_public_keys()) == (
        "terminal-reconciler-v1",
    )


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        '{"reconciler-v1":"not-an-ed25519-key"}',
        '{"bad key":"ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}',
        '{"reconciler-v1":"ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}',
        (
            '{"reconciler-v1":"ed25519:'
            'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",'
            '"reconciler-v1":"ed25519:'
            'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}'
        ),
    ],
)
def test_public_keyring_parser_rejects_ambiguous_or_noncanonical_input(raw: str) -> None:
    with pytest.raises(ValueError, match="TASK_RECONCILIATION_PUBLIC_KEYS_INVALID"):
        parse_task_reconciliation_public_keys(raw)


def test_rotation_keyring_is_strictly_parsed_and_injected_into_task_store() -> None:
    raw_keyring, authorities = _public_key_config("reconciler-v1", "reconciler-v2")
    settings = CoreSettings.model_validate(
        {
            "HOST_TASK_OPERATION_RECONCILIATION_OPERATOR_ENABLED": True,
            "HOST_TASK_RECONCILIATION_ED25519_PUBLIC_KEYS_JSON": raw_keyring,
        }
    )
    captured: dict[str, Any] = {}
    sentinel_store = object()

    def build_store(db_path: str, **kwargs: Any) -> Any:
        captured["db_path"] = db_path
        captured.update(kwargs)
        return sentinel_store

    result = _build_task_store_with_reconciliation_authority(
        canonical_db_path="C:/canonical/task-runtime.sqlite3",
        task_store_builder=build_store,
        reconciliation_public_keys=settings.task_reconciliation_public_keys(),
    )

    assert result is sentinel_store
    assert captured["db_path"] == "C:/canonical/task-runtime.sqlite3"
    verifier = captured["reconciliation_proof_verifier"]
    assert isinstance(verifier, Ed25519ReconciliationProofVerifier)
    message = b"operation-reconciliation-authority-binding"
    for key_id, private_key in authorities.items():
        signature = "ed25519:" + base64.urlsafe_b64encode(
            private_key.sign(message)
        ).decode("ascii")
        assert verifier.has_key(key_id) is True
        assert verifier.verify(
            key_id=key_id,
            message=message,
            signature=signature,
        ) is True
    assert verifier.has_key("retired-key") is False


def test_keyring_rejects_aliases_for_the_same_public_key_material() -> None:
    raw_keyring, _ = _public_key_config("reconciler-v1")
    encoded = json.loads(raw_keyring)["reconciler-v1"]

    with pytest.raises(
        ValueError,
        match="TASK_RECONCILIATION_PUBLIC_KEYS_INVALID",
    ):
        parse_task_reconciliation_public_keys(
            json.dumps(
                {
                    "reconciler-v1": encoded,
                    "reconciler-alias": encoded,
                }
            )
        )


def test_verifier_rejects_noncanonical_signature_pad_bits() -> None:
    raw_keyring, authorities = _public_key_config("reconciler-v1")
    verifier = Ed25519ReconciliationProofVerifier(
        parse_task_reconciliation_public_keys(raw_keyring)
    )
    message = b"canonical-signature-text"
    canonical = base64.urlsafe_b64encode(
        authorities["reconciler-v1"].sign(message)
    ).decode("ascii")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last_index = alphabet.index(canonical[-3])
    noncanonical_index = (last_index & 0b110000) | ((last_index + 1) & 0b001111)
    noncanonical = canonical[:-3] + alphabet[noncanonical_index] + "=="
    assert noncanonical != canonical
    assert base64.urlsafe_b64decode(noncanonical) == base64.urlsafe_b64decode(canonical)

    assert verifier.verify(
        key_id="reconciler-v1",
        message=message,
        signature="ed25519:" + canonical,
    )
    assert not verifier.verify(
        key_id="reconciler-v1",
        message=message,
        signature="ed25519:" + noncanonical,
    )


def test_empty_keyring_preserves_default_store_builder_contract() -> None:
    captured: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    sentinel_store = object()

    def build_store(*args: Any, **kwargs: Any) -> Any:
        captured.append((args, kwargs))
        return sentinel_store

    result = _build_task_store_with_reconciliation_authority(
        canonical_db_path="C:/canonical/task-runtime.sqlite3",
        task_store_builder=build_store,
        reconciliation_public_keys=(
            CoreSettings.model_validate({}).task_reconciliation_public_keys()
        ),
    )

    assert result is sentinel_store
    assert captured == [(("C:/canonical/task-runtime.sqlite3",), {})]
