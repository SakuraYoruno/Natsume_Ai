from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from host_brain.core.ids import now_ms as _now_ms
import traceback
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_OUT = ROOT / "artifacts" / "live_service_boundaries"
EXPECTED_DOCKER_CONTAINERS = ("natsume-chroma", "natsume-neo4j")
CRITICAL_COMPONENTS = ("chroma", "embedding", "neo4j", "llm", "vts_config")
HASH_EMBEDDING_MODEL_ID = "hash-embed-v1"


async def run_live_service_boundary_validation(args: argparse.Namespace) -> dict[str, Any]:
    mode = _resolve_mode(args)
    out_dir = _resolve_out_dir(args)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(getattr(args, "run_id", "") or f"service-boundary-{uuid.uuid4().hex[:10]}")
    started_ms = _now_ms()

    if mode == "fixture":
        components = _fixture_components(run_id=run_id)
    else:
        from tools.live_env import load_dotenv  # noqa: PLC0415

        load_dotenv(ROOT / ".env")
        components = await _live_components(args=args, run_id=run_id)

    checks = _build_checks(mode=mode, components=components, allow_hash_embedding=bool(getattr(args, "allow_hash_embedding", False)))
    summary = {
        "mode": mode,
        "evaluation_mode": "live_service_boundary_preflight",
        "passed": all(checks.values()),
        "run_id": run_id,
        "started_ms": started_ms,
        "duration_ms": _now_ms() - started_ms,
        "components": _compact_components(components),
        "checks": checks,
        "output_dir": str(out_dir),
    }
    details = {
        "summary": summary,
        "components": components,
    }
    _write_json(out_dir / "summary.json", summary)
    _write_json(out_dir / "details.json", details)
    (out_dir / "report.md").write_text(_report_markdown(summary, components), encoding="utf-8")
    return {"summary": summary, "details": details}


async def _live_components(*, args: argparse.Namespace, run_id: str) -> dict[str, Any]:
    timeout_sec = max(1.0, float(getattr(args, "timeout_sec", 5.0) or 5.0))
    llm_timeout_ms = max(800, int(getattr(args, "llm_timeout_ms", 30_000) or 30_000))
    chroma = _probe_chroma(run_id=run_id)
    vector_dim = int((chroma.get("details") or {}).get("vector_dim") or 0) or None
    return {
        "docker": _probe_docker(timeout_sec=timeout_sec),
        "chroma": chroma,
        "embedding": _probe_embedding(vector_dim=vector_dim),
        "neo4j": _probe_neo4j(run_id=run_id),
        "llm": await _probe_llm(run_id=run_id, llm_timeout_ms=llm_timeout_ms),
        "vts_config": _probe_vts_config(),
    }


def _fixture_components(*, run_id: str) -> dict[str, Any]:
    return {
        "docker": _component(
            "docker",
            ok=True,
            status="OK",
            runtime_class="mock",
            detail="fixture mode does not inspect Docker",
            details={"run_id": run_id},
        ),
        "chroma": _component(
            "chroma",
            ok=True,
            status="OK",
            runtime_class="mock",
            detail="fixture in-memory episodic store",
            details={"vector_dim": 8},
        ),
        "embedding": _component(
            "embedding",
            ok=True,
            status="OK",
            runtime_class="mock",
            detail="fixture static vector",
            details={"model": "fixture-vector"},
        ),
        "neo4j": _component(
            "neo4j",
            ok=True,
            status="OK",
            runtime_class="mock",
            detail="fixture in-memory bond graph",
            details={},
        ),
        "llm": _component(
            "llm",
            ok=True,
            status="OK",
            runtime_class="mock",
            detail="fixture deterministic generator",
            details={"provider": "fixture", "model": "fixture-surface"},
        ),
        "vts_config": _probe_vts_config(),
    }


