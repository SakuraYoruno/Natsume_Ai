#!/usr/bin/env python
"""Run a live gateway matrix against the current host + edge deployment."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / str(
    os.environ.get("Natsume_AUDIT_DB") or "host_brain/data/audit.sqlite3"
)
DEFAULT_GATEWAY_BASE_URL = str(
    os.environ.get("HOST_EDGE_GATEWAY_BASE_URL") or "http://192.168.1.101:19000"
).rstrip("/")
DEFAULT_EDGE_BASE_URL = str(
    os.environ.get("HOST_EDGE_RPC_BASE_URL")
    or os.environ.get("EDGE_RPC_BASE_URL")
    or "http://192.168.1.101:17800"
).rstrip("/")
DEFAULT_HOST_LOG_PATH = ROOT / "runtime" / "host" / "logs" / "host.stdout.log"
DEFAULT_REPORT_DIR = ROOT / "artifacts" / "release" / "reports"
REQUEST_ID_RE = re.compile(r"request_id=(req_[0-9a-f]+(?:::[^\\s]+)?)")


@dataclass(frozen=True)
class LiveCase:
    case_id: str
    prompt: str
    expected_targets: tuple[str, ...]
    required_patterns: tuple[str, ...] = ()
    notes: str = ""
    require_local_voice: bool = False
    force_dual_voice_reply: bool = False
    wait_voice_reply_result: bool = False
    allow_voice_reply_on_primary_failure: bool = False


@dataclass
class CaseOutcome:
    case_id: str
    conversation_id: str
    prompt: str
    expected_targets: list[str]
    webhook: dict[str, Any]
    request_id: str | None
    governor: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    graph: dict[str, Any] | None
    input_completed: dict[str, Any] | None
    log_excerpt: list[str]
    targets_seen: list[str]
    verdict: str
    summary: str
    notes: str = ""


def build_default_cases() -> list[LiveCase]:
    return [
        LiveCase(
            case_id="translate_delegate",
            prompt="把“今天辛苦了，早点休息”翻译成英文。",
            expected_targets=("translate_agent",),
            required_patterns=("rest",),
            require_local_voice=True,
            force_dual_voice_reply=True,
            wait_voice_reply_result=True,
            allow_voice_reply_on_primary_failure=True,
            notes="验证网关文本入口能否稳定命中 translate_agent。",
        ),
        LiveCase(
            case_id="intent_planning",
            prompt="帮我整理一下我现在应该先做什么，再给我一个三步建议。",
            expected_targets=("intent_agent",),
            require_local_voice=True,
            force_dual_voice_reply=True,
            wait_voice_reply_result=True,
            allow_voice_reply_on_primary_failure=True,
            required_patterns=("第一步", "第二步", "第三步"),
            notes="验证复杂建议请求是否真的被拆成步骤，而不是只回澄清问题。",
        ),
        LiveCase(
            case_id="hub_skill_search",
            prompt="蓝蓝，搜索技能 weather",
            expected_targets=("hub_skill_search",),
            required_patterns=("weather",),
            require_local_voice=True,
            force_dual_voice_reply=True,
            wait_voice_reply_result=True,
            allow_voice_reply_on_primary_failure=True,
            notes="验证自然语言到 Hub Skill Search 的映射是否可用。",
        ),
        LiveCase(
            case_id="memory_store",
            prompt="请记住：我明天上午 9 点开会。",
            expected_targets=("memory_store",),
            require_local_voice=True,
            force_dual_voice_reply=True,
            wait_voice_reply_result=True,
            allow_voice_reply_on_primary_failure=True,
            notes="验证 memory_store 主链接入，以及执行结果是否可用。",
        ),
        LiveCase(
            case_id="code_generation",
            prompt="写一个 Python 函数，输入数组后去重并按出现频次降序返回。",
            expected_targets=("codex_worker",),
            required_patterns=("def ",),
            require_local_voice=True,
            force_dual_voice_reply=True,
            wait_voice_reply_result=True,
            allow_voice_reply_on_primary_failure=True,
            notes="验证网关入口下的代码能力是否能交付可用代码片段。",
        ),
        LiveCase(
            case_id="multi_skill_plan",
            prompt="先帮我总结一下这段会话，再查一下当前情绪状态。",
            expected_targets=("session_summary", "emotion_query"),
            require_local_voice=True,
            force_dual_voice_reply=True,
            wait_voice_reply_result=True,
            allow_voice_reply_on_primary_failure=True,
            notes="验证多步自然语言请求是否会拆成 session_summary + emotion_query。",
        ),
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the live gateway matrix.")
    parser.add_argument(
        "--gateway-base-url",
        default=DEFAULT_GATEWAY_BASE_URL,
        help=f"Gateway base URL (default: {DEFAULT_GATEWAY_BASE_URL})",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Audit SQLite path (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--edge-base-url",
        default=DEFAULT_EDGE_BASE_URL,
        help=f"Edge RPC base URL (default: {DEFAULT_EDGE_BASE_URL})",
    )
    parser.add_argument(
        "--host-log-path",
        default=str(DEFAULT_HOST_LOG_PATH),
        help=f"Host stdout log path (default: {DEFAULT_HOST_LOG_PATH})",
    )
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help=f"Report output directory (default: {DEFAULT_REPORT_DIR})",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        default=180,
        help="Per-case wait timeout in seconds (default: 180)",
    )
    parser.add_argument(
        "--inter-case-settle-s",
        type=int,
        default=20,
        help="Max seconds to wait for edge voice pipeline to become idle between cases (default: 20)",
    )
    parser.add_argument(
        "--edge-http-timeout-s",
        type=int,
        default=8,
        help="Edge health HTTP timeout in seconds while waiting for idle (default: 8)",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Run only the named case_id. Can be provided multiple times.",
    )
    return parser.parse_args(argv)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _read_log_chunk(path: Path, start_offset: int) -> str:
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(start_offset)
        return handle.read()


def _send_gateway_webhook(
    *,
    gateway_base_url: str,
    case: LiveCase,
    conversation_id: str,
    timeout_s: int,
) -> dict[str, Any]:
    ts_ms = _now_ms()
    connect_timeout_s = max(5, min(int(timeout_s), 25))
    payload = {
        "msg_id": f"{case.case_id}::{ts_ms}",
        "sender": f"wxid_{case.case_id}",
        "from_wxid": f"wxid_{case.case_id}",
        "receiver": "wxid_natsume_live_probe",
        "to_wxid": "wxid_natsume_live_probe",
        "conversation_id": conversation_id,
        "content": case.prompt,
        "text": case.prompt,
        "msg_type": "1",
        "ts_ms": ts_ms,
        "sender_name": f"probe_{case.case_id}",
        "bridge_backend": "uia_hook",
    }
    if case.force_dual_voice_reply:
        payload["force_dual_voice_reply"] = True
    if case.wait_voice_reply_result:
        payload["wait_voice_reply_result"] = True
    if case.allow_voice_reply_on_primary_failure:
        payload["allow_voice_reply_on_primary_failure"] = True
    request = urllib.request.Request(
        f"{gateway_base_url}/v1/gateway/wechat/webhook",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    started_at = time.time()
    try:
        with urllib.request.urlopen(request, timeout=connect_timeout_s) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            try:
                parsed_body = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed_body = {"raw": raw_body}
            return {
                "sent_ts_ms": ts_ms,
                "http_status": response.status,
                "body": parsed_body,
                "latency_ms": int((time.time() - started_at) * 1000),
                "transport_error": None,
            }
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed_body = json.loads(raw_body)
        except json.JSONDecodeError:
            parsed_body = {"raw": raw_body}
        return {
            "sent_ts_ms": ts_ms,
            "http_status": exc.code,
            "body": parsed_body,
            "latency_ms": int((time.time() - started_at) * 1000),
            "transport_error": None,
        }
    except Exception as exc:  # pragma: no cover - live runtime branch
        return {
            "sent_ts_ms": ts_ms,
            "http_status": None,
            "body": None,
            "latency_ms": int((time.time() - started_at) * 1000),
            "transport_error": f"{type(exc).__name__}: {exc}",
        }


def _http_json(*, method: str, url: str, timeout_s: int) -> dict[str, Any]:
    request = urllib.request.Request(url, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=max(1, int(timeout_s))) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            try:
                parsed_body = json.loads(raw_body)
            except json.JSONDecodeError:
                parsed_body = {"raw": raw_body}
            return {
                "ok": True,
                "status_code": response.status,
                "body": parsed_body,
            }
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed_body = json.loads(raw_body)
        except json.JSONDecodeError:
            parsed_body = {"raw": raw_body}
        return {"ok": False, "status_code": exc.code, "body": parsed_body}
    except Exception as exc:  # pragma: no cover - live runtime branch
        return {"ok": False, "status_code": None, "body": {"error": f"{type(exc).__name__}: {exc}"}}


def _edge_voice_pipeline_idle(edge_health_body: Any) -> bool:
    if not isinstance(edge_health_body, dict):
        return False
    event = edge_health_body.get("event")
    if not isinstance(event, dict):
        return False
    components = event.get("components")
    if not isinstance(components, dict):
        return False

    resource_gate = components.get("resource_gate")
    resource_gate_map = dict(resource_gate) if isinstance(resource_gate, dict) else {}
    snapshot = dict(resource_gate_map.get("snapshot") or {})
    metrics = dict(snapshot.get("metrics") or {})
    active_tasks = list(snapshot.get("active_tasks") or [])
    queue_depth = int(metrics.get("queue_depth", 0) or 0)

    playback = components.get("tts_playback_device")
    playback_map = dict(playback) if isinstance(playback, dict) else {}
    playback_health = dict(playback_map.get("health") or {})
    playing = bool(playback_health.get("playing"))

    voice_pipeline = components.get("voice_pipeline")
    voice_pipeline_map = dict(voice_pipeline) if isinstance(voice_pipeline, dict) else {}
    voice_pipeline_health = dict(voice_pipeline_map.get("health") or {})
    mouth_sync_thread_alive = bool(voice_pipeline_health.get("mouth_sync_thread_alive"))

    return queue_depth <= 0 and not active_tasks and not playing and not mouth_sync_thread_alive


def _wait_for_edge_voice_idle(
    *,
    edge_base_url: str,
    settle_s: int,
    http_timeout_s: int,
) -> dict[str, Any]:
    if settle_s <= 0:
        return {"idle": True, "reason": "settle_disabled"}
    deadline = time.time() + settle_s
    last_snapshot: dict[str, Any] | None = None
    while time.time() < deadline:
        snapshot = _http_json(
            method="GET",
            url=f"{edge_base_url}/v1/edge/health",
            timeout_s=http_timeout_s,
        )
        last_snapshot = snapshot
        body = snapshot.get("body")
        if snapshot.get("ok") and int(snapshot.get("status_code") or 0) == 200 and _edge_voice_pipeline_idle(body):
            return {
                "idle": True,
                "reason": "edge_voice_pipeline_idle",
                "snapshot": snapshot,
            }
        time.sleep(1)
    return {
        "idle": False,
        "reason": "edge_voice_pipeline_busy_or_unreachable",
        "snapshot": last_snapshot,
    }


def _connect_db(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def _fetch_graph_by_session(
    *,
    db_path: Path,
    session_id: str,
    min_ts_ms: int,
) -> dict[str, Any] | None:
    connection = _connect_db(db_path)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT request_id, session_id, total_latency_ms, status, reason_code, node_count, nodes_json, ts_ms
            FROM graph_run_trace
            WHERE session_id = ? AND ts_ms >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (session_id, min_ts_ms - 1000),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        record = dict(row)
        try:
            nodes = json.loads(record.get("nodes_json") or "[]")
        except json.JSONDecodeError:
            nodes = []
        record["node_ids"] = [str(node.get("node_id")) for node in nodes if node.get("node_id")]
        record.pop("nodes_json", None)
        return record
    finally:
        connection.close()


def _fetch_graph_by_request_id(
    *,
    db_path: Path,
    request_id: str,
    min_ts_ms: int,
) -> dict[str, Any] | None:
    if not request_id:
        return None
    connection = _connect_db(db_path)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT request_id, session_id, total_latency_ms, status, reason_code, node_count, nodes_json, ts_ms
            FROM graph_run_trace
            WHERE request_id = ? AND ts_ms >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (request_id, min_ts_ms - 1000),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        record = dict(row)
        try:
            nodes = json.loads(record.get("nodes_json") or "[]")
        except json.JSONDecodeError:
            nodes = []
        record["node_ids"] = [str(node.get("node_id")) for node in nodes if node.get("node_id")]
        record.pop("nodes_json", None)
        return record
    finally:
        connection.close()


def _fetch_related_rows(db_path: Path, request_id: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not request_id:
        return [], []
    connection = _connect_db(db_path)
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT request_id, decision, reason_code, stage, ts_ms
            FROM governor_log
            WHERE request_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (request_id,),
        )
        governor_rows = [dict(row) for row in cursor.fetchall()]
        cursor.execute(
            """
            SELECT request_id, skill_name, result_status, result_code, result_digest, ts_ms
            FROM skill_exec_log
            WHERE request_id = ?
            ORDER BY id DESC
            LIMIT 12
            """,
            (request_id,),
        )
        skill_rows = [dict(row) for row in cursor.fetchall()]
        return governor_rows, skill_rows
    finally:
        connection.close()


def _fetch_input_completed_event(
    db_path: Path,
    request_id: str | None,
    *,
    conversation_id: str | None = None,
    min_ts_ms: int | None = None,
) -> dict[str, Any] | None:
    if not request_id and not conversation_id:
        return None
    connection = _connect_db(db_path)
    try:
        cursor = connection.cursor()
        normalized_rows: list[dict[str, Any]] = []

        def _append_rows(rows: list[sqlite3.Row]) -> None:
            for row in rows:
                record = dict(row)
                payload_raw = record.get("payload")
                if isinstance(payload_raw, str) and payload_raw.strip():
                    try:
                        record["payload"] = json.loads(payload_raw)
                    except json.JSONDecodeError:
                        record["payload"] = {"raw": payload_raw}
                normalized_rows.append(record)

        if request_id:
            cursor.execute(
                """
                SELECT request_id, event_type, status, reason_code, detail, payload, ts_ms
                FROM voice_event_log
                WHERE request_id = ? AND event_type = 'INPUT_COMPLETED'
                ORDER BY id DESC
                LIMIT 1
                """,
                (request_id,),
            )
            _append_rows(cursor.fetchall())

        need_conversation_fallback = bool(conversation_id) and (
            not normalized_rows
            or str(dict(normalized_rows[0].get("payload") or {}).get("conversation_id") or "").strip()
            != conversation_id
        )
        if need_conversation_fallback:
            threshold = int(min_ts_ms or 0) - 1000
            cursor.execute(
                """
                SELECT request_id, event_type, status, reason_code, detail, payload, ts_ms
                FROM voice_event_log
                WHERE event_type = 'INPUT_COMPLETED' AND ts_ms >= ?
                ORDER BY id DESC
                LIMIT 80
                """,
                (threshold,),
            )
            _append_rows(cursor.fetchall())

        if not normalized_rows:
            return None

        if conversation_id:
            for record in normalized_rows:
                payload = dict(record.get("payload") or {})
                if (
                    payload.get("source") == "GATEWAY"
                    and str(payload.get("conversation_id") or "").strip() == conversation_id
                ):
                    return record
            return None

        return normalized_rows[0]
    finally:
        connection.close()


def _fetch_gateway_event_by_conversation(
    db_path: Path,
    *,
    conversation_id: str,
    min_ts_ms: int,
    event_types: tuple[str, ...] = (
        "INPUT_ROUTE_DECIDED",
        "INPUT_COMPLETED",
        "INPUT_DISPATCHED",
        "INPUT_ACCEPTED",
    ),
) -> dict[str, Any] | None:
    if not conversation_id:
        return None
    connection = _connect_db(db_path)
    try:
        cursor = connection.cursor()
        placeholders = ",".join("?" for _ in event_types)
        params: list[Any] = [int(min_ts_ms) - 1000, *event_types]
        cursor.execute(
            f"""
            SELECT request_id, event_type, status, reason_code, detail, payload, ts_ms
            FROM voice_event_log
            WHERE ts_ms >= ? AND event_type IN ({placeholders})
            ORDER BY id DESC
            LIMIT 120
            """,
            params,
        )
        for row in cursor.fetchall():
            record = dict(row)
            payload_raw = record.get("payload")
            if isinstance(payload_raw, str) and payload_raw.strip():
                try:
                    record["payload"] = json.loads(payload_raw)
                except json.JSONDecodeError:
                    record["payload"] = {"raw": payload_raw}
            payload = dict(record.get("payload") or {})
            if (
                payload.get("source") == "GATEWAY"
                and str(payload.get("conversation_id") or "").strip() == conversation_id
            ):
                return record
        return None
    finally:
        connection.close()


def _extract_request_id(log_chunk: str, *, conversation_id: str | None = None) -> str | None:
    lines = [line for line in log_chunk.splitlines() if "request_id=req_" in line]
    if conversation_id:
        bound_lines = [line for line in lines if conversation_id in line]
        if bound_lines:
            match = REQUEST_ID_RE.search(bound_lines[-1])
            if match:
                return match.group(1)
        return None
    matches = REQUEST_ID_RE.findall(log_chunk)
    return matches[-1] if matches else None


def _extract_request_lines(log_chunk: str, request_id: str | None) -> list[str]:
    if not request_id:
        return []
    return [
        line.strip()
        for line in log_chunk.splitlines()
        if request_id in line
    ][-16:]


def _normalize_target_name(raw: str) -> str:
    value = str(raw or "").strip()
    if value.startswith("sub_agent:"):
        return value.split(":", 1)[1]
    if value.startswith("skill:"):
        return value.split(":", 1)[1]
    return value


def _targets_seen(skill_rows: list[dict[str, Any]], graph_row: dict[str, Any] | None) -> list[str]:
    seen: list[str] = []
    for row in skill_rows:
        target = _normalize_target_name(str(row.get("skill_name") or ""))
        if target and target not in seen:
            seen.append(target)
    for node_id in list((graph_row or {}).get("node_ids") or []):
        target = _normalize_target_name(str(node_id))
        if (
            target
            and target not in seen
            and (
                node_id.startswith("sub_agent:")
                or node_id.startswith("skill:")
            )
        ):
            seen.append(target)
    return seen


def _joined_skill_text(skill_rows: list[dict[str, Any]]) -> str:
    fragments = [str(row.get("result_digest") or "") for row in skill_rows]
    return "\n".join(fragment for fragment in fragments if fragment).strip()


def _evaluate_case(
    case: LiveCase,
    *,
    request_id: str | None,
    governor_rows: list[dict[str, Any]],
    skill_rows: list[dict[str, Any]],
    graph_row: dict[str, Any] | None,
    log_lines: list[str],
    input_completed: dict[str, Any] | None = None,
) -> tuple[str, str, list[str]]:
    targets = _targets_seen(skill_rows, graph_row)
    dispatch_failed = any("dispatch failed" in line.lower() for line in log_lines)
    target_match = all(expected in targets for expected in case.expected_targets)
    skill_text = _joined_skill_text(skill_rows)
    patterns_ok = all(pattern in skill_text for pattern in case.required_patterns)
    failed_skill_rows = [
        row for row in skill_rows
        if str(row.get("result_status") or "").upper() not in {"OK", "SUCCESS"}
    ]

    if request_id is None:
        return "FAIL", "没有抓到 request_id，说明入口证据不完整。", targets
    if dispatch_failed:
        return "FAIL", "Host 日志里出现 dispatch failed。", targets
    if graph_row is None and not skill_rows:
        return "FAIL", "没有 graph_run_trace，也没有 skill_exec 证据。", targets
    if not target_match:
        return "FAIL", f"目标路由不符合预期，实际 targets={targets}.", targets
    if failed_skill_rows:
        codes = [
            str(row.get("result_code") or "UNKNOWN")
            for row in failed_skill_rows
        ]
        return "FAIL", f"Skill 执行失败，result_code={codes}.", targets
    if case.require_local_voice:
        completed_payload = dict((input_completed or {}).get("payload") or {})
        voice_success = completed_payload.get("gateway_voice_success")
        voice_started = completed_payload.get("gateway_voice_started")
        vts_started = completed_payload.get("gateway_voice_vts_started")
        if voice_success is not True:
            return "FAIL", "本地语音回复未确认成功，edge 侧语音回包结果缺失。", targets
        if voice_started is not True:
            return "FAIL", "TTS 启动标记缺失，说明语音链路没有真正开始合成。", targets
        if vts_started is not True:
            return "FAIL", "语音链路已发起，但 VTS 联动标记缺失，说明表情链路没有启动。", targets
    if case.required_patterns and not patterns_ok:
        return "WARN", "命中了目标能力，但产出内容没有满足关键判定模式。", targets
    if governor_rows and str(governor_rows[0].get("decision") or "").upper() != "APPROVE":
        return "WARN", f"Governor 决策不是 APPROVE: {governor_rows[0]}.", targets
    return "PASS", "链路命中预期目标，并拿到了可用执行证据。", targets


def _run_case(
    *,
    gateway_base_url: str,
    db_path: Path,
    host_log_path: Path,
    timeout_s: int,
    case: LiveCase,
    run_tag: str,
) -> CaseOutcome:
    conversation_id = f"{case.case_id}_{run_tag}"
    log_offset = host_log_path.stat().st_size if host_log_path.exists() else 0
    webhook = _send_gateway_webhook(
        gateway_base_url=gateway_base_url,
        case=case,
        conversation_id=conversation_id,
        timeout_s=timeout_s,
    )
    sent_ts_ms = int(webhook.get("sent_ts_ms") or _now_ms())
    deadline = time.time() + timeout_s
    graph_row: dict[str, Any] | None = None
    request_id: str | None = None

    while time.time() < deadline:
        gateway_event = _fetch_gateway_event_by_conversation(
            db_path,
            conversation_id=conversation_id,
            min_ts_ms=sent_ts_ms,
        )
        if gateway_event is not None:
            request_id = str(gateway_event.get("request_id") or "").strip() or None
            if request_id:
                graph_row = _fetch_graph_by_request_id(
                    db_path=db_path,
                    request_id=request_id,
                    min_ts_ms=sent_ts_ms,
                )
                break
        log_chunk = _read_log_chunk(host_log_path, log_offset)
        request_id = _extract_request_id(log_chunk, conversation_id=conversation_id)
        if request_id and (
            f"dispatch failed request_id={request_id}" in log_chunk
            or f"dispatch completed request_id={request_id}" in log_chunk
        ):
            break
        time.sleep(2)

    log_chunk = _read_log_chunk(host_log_path, log_offset)
    if request_id is None:
        request_id = _extract_request_id(log_chunk, conversation_id=conversation_id)
    if request_id and graph_row is None:
        graph_row = _fetch_graph_by_request_id(
            db_path=db_path,
            request_id=request_id,
            min_ts_ms=sent_ts_ms,
        )
    input_completed = None
    if request_id or conversation_id:
        while time.time() < deadline:
            input_completed = _fetch_input_completed_event(
                db_path,
                request_id,
                conversation_id=conversation_id,
                min_ts_ms=sent_ts_ms,
            )
            if input_completed is not None:
                request_id = str(input_completed.get("request_id") or "").strip() or request_id
                break
            time.sleep(1)
    governor_rows, skill_rows = _fetch_related_rows(db_path, request_id)
    log_lines = _extract_request_lines(log_chunk, request_id)
    verdict, summary, targets = _evaluate_case(
        case,
        request_id=request_id,
        governor_rows=governor_rows,
        skill_rows=skill_rows,
        graph_row=graph_row,
        log_lines=log_lines,
        input_completed=input_completed,
    )
    return CaseOutcome(
        case_id=case.case_id,
        conversation_id=conversation_id,
        prompt=case.prompt,
        expected_targets=list(case.expected_targets),
        webhook=webhook,
        request_id=request_id,
        governor=governor_rows,
        skills=skill_rows,
        graph=graph_row,
        input_completed=input_completed,
        log_excerpt=log_lines,
        targets_seen=targets,
        verdict=verdict,
        summary=summary,
        notes=case.notes,
    )


def _build_report(
    *,
    gateway_base_url: str,
    db_path: Path,
    host_log_path: Path,
    outcomes: list[CaseOutcome],
) -> dict[str, Any]:
    verdict_counts: dict[str, int] = {"PASS": 0, "WARN": 0, "FAIL": 0}
    for outcome in outcomes:
        verdict_counts[outcome.verdict] = verdict_counts.get(outcome.verdict, 0) + 1
    return {
        "report_id": f"gateway_live_matrix_{_now_ms()}",
        "generated_at_ms": _now_ms(),
        "gateway_base_url": gateway_base_url,
        "db_path": str(db_path),
        "host_log_path": str(host_log_path),
        "summary": {
            "total_cases": len(outcomes),
            "pass": verdict_counts.get("PASS", 0),
            "warn": verdict_counts.get("WARN", 0),
            "fail": verdict_counts.get("FAIL", 0),
        },
        "cases": [asdict(outcome) for outcome in outcomes],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    db_path = Path(args.db_path)
    host_log_path = Path(args.host_log_path)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    selected = set(args.case or [])
    cases = [
        case for case in build_default_cases()
        if not selected or case.case_id in selected
    ]
    if not cases:
        print("No cases selected.", file=sys.stderr)
        return 2

    run_tag = str(_now_ms())
    outcomes: list[CaseOutcome] = []
    print(f"Running {len(cases)} live gateway cases against {args.gateway_base_url}", flush=True)
    for index, case in enumerate(cases, start=1):
        print(f"[{index}/{len(cases)}] {case.case_id}: {case.prompt}", flush=True)
        outcome = _run_case(
            gateway_base_url=args.gateway_base_url,
            db_path=db_path,
            host_log_path=host_log_path,
            timeout_s=int(args.timeout_s),
            case=case,
            run_tag=run_tag,
        )
        outcomes.append(outcome)
        voice_payload = dict((outcome.input_completed or {}).get("payload") or {})
        voice_summary = ""
        if voice_payload:
            voice_summary = (
                " voice="
                f"{voice_payload.get('gateway_voice_success')}"
                f"/started={voice_payload.get('gateway_voice_started')}"
                f"/vts={voice_payload.get('gateway_voice_vts_started')}"
            )
        print(
            f"  -> {outcome.verdict} request_id={outcome.request_id} targets={outcome.targets_seen}{voice_summary} summary={outcome.summary}",
            flush=True,
        )
        if index < len(cases):
            settle_result = _wait_for_edge_voice_idle(
                edge_base_url=args.edge_base_url,
                settle_s=int(args.inter_case_settle_s),
                http_timeout_s=int(args.edge_http_timeout_s),
            )
            print(
                f"     settle idle={settle_result.get('idle')} reason={settle_result.get('reason')}",
                flush=True,
            )

    report = _build_report(
        gateway_base_url=args.gateway_base_url,
        db_path=db_path,
        host_log_path=host_log_path,
        outcomes=outcomes,
    )
    report_path = report_dir / f"{report['report_id']}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report written to {report_path}", flush=True)
    return 0 if report["summary"]["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
