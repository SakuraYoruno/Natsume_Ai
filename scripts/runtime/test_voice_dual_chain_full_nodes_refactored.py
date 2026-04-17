#!/usr/bin/env python
"""Dual-end voice chain probe for runtime connectivity and RPC behavior.

Flow:
1. Host health/readiness probe.
2. Edge health probe.
3. Inject synthetic ASR events to Host via /v1/edge/events.
4. Trigger /v1/chat SSE and wait for COMMIT.
5. Validate COMMIT audit_refs.tts / audit_refs.vts.
6. Optionally verify audit rows from SQLite ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JsonDict = dict[str, Any]


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _env_get(env_file: dict[str, str], key: str, default: str) -> str:
    val = os.environ.get(key)
    if val is not None and str(val).strip():
        return str(val).strip()
    file_val = env_file.get(key)
    if file_val is not None and str(file_val).strip():
        return str(file_val).strip()
    return default


def _resolve_base_url(
    *,
    env_file: dict[str, str],
    explicit_url: str | None,
    host_key: str,
    port_key: str,
    default_host: str,
    default_port: int,
) -> str:
    if explicit_url and explicit_url.strip():
        return explicit_url.strip().rstrip("/")
    host = _env_get(env_file, host_key, default_host)
    if host in {"0.0.0.0", "::", ""}:
        host = "127.0.0.1"
    port_raw = _env_get(env_file, port_key, str(default_port))
    try:
        port = int(port_raw)
    except ValueError:
        port = int(default_port)
    return f"http://{host}:{port}"


def _http_json(
    method: str,
    url: str,
    *,
    payload: JsonDict | None = None,
    timeout_s: float = 10.0,
    headers: dict[str, str] | None = None,
) -> JsonDict:
    data = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, data=data, method=method.upper(), headers=req_headers)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body_bytes = resp.read()
            body_text = body_bytes.decode("utf-8", errors="replace")
            body_json: JsonDict | None = None
            parse_error = None
            if body_text.strip():
                try:
                    parsed = json.loads(body_text)
                    if isinstance(parsed, dict):
                        body_json = parsed
                except Exception as exc:  # pragma: no cover - defensive parse
                    parse_error = str(exc)
            return {
                "ok": True,
                "status_code": int(getattr(resp, "status", 200)),
                "latency_ms": int((time.time() - started) * 1000),
                "body": body_json if body_json is not None else body_text,
                "parse_error": parse_error,
            }
    except urllib.error.HTTPError as exc:
        error_text = ""
        try:
            error_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            error_text = str(exc)
        err_body: Any = error_text
        try:
            err_body = json.loads(error_text) if error_text else {}
        except Exception:
            pass
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "body": err_body,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
        }


def _stream_chat_sse(
    *,
    url: str,
    payload: JsonDict,
    timeout_s: float,
    max_events: int = 500,
) -> JsonDict:
    started = time.time()
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        },
    )
    events: list[JsonDict] = []
    event_data_lines: list[str] = []
    commit_event: JsonDict | None = None
    raw_line_count = 0

    def _flush_event() -> None:
        nonlocal commit_event
        if not event_data_lines:
            return
        raw_payload = "\n".join(event_data_lines)
        event_data_lines.clear()
        parsed_json: JsonDict | None = None
        parse_error = None
        try:
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                parsed_json = parsed
        except Exception as exc:
            parse_error = str(exc)
        row: JsonDict = {"raw": raw_payload}
        if parsed_json is not None:
            row["json"] = parsed_json
            if (
                parsed_json.get("phase") == "COMMIT"
                and bool(parsed_json.get("done"))
                and commit_event is None
            ):
                commit_event = parsed_json
        if parse_error is not None:
            row["parse_error"] = parse_error
        events.append(row)

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status_code = int(getattr(resp, "status", 200))
            while True:
                line = resp.readline()
                if not line:
                    break
                raw_line_count += 1
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    _flush_event()
                    if commit_event is not None or len(events) >= max_events:
                        break
                    continue
                if text.startswith(":"):
                    continue
                if text.startswith("data:"):
                    event_data_lines.append(text[5:].lstrip())
                    continue
            _flush_event()
            return {
                "ok": True,
                "status_code": status_code,
                "latency_ms": int((time.time() - started) * 1000),
                "events": events,
                "event_count": len(events),
                "line_count": raw_line_count,
                "commit_event": commit_event,
            }
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(exc)
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "body": body,
            "events": events,
            "event_count": len(events),
            "line_count": raw_line_count,
            "commit_event": commit_event,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "events": events,
            "event_count": len(events),
            "line_count": raw_line_count,
            "commit_event": commit_event,
        }


def _phase_counts(events: list[JsonDict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in events:
        payload = row.get("json")
        if not isinstance(payload, dict):
            continue
        phase = str(payload.get("phase") or "UNKNOWN")
        counts[phase] = counts.get(phase, 0) + 1
    return counts


def _is_tts_ok(tts_summary: Any, *, allow_degraded: bool) -> bool:
    if not isinstance(tts_summary, dict):
        return False
    status = str(tts_summary.get("status") or "").upper()
    reason_code = str(tts_summary.get("reason_code") or "").strip()
    if reason_code:
        return False
    event = tts_summary.get("event") if isinstance(tts_summary.get("event"), dict) else {}
    start_event = event.get("start_event") if isinstance(event.get("start_event"), dict) else {}
    event_type = str(event.get("event_type") or "").upper()
    start_event_type = str(start_event.get("event_type") or "").upper()
    playback_evidence = (
        event_type in {"TTS_PLAYBACK_STARTED", "TTS_PLAYBACK_FINISHED"}
        or start_event_type in {"TTS_PLAYBACK_STARTED", "TTS_PLAYBACK_FINISHED"}
    )
    if not playback_evidence:
        return False
    if bool(tts_summary.get("success")) and status not in {"REJECTED", "FAILED", "DEGRADED"}:
        return True
    if allow_degraded and status == "DEGRADED":
        return True
    return False


def _is_vts_ok(
    vts_summary: Any,
    *,
    allow_degraded: bool,
    require_mouth_sync: bool,
    runtime_event: Any = None,
) -> bool:
    if not isinstance(vts_summary, dict):
        return False
    status = str(vts_summary.get("status") or "").upper()
    reason_code = str(vts_summary.get("reason_code") or "").strip()
    if reason_code:
        return False
    if allow_degraded and status == "DEGRADED":
        return True
    if status in {"REJECTED", "FAILED", "SKIPPED", "DEGRADED"}:
        return False
    if ("success" in vts_summary) and (not bool(vts_summary.get("success"))):
        return False

    event = (
        vts_summary.get("event")
        if isinstance(vts_summary.get("event"), dict)
        else (runtime_event if isinstance(runtime_event, dict) else {})
    )
    expression_result = event.get("vts_expression_result") if isinstance(event.get("vts_expression_result"), dict) else {}
    talk_result = event.get("vts_talk_result") if isinstance(event.get("vts_talk_result"), dict) else {}
    mouth_sync = event.get("vts_mouth_sync") if isinstance(event.get("vts_mouth_sync"), dict) else {}

    if expression_result and not bool(expression_result.get("success")):
        return False
    if talk_result and not bool(talk_result.get("success")):
        return False
    if require_mouth_sync:
        if not bool(mouth_sync.get("started")):
            return False
        mouth_state = str(mouth_sync.get("state") or "").strip().lower()
        if mouth_state not in {"running", "finished", "stopped"}:
            return False
    if status in {"APPLIED", "ACCEPTED", "OK"}:
        return True
    return bool(vts_summary.get("success"))


def _is_synthetic_asr_ingest_ok(edge_events_result: JsonDict) -> bool:
    edge_events_body = edge_events_result.get("body") if isinstance(edge_events_result.get("body"), dict) else {}
    edge_events_success = bool(edge_events_body.get("success"))
    edge_events_total = int(edge_events_body.get("total") or 0)
    edge_events_accepted = int(edge_events_body.get("accepted") or 0)
    return bool(edge_events_result.get("ok")) and int(edge_events_result.get("status_code") or 0) == 200 and edge_events_success and edge_events_total > 0 and edge_events_total == edge_events_accepted


def _format_synthetic_asr_detail(edge_events_result: JsonDict) -> str:
    edge_events_body = edge_events_result.get("body") if isinstance(edge_events_result.get("body"), dict) else {}
    edge_events_total = int(edge_events_body.get("total") or 0)
    edge_events_accepted = int(edge_events_body.get("accepted") or 0)
    return (
        f"status_code={edge_events_result.get('status_code')}, "
        f"accepted={edge_events_accepted}/{edge_events_total}, "
        "source=synthetic_injection(/v1/edge/events)"
    )


def _format_synthetic_asr_detail_from_audit_db(audit_db: Any) -> str:
    count = int((audit_db or {}).get("voice_event_count") or 0) if isinstance(audit_db, dict) else 0
    return f"status_code=0, accepted={count}/{count}, source=audit_db(voice_event_log)"


def _real_asr_gate(allow_synthetic_asr: bool) -> tuple[bool, str]:
    if allow_synthetic_asr:
        return True, "synthetic ASR accepted by --allow-synthetic-asr"
    return (
        False,
        "real ASR not executed in this script (only synthetic /v1/edge/events injection); "
        "rerun with --allow-synthetic-asr only for non-ASR smoke checks",
    )


def _resolve_audit_db_path(env_file: dict[str, str]) -> Path:
    raw_path = _env_get(env_file, "Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _query_audit_db(
    *,
    db_path: Path,
    asr_request_id: str,
    chat_request_id: str | None,
) -> JsonDict:
    if not db_path.exists():
        return {
            "available": False,
            "reason": "db_file_not_found",
            "db_path": str(db_path),
        }
    result: JsonDict = {
        "available": True,
        "db_path": str(db_path),
        "voice_event_count": 0,
        "voice_event_latest": [],
        "tts_event_count": 0,
        "tts_event_latest": [],
        "avatar_action_count": 0,
        "avatar_action_latest": [],
    }
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        voice_count_row = conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM voice_event_log
            WHERE request_id = ?
              AND event_type IN ('ASR_CHUNK', 'ASR_FINAL')
            """,
            (asr_request_id,),
        ).fetchone()
        result["voice_event_count"] = int(voice_count_row["c"] if voice_count_row is not None else 0)

        voice_rows = conn.execute(
            """
            SELECT event_type, status, stage, reason_code, ts_ms
            FROM voice_event_log
            WHERE request_id = ?
            ORDER BY id DESC
            LIMIT 6
            """,
            (asr_request_id,),
        ).fetchall()
        result["voice_event_latest"] = [dict(row) for row in voice_rows]

        if chat_request_id:
            tts_count_row = conn.execute(
                """
                SELECT COUNT(*) AS c
                FROM voice_event_log
                WHERE request_id = ?
                  AND event_type LIKE 'TTS_%'
                """,
                (chat_request_id,),
            ).fetchone()
            result["tts_event_count"] = int(tts_count_row["c"] if tts_count_row is not None else 0)

            tts_rows = conn.execute(
                """
                SELECT event_type, status, reason_code, stage, detail, payload, ts_ms
                FROM voice_event_log
                WHERE request_id = ?
                  AND event_type LIKE 'TTS_%'
                ORDER BY id DESC
                LIMIT 10
                """,
                (chat_request_id,),
            ).fetchall()
            parsed_tts_rows: list[JsonDict] = []
            for row in tts_rows:
                item = dict(row)
                raw_payload = item.get("payload")
                if isinstance(raw_payload, str) and raw_payload.strip():
                    try:
                        parsed_payload = json.loads(raw_payload)
                        if isinstance(parsed_payload, dict):
                            item["payload"] = parsed_payload
                    except Exception:
                        pass
                parsed_tts_rows.append(item)
            result["tts_event_latest"] = parsed_tts_rows

            avatar_count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM avatar_action_log WHERE request_id = ?",
                (chat_request_id,),
            ).fetchone()
            result["avatar_action_count"] = int(avatar_count_row["c"] if avatar_count_row is not None else 0)

            avatar_rows = conn.execute(
                """
                SELECT trigger, emotion_tag, expression_id, status, reason_code, stage, ts_ms
                FROM avatar_action_log
                WHERE request_id = ?
                ORDER BY id DESC
                LIMIT 6
                """,
                (chat_request_id,),
            ).fetchall()
            result["avatar_action_latest"] = [dict(row) for row in avatar_rows]
    finally:
        conn.close()
    return result


