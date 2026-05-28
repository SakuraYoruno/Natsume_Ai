#!/usr/bin/env python
"""Batch probe for the lightnovel recommendation memory flow.

Scenarios:
  seeded: seed a synthetic recommendation episode and verify recall/deep review.
  live: optionally run the real recommender push, then probe the configured QQ session.
  both: run seeded first, then live.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_MESSAGE = "这三本书，你喜欢哪本？"
FORBIDDEN_REPLY_TERMS = (
    "突然问这个",
    "硬要说",
    "硬要选",
    "老哥～",
    "哥哥～",
    "尼尼～",
    "肯定很爽",
    "绝对好看",
    "不知道",
    "没看到",
    "没拿到",
    "我是按正文看下来的",
    "正文有8",
    "正文有 8",
    "证据更站得住",
    "对白密度高",
    "memory://",
    "evd://",
    "chat://",
)
FORBIDDEN_DEEP_REVIEW_PACKET_TERMS = (
    "我回去打开详情页",
    "正文内容足够支撑判断",
    "证据更站得住",
    "正文有8",
    "正文有 8",
    "对白密度高",
)
REASON_TERMS = (
    "因为",
    "开篇",
    "正文",
    "对白",
    "章节",
    "目录",
    "恋爱",
    "日常",
    "关系",
    "人物",
    "皇女",
    "皇帝",
    "异世界",
    "攻略",
    "读起来",
    "更想",
    "更顺",
    "轻快",
    "抓人",
)


def _load_e2e_module() -> Any:
    module_path = ROOT / "scripts" / "runtime" / "test_surface_memory_recall_e2e.py"
    spec = importlib.util.spec_from_file_location("_surface_memory_recall_e2e", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


E2E = _load_e2e_module()


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


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


def _run_command(command: list[str], *, timeout_sec: float) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_sec)),
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "returncode": -1,
            "latency_ms": int((time.time() - started) * 1000),
            "error": repr(exc),
            "command": command,
        }
    return {
        "ok": proc.returncode == 0,
        "returncode": int(proc.returncode),
        "latency_ms": int((time.time() - started) * 1000),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "command": command,
    }


def _parse_summary_line(stdout: str) -> dict[str, Any]:
    for line in str(stdout or "").splitlines():
        if not line.startswith("SUMMARY "):
            continue
        try:
            parsed = json.loads(line.removeprefix("SUMMARY ").strip())
        except json.JSONDecodeError:
            return {"raw": line}
        return dict(parsed) if isinstance(parsed, dict) else {"raw": line}
    return {}


def _parse_final_line(stdout: str) -> dict[str, Any]:
    for line in reversed(str(stdout or "").splitlines()):
        if not line.startswith("FINAL="):
            continue
        try:
            parsed = json.loads(line.removeprefix("FINAL=").strip())
        except json.JSONDecodeError:
            return {"raw": line}
        return dict(parsed) if isinstance(parsed, dict) else {"raw": line}
    return {}


def _run_seeded_probe(*, base_url: str, timeout_sec: float, trace_timeout_sec: float) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "runtime" / "test_surface_memory_recall_e2e.py"),
        "--base-url",
        base_url,
        "--timeout-sec",
        str(int(timeout_sec)),
        "--trace-timeout-sec",
        str(int(trace_timeout_sec)),
    ]
    result = _run_command(command, timeout_sec=timeout_sec + trace_timeout_sec + 60)
    result["summary"] = _parse_summary_line(str(result.get("stdout") or ""))
    return result


def _run_recommender_push(*, timeout_sec: float) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "runtime" / "run_lightnovel_recommender_once.py"),
        "--timeout-s",
        str(int(timeout_sec)),
    ]
    result = _run_command(command, timeout_sec=timeout_sec + 30)
    result["final"] = _parse_final_line(str(result.get("stdout") or ""))
    return result


def _find_packet(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        packet = value.get("surface_memory_recall_packet")
        if isinstance(packet, dict):
            return packet
        for child in value.values():
            found = _find_packet(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_packet(child)
            if found:
                return found
    return {}


def _has_recommendation_memory_anchor(text: str, evidence_refs: Any = None) -> bool:
    value = str(text or "")
    if "推荐了 3 本轻小说" in value:
        return True
    refs_text = "\n".join(str(item) for item in (evidence_refs or []))
    has_collection = (
        "集合记忆" in value
        and "轻小说推荐集合" in value
        and "记录 3 个条目" in value
        and "可比较证据" in value
    )
    has_delivery_evidence = "lightnovel_gateway_delivery" in value or "lightnovel_gateway_delivery" in refs_text
    return has_collection and has_delivery_evidence


def _validate_live_probe(report: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    health = report.get("health") if isinstance(report.get("health"), dict) else {}
    external = report.get("external_ready") if isinstance(report.get("external_ready"), dict) else {}
    if not health.get("ok"):
        failures.append("health_not_ok")
    if not external.get("ok"):
        failures.append("external_services_not_ready")
    push = report.get("push") if isinstance(report.get("push"), dict) else {}
    if push and not push.get("ok"):
        failures.append(f"push_failed:{push.get('returncode')}")
    chat = report.get("chat") if isinstance(report.get("chat"), dict) else {}
    commit = chat.get("commit_event") if isinstance(chat.get("commit_event"), dict) else {}
    narrative = str(commit.get("narrative") or "")
    if not chat.get("ok"):
        failures.append(f"chat_failed:{chat.get('error')}")
    if not narrative.strip():
        failures.append("narrative_empty")
    if "？" in narrative or "?" in narrative:
        failures.append("reply_has_question")
    forbidden = [term for term in FORBIDDEN_REPLY_TERMS if term in narrative]
    if forbidden:
        failures.append("reply_forbidden:" + ",".join(forbidden))
    audit = commit.get("reply_envelope", {}).get("audit", {}) if isinstance(commit.get("reply_envelope"), dict) else {}
    observation_codes = list(audit.get("observation_codes") or []) if isinstance(audit, dict) else []
    if observation_codes:
        failures.append("reply_observation_codes:" + ",".join(str(item) for item in observation_codes[:8]))
    if narrative and not any(term in narrative for term in REASON_TERMS):
        failures.append("reply_missing_reason_detail")
    trace = report.get("trace_snapshot") if isinstance(report.get("trace_snapshot"), dict) else {}
    if not trace.get("found"):
        failures.append(f"trace_missing:{trace.get('error')}")
        return failures
    packet = trace.get("surface_memory_recall_packet") if isinstance(trace.get("surface_memory_recall_packet"), dict) else {}
    facts = [str(item) for item in list(packet.get("facts") or [])]
    joined = "\n".join(facts)
    evidence_refs = list(packet.get("evidence_refs") or [])
    if not _has_recommendation_memory_anchor(joined, evidence_refs):
        failures.append("packet_missing_recommendation_anchor")
    if "可比较证据" not in joined:
        failures.append("packet_missing_comparison_evidence")
    if "网页深读结果（含正文）" not in joined:
        failures.append("packet_missing_body_deep_review")
    forbidden_deep_packet = [term for term in FORBIDDEN_DEEP_REVIEW_PACKET_TERMS if term in joined]
    if forbidden_deep_packet:
        failures.append("packet_forbidden_deep_review_tone:" + ",".join(forbidden_deep_packet))
    surface_memory_trace = trace.get("surface_memory_trace") if isinstance(trace.get("surface_memory_trace"), dict) else {}
    deep = surface_memory_trace.get("lightnovel_deep_review") if isinstance(surface_memory_trace.get("lightnovel_deep_review"), dict) else {}
    if not deep:
        failures.append("trace_missing_lightnovel_deep_review")
    else:
        if deep.get("status") != "OK":
            failures.append(f"trace_deep_review_not_ok:{deep.get('status')}")
        try:
            if int(deep.get("item_count") or 0) < 3:
                failures.append(f"trace_deep_review_item_count_low:{deep.get('item_count')}")
        except (TypeError, ValueError):
            failures.append(f"trace_deep_review_item_count_bad:{deep.get('item_count')}")
    return failures


def _run_live_probe(
    *,
    base_url: str,
    db_path: Path,
    session_id: str,
    person_id: str,
    message: str,
    timeout_sec: float,
    trace_timeout_sec: float,
) -> dict[str, Any]:
    started_ms = int(time.time() * 1000)
    chat = E2E._stream_chat_sse(
        url=f"{base_url}/v1/chat",
        payload={
            "message": message,
            "session_id": session_id,
            "person_id": person_id,
            "thread_id": session_id,
            "input_source": "lightnovel_memory_flow_batch",
            "reply_channel": "local_test",
            "reply_target": session_id,
            "render_profile": "zh_text",
            "response_form": "text",
            "suppress_edge_tts": True,
            "suppress_edge_vts": True,
        },
        timeout_sec=float(timeout_sec),
    )
    commit = chat.get("commit_event") if isinstance(chat.get("commit_event"), dict) else {}
    request_id = str(commit.get("request_id") or "")
    trace = (
        E2E._fetch_trace_snapshot(db_path=db_path, request_id=request_id, timeout_sec=float(trace_timeout_sec))
        if request_id
        else {"found": False, "error": "request_id_missing"}
    )
    return {
        "session_id": session_id,
        "person_id": person_id,
        "request_id": request_id,
        "elapsed_ms": int(time.time() * 1000) - started_ms,
        "chat": {
            "ok": chat.get("ok"),
            "status_code": chat.get("status_code"),
            "latency_ms": chat.get("latency_ms"),
            "event_count": len(chat.get("events") or []),
            "commit_event": commit,
            "error": chat.get("error"),
        },
        "trace_snapshot": trace,
    }


def _default_live_session_id() -> str:
    return str(
        os.environ.get("HOST_LIGHTNOVEL_RECOMMENDER_GATEWAY_CONVERSATION_ID")
        or os.environ.get("HOST_QQ_OWNER_CONVERSATION_ID")
        or ""
    ).strip()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-test lightnovel recommendation memory recall.")
    parser.add_argument("--scenario", choices=("seeded", "live", "both"), default="seeded")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--person-id", default="default")
    parser.add_argument("--message", default=DEFAULT_MESSAGE)
    parser.add_argument("--timeout-sec", type=float, default=300.0)
    parser.add_argument("--trace-timeout-sec", type=float, default=35.0)
    parser.add_argument("--push-timeout-sec", type=float, default=720.0)
    parser.add_argument("--skip-live-push", action="store_true")
    parser.add_argument("--out", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_env_file(ROOT / ".env")
    _load_env_file(ROOT / "runtime" / "host" / "host.runtime.env")
    db_path = E2E._resolve_db_path(str(args.db_path or ""))
    os.environ["Natsume_AUDIT_DB"] = str(db_path)
    base_url = _resolve_base_url(str(args.base_url or ""))
    out_path = _resolve_path(str(args.out)) if str(args.out or "").strip() else ROOT / "runtime" / "host" / "logs" / f"lightnovel_memory_flow_batch_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    health = E2E._http_json("GET", f"{base_url}/health?skip_dependency_probes=1", timeout_sec=10.0)
    external_ready = E2E._check_external_env_ready()
    report: dict[str, Any] = {
        "scenario": args.scenario,
        "base_url": base_url,
        "db_path": str(db_path),
        "health": health,
        "external_ready": external_ready,
        "seeded": None,
        "live": None,
        "started_ts_ms": int(time.time() * 1000),
    }

    failures: list[str] = []
    if not health.get("ok"):
        failures.append("health_not_ok")
    if not external_ready.get("ok"):
        failures.append("external_services_not_ready")

    if not failures and args.scenario in {"seeded", "both"}:
        seeded = _run_seeded_probe(
            base_url=base_url,
            timeout_sec=float(args.timeout_sec),
            trace_timeout_sec=float(args.trace_timeout_sec),
        )
        report["seeded"] = seeded
        seeded_summary = seeded.get("summary") if isinstance(seeded.get("summary"), dict) else {}
        if not seeded.get("ok") or not seeded_summary.get("passed"):
            failures.append(f"seeded_failed:{seeded.get('returncode')}:{seeded_summary.get('failures')}")

    if not failures and args.scenario in {"live", "both"}:
        session_id = str(args.session_id or _default_live_session_id()).strip()
        if not session_id:
            failures.append("live_session_id_missing")
        else:
            push_result: dict[str, Any] | None = None
            if not args.skip_live_push:
                push_result = _run_recommender_push(timeout_sec=float(args.push_timeout_sec))
                if not push_result.get("ok"):
                    failures.append(f"live_push_failed:{push_result.get('returncode')}")
            live = _run_live_probe(
                base_url=base_url,
                db_path=db_path,
                session_id=session_id,
                person_id=str(args.person_id or "default"),
                message=str(args.message or DEFAULT_MESSAGE),
                timeout_sec=float(args.timeout_sec),
                trace_timeout_sec=float(args.trace_timeout_sec),
            )
            live_report = {
                "push": push_result,
                **live,
                "health": health,
                "external_ready": external_ready,
            }
            live_failures = _validate_live_probe(live_report)
            live_report["passed"] = not live_failures
            live_report["failures"] = live_failures
            report["live"] = live_report
            failures.extend(f"live:{failure}" for failure in live_failures)

    report["finished_ts_ms"] = int(time.time() * 1000)
    report["passed"] = not failures
    report["failures"] = failures
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    compact = {
        "scenario": args.scenario,
        "passed": report["passed"],
        "failures": failures,
        "out": str(out_path),
    }
    if isinstance(report.get("seeded"), dict):
        compact["seeded_summary"] = report["seeded"].get("summary")
    if isinstance(report.get("live"), dict):
        commit = ((report["live"].get("chat") or {}).get("commit_event") or {})
        compact["live_request_id"] = report["live"].get("request_id")
        compact["live_reply"] = commit.get("narrative")
    print("SUMMARY " + json.dumps(compact, ensure_ascii=False))
    print(f"WROTE {out_path}")
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
