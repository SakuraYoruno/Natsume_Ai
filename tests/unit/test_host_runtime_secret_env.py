from __future__ import annotations

import os
from pathlib import Path

import pytest

from host_brain.runtime_env import (
    HOST_RUNTIME_ENV_FILE,
    HOST_RUNTIME_SECRET_ENV_FILE,
    RuntimeEnvironmentError,
    load_host_runtime_environment,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_detached_host_loads_separate_secret_file_last(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    public = _write(
        tmp_path / "runtime" / "host" / "host.runtime.env",
        "HOST_APP_PORT=28080\nHOST_EDGE_GATEWAY_HMAC_SECRET=public-must-not-win\n",
    )
    secret = _write(
        tmp_path / "acceptance.secrets.env",
        "HOST_EDGE_GATEWAY_HMAC_SECRET=ephemeral-value\n",
    )
    _write(tmp_path / ".env", "HOST_APP_PORT=18080\n")
    monkeypatch.setenv(HOST_RUNTIME_ENV_FILE, str(public))
    monkeypatch.setenv(HOST_RUNTIME_SECRET_ENV_FILE, str(secret))

    load_host_runtime_environment(repo_root=tmp_path)

    assert os.environ["HOST_APP_PORT"] == "28080"
    assert os.environ["HOST_EDGE_GATEWAY_HMAC_SECRET"] == "ephemeral-value"
    assert os.environ[HOST_RUNTIME_SECRET_ENV_FILE] == str(secret.resolve())


def test_public_runtime_file_cannot_redirect_secret_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    trusted = _write(tmp_path / "trusted.env", "TEST_RUNTIME_SECRET=trusted\n")
    untrusted = _write(tmp_path / "untrusted.env", "TEST_RUNTIME_SECRET=wrong\n")
    public = _write(
        tmp_path / "public.env",
        f"{HOST_RUNTIME_SECRET_ENV_FILE}={untrusted}\n",
    )
    monkeypatch.setenv(HOST_RUNTIME_ENV_FILE, str(public))
    monkeypatch.setenv(HOST_RUNTIME_SECRET_ENV_FILE, str(trusted))

    load_host_runtime_environment(repo_root=tmp_path)

    assert os.environ["TEST_RUNTIME_SECRET"] == "trusted"
    assert os.environ[HOST_RUNTIME_SECRET_ENV_FILE] == str(trusted.resolve())


def test_explicit_missing_secret_file_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.env"
    monkeypatch.setenv(HOST_RUNTIME_SECRET_ENV_FILE, str(missing))

    with pytest.raises(
        RuntimeEnvironmentError,
        match="HOST_RUNTIME_ENV_FILE_NOT_FOUND",
    ):
        load_host_runtime_environment(repo_root=tmp_path)


@pytest.mark.parametrize("public_name", (".env", "runtime.env"))
def test_secret_file_must_be_separate_from_public_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    public_name: str,
) -> None:
    public = _write(tmp_path / public_name, "HOST_APP_PORT=28080\n")
    if public_name == "runtime.env":
        monkeypatch.setenv(HOST_RUNTIME_ENV_FILE, str(public))
    monkeypatch.setenv(HOST_RUNTIME_SECRET_ENV_FILE, str(public))

    with pytest.raises(
        RuntimeEnvironmentError,
        match="HOST_RUNTIME_SECRET_ENV_FILE_MUST_BE_SEPARATE",
    ):
        load_host_runtime_environment(repo_root=tmp_path)