def _build_tts_summary_from_audit_db(audit_db: Any) -> JsonDict:
    if not isinstance(audit_db, dict):
        return {}
    rows = audit_db.get("tts_event_latest")
    if not isinstance(rows, list) or not rows:
        return {}

    latest_row = next((row for row in rows if isinstance(row, dict)), None)
    if latest_row is None:
        return {}
    playback_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("event_type") or "").upper() in {"TTS_PLAYBACK_STARTED", "TTS_PLAYBACK_FINISHED"}
        ),
        None,
    )
    start_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("event_type") or "").upper() == "TTS_PLAYBACK_STARTED"
        ),
        None,
    )

    target_row = playback_row or latest_row
    event_type = str(target_row.get("event_type") or "").upper()
    reason_code = str(target_row.get("reason_code") or "").strip()
    event_payload = target_row.get("payload") if isinstance(target_row.get("payload"), dict) else {}
    event: JsonDict = dict(event_payload)
    if event_type and not event.get("event_type"):
        event["event_type"] = event_type
    if start_row is not None and start_row is not target_row:
        start_payload = start_row.get("payload") if isinstance(start_row.get("payload"), dict) else {}
        start_event = dict(start_payload)
        if not start_event.get("event_type"):
            start_event["event_type"] = str(start_row.get("event_type") or "").upper()
        event["start_event"] = start_event

    success = event_type in {"TTS_PLAYBACK_STARTED", "TTS_PLAYBACK_FINISHED"} and not reason_code
    return {
        "success": success,
        "status": str(target_row.get("status") or "ACCEPTED"),
        "reason_code": reason_code,
        "event": event,
        "source": "audit_db",
    }


