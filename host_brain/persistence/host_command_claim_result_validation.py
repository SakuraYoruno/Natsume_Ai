"""Claim-result validation mixed into the canonical HostCommand repository.

This module owns no Store, connection factory, transaction, or mutable state.
Every query runs on the exact ``sqlite3.Connection`` supplied by
``HostCommandRepository`` so admission and replay keep one persistence/UoW
authority.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import hmac
import json
import sqlite3
from typing import Any

from host_brain.contracts.agent_runtime.internal_budget_claim_result import (
    build_internal_budget_rejection_result_ref,
)
from host_brain.contracts.agent_runtime.internal_nonacceptance_claim_result import (
    INTERNAL_NONACCEPTANCE_TERMINAL_REASONS,
    build_internal_nonacceptance_result_ref,
)
from host_brain.persistence.host_command_repository_codec import (
    matches_review_decision_result,
    normalize_external_sha256,
    required_text,
    secure_ingress_surface,
)
from host_brain.persistence.host_command_repository_types import (
    HOST_COMMAND_INGRESS_CONFLICT,
    HOST_COMMAND_SCHEMA_NOT_READY,
    HostCommandRepositoryError,
)
from host_brain.persistence.worker_projection_repository import (
    WorkerProjectionStoreError,
    validate_worker_projection_record,
)


def matches_tool_terminal_result(
    conn: sqlite3.Connection,
    *,
    result_ref: str,
    command: Mapping[str, Any],
) -> bool:
    """Verify a Tool claim result against its immutable Task terminal state."""

    if str(result_ref or "").strip() == str(command.get("command_id") or "").strip():
        return _matches_tool_nonworker_terminal(conn, command=command)

    try:
        row = conn.execute(
            """
            SELECT projection.*,
                   node.status AS node_terminal_status,
                   attempt.status AS attempt_terminal_status,
                   graph.status AS graph_terminal_status,
                   reservation.status AS reservation_terminal_status,
                   reservation.receipt_ref AS reservation_receipt_ref
            FROM task_runtime_worker_projection AS projection
            JOIN task_runtime_node AS node
              ON node.graph_id = projection.graph_id
             AND node.graph_revision = projection.graph_revision
             AND node.node_id = projection.node_id
            JOIN task_runtime_attempt AS attempt
              ON attempt.attempt_id = projection.attempt_id
             AND attempt.graph_id = projection.graph_id
             AND attempt.graph_revision = projection.graph_revision
             AND attempt.node_id = projection.node_id
            JOIN task_runtime_graph AS graph
              ON graph.graph_id = projection.graph_id
             AND graph.revision = projection.graph_revision
            LEFT JOIN task_runtime_idempotency_reservation AS reservation
              ON reservation.attempt_id = projection.attempt_id
             AND reservation.receipt_ref = projection.projection_id
            WHERE projection.projection_id = ?
              AND projection.command_id = ?
              AND projection.request_id = ?
              AND projection.graph_id = ?
              AND projection.graph_revision = ?
              AND projection.route_type = 'tool'
              AND projection.target_id = ?
            LIMIT 1
            """,
            (
                str(result_ref or "").strip(),
                str(command.get("command_id") or "").strip(),
                str(command.get("request_id") or "").strip(),
                str(command.get("graph_id") or "").strip(),
                int(command.get("graph_revision") or 0),
                str(command.get("target_id") or "").strip(),
            ),
        ).fetchone()
    except (sqlite3.DatabaseError, TypeError, ValueError):
        return False
    if row is None:
        return False
    try:
        projection = validate_worker_projection_record(row)
    except WorkerProjectionStoreError:
        return False
    if str(projection.get("projection_id") or "") != str(result_ref or ""):
        return False
    node_status = str(row["node_terminal_status"] or "").upper()
    attempt_status = str(row["attempt_terminal_status"] or "").upper()
    graph_status = str(row["graph_terminal_status"] or "").upper()
    if (
        node_status not in {"ACCEPTED", "FAILED", "CANCELLED"}
        or attempt_status not in {"SUCCEEDED", "FAILED", "CANCELLED"}
        or graph_status not in {"COMPLETED", "FAILED", "CANCELLED"}
    ):
        return False
    if node_status == "ACCEPTED":
        return (
            str(row["reservation_terminal_status"] or "").upper() == "COMMITTED"
            and str(row["reservation_receipt_ref"] or "") == str(result_ref)
        )
    return True


def _matches_tool_nonworker_terminal(
    conn: sqlite3.Connection,
    *,
    command: Mapping[str, Any],
) -> bool:
    """Bind command-id fallback to a terminal Task with no worker receipt."""

    work_order = command.get("work_order")
    task_node_id = str(getattr(work_order, "task_node_id", "") or "").strip()
    if not task_node_id:
        return False
    try:
        row = conn.execute(
            """
            SELECT graph.status AS graph_status,
                   node.status AS node_status,
                   EXISTS(
                       SELECT 1 FROM task_runtime_worker_projection AS projection
                       WHERE projection.command_id = ?
                         AND projection.graph_id = node.graph_id
                         AND projection.graph_revision = node.graph_revision
                         AND projection.node_id = node.node_id
                   ) AS has_projection,
                   EXISTS(
                       SELECT 1 FROM task_runtime_attempt AS attempt
                       WHERE attempt.graph_id = node.graph_id
                         AND attempt.graph_revision = node.graph_revision
                         AND attempt.node_id = node.node_id
                         AND attempt.status IN (
                             'CREATED', 'LEASED', 'RUNNING', 'RECOVERABLE',
                             'CANCEL_REQUESTED'
                         )
                   ) AS has_live_attempt,
                   EXISTS(
                       SELECT 1 FROM task_runtime_budget_reservation AS budget
                       WHERE budget.graph_id = node.graph_id
                         AND budget.graph_revision = node.graph_revision
                         AND budget.node_id = node.node_id
                         AND budget.status IN ('PENDING', 'ACTIVE')
                   ) AS has_live_budget
            FROM task_runtime_graph AS graph
            JOIN task_runtime_node AS node
              ON node.graph_id = graph.graph_id
             AND node.graph_revision = graph.revision
            WHERE graph.graph_id = ? AND graph.revision = ? AND node.node_id = ?
            LIMIT 1
            """,
            (
                str(command.get("command_id") or "").strip(),
                str(command.get("graph_id") or "").strip(),
                int(command.get("graph_revision") or 0),
                task_node_id,
            ),
        ).fetchone()
    except (sqlite3.DatabaseError, TypeError, ValueError):
        return False
    return bool(
        row is not None
        and str(row["graph_status"] or "").upper() in {"FAILED", "CANCELLED"}
        and str(row["node_status"] or "").upper() in {"FAILED", "CANCELLED"}
        and int(row["has_projection"] or 0) == 0
        and int(row["has_live_attempt"] or 0) == 0
        and int(row["has_live_budget"] or 0) == 0
    )


def _matches_internal_budget_rejection_terminal(
    owner: Any,
    conn: sqlite3.Connection,
    *,
    result_ref: str,
    command: Mapping[str, Any],
) -> bool:
    """Bind a claim result to one exact Internal C-boundary rejection.

    This does not infer a terminal outcome merely from FAILED rows.  The
    canonical TaskStore must independently reopen the Internal checkpoint
    budget terminal, which verifies the committed worker operation, rejected
    usage, released budget/lease and all five terminal Outboxes.
    """

    work_order = command.get("work_order")
    node_id = str(getattr(work_order, "task_node_id", "") or "").strip()
    command_id = str(command.get("command_id") or "").strip()
    claim_ref = str(command.get("ingress_claim_ref") or "").strip()
    graph_id = str(command.get("graph_id") or "").strip()
    try:
        revision = int(command.get("graph_revision") or 0)
    except (TypeError, ValueError):
        return False
    if not all((node_id, command_id, claim_ref, graph_id)) or revision < 1:
        return False
    try:
        candidates = conn.execute(
            """
            SELECT attempt_id
            FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
              AND status = 'FAILED'
            ORDER BY attempt_id
            """,
            (graph_id, revision, node_id),
        ).fetchall()
    except sqlite3.DatabaseError:
        return False
    if len(candidates) != 1:
        return False
    task_store = getattr(owner, "_task_store", None)
    resolver = getattr(
        task_store,
        "resolve_internal_agent_budget_rejection_terminal",
        None,
    )
    if not callable(resolver):
        return False
    attempt_id = str(candidates[0]["attempt_id"] or "").strip()
    if not attempt_id:
        return False
    try:
        resolved = resolver(attempt_id)
    except Exception:
        return False
    if not isinstance(resolved, Mapping):
        return False
    attempt = resolved.get("attempt")
    node = resolved.get("node")
    graph = resolved.get("graph")
    reservation = resolved.get("reservation")
    budget = resolved.get("budget")
    usage = resolved.get("usage")
    if not all(
        isinstance(value, Mapping)
        for value in (attempt, node, graph, reservation, budget, usage)
    ):
        return False
    if (
        str(resolved.get("authority_kind") or "")
        != "INTERNAL_AGENT_BUDGET_REJECTED"
        or str(attempt.get("attempt_id") or "") != attempt_id
        or str(attempt.get("graph_id") or "") != graph_id
        or attempt.get("graph_revision") != revision
        or str(attempt.get("node_id") or "") != node_id
        or str(node.get("node_id") or "") != node_id
        or str(graph.get("graph_id") or "") != graph_id
        or graph.get("revision") != revision
    ):
        return False
    try:
        expected = build_internal_budget_rejection_result_ref(
            command_id=command_id,
            ingress_claim_ref=claim_ref,
            graph_id=graph_id,
            graph_revision=revision,
            node_id=node_id,
            attempt_id=attempt_id,
            reservation_id=str(reservation.get("reservation_id") or ""),
            budget_id=str(budget.get("budget_id") or ""),
            usage_id=str(usage.get("usage_id") or ""),
        )
    except ValueError:
        return False
    return hmac.compare_digest(str(result_ref or ""), expected)


def _matches_internal_nonacceptance_terminal(
    owner: Any,
    conn: sqlite3.Connection,
    *,
    result_ref: str,
    command: Mapping[str, Any],
) -> bool:
    """Bind a result to one exact executed-but-not-accepted checkpoint.

    A terminal Task status alone is insufficient: the canonical TaskStore
    reopens the committed projection and historical/final Lease lineage.  The
    resolver intentionally returns no authority for raw worker failure or
    pre-checkpoint cancellation.
    """

    work_order = command.get("work_order")
    node_id = str(getattr(work_order, "task_node_id", "") or "").strip()
    command_id = str(command.get("command_id") or "").strip()
    claim_ref = str(command.get("ingress_claim_ref") or "").strip()
    graph_id = str(command.get("graph_id") or "").strip()
    try:
        revision = int(command.get("graph_revision") or 0)
    except (TypeError, ValueError):
        return False
    if not all((node_id, command_id, claim_ref, graph_id)) or revision < 1:
        return False
    try:
        candidates = conn.execute(
            """
            SELECT attempt_id
            FROM task_runtime_attempt
            WHERE graph_id = ? AND graph_revision = ? AND node_id = ?
              AND status = 'SUCCEEDED'
            ORDER BY attempt_id
            """,
            (graph_id, revision, node_id),
        ).fetchall()
    except sqlite3.DatabaseError:
        return False
    task_store = getattr(owner, "_task_store", None)
    resolver = getattr(
        task_store,
        "try_resolve_internal_agent_nonacceptance_checkpoint_terminal",
        None,
    )
    if not callable(resolver):
        return False
    matches = 0
    for candidate in candidates:
        attempt_id = str(candidate["attempt_id"] or "").strip()
        if not attempt_id:
            return False
        try:
            terminal = resolver(attempt_id)
        except Exception:
            return False
        if terminal is None:
            continue
        if not isinstance(terminal, Mapping):
            return False
        historical = terminal.get("historical_lease")
        completion = terminal.get("completion_lease")
        reservation = terminal.get("operation_reservation")
        projection = terminal.get("projection")
        terminal_reason = str(terminal.get("terminal_reason") or "")
        if not all(
            isinstance(value, Mapping)
            for value in (historical, completion, reservation, projection)
        ):
            return False
        if (
            str(terminal.get("authority_kind") or "")
            != "INTERNAL_AGENT_NONACCEPTANCE_TERMINAL"
            or terminal_reason not in INTERNAL_NONACCEPTANCE_TERMINAL_REASONS
            or str(historical.get("attempt_id") or "") != attempt_id
            or str(completion.get("attempt_id") or "") != attempt_id
            or str(completion.get("status") or "") != "RELEASED"
            or str(reservation.get("attempt_id") or "") != attempt_id
            or str(projection.get("command_id") or "") != command_id
            or str(projection.get("graph_id") or "") != graph_id
            or int(projection.get("graph_revision") or 0) != revision
            or str(projection.get("node_id") or "") != node_id
            or str(projection.get("attempt_id") or "") != attempt_id
        ):
            return False
        try:
            expected = build_internal_nonacceptance_result_ref(
                command_id=command_id,
                ingress_claim_ref=claim_ref,
                graph_id=graph_id,
                graph_revision=revision,
                node_id=node_id,
                attempt_id=attempt_id,
                historical_lease_id=str(historical.get("lease_id") or ""),
                completion_lease_id=str(completion.get("lease_id") or ""),
                reservation_id=str(reservation.get("reservation_id") or ""),
                projection_id=str(projection.get("projection_id") or ""),
                terminal_reason=terminal_reason,
            )
        except ValueError:
            return False
        if hmac.compare_digest(str(result_ref or ""), expected):
            matches += 1
    return matches == 1


def _operator_domain_result_ref(*, command_id: str, claim_ref: str) -> str:
    normalized_command = str(command_id or "").strip()
    normalized_claim = str(claim_ref or "").strip()
    if not normalized_command or not normalized_claim:
        return ""
    digest = hashlib.sha256(
        f"{normalized_command}\n{normalized_claim}".encode("utf-8")
    ).hexdigest()
    command_path = normalized_command.replace("_", "-").replace(".", "/")
    return f"obs-command://result/{command_path}/{digest}"


def _outcome_value(record: Any, field: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(field)
    return getattr(record, field, None)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _matches_authenticated_operator_outcome(
    owner: Any,
    *,
    result_ref: str,
    claim_ref: str,
    audit_ref: str,
    operator_command_id: str,
    action: str,
    required_scopes_json: str,
    body_sha256: str,
) -> bool:
    """Bind an outer claim result to its checksum-chained OBS outcome record."""

    lookup = getattr(owner, "_authenticated_outcome_lookup", None)
    if not callable(lookup):
        return False
    try:
        record = lookup(result_ref)
    except Exception:
        return False
    if record is None:
        return False
    key = _outcome_value(record, "key")
    try:
        required_scopes = tuple(
            sorted({str(item) for item in json.loads(required_scopes_json)})
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    key_scopes = tuple(sorted(_outcome_value(key, "required_scopes") or ()))
    expected_domain_result = _operator_domain_result_ref(
        command_id=operator_command_id,
        claim_ref=claim_ref,
    )
    return bool(
        expected_domain_result
        and str(_outcome_value(record, "receipt_ref") or "") == result_ref
        and _enum_value(_outcome_value(record, "state")) == "SUCCEEDED"
        and str(_outcome_value(record, "claim_ref") or "") == claim_ref
        and str(_outcome_value(record, "audit_ref") or "") == audit_ref
        and str(_outcome_value(record, "domain_result_ref") or "")
        == expected_domain_result
        and str(_outcome_value(key, "command_id") or "")
        == operator_command_id
        and str(_outcome_value(key, "action") or "") == action
        and key_scopes == required_scopes
        and str(_outcome_value(key, "body_sha256") or "") == body_sha256
    )


class HostCommandClaimResultValidationMixin:
    """Connection-neutral claim validation behavior for HostCommandRepository."""

    def _validate_claim_result_record(self, command_id: str) -> dict[str, Any]:
        normalized_id = required_text(
            command_id,
            field_name="command_id",
            max_length=512,
        )
        conn = self._connect()
        try:
            record = self._validated_record(
                conn,
                self._require_row(conn, normalized_id),
            )
            canonical = self._normalize_admission(
                record["command"],
                record["work_order"],
                record["assignment"],
                record["graph"],
            )
            self._validate_ingress_claim(conn, canonical)
            return record
        finally:
            conn.close()

    def _validate_ingress_claim(
        self,
        conn: sqlite3.Connection,
        normalized: Mapping[str, Any],
    ) -> None:
        claim_ref = str(normalized.get("ingress_claim_ref") or "").strip()
        if not claim_ref:
            return
        try:
            row = conn.execute(
                """
                SELECT principal_id, surface, action, required_scopes_json,
                       request_id, body_sha256, claim_status, result_ref
                FROM secure_ingress_idempotency_claim
                WHERE claim_ref = ?
                """,
                (claim_ref,),
            ).fetchone()
            audit = conn.execute(
                """
                SELECT request_id, surface, principal_id, body_sha256,
                       decision, claim_ref, side_effects_executed
                FROM secure_ingress_audit
                WHERE audit_ref = ?
                """,
                (str(normalized.get("ingress_audit_ref") or ""),),
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise HostCommandRepositoryError(
                HOST_COMMAND_SCHEMA_NOT_READY,
                "Secure Ingress migration 0058 is unavailable",
            ) from exc
        if row is None or audit is None or str(row["claim_status"]) != "COMMITTED":
            raise HostCommandRepositoryError(
                HOST_COMMAND_INGRESS_CONFLICT,
                "ingress claim/audit is absent or the claim is not COMMITTED",
            )
        expected_result = str(normalized["command_id"])
        actual_result = str(row["result_ref"] or "").strip()
        surface = str(normalized["surface"])
        terminal_result_matches = True
        if surface == "internal" and actual_result:
            terminal_result_matches = (
                conn.execute(
                    """
                    SELECT 1 FROM agent_execution_result
                    WHERE receipt_ref = ? AND command_id = ? LIMIT 1
                    """,
                    (actual_result, expected_result),
                ).fetchone()
                is not None
            )
            if not terminal_result_matches:
                terminal_result_matches = matches_review_decision_result(
                    conn,
                    result_ref=actual_result,
                    command=normalized,
                )
            if not terminal_result_matches:
                terminal_result_matches = _matches_internal_budget_rejection_terminal(
                    self,
                    conn,
                    result_ref=actual_result,
                    command=normalized,
                )
            if not terminal_result_matches:
                terminal_result_matches = _matches_internal_nonacceptance_terminal(
                    self,
                    conn,
                    result_ref=actual_result,
                    command=normalized,
                )
        elif surface == "scheduler" and actual_result:
            terminal_result_matches = (
                conn.execute(
                    """
                    SELECT 1 FROM scheduler_execution_result
                    WHERE receipt_ref = ? AND command_id = ? LIMIT 1
                    """,
                    (actual_result, expected_result),
                ).fetchone()
                is not None
            )
            if not terminal_result_matches:
                terminal_result_matches = matches_review_decision_result(
                    conn,
                    result_ref=actual_result,
                    command=normalized,
                )
        elif surface == "operator" and actual_result:
            work_order = normalized.get("work_order")
            metadata = getattr(work_order, "metadata", None)
            metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
            operator_command_id = str(
                metadata.get("operator_command_id") or ""
            ).strip()
            terminal_result_matches = (
                _matches_authenticated_operator_outcome(
                    self,
                    result_ref=actual_result,
                    claim_ref=claim_ref,
                    audit_ref=str(normalized.get("ingress_audit_ref") or ""),
                    operator_command_id=operator_command_id,
                    action=str(row["action"] or ""),
                    required_scopes_json=str(row["required_scopes_json"] or ""),
                    body_sha256=str(row["body_sha256"] or ""),
                )
                and conn.execute(
                    """
                    SELECT 1 FROM host_command_journal
                    WHERE command_id = ? AND request_id = ?
                      AND surface = 'operator'
                      AND ingress_claim_ref = ? AND ingress_audit_ref = ?
                    LIMIT 1
                    """,
                    (
                        expected_result,
                        str(normalized["request_id"]),
                        claim_ref,
                        str(normalized.get("ingress_audit_ref") or ""),
                    ),
                ).fetchone()
                is not None
            )
        elif surface == "tool" and actual_result:
            terminal_result_matches = matches_tool_terminal_result(
                conn,
                result_ref=actual_result,
                command=normalized,
            )
            if not terminal_result_matches:
                terminal_result_matches = matches_review_decision_result(
                    conn,
                    result_ref=actual_result,
                    command=normalized,
                )
        claim_body_sha256 = normalize_external_sha256(
            row["body_sha256"],
            field_name="secure_ingress_idempotency_claim.body_sha256",
        )
        audit_body_sha256 = normalize_external_sha256(
            audit["body_sha256"],
            field_name="secure_ingress_audit.body_sha256",
        )
        governed_surfaces = {"internal", "scheduler", "operator", "tool"}
        mismatched = (
            str(row["principal_id"]) != str(normalized["principal_id"])
            or str(row["surface"]) != secure_ingress_surface(surface)
            or str(row["request_id"]) != str(normalized["request_id"])
            or claim_body_sha256 != str(normalized["input_sha256"])
            or (surface in governed_surfaces and not terminal_result_matches)
            or (
                surface not in governed_surfaces
                and actual_result not in {"", expected_result}
            )
        )
        audit_mismatched = (
            str(audit["request_id"] or "") != str(normalized["request_id"])
            or str(audit["surface"]) != secure_ingress_surface(surface)
            or str(audit["principal_id"] or "") != str(normalized["principal_id"])
            or audit_body_sha256 != str(normalized["input_sha256"])
            or str(audit["decision"]) != "ALLOW"
            or str(audit["claim_ref"] or "") != claim_ref
            or int(audit["side_effects_executed"]) != 0
        )
        if mismatched or audit_mismatched:
            raise HostCommandRepositoryError(
                HOST_COMMAND_INGRESS_CONFLICT,
                "ingress claim binding does not match the command",
            )


__all__ = [
    "HostCommandClaimResultValidationMixin",
    "matches_tool_terminal_result",
]
