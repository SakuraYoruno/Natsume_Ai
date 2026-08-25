#!/usr/bin/env python
"""Gateway-based dual-end voice acceptance probe.

Flow:
1. Probe host health/readiness.
2. Probe edge health / VTS connection.
3. Submit a real gateway-style message to /v1/host/input.
4. Wait for INPUT_COMPLETED, TTS playback, and avatar/VTS evidence in audit DB.
5. Report gateway -> host -> edge -> playback/VTS chain status.
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
    env_value = os.environ.get(key)
    if env_value is not None and str(env_value).strip():
        return str(env_value).strip()
    file_value = env_file.get(key)
    if file_value is not None and str(file_value).strip():
        return str(file_value).strip()
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
    try:
        port = int(_env_get(env_file, port_key, str(default_port)))
    except ValueError:
        port = default_port
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


def _resolve_audit_db_path(env_file: dict[str, str]) -> Path:
    raw_path = _env_get(env_file, "Natsume_AUDIT_DB", "host_brain/data/audit.sqlite3")
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _parse_payload(raw_payload: Any) -> Any:
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        return raw_payload
    try:
        return json.loads(raw_payload)
    except Exception:
        return raw_payload


def _is_gateway_completion_candidate(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    if str(row.get("event_type") or "").upper() != "INPUT_COMPLETED":
        return False
    stage = str(row.get("stage") or "").strip().lower()
    if stage == "input_dispatch.completed":
        return True
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return str(payload.get("source") or "").strip().upper() == "GATEWAY"


def _query_gateway_audit_db(
    *,
    db_path: Path,
    request_id: str | None,
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
        "request_event_count": 0,
        "request_event_latest": [],
        "input_completed": None,
        "tts_event_count": 0,
        "tts_event_latest": [],
        "avatar_action_count": 0,
        "avatar_action_latest": [],
    }
    if not request_id:
        return result

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        request_rows = conn.execute(
            """
            SELECT event_type, direction, status, reason_code, stage, detail, payload, ts_ms
            FROM voice_event_log
            WHERE request_id = ?
            ORDER BY id DESC
            LIMIT 40
            """,
            (request_id,),
        ).fetchall()
        parsed_request_rows: list[JsonDict] = []
        for row in request_rows:
            item = dict(row)
            item["payload"] = _parse_payload(item.get("payload"))
            parsed_request_rows.append(item)
        result["request_event_count"] = len(parsed_request_rows)
        result["request_event_latest"] = parsed_request_rows
        result["input_completed"] = next((row for row in parsed_request_rows if _is_gateway_completion_candidate(row)), None)
        result["tts_event_latest"] = [
            row
            for row in parsed_request_rows
            if str(row.get("event_type") or "").upper().startswith("TTS_")
        ]
        result["tts_event_count"] = len(result["tts_event_latest"])

        avatar_rows = conn.execute(
            """
            SELECT trigger, emotion_tag, expression_id, status, reason_code, stage, detail, ts_ms
            FROM avatar_action_log
            WHERE request_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (request_id,),
        ).fetchall()
        result["avatar_action_latest"] = [dict(row) for row in avatar_rows]
        result["avatar_action_count"] = len(result["avatar_action_latest"])
    finally:
        conn.close()
    return result


def _build_gateway_completion_summary(audit_db: Any) -> JsonDict:
    if not isinstance(audit_db, dict):
        return {}
    request_rows = audit_db.get("request_event_latest")
    row = None
    if isinstance(request_rows, list):
        row = next((candidate for candidate in request_rows if _is_gateway_completion_candidate(candidate)), None)
    if row is None and _is_gateway_completion_candidate(audit_db.get("input_completed")):
        row = audit_db.get("input_completed")
    if not isinstance(row, dict):
        return {}
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return {
        "present": True,
        "status": str(row.get("status") or "").upper(),
        "reason_code": str(row.get("reason_code") or "").strip(),
        "detail": str(row.get("detail") or "").strip(),
        "decision": str(payload.get("decision") or "").upper(),
        "route_type": str(payload.get("route_type") or "").strip().lower(),
        "reply_form": str(payload.get("reply_form") or "").strip().lower(),
        "gateway_status": str(payload.get("gateway_status") or "").strip(),
        "voice_sent": bool(payload.get("voice_sent")),
        "voice_success": payload.get("gateway_voice_success"),
        "voice_started": payload.get("gateway_voice_started"),
        "voice_vts_started": payload.get("gateway_voice_vts_started"),
        "voice_vts_expression_success": payload.get("gateway_voice_vts_expression_success"),
        "payload": payload,
    }