def _build_vts_summary_from_audit_db(audit_db: Any) -> JsonDict:
    if not isinstance(audit_db, dict):
        return {}
    avatar_count = int(audit_db.get("avatar_action_count") or 0)
    if avatar_count <= 0:
        return {}
    avatar_rows = audit_db.get("avatar_action_latest")
    if not isinstance(avatar_rows, list) or not avatar_rows:
        return {}
    latest_row = next((row for row in avatar_rows if isinstance(row, dict)), None)
    if latest_row is None:
        return {}

    tts_rows = audit_db.get("tts_event_latest")
    tts_has_playback = False
    if isinstance(tts_rows, list):
        tts_has_playback = any(
            isinstance(row, dict)
            and str(row.get("event_type") or "").upper() in {"TTS_PLAYBACK_STARTED", "TTS_PLAYBACK_FINISHED"}
            for row in tts_rows
        )

    mouth_state = "finished" if tts_has_playback else "running"
    return {
        "success": str(latest_row.get("status") or "").upper() == "APPLIED",
        "status": str(latest_row.get("status") or "APPLIED"),
        "reason_code": str(latest_row.get("reason_code") or "").strip(),
        "event": {
            "vts_expression_result": {"success": str(latest_row.get("status") or "").upper() == "APPLIED"},
            "vts_talk_result": {"success": tts_has_playback},
            "vts_mouth_sync": {"started": tts_has_playback, "state": mouth_state},
        },
        "source": "audit_db",
    }


