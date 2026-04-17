from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

_MISSING_MODULE_RE = re.compile(r"No module named [\'\"]([^\'\"]+)[\'\"]")


def _resolve_python_executable() -> str:
    candidates = [
        Path.cwd() / '.venv' / 'Scripts' / 'python.exe',
        Path(sys.executable),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _collect_missing_modules(exc: BaseException | str) -> list[str]:
    text = str(exc or '').strip()
    modules: list[str] = []
    for match in _MISSING_MODULE_RE.findall(text):
        name = str(match or '').strip()
        if name and name not in modules:
            modules.append(name)
    return modules


def _run_command(command: list[str], *, cwd: Path | None = None, timeout_s: int = 300) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=max(5, int(timeout_s)),
        check=False,
    )
    return {
        'command': list(command),
        'cwd': (str(cwd) if cwd is not None else None),
        'returncode': int(completed.returncode),
        'stdout': str(completed.stdout or '').strip(),
        'stderr': str(completed.stderr or '').strip(),
        'ok': completed.returncode == 0,
    }


def repair_skill_loading_failure(
    *,
    skill_dir: Path,
    exc: BaseException | str,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    python_exe = _resolve_python_executable()
    requirements_txt = skill_dir / 'requirements.txt'
    package_json = skill_dir / 'package.json'

    if requirements_txt.exists():
        actions.append(_run_command([python_exe, '-m', 'pip', 'install', '-r', str(requirements_txt)], cwd=skill_dir))

    missing_modules = _collect_missing_modules(exc)
    if missing_modules:
        for module_name in missing_modules:
            actions.append(_run_command([python_exe, '-m', 'pip', 'install', module_name], cwd=skill_dir))

    npm_cmd = shutil.which('npm.cmd') or shutil.which('npm')
    if package_json.exists() and npm_cmd:
        actions.append(_run_command([npm_cmd, 'install'], cwd=skill_dir))

    ok = any(action.get('ok') for action in actions)
    return {
        'success': ok,
        'actions': actions,
        'missing_modules': missing_modules,
        'requirements_used': requirements_txt.exists(),
        'package_json_used': package_json.exists(),
    }
