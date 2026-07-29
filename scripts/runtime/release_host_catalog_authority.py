#!/usr/bin/env python3
"""Release the exact Service Catalog lease of a confirmed-stopped local Host.

The PowerShell lifecycle owner invokes this only after it has verified that the
recorded Host PID and its listener are absent.  This utility never creates,
renews, or takes over a coordinator lease; it reopens the canonical row, checks
that its owner identity names that exact stopped PID, and releases the existing
fenced authority through the persistence port.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.persistence.embodiment_outbox_repository import (
    EmbodimentOutboxRepository,
)
from host_brain.persistence.service_catalog_rollout_repository import (
    SQLiteServiceCatalogRolloutRepository,
)


_OWNER_PATTERN = re.compile(r"^host\.main\.catalog::(?P<pid>[1-9][0-9]*)::[0-9a-f]{32}$")


def _scope_digest(runtime_scope_id: str) -> str:
    return "sha256:" + hashlib.sha256(runtime_scope_id.encode("utf-8")).hexdigest()


def release_confirmed_stopped_host_authority(
    *,
    db_path: Path,
    expected_host_pid: int,
) -> dict[str, object]:
    """Release only an active lease exactly owned by ``expected_host_pid``."""

    if expected_host_pid <= 0:
        raise ValueError("expected_host_pid must be positive")
    if not db_path.is_file():
        raise ValueError("catalog database does not exist")

    runtime_scope_id = EmbodimentOutboxRepository(db_path).runtime_scope_id
    repository = SQLiteServiceCatalogRolloutRepository(db_path)
    authority = repository.active_authority(runtime_scope_id=runtime_scope_id)
    base = {
        "runtime_scope_digest": _scope_digest(runtime_scope_id),
        "expected_host_pid": int(expected_host_pid),
    }
    if authority is None:
        return {"status": "NOT_ACTIVE", **base}

    owner_match = _OWNER_PATTERN.fullmatch(authority.owner_id)
    observed_pid = int(owner_match.group("pid")) if owner_match else None
    if observed_pid != int(expected_host_pid):
        return {
            "status": "OWNER_MISMATCH",
            **base,
            "observed_owner_pid": observed_pid,
            "fencing_token": int(authority.fencing_token),
        }

    if not repository.release_authority(authority):
        return {
            "status": "RELEASE_REJECTED",
            **base,
            "fencing_token": int(authority.fencing_token),
        }
    return {
        "status": "RELEASED",
        **base,
        "fencing_token": int(authority.fencing_token),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Release an exact Service Catalog lease after a local Host exits."
    )
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--expected-host-pid", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = release_confirmed_stopped_host_authority(
            db_path=args.db_path,
            expected_host_pid=int(args.expected_host_pid),
        )
    except ValueError as exc:
        print(json.dumps({"status": "INVALID", "reason": str(exc)}, ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"RELEASED", "NOT_ACTIVE"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