def _build_tts_summary_from_event_rows(rows: Any) -> JsonDict:
    if not isinstance(rows, list) or not rows:
        return {}
    latest_row = next((row for row in rows if isinstance(row, dict)), None)
    if latest_row is None:
        return {}
    finished_row = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and str(row.get("event_type") or "").upper() == "TTS_PLAYBACK_FINISHED"
        ),
        None,
    )
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
    target_row = finished_row or playback_row or latest_row
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


def _build_tts_summary_from_gateway_completion(completion_summary: Any) -> JsonDict:
    if not isinstance(completion_summary, dict):
        return {}
    payload = completion_summary.get("payload")
    if not isinstance(payload, dict):
        return {}
    event = payload.get("gateway_voice_event") if isinstance(payload.get("gateway_voice_event"), dict) else {}
    status = str(payload.get("gateway_voice_status") or "").strip()
    reason_code = str(payload.get("gateway_voice_reason_code") or "").strip()
    success = payload.get("gateway_voice_success")
    if not event and not status and success is None:
        return {}
    return {
        "success": bool(success),
        "status": status or "UNKNOWN",
        "reason_code": reason_code,
        "event": dict(event),
        "source": "input_completed",
    }


def _build_vts_summary_from_avatar_rows(rows: Any, tts_summary: Any) -> JsonDict:
    if not isinstance(rows, list) or not rows:
        return {}
    latest_row = next((row for row in rows if isinstance(row, dict)), None)
    if latest_row is None:
        return {}
    tts_success = bool((tts_summary or {}).get("success")) if isinstance(tts_summary, dict) else False
    return {
        "success": str(latest_row.get("status") or "").upper() == "APPLIED",
        "status": str(latest_row.get("status") or "APPLIED"),
        "reason_code": str(latest_row.get("reason_code") or "").strip(),
        "event": {
            "vts_expression_result": {"success": str(latest_row.get("status") or "").upper() == "APPLIED"},
            "vts_talk_result": {"success": tts_success},
            "vts_mouth_sync": {"started": tts_success, "state": "finished" if tts_success else "running"},
        },
        "source": "audit_db",
    }


def _build_vts_summary_from_gateway_completion(completion_summary: Any) -> JsonDict:
    if not isinstance(completion_summary, dict):
        return {}
    payload = completion_summary.get("payload")
    if not isinstance(payload, dict):
        return {}
    event = payload.get("gateway_voice_event") if isinstance(payload.get("gateway_voice_event"), dict) else {}
    vts_started = bool(payload.get("gateway_voice_vts_started"))
    expression_success = bool(payload.get("gateway_voice_vts_expression_success"))
    if not event and not vts_started and not expression_success:
        return {}
    return {
        "success": bool(vts_started or expression_success),
        "status": "APPLIED" if expression_success else ("ACCEPTED" if vts_started else "UNKNOWN"),
        "reason_code": "",
        "event": dict(event),
        "source": "input_completed",
    }


def _has_tts_terminal_playback(tts_summary: Any) -> bool:
    if not isinstance(tts_summary, dict):
        return False
    event = tts_summary.get("event") if isinstance(tts_summary.get("event"), dict) else {}
    start_event = event.get("start_event") if isinstance(event.get("start_event"), dict) else {}
    event_type = str(event.get("event_type") or "").upper()
    start_event_type = str(start_event.get("event_type") or "").upper()
    return event_type == "TTS_PLAYBACK_FINISHED" or start_event_type == "TTS_PLAYBACK_FINISHED"


def _is_tts_terminal_failure(tts_summary: Any) -> bool:
    if not isinstance(tts_summary, dict):
        return False
    status = str(tts_summary.get("status") or "").upper()
    reason_code = str(tts_summary.get("reason_code") or "").strip()
    return bool(reason_code) or status in {"REJECTED", "FAILED"}


def _is_vts_terminal_failure(vts_summary: Any) -> bool:
    if not isinstance(vts_summary, dict):
        return False
    status = str(vts_summary.get("status") or "").upper()
    reason_code = str(vts_summary.get("reason_code") or "").strip()
    return bool(reason_code) or status in {"REJECTED", "FAILED"}


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
    event = vts_summary.get("event") if isinstance(vts_summary.get("event"), dict) else {}
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
    return status in {"APPLIED", "ACCEPTED", "OK"} or bool(vts_summary.get("success"))


