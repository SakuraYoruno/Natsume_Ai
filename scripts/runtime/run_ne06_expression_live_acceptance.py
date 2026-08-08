#!/usr/bin/env python
"""Run one real NE-06 expression apply -> Host turn -> rollback flow.

The runner uses the production SQLite authorities and the trusted no-network
Docker Lab.  It never starts, stops, or probes Edge/Gateway processes.  Voice
and Avatar remain explicitly untested when the remote Edge is disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping
import uuid

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.audit.migration_runner import SQLiteMigrationRunner  # noqa: E402
from host_brain.contracts.agent_runtime.review_policy import (  # noqa: E402
    ReviewItem,
    ReviewResumePhase,
    ReviewStatus,
    ReviewType,
)
from host_brain.evolution.version_activation import (  # noqa: E402
    EXPRESSION_PREFERENCE_POLICY_KIND,
    EvolutionVersionActivationService,
)
from host_brain.evolution_lab.companion_expression_experiment import (  # noqa: E402
    BoundCompanionExpressionDatasetReader,
    CompanionExpressionVerifier,
    activation_review_subject_version,
    build_companion_expression_spec,
    run_companion_expression_experiment,
)
from host_brain.evolution_lab.docker_executor import (  # noqa: E402
    TrustedDockerSandboxExecutor,
)
from host_brain.evolution_lab.trusted_runner import (  # noqa: E402
    TrustedEvolutionLabPorts,
    TrustedEvolutionLabRunner,
)
from host_brain.persistence.evolution_activation_v2_evidence import (  # noqa: E402
    feedback_subject_ref,
    feedback_target_kind,
    payload_digest,
)
from host_brain.persistence.evolution_activation_v2_repository import (  # noqa: E402
    SQLiteEvolutionActivationV2Repository,
)
from host_brain.persistence.evolution_lab_trusted_repository import (  # noqa: E402
    SQLiteTrustedEvolutionLabLedger,
)
from host_brain.persistence.evolution_lab_v2_repository import (  # noqa: E402
    SQLiteEvolutionLabV2Ledger,
)
from host_brain.persistence.review_repository import ReviewStore  # noqa: E402
from host_brain.tribe.prefrontal_runtime_policy import (  # noqa: E402
    expression_preference_directive,
    expression_preference_hints,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run trusted expression Lab, production Review/activation, one "
            "real Host turn, and exact rollback."
        )
    )
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--workspace-id", default="host-default")
    parser.add_argument("--subject-id", default="natsume_ai")
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--host-base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--operator-id", default="local-owner-acceptance")
    parser.add_argument("--receipt-path", required=True)
    parser.add_argument("--chat-timeout-s", type=float, default=90.0)
    args = parser.parse_args(argv)

    commit = str(args.commit_sha or "").strip().lower()
    _require_exact_tracked_commit(commit)
    db_path = Path(args.db_path).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    receipt_path = Path(args.receipt_path).expanduser().resolve()
    migration = SQLiteMigrationRunner(db_path=db_path).apply_migrations(
        request_id=f"ne06-expression-live:{commit[:12]}"
    )
    if not migration.success or int(
        migration.summary.get("latest_version", 0)
    ) < 142:
        raise RuntimeError(
            f"NE06_EXPRESSION_MIGRATION_FAILED:{migration.reason_code}"
        )

    repository = SQLiteEvolutionActivationV2Repository(db_path)
    service = EvolutionVersionActivationService(repository)
    reviews = ReviewStore(db_path)
    workspace_id = str(args.workspace_id).strip()
    subject_id = str(args.subject_id).strip()
    run_id = f"ne06-expression-{int(time.time() * 1000)}"
    signal_id = f"{run_id}:{commit[:12]}"
    staged = service.stage_expression_feedback(
        workspace_id=workspace_id,
        subject_id=subject_id,
        target_request_id=signal_id,
        target_text_hash=hashlib.sha256(signal_id.encode("utf-8")).hexdigest(),
        feedback_score=-1.0,
        feedback_tags=("ai_tone", "template_like"),
        feedback_source="local_owner_acceptance",
    )
    if str(staged.get("status")) != "STAGED":
        raise RuntimeError("NE06_EXPRESSION_PROPOSAL_NOT_STAGED")
    feedback_proposal = dict(staged["proposal"])
    original = repository.active_policy(
        workspace_id=workspace_id,
        subject_id=subject_id,
        policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
    )
    if original is None:
        raise RuntimeError("NE06_EXPRESSION_BASELINE_MISSING")
    original_payload = dict(original["payload"])
    target_payload = dict(feedback_proposal["change"])
    original_version = str(original["active_version"])
    original_digest = str(original["active_digest"])
    target_version = str(feedback_proposal["target_version"])
    target_digest = payload_digest(target_payload)
    initial_generation = int(original["activation_generation"])
    target_kind = feedback_target_kind(
        workspace_id=workspace_id,
        subject_id=subject_id,
        policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
    )

    spec_registry = SQLiteEvolutionLabV2Ledger(db_path)
    trusted_ledger = SQLiteTrustedEvolutionLabLedger(db_path)
    worker_source = (
        ROOT
        / "host_brain"
        / "evolution_lab"
        / "companion_expression_worker.py"
    )
    executor = TrustedDockerSandboxExecutor(
        runtime_root=runtime_root,
        image_reference=args.image_reference,
        worker_source=worker_source,
    )
    apply_authority = _run_lab_authority(
        workspace_id=workspace_id,
        commit=commit,
        baseline_version=original_version,
        target_version=target_version,
        baseline_payload=original_payload,
        target_payload=target_payload,
        target_kind=target_kind,
        owner_id=f"{args.operator_id}:{run_id}:apply",
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )
    rollback_authority = _run_lab_authority(
        workspace_id=workspace_id,
        commit=commit,
        baseline_version=target_version,
        target_version=original_version,
        baseline_payload=target_payload,
        target_payload=original_payload,
        target_kind=target_kind,
        owner_id=f"{args.operator_id}:{run_id}:rollback",
        executor=executor,
        spec_registry=spec_registry,
        trusted_ledger=trusted_ledger,
    )

    subject_ref = feedback_subject_ref(
        workspace_id=workspace_id,
        subject_id=subject_id,
        policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
        proposal_digest=str(feedback_proposal["proposal_digest"]),
    )
    apply_review = _create_and_approve_review(
        reviews=reviews,
        run_id=run_id,
        action="apply",
        operator_id=str(args.operator_id),
        subject_ref=subject_ref,
        subject_version=activation_review_subject_version(
            initial_generation
        ),
        lab_report=apply_authority["report"],
        lab_proposal=apply_authority["proposal"],
        feedback_proposal=feedback_proposal,
        policy_digest=target_digest,
    )
    rollback_review = _create_and_approve_review(
        reviews=reviews,
        run_id=run_id,
        action="rollback",
        operator_id=str(args.operator_id),
        subject_ref=subject_ref,
        subject_version=activation_review_subject_version(
            initial_generation + 1
        ),
        lab_report=rollback_authority["report"],
        lab_proposal=rollback_authority["proposal"],
        feedback_proposal=feedback_proposal,
        policy_digest=original_digest,
    )

    baseline_turn = _run_host_turn(
        host_base_url=args.host_base_url,
        db_path=db_path,
        session_id=f"{run_id}-baseline",
        timeout_s=float(args.chat_timeout_s),
    )
    applied_receipt = service.apply_feedback(
        workspace_id=workspace_id,
        subject_id=subject_id,
        proposal_id=str(feedback_proposal["proposal_id"]),
        lab_report_digest=apply_authority["report"].report_digest,
        review_id=apply_review["review_id"],
        review_status_version=apply_review["status_version"],
        expected_generation=initial_generation,
        idempotency_key=f"{run_id}:apply",
    )
    active_after_apply = repository.active_policy(
        workspace_id=workspace_id,
        subject_id=subject_id,
        policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
    )
    candidate_turn: dict[str, Any] = {}
    rolled_back_receipt = None
    rollback_error = ""
    try:
        candidate_turn = _run_host_turn(
            host_base_url=args.host_base_url,
            db_path=db_path,
            session_id=f"{run_id}-candidate",
            timeout_s=float(args.chat_timeout_s),
        )
    finally:
        try:
            rolled_back_receipt = service.rollback_feedback(
                workspace_id=workspace_id,
                subject_id=subject_id,
                proposal_id=str(feedback_proposal["proposal_id"]),
                lab_report_digest=(
                    rollback_authority["report"].report_digest
                ),
                review_id=rollback_review["review_id"],
                review_status_version=rollback_review["status_version"],
                expected_generation=initial_generation + 1,
                idempotency_key=f"{run_id}:rollback",
            )
        except Exception as exc:  # noqa: BLE001 - preserve recovery evidence
            rollback_error = f"{type(exc).__name__}:{exc}"

    active_after_rollback = repository.active_policy(
        workspace_id=workspace_id,
        subject_id=subject_id,
        policy_kind=EXPRESSION_PREFERENCE_POLICY_KIND,
    )
    rollback_turn = (
        _run_host_turn(
            host_base_url=args.host_base_url,
            db_path=db_path,
            session_id=f"{run_id}-rollback",
            timeout_s=float(args.chat_timeout_s),
        )
        if not rollback_error
        else {}
    )
    checks = {
        "baseline_host_turn": _turn_passed(baseline_turn),
        "candidate_host_turn": _turn_passed(candidate_turn),
        "rollback_host_turn": _turn_passed(rollback_turn),
        "apply_projection_exact": bool(
            active_after_apply
            and str(active_after_apply["active_version"])
            == target_version
            and str(active_after_apply["active_digest"]) == target_digest
        ),
        "candidate_hints_active": expression_preference_hints(
            None if active_after_apply is None else active_after_apply["payload"]
        )
        == ["避免机械 AI 腔", "避免模板化分段"],
        "rollback_projection_exact": bool(
            active_after_rollback
            and str(active_after_rollback["active_version"])
            == original_version
            and str(active_after_rollback["active_digest"])
            == original_digest
        ),
        "rollback_hints_exact": expression_preference_hints(
            None
            if active_after_rollback is None
            else active_after_rollback["payload"]
        )
        == expression_preference_hints(original_payload),
        "baseline_surface_policy_exact": (
            _turn_expression_directive(baseline_turn)
            == expression_preference_directive(original_payload)
        ),
        "candidate_surface_policy_exact": (
            _turn_expression_directive(candidate_turn)
            == expression_preference_directive(target_payload)
        ),
        "rollback_surface_policy_exact": (
            _turn_expression_directive(rollback_turn)
            == expression_preference_directive(original_payload)
        ),
        "baseline_prompt_directive_digest_exact": (
            _turn_prompt_directive_digest(baseline_turn)
            == _expression_directive_digest(original_payload)
        ),
        "candidate_prompt_directive_digest_exact": (
            _turn_prompt_directive_digest(candidate_turn)
            == _expression_directive_digest(target_payload)
        ),
        "rollback_prompt_directive_digest_exact": (
            _turn_prompt_directive_digest(rollback_turn)
            == _expression_directive_digest(original_payload)
        ),
        "candidate_prompt_digest_changed": (
            str(dict(candidate_turn.get("model") or {}).get("prompt_digest") or "")
            != str(dict(baseline_turn.get("model") or {}).get("prompt_digest") or "")
        ),
        "docker_apply_two_arms": len(apply_authority["report"].runs) == 2,
        "docker_rollback_two_arms": (
            len(rollback_authority["report"].runs) == 2
        ),
        "production_side_effects_zero": all(
            not run.sandbox_attestation.effect_audit.production_side_effects
            for report in (
                apply_authority["report"],
                rollback_authority["report"],
            )
            for run in report.runs
        ),
        "rollback_receipt_present": rolled_back_receipt is not None,
        "rollback_error_empty": not rollback_error,
    }
    status = (
        "PASS_REAL_DOCKER_REVIEW_ACTIVATION_SURFACE_CAUSAL_ROLLBACK"
        if all(checks.values())
        else "FAIL"
    )
    payload = {
        "schema": "natsume.ne06.expression-live-acceptance.v3",
        "status": status,
        "run_id": run_id,
        "commit_sha": commit,
        "migration_head": int(
            migration.summary.get("latest_version", 0)
        ),
        "workspace_id": workspace_id,
        "subject_id": subject_id,
        "policy_kind": EXPRESSION_PREFERENCE_POLICY_KIND,
        "checks": checks,
        "observations": {
            "candidate_prompt_tokens_changed": (
                int(
                    dict(candidate_turn.get("model") or {}).get(
                        "prompt_tokens"
                    )
                    or 0
                )
                != int(
                    dict(baseline_turn.get("model") or {}).get(
                        "prompt_tokens"
                    )
                    or 0
                )
            ),
        },
        "versions": {
            "original": original_version,
            "candidate": target_version,
            "final": (
                ""
                if active_after_rollback is None
                else str(active_after_rollback["active_version"])
            ),
            "initial_generation": initial_generation,
            "final_generation": (
                0
                if active_after_rollback is None
                else int(active_after_rollback["activation_generation"])
            ),
        },
        "lab": {
            "image_reference": args.image_reference,
            "apply_report_digest": apply_authority["report"].report_digest,
            "rollback_report_digest": (
                rollback_authority["report"].report_digest
            ),
            "apply_run_receipts": [
                run.receipt_digest for run in apply_authority["report"].runs
            ],
            "rollback_run_receipts": [
                run.receipt_digest
                for run in rollback_authority["report"].runs
            ],
        },
        "review": {
            "apply": apply_review,
            "rollback": rollback_review,
            "operator_id": str(args.operator_id),
        },
        "activation": {
            "apply_receipt_digest": applied_receipt.receipt_digest,
            "rollback_receipt_digest": (
                ""
                if rolled_back_receipt is None
                else rolled_back_receipt.receipt_digest
            ),
            "rollback_error": rollback_error,
        },
        "host_turns": {
            "baseline": baseline_turn,
            "candidate": candidate_turn,
            "rollback": rollback_turn,
        },
        "human_experience_verdict": (
            "UNPROVEN_SINGLE_SAMPLE_NOT_A_BLIND_COMPARISON"
        ),
        "voice": "NOT_TESTED_REMOTE_EDGE_DISABLED",
        "avatar": "NOT_TESTED_REMOTE_EDGE_DISABLED",
        "remote_ci": "NOT_RUN_ACCOUNT_QUOTA",
        "security": "NOT_RUN_USER_SKIPPED",
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    receipt_path.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if status.startswith("PASS_") else 1


def _run_lab_authority(
    *,
    workspace_id: str,
    commit: str,
    baseline_version: str,
    target_version: str,
    baseline_payload: Mapping[str, Any],
    target_payload: Mapping[str, Any],
    target_kind: str,
    owner_id: str,
    executor: TrustedDockerSandboxExecutor,
    spec_registry: SQLiteEvolutionLabV2Ledger,
    trusted_ledger: SQLiteTrustedEvolutionLabLedger,
) -> dict[str, Any]:
    provisional_dataset, _ = build_companion_expression_spec(
        workspace_id=workspace_id,
        code_version=commit,
        isolation_profile_digest=executor.sandbox_profile_digest,
        verifier_digest="0" * 64,
        baseline_version=baseline_version,
        target_version=target_version,
        baseline_policy=baseline_payload,
        target_policy=target_payload,
    )
    reader = BoundCompanionExpressionDatasetReader(provisional_dataset)
    verifier = CompanionExpressionVerifier(dataset_reader=reader)
    dataset, spec = build_companion_expression_spec(
        workspace_id=workspace_id,
        code_version=commit,
        isolation_profile_digest=executor.sandbox_profile_digest,
        verifier_digest=verifier.verifier_digest,
        baseline_version=baseline_version,
        target_version=target_version,
        baseline_policy=baseline_payload,
        target_policy=target_payload,
    )
    reader = BoundCompanionExpressionDatasetReader(dataset)
    verifier = CompanionExpressionVerifier(dataset_reader=reader)
    runner = TrustedEvolutionLabRunner(
        ports=TrustedEvolutionLabPorts(
            dataset_reader=reader,
            executor=executor,
            verifier=verifier,
            spec_registry=spec_registry,
            operation_ledger=trusted_ledger,
        )
    )
    result = run_companion_expression_experiment(
        runner=runner,
        ledger=trusted_ledger,
        spec=spec,
        target_kind=target_kind,
        owner_id=owner_id,
    )
    return {"report": result.report, "proposal": result.proposal}


def _create_and_approve_review(
    *,
    reviews: ReviewStore,
    run_id: str,
    action: str,
    operator_id: str,
    subject_ref: str,
    subject_version: int,
    lab_report: Any,
    lab_proposal: Any,
    feedback_proposal: Mapping[str, Any],
    policy_digest: str,
) -> dict[str, Any]:
    review_id = f"review-{run_id}-{action}"
    item = ReviewItem(
        review_id=review_id,
        review_type=ReviewType.PRE_EXECUTION_APPROVAL,
        subject_ref=subject_ref,
        subject_version=subject_version,
        requester=operator_id,
        reason=f"本机真实验收批准表达偏好 {action}",
        risk_level=2,
        side_effects=("expression-policy-version-switch",),
        evidence_refs=(
            f"evolution-lab-report://sha256/{lab_report.report_digest}",
            (
                "evolution-proposal://sha256/"
                f"{lab_proposal.proposal_digest}"
            ),
            (
                "evolution-feedback-proposal://sha256/"
                f"{feedback_proposal['proposal_digest']}"
            ),
            f"evolution-policy-version://sha256/{policy_digest}",
        ),
        status=ReviewStatus.PENDING,
        expires_at=int(time.time() * 1000) + 1_800_000,
        resume_phase=ReviewResumePhase.PRE_EXECUTION,
        checkpoint_id=f"checkpoint://{run_id}/{action}",
    )
    reviews.create_review(item)
    approved = reviews.approve_review(
        review_id,
        actor_id=operator_id,
        expected_subject_ref=subject_ref,
        expected_subject_version=subject_version,
        expected_resume_phase=ReviewResumePhase.PRE_EXECUTION,
        expected_checkpoint_id=f"checkpoint://{run_id}/{action}",
    )
    review_record = dict(approved["review"])
    review_item = dict(review_record.get("review") or {})
    return {
        "review_id": review_id,
        "status": str(review_item.get("status") or ""),
        "status_version": int(review_record["status_version"]),
        "decision_actor": str(review_item.get("decision_actor") or ""),
    }


def _run_host_turn(
    *,
    host_base_url: str,
    db_path: Path,
    session_id: str,
    timeout_s: float,
) -> dict[str, Any]:
    payload = {
        "message": "今天有点累，脑子也不想转了。",
        "workspace_id": "host-default",
        "person_id": "default",
        "session_id": session_id,
        "suppress_edge_tts": True,
        "suppress_edge_vts": True,
    }
    events: list[dict[str, Any]] = []
    started = time.perf_counter()
    with httpx.Client(timeout=timeout_s, trust_env=False) as client:
        with client.stream(
            "POST", host_base_url.rstrip("/") + "/v1/chat", json=payload
        ) as response:
            status_code = int(response.status_code)
            response.raise_for_status()
            for line in response.iter_lines():
                raw = str(line or "").strip()
                if not raw.startswith("data:"):
                    continue
                try:
                    event = json.loads(raw.removeprefix("data:").strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(event, Mapping):
                    events.append(dict(event))
    request_id = next(
        (
            str(event.get("request_id") or "")
            for event in reversed(events)
            if event.get("request_id")
        ),
        "",
    )
    render_text = next(
        (
            _event_text(event)
            for event in reversed(events)
            if str(event.get("phase") or "").upper() == "RENDER"
            and _event_text(event)
        ),
        "",
    )
    commit = next(
        (
            event
            for event in reversed(events)
            if str(event.get("phase") or "").upper() == "COMMIT"
        ),
        {},
    )
    attempt = _model_attempt(db_path, request_id)
    surface_style_anchors = _surface_style_anchors(
        db_path,
        request_id,
        timeout_s=min(10.0, max(1.0, timeout_s / 3.0)),
    )
    return {
        "status_code": status_code,
        "request_id": request_id,
        "session_id": session_id,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "render_text": render_text,
        "commit_status": str(commit.get("status") or ""),
        "commit_reason_code": str(commit.get("reason_code") or ""),
        "model": attempt,
        "surface_style_anchors": surface_style_anchors,
    }


def _surface_style_anchors(
    db_path: Path,
    request_id: str,
    *,
    timeout_s: float,
) -> list[str]:
    """Read the existing accepted foreground projection, without new audit."""

    if not request_id:
        return []
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                """
                SELECT payload_json
                FROM life_command
                WHERE correlation_id = ?
                  AND command_type = 'foreground_completed'
                ORDER BY created_at_ms DESC
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()
        if row is not None:
            try:
                payload = json.loads(str(row[0] or "{}"))
            except json.JSONDecodeError:
                return []
            reply_plan = dict(payload.get("reply_plan") or {})
            return [
                str(item).strip()
                for item in list(reply_plan.get("style_anchor") or [])
                if str(item).strip()
            ]
        if time.monotonic() >= deadline:
            return []
        time.sleep(0.05)


