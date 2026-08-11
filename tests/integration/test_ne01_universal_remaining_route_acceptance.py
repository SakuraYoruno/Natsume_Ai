from __future__ import annotations

import sqlite3
import time

import pytest

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.persistence.universal_route_acceptance_reader import (
    UniversalRouteAcceptanceReader,
)
from host_brain.release.universal_route_acceptance import (
    build_universal_route_acceptance_receipt,
)
from tools.acceptance.universal_remaining_routes import (
    append_remaining_routes,
)


@pytest.mark.asyncio
async def test_scheduler_internal_and_operator_append_real_durable_terminals(
    tmp_path,
) -> None:
    database = tmp_path / "universal-remaining-routes.sqlite3"
    migrated = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id="ne01-universal-remaining-routes-test",
    )
    assert migrated.success, migrated.as_dict()

    request_ids = await append_remaining_routes(
        database=database,
        evidence_root=tmp_path / "evidence",
    )

    assert set(request_ids) == {"scheduler", "internal", "operator"}
    snapshot = UniversalRouteAcceptanceReader(database).capture(request_ids)
    applied = snapshot["migration"]
    receipt = build_universal_route_acceptance_receipt(
        source_binding={
            "commit_sha": "b" * 40,
            "migration_head": (
                f"{int(applied['migration_id']):04d}_{applied['name']}.sql"
            ),
            "repository_clean": True,
        },
        snapshot=snapshot,
        request_ids=request_ids,
        generated_at_ms=int(time.time() * 1_000),
    )
    assert {
        route: record["status"]
        for route, record in receipt["route_acceptance"].items()
    } == {
        "chat": "NOT_RUN",
        "skill": "NOT_RUN",
        "plan": "NOT_RUN",
        "delegate": "NOT_RUN",
        "scheduler": "PASS",
        "internal": "PASS",
        "operator": "PASS",
    }
    assert receipt["overall_status"] == "INCOMPLETE"

    with sqlite3.connect(database) as connection:
        claims = connection.execute(
            """
            SELECT request_id, claim_status, result_ref
            FROM secure_ingress_idempotency_claim
            WHERE request_id IN (?, ?, ?)
            ORDER BY request_id
            """,
            tuple(sorted(request_ids.values())),
        ).fetchall()
    assert len(claims) == 3
    assert all(status == "COMMITTED" and str(result_ref or "") for _, status, result_ref in claims)
