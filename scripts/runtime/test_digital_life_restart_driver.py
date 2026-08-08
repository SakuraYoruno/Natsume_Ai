#!/usr/bin/env python
"""Exercise reunion/open-loop dialogue across a real Host restart.

This driver uses the local personal ``POST /v1/chat`` surface.  It proves that
the real model, accepted Life command, relationship episode, and aftereffect
survive replacement of the Host process.  It intentionally does not claim
Gateway or TaskGraph coverage; those are measured by the separate live cohort.

Execution is split into ``prepare`` and ``resume`` so this program never stops
OBS, Monitor, Gateway, Docker, or a remote Edge.  The local personal surface
has one relationship subject and one current unresolved thread, so each batch
accepts exactly one variant; independent scenarios must not overwrite one
another before the restart.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence
from urllib.request import urlopen
import uuid

import httpx


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.runtime.test_digital_life_live_cohort import (  # noqa: E402
    DEFAULT_EXECUTION_MANIFEST,
    load_executable_cases,
)


SCHEMA = "natsume.digital-life-restart-driver.v1"
FAMILY_ID = "reunion_and_open_loop"
TESTED_CODE_PATHS = (
    Path(__file__).resolve(),
    ROOT / "host_brain" / "service_catalog" / "host_startup.py",
    ROOT / "scripts" / "runtime" / "start_host_side.ps1",
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tested_code_fingerprint() -> dict[str, Any]:
    files = {
        path.relative_to(ROOT).as_posix(): _sha256_bytes(path.read_bytes())
        for path in TESTED_CODE_PATHS
    }
    encoded = json.dumps(
        files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    dirty = bool(
        subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *files,
            ],
            cwd=ROOT,
            text=True,
        ).strip()
    )
    return {
        "base_commit_sha": _git_sha(),
        "worktree_dirty": dirty,
        "files": files,
        "fingerprint_sha256": _sha256_bytes(encoded),
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        dict(payload), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _read_host_pid(runtime_root: Path) -> int:
    pid = int((runtime_root / "host.pid").read_text(encoding="ascii").strip())
    if pid <= 0:
        raise RuntimeError("HOST_PID_INVALID")
    return pid


def _health_snapshot(host_base_url: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, path in (("health", "/health"), ("ready", "/ready")):
        try:
            with urlopen(host_base_url.rstrip("/") + path, timeout=8) as response:
                body = json.loads(response.read().decode("utf-8"))
                result[name] = {
                    "http_status": int(response.status),
                    "payload": body if isinstance(body, Mapping) else {},
                }
        except Exception as exc:  # noqa: BLE001 - retain only failure kind
            result[name] = {
                "http_status": 0,
                "error_type": type(exc).__name__,
            }
    return result


def _host_is_ready(snapshot: Mapping[str, Any]) -> bool:
    health = dict(snapshot.get("health") or {})
    ready = dict(snapshot.get("ready") or {})
    health_payload = dict(health.get("payload") or {})
    ready_payload = dict(ready.get("payload") or {})
    if int(health.get("http_status") or 0) != 200:
        return False
    if int(ready.get("http_status") or 0) != 200:
        return False
    health_status = str(health_payload.get("status") or "").upper()
    ready_status = str(ready_payload.get("status") or "").upper()
    if health_status in {"OK", "HEALTHY"} and (
        ready_payload.get("ready") is True or ready_status in {"OK", "READY"}
    ):
        return True

    # This driver deliberately leaves the remote Edge offline.  Host /ready
    # therefore reports ACCEPTANCE_PENDING_EXTERNAL even when every local
    # runtime component is usable.  Accept only that narrow shape: no actual
    # unavailable dependency and every not-tested dependency belongs to Edge.
    if health_status != "ACCEPTANCE_PENDING_EXTERNAL" or ready_status != health_status:
        return False
    health_summary = dict(
        dict(health_payload.get("details") or {}).get("summary") or {}
    )
    ready_summary = dict(ready_payload.get("summary") or {})
    dependency_summary = dict(ready_summary.get("dependency_summary") or {})
    raw_not_tested_ids = dependency_summary.get("not_tested_ids") or ()
    if isinstance(raw_not_tested_ids, str):
        not_tested_ids = {
            value for value in raw_not_tested_ids.split() if value
        }
    elif isinstance(raw_not_tested_ids, Sequence):
        not_tested_ids = {
            str(value).strip() for value in raw_not_tested_ids if str(value).strip()
        }
    else:
        not_tested_ids = set()
    return (
        int(health_summary.get("required_unavailable") or 0) == 0
        and int(health_summary.get("runtime_component_unavailable") or 0) == 0
        and int(dependency_summary.get("unavailable") or 0) == 0
        and int(ready_summary.get("runtime_component_unavailable") or 0) == 0
        and bool(not_tested_ids)
        and all(value.startswith("edge.rpc.") for value in not_tested_ids)
    )


def _relationship_snapshot(db_path: Path, relationship_subject_id: str) -> dict[str, Any]:
    subject = str(relationship_subject_id or "").strip()
    if not subject:
        return {}
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        event = connection.execute(
            """
            SELECT person_id, state_revision, event_digest
            FROM relationship_life_event_v1
            WHERE relationship_subject_id = ?
            ORDER BY state_revision DESC, created_at_ms DESC
            LIMIT 1
            """,
            (subject,),
        ).fetchone()
        if event is None:
            return {}
        state = connection.execute(
            "SELECT version, state_json FROM digital_life_state WHERE person_id = ?",
            (str(event["person_id"]),),
        ).fetchone()
        if state is None:
            return {}
    state_json = str(state["state_json"] or "{}")
    payload = json.loads(state_json)
    relationship = dict(payload.get("relationship_v2") or {})
    active = [
        {
            "episode_id": str(row.get("episode_id") or ""),
            "response_receipt_ref": str(row.get("response_receipt_ref") or ""),
            "session_id": str(row.get("session_id") or ""),
            "source_kind": str(row.get("source_kind") or ""),
        }
        for row in list(relationship.get("active_episodes") or [])
        if isinstance(row, Mapping)
    ]
    last_aftereffect = dict(relationship.get("last_aftereffect") or {})
    return {
        "relationship_subject_id": subject,
        "aggregate_person_id": str(event["person_id"]),
        "state_revision": int(event["state_revision"] or 0),
        "state_version": int(state["version"] or 0),
        "state_digest": _sha256_bytes(state_json.encode("utf-8")),
        "event_digest": str(event["event_digest"] or ""),
        "active_episodes": active,
        "last_aftereffect_episode_id": str(last_aftereffect.get("episode_id") or ""),
        "last_aftereffect_source_receipt_ref": str(
            last_aftereffect.get("source_receipt_ref") or ""
        ),
        "repair_required": bool(relationship.get("repair_required")),
        "proactive_permission": str(relationship.get("proactive_permission") or ""),
    }


def _episode_for_session(
    snapshot: Mapping[str, Any], session_id: str
) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in list(snapshot.get("active_episodes") or [])
        if isinstance(row, Mapping)
        and str(row.get("session_id") or "") == str(session_id or "")
    ]
    return rows[-1] if rows else {}


def _wait_for_session_episode(
    db_path: Path,
    *,
    subject_id: str,
    session_id: str,
    min_revision: int,
    timeout_s: float,
) -> dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_s))
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        latest = _relationship_snapshot(db_path, subject_id)
        if (
            int(latest.get("state_revision") or 0) >= int(min_revision)
            and _episode_for_session(latest, session_id)
        ):
            return latest
        time.sleep(0.5)
    return latest


def _wait_for_life_commit(
    db_path: Path, request_id: str, *, timeout_s: float
) -> dict[str, Any]:
    deadline = time.time() + max(1.0, float(timeout_s))
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    while time.time() < deadline:
        with sqlite3.connect(uri, uri=True, timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT c.command_id, c.person_id, c.payload_json,
                       r.status, r.state_version, r.state_digest
                FROM life_command AS c
                LEFT JOIN life_command_receipt AS r ON r.command_id = c.command_id
                WHERE c.command_type = 'foreground_completed'
                  AND c.payload_json LIKE ?
                ORDER BY c.created_at_ms DESC
                LIMIT 1
                """,
                (f'%"request_id":"{request_id}"%',),
            ).fetchone()
        if row is not None and str(row["status"] or "").upper() == "APPLIED":
            payload = json.loads(str(row["payload_json"] or "{}"))
            return {
                "command_id": str(row["command_id"]),
                "person_id": str(payload.get("person_id") or row["person_id"] or ""),
                "workspace_id": str(payload.get("workspace_id") or ""),
                "session_id": str(payload.get("session_id") or ""),
                "request_id": str(payload.get("request_id") or ""),
                "receipt_status": str(row["status"] or ""),
                "state_version": int(row["state_version"] or 0),
                "state_digest": str(row["state_digest"] or ""),
            }
        time.sleep(0.25)
    return {}