def _turn_expression_directive(turn: Mapping[str, Any]) -> str:
    for item in list(turn.get("surface_style_anchors") or []):
        text = str(item or "").strip()
        if text.startswith("已激活的长期表达偏好（本轮执行）："):
            return text
    return ""


def _expression_directive_digest(payload: Mapping[str, Any] | None) -> str:
    directive = expression_preference_directive(payload)
    if not directive:
        return ""
    return hashlib.sha256(directive.encode("utf-8")).hexdigest()


def _turn_prompt_directive_digest(turn: Mapping[str, Any]) -> str:
    return str(
        dict(turn.get("model") or {}).get(
            "surface_expression_directive_digest"
        )
        or ""
    ).strip()


def _model_attempt(db_path: Path, request_id: str) -> dict[str, Any]:
    if not request_id:
        return {}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT provider, model_id, status, latency_ms,
                   prompt_digest, metadata
            FROM model_attempt_log
            WHERE request_id = ?
            ORDER BY attempt_index DESC, id DESC
            LIMIT 1
            """,
            (request_id,),
        ).fetchone()
    if row is None:
        return {}
    try:
        metadata = json.loads(str(row["metadata"] or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    transport = (
        dict(metadata.get("provider_transport_measurements") or {})
        if isinstance(metadata, Mapping)
        else {}
    )
    usage = (
        dict(metadata.get("provider_budget_measurements") or {})
        if isinstance(metadata, Mapping)
        else {}
    )
    return {
        "provider": str(row["provider"] or ""),
        "model_id": str(row["model_id"] or ""),
        "status": str(row["status"] or ""),
        "latency_ms": int(row["latency_ms"] or 0),
        "prompt_digest": str(row["prompt_digest"] or ""),
        "surface_expression_directive_digest": str(
            metadata.get("surface_expression_directive_digest") or ""
        ),
        "transport_external": bool(transport.get("transport_external")),
        "transport_identity_digest": str(
            transport.get("transport_identity_digest") or ""
        ),
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "model_cost_micros": int(usage.get("model_cost_micros") or 0),
    }


def _turn_passed(turn: Mapping[str, Any]) -> bool:
    model = turn.get("model")
    return bool(
        int(turn.get("status_code") or 0) == 200
        and str(turn.get("request_id") or "")
        and str(turn.get("render_text") or "")
        and isinstance(model, Mapping)
        and str(model.get("status") or "") == "succeeded"
        and bool(model.get("transport_external"))
        and str(model.get("transport_identity_digest") or "")
    )


def _event_text(event: Mapping[str, Any]) -> str:
    for key in ("content", "text", "narrative", "reply", "chunk"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _require_exact_tracked_commit(expected: str) -> None:
    if len(expected) != 40 or any(
        character not in "0123456789abcdef" for character in expected
    ):
        raise RuntimeError("NE06_EXPRESSION_COMMIT_SHA_INVALID")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if (
        head.returncode != 0
        or status.returncode != 0
        or str(head.stdout or "").strip().lower() != expected
        or str(status.stdout or "").strip()
    ):
        raise RuntimeError("NE06_EXPRESSION_REQUIRES_EXACT_TRACKED_COMMIT")


if __name__ == "__main__":
    raise SystemExit(main())
