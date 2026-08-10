"""Run six production Digital Life proactive-driver scenarios.

This runner uses the production reducer, command service, relationship
lifecycle, SQLite UoW/outbox, Governor decision engine, and durable grant
resolver.  It intentionally stops at the committed proactive outbox boundary:
no Gateway delivery receipt is fabricated and no transport stub is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Mapping
import uuid


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.migration_runner import SQLiteMigrationRunner
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.digital_life.companion_proactive import decide_companion_proactive
from host_brain.digital_life.policy import (
    PROACTIVE_IDLE_THRESHOLD_MS,
    build_life_tick_payload,
)
from host_brain.digital_life.reader import LifeSnapshotReader
from host_brain.digital_life.relationship_lifecycle import (
    RelationshipLifecycleService,
    RelationshipScope,
)
from host_brain.digital_life.service import LifeCommandService
from host_brain.governor.decision import IntentDecisionEngine
from host_brain.orchestration.governor_grant import (
    DurableGovernorGrantResolver,
    governor_grant_ref,
)
from host_brain.persistence.life_command_repository import LifeCommandRepository
from host_brain.runtime.context_contracts import SceneDecisionV2
from host_brain.runtime.scene_consumer_projection import SceneIdentityV2
from host_brain.scheduler.command_service import _build_governor_intent
from natsume_contracts.life import (
    EmotionComponentV1,
    EmotionEpisodeV1,
    EventAppraisalV1,
    LifeCommand,
    LifeCommandReceipt,
    LifeCommandStatus,
    LifeDeliveryAuthorization,
)


_IDLE_FIVE_HOURS_MS = 5 * 60 * 60 * 1_000
_IDLE_ONE_HOUR_MS = 60 * 60 * 1_000


class _Clock:
    def __init__(self, value: int) -> None:
        self.value = int(value)

    def __call__(self) -> int:
        return int(self.value)


def _git_identity(repo_root: Path) -> tuple[str | None, str | None]:
    try:
        commit = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            encoding="utf-8",
        ).strip()
        diff = subprocess.check_output(
            ["git", "-C", str(repo_root), "diff", "--binary", "HEAD"]
        )
        return commit, hashlib.sha256(diff).hexdigest()
    except Exception:
        return None, None


def _receipt_summary(receipt: LifeCommandReceipt | None) -> dict[str, Any] | None:
    if receipt is None:
        return None
    value = receipt.as_dict()
    return {
        "command_id": str(value.get("command_id") or ""),
        "event_id": str(value.get("event_id") or ""),
        "person_id": str(value.get("person_id") or ""),
        "status": str(value.get("status") or ""),
        "state_version": int(value.get("state_version") or 0),
        "state_digest": str(value.get("state_digest") or ""),
        "outbox_count": int(value.get("outbox_count") or 0),
        "replayed": bool(value.get("replayed")),
        "created_at_ms": int(value.get("created_at_ms") or 0),
    }


def _case(
    case_id: str,
    name: str,
    *,
    passed: bool,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "status": "PASS" if passed else "FAIL",
        "checks_passed": bool(passed),
        "evidence": dict(evidence),
    }


def _submit_command(
    *,
    service: LifeCommandService,
    person_id: str,
    command_id: str,
    command_type: str,
    payload: Mapping[str, Any],
    created_at_ms: int,
) -> LifeCommandReceipt:
    def build(snapshot: Any) -> LifeCommand:
        return LifeCommand(
            command_id=command_id,
            person_id=person_id,
            command_type=command_type,
            payload=dict(payload),
            expected_version=int(snapshot.version),
            created_at_ms=int(created_at_ms),
            correlation_id=command_id,
            causation_id=command_id,
            metadata={"producer": "a22-real-proactive-driver"},
        )

    receipt = service.submit_next(person_id, build)
    if receipt.status is not LifeCommandStatus.APPLIED:
        raise RuntimeError(f"Life command was not applied: {receipt.as_dict()}")
    return receipt


def _real_foreground_receipt(
    *,
    service: LifeCommandService,
    case_token: str,
    at_ms: int,
) -> LifeCommandReceipt:
    """Create the applied Life fact consumed by relationship provisioning."""

    return _submit_command(
        service=service,
        person_id=f"foreground-receipt::{case_token}",
        command_id=f"a22-proactive-foreground::{case_token}",
        command_type="foreground_completed",
        payload={"session_id": f"foreground-session-{case_token}"},
        created_at_ms=at_ms,
    )


def _provision_scope(
    *,
    service: LifeCommandService,
    lifecycle: RelationshipLifecycleService,
    case_token: str,
    at_ms: int,
    permission: bool,
    close_episode: bool,
) -> tuple[RelationshipScope, dict[str, Any]]:
    workspace_id = f"workspace-a22-proactive-{case_token}"
    subject_id = f"person-a22-proactive-{case_token}"
    origin_session = f"session-a22-proactive-origin-{case_token}"
    foreground = _real_foreground_receipt(
        service=service,
        case_token=case_token,
        at_ms=at_ms,
    )
    provisioned = lifecycle.provision_accepted_response(
        workspace_id=workspace_id,
        relationship_subject_id=subject_id,
        session_id=origin_session,
        request_id=f"a22-proactive-provision-{case_token}",
        response_receipt=foreground.as_dict(),
        response_text="我会在这里。",
        proactive_target={
            "platform": "qq",
            "conversation_id": f"acceptance-self-{case_token}",
        },
    )
    permission_receipt: LifeCommandReceipt | None = None
    if permission:
        permission_receipt = lifecycle.record_proactive_permission(
            workspace_id=workspace_id,
            relationship_subject_id=subject_id,
            session_id=origin_session,
            request_id=f"a22-proactive-permission-{case_token}",
            proactive_permission="ALLOWED",
            source_ref=f"relationship-consent://a22/{case_token}",
        )
    followup_receipt: LifeCommandReceipt | None = None
    followup_reason = "NOT_REQUESTED"
    if close_episode:
        followup = lifecycle.record_followup(
            request_body=SimpleNamespace(
                workspace_id=workspace_id,
                person_id=subject_id,
                session_id=f"session-a22-proactive-followup-{case_token}",
                emotion_context={},
                message="好，我知道了。",
            ),
            request_id=f"a22-proactive-followup-{case_token}",
        )
        followup_receipt = followup.receipt
        followup_reason = followup.reason_code
    scope = RelationshipScope.from_values(
        workspace_id=workspace_id,
        relationship_subject_id=subject_id,
        session_id=f"session-a22-proactive-tick-{case_token}",
    )
    return scope, {
        "foreground_receipt": _receipt_summary(foreground),
        "provision_receipt": _receipt_summary(provisioned),
        "permission_receipt": _receipt_summary(permission_receipt),
        "followup_receipt": _receipt_summary(followup_receipt),
        "followup_reason_code": followup_reason,
    }


def _seed_warm_affect(
    *,
    service: LifeCommandService,
    scope: RelationshipScope,
    case_token: str,
    at_ms: int,
) -> LifeCommandReceipt:
    appraisal = EventAppraisalV1(
        appraisal_id=f"a22-proactive-appraisal-{case_token}",
        workspace_id=scope.workspace_id,
        relationship_subject_id=scope.relationship_subject_id,
        session_id=scope.session_id,
        event_kind="relationship_reunion",
        target_ref=f"event://a22/proactive/{case_token}",
        goal_congruence=0.82,
        responsibility=0.1,
        controllability=0.65,
        relationship_significance=0.92,
        boundary_relevance=0.05,
        risk=0.02,
        uncertainty=0.08,
        evidence_refs=(f"appraisal://a22/proactive/{case_token}",),
    )
    episode = EmotionEpisodeV1(
        episode_id=f"a22-proactive-affect-episode-{case_token}",
        appraisal_id=appraisal.appraisal_id,
        relationship_subject_id=scope.relationship_subject_id,
        trigger_ref=appraisal.target_ref,
        components=(
            EmotionComponentV1("CARE", 0.84, 0.65, 0.34, "comfort"),
            EmotionComponentV1("SHARED_JOY", 0.8, 0.82, 0.52, "play"),
        ),
        opened_at_ms=at_ms,
        decay_half_life_ms=3_600_000,
        evidence_refs=(f"episode://a22/proactive/{case_token}",),
    )
    return _submit_command(
        service=service,
        person_id=scope.aggregate_id,
        command_id=f"a22-proactive-affect::{case_token}",
        command_type="affective_event_appraised",
        payload={"appraisal": appraisal.as_dict(), "episode": episode.as_dict()},
        created_at_ms=at_ms,
    )


def _durable_scheduler_authorization(
    *,
    ledger: SQLiteLedger,
    person_id: str,
    case_token: str,
    now_ms: int,
) -> tuple[LifeDeliveryAuthorization, dict[str, Any]]:
    request_id = f"a22-proactive-scheduler-request-{case_token}"
    principal_id = "a22-scheduler-service"
    occurrence = SimpleNamespace(
        task_id="life_tick",
        occurrence_id=f"scheduleroccurrence://a22/proactive/{case_token}",
        principal_id=principal_id,
        payload={"person_id": person_id},
    )
    definition = SimpleNamespace(
        worker_key="life_command",
        intent_template={"args": {"person_id": person_id}},
    )
    intent = _build_governor_intent(
        occurrence=occurrence,
        definition=definition,
        request_id=request_id,
        session_id=f"a22-proactive-scheduler-session-{case_token}",
        ts_ms=now_ms,
        mode="normal",
    )
    decision = IntentDecisionEngine(ledger=ledger).evaluate_intent(
        intent,
        request_id=request_id,
        current_ms=now_ms + 1,
        governance_snapshot={
            "trust_level": 80,
            "energy_level": 80,
            "sys_mode": "normal",
            "rate_limit_hit": False,
            "scope": "system",
            "scope_allowlist": ["system"],
            "safety_flag": False,
            "airlock_blocked": False,
        },
    )
    if str(decision.get("decision") or "").upper() != "APPROVE":
        raise RuntimeError(f"Governor rejected scheduler intent: {decision}")
    grant_ref = governor_grant_ref(request_id, decision)
    authorization = LifeDeliveryAuthorization(
        request_id=request_id,
        governor_grant_ref=grant_ref,
        authority_kind="scheduler_host_command",
        authority_principal_id=principal_id,
    )
    resolved = DurableGovernorGrantResolver(ledger).require(
        request_id=request_id,
        expected_ref=grant_ref,
    )
    return authorization, {
        "request_id": request_id,
        "intent_id": str(decision.get("intent_id") or ""),
        "decision": str(decision.get("decision") or ""),
        "governor_grant_ref": grant_ref,
        "durable_resolved": bool(resolved),
        "principal_id": principal_id,
    }


def _occurrence_payload(
    *,
    scope: RelationshipScope,
    authorization: LifeDeliveryAuthorization,
) -> dict[str, Any]:
    scene = SceneDecisionV2(
        workspace_id=scope.workspace_id,
        subject_id=scope.relationship_subject_id,
        scene_id="scene.casual-chat",
        revision=1,
        world_snapshot_digest=hashlib.sha256(
            f"world:{scope.aggregate_id}".encode()
        ).hexdigest(),
        context_digest=hashlib.sha256(
            f"context:{scope.aggregate_id}".encode()
        ).hexdigest(),
        guardian_snapshot_digest=hashlib.sha256(
            f"guardian:{scope.aggregate_id}".encode()
        ).hexdigest(),
        constraints=(),
    )
    return {
        "timezone": "Asia/Shanghai",
        "delivery_authorization": authorization.as_dict(),
        "scene_decision_v2": scene.to_dict(),
        "scene_identity_v2": SceneIdentityV2.from_decision(scene).to_dict(),
    }


def _build_tick_command(
    *,
    reader: LifeSnapshotReader,
    person_id: str,
    case_token: str,
    scheduled_at_ms: int,
    occurrence_payload: Mapping[str, Any],
) -> tuple[LifeCommand, dict[str, Any]]:
    snapshot = reader.get_snapshot(person_id)
    payload = build_life_tick_payload(
        snapshot,
        scheduled_at_ms=scheduled_at_ms,
        occurrence_payload=occurrence_payload,
        task_id="life_tick",
        definition_revision=1,
    )
    command = LifeCommand(
        command_id=f"life_tick::a22-proactive::{case_token}",
        person_id=person_id,
        command_type="tick",
        payload=payload,
        expected_version=snapshot.version,
        created_at_ms=scheduled_at_ms,
        correlation_id=f"a22-proactive-scheduler-request-{case_token}",
        causation_id=f"scheduleroccurrence://a22/proactive/{case_token}",
        metadata={"producer": "a22-real-proactive-driver"},
    )
    return command, payload


def _outbox_evidence(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        payload = json.loads(str(row.get("payload_json") or "{}"))
        extra = dict(payload.get("raw_extra") or {})
        decision = dict(extra.get("companion_proactive_decision") or {})
        plan = dict(extra.get("affective_turn_plan") or {})
        expression = dict(plan.get("expression_plan") or {})
        evidence.append(
            {
                "outbox_id": str(row.get("outbox_id") or ""),
                "delivery_key": str(row.get("delivery_key") or ""),
                "destination": str(row.get("destination") or ""),
                "status": str(row.get("status") or ""),
                "payload_digest": str(row.get("payload_digest") or ""),
                "authorization_digest": str(row.get("authorization_digest") or ""),
                "decision_id": str(decision.get("decision_id") or ""),
                "plan_id": str(plan.get("plan_id") or ""),
                "synchronization_id": str(
                    expression.get("synchronization_id") or ""
                ),
                "content_digest": str(decision.get("content_digest") or ""),
            }
        )
    return evidence


def run_matrix(
    *,
    database_path: Path,
    repo_root: Path,
) -> dict[str, Any]:
    database = database_path.resolve()
    if database.exists():
        raise FileExistsError(f"acceptance database already exists: {database}")
    database.parent.mkdir(parents=True, exist_ok=True)
    wall_ms = int(time.time() * 1000)
    migration = SQLiteMigrationRunner(db_path=database).apply_migrations(
        request_id=f"a22-proactive-migration-{uuid.uuid4().hex}",
        ts_ms=wall_ms,
    )
    if not migration.success:
        raise RuntimeError(f"migration failed: {migration.as_dict()}")
    clock = _Clock(wall_ms)
    repository = LifeCommandRepository(database)
    service = LifeCommandService(repository, clock=clock)
    reader = LifeSnapshotReader(repository)
    lifecycle = RelationshipLifecycleService(
        command_service_provider=lambda: service,
        snapshot_reader_provider=lambda: reader,
        clock_ms=clock,
    )
    ledger = SQLiteLedger(database)
    cases: list[dict[str, Any]] = []

    # 01: an untouched real aggregate has no target and cannot reach out.
    token = "target-unavailable"
    base_ms = wall_ms - _IDLE_FIVE_HOURS_MS
    clock.value = base_ms
    scope = RelationshipScope.from_values(
        workspace_id=f"workspace-a22-proactive-{token}",
        relationship_subject_id=f"person-a22-proactive-{token}",
        session_id=f"session-a22-proactive-{token}",
    )
    authorization, grant = _durable_scheduler_authorization(
        ledger=ledger,
        person_id=scope.aggregate_id,
        case_token=token,
        now_ms=wall_ms,
    )
    before = reader.get_snapshot(scope.aggregate_id)
    decision = decide_companion_proactive(
        before.state, now_ms=wall_ms, timezone_name="Asia/Shanghai"
    )
    command, payload = _build_tick_command(
        reader=reader,
        person_id=scope.aggregate_id,
        case_token=token,
        scheduled_at_ms=wall_ms,
        occurrence_payload=_occurrence_payload(scope=scope, authorization=authorization),
    )
    clock.value = wall_ms
    receipt = service.submit(command)
    rows = repository.list_outbox(command_id=command.command_id)
    cases.append(
        _case(
            "PROACTIVE-01",
            "missing canonical target holds without outbox",
            passed=(
                decision.reason_code == "PROACTIVE_TARGET_UNAVAILABLE"
                and not decision.send_allowed
                and receipt.status is LifeCommandStatus.APPLIED
                and int(receipt.outbox_count) == 0
                and payload["deliveries"] == []
                and rows == []
            ),
            evidence={
                "decision": decision.as_trace(),
                "grant": grant,
                "receipt": _receipt_summary(receipt),
                "outbox_count": len(rows),
            },
        )
    )

    # 02: a real accepted-response target without explicit permission holds.
    token = "permission-hold"
    base_ms = wall_ms - _IDLE_FIVE_HOURS_MS
    clock.value = base_ms
    scope, lifecycle_evidence = _provision_scope(
        service=service,
        lifecycle=lifecycle,
        case_token=token,
        at_ms=base_ms,
        permission=False,
        close_episode=True,
    )
    clock.value = base_ms + 10
    warm = _seed_warm_affect(
        service=service,
        scope=scope,
        case_token=token,
        at_ms=clock.value,
    )
    authorization, grant = _durable_scheduler_authorization(
        ledger=ledger,
        person_id=scope.aggregate_id,
        case_token=token,
        now_ms=wall_ms,
    )
    before = reader.get_snapshot(scope.aggregate_id)
    decision = decide_companion_proactive(
        before.state, now_ms=wall_ms, timezone_name="Asia/Shanghai"
    )
    command, payload = _build_tick_command(
        reader=reader,
        person_id=scope.aggregate_id,
        case_token=token,
        scheduled_at_ms=wall_ms,
        occurrence_payload=_occurrence_payload(scope=scope, authorization=authorization),
    )
    clock.value = wall_ms
    receipt = service.submit(command)
    rows = repository.list_outbox(command_id=command.command_id)
    cases.append(
        _case(
            "PROACTIVE-02",
            "target without explicit permission holds without outbox",
            passed=(
                decision.reason_code == "PROACTIVE_PERMISSION_NOT_ALLOWED"
                and not decision.send_allowed
                and receipt.status is LifeCommandStatus.APPLIED
                and int(receipt.outbox_count) == 0
                and payload["deliveries"] == []
                and rows == []
            ),
            evidence={
                "lifecycle": lifecycle_evidence,
                "warm_affect_receipt": _receipt_summary(warm),
                "decision": decision.as_trace(),
                "grant": grant,
                "receipt": _receipt_summary(receipt),
                "outbox_count": len(rows),
            },
        )
    )

    # 03: permission cannot override an unresolved relationship episode.
    token = "episode-open"
    base_ms = wall_ms - _IDLE_FIVE_HOURS_MS
    clock.value = base_ms
    scope, lifecycle_evidence = _provision_scope(
        service=service,
        lifecycle=lifecycle,
        case_token=token,
        at_ms=base_ms,
        permission=True,
        close_episode=False,
    )
    clock.value = base_ms + 10
    warm = _seed_warm_affect(
        service=service,
        scope=scope,
        case_token=token,
        at_ms=clock.value,
    )
    authorization, grant = _durable_scheduler_authorization(
        ledger=ledger,
        person_id=scope.aggregate_id,
        case_token=token,
        now_ms=wall_ms,
    )
    before = reader.get_snapshot(scope.aggregate_id)
    decision = decide_companion_proactive(
        before.state, now_ms=wall_ms, timezone_name="Asia/Shanghai"
    )
    command, payload = _build_tick_command(
        reader=reader,
        person_id=scope.aggregate_id,
        case_token=token,
        scheduled_at_ms=wall_ms,
        occurrence_payload=_occurrence_payload(scope=scope, authorization=authorization),
    )
    clock.value = wall_ms
    receipt = service.submit(command)
    rows = repository.list_outbox(command_id=command.command_id)
    cases.append(
        _case(
            "PROACTIVE-03",
            "open relationship episode holds despite permission",
            passed=(
                decision.reason_code == "PROACTIVE_RELATIONSHIP_EPISODE_OPEN"
                and not decision.send_allowed
                and receipt.status is LifeCommandStatus.APPLIED
                and int(receipt.outbox_count) == 0
                and payload["deliveries"] == []
                and rows == []
            ),
            evidence={
                "lifecycle": lifecycle_evidence,
                "warm_affect_receipt": _receipt_summary(warm),
                "decision": decision.as_trace(),
                "grant": grant,
                "receipt": _receipt_summary(receipt),
                "outbox_count": len(rows),
            },
        )
    )

    # 04: even an otherwise eligible state cannot bypass the four-hour gate.
    token = "idle-threshold"
    base_ms = wall_ms - _IDLE_ONE_HOUR_MS
    clock.value = base_ms
    scope, lifecycle_evidence = _provision_scope(
        service=service,
        lifecycle=lifecycle,
        case_token=token,
        at_ms=base_ms,
        permission=True,
        close_episode=True,
    )
    clock.value = base_ms + 10
    warm = _seed_warm_affect(
        service=service,
        scope=scope,
        case_token=token,
        at_ms=clock.value,
    )
    authorization, grant = _durable_scheduler_authorization(
        ledger=ledger,
        person_id=scope.aggregate_id,
        case_token=token,
        now_ms=wall_ms,
    )
    before = reader.get_snapshot(scope.aggregate_id)
    last_seen = int(dict(before.state.get("clock") or {}).get("last_user_seen_ms") or 0)
    idle_ms = max(0, wall_ms - last_seen)
    command, payload = _build_tick_command(
        reader=reader,
        person_id=scope.aggregate_id,
        case_token=token,
        scheduled_at_ms=wall_ms,
        occurrence_payload=_occurrence_payload(scope=scope, authorization=authorization),
    )
    clock.value = wall_ms
    receipt = service.submit(command)
    rows = repository.list_outbox(command_id=command.command_id)
    cases.append(
        _case(
            "PROACTIVE-04",
            "four-hour idle threshold holds before policy delivery",
            passed=(
                0 < idle_ms < PROACTIVE_IDLE_THRESHOLD_MS
                and receipt.status is LifeCommandStatus.APPLIED
                and int(receipt.outbox_count) == 0
                and payload["deliveries"] == []
                and rows == []
            ),
            evidence={
                "lifecycle": lifecycle_evidence,
                "warm_affect_receipt": _receipt_summary(warm),
                "idle_ms": idle_ms,
                "required_idle_ms": PROACTIVE_IDLE_THRESHOLD_MS,
                "grant": grant,
                "receipt": _receipt_summary(receipt),
                "outbox_count": len(rows),
            },
        )
    )

    # 05/06 share one real command: commit once, then exact replay.
    token = "eligible"
    base_ms = wall_ms - _IDLE_FIVE_HOURS_MS
    clock.value = base_ms
    scope, lifecycle_evidence = _provision_scope(
        service=service,
        lifecycle=lifecycle,
        case_token=token,
        at_ms=base_ms,
        permission=True,
        close_episode=True,
    )
    clock.value = base_ms + 10
    warm = _seed_warm_affect(
        service=service,
        scope=scope,
        case_token=token,
        at_ms=clock.value,
    )
    authorization, grant = _durable_scheduler_authorization(
        ledger=ledger,
        person_id=scope.aggregate_id,
        case_token=token,
        now_ms=wall_ms,
    )
    before = reader.get_snapshot(scope.aggregate_id)
    decision = decide_companion_proactive(
        before.state, now_ms=wall_ms, timezone_name="Asia/Shanghai"
    )
    command, payload = _build_tick_command(
        reader=reader,
        person_id=scope.aggregate_id,
        case_token=token,
        scheduled_at_ms=wall_ms,
        occurrence_payload=_occurrence_payload(scope=scope, authorization=authorization),
    )
    clock.value = wall_ms
    first_receipt = service.submit(command)
    first_rows = repository.list_outbox(command_id=command.command_id)
    outbox = _outbox_evidence(first_rows)
    persisted_payload = json.loads(str(first_rows[0]["payload_json"])) if first_rows else {}
    grant_resolved = False
    if first_rows:
        DurableGovernorGrantResolver(ledger).require_delivery_bound(
            request_id=authorization.request_id,
            expected_ref=authorization.governor_grant_ref,
            authority_kind=authorization.authority_kind,
            authority_principal_id=authorization.authority_principal_id,
            person_id=scope.aggregate_id,
            destination=str(first_rows[0]["destination"]),
            payload=persisted_payload,
            now_ms=wall_ms + 100,
        )
        grant_resolved = True
    decision_record = dict(payload.get("companion_proactive_decision") or {})
    cases.append(
        _case(
            "PROACTIVE-05",
            "eligible canonical impulse commits one authorized durable outbox",
            passed=(
                decision.send_allowed
                and decision.reason_code == "PROACTIVE_CANONICAL_IMPULSE_READY"
                and first_receipt.status is LifeCommandStatus.APPLIED
                and int(first_receipt.outbox_count) == 1
                and len(first_rows) == 1
                and len(outbox) == 1
                and outbox[0]["destination"] == "proactive.gateway"
                and bool(outbox[0]["decision_id"])
                and bool(outbox[0]["plan_id"])
                and bool(outbox[0]["synchronization_id"])
                and outbox[0]["decision_id"] == decision_record.get("decision_id")
                and grant_resolved
            ),
            evidence={
                "lifecycle": lifecycle_evidence,
                "warm_affect_receipt": _receipt_summary(warm),
                "decision": decision.as_trace(),
                "grant": {**grant, "delivery_bound_resolved": grant_resolved},
                "receipt": _receipt_summary(first_receipt),
                "outbox": outbox,
                "gateway_dispatch_attempted": False,
                "delivery_receipt_claimed": False,
            },
        )
    )

    replay_receipt = service.submit(command)
    replay_rows = repository.list_outbox(command_id=command.command_id)
    cases.append(
        _case(
            "PROACTIVE-06",
            "exact command replay returns durable receipt without duplicate outbox",
            passed=(
                replay_receipt.status is LifeCommandStatus.APPLIED
                and bool(replay_receipt.replayed)
                and replay_receipt.event_id == first_receipt.event_id
                and replay_receipt.state_digest == first_receipt.state_digest
                and len(replay_rows) == 1
                and str(replay_rows[0]["outbox_id"])
                == str(first_rows[0]["outbox_id"])
            ),
            evidence={
                "first_receipt": _receipt_summary(first_receipt),
                "replay_receipt": _receipt_summary(replay_receipt),
                "first_outbox": outbox,
                "replay_outbox": _outbox_evidence(replay_rows),
                "duplicate_external_send_attempted": False,
                "delivery_receipt_claimed": False,
            },
        )
    )

    commit, diff_sha = _git_identity(repo_root.resolve())
    migration_payload = migration.as_dict()
    migration_records = list(migration_payload.get("records") or [])
    migration_records_sha256 = hashlib.sha256(
        json.dumps(
            migration_records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    report: dict[str, Any] = {
        "schema_version": "natsume.a22.proactive-real-driver-matrix.v1",
        "status": (
            "PASS"
            if len(cases) == 6 and all(row["status"] == "PASS" for row in cases)
            else "FAIL"
        ),
        "repository_commit": commit,
        "worktree_diff_sha256": diff_sha,
        "database_path": str(database),
        "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
        "migration": {
            "success": bool(migration.success),
            "stage": migration.stage,
            "reason_code": migration.reason_code,
            "detail": migration.detail,
            "summary": dict(migration.summary),
            "records_sha256": migration_records_sha256,
        },
        "real_production_components": [
            "SQLiteMigrationRunner",
            "LifeCommandService",
            "RelationshipLifecycleService",
            "build_life_tick_payload",
            "IntentDecisionEngine",
            "DurableGovernorGrantResolver",
            "LifeCommandRepository",
        ],
        "mock_transport": False,
        "stub_receipts": False,
        "gateway_dispatch_attempted": False,
        "delivery_receipts_claimed": 0,
        "scope_statement": (
            "This matrix proves policy holds, canonical Life receipts, durable "
            "Governor authorization, atomic proactive outbox commit, and replay. "
            "It makes no external Gateway delivery claim."
        ),
        "case_count": len(cases),
        "passed_case_count": sum(row["status"] == "PASS" for row in cases),
        "cases": cases,
    }
    canonical = json.dumps(
        report, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_matrix(database_path=args.database, repo_root=args.repo_root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
