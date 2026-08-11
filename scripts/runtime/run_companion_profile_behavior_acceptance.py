"""Verify durable companion-profile canary, activation and exact rollback.

The runner uses the production relationship/profile/fusion/planner/delivery
path over an isolated, fully migrated SQLite database.  It proves that one
profile changes the next text-generation policy and Host-side voice/avatar
protocol, then that rollback restores the exact predecessor.  It does not
claim a model-generated sentence or a physical Edge effect.
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
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.digital_life.affect_fusion import AffectFusionService
from host_brain.digital_life.affective_expression_delivery import (
    build_sentence_expression_delivery,
)
from host_brain.digital_life.affective_turn_planner import (
    build_affective_turn_guidance,
)
from host_brain.digital_life.companion_profile_lifecycle import (
    CompanionProfileLifecycleService,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.persistence.life_command_repository import LifeCommandRepository
from natsume_contracts.life import CompanionProfilePreferencesV1


ACCEPTANCE_SCHEMA_VERSION = "natsume.companion-profile-behavior-acceptance.v1"
MIGRATIONS = REPOSITORY_ROOT / "host_brain" / "audit" / "migrations"
DAY_MS = 86_400_000


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
        request_id=f"companion-profile-behavior::{run_id}",
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


def _request(*, session_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_id="workspace-profile-acceptance",
        person_id="person-profile-acceptance",
        session_id=session_id,
        emotion_context={},
        message="就照平常这样说话吧。",
        emotion_tag=None,
        emotion_intensity=None,
        multimodal_observations={},
        mood_state=None,
        emotion_range_hint=None,
    )


def _accepted_response(*, ordinal: int, created_at_ms: int) -> dict[str, object]:
    return {
        "status": "APPLIED",
        "event_id": f"profile-acceptance-response-{ordinal}",
        "command_id": f"profile-acceptance-response-command-{ordinal}",
        "created_at_ms": created_at_ms,
    }


def _strong_aftereffect(
    *,
    relationship: RelationshipLifecycleService,
    clock: list[int],
    ordinal: int,
) -> str:
    session_id = f"profile-acceptance-aftereffect-{ordinal}"
    response = _accepted_response(ordinal=ordinal, created_at_ms=clock[0])
    relationship.provision_accepted_response(
        workspace_id="workspace-profile-acceptance",
        relationship_subject_id="person-profile-acceptance",
        session_id=session_id,
        request_id=f"profile-acceptance-provision-{ordinal}",
        response_receipt=response,
        response_text="我在，慢一点也没关系。",
    )
    clock[0] += 1_000
    result = relationship.record_explicit_response_feedback(
        workspace_id="workspace-profile-acceptance",
        relationship_subject_id="person-profile-acceptance",
        session_id=session_id,
        request_id=f"profile-acceptance-feedback-{ordinal}",
        response_receipt_ref=f"life-event://{response['event_id']}",
        feedback="HELPFUL",
        source_ref=f"secure-ingress-audit://profile-acceptance-{ordinal}",
    )
    aftereffect = result.aftereffect
    if (
        aftereffect is None
        or aftereffect.outcome != "HELPFUL"
        or aftereffect.helpfulness < 0.85
        or not aftereffect.boundary_respected
    ):
        raise RuntimeError("ACCEPTANCE_AFTEREFFECT_NOT_ELIGIBLE")
    return aftereffect.aftereffect_id


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _plan_snapshot(
    *,
    fusion: AffectFusionService,
    clock: list[int],
    session_id: str,
    request_id: str,
) -> dict[str, Any]:
    fused = fusion.resolve_request(
        request_body=_request(session_id=session_id),
        request_id=request_id,
    )
    guidance = build_affective_turn_guidance(
        emotion_context=fused.as_emotion_context(),
        request_id=request_id,
        workspace_id="workspace-profile-acceptance",
        relationship_subject_id="person-profile-acceptance",
        session_id=session_id,
        created_at_ms=clock[0],
    )
    if guidance is None:
        raise RuntimeError("ACCEPTANCE_AFFECTIVE_TURN_GUIDANCE_MISSING")
    plan = guidance.plan
    intents, receipts = build_sentence_expression_delivery(
        affective_turn_plan=plan.as_dict(),
        sentence="那我们就照这个节奏说。",
        sentence_index=0,
        created_at_ms=clock[0],
    )
    voice = next(item for item in intents if item.channel == "VOICE")
    avatar = next(item for item in intents if item.channel == "AVATAR")
    if any(item.synchronization_id != plan.expression_plan.synchronization_id for item in intents):
        raise RuntimeError("ACCEPTANCE_EXPRESSION_SYNCHRONIZATION_DRIFT")
    if any(item.status != "HOST_PROTOCOL_STAGED" for item in receipts):
        raise RuntimeError("ACCEPTANCE_EXPRESSION_HOST_PROTOCOL_NOT_STAGED")
    style_anchors = tuple(guidance.style_anchor)
    return {
        "profile_id": plan.companion_profile_id,
        "profile_digest": plan.companion_profile_digest,
        "profile_revision": plan.companion_profile_revision,
        "profile_stage": plan.companion_profile_stage,
        "text_generation_policy": {
            "style_anchor_count": len(style_anchors),
            "style_anchor_digest": _digest(style_anchors),
            "no_followup_anchor": any(
                "不要追加追问" in item for item in style_anchors
            ),
        },
        "voice": {
            "rate": plan.expression_plan.voice_rate,
            "profile_id": str(voice.payload.get("companion_profile_id") or ""),
            "profile_digest": str(
                voice.payload.get("companion_profile_digest") or ""
            ),
            "synchronization_id": voice.synchronization_id,
        },
        "avatar": {
            "motion_intensity": plan.expression_plan.avatar_motion_intensity,
            "profile_id": str(avatar.payload.get("companion_profile_id") or ""),
            "profile_digest": str(
                avatar.payload.get("companion_profile_digest") or ""
            ),
            "synchronization_id": avatar.synchronization_id,
        },
        "synchronization_id": plan.expression_plan.synchronization_id,
    }


def _profile_metrics(db_path: Path, *, aggregate_id: str) -> dict[str, Any]:
    with closing(sqlite3.connect(db_path)) as connection:
        connection.row_factory = sqlite3.Row
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        rows = list(
            connection.execute(
                """
                SELECT profile_kind, profile_id, created_at_ms, event_id
                FROM companion_profile_life_event_v1
                WHERE person_id = ?
                ORDER BY created_at_ms, event_id
                """,
                (aggregate_id,),
            )
        )
        kinds = [str(row["profile_kind"]) for row in rows]
        connection.create_function(
            "natsume_sha256_hex",
            1,
            lambda value: hashlib.sha256(
                value
                if isinstance(value, bytes)
                else str(value).encode("utf-8")
            ).hexdigest(),
            deterministic=True,
        )
        try:
            connection.execute(
                "UPDATE companion_profile_life_event_v1 "
                "SET profile_kind = 'ACTIVATED' WHERE person_id = ?",
                (aggregate_id,),
            )
        except sqlite3.IntegrityError:
            immutable_guard_rejected_update = True
        else:
            raise RuntimeError("ACCEPTANCE_PROFILE_PROJECTION_WAS_MUTABLE")
    return {
        "integrity_check": integrity,
        "event_count": len(rows),
        "event_kinds": kinds,
        "immutable_guard_rejected_update": immutable_guard_rejected_update,
    }


def run_acceptance(
    *,
    repository_root: Path,
    run_id: str,
) -> dict[str, Any]:
    started = time.monotonic()
    now_ms = int(time.time() * 1_000)
    clock = [now_ms]
    with tempfile.TemporaryDirectory(prefix="natsume-companion-profile-") as raw:
        root = Path(raw).resolve()
        db_path = root / "companion-profile.sqlite3"
        migration_head, migration_head_version = _migrate(
            db_path,
            run_id=run_id,
            now_ms=now_ms,
        )
        repository = LifeCommandRepository(db_path)
        command_service = LifeCommandService(repository, clock=lambda: clock[0])
        reader = LifeSnapshotReader(repository)
        profile_holder: dict[str, CompanionProfileLifecycleService] = {}
        relationship = RelationshipLifecycleService(
            command_service_provider=lambda: command_service,
            snapshot_reader_provider=lambda: reader,
            companion_profile_lifecycle_service_provider=lambda: profile_holder.get(
                "service"
            ),
            clock_ms=lambda: clock[0],
        )
        profile = CompanionProfileLifecycleService(
            command_service_provider=lambda: command_service,
            snapshot_reader_provider=lambda: reader,
            clock_ms=lambda: clock[0],
        )
        profile_holder["service"] = profile
        fusion = AffectFusionService(
            command_service_provider=lambda: command_service,
            snapshot_reader_provider=lambda: reader,
            relationship_lifecycle_service_provider=lambda: relationship,
            clock_ms=lambda: clock[0],
        )

        evidence_ids: list[str] = []
        for ordinal in range(5):
            evidence_ids.append(
                _strong_aftereffect(
                    relationship=relationship,
                    clock=clock,
                    ordinal=ordinal,
                )
            )
            clock[0] += DAY_MS

        scope = RelationshipScope.from_values(
            workspace_id="workspace-profile-acceptance",
            relationship_subject_id="person-profile-acceptance",
            session_id="profile-acceptance-scope",
        )
        baseline_state = reader.get_snapshot(scope.aggregate_id)
        baseline = _plan_snapshot(
            fusion=fusion,
            clock=clock,
            session_id="profile-acceptance-baseline",
            request_id="profile-acceptance-baseline",
        )

        canary_started = profile.start_canary(
            workspace_id="workspace-profile-acceptance",
            relationship_subject_id="person-profile-acceptance",
            session_id="profile-acceptance-canary",
            request_id="profile-acceptance-canary-start",
            preferences=CompanionProfilePreferencesV1(
                addressing_style="GENTLE_DIRECT",
                response_length="COMPACT",
                humor_level="LOW",
                followup_rate="NONE",
                comfort_mode="QUIET_FIRST",
                proactive_window="EVENING",
                tts_rate_scale=1.15,
                avatar_motion_intensity=0.8,
            ),
            evidence_aftereffect_ids=evidence_ids,
        )
        candidate = canary_started.state.canary_profile
        if candidate is None:
            raise RuntimeError("ACCEPTANCE_COMPANION_PROFILE_CANARY_MISSING")
        canary = _plan_snapshot(
            fusion=fusion,
            clock=clock,
            session_id="profile-acceptance-canary-turn",
            request_id="profile-acceptance-canary-turn",
        )

        for ordinal in range(5, 10):
            clock[0] += DAY_MS
            _strong_aftereffect(
                relationship=relationship,
                clock=clock,
                ordinal=ordinal,
            )
        active_state = reader.get_snapshot(scope.aggregate_id)
        active = _plan_snapshot(
            fusion=fusion,
            clock=clock,
            session_id="profile-acceptance-active-turn",
            request_id="profile-acceptance-active-turn",
        )

        rolled_back = profile.rollback_active(
            workspace_id="workspace-profile-acceptance",
            relationship_subject_id="person-profile-acceptance",
            session_id="profile-acceptance-rollback",
            request_id="profile-acceptance-rollback",
            expected_active_profile_id=candidate.profile_id,
        )
        rollback_state = reader.get_snapshot(scope.aggregate_id)
        rollback = _plan_snapshot(
            fusion=fusion,
            clock=clock,
            session_id="profile-acceptance-rollback-turn",
            request_id="profile-acceptance-rollback-turn",
        )

        if baseline["profile_stage"] != "BASELINE":
            raise RuntimeError("ACCEPTANCE_BASELINE_PROFILE_STAGE_INVALID")
        if canary["profile_stage"] != "CANARY":
            raise RuntimeError("ACCEPTANCE_CANARY_PROFILE_STAGE_INVALID")
        if active["profile_stage"] != "ACTIVE":
            raise RuntimeError("ACCEPTANCE_ACTIVE_PROFILE_STAGE_INVALID")
        if rollback["profile_stage"] != "BASELINE":
            raise RuntimeError("ACCEPTANCE_ROLLBACK_PROFILE_STAGE_INVALID")
        if (
            canary["profile_id"] != candidate.profile_id
            or active["profile_id"] != candidate.profile_id
        ):
            raise RuntimeError("ACCEPTANCE_CANDIDATE_PROFILE_IDENTITY_DRIFT")
        if (
            rollback["profile_id"] != baseline["profile_id"]
            or rollback["profile_digest"] != baseline["profile_digest"]
        ):
            raise RuntimeError("ACCEPTANCE_ROLLBACK_PREDECESSOR_DRIFT")

        changed = {
            "text_generation_policy": (
                canary["text_generation_policy"]["style_anchor_digest"]
                != baseline["text_generation_policy"]["style_anchor_digest"]
            ),
            "voice_rate": canary["voice"]["rate"] != baseline["voice"]["rate"],
            "avatar_motion": (
                canary["avatar"]["motion_intensity"]
                != baseline["avatar"]["motion_intensity"]
            ),
        }
        restored = {
            "text_generation_policy": (
                rollback["text_generation_policy"]["style_anchor_digest"]
                == baseline["text_generation_policy"]["style_anchor_digest"]
            ),
            "voice_rate": rollback["voice"]["rate"] == baseline["voice"]["rate"],
            "avatar_motion": (
                rollback["avatar"]["motion_intensity"]
                == baseline["avatar"]["motion_intensity"]
            ),
        }
        if not all(changed.values()) or not all(restored.values()):
            raise RuntimeError("ACCEPTANCE_PROFILE_BEHAVIOR_CHANGE_OR_ROLLBACK_FAILED")
        if not canary["text_generation_policy"]["no_followup_anchor"]:
            raise RuntimeError("ACCEPTANCE_PROFILE_TEXT_POLICY_NOT_APPLIED")
        if canary["voice"]["profile_id"] != candidate.profile_id:
            raise RuntimeError("ACCEPTANCE_PROFILE_VOICE_BINDING_DRIFT")
        if canary["avatar"]["profile_id"] != candidate.profile_id:
            raise RuntimeError("ACCEPTANCE_PROFILE_AVATAR_BINDING_DRIFT")

        durable = reader.get_snapshot(scope.aggregate_id)
        replayed = LifeCommandService(
            LifeCommandRepository(db_path),
            clock=lambda: clock[0],
        ).replay(scope.aggregate_id)
        if replayed.as_dict() != durable.as_dict():
            raise RuntimeError("ACCEPTANCE_PROFILE_RESTART_REPLAY_DRIFT")
        metrics = _profile_metrics(db_path, aggregate_id=scope.aggregate_id)
        expected_kinds = [
            "CANARY_STARTED",
            *(["CANARY_INTERACTION"] * 5),
            "ACTIVATED",
            "ROLLED_BACK",
        ]
        if metrics["event_kinds"] != expected_kinds:
            raise RuntimeError("ACCEPTANCE_PROFILE_EVENT_SEQUENCE_DRIFT")

        receipt = {
            "schema_version": ACCEPTANCE_SCHEMA_VERSION,
            "run_id": str(run_id).strip(),
            "commit_sha": _commit_sha(repository_root),
            "database_kind": "ISOLATED_REAL_SQLITE",
            "migration_head": migration_head,
            "migration_head_version": migration_head_version,
            "evidence": {
                "pre_canary_aftereffect_count": 5,
                "pre_canary_evidence_day_count": 5,
                "canary_interaction_count": 5,
                "confidence_lower_bound": candidate.confidence_lower_bound,
                "boundary_violation_count": candidate.boundary_violation_count,
            },
            "lifecycle": {
                "status": "PASS",
                "baseline_profile_id": baseline["profile_id"],
                "candidate_profile_id": candidate.profile_id,
                "active_profile_id": active["profile_id"],
                "rollback_profile_id": rollback["profile_id"],
                "rollback_restored_exact_predecessor": True,
                "baseline_state_version": baseline_state.version,
                "active_state_version": active_state.version,
                "rollback_state_version": rollback_state.version,
            },
            "behavior": {
                "status": "PASS_HOST_PROTOCOL_ONLY",
                "changed_during_canary": changed,
                "preserved_after_activation": {
                    "text_generation_policy": (
                        active["text_generation_policy"]["style_anchor_digest"]
                        == canary["text_generation_policy"]["style_anchor_digest"]
                    ),
                    "voice_rate": active["voice"]["rate"] == canary["voice"]["rate"],
                    "avatar_motion": (
                        active["avatar"]["motion_intensity"]
                        == canary["avatar"]["motion_intensity"]
                    ),
                },
                "restored_after_rollback": restored,
                "baseline_voice_rate": baseline["voice"]["rate"],
                "candidate_voice_rate": canary["voice"]["rate"],
                "baseline_avatar_motion": baseline["avatar"]["motion_intensity"],
                "candidate_avatar_motion": canary["avatar"]["motion_intensity"],
                "model_generation_executed": False,
                "physical_edge_effect_executed": False,
            },
            "durable_state": {
                "status": "PASS",
                **metrics,
                "restart_snapshot_equal": True,
                "database_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
            },
            "duration_ms": round((time.monotonic() - started) * 1_000),
            "external_side_effects": 0,
            "private_or_user_data_used": False,
            "temporary_database_removed": True,
            "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
            "status": "PASS_REAL_SQLITE_PROFILE_HOST_BEHAVIOR_EXACT_ROLLBACK",
        }
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run companion profile Host behavior acceptance",
    )
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--receipt", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = run_acceptance(
        repository_root=args.repository_root.resolve(),
        run_id=args.run_id,
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
