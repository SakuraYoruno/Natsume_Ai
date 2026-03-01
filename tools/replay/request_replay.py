"""Request-level replay report builder for audit chain diagnostics."""

from __future__ import annotations

import json
from typing import Any

from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    TRACE_MISSING_APPROVAL_TOKEN_REF,
    TRACE_MISSING_GOVERNOR,
    TRACE_MISSING_INTENT,
    TRACE_MISSING_SKILL_EXEC,
)


S_REPLAY_REPORT = "S_REPLAY_REPORT"

STATUS_COMPLETE = "COMPLETE"
STATUS_COMPLETE_REJECTED = "COMPLETE_REJECTED"
STATUS_INCOMPLETE = "INCOMPLETE"


def build_request_replay_report(*, ledger: SQLiteLedger, request_id: str) -> dict[str, Any]:
    intent_rows = _by_request(ledger.intent_log, request_id)
    governor_rows = _by_request(ledger.governor_log, request_id)
    skill_rows = _by_request(ledger.skill_exec_log, request_id)
    error_rows = _by_request(ledger.error_log, request_id)
    qa_rows = _by_request(ledger.qa_violation_log, request_id)

    latest_governor = _latest_by_ts(governor_rows)
    issues = _detect_chain_issues(
        intent_rows=intent_rows,
        governor_rows=governor_rows,
        skill_rows=skill_rows,
        latest_governor=latest_governor,
    )

    status, reason_code = _resolve_status_and_reason(latest_governor=latest_governor, issues=issues)
    reason_code_chain = _build_reason_code_chain(
        issues=issues,
        governor_rows=governor_rows,
        skill_rows=skill_rows,
        error_rows=error_rows,
        qa_rows=qa_rows,
    )
    evidence_refs = sorted(
        {
            str(row.get("evidence_ref") or "").strip()
            for row in skill_rows
            if str(row.get("evidence_ref") or "").strip()
        }
    )

    return {
        "request_id": request_id,
        "stage": S_REPLAY_REPORT,
        "status": status,
        "reason_code": reason_code,
        "summary": {
            "status": status,
            "issue_count": len(issues),
            "intent_count": len(intent_rows),
            "governor_count": len(governor_rows),
            "skill_exec_count": len(skill_rows),
            "error_count": len(error_rows),
            "qa_violation_count": len(qa_rows),
            "reason_code_chain": reason_code_chain,
            "evidence_refs": evidence_refs,
        },
        "issues": issues,
        "triptych": {
            "intent_log": intent_rows,
            "governor_log": governor_rows,
            "skill_exec_log": skill_rows,
        },
        "error_log": error_rows,
        "qa_violation_log": qa_rows,
        "latest_governor": latest_governor,
    }


def build_request_replay_report_from_db(*, db_path: str, request_id: str) -> dict[str, Any]:
    ledger = SQLiteLedger(db_path=db_path)
    try:
        return build_request_replay_report(ledger=ledger, request_id=request_id)
    finally:
        ledger.close()


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if len(args) < 2:
        print(
            json.dumps(
                {
                    "stage": S_REPLAY_REPORT,
                    "status": STATUS_INCOMPLETE,
                    "reason_code": TRACE_MISSING_INTENT,
                    "detail": "usage: request_replay.py <db_path> <request_id>",
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    report = build_request_replay_report_from_db(db_path=args[0], request_id=args[1])
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _detect_chain_issues(
    *,
    intent_rows: list[dict[str, Any]],
    governor_rows: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    latest_governor: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not intent_rows:
        issues.append(
            {
                "code": TRACE_MISSING_INTENT,
                "detail": "intent_log row missing for request_id",
                "stage": "S_INTENT",
            }
        )
    if intent_rows and not governor_rows:
        issues.append(
            {
                "code": TRACE_MISSING_GOVERNOR,
                "detail": "governor_log row missing for request_id",
                "stage": "S_GOVERNOR",
            }
        )

    decision = str((latest_governor or {}).get("decision") or "").upper()
    if decision == "APPROVE" and not skill_rows:
        issues.append(
            {
                "code": TRACE_MISSING_SKILL_EXEC,
                "detail": "skill_exec_log row missing for approved decision",
                "stage": "S_SKILL",
            }
        )

    if decision == "APPROVE":
        for row in skill_rows:
            result_status = str(row.get("result_status") or "").upper()
            if result_status not in {"SUCCESS", "IDEMPOTENT_REPLAY"}:
                continue
            approval_ref = str(row.get("approval_token_ref") or "").strip()
            if approval_ref:
                continue
            issues.append(
                {
                    "code": TRACE_MISSING_APPROVAL_TOKEN_REF,
                    "detail": "skill_exec success row missing approval_token_ref",
                    "stage": "S_SKILL",
                }
            )
            break

    return issues


def _resolve_status_and_reason(
    *,
    latest_governor: dict[str, Any] | None,
    issues: list[dict[str, Any]],
) -> tuple[str, str | None]:
    if issues:
        return STATUS_INCOMPLETE, str(issues[0]["code"])

    decision = str((latest_governor or {}).get("decision") or "").upper()
    if decision == "REJECT":
        governor_reason = str((latest_governor or {}).get("reason_code") or "").strip() or None
        return STATUS_COMPLETE_REJECTED, governor_reason
    if decision == "APPROVE":
        return STATUS_COMPLETE, None
    return STATUS_INCOMPLETE, TRACE_MISSING_GOVERNOR


def _build_reason_code_chain(
    *,
    issues: list[dict[str, Any]],
    governor_rows: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    qa_rows: list[dict[str, Any]],
) -> list[str]:
    chain: list[str] = []

    for issue in issues:
        _append_reason(chain, issue.get("code"))
    for row in governor_rows:
        _append_reason(chain, row.get("reason_code"))
    for row in skill_rows:
        _append_reason(chain, row.get("result_code"))
    for row in error_rows:
        _append_reason(chain, row.get("reason_code"))
    for row in qa_rows:
        _append_reason(chain, row.get("code"))
    return chain


def _append_reason(chain: list[str], raw: Any) -> None:
    reason = str(raw or "").strip()
    if not reason:
        return
    if reason in chain:
        return
    chain.append(reason)


def _by_request(rows: list[dict[str, Any]], request_id: str) -> list[dict[str, Any]]:
    filtered = [dict(row) for row in rows if str(row.get("request_id") or "") == request_id]
    filtered.sort(key=lambda row: int(row.get("ts_ms") or 0))
    return filtered


def _latest_by_ts(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    ordered = sorted(rows, key=lambda row: int(row.get("ts_ms") or 0))
    return dict(ordered[-1])


if __name__ == "__main__":
    raise SystemExit(main())