def _probe_docker(*, timeout_sec: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except FileNotFoundError:
        return _component(
            "docker",
            ok=False,
            status="FAILED",
            runtime_class="live",
            detail="docker CLI not found",
            latency_ms=_elapsed_ms(started),
        )
    except subprocess.TimeoutExpired:
        return _component(
            "docker",
            ok=False,
            status="FAILED",
            runtime_class="live",
            detail=f"docker ps timed out after {timeout_sec:.1f}s",
            latency_ms=_elapsed_ms(started),
        )
    except Exception as exc:  # pragma: no cover - defensive live path
        return _component(
            "docker",
            ok=False,
            status="FAILED",
            runtime_class="live",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
        )

    rows = _parse_docker_ps(result.stdout)
    names = {str(row.get("name") or "") for row in rows}
    expected_present = [name for name in EXPECTED_DOCKER_CONTAINERS if name in names]
    ok = result.returncode == 0 and len(expected_present) == len(EXPECTED_DOCKER_CONTAINERS)
    detail = (
        "expected natsume Docker services are running"
        if ok
        else f"docker ps returned {result.returncode}; expected_present={expected_present}"
    )
    return _component(
        "docker",
        ok=ok,
        status="OK" if ok else "FAILED",
        runtime_class="live",
        detail=detail,
        latency_ms=_elapsed_ms(started),
        details={
            "containers": rows,
            "expected_containers": list(EXPECTED_DOCKER_CONTAINERS),
            "expected_present": expected_present,
            "stderr": result.stderr.strip()[:500],
        },
    )


def _parse_docker_ps(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw_line in str(text or "").splitlines():
        parts = raw_line.split("\t", 3)
        if not parts or not parts[0].strip():
            continue
        while len(parts) < 4:
            parts.append("")
        rows.append(
            {
                "name": parts[0].strip(),
                "image": parts[1].strip(),
                "status": parts[2].strip(),
                "ports": parts[3].strip(),
            }
        )
    return rows


def _probe_chroma(*, run_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from host_brain.memory.chroma_client import (  # noqa: PLC0415
            PersistentChromaClient,
            load_chroma_runtime_config,
            resolve_collection_vector_dim,
        )

        cfg_result = load_chroma_runtime_config()
        if not cfg_result.success or cfg_result.config is None:
            return _component(
                "chroma",
                ok=False,
                status="FAILED",
                runtime_class="disabled",
                detail=f"invalid chroma config: {cfg_result.reason_code}",
                latency_ms=_elapsed_ms(started),
                details={"config": cfg_result.as_dict()},
            )
        if not cfg_result.config.enabled:
            return _component(
                "chroma",
                ok=False,
                status="SKIPPED",
                runtime_class="disabled",
                detail="CHROMA_ENABLED=false",
                latency_ms=_elapsed_ms(started),
                details={"config": cfg_result.as_dict()},
            )
        client = PersistentChromaClient.from_runtime_config(cfg_result.config)
        vector_dim = resolve_collection_vector_dim(client)
        probe_id = f"codex_live_boundary_chroma_{uuid.uuid4().hex[:10]}"
        embedding = _deterministic_probe_embedding(vector_dim)
        client.upsert(
            memory_id=probe_id,
            text=f"live service boundary chroma probe {run_id}",
            embedding=embedding,
            metadata={"validation": "live_service_boundaries", "run_id": run_id},
        )
        fetched = client.get(probe_id)
        search_rows = client.search(query_embedding=embedding, top_k=1)
        client.delete(probe_id)
        ok = fetched is not None and any(str(row.get("memory_id") or "") == probe_id for row in search_rows)
        return _component(
            "chroma",
            ok=ok,
            status="OK" if ok else "FAILED",
            runtime_class="live",
            detail="chroma write/get/search/delete probe completed" if ok else "chroma probe did not retrieve written row",
            latency_ms=_elapsed_ms(started),
            details={
                "endpoint": cfg_result.config.endpoint,
                "tenant": cfg_result.config.tenant,
                "database": cfg_result.config.database,
                "default_collection": cfg_result.config.default_collection,
                "vector_dim": vector_dim,
                "probe_id": probe_id,
                "fetched": fetched is not None,
                "search_hit": any(str(row.get("memory_id") or "") == probe_id for row in search_rows),
                "config": cfg_result.config.as_dict(),
            },
        )
    except Exception as exc:
        return _component(
            "chroma",
            ok=False,
            status="FAILED",
            runtime_class="live",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
            details={"traceback": traceback.format_exc(limit=4)},
        )


def _probe_embedding(*, vector_dim: int | None) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from host_brain.memory.chroma_client import embed_text  # noqa: PLC0415

        embedding, model_id = embed_text(
            "live service boundary semantic embedding probe",
            dim=int(vector_dim) if vector_dim else None,
        )
        normalized_model = str(model_id or "").strip()
        is_hash = normalized_model == HASH_EMBEDDING_MODEL_ID
        return _component(
            "embedding",
            ok=bool(embedding),
            status="OK" if embedding else "FAILED",
            runtime_class="fallback" if is_hash else "live",
            detail=(
                "semantic embedding model loaded"
                if not is_hash
                else "embedding fell back to hash-embed-v1"
            ),
            latency_ms=_elapsed_ms(started),
            details={
                "model": normalized_model,
                "dim": len(list(embedding or [])),
                "requested_dim": vector_dim,
                "hash_fallback": is_hash,
            },
        )
    except Exception as exc:
        return _component(
            "embedding",
            ok=False,
            status="FAILED",
            runtime_class="live",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
            details={"traceback": traceback.format_exc(limit=4)},
        )


def _probe_neo4j(*, run_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    client: Any = None
    try:
        from host_brain.bond.neo4j_client import (  # noqa: PLC0415
            build_neo4j_bond_client,
            load_neo4j_runtime_config,
        )
        from host_brain.bond.bond_dynamics import BondDynamicsWriter  # noqa: PLC0415

        cfg_result = load_neo4j_runtime_config()
        if not cfg_result.success or cfg_result.config is None:
            return _component(
                "neo4j",
                ok=False,
                status="FAILED",
                runtime_class="disabled",
                detail=f"invalid neo4j config: {cfg_result.reason_code}",
                latency_ms=_elapsed_ms(started),
                details={"config": cfg_result.as_dict()},
            )
        if not cfg_result.config.enabled:
            return _component(
                "neo4j",
                ok=False,
                status="SKIPPED",
                runtime_class="disabled",
                detail="BOND_NEO4J_ENABLED=false",
                latency_ms=_elapsed_ms(started),
                details={"config": cfg_result.as_dict()},
            )
        client = build_neo4j_bond_client(config=cfg_result.config)
        if client is None:
            raise RuntimeError("neo4j client disabled")
        event_id = f"codex_live_boundary_neo4j_{uuid.uuid4().hex[:10]}"
        result = BondDynamicsWriter(neo4j_client=client).apply_update(
            request_id=f"live-boundary-neo4j-{run_id}",
            intent_id=f"live-boundary-intent-{run_id}",
            seq=1,
            event_id=event_id,
            user_id=f"live-boundary-user-{run_id}",
            session_id=f"live-boundary-session-{run_id}",
            topic_id="live_service_boundary",
            capability_hint="service boundary write/read probe",
            evidence_refs=[f"evd://live-service-boundary/{run_id}/neo4j"],
            payload={"validation": "live_service_boundaries", "run_id": run_id},
            update_kind="LIVE_SERVICE_BOUNDARY_PROBE",
            ts_ms=_now_ms(),
        )
        fetched = client.get_event(event_id)
        row = result.event.get("neo4j_event") if result.success else None
        ok = bool(row) and fetched is not None
        return _component(
            "neo4j",
            ok=ok,
            status="OK" if ok else "FAILED",
            runtime_class="live",
            detail="neo4j write/get probe completed" if ok else "neo4j get_event did not return written event",
            latency_ms=_elapsed_ms(started),
            details={
                "uri": cfg_result.config.uri,
                "database": cfg_result.config.database,
                "event_id": event_id,
                "seed_written": bool(row),
                "fetched": fetched is not None,
                "config": cfg_result.config.as_dict(),
            },
        )
    except Exception as exc:
        return _component(
            "neo4j",
            ok=False,
            status="FAILED",
            runtime_class="live",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
            details={"traceback": traceback.format_exc(limit=4)},
        )
    finally:
        if client is not None and hasattr(client, "close"):
            try:
                client.close()
            except Exception:
                pass


async def _probe_llm(*, run_id: str, llm_timeout_ms: int) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from host_brain.app.route_catalog_runtime import (  # noqa: PLC0415
            resolve_surface_reply_llm_runtime,
            resolve_host_llm_runtime,
        )
        from host_brain.model_runtime.production_route_policy import (  # noqa: PLC0415
            build_production_server_route_catalog,
        )
        from host_brain.model_runtime import (  # noqa: PLC0415
            CallableProviderAdapter,
            InMemoryAttemptAuditRepository,
            ModelAttemptRequest,
            PromptRef,
            ProviderTarget,
            RouteDecision,
            StaticRoutePolicy,
            SURFACE_MAIN_TEXT_PROMPT_ID,
            build_live_production_prompt_registry,
            build_model_runtime_composition,
        )
        from host_brain.integrations.llm_helpers import invoke_llm_text  # noqa: PLC0415

        catalog = build_production_server_route_catalog()
        base_runtime = resolve_host_llm_runtime(
            catalog=catalog,
            scene_type="CHAT",
        )
        runtime = resolve_surface_reply_llm_runtime(
            catalog=catalog,
            base_runtime=dict(base_runtime or {}),
        )
        provider = str(runtime.get("provider_name") or "").strip().lower()
        model_id = str(runtime.get("model_id") or "").strip()
        available = bool(runtime.get("available"))
        if not available:
            return _component(
                "llm",
                ok=False,
                status="FAILED",
                runtime_class="mock" if provider == "mock" else "disabled",
                detail=f"LLM runtime unavailable: {runtime.get('route_reason_code') or runtime.get('reason_code') or ''}",
                latency_ms=_elapsed_ms(started),
                details=_redact_runtime(runtime),
            )

        timeout_ms = min(
            max(800, int(runtime.get("timeout_ms") or llm_timeout_ms)),
            llm_timeout_ms,
        )
        api_key = str(runtime.get("llm_api_key") or "").strip()
        base_url_override = str(runtime.get("base_url_override") or "").strip() or None

        def _transport(attempt):  # noqa: ANN001
            return {
                "content": invoke_llm_text(
                    provider_name=attempt.target.provider,
                    model_id=attempt.target.model_id,
                    api_key=api_key,
                    timeout_ms=attempt.timeout_ms,
                    messages=attempt.prompt.as_message_dicts(),
                    gen_params=dict(attempt.target.metadata.get("generation_parameters") or {}),
                    base_url_override=base_url_override,
                    feature_key="live_service_boundary",
                )
            }

        target = ProviderTarget(
            adapter_id="live-service-boundary",
            provider=provider,
            model_id=model_id,
            timeout_ms=timeout_ms,
            metadata={
                "generation_parameters": {
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "max_tokens": 16,
                }
            },
        )
        route = RouteDecision(
            decision_id=f"live-service-boundary::{run_id}",
            targets=(target,),
            reason="LIVE_SERVICE_BOUNDARY_PREFLIGHT",
            max_attempts=1,
        )
        composition = build_model_runtime_composition(
            prompt_registry=build_live_production_prompt_registry(),
            route_policy=StaticRoutePolicy(route),
            provider_adapters=[
                CallableProviderAdapter(
                    "live-service-boundary",
                    complete_fn=_transport,
                    run_sync_in_thread=True,
                )
            ],
            audit_repository=InMemoryAttemptAuditRepository(),
        )
        result = await composition.attempt_service.complete(
            ModelAttemptRequest(
                request_id=f"req-{run_id}-llm-preflight",
                scene_type="CHAT",
                cognitive_tier="B",
                prompt=PromptRef(
                    SURFACE_MAIN_TEXT_PROMPT_ID,
                    variables={
                        "messages": (
                            {
                                "role": "system",
                                "content": "Reply with the exact token: live_service_boundary_ok",
                            },
                            {"role": "user", "content": "ping"},
                        )
                    },
                ),
                deadline_ms=int(time.time() * 1000) + timeout_ms,
                max_attempts=1,
                foreground=False,
                metadata={"caller": "live_service_boundary"},
            )
        )
        final_target = result.final_target
        final_provider = str(
            getattr(final_target, "provider", "") or provider
        ).strip().lower()
        final_model = str(getattr(final_target, "model_id", "") or model_id).strip()
        ok = bool(result.success and result.content.strip())
        return _component(
            "llm",
            ok=ok,
            status="OK" if ok else "FAILED",
            runtime_class="mock" if final_provider == "mock" or provider == "mock" else "live",
            detail="LLM generated a non-empty preflight response"
            if ok
            else "canonical model attempt failed or returned empty content",
            latency_ms=_elapsed_ms(started),
            details={
                "provider": provider,
                "model": model_id,
                "final_provider": final_provider,
                "final_model": final_model,
                "success": bool(result.success),
                "error_code": result.error_code,
                "attempts": [
                    {
                        "provider": str(item.provider or ""),
                        "model": str(item.model_id or ""),
                        "status": getattr(item.status, "value", item.status),
                        "error_code": item.error_code,
                        "latency_ms": item.latency_ms,
                    }
                    for item in list(result.attempts or ())
                ],
                "response_preview": result.content[:80],
                "runtime": _redact_runtime(runtime),
            },
        )
    except Exception as exc:
        return _component(
            "llm",
            ok=False,
            status="FAILED",
            runtime_class="live",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
            details={"traceback": traceback.format_exc(limit=4)},
        )
def _probe_vts_config() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from host_brain.channels.vts_runtime_mapping import (  # noqa: PLC0415
            get_default_vts_hotkey_config_path,
            load_runtime_emotion_mapping,
            runtime_emotion_expression_id,
        )

        path = get_default_vts_hotkey_config_path()
        mapping = load_runtime_emotion_mapping(path)
        required = ("NEUTRAL", "HAPPY", "SAD", "ANGRY", "CALM", "FRUSTRATED", "LOVING")
        missing = [tag for tag in required if tag not in mapping]
        sample = {
            tag: runtime_emotion_expression_id(tag, mapping=mapping)
            for tag in ("NEUTRAL", "HAPPY", "SAD", "ANGRY", "FRUSTRATED", "LOVING")
        }
        ok = not missing and all(str(value or "").strip() for value in sample.values())
        return _component(
            "vts_config",
            ok=ok,
            status="OK" if ok else "FAILED",
            runtime_class="live",
            detail="VTS emotion mapping config loaded" if ok else f"VTS mapping missing tags: {missing}",
            latency_ms=_elapsed_ms(started),
            details={
                "config_path": path,
                "tag_count": len(mapping),
                "required_tags": list(required),
                "missing_tags": missing,
                "sample_expression_ids": sample,
            },
        )
    except Exception as exc:
        return _component(
            "vts_config",
            ok=False,
            status="FAILED",
            runtime_class="fallback",
            detail=f"{type(exc).__name__}: {exc}",
            latency_ms=_elapsed_ms(started),
            details={"traceback": traceback.format_exc(limit=4)},
        )


def _build_checks(*, mode: str, components: Mapping[str, Any], allow_hash_embedding: bool) -> dict[str, bool]:
    compact = _compact_components(components)
    runtime_classes = {
        name: str(info.get("runtime_class") or "")
        for name, info in compact.items()
    }
    checks = {
        "service_boundary.required_components_reported": all(name in compact for name in ("docker", *CRITICAL_COMPONENTS)),
        "service_boundary.critical_components_ok": all(bool((components.get(name) or {}).get("ok")) for name in CRITICAL_COMPONENTS),
        "service_boundary.vts_mapping_loaded_from_config": bool((components.get("vts_config") or {}).get("ok"))
        and runtime_classes.get("vts_config") == "live",
    }
    if mode == "live":
        checks.update(
            {
                "service_boundary.docker_expected_services_running": bool((components.get("docker") or {}).get("ok")),
                "service_boundary.chroma_is_live": bool((components.get("chroma") or {}).get("ok"))
                and runtime_classes.get("chroma") == "live",
                "service_boundary.neo4j_is_live": bool((components.get("neo4j") or {}).get("ok"))
                and runtime_classes.get("neo4j") == "live",
                "service_boundary.llm_is_real_not_mock": bool((components.get("llm") or {}).get("ok"))
                and runtime_classes.get("llm") == "live",
                "service_boundary.embedding_is_semantic_not_hash": bool((components.get("embedding") or {}).get("ok"))
                and (
                    allow_hash_embedding
                    or runtime_classes.get("embedding") == "live"
                ),
            }
        )
    else:
        checks.update(
            {
                "service_boundary.fixture_marks_mock_components": all(
                    runtime_classes.get(name) == "mock"
                    for name in ("docker", "chroma", "embedding", "neo4j", "llm")
                ),
                "service_boundary.fixture_does_not_claim_live_services": not any(
                    runtime_classes.get(name) == "live"
                    for name in ("docker", "chroma", "embedding", "neo4j", "llm")
                ),
            }
        )
    return checks


def _component(
    name: str,
    *,
    ok: bool,
    status: str,
    runtime_class: str,
    detail: str,
    latency_ms: int | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "status": str(status or ("OK" if ok else "FAILED")).strip().upper(),
        "runtime_class": _normalize_runtime_class(runtime_class),
        "detail": str(detail or "").strip(),
        "latency_ms": latency_ms,
        "details": dict(details or {}),
    }


def _normalize_runtime_class(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"live", "fallback", "mock", "disabled"}:
        return normalized
    return "fallback"


def _compact_components(components: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for name, raw in dict(components or {}).items():
        item = dict(raw or {}) if isinstance(raw, Mapping) else {}
        compact[str(name)] = {
            "ok": bool(item.get("ok")),
            "status": str(item.get("status") or ""),
            "runtime_class": str(item.get("runtime_class") or ""),
            "detail": str(item.get("detail") or ""),
            "latency_ms": item.get("latency_ms"),
        }
    return compact


def _deterministic_probe_embedding(dim: int) -> list[float]:
    size = max(1, int(dim or 1))
    return [round(((idx % 17) + 1) / 100.0, 6) for idx in range(size)]


def _redact_runtime(runtime: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in dict(runtime or {}).items():
        if "key" in str(key).lower() or "secret" in str(key).lower():
            redacted[str(key)] = "<redacted>" if str(value or "").strip() else None
            continue
        if callable(value):
            redacted[str(key)] = f"<callable:{getattr(value, '__name__', type(value).__name__)}>"
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            redacted[str(key)] = value
        elif isinstance(value, Mapping):
            redacted[str(key)] = _redact_runtime(value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            redacted[str(key)] = [
                _redact_runtime(item) if isinstance(item, Mapping) else item
                for item in list(value)[:12]
            ]
        else:
            redacted[str(key)] = str(value)
    return redacted


def _report_markdown(summary: Mapping[str, Any], components: Mapping[str, Any]) -> str:
    lines = [
        "# Live Service Boundary Preflight",
        "",
        "This preflight verifies whether the host main chain is backed by real services or by mocks/fallbacks.",
        "",
        f"- mode: `{summary.get('mode')}`",
        f"- passed: `{summary.get('passed')}`",
        f"- duration_ms: `{summary.get('duration_ms')}`",
        f"- output_dir: `{summary.get('output_dir')}`",
        "",
        "## Components",
        "",
    ]
    for name, raw in dict(components or {}).items():
        item = dict(raw or {}) if isinstance(raw, Mapping) else {}
        lines.append(
            f"- [{'x' if item.get('ok') else ' '}] `{name}` "
            f"class=`{item.get('runtime_class')}` status=`{item.get('status')}` "
            f"latency_ms=`{item.get('latency_ms')}` detail={item.get('detail')}"
        )
    lines.extend(["", "## Checks", ""])
    for key, value in dict(summary.get("checks") or {}).items():
        lines.append(f"- [{'x' if value else ' '}] `{key}`")
    lines.extend(
        [
            "",
            "## Raw Details",
            "",
            "```json",
            json.dumps(components, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_out_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "out_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return DEFAULT_OUT / time.strftime("%Y%m%d_%H%M%S")


def _resolve_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "mode", "") or "live").strip().lower()
    if mode not in {"fixture", "live"}:
        raise ValueError(f"unsupported validation mode: {mode}")
    return mode


def _elapsed_ms(started: float) -> int:
    return int(round((time.perf_counter() - started) * 1000))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate live Docker/Chroma/Neo4j/LLM/VTS service boundaries.")
    parser.add_argument("--mode", choices=("fixture", "live"), default="live")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--llm-timeout-ms", type=int, default=30_000)
    parser.add_argument(
        "--allow-hash-embedding",
        action="store_true",
        help="Allow hash-embed fallback to pass the live embedding check.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(list(argv) if argv is not None else None)
    result = asyncio.run(run_live_service_boundary_validation(args))
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if result["summary"].get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