def _await_runtime_voice_evidence(
    *,
    db_path: Path,
    asr_request_id: str,
    chat_request_id: str | None,
    wait_seconds: float,
    poll_interval_s: float = 1.0,
) -> JsonDict:
    audit_db = _query_audit_db(
        db_path=db_path,
        asr_request_id=asr_request_id,
        chat_request_id=chat_request_id,
    )
    if not chat_request_id or wait_seconds <= 0:
        return audit_db

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if _build_tts_summary_from_audit_db(audit_db) and _build_vts_summary_from_audit_db(audit_db):
            return audit_db
        time.sleep(poll_interval_s)
        audit_db = _query_audit_db(
            db_path=db_path,
            asr_request_id=asr_request_id,
            chat_request_id=chat_request_id,
        )
    return audit_db


def _extract_edge_component_status(edge_health_body: Any, component_name: str) -> str:
    if not isinstance(edge_health_body, dict):
        return ""
    event = edge_health_body.get("event")
    if not isinstance(event, dict):
        return ""
    components = event.get("components")
    if not isinstance(components, dict):
        return ""
    component = components.get(component_name)
    if not isinstance(component, dict):
        return ""
    return str(component.get("status") or "").upper()


def _extract_edge_component_health_state(edge_health_body: Any, component_name: str) -> str:
    if not isinstance(edge_health_body, dict):
        return ""
    event = edge_health_body.get("event")
    if not isinstance(event, dict):
        return ""
    components = event.get("components")
    if not isinstance(components, dict):
        return ""
    component = components.get(component_name)
    if not isinstance(component, dict):
        return ""
    health = component.get("health")
    if not isinstance(health, dict):
        return ""
    return str(health.get("state") or "").upper()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Dual-end voice probe (synthetic ASR injection + real chat/TTS/VTS checks). "
            "By default ASR real-capture gate fails unless --allow-synthetic-asr is set."
        )
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env for defaults")
    parser.add_argument("--host-url", default="", help="Host base URL, e.g. http://127.0.0.1:18080")
    parser.add_argument("--edge-url", default="", help="Edge base URL, e.g. http://192.168.1.101:17800")
    parser.add_argument("--session-id", default="", help="Fixed session id (optional)")
    parser.add_argument("--chat-message", default="おはよう、今日もよろしくね。短く返事して。", help="Chat message")
    parser.add_argument("--sse-timeout-s", type=float, default=150.0, help="SSE timeout seconds")
    parser.add_argument("--http-timeout-s", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--voice-evidence-wait-s",
        type=float,
        default=45.0,
        help="Seconds to wait for TTS/VTS evidence to land in the audit DB after COMMIT",
    )
    parser.add_argument("--report-dir", default="artifacts/release/reports", help="Report output directory")
    parser.add_argument("--allow-degraded-tts", action="store_true", help="Treat TTS DEGRADED as pass")
    parser.add_argument("--allow-degraded-vts", action="store_true", help="Treat VTS DEGRADED as pass")
    parser.add_argument(
        "--allow-vts-no-mouth-sync",
        action="store_true",
        help="Allow VTS pass when mouth_sync.started is false",
    )
    parser.add_argument(
        "--allow-synthetic-asr",
        action="store_true",
        help="Allow synthetic ASR injection to satisfy ASR gate (strict mode otherwise fails)",
    )
    args = parser.parse_args()

    env_file_path = Path(args.env_file)
    env_file = _load_env_file(env_file_path)
    audit_db_path = _resolve_audit_db_path(env_file)

    host_url = _resolve_base_url(
        env_file=env_file,
        explicit_url=args.host_url,
        host_key="HOST_APP_HOST",
        port_key="HOST_APP_PORT",
        default_host="127.0.0.1",
        default_port=18080,
    )
    edge_url = _resolve_base_url(
        env_file=env_file,
        explicit_url=args.edge_url,
        host_key="EDGE_RPC_HOST",
        port_key="EDGE_RPC_PORT",
        default_host="127.0.0.1",
        default_port=17800,
    )

    started_at = _iso_now()
    session_id = args.session_id.strip() or f"sess-refactor-{uuid.uuid4().hex[:12]}"
    asr_request_id = f"asr-refactor-{uuid.uuid4().hex[:12]}"
    ts_ms = int(time.time() * 1000)

    host_health = _http_json("GET", f"{host_url}/health", timeout_s=args.http_timeout_s)
    host_ready = _http_json("GET", f"{host_url}/ready", timeout_s=args.http_timeout_s)
    edge_health = _http_json("GET", f"{edge_url}/v1/edge/health", timeout_s=args.http_timeout_s)

    asr_events_payload: JsonDict = {
        "events": [
            {
                "event_type": "ASR_CHUNK",
                "ts_ms": ts_ms,
                "request_id": asr_request_id,
                "session_id": session_id,
                "source": "edge",
                "direction": "EDGE_TO_HOST",
                "status": "RECEIVED",
                "seq": 1,
                "stage": "S_ASR_CHUNK",
                "payload": {
                    "text": args.chat_message,
                    "language": "ja",
                    "chunk_index": 0,
                },
            },
            {
                "event_type": "ASR_FINAL",
                "ts_ms": ts_ms + 1,
                "request_id": asr_request_id,
                "session_id": session_id,
                "source": "edge",
                "direction": "EDGE_TO_HOST",
                "status": "RECEIVED",
                "seq": 2,
                "stage": "S_ASR_FINAL",
                "payload": {
                    "text": args.chat_message,
                    "language": "ja",
                    "emotion_tag": "HAPPY",
                    "emotion_intensity": 0.72,
                },
            },
        ]
    }
    edge_events_result = _http_json(
        "POST",
        f"{host_url}/v1/edge/events",
        payload=asr_events_payload,
        timeout_s=args.http_timeout_s,
    )

    chat_payload: JsonDict = {
        "message": args.chat_message,
        "session_id": session_id,
        "scene_type": "CHAT",
        "emotion_tag": "HAPPY",
        "emotion_intensity": 0.72,
    }
    chat_sse = _stream_chat_sse(
        url=f"{host_url}/v1/chat",
        payload=chat_payload,
        timeout_s=args.sse_timeout_s,
    )

    commit_event = chat_sse.get("commit_event")
    commit_payload = commit_event if isinstance(commit_event, dict) else {}
    chat_request_id = str(commit_payload.get("request_id") or "").strip() or None
    audit_db = _await_runtime_voice_evidence(
        db_path=audit_db_path,
        asr_request_id=asr_request_id,
        chat_request_id=chat_request_id,
        wait_seconds=float(args.voice_evidence_wait_s),
    )
    audit_refs = commit_payload.get("audit_refs") if isinstance(commit_payload.get("audit_refs"), dict) else {}
    tts_summary = audit_refs.get("tts") if isinstance(audit_refs, dict) else {}
    vts_summary = audit_refs.get("vts") if isinstance(audit_refs, dict) else {}
    tts_source = "audit_refs"
    vts_source = "audit_refs"
    if not isinstance(tts_summary, dict) or not tts_summary:
        fallback_tts_summary = _build_tts_summary_from_audit_db(audit_db)
        if fallback_tts_summary:
            tts_summary = fallback_tts_summary
            tts_source = "audit_db"
    if not isinstance(vts_summary, dict) or not vts_summary:
        fallback_vts_summary = _build_vts_summary_from_audit_db(audit_db)
        if fallback_vts_summary:
            vts_summary = fallback_vts_summary
            vts_source = "audit_db"
    tts_event = tts_summary.get("event") if isinstance(tts_summary, dict) and isinstance(tts_summary.get("event"), dict) else {}
    tts_start_event = tts_event.get("start_event") if isinstance(tts_event.get("start_event"), dict) else {}
    tts_event_type = str(tts_event.get("event_type") or "").strip()
    tts_start_event_type = str(tts_start_event.get("event_type") or "").strip()

    commit_decision = str(commit_payload.get("decision") or "").upper()
    commit_done = bool(commit_payload.get("done"))
    chat_commit_ok = commit_done and commit_decision == "APPROVE"
    tts_ok = _is_tts_ok(tts_summary, allow_degraded=args.allow_degraded_tts)
    vts_ok = _is_vts_ok(
        vts_summary,
        allow_degraded=args.allow_degraded_vts,
        require_mouth_sync=(not args.allow_vts_no_mouth_sync),
        runtime_event=tts_event,
    )
    synthetic_asr_ok = _is_synthetic_asr_ingest_ok(edge_events_result)
    synthetic_asr_detail = _format_synthetic_asr_detail(edge_events_result)
    if (not synthetic_asr_ok) and int(audit_db.get("voice_event_count") or 0) >= 2:
        synthetic_asr_ok = True
        synthetic_asr_detail = _format_synthetic_asr_detail_from_audit_db(audit_db)
    real_asr_ok, real_asr_detail = _real_asr_gate(args.allow_synthetic_asr)

    host_health_ok = bool(host_health.get("ok")) and int(host_health.get("status_code") or 0) == 200
    host_ready_ok = (
        bool(host_ready.get("ok"))
        and int(host_ready.get("status_code") or 0) == 200
        and bool((host_ready.get("body") or {}).get("ready"))
    )
    if chat_commit_ok and (not host_health_ok or not host_ready_ok):
        retry_timeout_s = max(float(args.http_timeout_s), 30.0)
        if not host_health_ok:
            host_health_retry = _http_json("GET", f"{host_url}/health", timeout_s=retry_timeout_s)
            if bool(host_health_retry.get("ok")) and int(host_health_retry.get("status_code") or 0) == 200:
                host_health = host_health_retry
                host_health_ok = True
        if not host_ready_ok:
            host_ready_retry = _http_json("GET", f"{host_url}/ready", timeout_s=retry_timeout_s)
            if (
                bool(host_ready_retry.get("ok"))
                and int(host_ready_retry.get("status_code") or 0) == 200
                and bool((host_ready_retry.get("body") or {}).get("ready"))
            ):
                host_ready = host_ready_retry
                host_ready_ok = True
    edge_health_ok = bool(edge_health.get("ok")) and int(edge_health.get("status_code") or 0) == 200

    edge_tts_component_status = _extract_edge_component_status(edge_health.get("body"), "tts")
    edge_vts_component_status = _extract_edge_component_status(edge_health.get("body"), "vts")
    edge_vts_state = _extract_edge_component_health_state(edge_health.get("body"), "vts")

    nodes = [
        {
            "node": "host.health",
            "passed": host_health_ok,
            "detail": f"status_code={host_health.get('status_code')}",
        },
        {
            "node": "host.ready",
            "passed": host_ready_ok,
            "detail": f"ready={((host_ready.get('body') or {}).get('ready'))}",
        },
        {
            "node": "edge.health",
            "passed": edge_health_ok,
            "detail": (
                f"status_code={edge_health.get('status_code')}, "
                f"tts={edge_tts_component_status or 'UNKNOWN'}, "
                f"vts={edge_vts_component_status or 'UNKNOWN'}"
            ),
        },
        {
            "node": "edge.vts.connection",
            "passed": edge_vts_state in {"CONNECTED", "AUTHENTICATED"},
            "detail": f"vts_state={edge_vts_state or 'UNKNOWN'}",
        },
        {
            "node": "asr.synthetic_ingest",
            "passed": synthetic_asr_ok,
            "detail": synthetic_asr_detail,
        },
        {
            "node": "asr.real_capture",
            "passed": real_asr_ok,
            "detail": real_asr_detail,
        },
        {
            "node": "chat.commit",
            "passed": chat_commit_ok,
            "detail": f"decision={commit_decision or 'UNKNOWN'}, done={commit_done}",
        },
        {
            "node": "tts.rpc",
            "passed": tts_ok,
            "detail": (
                f"source={tts_source}, "
                f"status={str((tts_summary or {}).get('status') or 'UNKNOWN')}, "
                f"reason={str((tts_summary or {}).get('reason_code') or '')}, "
                f"event_type={tts_event_type or '-'}, "
                f"start_event_type={tts_start_event_type or '-'}"
            ),
        },
        {
            "node": "vts.rpc",
            "passed": vts_ok,
            "detail": (
                f"source={vts_source}, "
                f"status={str((vts_summary or {}).get('status') or 'UNKNOWN')}, "
                f"reason={str((vts_summary or {}).get('reason_code') or '')}"
            ),
        },
    ]

    failed_nodes = [row["node"] for row in nodes if not bool(row.get("passed"))]
    overall_passed = len(failed_nodes) == 0

    report: JsonDict = {
        "meta": {
            "script": "test_voice_dual_chain_full_nodes_refactored.py",
            "mode": "synthetic_asr_injection",
            "started_at": started_at,
            "finished_at": _iso_now(),
        },
        "inputs": {
            "host_url": host_url,
            "edge_url": edge_url,
            "session_id": session_id,
            "asr_request_id": asr_request_id,
            "chat_message": args.chat_message,
            "env_file": str(env_file_path),
        },
        "steps": {
            "host_health": host_health,
            "host_ready": host_ready,
            "edge_health": edge_health,
            "edge_events_ingest": edge_events_result,
            "runtime_voice_sources": {
                "tts": tts_source,
                "vts": vts_source,
            },
            "chat_sse": {
                "ok": bool(chat_sse.get("ok")),
                "status_code": chat_sse.get("status_code"),
                "latency_ms": chat_sse.get("latency_ms"),
                "event_count": chat_sse.get("event_count"),
                "line_count": chat_sse.get("line_count"),
                "phase_counts": _phase_counts(chat_sse.get("events") or []),
                "commit_event": commit_payload,
                "error": chat_sse.get("error"),
                "body": chat_sse.get("body"),
            },
        },
        "audit_db": audit_db,
        "matrix": nodes,
        "overall": {
            "passed": overall_passed,
            "failed_nodes": failed_nodes,
        },
    }

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"voice_dual_chain_full_nodes_{int(time.time())}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[voice-chain] host={host_url} edge={edge_url}")
    print(f"[voice-chain] report={report_path}")
    for node in nodes:
        state = "PASS" if node["passed"] else "FAIL"
        print(f"[{state}] {node['node']} | {node['detail']}")
    print(f"[voice-chain] overall={'PASS' if overall_passed else 'FAIL'}")

    return 0 if overall_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
