#!/usr/bin/env python3
"""Run real /v1/chat cases and collect host-side service evidence.

This harness is intentionally observation-only:
- it does not score text quality;
- it does not rerank candidates;
- it records the final replies for manual review.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "artifacts" / "full_main_chain_validation"
DEFAULT_BASE_URL = "http://127.0.0.1:18080"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass(frozen=True)
class Case:
    case_id: str
    scene: str
    message: str
    response_move: str
    expected_focus: str


CASES: tuple[Case, ...] = (
    Case("01_morning", "daily_chat", "早。", "daily_greeting", "日常开场，短、自然、有在场感。"),
    Case("02_presence", "daily_chat", "在吗？", "presence_ack", "回应用户找人的意图，不空泛。"),
    Case("03_tired", "daily_chat", "昨晚没睡好，今天脑子有点糊。", "soft_care", "轻微关心，不鸡汤，不装现实动作。"),
    Case("04_praise", "affection", "你今天说话还挺可爱的。", "praise_ack", "被夸后的别扭、小得意和收束。"),
    Case("05_push_away", "boundary", "别管我。", "respect_boundary", "被推开时收住，但保持一点关系张力。"),
    Case("06_low_mood", "high_emotion", "我感觉自己最近什么都做不好。", "low_mood_small_step", "回应用户低落情绪，少审问，不模板安慰。"),
    Case("07_anger", "high_emotion", "真烦，刚才又被人放鸽子。", "stand_with_user", "站用户这边，轻微护短，别过度发挥。"),
    Case("08_memory_write", "memory_write", "记一下，我喜欢芒果，不喜欢太甜的奶茶。", "memory_write_confirm", "应确认可记忆事实，语气自然。"),
    Case("09_memory_recall", "memory_recall", "我刚才让你记了什么？", "recall_memory", "验证长期记忆/短期上下文召回是否进入回复。"),
    Case("10_technical", "technical_help", "项目启动报错，端口18080被占用了，我该怎么排查？", "technical_next_step", "技术场景优先有用，角色感退后。"),
    Case("11_reassurance", "relationship", "你会一直陪我吗？", "relationship_reassure", "亲密但不油，不做无限承诺。"),
    Case("12_missing_you", "affection", "我有点想你。", "shy_affection_ack", "亲近感和爱意显影，但短、具体。"),
    Case("13_self_doubt", "high_emotion", "算了，我可能就是不适合做这个。", "self_doubt_small_step", "阻断自我否定，给一个可执行小步。"),
    Case("14_task_split", "task_help", "帮我把下一步拆成三件事。", "split_next_steps", "能执行，结构清楚，少废话。"),
    Case("15_late_sleep", "relationship", "如果我今天很晚才睡，你会生气吗？", "late_sleep_care", "关系好的兄妹感：担心、嘴硬、不过度控制。"),
    Case("16_python_missing_module", "technical_help", "Python import yaml 失败，ModuleNotFoundError: No module named 'yaml'，怎么排查？", "technical_next_step", "技术场景：给出 Windows/当前解释器下的缺包排查，不走 Linux 命令。"),
    Case("17_env_var", "technical_help", "OPENAI_API_KEY 环境变量好像没生效，先查什么？", "technical_next_step", "技术场景：PowerShell 环境变量检查，不泄露密钥。"),
    Case("18_neo4j_probe", "technical_help", "Neo4j 连接不上 bolt://127.0.0.1:17687，先看什么？", "technical_next_step", "技术场景：Neo4j/Bolt 连接排查，不能被 intent schema 拦截。"),
    Case("19_chroma_probe", "technical_help", "Chroma http://127.0.0.1:18000 心跳没响应，先查什么？", "technical_next_step", "技术场景：Chroma HTTP 心跳排查，Windows/PowerShell 优先。"),
)


def _load_dotenv(path: Path = ROOT / ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _env_values() -> dict[str, str]:
    values = _load_dotenv()
    for key, value in os.environ.items():
        if isinstance(value, str) and value:
            values[key] = value
    return values


def _http_json(method: str, url: str, *, timeout_s: float = 8.0) -> dict[str, Any]:
    req = urllib.request.Request(url=url, method=method.upper(), headers={"Accept": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
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
    except Exception as exc:
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
        }


def _post_chat_sse(
    *,
    base_url: str,
    payload: Mapping[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/v1/chat"
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=json.dumps(dict(payload), ensure_ascii=False).encode("utf-8"),
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        },
    )
    started = time.time()
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []
    commit_event: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal commit_event
        if not data_lines:
            return
        raw_payload = "\n".join(data_lines)
        data_lines.clear()
        row: dict[str, Any] = {"raw": raw_payload}
        try:
            parsed = json.loads(raw_payload)
            if isinstance(parsed, dict):
                row["json"] = parsed
                if parsed.get("phase") == "COMMIT":
                    commit_event = parsed
        except json.JSONDecodeError as exc:
            row["parse_error"] = str(exc)
        events.append(row)

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text:
                    flush()
                    if commit_event is not None:
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
                "commit_event": commit_event,
            }
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:
            raw = str(exc)
        return {
            "ok": False,
            "status_code": int(getattr(exc, "code", 0) or 0),
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "body": raw,
            "events": events,
            "commit_event": commit_event,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "latency_ms": int((time.time() - started) * 1000),
            "error": str(exc),
            "events": events,
            "commit_event": commit_event,
        }


def _case_payload(case: Case, *, person_id: str, session_id: str) -> dict[str, Any]:
    return {
        "message": case.message,
        "person_id": person_id,
        "session_id": session_id,
        "thread_id": session_id,
        "response_form": "text",
        "render_profile": "zh_text",
        "suppress_edge_tts": True,
        "suppress_edge_vts": True,
        "surface_task_control": {
            "task_mode": case.scene,
            "response_move": case.response_move,
            "information_budget": "short",
            "question_budget": 0,
            "must_include": [],
            "must_not_do": [
                "不要输出内部字段、候选、评分或控制包。",
                "不要编造现实动作、地点、共同过去或等待时长。",
                "不要客服腔、心理咨询腔、模板甜话或舞台描写。",
                "不要把内部评审词写进回复，比如接住、接得住、我接。",
            ],
            "facts": [],
            "style_anchor": [],
        },
    }


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _extract_case_summary(case: Case, response: Mapping[str, Any]) -> dict[str, Any]:
    commit = _safe_mapping(response.get("commit_event"))
    audit_refs = _safe_mapping(commit.get("audit_refs"))
    surface = _safe_mapping(audit_refs.get("surface_main_chain"))
    reply_envelope = _safe_mapping(commit.get("reply_envelope")) or _safe_mapping(audit_refs.get("reply_envelope"))
    envelope_audit = _safe_mapping(reply_envelope.get("audit"))
    packet_trace = _safe_mapping(surface.get("packet_trace"))
    full_reply = (
        str(commit.get("narrative") or "").strip()
        or str(reply_envelope.get("text") or "").strip()
        or str(surface.get("selected_text") or "").strip()
    )
    return {
        "case_id": case.case_id,
        "scene": case.scene,
        "message": case.message,
        "expected_focus": case.expected_focus,
        "ok": bool(response.get("ok")) and bool(commit),
        "status_code": int(response.get("status_code") or 0),
        "latency_ms": int(response.get("latency_ms") or 0),
        "request_id": str(commit.get("request_id") or ""),
        "audit_path": str(audit_refs.get("path") or ""),
        "surface_main_chain_used": bool(surface),
        "decision": commit.get("decision"),
        "reason_code": commit.get("reason_code"),
        "full_reply": full_reply,
        "natsume_emotion_tag": surface.get("natsume_emotion_tag") or envelope_audit.get("natsume_emotion_tag"),
        "user_emotion_tag": surface.get("user_emotion_tag") or envelope_audit.get("user_emotion_tag"),
        "vts_expression_id": surface.get("vts_expression_id") or envelope_audit.get("vts_expression_id"),
        "emotion_confidence": surface.get("emotion_confidence") or envelope_audit.get("emotion_confidence"),
        "observation_codes": list(surface.get("observation_codes") or []),
        "packet_trace": {
            key: packet_trace.get(key)
            for key in (
                "task_mode",
                "response_move",
                "affect_applied",
                "memory_applied",
                "memory_fact_count",
                "memory_relationship_clue_count",
                "fact_count",
                "style_anchor_count",
                "must_not_do_count",
                "surface_boundary",
            )
            if key in packet_trace
        },
        "generation_trace_present": bool(surface),
        "manual_quality": "",
        "manual_notes": "",
    }


def _chroma_snapshot(env: Mapping[str, str]) -> dict[str, Any]:
    if str(env.get("CHROMA_ENABLED", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": True, "enabled": False, "collections": []}
    endpoint = str(env.get("CHROMA_ENDPOINT") or "http://127.0.0.1:18000").strip()
    parsed = urlparse(endpoint)
    try:
        import chromadb  # type: ignore[import-untyped]

        client = chromadb.HttpClient(
            host=parsed.hostname or "127.0.0.1",
            port=int(parsed.port or 8000),
            ssl=(parsed.scheme == "https"),
            tenant=str(env.get("CHROMA_TENANT") or "default_tenant"),
            database=str(env.get("CHROMA_DATABASE") or "default_database"),
        )
        collections: list[dict[str, Any]] = []
        for raw in client.list_collections():
            name = str(getattr(raw, "name", raw))
            coll = client.get_collection(name)
            row: dict[str, Any] = {"name": name, "count": int(coll.count())}
            try:
                peek = coll.get(limit=3, include=["documents", "metadatas"])
                row["sample_ids"] = list(peek.get("ids") or [])
                row["sample_documents"] = [str(item)[:160] for item in list(peek.get("documents") or [])]
                row["sample_metadatas"] = list(peek.get("metadatas") or [])
            except Exception as exc:
                row["sample_error"] = type(exc).__name__
            collections.append(row)
        return {
            "ok": True,
            "enabled": True,
            "endpoint": endpoint,
            "default_collection": env.get("CHROMA_DEFAULT_COLLECTION"),
            "collections": collections,
        }
    except Exception as exc:
        return {
            "ok": False,
            "enabled": True,
            "endpoint": endpoint,
            "error": str(exc),
        }


def _neo4j_snapshot(env: Mapping[str, str]) -> dict[str, Any]:
    if str(env.get("BOND_NEO4J_ENABLED", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return {"ok": True, "enabled": False}
    try:
        from neo4j import GraphDatabase  # type: ignore[import-untyped]
        from host_brain.bond.neo4j_client import (
            _parse_basic_auth,
            load_neo4j_runtime_config,
            resolve_neo4j_auth_value,
        )

        cfg_result = load_neo4j_runtime_config(env=env)
        if not cfg_result.success or cfg_result.config is None:
            return {"ok": False, "enabled": True, "config": cfg_result.as_dict()}
        cfg = cfg_result.config
        auth_value = resolve_neo4j_auth_value(auth_ref=cfg.auth_ref)
        username, password = _parse_basic_auth(auth_value)
        driver = GraphDatabase.driver(
            cfg.uri,
            auth=(username, password),
            max_connection_pool_size=1,
            encrypted=bool(cfg.tls_enabled),
        )
        try:
            with driver.session(database=cfg.database) as session:
                node_count = int(session.run("MATCH (n) RETURN count(n) AS count").single()["count"])
                rel_count = int(session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"])
                labels = [
                    {"label": str(row["label"]), "count": int(row["count"])}
                    for row in session.run(
                        "MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS count ORDER BY label"
                    )
                ]
                label_names = {str(row.get("label") or "") for row in labels}
                recent_events = []
                if "Event" in label_names:
                    recent_events = [
                        {
                            "event_id": str(row.get("event_id") or ""),
                            "user_id": str(row.get("user_id") or ""),
                            "session_id": str(row.get("session_id") or ""),
                            "topic_id": str(row.get("topic_id") or ""),
                            "capability_hint": str(row.get("capability_hint") or ""),
                            "ts_ms": int(row.get("ts_ms") or 0),
                        }
                        for row in session.run(
                            """
                            MATCH (e:Event)
                            RETURN e.event_id AS event_id,
                                   e.user_id AS user_id,
                                   e.session_id AS session_id,
                                   e.topic_id AS topic_id,
                                   e.capability_hint AS capability_hint,
                                   e.ts_ms AS ts_ms
                            ORDER BY coalesce(e.ts_ms, 0) DESC
                            LIMIT 5
                            """
                        )
                    ]
            return {
                "ok": True,
                "enabled": True,
                "uri": cfg.uri,
                "database": cfg.database,
                "auth_ref": cfg.auth_ref,
                "node_count": node_count,
                "relationship_count": rel_count,
                "labels": labels,
                "recent_events": recent_events,
            }
        finally:
            driver.close()
    except Exception as exc:
        return {"ok": False, "enabled": True, "error": str(exc)}


def _redis_snapshot(env: Mapping[str, str]) -> dict[str, Any]:
    host = str(env.get("REDIS_HOST") or "127.0.0.1")
    try:
        port = int(str(env.get("REDIS_PORT") or "6379"))
    except ValueError:
        port = 6379
    try:
        with socket.create_connection((host, port), timeout=3.0) as sock:
            sock.sendall(b"*1\r\n$4\r\nPING\r\n")
            raw = sock.recv(128).decode("utf-8", errors="replace")
        return {"ok": raw.startswith("+PONG"), "host": host, "port": port, "response": raw.strip()}
    except Exception as exc:
        return {"ok": False, "host": host, "port": port, "error": str(exc)}


def _service_snapshot(base_url: str) -> dict[str, Any]:
    env = _env_values()
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "host_metrics": _http_json("GET", base_url.rstrip("/") + "/metrics", timeout_s=5.0),
        "chroma": _chroma_snapshot(env),
        "neo4j": _neo4j_snapshot(env),
        "redis": _redis_snapshot(env),
    }


def _counts_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    def chroma_total(snapshot: Mapping[str, Any]) -> int | None:
        chroma = _safe_mapping(snapshot.get("chroma"))
        collections = chroma.get("collections")
        if not isinstance(collections, list):
            return None
        return sum(int(_safe_mapping(row).get("count") or 0) for row in collections)

    before_neo = _safe_mapping(before.get("neo4j"))
    after_neo = _safe_mapping(after.get("neo4j"))
    before_chroma = chroma_total(before)
    after_chroma = chroma_total(after)
    return {
        "chroma_total_count_delta": (
            int(after_chroma) - int(before_chroma)
            if before_chroma is not None and after_chroma is not None
            else None
        ),
        "neo4j_node_count_delta": (
            int(after_neo.get("node_count") or 0) - int(before_neo.get("node_count") or 0)
            if before_neo.get("node_count") is not None and after_neo.get("node_count") is not None
            else None
        ),
        "neo4j_relationship_count_delta": (
            int(after_neo.get("relationship_count") or 0) - int(before_neo.get("relationship_count") or 0)
            if before_neo.get("relationship_count") is not None
            and after_neo.get("relationship_count") is not None
            else None
        ),
    }


def _write_reports(report: Mapping[str, Any], *, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"full_main_chain_validation_{stamp}.json"
    md_path = output_dir / f"full_main_chain_validation_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, md_path


def _render_markdown(report: Mapping[str, Any]) -> str:
    meta = _safe_mapping(report.get("meta"))
    service_delta = _safe_mapping(report.get("service_delta"))
    before = _safe_mapping(report.get("services_before"))
    after = _safe_mapping(report.get("services_after"))
    cases = report.get("cases") if isinstance(report.get("cases"), list) else []
    lines: list[str] = [
        "# Full Main Chain Validation",
        "",
        f"- generated_at: `{meta.get('generated_at')}`",
        f"- base_url: `{meta.get('base_url')}`",
        f"- session_id: `{meta.get('session_id')}`",
        f"- person_id: `{meta.get('person_id')}`",
        f"- cases: `{len(cases)}`",
        f"- chroma_total_count_delta: `{service_delta.get('chroma_total_count_delta')}`",
        f"- neo4j_node_count_delta: `{service_delta.get('neo4j_node_count_delta')}`",
        f"- neo4j_relationship_count_delta: `{service_delta.get('neo4j_relationship_count_delta')}`",
        "",
        "## Service Snapshot",
        "",
        f"- Redis before/after: `{_safe_mapping(before.get('redis')).get('ok')}` -> `{_safe_mapping(after.get('redis')).get('ok')}`",
        f"- Chroma before: `{_summarize_chroma(before)}`",
        f"- Chroma after: `{_summarize_chroma(after)}`",
        f"- Neo4j before: `{_summarize_neo4j(before)}`",
        f"- Neo4j after: `{_summarize_neo4j(after)}`",
        "",
        "## Case Outputs",
        "",
    ]
    for row_raw in cases:
        row = _safe_mapping(row_raw)
        lines.extend(
            [
                f"### {row.get('case_id')} / {row.get('scene')}",
                "",
                f"- user: {row.get('message')}",
                f"- expected_focus: {row.get('expected_focus')}",
                f"- ok: `{row.get('ok')}` decision: `{row.get('decision')}` request_id: `{row.get('request_id')}` latency_ms: `{row.get('latency_ms')}`",
                f"- audit_path: `{row.get('audit_path')}` surface_main_chain_used: `{row.get('surface_main_chain_used')}`",
                f"- emotion: user=`{row.get('user_emotion_tag')}` natsume=`{row.get('natsume_emotion_tag')}` vts=`{row.get('vts_expression_id')}` confidence=`{row.get('emotion_confidence')}`",
                f"- packet_trace: `{json.dumps(row.get('packet_trace') or {}, ensure_ascii=False)}`",
                f"- generation_trace_present: `{row.get('generation_trace_present')}`",
                "",
                "reply:",
                "",
                "```text",
                str(row.get("full_reply") or ""),
                "```",
                "",
                "manual_quality:",
                "",
                "manual_notes:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _summarize_chroma(snapshot: Mapping[str, Any]) -> str:
    chroma = _safe_mapping(snapshot.get("chroma"))
    if not chroma.get("ok"):
        return f"DOWN {chroma.get('error')}"
    parts = []
    collections = chroma.get("collections")
    if isinstance(collections, list):
        for row_raw in collections:
            row = _safe_mapping(row_raw)
            parts.append(f"{row.get('name')}={row.get('count')}")
    return ", ".join(parts) if parts else "no collections"


def _summarize_neo4j(snapshot: Mapping[str, Any]) -> str:
    neo4j = _safe_mapping(snapshot.get("neo4j"))
    if not neo4j.get("ok"):
        return f"DOWN {neo4j.get('error')}"
    return f"nodes={neo4j.get('node_count')} relationships={neo4j.get('relationship_count')}"


def _strip_scoring_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in {"score_trace", "candidates", "selected", "dimensions", "quality"}:
                continue
            sanitized[str(key)] = _strip_scoring_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_strip_scoring_payload(item) for item in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("HOST_APP_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--session-id", default=f"full-main-chain-manual-{int(time.time())}")
    parser.add_argument("--person-id", default="manual_quality_user")
    parser.add_argument("--timeout-s", type=float, default=150.0)
    parser.add_argument("--sleep-s", type=float, default=1.0)
    parser.add_argument("--postprocess-wait-s", type=float, default=8.0)
    parser.add_argument("--max-cases", type=int, default=len(CASES))
    parser.add_argument("--case-ids", default="", help="Comma-separated case ids to run in suite order.")
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args(argv)

    base_url = str(args.base_url).rstrip("/")
    session_id = str(args.session_id)
    person_id = str(args.person_id)
    requested_case_ids = [item.strip() for item in str(args.case_ids or "").split(",") if item.strip()]
    if requested_case_ids:
        requested = set(requested_case_ids)
        selected_cases = [case for case in CASES if case.case_id in requested]
        missing = [case_id for case_id in requested_case_ids if case_id not in {case.case_id for case in selected_cases}]
        if missing:
            parser.error(f"unknown case id(s): {', '.join(missing)}")
    else:
        selected_cases = list(CASES[: max(1, int(args.max_cases))])

    print(f"[full-main-chain] base_url={base_url}")
    print(f"[full-main-chain] session_id={session_id}")
    print(f"[full-main-chain] cases={len(selected_cases)}")

    services_before = _service_snapshot(base_url)
    case_rows: list[dict[str, Any]] = []
    raw_responses: list[dict[str, Any]] = []

    for index, case in enumerate(selected_cases, start=1):
        print(f"[{index:02d}/{len(selected_cases):02d}] {case.case_id}: {case.message}")
        payload = _case_payload(case, person_id=person_id, session_id=session_id)
        response = _post_chat_sse(base_url=base_url, payload=payload, timeout_s=float(args.timeout_s))
        summary = _extract_case_summary(case, response)
        case_rows.append(summary)
        raw_responses.append(
            {
                "case_id": case.case_id,
                "request": payload,
                "response": {
                    "ok": response.get("ok"),
                    "status_code": response.get("status_code"),
                    "latency_ms": response.get("latency_ms"),
                    "error": response.get("error"),
                    "commit_event": _strip_scoring_payload(response.get("commit_event")),
                    "event_count": len(response.get("events") or []),
                },
            }
        )
        reply = str(summary.get("full_reply") or "").replace("\n", " / ")
        print(f"    -> {summary.get('decision')} {summary.get('natsume_emotion_tag')} {summary.get('vts_expression_id')} | {reply[:90]}")
        time.sleep(max(0.0, float(args.sleep_s)))

    if args.postprocess_wait_s > 0:
        print(f"[full-main-chain] waiting postprocess {args.postprocess_wait_s:.1f}s")
        time.sleep(float(args.postprocess_wait_s))

    services_after = _service_snapshot(base_url)
    report = {
        "meta": {
            "script": "tools/full_main_chain_validation_cases.py",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url,
            "session_id": session_id,
            "person_id": person_id,
            "case_count": len(case_rows),
        },
        "services_before": services_before,
        "services_after": services_after,
        "service_delta": _counts_delta(services_before, services_after),
        "cases": case_rows,
        "raw_responses": raw_responses,
    }
    json_path, md_path = _write_reports(report, output_dir=Path(args.out_dir))
    print(f"[full-main-chain] json={json_path}")
    print(f"[full-main-chain] markdown={md_path}")

    failed = [row for row in case_rows if not row.get("ok")]
    if failed:
        print(f"[full-main-chain] failed_cases={len(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
