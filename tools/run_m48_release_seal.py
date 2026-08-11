"""Evaluate an M48 release-seal bundle and write its result atomically.

This runner is deliberately unable to issue final release approval.  Its only
successful state is ``READY_FOR_MASTER_SEAL``; MASTER remains responsible for
the separate ``SEALED`` decision.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.release.m48_seal import (  # noqa: E402
    M48_SEAL_RESULT_SCHEMA_VERSION,
    M48SealIssue,
    M48SealResult,
    M48SealStatus,
    evaluate_m48_release_seal,
)


EXIT_READY = 0
EXIT_NOT_READY = 1
RUNNER_EXCEPTION_REASON = "M48_SEAL_RUNNER_EXCEPTION"
RUNNER_RESULT_INVALID_REASON = "M48_SEAL_RUNNER_RESULT_INVALID"
RUNNER_OUTPUT_WRITE_FAILED_REASON = "M48_SEAL_RUNNER_OUTPUT_WRITE_FAILED"

_ALLOWED_STATUSES = frozenset(status.value for status in M48SealStatus)


def _fail_closed_payload(reason_code: str, detail: str) -> dict[str, Any]:
    return M48SealResult(
        schema_version=M48_SEAL_RESULT_SCHEMA_VERSION,
        release_id=None,
        milestone=None,
        status=M48SealStatus.BLOCKED,
        master_approval_required=True,
        issues=(M48SealIssue(reason_code=reason_code, detail=detail),),
        checked_artifacts=(),
        verified_evidence_ids=(),
        verified_rollback_ids=(),
    ).to_dict()


def _contains_final_approval(value: Any) -> bool:
    if value == "SEALED":
        return True
    if isinstance(value, Mapping):
        return any(_contains_final_approval(key) or _contains_final_approval(item) for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_final_approval(item) for item in value)
    return False


def _validated_result_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("seal evaluator returned a non-object result")
    payload = dict(value)
    if payload.get("schema_version") != M48_SEAL_RESULT_SCHEMA_VERSION:
        raise ValueError("seal evaluator returned an unsupported result schema")
    if payload.get("status") not in _ALLOWED_STATUSES:
        raise ValueError("seal evaluator returned an unsupported status")
    if payload.get("master_approval_required") is not True:
        raise ValueError("seal evaluator removed the MASTER approval gate")
    if _contains_final_approval(payload):
        raise ValueError("seal evaluator attempted to issue final approval")
    return payload


def _json_text(payload: Mapping[str, Any], *, pretty: bool) -> str:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return json.dumps(dict(payload), **options) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _output_path(*, root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def run_release_seal(
    *,
    root: str | Path,
    manifest: str | Path,
    output: str | Path,
    pretty: bool = False,
) -> tuple[int, dict[str, Any]]:
    """Run the fail-closed seal evaluation and persist one JSON result."""

    root_path = Path(root).resolve()
    try:
        result = evaluate_m48_release_seal(root=root_path, manifest=manifest)
        payload = _validated_result_payload(result.to_dict())
    except Exception as exc:  # The CLI boundary must convert all evaluator failures.
        payload = _fail_closed_payload(
            RUNNER_EXCEPTION_REASON,
            f"seal evaluation raised {type(exc).__name__}",
        )

    output_path = _output_path(root=root_path, value=output)
    try:
        _atomic_write_text(output_path, _json_text(payload, pretty=pretty))
    except Exception as exc:
        payload = _fail_closed_payload(
            RUNNER_OUTPUT_WRITE_FAILED_REASON,
            f"seal result write raised {type(exc).__name__}",
        )
        return EXIT_NOT_READY, payload

    exit_code = EXIT_READY if payload["status"] == M48SealStatus.READY_FOR_MASTER_SEAL.value else EXIT_NOT_READY
    return exit_code, payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate an M48 release bundle without issuing final MASTER approval."
    )
    parser.add_argument("--root", required=True, help="Repository root containing the seal artifacts.")
    parser.add_argument(
        "--manifest",
        required=True,
        help="Manifest JSON path; relative paths are resolved below --root.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Result JSON path; relative paths are resolved below --root.",
    )
    parser.add_argument("--pretty", action="store_true", help="Write indented deterministic JSON.")
    args = parser.parse_args(argv)

    exit_code, payload = run_release_seal(
        root=args.root,
        manifest=args.manifest,
        output=args.output,
        pretty=args.pretty,
    )
    sys.stdout.write(_json_text(payload, pretty=args.pretty))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