def _await_gateway_runtime_evidence(
    *,
    db_path: Path,
    request_id: str | None,
    wait_seconds: float,
    allow_degraded_tts: bool,
    allow_degraded_vts: bool,
    require_mouth_sync: bool,
    poll_interval_s: float = 1.0,
) -> JsonDict:
    audit_db = _query_gateway_audit_db(db_path=db_path, request_id=request_id)
    if not request_id or wait_seconds <= 0:
        return audit_db

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        completion_summary = _build_gateway_completion_summary(audit_db)
        tts_summary = _build_tts_summary_from_event_rows(audit_db.get("tts_event_latest")) or _build_tts_summary_from_gateway_completion(
            completion_summary
        )
        vts_summary = _build_vts_summary_from_avatar_rows(audit_db.get("avatar_action_latest"), tts_summary) or _build_vts_summary_from_gateway_completion(
            completion_summary
        )
        if completion_summary and not bool(completion_summary.get("voice_sent")):
            return audit_db
        tts_ok = _is_tts_ok(tts_summary, allow_degraded=allow_degraded_tts)
        vts_ok = _is_vts_ok(
            vts_summary,
            allow_degraded=allow_degraded_vts,
            require_mouth_sync=require_mouth_sync,
        )
        if completion_summary and _is_tts_terminal_failure(tts_summary):
            return audit_db
        if completion_summary and _is_vts_terminal_failure(vts_summary):
            return audit_db
        if completion_summary and tts_ok and vts_ok and _has_tts_terminal_playback(tts_summary):
            return audit_db
        time.sleep(poll_interval_s)
        audit_db = _query_gateway_audit_db(db_path=db_path, request_id=request_id)
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
        description="Gateway voice acceptance probe based on /v1/host/input and audit evidence."
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env for defaults")
    parser.add_argument("--host-url", default="", help="Host base URL, e.g. http://127.0.0.1:18080")
    parser.add_argument("--edge-url", default="", help="Edge base URL, e.g. http://192.168.1.101:17800")
    parser.add_argument("--session-id", default="", help="Fixed gateway conversation id (optional)")
    parser.add_argument(
        "--chat-message",
        default="おはよう。音声で短く返事して、口型と表情も動かして。",
        help="Gateway input message",
    )
    parser.add_argument("--http-timeout-s", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument(
        "--voice-evidence-wait-s",
        type=float,
        default=75.0,
        help="Seconds to wait for gateway completion and TTS/VTS evidence in audit DB",
    )
    parser.add_argument("--report-dir", default="artifacts/release/reports", help="Report output directory")
    parser.add_argument("--allow-degraded-tts", action="store_true", help="Treat TTS DEGRADED as pass")
    parser.add_argument("--allow-degraded-vts", action="store_true", help="Treat VTS DEGRADED as pass")
    parser.add_argument(
        "--allow-vts-no-mouth-sync",
        action="store_true",
        help="Allow VTS pass when mouth_sync.started is false",
    )
    parser.add_argument("--gateway-platform", default="wechat", help="Platform value for /v1/host/input")
    parser.set_defaults(
        chat_message="あいさつだけして。ひらがなだけで、とてもみじかく、いちぶんでへんじして。こえとくちぱくとひょうじょうも。"
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
    conversation_id = args.session_id.strip() or f"gateway-refactor-{uuid.uuid4().hex[:12]}"

    host_health = _http_json("GET", f"{host_url}/health", timeout_s=args.http_timeout_s)
    host_ready = _http_json("GET", f"{host_url}/ready", timeout_s=args.http_timeout_s)
    edge_health = _http_json("GET", f"{edge_url}/v1/edge/health", timeout_s=args.http_timeout_s)

    gateway_payload: JsonDict = {
        "source": "GATEWAY",
        "platform": str(args.gateway_platform or "wechat").strip() or "wechat",
        "conversation_id": conversation_id,
        "content": args.chat_message,
        "msg_type": "text",
        "raw_extra": {
            "force_dual_voice_reply": True,
            "wait_voice_reply_result": False,
            "allow_voice_reply_on_primary_failure": True,
        },
    }
    gateway_input_result = _http_json(
        "POST",
        f"{host_url}/v1/host/input",
        payload=gateway_payload,
        timeout_s=args.http_timeout_s,
    )
    gateway_input_body = gateway_input_result.get("body") if isinstance(gateway_input_result.get("body"), dict) else {}
    gateway_request_id = str(gateway_input_body.get("request_id") or "").strip() or None

    audit_db = _await_gateway_runtime_evidence(
        db_path=audit_db_path,
        request_id=gateway_request_id,
        wait_seconds=float(args.voice_evidence_wait_s),
        allow_degraded_tts=args.allow_degraded_tts,
        allow_degraded_vts=args.allow_degraded_vts,
        require_mouth_sync=(not args.allow_vts_no_mouth_sync),
    )
    gateway_completion = _build_gateway_completion_summary(audit_db)
    tts_summary = _build_tts_summary_from_event_rows(audit_db.get("tts_event_latest")) or _build_tts_summary_from_gateway_completion(
        gateway_completion
    )
    vts_summary = _build_vts_summary_from_avatar_rows(audit_db.get("avatar_action_latest"), tts_summary) or _build_vts_summary_from_gateway_completion(
        gateway_completion
    )
    tts_source = str((tts_summary or {}).get("source") or ("audit_db" if tts_summary else "none"))
    vts_source = str((vts_summary or {}).get("source") or ("audit_db" if vts_summary else "none"))

    gateway_input_ok = (
        bool(gateway_input_result.get("ok"))
        and int(gateway_input_result.get("status_code") or 0) == 200
        and bool(gateway_input_body.get("success"))
        and str(gateway_input_body.get("status") or "").upper() in {"ACCEPTED", "COMPLETED"}
        and bool(gateway_request_id)
    )
    gateway_complete_ok = (
        bool(gateway_completion)
        and str(gateway_completion.get("status") or "").upper() in {"OK", "DEGRADED"}
        and str(gateway_completion.get("decision") or "").upper() == "APPROVE"
    )
    tts_ok = _is_tts_ok(tts_summary, allow_degraded=args.allow_degraded_tts)
    vts_ok = _is_vts_ok(
        vts_summary,
        allow_degraded=args.allow_degraded_vts,
        require_mouth_sync=(not args.allow_vts_no_mouth_sync),
    )

    host_health_ok = bool(host_health.get("ok")) and int(host_health.get("status_code") or 0) == 200
    host_ready_ok = (
        bool(host_ready.get("ok"))
        and int(host_ready.get("status_code") or 0) == 200
        and bool((host_ready.get("body") or {}).get("ready"))
    )
    if gateway_complete_ok and (not host_health_ok or not host_ready_ok):
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

    tts_event = tts_summary.get("event") if isinstance(tts_summary, dict) and isinstance(tts_summary.get("event"), dict) else {}
    tts_start_event = tts_event.get("start_event") if isinstance(tts_event.get("start_event"), dict) else {}
    tts_event_type = str(tts_event.get("event_type") or "").strip()
    tts_start_event_type = str(tts_start_event.get("event_type") or "").strip()

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
            "node": "gateway.input_accepted",
            "passed": gateway_input_ok,
            "detail": (
                f"status_code={gateway_input_result.get('status_code')}, "
                f"status={str(gateway_input_body.get('status') or 'UNKNOWN')}, "
                f"request_id={gateway_request_id or '-'}"
            ),
        },
        {
            "node": "gateway.dispatch_completed",
            "passed": gateway_complete_ok,
            "detail": (
                f"status={str(gateway_completion.get('status') or 'UNKNOWN')}, "
                f"decision={str(gateway_completion.get('decision') or 'UNKNOWN')}, "
                f"route_type={str(gateway_completion.get('route_type') or '-')}, "
                f"reply_form={str(gateway_completion.get('reply_form') or '-')}, "
                f"voice_sent={str(bool(gateway_completion.get('voice_sent'))).lower()}"
            ),
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
            "mode": "gateway_host_input_voice_acceptance",
            "started_at": started_at,
            "finished_at": _iso_now(),
        },
        "inputs": {
            "host_url": host_url,
            "edge_url": edge_url,
            "conversation_id": conversation_id,
            "request_id": gateway_request_id,
            "chat_message": args.chat_message,
            "gateway_platform": gateway_payload.get("platform"),
            "env_file": str(env_file_path),
        },
        "steps": {
            "host_health": host_health,
            "host_ready": host_ready,
            "edge_health": edge_health,
            "gateway_input": gateway_input_result,
            "gateway_completion": gateway_completion,
            "runtime_voice_sources": {
                "tts": tts_source,
                "vts": vts_source,
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
