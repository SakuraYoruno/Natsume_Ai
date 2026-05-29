#!/usr/bin/env python
"""Live batch probe for LifeMemory recall through the real /v1/chat chain.

The deterministic scenario batch proves routing in-process. This probe seeds
real runtime LifeMemory rows into the configured SQLite ledger, sends several
natural follow-up messages to the running Host service, then validates the trace
receipt/readiness packet that crossed the actual surface chain.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import uuid
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.observability_persist import persist_life_action_event_memory  # noqa: E402
from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: E402
from host_brain.memory.life_memory import LifeMemoryConsolidator, LifeMemoryStore  # noqa: E402


DEFAULT_BASE_URL = "http://127.0.0.1:18080"
DEFAULT_DB_PATH = "host_brain/data/audit.sqlite3"
DEFAULT_ENV_FILE = "runtime/host/host.runtime.env"
DEFAULT_OUT_DIR = "runtime/host/logs"
CN_TZ = timezone(timedelta(hours=8))
FORBIDDEN_REPLY_TERMS = (
    "突然问这个",
    "硬要说",
    "不知道",
    "没看到",
    "memory://",
    "evd://",
    "chat://",
)


@dataclass(frozen=True)
class RealSceneCase:
    case_id: str
    message: str
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    min_items: int = 0
    expected_contract: str = ""
    expected_need_type: str = ""
    expected_status: str = "READY"


def _load_e2e_module() -> Any:
    module_path = ROOT / "scripts" / "runtime" / "test_surface_memory_recall_e2e.py"
    spec = importlib.util.spec_from_file_location("_surface_memory_recall_e2e", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


E2E = _load_e2e_module()


def run_real_scene_batch(
    *,
    base_url: str,
    db_path: Path,
    session_id: str,
    person_id: str,
    timeout_sec: float = 300.0,
    trace_timeout_sec: float = 60.0,
    skip_health: bool = False,
) -> dict[str, Any]:
    started = int(time.time() * 1000)
    base = _resolve_base_url(base_url)
    os.environ["Natsume_AUDIT_DB"] = str(db_path)
    health = {"ok": True, "skipped": True} if skip_health else E2E._http_json("GET", f"{base}/health?skip_dependency_probes=1", timeout_sec=10.0)
    external_ready = {"ok": True, "skipped": True} if skip_health else E2E._check_external_env_ready()
    seed = _seed_real_scene_world(db_path=db_path, session_id=session_id, person_id=person_id, ts_ms=started)
    cases = _cases(seed)
    results = [
        _run_case(
            case=case,
            base_url=base,
            db_path=db_path,
            session_id=session_id,
            person_id=person_id,
            timeout_sec=timeout_sec,
            trace_timeout_sec=trace_timeout_sec,
        )
        for case in cases
    ]
    health_ok = bool(health.get("ok")) and bool(external_ready.get("ok"))
    return {
        "schema": "Natsume.life_memory_real_scene_batch.v1",
        "started_ts_ms": started,
        "finished_ts_ms": int(time.time() * 1000),
        "base_url": base,
        "db_path": str(db_path),
        "session_id": session_id,
        "person_id": person_id,
        "health": health,
        "external_ready": external_ready,
        "seed": seed,
        "success": health_ok and all(item["passed"] for item in results),
        "case_count": len(results),
        "passed_count": sum(1 for item in results if item["passed"]),
        "failed_count": sum(1 for item in results if not item["passed"]),
        "cases": results,
    }


def _seed_real_scene_world(*, db_path: Path, session_id: str, person_id: str, ts_ms: int) -> dict[str, Any]:
    ledger = SQLiteLedger(db_path=db_path)
    try:
        store = LifeMemoryStore(ledger=ledger)
        now = datetime.now(tz=CN_TZ)
        yesterday_dt = now - timedelta(days=1)
        run_id = f"real-scene-{ts_ms}-{uuid.uuid4().hex[:8]}"
        day_specs = [
            ("today", now, "今天"),
            ("yesterday", yesterday_dt, "昨天"),
            ("day_before_yesterday", now - timedelta(days=2), "前天"),
            ("three_days_ago", now - timedelta(days=3), "三天前"),
        ]
        deliveries: dict[str, Any] = {}
        for label, dt, cn_label in day_specs:
            delivery_ts = int(datetime(dt.year, dt.month, dt.day, 12, tzinfo=CN_TZ).timestamp() * 1000)
            cards = _cards(cn_label, label)
            result = persist_life_action_event_memory(
                event_source="codex_production",
                action_kind="lightnovel_recommendation_delivery",
                action_id=f"{run_id}:{label}:lightnovel_gateway_delivery",
                actor_id="natsume",
                request_id=f"req-{run_id}-{label}",
                intent_id="codex_lightnovel_recommender",
                person_id=person_id,
                session_id=session_id,
                status="COMPLETED",
                action_summary=f"我在{cn_label}给 {person_id} 推荐了 3 本轻小说。",
                ts_ms=delivery_ts,
                evidence_refs=[f"gateway://message/{run_id}/{label}/card/{index}" for index in range(1, 4)],
                life_memory_store=store,
                episodic_store=None,
                metadata={
                    "platform": "qq",
                    "conversation_id": session_id,
                    "selected_recommendations": cards,
                    "e2e_probe": True,
                    "real_scene_batch": run_id,
                },
            )
            deliveries[label] = {
                "episode_id": (result.get("life_memory") or {}).get("episode_id"),
                "date": dt.date().isoformat(),
                "cards": cards,
            }
        store.write_chat_episode(
            request_id=f"req-{run_id}-preference",
            session_id=session_id,
            person_id=person_id,
            user_message="以后推荐轻小说时，每本书的简介后应该换行，别模板腔。",
            assistant_reply="记住了，后面按自然的卡片格式发。",
            ts_ms=ts_ms - 500,
            metadata={"e2e_probe": True, "real_scene_batch": run_id},
        )
        store.write_background_episode(
            event_source="codex_production",
            event_id=f"{run_id}:deepseek-failure",
            person_id=person_id,
            session_id=session_id,
            task_id="llm_pool_call",
            status="FAILED",
            reason_code="DEEPSEEK_TIMEOUT",
            detail="DeepSeek 失败原因：供应商超时，后续优先检查网络和 provider 健康状态。",
            ts_ms=ts_ms - 400,
            metadata={"e2e_probe": True, "real_scene_batch": run_id},
        )
        wrong = store.write_chat_episode(
            request_id=f"req-{run_id}-wrong-cat-cover",
            session_id=session_id,
            person_id=person_id,
            user_message="刚才那三本里你选哪本？",
            assistant_reply="我选封面有猫的那本。",
            ts_ms=ts_ms - 300,
            metadata={"e2e_probe": True, "real_scene_batch": run_id},
        )
        correction = store.write_chat_episode(
            request_id=f"req-{run_id}-fix-cat-cover",
            session_id=session_id,
            person_id=person_id,
            user_message="不对，没有封面有猫这本。",
            assistant_reply="你说得对，我撤回这个判断。",
            ts_ms=ts_ms - 250,
            metadata={"e2e_probe": True, "real_scene_batch": run_id},
        )
        store.correct_episode(
            episode_id=str(wrong["episode_id"]),
            correction_kind="correction",
            correction_episode_id=str(correction["episode_id"]),
            reason_code="USER_CORRECTED_FALSE_MEMORY",
            detail="用户指出没有封面有猫这本。",
            suppress=False,
            ts_ms=ts_ms - 200,
        )
        store.write_background_episode(
            event_source="browser",
            event_id=f"{run_id}:onebot-edge-report",
            person_id=person_id,
            session_id=session_id,
            task_id="web_research",
            status="COMPLETED",
            reason_code="WEB_REPORT_READ",
            detail=(
                "昨天阅读了一个网页报告；网页主题：OneBot 部署位置核对；"
                "正文内容：OneBot 在 edge-lan 上运行，本机只负责 host；"
                "依据：报告正文包含 edge-lan ssh、网关地址和运行位置三项互相一致的信息。"
            ),
            ts_ms=int(
                datetime(
                    yesterday_dt.year,
                    yesterday_dt.month,
                    yesterday_dt.day,
                    18,
                    tzinfo=CN_TZ,
                ).timestamp()
                * 1000
            ),
            metadata={
                "domain": "web",
                "e2e_probe": True,
                "real_scene_batch": run_id,
                "evidence_refs": [
                    f"https://example.com/{run_id}/onebot-edge-report",
                    f"doc://web/{run_id}/onebot-edge-report#body",
                ],
                "items": [
                    {
                        "title": "OneBot 部署位置网页报告",
                        "detail_url": f"https://example.com/{run_id}/onebot-edge-report",
                        "content_excerpt": "OneBot 在 edge-lan 上运行，本机只负责 host。",
                        "body_ref": f"doc://web/{run_id}/onebot-edge-report#body",
                    }
                ],
            },
        )
        semantic = LifeMemoryConsolidator(store=store).distill_semantic_memories(
            limit=120,
            max_records=32,
            ts_ms=ts_ms - 100,
            operator_node_id="life_memory_real_scene_batch",
        )
        return {
            "run_id": run_id,
            "deliveries": deliveries,
            "wrong_episode_id": wrong["episode_id"],
            "correction_episode_id": correction["episode_id"],
            "semantic": semantic,
        }
    finally:
        ledger.close()


def _cards(day_label: str, slug: str) -> list[dict[str, object]]:
    return [
        {
            "title": f"{day_label}推荐 A：培养失败的前美少女童星",
            "selection_reason": "恋爱日常，关系展开轻快",
            "intro_snippet": "前童星和伸手帮忙的人重新建立关系。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{slug}-a",
        },
        {
            "title": f"{day_label}推荐 B：与神在河岸",
            "selection_reason": "奇幻身份钩子，资料完整",
            "intro_snippet": "神明、河岸与现实感交叠，标题辨识度高。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{slug}-b",
        },
        {
            "title": f"{day_label}推荐 C：异世界皇女皇帝线",
            "selection_reason": "皇女、皇帝、异世界身份线明确",
            "intro_snippet": "转移后与皇女结婚并成为皇帝，设定直给。",
            "detail_url": f"https://www.lightnovel.fun/cn/detail/{slug}-c",
        },
    ]


def _cases(seed: Mapping[str, Any]) -> list[RealSceneCase]:
    yesterday_b = str(seed["deliveries"]["yesterday"]["cards"][1]["title"])
    return [
        RealSceneCase(
            case_id="current_selection",
            message="这三本书，你喜欢哪本？",
            must_contain=("今天推荐",),
            must_not_contain=("DeepSeek", "封面有猫"),
            expected_contract="anaphoric_selection",
            expected_need_type="collection_selection",
        ),
        RealSceneCase(
            case_id="yesterday_three",
            message="昨天三本呢？",
            must_contain=("昨天推荐",),
            must_not_contain=("今天推荐", "DeepSeek"),
            min_items=3,
            expected_contract="collection_date",
            expected_need_type="collection",
        ),
        RealSceneCase(
            case_id="day_before_yesterday_three",
            message="前天三本呢？",
            must_contain=("前天推荐",),
            must_not_contain=("今天推荐", "昨天推荐", "DeepSeek"),
            min_items=3,
            expected_contract="collection_date",
            expected_need_type="collection",
        ),
        RealSceneCase(
            case_id="recent_many",
            message="这几天十几本里有哪些？",
            must_contain=("记录 12 个条目",),
            must_not_contain=("DeepSeek",),
            min_items=10,
            expected_contract="collection_recent",
            expected_need_type="collection",
        ),
        RealSceneCase(
            case_id="specific_prior_entity",
            message=f"{yesterday_b} 这本好看吗？",
            must_contain=(yesterday_b,),
            must_not_contain=("今天推荐 B", "DeepSeek"),
            expected_contract="anaphoric_selection",
            expected_need_type="entity_or_collection_selection",
        ),
        RealSceneCase(
            case_id="preference_lookup",
            message="还记得我说推荐格式要怎样吗？",
            must_contain=("简介后应该换行",),
            must_not_contain=("DeepSeek",),
            expected_contract="preference",
            expected_need_type="semantic_or_episode_preference",
        ),
        RealSceneCase(
            case_id="failure_debug",
            message="DeepSeek失败原因是什么？",
            must_contain=("供应商超时",),
            must_not_contain=("昨天推荐",),
            expected_contract="failure_debug",
            expected_need_type="failure_episode",
        ),
        RealSceneCase(
            case_id="correction_lookup",
            message="你刚才是不是记错了封面猫那本？",
            must_contain=("没有封面有猫",),
            must_not_contain=("我选封面有猫",),
            expected_contract="correction",
            expected_need_type="correction_chain",
        ),
        RealSceneCase(
            case_id="web_evidence_review",
            message="昨天那个网页内容靠谱吗？依据是什么？",
            must_contain=("OneBot 在 edge-lan",),
            must_not_contain=("DeepSeek",),
            expected_contract="evidence_review",
            expected_need_type="evidence_review",
        ),
    ]


def _run_case(
    *,
    case: RealSceneCase,
    base_url: str,
    db_path: Path,
    session_id: str,
    person_id: str,
    timeout_sec: float,
    trace_timeout_sec: float,
) -> dict[str, Any]:
    started = time.time()
    chat = E2E._stream_chat_sse(
        url=f"{base_url}/v1/chat",
        payload={
            "message": case.message,
            "session_id": session_id,
            "person_id": person_id,
            "thread_id": session_id,
            "input_source": "life_memory_real_scene_batch",
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
    if request_id:
        trace = _merge_runtime_surface_memory_trace(db_path=db_path, request_id=request_id, trace=trace)
    checks = _validate_case(case=case, chat=chat, trace=trace)
    return {
        "case": asdict(case),
        "passed": all(checks.values()),
        "checks": checks,
        "request_id": request_id,
        "latency_ms": int((time.time() - started) * 1000),
        "reply": commit.get("narrative"),
        "trace_found": bool(trace.get("found")),
        "packet": _compact_packet(trace.get("surface_memory_recall_packet") if isinstance(trace, dict) else {}),
        "surface_memory_trace": _compact_surface_memory_trace(trace.get("surface_memory_trace") if isinstance(trace, dict) else {}),
        "query_plan": trace.get("query_plan") if isinstance(trace, dict) else {},
    }


def _validate_case(*, case: RealSceneCase, chat: Mapping[str, Any], trace: Mapping[str, Any]) -> dict[str, bool]:
    commit = chat.get("commit_event") if isinstance(chat.get("commit_event"), Mapping) else {}
    reply = str(commit.get("narrative") or "")
    packet = trace.get("surface_memory_recall_packet") if isinstance(trace, Mapping) else {}
    packet = packet if isinstance(packet, Mapping) else {}
    facts = [str(item) for item in list(packet.get("facts") or [])]
    joined = "\n".join(facts)
    surface_trace = trace.get("surface_memory_trace") if isinstance(trace, Mapping) and isinstance(trace.get("surface_memory_trace"), Mapping) else {}
    trace_payload = packet.get("trace") if isinstance(packet.get("trace"), Mapping) else {}
    if surface_trace:
        trace_payload = {**dict(trace_payload), **dict(surface_trace)}
    receipt = trace_payload.get("recall_receipt") if isinstance(trace_payload.get("recall_receipt"), Mapping) else {}
    coverage = trace_payload.get("recall_coverage") if isinstance(trace_payload.get("recall_coverage"), Mapping) else {}
    readiness = trace_payload.get("recall_answer_readiness") if isinstance(trace_payload.get("recall_answer_readiness"), Mapping) else {}
    need = trace_payload.get("recall_need") if isinstance(trace_payload.get("recall_need"), Mapping) else {}
    selected_items = int(receipt.get("selected_item_count") or coverage.get("selected_item_count") or 0)
    return {
        "chat_ok": bool(chat.get("ok")),
        "reply_nonempty": bool(reply.strip()),
        "reply_clean": not any(term in reply for term in FORBIDDEN_REPLY_TERMS),
        "trace_found": bool(trace.get("found")),
        "packet_has_facts": bool(facts),
        "contains": all(term in joined for term in case.must_contain),
        "excludes": all(term not in joined for term in case.must_not_contain),
        "min_items": selected_items >= int(case.min_items or 0),
        "contract": not case.expected_contract or str(need.get("recall_contract_id") or "") == case.expected_contract,
        "need_type": not case.expected_need_type or str(need.get("need_type") or "") == case.expected_need_type,
        "readiness": not case.expected_status or str(readiness.get("status") or "") == case.expected_status,
    }


def _merge_runtime_surface_memory_trace(*, db_path: Path, request_id: str, trace: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(trace or {})
    runtime_trace = _fetch_pipeline_surface_memory_trace(db_path=db_path, request_id=request_id)
    if not runtime_trace:
        return merged
    surface_trace = runtime_trace.get("surface_memory_trace")
    if isinstance(surface_trace, Mapping) and surface_trace:
        merged["surface_memory_trace"] = dict(surface_trace)
    query_plan = runtime_trace.get("query_plan")
    if isinstance(query_plan, Mapping) and query_plan and not merged.get("query_plan"):
        merged["query_plan"] = dict(query_plan)
    debug_trace = runtime_trace.get("surface_memory_debug_trace")
    if isinstance(debug_trace, Mapping):
        packet = debug_trace.get("episode_recall_packet")
        if isinstance(packet, Mapping):
            merged["surface_memory_debug_packet"] = dict(packet)
            if not merged.get("surface_memory_recall_packet"):
                merged["surface_memory_recall_packet"] = _packet_from_debug_packet(packet, surface_trace)
    return merged


def _fetch_pipeline_surface_memory_trace(*, db_path: Path, request_id: str) -> dict[str, Any]:
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT payload
                FROM retrieval_event_log
                WHERE request_id = ? AND op_kind = 'PIPELINE'
                ORDER BY id DESC
                LIMIT 1
                """,
                (request_id,),
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return {}
    if row is None:
        return {}
    try:
        payload = json.loads(str(row["payload"] or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _packet_from_debug_packet(packet: Mapping[str, Any], surface_trace: Any) -> dict[str, Any]:
    rows = [dict(row) for row in list(packet.get("rows") or []) if isinstance(row, Mapping)]
    return {
        "facts": [str(row.get("fact") or row.get("snippet") or row.get("text") or "") for row in rows if str(row.get("fact") or row.get("snippet") or row.get("text") or "").strip()],
        "evidence_refs": list(packet.get("evidence_refs") or []),
        "source_refs": list(packet.get("source_refs") or []),
        "route_ids": list(packet.get("route_ids") or []),
        "candidate_count": int(packet.get("candidate_count") or 0),
        "trace": dict(surface_trace or {}) if isinstance(surface_trace, Mapping) else {},
    }


def _compact_packet(packet: Any) -> dict[str, Any]:
    if not isinstance(packet, Mapping):
        return {}
    trace = packet.get("trace") if isinstance(packet.get("trace"), Mapping) else {}
    return {
        "facts": list(packet.get("facts") or [])[:6],
        "reason_code": packet.get("reason_code"),
        "confidence": packet.get("confidence"),
        "route_ids": list(packet.get("route_ids") or []),
        "recall_need": trace.get("recall_need"),
        "recall_receipt": trace.get("recall_receipt"),
        "recall_coverage": trace.get("recall_coverage"),
        "recall_answer_readiness": trace.get("recall_answer_readiness"),
    }


def _compact_surface_memory_trace(trace: Any) -> dict[str, Any]:
    if not isinstance(trace, Mapping):
        return {}
    return {
        "recall_need": trace.get("recall_need"),
        "recall_receipt": trace.get("recall_receipt"),
        "recall_coverage": trace.get("recall_coverage"),
        "recall_answer_readiness": trace.get("recall_answer_readiness"),
        "lightnovel_deep_review": trace.get("lightnovel_deep_review"),
        "generic_evidence_review": trace.get("generic_evidence_review"),
    }


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


def _resolve_base_url(value: str) -> str:
    explicit = str(value or "").strip()
    if explicit:
        return explicit.rstrip("/")
    env_url = str(os.environ.get("HOST_APP_BASE_URL") or "").strip()
    if env_url:
        return env_url.rstrip("/")
    host = str(os.environ.get("HOST_APP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = str(os.environ.get("HOST_APP_PORT") or "18080").strip() or "18080"
    return f"http://{host}:{port}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live batch LifeMemory recall probes through /v1/chat.")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--person-id", default="life-memory-real-scene-e2e")
    parser.add_argument("--timeout-sec", type=float, default=300.0)
    parser.add_argument("--trace-timeout-sec", type=float, default=60.0)
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--out", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _load_env_file(_resolve_path(str(args.env_file or DEFAULT_ENV_FILE)))
    db_path = _resolve_path(str(args.db_path or DEFAULT_DB_PATH))
    session_id = str(args.session_id or "").strip() or f"life-memory-real-scene-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    person_id = str(args.person_id or "life-memory-real-scene-e2e").strip()
    report = run_real_scene_batch(
        base_url=str(args.base_url or ""),
        db_path=db_path,
        session_id=session_id,
        person_id=person_id,
        timeout_sec=float(args.timeout_sec),
        trace_timeout_sec=float(args.trace_timeout_sec),
        skip_health=bool(args.skip_health),
    )
    out_path = _resolve_path(str(args.out)) if str(args.out or "").strip() else ROOT / DEFAULT_OUT_DIR / f"life_memory_real_scene_batch_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    compact = {
        "success": report["success"],
        "passed_count": report["passed_count"],
        "failed_count": report["failed_count"],
        "session_id": session_id,
        "out": str(out_path),
        "failed_cases": [
            item["case"]["case_id"]
            for item in report["cases"]
            if not item["passed"]
        ],
    }
    print("SUMMARY " + json.dumps(compact, ensure_ascii=False))
    print(f"WROTE {out_path}")
    return 0 if report["success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
