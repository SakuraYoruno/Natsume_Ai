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
import re
import sqlite3
import subprocess
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
    "硬要选",
    "认真看完",
    "最近在看",
    "不知道",
    "没看到",
    "居然",
    "我是按正文看下来的",
    "证据更站得住",
    "正文有8",
    "正文有 8",
    "memory://",
    "evd://",
    "chat://",
)
TITLE_TERMS = ("与神在河岸", "皇女", "培养失败", "美少女童星")
REASON_TERMS = (
    "标题",
    "名字",
    "感觉",
    "调调",
    "有意思",
    "适合",
    "比较",
    "因为",
    "看着",
    "会是",
    "路线",
    "更新",
    "明确",
    "吸引",
    "对我胃口",
    "正文",
    "读感",
    "节奏",
    "对白",
    "世界观",
    "开篇",
    "章节",
    "皇女",
    "皇帝",
    "攻略",
    "人物",
    "关系",
    "读起来",
    "爽快",
)
DETAIL_TERMS = (
    "公开信号",
    "资料完整",
    "奇幻身份",
    "恋爱日常",
    "设定新鲜",
    "异世界",
    "皇女",
    "设定",
    "路线",
    "章节",
    "目录",
    "更新",
    "热度",
    "看点",
    "简介",
    "正文",
    "深读",
    "读到",
    "体量",
    "人物关系",
    "对白",
    "读感",
    "开篇",
    "攻略",
    "宮內",
    "皇母",
)
DEEP_REVIEW_SELECTION_RE = re.compile(
    r"网页深读结果(?:（含正文）)?[:：].{0,120}?当前更推荐第([一二三四五六七八九十\d]{1,3})本《([^》]{1,120})》",
    re.DOTALL,
)


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


