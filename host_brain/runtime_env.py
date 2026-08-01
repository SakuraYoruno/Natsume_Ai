"""Bootstrap environment loading for long-lived Host processes.

The public runtime snapshot intentionally excludes credentials.  A detached
Windows process therefore needs an explicit, separate path for short-lived
runtime secrets; only the launcher-provided path is trusted for that purpose.
"""

from __future__ import annotations

import os
from pathlib import Path


HOST_RUNTIME_ENV_FILE = "HOST_RUNTIME_ENV_FILE"
HOST_RUNTIME_SECRET_ENV_FILE = "HOST_RUNTIME_SECRET_ENV_FILE"


class RuntimeEnvironmentError(RuntimeError):
    """Raised when an explicitly configured runtime environment is unsafe."""


def _resolve_path(raw_path: str, *, repo_root: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve(strict=False)


def load_env_file(
    env_path: Path,
    *,
    override: bool = False,
    required: bool = False,
) -> None:
    """Load a simple KEY=VALUE file without ever returning or logging values."""

    if not env_path.is_file():
        if required:
            raise RuntimeEnvironmentError(
                f"HOST_RUNTIME_ENV_FILE_NOT_FOUND: {env_path}"
            )
        return
    # Windows PowerShell 5.1 writes ``-Encoding UTF8`` with a BOM.  ``utf-8``
    # would retain that marker in the first variable name and silently ignore
    # the setting; ``utf-8-sig`` accepts both BOM and BOM-less snapshots.
    with env_path.open(encoding="utf-8-sig") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if override or key not in os.environ:
                os.environ[key] = value


def load_host_runtime_environment(*, repo_root: Path) -> None:
    """Load base, public runtime, then launcher-selected secret environment.

    ``HOST_RUNTIME_SECRET_ENV_FILE`` is captured before either checked-in or
    public runtime files are read.  Those files therefore cannot redirect the
    Host to an attacker-selected credential file.
    """

    resolved_root = repo_root.resolve(strict=False)
    launcher_secret_raw = str(
        os.environ.get(HOST_RUNTIME_SECRET_ENV_FILE) or ""
    ).strip()

    load_env_file(resolved_root / ".env", override=False)

    runtime_env_raw = str(os.environ.get(HOST_RUNTIME_ENV_FILE) or "").strip()
    runtime_env_path = _resolve_path(
        runtime_env_raw or "runtime/host/host.runtime.env",
        repo_root=resolved_root,
    )
    load_env_file(runtime_env_path, override=True)

    if not launcher_secret_raw:
        os.environ.pop(HOST_RUNTIME_SECRET_ENV_FILE, None)
        return

    secret_env_path = _resolve_path(
        launcher_secret_raw,
        repo_root=resolved_root,
    )
    if secret_env_path == runtime_env_path or secret_env_path == (
        resolved_root / ".env"
    ).resolve(strict=False):
        raise RuntimeEnvironmentError(
            "HOST_RUNTIME_SECRET_ENV_FILE_MUST_BE_SEPARATE"
        )

    os.environ[HOST_RUNTIME_SECRET_ENV_FILE] = str(secret_env_path)
    load_env_file(secret_env_path, override=True, required=True)
    os.environ[HOST_RUNTIME_SECRET_ENV_FILE] = str(secret_env_path)


__all__ = [
    "HOST_RUNTIME_ENV_FILE",
    "HOST_RUNTIME_SECRET_ENV_FILE",
    "RuntimeEnvironmentError",
    "load_env_file",
    "load_host_runtime_environment",
]
