from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.app.surface_emotion_bond_flow import apply_surface_emotion_bond_flow  # noqa: E402
from host_brain.bond.bond_dynamics import BondDynamicsWriter  # noqa: E402
from host_brain.bond.bond_stage_engine import BondStageEngine  # noqa: E402
from host_brain.bond.summarizer import derive_bond_summary  # noqa: E402
from host_brain.persona.surface_affect_control import build_surface_affect_control_packet  # noqa: E402
from host_brain.persona.surface_main_chain import run_surface_main_chain  # noqa: E402
from host_brain.persona.surface_memory_recall import (  # noqa: E402
    SURFACE_MEMORY_RECALL_SKIPPED,
    build_surface_turn_memory_text,
    recall_surface_memory_for_context,
    write_surface_turn_memory,
)
from host_brain.persona.isolated_surface_continuity import ThreeLayerContinuityContext  # noqa: E402


DEFAULT_OUT = ROOT / "artifacts" / "surface_memory_emotion_loop"
SOURCE_REF_MARKERS = ("memory://", "evd://", "chat://", "source_refs=", "evidence_refs=", "metadata")
SOUL = "你是数字生命夏目蓝。中文私聊，反应快，有一点嘴硬；事实准确性永远高于角色感。"


@dataclass(frozen=True)
class FixtureMemoryRecord:
    memory_id: str
    text: str
    evidence_refs: tuple[str, ...]
    emotion_tag: str
    emotion_valence: float
    emotion_arousal: float


class FixtureEpisodicStore:
    def __init__(self) -> None:
        self.records: list[FixtureMemoryRecord] = []

    def write_memory(self, **kwargs: Any) -> SimpleNamespace:
        memory_id = f"surface-loop-mem-{len(self.records) + 1}"
        record = FixtureMemoryRecord(
            memory_id=memory_id,
            text=str(kwargs.get("text") or "").strip(),
            evidence_refs=tuple(str(item) for item in list(kwargs.get("evidence_refs") or [])),
            emotion_tag=str(kwargs.get("emotion_tag") or "NEUTRAL").strip().upper(),
            emotion_valence=float(kwargs.get("emotion_valence") or 0.0),
            emotion_arousal=float(kwargs.get("emotion_arousal") or 0.3),
        )
        self.records.append(record)
        return SimpleNamespace(
            success=True,
            reason_code=None,
            detail="fixture episodic memory written",
            memory_id=memory_id,
        )


