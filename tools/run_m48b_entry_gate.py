"""Validate the M48-B Entry Gate packet and write a deterministic result."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.release.m48b_entry_gate import (  # noqa: E402
    M48B_ENTRY_GATE_RESULT_SCHEMA_VERSION,
    evaluate_m48b_entry_gate,
    load_m48b_entry_gate_packet,
)


EXIT_OPEN = 0
EXIT_BLOCKED = 1


def _blocked(reason_code: str, detail: str) -> dict[str, Any]:
    return {
        "schema_version": M48B_ENTRY_GATE_RESULT_SCHEMA_VERSION,
        "milestone": "M48-B",
        "status": "BLOCKED",
        "master_approval_required": True,
        "clause_status": {str(number): "FAILED" for number in range(1, 6)},
        "issues": [{"reason_code": reason_code, "detail": detail}],
        "checked_ownership_scopes": 0,
        "checked_migrations": 0,
        "checked_cutover_surfaces": 0,
        "checked_isolation_domains": 0,
        "checked_p0_slices": 0,
    }


def _atomic_write(path: Path, payload: Mapping[str, Any], *, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ) + "\n"
    temporary: Path | None = None
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
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_entry_gate(
    *,
    root: str | Path,
    packet: str | Path,
    output: str | Path,
    pretty: bool = False,
) -> tuple[int, dict[str, Any]]:
    root_path = Path(root).resolve()
    packet_path = Path(packet)
    if not packet_path.is_absolute():
        packet_path = root_path / packet_path
    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = root_path / output_path
    try:
        payload = evaluate_m48b_entry_gate(
            root=root_path,
            packet=load_m48b_entry_gate_packet(packet_path),
        ).to_dict()
    except Exception as exc:  # fail closed at the CLI boundary
        payload = _blocked("M48B_ENTRY_GATE_RUNNER_EXCEPTION", str(exc))
    _atomic_write(output_path, payload, pretty=pretty)
    return (EXIT_OPEN if payload.get("status") == "OPEN" else EXIT_BLOCKED), payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument(
        "--packet",
        default="docs/30_devspec/evidence/m48b/M48B_ENTRY_GATE_PACKET.json",
    )
    parser.add_argument(
        "--output",
        default="docs/30_devspec/evidence/m48b/M48B_ENTRY_GATE_RESULT.json",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    exit_code, payload = run_entry_gate(
        root=args.root,
        packet=args.packet,
        output=args.output,
        pretty=args.pretty,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
