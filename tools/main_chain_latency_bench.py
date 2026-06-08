#!/usr/bin/env python3
"""Benchmark the real chat main chain and a few direct-route controls.

The script sends SSE chat requests to the local host service, prints the final
text, and joins audit rows so slow turns can be split into thalamus intent,
surface rendering, graph runtime, and model-pool attempts.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_DB_PATH = Path("host_brain/data/audit.sqlite3")

MAIN_CASES: list[dict[str, str]] = [
    {
        "bucket": "main_chat",
        "label": "codex_migration_risk",
        "message": "你觉得把后台任务迁到本机 Codex 以后，最需要继续观察的风险是什么？用两句说。",
    },
    {
        "bucket": "main_chat",
        "label": "main_chain_latency_suspect",
        "message": "如果我要继续压整条聊天链路的耗时，你会先怀疑哪一段？不要列清单。",
    },
    {
        "bucket": "main_chat",
        "label": "main_chain_feel",
        "message": "别讲测试指标，直接说你现在对这个主链体感的判断，像正常聊天那样。",
    },
]

CONTROL_CASES: list[dict[str, str]] = [
    {
        "bucket": "direct_status",
        "label": "task_progress",
        "message": "这次主链效果怎么样，短句回答",
    },
    {
        "bucket": "direct_recent",
        "label": "recent_turn",
        "seed": "我刚才说的观察锚点是 surface fallback 质量。",
        "message": "刚才说到哪了",
    },
]


def _post_sse(base_url: str, path: str, body: dict[str, Any], *, timeout: int) -> tuple[list[dict[str, Any]], int, int]:
    url = f"{base_url.rstrip('/')}{path}"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.perf_counter()
    commit_ms = 0
    events: list[dict[str, Any]] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = str(resp.headers.get("content-type") or "").lower()
            if "application/json" in content_type and "text/event-stream" not in content_type:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    events.append({"_raw": raw[:500]})
            else:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        event = json.loads(raw)
                        events.append(event)
                        if not commit_ms and isinstance(event, dict) and event.get("phase") == "COMMIT":
                            commit_ms = int((time.perf_counter() - started) * 1000)
                    except json.JSONDecodeError:
                        events.append({"_raw_event": raw[:500]})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            events.append({"_http_error": exc.code, "_body": raw[:500]})
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return events, elapsed_ms, commit_ms or elapsed_ms


def _commit_event(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if isinstance(event, dict) and event.get("phase") == "COMMIT":
            return event
    return events[-1] if events else {}


def _connect(db_path: Path) -> sqlite3.Connection | None:
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None


def _json_loads(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, str) and parsed.strip().startswith(("[", "{")):
            try:
                return json.loads(parsed)
            except json.JSONDecodeError:
                return parsed
        return parsed
    except json.JSONDecodeError:
        return None


def _fetch_audit(conn: sqlite3.Connection | None, request_id: str) -> dict[str, Any]:
    if not conn or not request_id or request_id == "?":
        return {}
    out: dict[str, Any] = {}
    try:
        pool_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM llm_pool_call_log WHERE request_id=? ORDER BY id",
                (request_id,),
            ).fetchall()
        ]
        for row in pool_rows:
            row["attempts"] = _json_loads(row.get("attempts_json")) or []
        out["pool"] = pool_rows
    except Exception:
        out["pool"] = []
    try:
        graph = conn.execute(
            "SELECT * FROM graph_run_trace WHERE request_id=? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        if graph:
            graph_row = dict(graph)
            graph_row["nodes"] = _json_loads(graph_row.get("nodes_json")) or []
            out["graph"] = graph_row
    except Exception:
        pass
    try:
        intent = conn.execute(
            "SELECT * FROM intent_log WHERE request_id=? ORDER BY id DESC LIMIT 1",
            (request_id,),
        ).fetchone()
        if intent:
            intent_row = dict(intent)
            intent_row["draft"] = _json_loads(intent_row.get("proposed_intent_draft_json")) or {}
            out["intent"] = intent_row
    except Exception:
        pass
    return out


def _pool_ms(audit: dict[str, Any], scene_type: str) -> int:
    total = 0
    for row in audit.get("pool") or []:
        if str(row.get("scene_type") or "").upper() == scene_type.upper():
            total += int(row.get("total_latency_ms") or 0)
    return total


def _pool_final(audit: dict[str, Any], scene_type: str) -> str:
    for row in reversed(audit.get("pool") or []):
        if str(row.get("scene_type") or "").upper() == scene_type.upper():
            provider = str(row.get("final_provider") or "?")
            model = str(row.get("final_model") or "?")
            exhausted = " exhausted" if int(row.get("pool_exhausted") or 0) else ""
            return f"{provider}:{model}{exhausted}"
    return "-"


def _pool_attempts(audit: dict[str, Any], scene_type: str) -> str:
    pieces: list[str] = []
    for row in audit.get("pool") or []:
        if str(row.get("scene_type") or "").upper() != scene_type.upper():
            continue
        for attempt in row.get("attempts") or []:
            if not isinstance(attempt, dict):
                pieces.append(_preview(attempt, 40))
                continue
            provider = str(attempt.get("provider") or "?")
            status = str(attempt.get("status") or "?")
            latency = int(attempt.get("latency_ms") or 0)
            pieces.append(f"{provider}:{status}:{latency}ms")
    return ", ".join(pieces) if pieces else "-"


def _preview(text: Any, limit: int = 86) -> str:
    value = " ".join(str(text or "").split())
    return value[:limit] + ("..." if len(value) > limit else "")


def _run_case(
    *,
    base_url: str,
    db_path: Path,
    case: dict[str, str],
    session_id: str,
    timeout: int,
) -> dict[str, Any]:
    if case.get("seed"):
        _post_sse(base_url, "/v1/chat", {"message": case["seed"], "session_id": session_id}, timeout=timeout)
        time.sleep(0.2)

    events, response_close_ms, commit_ms = _post_sse(
        base_url,
        "/v1/chat",
        {"message": case["message"], "session_id": session_id},
        timeout=timeout,
    )
    commit = _commit_event(events)
    request_id = str(commit.get("request_id") or "?")
    time.sleep(0.35)
    conn = _connect(db_path)
    try:
        audit = _fetch_audit(conn, request_id)
    finally:
        if conn:
            conn.close()

    intent = (audit.get("intent") or {}).get("draft") or {}
    route = str(intent.get("route") or commit.get("route") or "")
    target = str(intent.get("target_id") or "")
    path = str((commit.get("audit_refs") or {}).get("path") or "")
    narrative = str(commit.get("narrative") or "")
    reply_envelope = commit.get("reply_envelope") or {}
    raw_extra = reply_envelope.get("raw_extra") if isinstance(reply_envelope, dict) else {}
    fallback = "yes" if (
        str(commit.get("reason_code") or "").upper() == "SURFACE_MAIN_CHAIN_FALLBACK"
        or (isinstance(raw_extra, dict) and bool(raw_extra.get("fallback")))
    ) else "no"
    graph_ms = int((audit.get("graph") or {}).get("total_latency_ms") or 0)
    return {
        "bucket": case["bucket"],
        "label": case["label"],
        "request_id": request_id,
        "wall_ms": commit_ms,
        "response_close_ms": response_close_ms,
        "graph_ms": graph_ms,
        "thalamus_ms": _pool_ms(audit, "THALAMUS_INTENT"),
        "surface_ms": _pool_ms(audit, "CHAT_SURFACE"),
        "surface_final": _pool_final(audit, "CHAT_SURFACE"),
        "surface_attempts": _pool_attempts(audit, "CHAT_SURFACE"),
        "decision": str(commit.get("decision") or "?"),
        "reason_code": str(commit.get("reason_code") or ""),
        "route": route or "-",
        "target": target or "-",
        "path": path or "-",
        "fallback": fallback,
        "text": narrative,
    }


def _print_row(row: dict[str, Any]) -> None:
    route = row["route"] if row["target"] == "-" else f"{row['route']}:{row['target']}"
    print(
        f"{row['bucket']:<14} {row['label']:<26} total={row['wall_ms']:>6}ms "
        f"close={row['response_close_ms']:>6}ms "
        f"thalamus={row['thalamus_ms']:>5}ms surface={row['surface_ms']:>5}ms "
        f"route={route:<22} fallback={row['fallback']:<3} req={row['request_id'][:16]}"
    )
    print(f"  surface={row['surface_final']} attempts={row['surface_attempts']}")
    print(f"  text: {_preview(row['text'])}")


def _summarize(rows: list[dict[str, Any]]) -> None:
    print()
    print("Summary by bucket")
    print("-" * 72)
    buckets = sorted({str(row["bucket"]) for row in rows})
    for bucket in buckets:
        values = [int(row["wall_ms"]) for row in rows if row["bucket"] == bucket]
        if not values:
            continue
        avg = int(statistics.mean(values))
        p50 = int(statistics.median(values))
        print(f"{bucket:<14} n={len(values):<2} avg={avg:>6}ms p50={p50:>6}ms min={min(values):>6}ms max={max(values):>6}ms")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--include-controls", action="store_true")
    parser.add_argument("--jsonl", default="")
    args = parser.parse_args()

    cases = list(MAIN_CASES)
    if args.include_controls:
        cases.extend(CONTROL_CASES)

    run_id = int(time.time())
    rows: list[dict[str, Any]] = []
    db_path = Path(args.db_path)
    print(f"Main-chain latency bench base_url={args.base_url} repeats={args.repeats} db={db_path}")
    print("-" * 72)
    for repeat in range(1, max(1, args.repeats) + 1):
        for index, case in enumerate(cases, start=1):
            session_id = f"mainchain-bench-{run_id}-{repeat}-{index}"
            row = _run_case(base_url=args.base_url, db_path=db_path, case=case, session_id=session_id, timeout=args.timeout)
            rows.append(row)
            _print_row(row)
            if args.jsonl:
                with open(args.jsonl, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            time.sleep(0.3)
    _summarize(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
