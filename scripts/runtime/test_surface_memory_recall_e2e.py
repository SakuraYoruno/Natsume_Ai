#!/usr/bin/env python
"""Live E2E probe for episode-backed surface memory recall.

This seeds a recent lightnovel recommendation life episode, adds noisy
scheduler/self-echo memories, calls /v1/chat with an anaphoric follow-up, and
asserts the surface memory packet keeps the recommendation while dropping noise.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
DEFAULT_MESSAGE = "这三本书，你喜欢哪本？"
FORBIDDEN_PACKET_TERMS = (
    "emotion_decay_tick",
    "inner_world_numeric_tick",
    "silence_tick",
    "heartbeat",
    "health_check",
    "resource_guard",
    "最近在看一本讲宇宙",
)
FORBIDDEN_REPLY_TERMS = (
    "突然问这个",
    "硬要说",
    "最近在看",
    "不知道",
    "没看到",
    "memory://",
    "evd://",
    "chat://",
)
TITLE_TERMS = ("与神在河岸", "皇女", "培养失败", "美少女童星")
REASON_TERMS = ("标题", "名字", "感觉", "调调", "有意思", "适合", "比较", "因为", "看着", "会是")


JsonDict = dict[str, Any]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live surface memory recall E2E probe against /v1/chat.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    parser.add_argument("--trace-timeout-sec", type=float, default=15.0)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--person-id", default="surface-memory-recall-e2e")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--out", default="")
    parser.add_argument("--skip-health", action="store_true")
    return parser.parse_args()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT / path


def _resolve_base_url(explicit: str) -> str:
    value = str(explicit or "").strip()
    if value:
        return value.rstrip("/")
    env_url = str(os.environ.get("HOST_APP_BASE_URL") or "").strip()
    if env_url:
        return env_url.rstrip("/")
    host = str(os.environ.get("HOST_APP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = str(os.environ.get("HOST_APP_PORT") or "18080").strip() or "18080"
    return f"http://{host}:{port}"


def _resolve_db_path(explicit: str) -> Path:
    value = str(explicit or os.environ.get("Natsume_AUDIT_DB") or DEFAULT_DB_PATH).strip() or DEFAULT_DB_PATH
    return _resolve_path(value)


def _http_json(method: str, url: str, *, payload: JsonDict | None = None, timeout_sec: float = 10.0) -> JsonDict:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url=url, method=method.upper(), data=data, headers=headers)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                body: Any = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError:
                body = raw
            return {
                "ok": True,
                "status_code": int(getattr(resp, "status", 200)),
                "latency_ms": int((time.time() - started) * 1000),
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "body": body,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": repr(exc),
        }


def _stream_chat_sse(*, url: str, payload: JsonDict, timeout_sec: float) -> JsonDict:
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Accept": "text/event-stream", "Content-Type": "application/json"},
    )
    started = time.time()
    events: list[JsonDict] = []
    commit: JsonDict | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal commit
        if not data_lines:
            return
        raw_payload = "\n".join(data_lines)
        data_lines.clear()
        try:
            parsed = json.loads(raw_payload)
        except json.JSONDecodeError:
            parsed = {"raw": raw_payload}
        events.append(parsed if isinstance(parsed, dict) else {"raw": raw_payload})
        if isinstance(parsed, dict) and parsed.get("phase") == "COMMIT" and commit is None:
            commit = parsed

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    flush()
                    if commit is not None:
                        break
                    continue
                if text.startswith("data:"):
                    data_lines.append(text[5:].lstrip())
            flush()
            return {
                "ok": True,
                "status_code": int(getattr(resp, "status", 200)),
                "latency_ms": int((time.time() - started) * 1000),
                "events": events,
                "commit_event": commit,
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": repr(exc),
            "events": events,
            "commit_event": commit,
        }


def _seed_life_memory(*, session_id: str, person_id: str, message: str, ts_ms: int) -> JsonDict:
    from host_brain.app.dependencies import get_life_memory_store  # noqa: PLC0415
    from host_brain.memory.life_memory import (  # noqa: PLC0415
        EVIDENCE_SCHEDULER_EVENT,
        EVIDENCE_TOOL_RESULT,
        EPISODE_ACTION,
        EPISODE_BACKGROUND,
        RETENTION_HOT,
        TRUTH_VERIFIED,
    )

    store = get_life_memory_store()
    run_id = f"surface-memory-e2e-{ts_ms}-{uuid.uuid4().hex[:8]}"
    recommendation_ref = f"action://event/{run_id}:lightnovel_gateway_delivery"
    recommendation_summary = (
        "codex_production lightnovel_recommendation_delivery COMPLETED；"
        f"我刚才通过 qq 给 {person_id} 推荐了 3 本轻小说："
        "1. [丸焦ししゃも]给被人说‘培养失败’的前美少女童星（SSR）搭了把手之后；"
        "2. 1205.与神在河岸；"
        "3. [軽井広]轉移到異世界後，與美少女皇女結婚的我成為皇帝。"
    )
    recommendation = store.upsert_episode(
        episode_kind=EPISODE_ACTION,
        evidence_refs=[recommendation_ref],
        primary_ref=recommendation_ref,
        person_id=person_id,
        session_id=session_id,
        action="lightnovel_recommendation_delivery",
        result="COMPLETED",
        summary=recommendation_summary,
        salience=0.65,
        retention_state=RETENTION_HOT,
        truth_state=TRUTH_VERIFIED,
        ts_ms=ts_ms - 1000,
        metadata={"action_kind": "lightnovel_recommendation_delivery", "e2e_probe": True},
    )
    store.attach_evidence(
        episode_id=str(recommendation.get("episode_id") or ""),
        evidence_kind=EVIDENCE_TOOL_RESULT,
        source_ref=recommendation_ref,
        request_id=run_id,
        content=recommendation_summary,
        payload={"e2e_probe": True, "session_id": session_id, "person_id": person_id},
        trust_level=TRUTH_VERIFIED,
        ts_ms=ts_ms - 1000,
    )
    noise_ids: list[str] = []
    for index, action in enumerate(("emotion_decay_tick", "inner_world_numeric_tick", "silence_tick"), start=1):
        noise_ref = f"scheduler://event/{run_id}:{action}"
        noise_summary = f"scheduler {action} OK 后台任务记忆：{action} 状态=OK。"
        noise = store.upsert_episode(
            episode_kind=EPISODE_BACKGROUND,
            evidence_refs=[noise_ref],
            primary_ref=noise_ref,
            person_id=person_id,
            session_id=session_id,
            action=action,
            result="OK",
            summary=noise_summary,
            salience=0.55,
            retention_state=RETENTION_HOT,
            truth_state=TRUTH_VERIFIED,
            ts_ms=ts_ms - 900 + index,
            metadata={"e2e_probe": True},
        )
        store.attach_evidence(
            episode_id=str(noise.get("episode_id") or ""),
            evidence_kind=EVIDENCE_SCHEDULER_EVENT,
            source_ref=noise_ref,
            request_id=run_id,
            content=noise_summary,
            payload={"e2e_probe": True, "action": action},
            trust_level=TRUTH_VERIFIED,
            ts_ms=ts_ms - 900 + index,
        )
        noise_ids.append(str(noise.get("episode_id") or ""))
    echo = store.write_chat_episode(
        request_id=f"{run_id}:self_echo",
        session_id=session_id,
        person_id=person_id,
        user_message=message,
        assistant_reply="哈？最近在看一本讲宇宙和人的，挺有意思的。",
        ts_ms=ts_ms - 700,
        metadata={"e2e_probe": True},
    )
    return {
        "run_id": run_id,
        "recommendation_episode_id": recommendation.get("episode_id"),
        "noise_episode_ids": noise_ids,
        "self_echo_episode_id": echo.get("episode_id"),
    }


def _connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _json_loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def _find_memory_packet(value: Any) -> JsonDict | None:
    if isinstance(value, dict):
        packet = value.get("surface_memory_recall_packet")
        if isinstance(packet, dict):
            return packet
        for child in value.values():
            found = _find_memory_packet(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_memory_packet(child)
            if found is not None:
                return found
    return None


def _fetch_trace_snapshot(*, db_path: Path, request_id: str, timeout_sec: float) -> JsonDict:
    deadline = time.time() + max(0.5, timeout_sec)
    last_error = ""
    while time.time() < deadline:
        try:
            conn = _connect_db(db_path)
            try:
                trace_row = conn.execute(
                    """
                    SELECT request_id, session_id, total_latency_ms, status, reason_code, node_count, nodes_json, ts_ms
                    FROM graph_run_trace
                    WHERE request_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (request_id,),
                ).fetchone()
                retrieval_rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT op_kind, status, reason_code, stage, payload, detail, ts_ms
                        FROM retrieval_event_log
                        WHERE request_id = ?
                        ORDER BY id
                        """,
                        (request_id,),
                    )
                ]
                token_rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT scene_type, cognitive_tier, provider, model_id, total_tokens, billable_total_tokens, ts_ms
                        FROM llm_token_usage_log
                        WHERE request_id = ?
                        ORDER BY id
                        """,
                        (request_id,),
                    )
                ]
                llm_rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT node_id, provider, model_id, status, reason_code, latency_ms, ts_ms
                        FROM llm_node_call_log
                        WHERE request_id = ?
                        ORDER BY id
                        """,
                        (request_id,),
                    )
                ]
            finally:
                conn.close()
            if trace_row is not None:
                record = dict(trace_row)
                nodes = _json_loads(record.get("nodes_json"), [])
                packet = _find_memory_packet(nodes)
                query_plan = _extract_query_plan(nodes, retrieval_rows)
                record.pop("nodes_json", None)
                return {
                    "found": True,
                    "trace": record,
                    "node_ids": [str(node.get("node_id")) for node in nodes if isinstance(node, dict) and node.get("node_id")],
                    "surface_memory_recall_packet": packet or {},
                    "query_plan": query_plan,
                    "retrieval_rows": [_compact_retrieval_row(row) for row in retrieval_rows],
                    "llm_rows": llm_rows,
                    "token_rows": token_rows,
                }
        except Exception as exc:  # noqa: BLE001
            last_error = repr(exc)
        time.sleep(0.4)
    return {"found": False, "error": last_error or "graph_run_trace_not_found"}


def _extract_query_plan(nodes: Any, retrieval_rows: list[dict[str, Any]]) -> JsonDict:
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if str(node.get("node_id") or "") == "query_planner":
                output = node.get("output_data")
                if isinstance(output, dict):
                    return dict(output)
            output = node.get("output_data")
            if isinstance(output, dict):
                plan = output.get("query_plan")
                if isinstance(plan, dict):
                    return dict(plan)
    for row in retrieval_rows:
        payload = _json_loads(row.get("payload"), {})
        if isinstance(payload, dict) and isinstance(payload.get("query_plan"), dict):
            return dict(payload["query_plan"])
    return {}


def _compact_retrieval_row(row: dict[str, Any]) -> JsonDict:
    payload = _json_loads(row.get("payload"), {})
    compact: JsonDict = {
        "op_kind": row.get("op_kind"),
        "status": row.get("status"),
        "reason_code": row.get("reason_code"),
        "stage": row.get("stage"),
        "detail": row.get("detail"),
        "ts_ms": row.get("ts_ms"),
    }
    if isinstance(payload, dict):
        compact["candidate_count"] = payload.get("candidate_count")
        compact["route_count"] = payload.get("route_count")
        compact["query_plan"] = payload.get("query_plan")
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        compact["items_head"] = [
            {
                "score": item.get("score") or item.get("confidence"),
                "store_id": (item.get("metadata") or {}).get("store_id") if isinstance(item.get("metadata"), dict) else item.get("store_id"),
                "snippet": str(item.get("snippet") or item.get("text") or "")[:240],
            }
            for item in items[:8]
            if isinstance(item, dict)
        ]
    return compact


def _validate_report(report: JsonDict) -> list[str]:
    failures: list[str] = []
    health = report.get("health") if isinstance(report.get("health"), dict) else {}
    if not report.get("health_skipped") and not bool(health.get("ok")):
        failures.append("health_not_ok")
    chat = report.get("chat") if isinstance(report.get("chat"), dict) else {}
    commit = chat.get("commit_event") if isinstance(chat.get("commit_event"), dict) else {}
    narrative = str(commit.get("narrative") or "")
    if not bool(chat.get("ok")) or int(chat.get("status_code") or 0) != 200:
        failures.append(f"chat_http_failed:{chat.get('status_code')}:{chat.get('error')}")
    if not commit:
        failures.append("commit_missing")
    if commit and commit.get("decision") != "APPROVE":
        failures.append(f"decision_not_approve:{commit.get('decision')}")
    if not narrative.strip():
        failures.append("narrative_empty")
    forbidden_reply = [term for term in FORBIDDEN_REPLY_TERMS if term in narrative]
    if forbidden_reply:
        failures.append("reply_forbidden:" + ",".join(forbidden_reply))
    if narrative and not any(term in narrative for term in TITLE_TERMS):
        failures.append("reply_missing_recommended_title")
    if narrative and not any(term in narrative for term in REASON_TERMS):
        failures.append("reply_missing_short_reason")

    trace = report.get("trace_snapshot") if isinstance(report.get("trace_snapshot"), dict) else {}
    if not bool(trace.get("found")):
        failures.append(f"trace_missing:{trace.get('error')}")
        return failures
    query_plan = trace.get("query_plan") if isinstance(trace.get("query_plan"), dict) else {}
    if query_plan.get("recall_policy") != "MUST_RECALL":
        failures.append(f"recall_policy_not_must:{query_plan.get('recall_policy')}")
    if query_plan.get("reason") != "anaphoric_selection_recall":
        failures.append(f"recall_reason_not_anaphoric:{query_plan.get('reason')}")

    packet = trace.get("surface_memory_recall_packet") if isinstance(trace.get("surface_memory_recall_packet"), dict) else {}
    facts = [str(item) for item in packet.get("facts") or []]
    joined_facts = "\n".join(facts)
    if not facts:
        failures.append("packet_facts_empty")
    if "推荐了 3 本轻小说" not in joined_facts:
        failures.append("packet_missing_recommendation_episode")
    if "与神在河岸" not in joined_facts:
        failures.append("packet_missing_title_anchor")
    forbidden_packet = [term for term in FORBIDDEN_PACKET_TERMS if term in joined_facts]
    if forbidden_packet:
        failures.append("packet_forbidden:" + ",".join(forbidden_packet))
    if len(facts) > 2:
        failures.append(f"packet_too_many_facts:{len(facts)}")
    must_not_do = "\n".join(str(item) for item in packet.get("must_not_do") or [])
    if "不要把指代选择追问当成无关新话题" not in must_not_do:
        failures.append("packet_missing_anaphoric_guard")
    retrieval_rows = trace.get("retrieval_rows") if isinstance(trace.get("retrieval_rows"), list) else []
    recall_rows = [row for row in retrieval_rows if isinstance(row, dict) and str(row.get("op_kind") or "").upper() == "RECALL"]
    if not recall_rows:
        failures.append("retrieval_recall_row_missing")
    for row in recall_rows:
        candidate_count = row.get("candidate_count")
        try:
            numeric_count = int(candidate_count)
        except (TypeError, ValueError):
            numeric_count = -1
        if numeric_count > 2:
            failures.append(f"retrieval_candidate_count_too_high:{numeric_count}")
        preview = json.dumps(row.get("items_head") or [], ensure_ascii=False)
        forbidden_preview = [term for term in FORBIDDEN_PACKET_TERMS if term in preview]
        if forbidden_preview:
            failures.append("retrieval_preview_forbidden:" + ",".join(forbidden_preview))
        if "推荐了 3 本轻小说" not in preview:
            failures.append("retrieval_preview_missing_recommendation")
    return failures


def main() -> int:
    args = _parse_args()
    _load_env_file(_resolve_path(str(args.env_file)))
    db_path = _resolve_db_path(str(args.db_path))
    os.environ["Natsume_AUDIT_DB"] = str(db_path)

    base_url = _resolve_base_url(str(args.base_url))
    session_id = str(args.session_id or "").strip() or f"surface-memory-recall-e2e-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    person_id = str(args.person_id or "surface-memory-recall-e2e").strip()
    message = str(args.message or DEFAULT_MESSAGE).strip() or DEFAULT_MESSAGE
    out_path = Path(args.out) if str(args.out or "").strip() else ROOT / "runtime" / "host" / "logs" / f"surface_memory_recall_e2e_{session_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    started_ms = _now_ms()
    health = {"ok": True, "skipped": True} if args.skip_health else _http_json("GET", f"{base_url}/health?skip_dependency_probes=1", timeout_sec=10.0)
    seed = _seed_life_memory(session_id=session_id, person_id=person_id, message=message, ts_ms=started_ms)
    chat = _stream_chat_sse(
        url=f"{base_url}/v1/chat",
        payload={
            "message": message,
            "session_id": session_id,
            "person_id": person_id,
            "thread_id": session_id,
            "input_source": "surface_memory_recall_e2e",
            "reply_channel": "local_test",
            "reply_target": session_id,
            "render_profile": "zh_text",
            "response_form": "text",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
        },
        timeout_sec=float(args.timeout_sec),
    )
    commit = chat.get("commit_event") if isinstance(chat.get("commit_event"), dict) else {}
    request_id = str(commit.get("request_id") or "")
    trace_snapshot = _fetch_trace_snapshot(
        db_path=db_path,
        request_id=request_id,
        timeout_sec=float(args.trace_timeout_sec),
    ) if request_id else {"found": False, "error": "request_id_missing"}

    report: JsonDict = {
        "summary": {
            "scenario": "surface_memory_recall_e2e",
            "session_id": session_id,
            "person_id": person_id,
            "request_id": request_id,
            "elapsed_ms": _now_ms() - started_ms,
            "db_path": str(db_path),
            "base_url": base_url,
        },
        "health_skipped": bool(args.skip_health),
        "health": health,
        "seed": seed,
        "chat": {
            "ok": chat.get("ok"),
            "status_code": chat.get("status_code"),
            "latency_ms": chat.get("latency_ms"),
            "event_count": len(chat.get("events") or []),
            "commit_event": commit,
            "error": chat.get("error"),
        },
        "trace_snapshot": trace_snapshot,
    }
    failures = _validate_report(report)
    report["summary"]["passed"] = not failures
    report["summary"]["failures"] = failures
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("SUMMARY " + json.dumps(report["summary"], ensure_ascii=False), flush=True)
    print("REPLY " + str(commit.get("narrative") or ""), flush=True)
    packet = trace_snapshot.get("surface_memory_recall_packet") if isinstance(trace_snapshot, dict) else {}
    if isinstance(packet, dict):
        print("PACKET_FACTS " + json.dumps(packet.get("facts") or [], ensure_ascii=False), flush=True)
    print(f"WROTE {out_path}", flush=True)
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