def _model_attempt_summary(db_path: Path, request_id: str) -> list[dict[str, Any]]:
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT attempt_index, status, provider, model_id, latency_ms, error_code
            FROM model_attempt_log
            WHERE request_id = ? AND status <> 'started'
            ORDER BY attempt_index, ended_at_ms
            """,
            (request_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _event_text(event: Mapping[str, Any]) -> str:
    for key in ("content", "text", "narrative", "reply", "chunk"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _run_host_turn(
    *,
    host_base_url: str,
    db_path: Path,
    message: str,
    session_id: str,
    timeout_s: float,
    life_wait_s: float,
) -> dict[str, Any]:
    client_request_id = f"req_{uuid.uuid4().hex}"
    payload = {
        "message": message,
        "session_id": session_id,
        "request_id": client_request_id,
        "suppress_edge_tts": True,
        "suppress_edge_vts": True,
    }
    started = time.perf_counter()
    events: list[dict[str, Any]] = []
    http_status = 0
    error_type = ""
    try:
        with httpx.Client(timeout=timeout_s, trust_env=False) as client:
            with client.stream(
                "POST", host_base_url.rstrip("/") + "/v1/chat", json=payload
            ) as response:
                http_status = int(response.status_code)
                response.raise_for_status()
                for raw_line in response.iter_lines():
                    line = str(raw_line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line.removeprefix("data:").strip())
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, Mapping):
                        events.append(dict(event))
    except Exception as exc:  # noqa: BLE001 - record only the failure class
        error_type = type(exc).__name__
    server_request_id = next(
        (
            str(event.get("request_id") or "")
            for event in reversed(events)
            if event.get("request_id")
        ),
        "",
    )
    render_events = [event for event in events if str(event.get("phase") or "") == "RENDER"]
    commit = next(
        (event for event in reversed(events) if str(event.get("phase") or "") == "COMMIT"),
        {},
    )
    visible_text = next(
        (text for text in (_event_text(event) for event in reversed(render_events)) if text),
        "",
    )
    life_commit = (
        _wait_for_life_commit(db_path, server_request_id, timeout_s=life_wait_s)
        if server_request_id
        else {}
    )
    attempts = (
        _model_attempt_summary(db_path, server_request_id) if server_request_id else []
    )
    completed = (
        http_status == 200
        and not error_type
        and bool(render_events)
        and str(commit.get("decision") or "").upper() == "APPROVE"
        and str(life_commit.get("receipt_status") or "").upper() == "APPLIED"
        and str(life_commit.get("session_id") or "") == session_id
    )
    return {
        "message": message,
        "client_request_id": client_request_id,
        "request_id": server_request_id,
        "session_id": session_id,
        "http_status": http_status,
        "error_type": error_type,
        "phases": [str(event.get("phase") or "") for event in events],
        "commit_decision": str(commit.get("decision") or ""),
        "visible_text": visible_text,
        "life_commit": life_commit,
        "model_attempts": attempts,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "completed": completed,
    }


def restart_continuity_failures(
    *,
    host_pid_before: int,
    host_pid_after: int,
    recovered: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    target_episode_id: str,
    target_response_receipt_ref: str,
) -> list[str]:
    failures: list[str] = []
    if host_pid_before <= 0 or host_pid_after <= 0 or host_pid_before == host_pid_after:
        failures.append("HOST_PROCESS_NOT_REPLACED")
    if not before or not recovered:
        failures.append("RELATIONSHIP_STATE_NOT_RECOVERED")
        return failures
    for key in (
        "aggregate_person_id",
        "state_revision",
        "state_version",
        "state_digest",
        "event_digest",
        "active_episodes",
        "repair_required",
        "proactive_permission",
    ):
        if recovered.get(key) != before.get(key):
            failures.append(f"RECOVERED_{key.upper()}_DRIFT")
    if not after:
        failures.append("RELATIONSHIP_STATE_NOT_ADVANCED")
        return failures
    if int(after.get("state_revision") or 0) <= int(before.get("state_revision") or 0):
        failures.append("RELATIONSHIP_REVISION_NOT_ADVANCED")
    if not target_episode_id or not target_response_receipt_ref:
        failures.append("PRE_RESTART_OPEN_EPISODE_MISSING")
    else:
        if str(after.get("last_aftereffect_episode_id") or "") != target_episode_id:
            failures.append("POST_RESTART_AFTEREFFECT_EPISODE_DRIFT")
        if (
            str(after.get("last_aftereffect_source_receipt_ref") or "")
            != target_response_receipt_ref
        ):
            failures.append("POST_RESTART_AFTEREFFECT_RECEIPT_DRIFT")
    return failures


def _selected_cases(manifest: Path, variants: Sequence[int]) -> tuple[Any, ...]:
    selected = tuple(
        case
        for case in load_executable_cases(manifest)
        if case.family_id == FAMILY_ID and case.variant_index in set(variants)
    )
    if len(selected) != len(set(variants)):
        raise RuntimeError("RESTART_VARIANT_SELECTION_INVALID")
    return selected


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def prepare(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path).resolve()
    runtime_root = Path(args.runtime_root).resolve()
    cases = _selected_cases(Path(args.execution_manifest), args.variant)
    run_id = f"dl-restart-{int(time.time() * 1000)}"
    outcomes: list[dict[str, Any]] = []
    relationship_subjects: set[str] = set()
    for case in cases:
        session_id = f"{run_id}-{case.family_id}-{case.variant_index}"
        turns: list[dict[str, Any]] = []
        for message in case.setup_turns:
            turn = _run_host_turn(
                host_base_url=str(args.host_base_url),
                db_path=db_path,
                message=message,
                session_id=session_id,
                timeout_s=float(args.timeout_s),
                life_wait_s=float(args.life_wait_s),
            )
            turns.append(turn)
            if not turn["completed"]:
                break
        subject = str(dict(turns[-1].get("life_commit") or {}).get("person_id") or "") if turns else ""
        if subject:
            relationship_subjects.add(subject)
        outcomes.append(
            {
                "case": asdict(case),
                "session_id": session_id,
                "subject_id": subject,
                "setup_turns": turns,
            }
        )
    if len(relationship_subjects) != 1:
        aggregate = {}
    else:
        subject = next(iter(relationship_subjects))
        deadline = time.time() + max(1.0, float(args.relationship_wait_s))
        aggregate = {}
        while time.time() < deadline:
            aggregate = _relationship_snapshot(db_path, subject)
            if aggregate and all(
                _episode_for_session(aggregate, str(row["session_id"]))
                for row in outcomes
            ):
                break
            time.sleep(0.5)
    for row in outcomes:
        row["pre_restart_target_episode"] = _episode_for_session(
            aggregate, str(row["session_id"])
        )
        row["prepare_completed"] = (
            len(row["setup_turns"]) == len(dict(row["case"]).get("setup_turns") or [])
            and all(bool(turn.get("completed")) for turn in row["setup_turns"])
            and bool(row["pre_restart_target_episode"])
        )
    payload = {
        "schema": SCHEMA,
        "phase": "PREPARED",
        "transport": "HOST_DIRECT_PERSONAL_SURFACE",
        "gateway_covered_by_this_driver": False,
        "task_graph_covered_by_this_driver": False,
        "run_id": run_id,
        "commit_sha": _git_sha(),
        "tested_code": _tested_code_fingerprint(),
        "created_at_ms": int(time.time() * 1000),
        "host_pid_before": _read_host_pid(runtime_root),
        "host_health_before": _health_snapshot(str(args.host_base_url)),
        "remote_edge_contacted": False,
        "db_path": str(db_path),
        "runtime_root": str(runtime_root),
        "pre_restart_relationship": aggregate,
        "outcomes": outcomes,
        "prepare_status": (
            "PASS" if all(row["prepare_completed"] for row in outcomes) else "FAIL"
        ),
    }
    checkpoint = Path(args.checkpoint).resolve()
    _atomic_json(checkpoint, payload)
    print(json.dumps({
        "phase": "PREPARED",
        "status": payload["prepare_status"],
        "run_id": run_id,
        "variants": list(args.variant),
        "checkpoint": str(checkpoint),
    }, ensure_ascii=False))
    return 0 if payload["prepare_status"] == "PASS" else 1


def resume(args: argparse.Namespace) -> int:
    checkpoint = Path(args.checkpoint).resolve()
    prepared = json.loads(checkpoint.read_text(encoding="utf-8"))
    if str(prepared.get("schema") or "") != SCHEMA or str(prepared.get("phase") or "") != "PREPARED":
        raise RuntimeError("RESTART_CHECKPOINT_INVALID")
    if str(prepared.get("commit_sha") or "") != _git_sha():
        raise RuntimeError("RESTART_CHECKPOINT_COMMIT_DRIFT")
    if dict(prepared.get("tested_code") or {}) != _tested_code_fingerprint():
        raise RuntimeError("RESTART_CHECKPOINT_CODE_DRIFT")
    runtime_root = Path(args.runtime_root).resolve()
    db_path = Path(args.db_path).resolve()
    host_pid_before = int(prepared.get("host_pid_before") or 0)
    host_pid_after = _read_host_pid(runtime_root)
    health_after = _health_snapshot(str(args.host_base_url))
    before = dict(prepared.get("pre_restart_relationship") or {})
    subject_id = str(before.get("relationship_subject_id") or "")
    recovered = _relationship_snapshot(db_path, subject_id)
    outcomes: list[dict[str, Any]] = []
    for raw in list(prepared.get("outcomes") or []):
        case = dict(raw.get("case") or {})
        target = dict(raw.get("pre_restart_target_episode") or {})
        case_before = _relationship_snapshot(db_path, subject_id)
        probe = _run_host_turn(
            host_base_url=str(args.host_base_url),
            db_path=db_path,
            message=str(case.get("probe_turn") or ""),
            session_id=str(raw.get("session_id") or ""),
            timeout_s=float(args.timeout_s),
            life_wait_s=float(args.life_wait_s),
        )
        after = _wait_for_session_episode(
            db_path,
            subject_id=subject_id,
            session_id=str(raw.get("session_id") or ""),
            min_revision=int(case_before.get("state_revision") or 0) + 1,
            timeout_s=float(args.relationship_wait_s),
        )
        failures = restart_continuity_failures(
            host_pid_before=host_pid_before,
            host_pid_after=host_pid_after,
            recovered=recovered,
            before=before,
            after=after,
            target_episode_id=str(target.get("episode_id") or ""),
            target_response_receipt_ref=str(target.get("response_receipt_ref") or ""),
        )
        if not bool(probe.get("completed")):
            failures.append("POST_RESTART_PROBE_NOT_COMPLETED")
        if str(dict(probe.get("life_commit") or {}).get("person_id") or "") != subject_id:
            failures.append("RELATIONSHIP_SUBJECT_DRIFT")
        if not _host_is_ready(health_after):
            failures.append("HOST_NOT_READY_AFTER_RESTART")
        outcomes.append({
            **raw,
            "probe_turn": probe,
            "post_probe_relationship": after,
            "verdict": {
                "status": "FAIL" if failures else "AUTOMATED_PASS_HUMAN_REVIEW_PENDING",
                "failures": failures,
                "restart_continuity": "PASS" if not failures else "FAIL",
            },
        })
    passed = sum(
        str(dict(row.get("verdict") or {}).get("status") or "")
        == "AUTOMATED_PASS_HUMAN_REVIEW_PENDING"
        for row in outcomes
    )
    report = {
        "schema": SCHEMA,
        "phase": "COMPLETED",
        "transport": "HOST_DIRECT_PERSONAL_SURFACE",
        "gateway_covered_by_this_driver": False,
        "task_graph_covered_by_this_driver": False,
        "run_id": str(prepared.get("run_id") or ""),
        "commit_sha": _git_sha(),
        "tested_code": prepared.get("tested_code"),
        "completed_at_ms": int(time.time() * 1000),
        "host_pid_before": host_pid_before,
        "host_pid_after": host_pid_after,
        "host_health_before": prepared.get("host_health_before"),
        "host_health_after": health_after,
        "remote_edge_contacted": False,
        "recovered_relationship": recovered,
        "outcomes": outcomes,
        "summary": {
            "case_count": len(outcomes),
            "automated_pass_human_review_pending": int(passed),
            "failed": len(outcomes) - int(passed),
            "host_process_replaced": host_pid_before != host_pid_after,
            "host_ready_after_restart": _host_is_ready(health_after),
            "status": "AUTOMATED_PASS_HUMAN_REVIEW_PENDING" if passed == len(outcomes) else "FAIL",
        },
    }
    report_path = checkpoint.with_name(checkpoint.stem + "-final.json")
    _atomic_json(report_path, report)
    report_sha = _sha256_bytes(report_path.read_bytes())
    print(json.dumps({
        "phase": "COMPLETED",
        "status": report["summary"]["status"],
        "case_count": len(outcomes),
        "passed": int(passed),
        "report": str(report_path),
        "report_sha256": report_sha,
    }, ensure_ascii=False))
    return 0 if passed == len(outcomes) else 1


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run reunion/open-loop cases across a real Host restart."
    )
    parser.add_argument("phase", choices=("prepare", "resume"))
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--execution-manifest", default=str(DEFAULT_EXECUTION_MANIFEST))
    parser.add_argument("--host-base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--variant", type=int, action="append", required=True)
    parser.add_argument("--timeout-s", type=float, default=90.0)
    parser.add_argument("--life-wait-s", type=float, default=30.0)
    parser.add_argument("--relationship-wait-s", type=float, default=30.0)
    args = parser.parse_args(argv)
    variants = tuple(dict.fromkeys(int(value) for value in args.variant))
    if len(variants) != 1 or any(value < 1 or value > 6 for value in variants):
        parser.error("select exactly one variant in the range 1..6")
    args.variant = variants
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return prepare(args) if args.phase == "prepare" else resume(args)
    except Exception as exc:  # noqa: BLE001 - redact traceback and private state
        print(json.dumps({
            "status": "ERROR",
            "reason_code": f"{type(exc).__name__}:{str(exc)[:200]}",
        }, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
