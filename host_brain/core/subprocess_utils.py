"""静默 subprocess 工具——Windows 下绝不弹控制台窗口。"""

from __future__ import annotations

import os
import subprocess
from typing import Any

if os.name == "nt":
    _NO_WINDOW_FLAGS: int = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
else:
    _NO_WINDOW_FLAGS: int = 0


def run(
    args: list[str] | str,
    *,
    stdin: Any = None,
    input: str | bytes | None = None,
    stdout: Any = None,
    stderr: Any = None,
    capture_output: bool = False,
    timeout: float | None = None,
    check: bool = False,
    text: bool | str = False,
    encoding: str | None = None,
    errors: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """subprocess.run() 的静默替代——Windows 下自动加上 CREATE_NO_WINDOW。"""
    creationflags = kwargs.pop("creationflags", 0) | _NO_WINDOW_FLAGS
    return subprocess.run(
        args,
        stdin=stdin,
        input=input,  # type: ignore[arg-type]
        stdout=stdout,
        stderr=stderr,
        capture_output=capture_output,
        timeout=timeout,
        check=check,
        text=text,  # type: ignore[arg-type]
        encoding=encoding,
        errors=errors,
        env=env,
        cwd=cwd,
        creationflags=creationflags,
        **kwargs,
    )


def Popen(
    args: list[str] | str,
    *,
    stdin: Any = None,
    stdout: Any = None,
    stderr: Any = None,
    text: bool | str = False,
    encoding: str | None = None,
    errors: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    **kwargs: Any,
) -> subprocess.Popen[Any]:
    """subprocess.Popen() 的静默替代——Windows 下自动加上 CREATE_NO_WINDOW。"""
    creationflags = kwargs.pop("creationflags", 0) | _NO_WINDOW_FLAGS
    return subprocess.Popen(
        args,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        text=text,  # type: ignore[arg-type]
        encoding=encoding,
        errors=errors,
        env=env,
        cwd=cwd,
        creationflags=creationflags,
        **kwargs,
    )
