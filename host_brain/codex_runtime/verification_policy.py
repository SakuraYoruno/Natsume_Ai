"""Verification helpers for Codex runtime v2."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


class VerificationPolicy:
    def run(self, plan: dict[str, Any], *, workspace: Path, request_id: str | None, intent_id: str) -> dict[str, Any]:
        code = str(plan.get("verification_code") or "").strip()
        if code:
            from host_brain.airlock import SandboxExecutor  # noqa: PLC0415

            result = SandboxExecutor().execute(
                code,
                request_id=request_id,
                intent_id=intent_id,
                language="python",
            )
            return {
                "status": result.status,
                "reason_code": result.reason_code,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
                "evidence_ref": result.evidence_ref,
                "code_digest": result.code_digest,
            }

        commands = plan.get("commands")
        if isinstance(commands, list) and commands:
            rows: list[dict[str, Any]] = []
            overall = "PASS"
            for command in commands[:5]:
                if isinstance(command, str):
                    args = command.split()
                elif isinstance(command, list):
                    args = [str(part) for part in command if str(part).strip()]
                else:
                    continue
                if not args:
                    continue
                completed = subprocess.run(
                    args,
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=int(plan.get("timeout_seconds") or 120),
                    check=False,
                )
                if completed.returncode != 0:
                    overall = "FAIL"
                rows.append(
                    {
                        "command": args,
                        "exit_code": completed.returncode,
                        "stdout": str(completed.stdout or "")[:4000],
                        "stderr": str(completed.stderr or "")[:4000],
                    }
                )
            return {"status": overall if rows else "NOT_RUN", "commands": rows}

        return {"status": "NOT_RUN", "reason": "verification plan did not request executable checks"}


__all__ = ["VerificationPolicy"]