class FixtureRecallService:
    def __init__(self, store: FixtureEpisodicStore) -> None:
        self.store = store
        self.calls: list[dict[str, Any]] = []

    def recall(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(dict(kwargs))
        allowed = {str(item) for item in list(kwargs.get("target_store_ids") or [])}
        candidates: list[dict[str, Any]] = []
        for record in self.store.records:
            candidates.append(
                {
                    "doc_id": record.memory_id,
                    "source_ref": f"memory://episodic/{record.memory_id}",
                    "evidence_ref": record.evidence_refs[0] if record.evidence_refs else f"evd://episodic/{record.memory_id}",
                    "snippet": record.text,
                    "score": 0.94,
                    "confidence": 0.94,
                    "metadata": {
                        "store_id": "episodic_vector",
                        "emotion_tag": record.emotion_tag,
                    },
                }
            )
        candidates.extend(
            [
                {
                    "doc_id": "sem-low-energy",
                    "source_ref": "ledger://semantic_memory/sem-low-energy",
                    "evidence_ref": "semantic://sem-low-energy",
                    "snippet": "用户低能量时偏好少追问，先给一个能推进的小步骤。",
                    "score": 0.88,
                    "metadata": {"store_id": "semantic_memory"},
                },
                {
                    "doc_id": "bond-baseline",
                    "source_ref": "neo4j://bond/event/bond-baseline",
                    "evidence_ref": "evd://bond/bond-baseline",
                    "snippet": "关系阶段：关系好的兄妹，熟人私聊，不升级亲密。",
                    "score": 0.81,
                    "metadata": {"store_id": "neo4j_bond"},
                },
            ]
        )
        if allowed:
            candidates = [item for item in candidates if str(item.get("metadata", {}).get("store_id") or "") in allowed]
        routes = [
            {"route_id": "semantic", "store_id": "semantic_memory"},
            {"route_id": "episodic", "store_id": "episodic_vector"},
            {"route_id": "bond", "store_id": "neo4j_bond"},
        ]
        if allowed:
            routes = [item for item in routes if item["store_id"] in allowed]
        return SimpleNamespace(
            output={
                "query": str(kwargs.get("query") or ""),
                "status": "OK",
                "stage": "S_RET_RECALL",
                "degraded": False,
                "reason_code": None,
                "routes": routes,
                "route_rows": [],
                "route_errors": [],
                "candidate_count": len(candidates),
                "candidates": candidates,
            }
        )


class ApplyingBondQueue:
    def __init__(self, engine: BondStageEngine) -> None:
        self.engine = engine
        self.writer = BondDynamicsWriter(stage_engine=engine)
        self.payloads: list[dict[str, Any]] = []
        self.results: list[Any] = []

    def enqueue(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.payloads.append(row)
        result = self.writer.apply_update(**row)
        self.results.append(result)
        transition = dict(result.event.get("stage_transition") or {})
        return {
            "status": "APPLIED" if result.success else result.status,
            "queue_size": len(self.payloads),
            "transition_status": transition.get("transition_status"),
            "reason_code": result.reason_code,
        }


class ImmediateBondQueue:
    """Synchronous queue shim used by live validation to hit the real writer."""

    def __init__(self, writer: Any) -> None:
        self.writer = writer
        self.payloads: list[dict[str, Any]] = []
        self.results: list[Any] = []

    def enqueue(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(payload)
        self.payloads.append(row)
        result = self.writer.apply_update(**row)
        self.results.append(result)
        status = "APPLIED" if bool(getattr(result, "success", False)) else str(getattr(result, "status", "REJECTED") or "REJECTED")
        return {
            "status": status,
            "reason_code": getattr(result, "reason_code", None),
            "queue_size": len(self.payloads),
            "writer_status": getattr(result, "status", None),
            "event_id": dict(getattr(result, "event", {}) or {}).get("event_id"),
        }


@dataclass
class LoopComponents:
    mode: str
    store: Any
    recall_service: Any
    bond_engine: BondStageEngine
    bond_queue: Any
    query_embedding: list[float]
    query_embedding_model: str
    query_embedding_source: str
    service_status: dict[str, Any]
    run_id: str
    request_prefix: str
    user_id: str
    session_id: str
    base_ts_ms: int
    close: Callable[[], dict[str, Any]] | None = None


async def run_surface_memory_emotion_loop(args: argparse.Namespace) -> dict[str, Any]:
    mode = _resolve_mode(args)
    out_dir = _resolve_out_dir(args=args, mode=mode)
    out_dir.mkdir(parents=True, exist_ok=True)
    first_user = "上次那个问题我又卡住了。"
    first_reply = "上次卡在输入输出，这轮先看那一块。"
    current_user = "今天有点难受，还记得上次我卡在哪里吗"
    components: LoopComponents | None = None
    try:
        components = _build_loop_components(
            mode=mode,
            args=args,
            out_dir=out_dir,
            first_user=first_user,
            first_reply=first_reply,
            current_user=current_user,
        )
    except Exception as exc:
        return _write_failure_result(
            out_dir=out_dir,
            mode=mode,
            reason_code="VALIDATION_SETUP_FAILED",
            exc=exc,
            details={"stage": "build_loop_components"},
        )

    store = components.store
    recall_service = components.recall_service
    bond_engine = components.bond_engine
    bond_queue = components.bond_queue
    write_request_id = f"{components.request_prefix}-write"
    recall_request_id = f"{components.request_prefix}-recall"
    surface_request_id = f"{components.request_prefix}-surface"
    ts_write = components.base_ts_ms
    ts_recall = components.base_ts_ms + 1_000
    ts_surface = components.base_ts_ms + 2_000

    memory_write = write_surface_turn_memory(
        episodic_store=store,
        request_id=write_request_id,
        session_id=components.session_id,
        person_id=components.user_id,
        user_message=first_user,
        assistant_reply=first_reply,
        ts_ms=ts_write,
        intent_id="loop_memory_write",
        evidence_refs=[f"surface_turn:{write_request_id}"],
        emotion_tag="ANXIOUS",
        emotion_valence=-0.45,
        emotion_arousal=0.7,
    )

    context = ThreeLayerContinuityContext(
        current_user_message=current_user,
        task_mode_hint="recall_memory",
        response_move_hint="answer_from_memory",
        emotional_tone_hint="soft_containment",
        relationship_distance="familiar",
        information_budget="short",
        question_budget=0,
    )
    recall_packet = recall_surface_memory_for_context(
        recall_service=recall_service,
        context=context,
        request_id=recall_request_id,
        query_embedding=components.query_embedding,
        intent_id="loop_memory_recall",
        person_id=components.user_id,
        session_id=components.session_id,
        ts_ms=ts_recall,
        emotion_filter="ANXIOUS",
        bond_summary={"stage": "关系好的兄妹", "capability_hint": "关系好的兄妹，可轻松拌嘴"},
    )
    affect_packet = build_surface_affect_control_packet(
        emotion_state={
            "emotion_tag": "SAD",
            "emotion_intensity": 0.78,
            "valence": -0.72,
            "emotion_inference_source": "fixture_user_text",
        },
        bond_summary={"relationship_distance": "familiar", "stage": "关系好的兄妹"},
        task_mode="recall_memory",
    )
    surface_result = await run_surface_main_chain(
        _fixture_generate,
        {
            "soul_md": SOUL,
            "user_message": current_user,
            "case_id": "surface_memory_emotion_loop",
            "task_control": {
                "response_move": "answer_from_memory",
                "task_mode": "recall_memory",
                "information_budget": "short",
                "question_budget": 0,
                "facts": ["本轮要验证记忆召回、情绪标签、关系更新能串起来。"],
                "style_anchor": ["先承接难受，再用记忆事实回答。"],
            },
            "memory_packet": recall_packet,
            "affect_packet": affect_packet,
            "max_facts": 8,
            "max_style_anchors": 12,
            "max_must_not_do": 14,
        },
    )
    rendered_prompt = "\n".join(message["content"] for message in surface_result.runtime.messages)
    retrieval_evidence_items = [{"evidence_ref": ref} for ref in recall_packet.evidence_refs]
    bond_update = apply_surface_emotion_bond_flow(
        request_id=surface_request_id,
        person_id=components.user_id,
        session_id=components.session_id,
        user_message=current_user,
        assistant_reply=surface_result.selected_text,
        emotion_context={
            "emotion_tag": surface_result.natsume_emotion_tag,
            "surface_natsume_emotion_tag": surface_result.natsume_emotion_tag,
            "surface_user_emotion_tag": surface_result.user_emotion_tag,
            "emotion_intensity": surface_result.runtime.emotion_confidence,
        },
        bond_summary={"capability_hint": "关系好的兄妹，可轻松拌嘴"},
        reply_plan={"primary_topic": "recall_memory", "intent_id": "loop_surface_reply"},
        retrieval_evidence_items=retrieval_evidence_items,
        ts_ms=ts_surface,
        queue=bond_queue,
    )
    bond_payload = bond_queue.payloads[-1] if bond_queue.payloads else {}
    bond_state = bond_engine.get_state(str(bond_payload.get("user_id") or ""))
    bond_summary = bond_state.to_summary()
    safe_bond_summary = derive_bond_summary(
        {"event_id": "req-loop-surface", "evidence_refs": list(bond_payload.get("evidence_refs") or [])},
        bond_score_state=bond_state,
    )

    details = {
        "memory_write": memory_write.as_dict(),
        "recall_packet": recall_packet.as_dict(),
        "affect_packet": affect_packet.as_dict(),
        "surface_result": surface_result.as_dict(),
        "bond_update": dict(bond_update),
        "bond_payload": bond_payload,
        "bond_state": {
            "user_id": bond_state.user_id,
            "current_stage": bond_state.current_stage,
            "event_count": bond_state.event_count,
            "total_score": bond_state.total_score,
            "recent_scores": list(bond_state.recent_scores),
        },
        "bond_summary": bond_summary,
        "safe_bond_summary": safe_bond_summary,
        "recall_calls": list(getattr(recall_service, "calls", [])),
        "services": components.service_status,
        "query_embedding": {
            "model": components.query_embedding_model,
            "source": components.query_embedding_source,
            "dim": len(components.query_embedding),
        },
    }
    checks = _checks(
        memory_write=memory_write.as_dict(),
        recall_packet=recall_packet.as_dict(),
        surface_result=surface_result.as_dict(),
        rendered_prompt=rendered_prompt,
        bond_update=bond_update,
        bond_state=details["bond_state"],
    )
    summary = {
        "mode": components.mode,
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "passed": all(checks.values()),
        "checks": checks,
        "output_dir": str(out_dir),
        "selected_text": surface_result.selected_text,
        "natsume_emotion_tag": surface_result.natsume_emotion_tag,
        "user_emotion_tag": surface_result.user_emotion_tag,
        "vts_expression_id": surface_result.vts_expression_id,
        "bond_update_kind": bond_update.get("update_kind"),
        "bond_status": bond_update.get("status"),
        "query_embedding_model": components.query_embedding_model,
    }
    close_result: dict[str, Any] = {}
    if components.close is not None:
        close_result = components.close()
        details["close"] = close_result
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_markdown_report(summary, details), encoding="utf-8")
    return {"summary": summary, "details": details}


def _build_loop_components(
    *,
    mode: str,
    args: argparse.Namespace,
    out_dir: Path,
    first_user: str,
    first_reply: str,
    current_user: str,
) -> LoopComponents:
    if mode == "fixture":
        return _build_fixture_components()
    if mode == "live":
        return _build_live_components(
            args=args,
            out_dir=out_dir,
            first_user=first_user,
            first_reply=first_reply,
            current_user=current_user,
        )
    raise ValueError(f"unsupported validation mode: {mode}")


def _build_fixture_components() -> LoopComponents:
    store = FixtureEpisodicStore()
    recall_service = FixtureRecallService(store)
    bond_engine = BondStageEngine()
    bond_queue = ApplyingBondQueue(bond_engine)
    return LoopComponents(
        mode="fixture",
        store=store,
        recall_service=recall_service,
        bond_engine=bond_engine,
        bond_queue=bond_queue,
        query_embedding=[0.1] * 8,
        query_embedding_model="fixture-vector",
        query_embedding_source="fixture-static",
        service_status={"fixture": {"ok": True}},
        run_id="fixture",
        request_prefix="req-loop",
        user_id="user-loop",
        session_id="sess-loop",
        base_ts_ms=1_000,
    )


def _build_live_components(
    *,
    args: argparse.Namespace,
    out_dir: Path,
    first_user: str,
    first_reply: str,
    current_user: str,
) -> LoopComponents:
    from tools.live_env import load_dotenv  # noqa: PLC0415
    from host_brain.audit.sqlite_ledger import SQLiteLedger  # noqa: PLC0415
    from host_brain.bond.bond_dynamics import BondDynamicsWriter  # noqa: PLC0415
    from host_brain.bond.neo4j_client import build_neo4j_bond_client, load_neo4j_runtime_config  # noqa: PLC0415
    from host_brain.bond.user_identity import get_canonical_bond_user_id  # noqa: PLC0415
    from host_brain.core.ids import now_ms  # noqa: PLC0415
    from host_brain.memory.chroma_client import (  # noqa: PLC0415
        PersistentChromaClient,
        embed_text,
        load_chroma_runtime_config,
        resolve_collection_vector_dim,
    )
    from host_brain.memory.episodic import EpisodicStore  # noqa: PLC0415
    from host_brain.retrieval.recall_fusion import RecallFusionServiceContract  # noqa: PLC0415
    from host_brain.retrieval.recall_fusion_config import load_recall_runtime_config  # noqa: PLC0415

    load_dotenv(ROOT / ".env")
    run_id = "live_" + uuid.uuid4().hex[:12]
    base_ts_ms = now_ms()
    bond_user_id = get_canonical_bond_user_id()
    services: dict[str, Any] = {"run_id": run_id}

    chroma_cfg_result = load_chroma_runtime_config()
    services["chroma_config"] = chroma_cfg_result.as_dict()
    if not chroma_cfg_result.success or chroma_cfg_result.config is None or not chroma_cfg_result.config.enabled:
        raise RuntimeError(f"chroma runtime config invalid: {chroma_cfg_result.as_dict()}")
    chroma_client = PersistentChromaClient.from_runtime_config(chroma_cfg_result.config)
    vector_dim = resolve_collection_vector_dim(chroma_client)
    probe_id = f"codex_surface_loop_probe_{uuid.uuid4().hex[:10]}"
    probe_embedding, probe_model = embed_text("surface memory emotion live probe", dim=vector_dim)
    chroma_client.upsert(
        memory_id=probe_id,
        text="surface memory emotion live probe",
        embedding=probe_embedding,
        metadata={"validation": "surface_memory_emotion_loop", "run_id": run_id},
    )
    fetched_probe = chroma_client.get(probe_id)
    chroma_client.delete(probe_id)
    if fetched_probe is None:
        raise RuntimeError("chroma probe upsert/get failed")
    services["chroma"] = {
        "ok": True,
        "endpoint": chroma_cfg_result.config.endpoint,
        "default_collection": chroma_cfg_result.config.default_collection,
        "vector_dim": vector_dim,
        "probe_embedding_model": probe_model,
    }

    recall_cfg_result = load_recall_runtime_config()
    services["recall_config"] = recall_cfg_result.as_dict()
    if not recall_cfg_result.success or recall_cfg_result.config is None:
        raise RuntimeError(f"recall runtime config invalid: {recall_cfg_result.as_dict()}")

    neo4j_cfg_result = load_neo4j_runtime_config()
    services["neo4j_config"] = neo4j_cfg_result.as_dict()
    if not neo4j_cfg_result.success or neo4j_cfg_result.config is None or not neo4j_cfg_result.config.enabled:
        raise RuntimeError(f"neo4j runtime config invalid: {neo4j_cfg_result.as_dict()}")
    neo4j_client = build_neo4j_bond_client(config=neo4j_cfg_result.config)
    if neo4j_client is None:
        raise RuntimeError("neo4j client disabled")

    ledger = SQLiteLedger(db_path=out_dir / f"live_validation_{run_id}.sqlite3", check_same_thread=False)
    bond_engine = BondStageEngine(ledger=ledger)
    writer = BondDynamicsWriter(ledger=ledger, neo4j_client=neo4j_client, stage_engine=bond_engine)
    seed_event_id = f"codex_surface_loop_seed_{uuid.uuid4().hex[:10]}"
    seed_result = writer.apply_update(
        request_id=f"req-surface-loop-{run_id}-seed",
        intent_id="loop_bond_seed",
        session_id=f"sess-surface-loop-{run_id}",
        seq=0,
        user_id=bond_user_id,
        topic_id="recall_memory",
        capability_hint="关系好的兄妹，可轻松拌嘴；上次卡在输入输出；今天有点难受还问记不记得",
        evidence_refs=[f"evd://surface-loop/{run_id}/seed"],
        payload={
            "validation": "surface_memory_emotion_loop",
            "summary": "关系线索：关系好的兄妹。记忆线索：用户上次卡在输入输出；今天有点难受，问还记不记得卡在哪里。",
            "current_user": current_user,
        },
        update_kind="SHARED_MEMORY",
        event_id=seed_event_id,
        ts_ms=base_ts_ms,
        emotion_tag="ANXIOUS",
        emotion_intensity=0.68,
        emotion_valence=-0.45,
    )
    fetched_seed = neo4j_client.get_event(seed_event_id)
    if not bool(seed_result.success) or fetched_seed is None:
        raise RuntimeError("neo4j seed write/get failed")
    services["neo4j"] = {
        "ok": True,
        "uri": neo4j_cfg_result.config.uri,
        "database": neo4j_cfg_result.config.database,
        "seed_event_id": seed_event_id,
        "seed_written": True,
    }

    memory_text = build_surface_turn_memory_text(user_message=first_user, assistant_reply=first_reply)
    query_embedding, query_model = embed_text(memory_text, dim=vector_dim)
    store = EpisodicStore(ledger=ledger, chroma_client=chroma_client)
    recall_service = RecallFusionServiceContract(
        ledger=ledger,
        chroma_client=chroma_client,
        neo4j_client=neo4j_client,
        runtime_config=recall_cfg_result.config,
        node_id="surface_main_chain_memory_recall",
        use_hybrid=False,
    )

    def _close() -> dict[str, Any]:
        closed: dict[str, Any] = {}
        try:
            neo4j_client.close()
            closed["neo4j"] = True
        except Exception as exc:  # pragma: no cover - close best effort
            closed["neo4j"] = f"{type(exc).__name__}: {exc}"
        try:
            ledger.close()
            closed["ledger"] = True
        except Exception as exc:  # pragma: no cover - close best effort
            closed["ledger"] = f"{type(exc).__name__}: {exc}"
        return closed

    return LoopComponents(
        mode="live",
        store=store,
        recall_service=recall_service,
        bond_engine=bond_engine,
        bond_queue=ImmediateBondQueue(writer),
        query_embedding=query_embedding,
        query_embedding_model=query_model,
        query_embedding_source="written_surface_turn_memory_text",
        service_status=services,
        run_id=run_id,
        request_prefix=f"req-surface-loop-{run_id}",
        user_id=bond_user_id,
        session_id=f"sess-surface-loop-{run_id}",
        base_ts_ms=base_ts_ms + 1_000,
        close=_close,
    )


def _fixture_generate(messages: Sequence[Mapping[str, str]]) -> str:
    rendered = "\n".join(str(message.get("content") or "") for message in messages)
    if "输入输出" in rendered and "今天有点难受" in rendered:
        reply = "记得，上次卡在输入输出那块。先别急，这轮就看那一处。"
    else:
        reply = "记得，先别急，我们只看最卡的那一处。"
    return json.dumps(
        {
            "reply_text": reply,
            "natsume_emotion_tag": "CALM",
            "user_emotion_tag": "SAD",
            "emotion_confidence": 0.86,
        },
        ensure_ascii=False,
    )


def _checks(
    *,
    memory_write: Mapping[str, Any],
    recall_packet: Mapping[str, Any],
    surface_result: Mapping[str, Any],
    rendered_prompt: str,
    bond_update: Mapping[str, Any],
    bond_state: Mapping[str, Any],
) -> dict[str, bool]:
    selected_text = str(surface_result.get("selected_text") or "")
    recall_facts = "\n".join(str(item) for item in list(recall_packet.get("facts") or []))
    route_ids = {str(item).lower() for item in list(recall_packet.get("route_ids") or [])}
    row_store_ids = {
        str(row.get("store_id") or "").lower()
        for row in list(recall_packet.get("rows") or [])
        if isinstance(row, Mapping)
    }
    route_and_store_ids = route_ids | row_store_ids
    compiled = dict(surface_result.get("compiled") or {})
    packet = dict(compiled.get("packet") or {})
    surface_material = json.dumps(
        {
            "facts": list(packet.get("facts") or []),
            "style_anchor": list(packet.get("style_anchor") or []),
            "must_include": list(packet.get("must_include") or []),
        },
        ensure_ascii=False,
    )
    return {
        "memory_write_success": bool(memory_write.get("success")),
        "recall_was_not_skipped": recall_packet.get("reason_code") != SURFACE_MEMORY_RECALL_SKIPPED,
        "recall_has_written_memory_fact": "输入输出" in recall_facts,
        "recall_has_memory_and_bond_routes": bool(
            ({"episodic", "episodic_vector", "route_episodic"} & route_and_store_ids)
            and ({"bond", "neo4j_bond", "route_neo4j_bond"} & route_and_store_ids)
        ),
        "prompt_has_compressed_memory_fact": "输入输出" in rendered_prompt,
        "prompt_has_no_source_ref_leak": not any(marker in surface_material for marker in SOURCE_REF_MARKERS)
        and not any(marker in selected_text for marker in SOURCE_REF_MARKERS),
        "prompt_has_no_legacy_identity_attribution": not any(
            marker in rendered_prompt or marker in selected_text
            for marker in ("新海天回应", "夏目蓝回应")
        ),
        "surface_reply_uses_memory": "输入输出" in selected_text,
        "surface_emotion_tags_present": bool(surface_result.get("natsume_emotion_tag")) and bool(surface_result.get("user_emotion_tag")),
        "surface_user_emotion_is_negative": str(surface_result.get("user_emotion_tag") or "").upper() in {"SAD", "ANXIOUS", "HURT"},
        "surface_vts_expression_present": bool(surface_result.get("vts_expression_id")),
        "surface_no_hard_gate_observations": not list(surface_result.get("observation_codes") or []),
        "bond_update_applied": str(bond_update.get("status") or "") == "APPLIED",
        "bond_update_uses_user_emotion": "surface_user_emotion_used" in list(bond_update.get("reason_codes") or []),
        "bond_update_kind_expected": str(bond_update.get("update_kind") or "") == "EMOTIONAL_SUPPORT",
        "bond_state_changed": int(bond_state.get("event_count") or 0) >= 1,
    }


def _markdown_report(summary: Mapping[str, Any], details: Mapping[str, Any]) -> str:
    checks = dict(summary.get("checks") or {})
    lines = [
        "# Surface Memory Emotion Loop Validation",
        "",
        f"- mode: `{summary.get('mode')}`",
        f"- evaluation_mode: `{summary.get('evaluation_mode')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- selected_text: {summary.get('selected_text')}",
        f"- emotion: `{summary.get('user_emotion_tag')}` -> `{summary.get('natsume_emotion_tag')}`",
        f"- vts_expression_id: `{summary.get('vts_expression_id')}`",
        f"- bond_update_kind: `{summary.get('bond_update_kind')}`",
        "",
        "## Checks",
        "",
    ]
    for key, value in checks.items():
        lines.append(f"- [{'x' if value else ' '}] `{key}`")
    lines.extend(
        [
            "",
            "## Memory Facts",
            "",
            *[f"- {item}" for item in list(dict(details.get("recall_packet") or {}).get("facts") or [])],
            "",
            "## Bond Summary",
            "",
            "```json",
            json.dumps(details.get("safe_bond_summary") or {}, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_mode(args: argparse.Namespace) -> str:
    return str(getattr(args, "mode", None) or "fixture").strip().lower() or "fixture"


def _resolve_out_dir(*, args: argparse.Namespace, mode: str) -> Path:
    raw = getattr(args, "out_dir", None)
    if raw:
        return Path(raw)
    suffix = "live_latest" if mode == "live" else "fixture_latest"
    return DEFAULT_OUT / suffix


def _write_failure_result(
    *,
    out_dir: Path,
    mode: str,
    reason_code: str,
    exc: BaseException,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    summary = {
        "mode": mode,
        "evaluation_mode": "contract_validation_no_text_quality_scoring",
        "passed": False,
        "reason_code": reason_code,
        "error_type": type(exc).__name__,
        "detail": str(exc),
        "output_dir": str(out_dir),
    }
    payload = {
        "failure": dict(summary),
        "details": dict(details or {}),
        "traceback": traceback.format_exc(),
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", payload)
    (out_dir / "report.md").write_text(_markdown_report(summary, payload), encoding="utf-8")
    return {"summary": summary, "details": payload}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate the surface memory -> emotion -> bond loop.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="fixture")
    parser.add_argument("--out-dir", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_surface_memory_emotion_loop(args))
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
