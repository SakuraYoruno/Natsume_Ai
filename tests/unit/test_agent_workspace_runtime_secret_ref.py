from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from host_brain.agent_runtime.workspace_runtime import (
    WORKSPACE_RUNTIME_BINDING_INVALID,
    DurableWorkspaceRuntimeError,
)
from host_brain.app.settings.core import CoreSettings
from host_brain.composition import agent_workspace


def _runtime_settings(*, direct: str = "", secret_ref: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        agent_workspace_hmac_secret=SecretStr(direct),
        agent_workspace_hmac_secret_ref=secret_ref,
    )


def test_workspace_secret_ref_resolves_only_from_ignored_runtime_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "workspace-secret-with-more-than-thirty-two-bytes"
    secret_ref = "sec://workspace/ne07-test"
    store = tmp_path / "secret_store.runtime.local.json"
    store.write_text(
        json.dumps({secret_ref: secret}),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_workspace, "AGENT_WORKSPACE_SECRET_STORE", store)

    resolved = agent_workspace.resolve_agent_workspace_token_secret(
        _runtime_settings(secret_ref=secret_ref)
    )

    assert resolved == secret.encode("utf-8")


@pytest.mark.parametrize(
    ("secret_ref", "store_payload"),
    (
        ("", {}),
        ("not-a-secret-ref", {}),
        ("sec://workspace/missing", {}),
    ),
)
def test_workspace_secret_ref_fails_closed_without_leaking_store_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    secret_ref: str,
    store_payload: dict[str, str],
) -> None:
    store = tmp_path / "secret_store.runtime.local.json"
    store.write_text(
        json.dumps(
            {
                **store_payload,
                "sec://unrelated": "must-never-appear-in-an-error-message",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_workspace, "AGENT_WORKSPACE_SECRET_STORE", store)

    with pytest.raises(DurableWorkspaceRuntimeError) as caught:
        agent_workspace.resolve_agent_workspace_token_secret(
            _runtime_settings(secret_ref=secret_ref)
        )

    assert caught.value.reason_code == WORKSPACE_RUNTIME_BINDING_INVALID
    assert "must-never-appear" not in str(caught.value)


def test_workspace_secret_source_is_unambiguous_and_direct_input_stays_compatible(
) -> None:
    direct = "d" * 48
    assert agent_workspace.resolve_agent_workspace_token_secret(
        _runtime_settings(direct=direct)
    ) == direct.encode("utf-8")

    with pytest.raises(ValidationError, match="SOURCE_AMBIGUOUS"):
        CoreSettings(
            _env_file=None,
            HOST_AGENT_WORKSPACE_HMAC_SECRET=direct,
            HOST_AGENT_WORKSPACE_HMAC_SECRET_REF="sec://workspace/ne07-test",
        )

    with pytest.raises(ValidationError, match="SECRET_REF_INVALID"):
        CoreSettings(
            _env_file=None,
            HOST_AGENT_WORKSPACE_HMAC_SECRET_REF="plain-text-is-not-a-ref",
        )