def _check_external_env_ready(*, timeout_sec: float = 45.0) -> JsonDict:
    script = ROOT / "scripts" / "runtime" / "check_external_env.py"
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout_sec)),
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "latency_ms": int((time.time() - started) * 1000),
            "error": repr(exc),
        }
    stdout = str(proc.stdout or "").strip()
    parsed: Any = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"raw": stdout[:2000]}
    return {
        "ok": int(proc.returncode or 0) == 0 and bool(isinstance(parsed, dict) and parsed.get("ready")),
        "status_code": int(proc.returncode or 0),
        "latency_ms": int((time.time() - started) * 1000),
        "body": parsed,
        "stderr": str(proc.stderr or "").strip()[:2000],
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
    selected_recommendations = [
        {
            "title": "[丸焦ししゃも]给被人说‘培养失败’的前美少女童星（SSR）搭了把手之后",
            "detail_url": "https://www.lightnovel.fun/cn/detail/1144609",
            "selection_reason": "命中偏好：恋爱日常、设定新鲜感",
            "intro_snippet": "前美少女童星与搭把手后的关系展开，偏轻松恋爱日常。",
            "update_time": "2 小时前",
            "comment_count": "8",
        },
        {
            "title": "1205.与神在河岸",
            "detail_url": "https://www.lightnovel.fun/cn/detail/1144610",
            "selection_reason": "公开信号较好：更新、热度或资料完整度较高",
            "intro_snippet": "标题和简介带奇幻身份钩子，信息完整度比普通列表项更高。",
            "update_time": "1 小时前",
            "view_count": "79",
            "comment_count": "1",
        },
        {
            "title": "[軽井広]轉移到異世界後，與美少女皇女結婚的我成為皇帝",
            "detail_url": "https://www.lightnovel.fun/cn/detail/1144301",
            "selection_reason": "命中偏好：奇幻身份钩子、设定新鲜感",
            "intro_snippet": "异世界转移、皇女结婚、成为皇帝，路线直球但题材明确。",
            "update_time": "5/23",
            "view_count": "4.1w",
            "comment_count": "28",
        },
    ]
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
        metadata={
            "action_kind": "lightnovel_recommendation_delivery",
            "selected_recommendations": selected_recommendations,
            "e2e_probe": True,
        },
    )
    store.attach_evidence(
        episode_id=str(recommendation.get("episode_id") or ""),
        evidence_kind=EVIDENCE_TOOL_RESULT,
        source_ref=recommendation_ref,
        request_id=run_id,
        content=recommendation_summary,
        payload={
            "e2e_probe": True,
            "session_id": session_id,
            "person_id": person_id,
            "metadata": {"selected_recommendations": selected_recommendations},
        },
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


def _find_surface_memory_trace(value: Any) -> JsonDict | None:
    if isinstance(value, dict):
        trace = value.get("surface_memory_trace")
        if isinstance(trace, dict):
            return trace
        for child in value.values():
            found = _find_surface_memory_trace(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_surface_memory_trace(child)
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
                surface_memory_trace = _find_surface_memory_trace(nodes)
                query_plan = _extract_query_plan(nodes, retrieval_rows)
                record.pop("nodes_json", None)
                return {
                    "found": True,
                    "trace": record,
                    "node_ids": [str(node.get("node_id")) for node in nodes if isinstance(node, dict) and node.get("node_id")],
                    "surface_memory_recall_packet": packet or {},
                    "surface_memory_trace": surface_memory_trace or {},
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
    external_ready = report.get("external_ready") if isinstance(report.get("external_ready"), dict) else {}
    if not report.get("health_skipped") and not bool(external_ready.get("ok")):
        failures.append("external_services_not_ready")
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
    if "可比较证据" not in joined_facts:
        failures.append("packet_missing_comparison_evidence")
    if "看点：" not in joined_facts or "简介：" not in joined_facts:
        failures.append("packet_missing_structured_detail")
    if "网页深读结果（含正文）" not in joined_facts:
        failures.append("packet_missing_body_deep_review")
    surface_memory_trace = trace.get("surface_memory_trace") if isinstance(trace.get("surface_memory_trace"), dict) else {}
    deep_trace = (
        surface_memory_trace.get("lightnovel_deep_review")
        if isinstance(surface_memory_trace.get("lightnovel_deep_review"), dict)
        else {}
    )
    if not deep_trace:
        failures.append("trace_missing_lightnovel_deep_review")
    else:
        if str(deep_trace.get("status") or "") != "OK":
            failures.append(f"trace_deep_review_not_ok:{deep_trace.get('status')}")
        if str(deep_trace.get("source") or "") not in {"fetch", "cache"}:
            failures.append(f"trace_deep_review_bad_source:{deep_trace.get('source')}")
        try:
            if int(deep_trace.get("item_count") or 0) < 3:
                failures.append(f"trace_deep_review_item_count_low:{deep_trace.get('item_count')}")
        except (TypeError, ValueError):
            failures.append(f"trace_deep_review_item_count_bad:{deep_trace.get('item_count')}")
    deep_selection = _deep_review_selection(joined_facts)
    if deep_selection and narrative:
        if not _reply_matches_deep_selection(narrative, deep_selection):
            failures.append(
                f"reply_deep_review_selection_mismatch:{deep_selection.get('index')}:{deep_selection.get('title')}"
            )
    if "可比较证据" in joined_facts and not any(term in narrative for term in DETAIL_TERMS):
        failures.append("reply_missing_comparison_detail")
    if "可比较证据" in joined_facts:
        detail_hits = tuple(term for term in DETAIL_TERMS if term in narrative)
        if len(set(detail_hits)) < 2:
            failures.append("reply_missing_multi_dimension_detail")
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


def _deep_review_selection(text: str) -> JsonDict:
    match = DEEP_REVIEW_SELECTION_RE.search(str(text or ""))
    if not match:
        return {}
    return {"index": _selection_index(match.group(1)), "title": _compact_title(match.group(2))}


def _selection_index(value: str) -> int:
    raw = str(value or "").strip()
    if raw.isdigit():
        return int(raw)
    return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}.get(raw, 0)


def _compact_title(value: str) -> str:
    return re.sub(r"[\s「」“”\"'《》\[\]（）()]+", "", str(value or ""))


def _reply_matches_deep_selection(reply: str, selection: JsonDict) -> bool:
    value = _compact_title(reply)
    index = int(selection.get("index") or 0)
    if index > 0:
        labels = {
            1: ("第一本", "第1本", "1本"),
            2: ("第二本", "第2本", "2本"),
            3: ("第三本", "第3本", "3本"),
            4: ("第四本", "第4本", "4本"),
        }.get(index, ())
        if any(label in value for label in labels):
            return True
    title = _compact_title(str(selection.get("title") or ""))
    terms = [title[:18]]
    for sequence in re.findall(r"[\u3040-\u30ff\u3400-\u9fffA-Za-z0-9_]{2,12}", title):
        if sequence not in {"轻小说", "推荐", "正文"}:
            terms.append(sequence)
    if "皇女" in title or "皇帝" in title:
        terms.extend(["皇女", "皇帝"])
    if "河岸" in title:
        terms.append("河岸")
    if "童星" in title:
        terms.append("童星")
    return any(term and term in value for term in dict.fromkeys(terms))


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
    external_ready = {"ok": True, "skipped": True} if args.skip_health else _check_external_env_ready()
    if not args.skip_health and (not bool(health.get("ok")) or not bool(external_ready.get("ok"))):
        report: JsonDict = {
            "summary": {
                "scenario": "surface_memory_recall_e2e",
                "session_id": session_id,
                "person_id": person_id,
                "request_id": "",
                "elapsed_ms": _now_ms() - started_ms,
                "db_path": str(db_path),
                "base_url": base_url,
                "passed": False,
                "failures": [
                    *([] if bool(health.get("ok")) else ["health_not_ok"]),
                    *([] if bool(external_ready.get("ok")) else ["external_services_not_ready"]),
                ],
            },
            "health_skipped": False,
            "health": health,
            "external_ready": external_ready,
            "seed": {},
            "chat": {},
            "trace_snapshot": {},
        }
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("SUMMARY " + json.dumps(report["summary"], ensure_ascii=False), flush=True)
        print(f"WROTE {out_path}", flush=True)
        return 2
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
        "external_ready": external_ready,
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
