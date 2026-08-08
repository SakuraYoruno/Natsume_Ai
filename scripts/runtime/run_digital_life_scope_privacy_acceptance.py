"""Exercise Digital Life affect scope, privacy and bounded replay on SQLite.

This acceptance uses the production AffectFusion/Life command/reducer/store
path with an isolated, fully migrated database.  It deliberately never writes
the supplied text/audio/image/video sentinels to its report.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Mapping, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from natsume_contracts.life import LifeCommand, LifeCommandStatus


ACCEPTANCE_SCHEMA_VERSION = "natsume.digital-life-scope-privacy-acceptance.v1"
MIGRATIONS = REPOSITORY_ROOT / "host_brain" / "audit" / "migrations"
_FORBIDDEN_AFFECT_KEYS = frozenset(
    {
        "assistant_reply",
        "audio",
        "audio_base64",
        "image",
        "image_base64",
        "model_chain",
        "raw_audio",
        "raw_image",
        "raw_text",
        "transcript",
        "user_message",
        "video",
    }
)


def _commit_sha(repository_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip().lower()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RuntimeError("ACCEPTANCE_COMMIT_SHA_INVALID")
    return value


def _migrate(db_path: Path, *, run_id: str, now_ms: int) -> tuple[str, int]:
    result = SQLiteMigrationRunner(
        db_path=db_path,
        migrations_dir=MIGRATIONS,
    ).apply_migrations(
        request_id=f"digital-life-scope-privacy::{run_id}",
        ts_ms=now_ms,
    )
    if not result.success:
        raise RuntimeError("ACCEPTANCE_MIGRATION_FAILED")
    summary = dict(result.summary or {})
    current = int(summary.get("current_version") or 0)
    latest = int(summary.get("latest_version") or 0)
    if current < 1 or current != latest:
        raise RuntimeError("ACCEPTANCE_MIGRATION_HEAD_NOT_CURRENT")
    prefix = f"{latest:04d}_"
    names = sorted(
        str(record.migration_id)
        for record in tuple(result.records or ())
        if str(record.migration_id).startswith(prefix)
    )
    if not names:
        raise RuntimeError("ACCEPTANCE_MIGRATION_HEAD_ASSET_MISSING")
    return names[-1] + ".sql", latest


def _request(
    *,
    message: str,
    workspace_id: str,
    person_id: str,
    session_id: str,
    observations: Sequence[Mapping[str, Any]] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        message=message,
        workspace_id=workspace_id,
        person_id=person_id,
        session_id=session_id,
        emotion_context={"user_affect_observations": list(observations)},
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
        mood_state=None,
        emotion_range_hint=None,
    )


def _forbidden_paths(value: Any, *, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key or "").strip().lower()
            child_path = f"{path}.{key or '<empty>'}"
            if key in _FORBIDDEN_AFFECT_KEYS:
                findings.append(child_path)
            findings.extend(_forbidden_paths(child, path=child_path))
    elif isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for index, child in enumerate(value):
            findings.extend(_forbidden_paths(child, path=f"{path}[{index}]"))
    return findings


def _scope_violations(
    payloads: Sequence[Mapping[str, Any]],
    *,
    workspace_id: str,
    relationship_subject_id: str,
    session_id: str,
) -> list[str]:
    violations: list[str] = []
    for index, payload in enumerate(payloads):
        state_after = dict(payload.get("state_after") or {})
        identity = dict(state_after.get("identity") or {})
        if identity.get("relationship_subject_id") != relationship_subject_id:
            violations.append(f"event_{index}:identity_subject")

        command_payload = dict(payload.get("command_payload") or {})
        candidates = [
            dict(command_payload.get("observation") or {}),
            dict(command_payload.get("appraisal") or {}),
            dict(dict(state_after.get("affective") or {}).get("last_observation") or {}),
            dict(dict(state_after.get("affective") or {}).get("last_appraisal") or {}),
        ]
        for candidate_index, candidate in enumerate(candidates):
            if not candidate:
                continue
            if candidate.get("workspace_id") not in (None, "", workspace_id):
                violations.append(
                    f"event_{index}:candidate_{candidate_index}:workspace"
                )
            if candidate.get("relationship_subject_id") not in (
                None,
                "",
                relationship_subject_id,
            ):
                violations.append(
                    f"event_{index}:candidate_{candidate_index}:subject"
                )
            if candidate.get("session_id") not in (None, "", session_id):
                violations.append(
                    f"event_{index}:candidate_{candidate_index}:session"
                )
    return violations


def _read_affect_json(
    db_path: Path,
    *,
    aggregate_ids: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, Any]]:
    placeholders = ",".join("?" for _ in aggregate_ids)
    documents: list[dict[str, Any]] = []
    events_by_aggregate: dict[str, list[dict[str, Any]]] = {
        aggregate_id: [] for aggregate_id in aggregate_ids
    }
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute(
            f"""
            SELECT person_id, payload_json AS body
            FROM life_command
            WHERE person_id IN ({placeholders})
              AND command_type LIKE 'affective_%'
            """,
            tuple(aggregate_ids),
        ):
            documents.append(json.loads(str(row["body"])))
        for row in connection.execute(
            f"""
            SELECT person_id, event_payload_json AS body
            FROM life_event_journal
            WHERE person_id IN ({placeholders})
              AND event_type LIKE 'affective_%'
            ORDER BY person_id, sequence
            """,
            tuple(aggregate_ids),
        ):
            body = json.loads(str(row["body"]))
            documents.append(body)
            events_by_aggregate[str(row["person_id"])].append(body)
        for row in connection.execute(
            f"""
            SELECT person_id, state_json AS body
            FROM digital_life_state
            WHERE person_id IN ({placeholders})
            """,
            tuple(aggregate_ids),
        ):
            documents.append(json.loads(str(row["body"])))
        for row in connection.execute(
            f"""
            SELECT person_id, payload_json AS body
            FROM life_projection_intent
            WHERE person_id IN ({placeholders})
              AND json_extract(payload_json, '$.event_type') LIKE 'affective_%'
            """,
            tuple(aggregate_ids),
        ):
            documents.append(json.loads(str(row["body"])))

        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        metrics = dict(
            connection.execute(
                f"""
                SELECT
                    COUNT(*) AS event_count,
                    MAX(length(CAST(event_payload_json AS BLOB)))
                        AS max_event_payload_bytes
                FROM life_event_journal
                WHERE person_id IN ({placeholders})
                  AND event_type LIKE 'affective_%'
                """,
                tuple(aggregate_ids),
            ).fetchone()
        )
        state_metrics = dict(
            connection.execute(
                f"""
                SELECT
                    COUNT(*) AS state_count,
                    MAX(length(CAST(state_json AS BLOB))) AS max_state_bytes
                FROM digital_life_state
                WHERE person_id IN ({placeholders})
                """,
                tuple(aggregate_ids),
            ).fetchone()
        )
        projection_count = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM affective_life_event_v1
                WHERE person_id IN ({placeholders})
                """,
                tuple(aggregate_ids),
            ).fetchone()[0]
        )
    return documents, events_by_aggregate, {
        "integrity_check": integrity,
        "event_count": int(metrics.get("event_count") or 0),
        "max_event_payload_bytes": int(metrics.get("max_event_payload_bytes") or 0),
        "state_count": int(state_metrics.get("state_count") or 0),
        "max_state_bytes": int(state_metrics.get("max_state_bytes") or 0),
        "affective_projection_count": projection_count,
    }


def run_acceptance(
    *,
    repository_root: Path,
    run_id: str,
    event_count: int = 1_001,
) -> dict[str, Any]:
    sequence_count = int(event_count)
    if not 1 <= sequence_count <= 5_000:
        raise ValueError("event_count must be between 1 and 5000")
    started = time.monotonic()
    now_ms = int(time.time() * 1000)
    clock = [now_ms]
    run_digest = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:20]
    raw_sentinels = (
        f"private-user-text-{run_digest}",
        f"private-audio-{run_digest}",
        f"private-image-{run_digest}",
        f"private-video-{run_digest}",
    )

    with tempfile.TemporaryDirectory(prefix="natsume-digital-life-scope-") as raw:
        root = Path(raw).resolve()
        db_path = root / "digital-life.sqlite3"
        migration_head, migration_head_version = _migrate(
            db_path,
            run_id=run_id,
            now_ms=now_ms,
        )
        repository = LifeCommandRepository(db_path)
        service = LifeCommandService(repository, clock=lambda: clock[0])
        reader = LifeSnapshotReader(repository)
        fusion = AffectFusionService(
            command_service_provider=lambda: service,
            snapshot_reader_provider=lambda: reader,
            clock_ms=lambda: clock[0],
        )

        scope_specs = (
            ("workspace-a", "person-a", "session-a"),
            ("workspace-b", "person-a", "session-a"),
            ("workspace-a", "person-a", "session-b"),
            ("workspace-a", "person-b", "session-a"),
        )
        primary = fusion.resolve_request(
            request_body=_request(
                message=raw_sentinels[0],
                workspace_id=scope_specs[0][0],
                person_id=scope_specs[0][1],
                session_id=scope_specs[0][2],
                observations=(
                    {
                        "modality": "ACOUSTIC",
                        "emotion_tag": "ANXIOUS",
                        "confidence": 0.74,
                        "raw_audio": raw_sentinels[1],
                    },
                    {
                        "modality": "VISUAL",
                        "emotion_tag": "SAD",
                        "confidence": 0.69,
                        "raw_image": raw_sentinels[2],
                        "video": raw_sentinels[3],
                    },
                    {
                        "modality": "VISUAL",
                        "emotion_tag": "SAD",
                        "confidence": 0.71,
                        "expires_at_ms": now_ms + 99_999,
                    },
                ),
            ),
            request_id=f"{run_digest}:scope-primary",
        )
        if "RAW_MODALITY_OMITTED" not in primary.reason_codes:
            raise RuntimeError("ACCEPTANCE_RAW_MODALITY_WAS_NOT_OMITTED")
        visual = next(
            item for item in primary.observations if item.modality == "VISUAL"
        )
        if visual.expires_at_ms - visual.observed_at_ms != 3_000:
            raise RuntimeError("ACCEPTANCE_VISUAL_TTL_NOT_BOUNDED")

        current = reader.get_snapshot(primary.scope.aggregate_id)
        invalid_command_id = f"affect_raw_rejected_{run_digest}"
        invalid = LifeCommand(
            command_id=invalid_command_id,
            person_id=primary.scope.aggregate_id,
            command_type="affective_observation_recorded",
            payload={
                "observation": primary.observations[0].as_dict(),
                "raw_audio": raw_sentinels[1],
            },
            expected_version=current.version,
            created_at_ms=clock[0],
        )
        try:
            service.submit(invalid)
        except ValueError:
            raw_command_rejected = True
        else:
            raise RuntimeError("ACCEPTANCE_RAW_COMMAND_WAS_ACCEPTED")
        if reader.get_receipt(invalid_command_id) is not None:
            raise RuntimeError("ACCEPTANCE_REJECTED_RAW_COMMAND_LEFT_RECEIPT")

        other_results = (
            fusion.resolve_request(
                request_body=_request(
                    message="今天真的很开心。",
                    workspace_id=scope_specs[1][0],
                    person_id=scope_specs[1][1],
                    session_id=scope_specs[1][2],
                ),
                request_id=f"{run_digest}:scope-workspace-b",
            ),
            fusion.resolve_request(
                request_body=_request(
                    message="我真的很难受。",
                    workspace_id=scope_specs[2][0],
                    person_id=scope_specs[2][1],
                    session_id=scope_specs[2][2],
                ),
                request_id=f"{run_digest}:scope-session-b",
            ),
            fusion.resolve_request(
                request_body=_request(
                    message="先别问了，我想一个人待会儿。",
                    workspace_id=scope_specs[3][0],
                    person_id=scope_specs[3][1],
                    session_id=scope_specs[3][2],
                ),
                request_id=f"{run_digest}:scope-person-b",
            ),
        )
        results = (primary, *other_results)
        aggregate_ids = tuple(result.scope.aggregate_id for result in results)
        if len(set(aggregate_ids)) != len(scope_specs):
            raise RuntimeError("ACCEPTANCE_SCOPE_AGGREGATE_COLLISION")
        other_before = {
            result.scope.aggregate_id: reader.get_snapshot(
                result.scope.aggregate_id
            ).state_digest
            for result in other_results
        }

        for index, message in enumerate(
            (
                "你真恶心。",
                "别人比你好。",
                "先别问了，我想一个人待会儿。",
                "你会吃醋吗？",
            ),
            start=1,
        ):
            clock[0] += 1_000
            fusion.resolve_request(
                request_body=_request(
                    message=message,
                    workspace_id=scope_specs[0][0],
                    person_id=scope_specs[0][1],
                    session_id=scope_specs[0][2],
                ),
                request_id=f"{run_digest}:episode-{index}",
            )
        before_sequence = reader.get_snapshot(primary.scope.aggregate_id)
        before_affective = dict(before_sequence.state.get("affective") or {})
        if len(before_affective.get("active_episodes") or ()) != 4:
            raise RuntimeError("ACCEPTANCE_EPISODE_HEAD_NOT_FULL_AND_BOUNDED")
        before_state_bytes = len(
            json.dumps(
                before_sequence.state,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

        version = before_sequence.version
        for index in range(sequence_count):
            clock[0] += 60_000
            receipt = service.submit(
                LifeCommand(
                    command_id=f"affect_soak_{run_digest}_{index:05d}",
                    person_id=primary.scope.aggregate_id,
                    command_type="affective_time_advanced",
                    payload={},
                    expected_version=version,
                    created_at_ms=clock[0],
                )
            )
            if receipt.status is not LifeCommandStatus.APPLIED:
                raise RuntimeError("ACCEPTANCE_TIME_ADVANCE_NOT_APPLIED")
            version = receipt.state_version

        final = reader.get_snapshot(primary.scope.aggregate_id)
        final_affective = dict(final.state.get("affective") or {})
        episodes = list(final_affective.get("active_episodes") or [])
        components = list(final_affective.get("components") or [])
        max_evidence_refs = max(
            (len(dict(item).get("evidence_refs") or ()) for item in episodes),
            default=0,
        )
        final_state_bytes = len(
            json.dumps(
                final.state,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if (
            len(episodes) > 4
            or len(components) > 8
            or max_evidence_refs > 16
            or final_state_bytes > 65_536
        ):
            raise RuntimeError("ACCEPTANCE_AFFECTIVE_STATE_UNBOUNDED")

        restarted_repository = LifeCommandRepository(db_path)
        restarted = LifeCommandService(
            restarted_repository,
            clock=lambda: clock[0],
        )
        replayed = restarted.replay(primary.scope.aggregate_id)
        if replayed.state_digest != final.state_digest or replayed.as_dict() != final.as_dict():
            raise RuntimeError("ACCEPTANCE_RESTART_REPLAY_DRIFT")

        other_after = {
            aggregate_id: reader.get_snapshot(aggregate_id).state_digest
            for aggregate_id in other_before
        }
        changed_other_scopes = sorted(
            aggregate_id
            for aggregate_id, digest in other_after.items()
            if digest != other_before[aggregate_id]
        )
        if changed_other_scopes:
            raise RuntimeError("ACCEPTANCE_PRIMARY_SEQUENCE_MUTATED_OTHER_SCOPE")

        documents, events_by_aggregate, durable = _read_affect_json(
            db_path,
            aggregate_ids=aggregate_ids,
        )
        encoded_documents = json.dumps(
            documents,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        sentinel_occurrences = sum(
            encoded_documents.count(sentinel) for sentinel in raw_sentinels
        )
        forbidden_paths = [
            path
            for document in documents
            for path in _forbidden_paths(document)
        ]
        scope_violations: list[str] = []
        for result, spec in zip(results, scope_specs, strict=True):
            scope_violations.extend(
                _scope_violations(
                    events_by_aggregate[result.scope.aggregate_id],
                    workspace_id=spec[0],
                    relationship_subject_id=spec[1],
                    session_id=spec[2],
                )
            )
        if sentinel_occurrences or forbidden_paths or scope_violations:
            raise RuntimeError("ACCEPTANCE_AFFECT_JOURNAL_PRIVACY_OR_SCOPE_VIOLATION")
        if durable["integrity_check"] != "ok":
            raise RuntimeError("ACCEPTANCE_SQLITE_INTEGRITY_FAILED")
        if durable["max_event_payload_bytes"] > 262_144:
            raise RuntimeError("ACCEPTANCE_EVENT_PAYLOAD_UNBOUNDED")

        receipt = {
            "schema_version": ACCEPTANCE_SCHEMA_VERSION,
            "run_id": str(run_id).strip(),
            "commit_sha": _commit_sha(repository_root),
            "database_kind": "ISOLATED_REAL_SQLITE",
            "migration_head": migration_head,
            "migration_head_version": migration_head_version,
            "scope_isolation": {
                "status": "PASS",
                "scope_count": len(scope_specs),
                "distinct_aggregate_count": len(set(aggregate_ids)),
                "workspace_count": len({item[0] for item in scope_specs}),
                "relationship_subject_count": len({item[1] for item in scope_specs}),
                "session_count": len({item[2] for item in scope_specs}),
                "scope_violation_count": len(scope_violations),
                "other_scope_digest_changes_after_primary_sequence": len(
                    changed_other_scopes
                ),
            },
            "privacy": {
                "status": "PASS",
                "raw_modality_omitted": True,
                "direct_raw_command_rejected": raw_command_rejected,
                "visual_ttl_ms": visual.expires_at_ms - visual.observed_at_ms,
                "forbidden_key_occurrences": len(forbidden_paths),
                "private_sentinel_occurrences": sentinel_occurrences,
                "raw_audio_video_image_persisted": False,
                "user_text_persisted_in_affect_journal": False,
            },
            "bounded_replay": {
                "status": "PASS",
                "event_sequence_count": sequence_count,
                "simulated_event_time_ms": sequence_count * 60_000,
                "active_episode_count_before_sequence": len(
                    before_affective.get("active_episodes") or ()
                ),
                "active_episode_count_after_sequence": len(episodes),
                "component_count_after_sequence": len(components),
                "max_episode_evidence_refs": max_evidence_refs,
                "snapshot_bytes_before_sequence": before_state_bytes,
                "snapshot_bytes_after_sequence": final_state_bytes,
                "restart_digest_equal": replayed.state_digest == final.state_digest,
                "restart_snapshot_equal": replayed.as_dict() == final.as_dict(),
            },
            "durable_state": {
                "status": "PASS",
                **durable,
                "database_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
            },
            "duration_ms": round((time.monotonic() - started) * 1_000),
            "external_side_effects": 0,
            "private_or_user_data_used": False,
            "temporary_database_removed": True,
            "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
            "status": "PASS_REAL_SQLITE_SCOPE_PRIVACY_BOUNDED_REPLAY",
        }
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Digital Life scope/privacy/bounded-replay acceptance",
    )
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event-count", type=int, default=1_001)
    parser.add_argument("--receipt", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = run_acceptance(
        repository_root=args.repository_root.resolve(),
        run_id=args.run_id,
        event_count=args.event_count,
    )
    encoded = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
    if args.receipt is not None:
        target = args.receipt.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
