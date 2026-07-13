"""Durable Life, Security Immune and Skill/Domain-Pack query projections.

The loaders in this module are background-worker entry points.  HTTP handlers
must only call :func:`read_domain_projection`; they must never call a loader,
initialize a domain singleton, or inspect a supply-chain directory.
"""

from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
import threading
import time
from collections.abc import Mapping
from typing import Any, Callable

from natsume_contracts.operator.v1 import ProjectionDegradedState

from host_brain.persona.life_evolution_view import build_life_evolution_view

from .projection_daemon import register_projection_job
from .projection_runtime import projection_meta
from host_brain.observability.projection_store import QueryProjectionStore, digest_json


LIFE_EVOLUTION_KIND = "life_evolution"
LIFE_DEFAULT_KEY = "default"
LIFE_MAX_AGE_MS = 30_000
SECURITY_IMMUNE_KIND = "security_immune"
SECURITY_IMMUNE_KEY = "runtime"
SECURITY_IMMUNE_MAX_AGE_MS = 10 * 60_000
SKILL_PACK_CATALOG_KIND = "skill_pack_catalog"
SKILL_PACK_CATALOG_KEY = "index"
SKILL_PACK_CATALOG_MAX_AGE_MS = 5 * 60_000

_job_lock = threading.Lock()
_last_job_run: dict[str, float] = {}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _canonical_life_view(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Project the canonical Life snapshot into the existing UI contract."""

    envelope = dict(snapshot)
    person_id = str(envelope.get("person_id") or "default").strip() or "default"
    version = max(0, int(envelope.get("version") or 0))
    updated_at_ms = max(0, int(envelope.get("updated_at_ms") or 0))
    state = _mapping(envelope.get("state"))
    source_ref = f"life://snapshot/{person_id}/{version}"
    fact_keys = ("clock", "runtime", "environment", "scene", "affect", "needs", "work")
    facts = [
        {
            "key": key,
            "value": dict(value),
            "epistemic_type": "observed",
            "source": "canonical_life_snapshot",
            "source_ref": f"{source_ref}/{key}",
            "confidence": 1.0,
            "observed_at_ms": updated_at_ms,
            "expires_at_ms": 0,
            "conflict_state": "NONE",
            "reason_codes": [],
        }
        for key in fact_keys
        if (value := state.get(key)) and isinstance(value, Mapping)
    ]
    world = {
        "schema_version": "Natsume.life.canonical_world_projection.v1",
        "status": "CURRENT" if version > 0 or updated_at_ms > 0 else "UNAVAILABLE",
        "generated_at_ms": updated_at_ms,
        "facts": facts,
        "source_summary": {
            "source": "canonical_life_snapshot",
            "snapshot_version": version,
            "state_digest": str(envelope.get("state_digest") or ""),
        },
    }

    appraisal = _mapping(state.get("appraisal"))
    appraisals: list[dict[str, Any]] = []
    if appraisal:
        appraisals.append(
            {
                "schema_version": "Natsume.life.canonical_appraisal.v1",
                "appraisal_id": f"canonical-appraisal-{person_id}-{version}",
                "source_event_kind": str(appraisal.get("event_type") or "life_snapshot"),
                "source_event_ref": source_ref,
                "source_event_digest": str(envelope.get("state_digest") or ""),
                "ts_ms": int(appraisal.get("appraised_at_ms") or updated_at_ms),
                "summary": appraisal,
                "bond_signal": {},
                "evidence_refs": [source_ref],
                "reason_tags": ["canonical_life_projection"],
            }
        )

    relationship = _mapping(state.get("relationship"))
    bond = {
        "schema_version": "Natsume.life.canonical_bond_projection.v1",
        "projection_id": f"canonical-bond-{person_id}-{version}",
        "status": "CURRENT" if relationship else "UNAVAILABLE",
        "projected_at_ms": updated_at_ms,
        "compatibility": {
            "current_stage": relationship.get("stage"),
            "total_score": relationship.get("warmth"),
            "trend": relationship.get("trend"),
            "event_count": relationship.get("event_count", 0),
            "last_event_ts_ms": relationship.get("last_event_ts_ms", updated_at_ms),
            "epistemic_type": "canonical_life_projection",
        },
        "relationship": {"vector": relationship},
        "evidence_refs": [source_ref],
        "reason_codes": [],
    }

    canonical_scene = _mapping(state.get("scene"))
    scene: dict[str, Any] = {}
    if canonical_scene:
        scene = {
            "schema_version": "Natsume.life.canonical_scene_projection.v1",
            "decision_id": f"canonical-scene-{person_id}-{version}",
            "scene_type": str(canonical_scene.get("kind") or "general"),
            "scene_tags": list(canonical_scene.get("tags") or []),
            "observed_at_ms": int(canonical_scene.get("since_ms") or updated_at_ms),
            "expires_at_ms": 0,
            "confidence": 1.0,
            "epistemic_type": "observed",
            "conflict_state": "NONE",
            "source_refs": [source_ref],
            "observation_source_ref": source_ref,
            "observed_from_actual_producer": True,
            "proactive_policy": "ALLOW",
            "expression_mode": str(canonical_scene.get("kind") or "natural"),
            "reason_codes": [],
            "user_goal_preserved": True,
        }

    return build_life_evolution_view(
        person_id=person_id,
        world_snapshot=world,
        appraisal_records=appraisals,
        bond_projection=bond,
        scene_decision=scene,
        action_arbiter_decision={},
        action_preview={},
        now_ms=updated_at_ms or int(time.time() * 1000),
    )


def load_life_evolution_projection_source(
    person_id: str = LIFE_DEFAULT_KEY,
    *,
    snapshot_reader: Callable[[str], Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], int]:
    """Background source: canonical durable Life snapshot only."""

    if snapshot_reader is None:
        from host_brain.digital_life.application_boundary import read_life_snapshot

        snapshot_reader = read_life_snapshot
    snapshot = dict(snapshot_reader(str(person_id or LIFE_DEFAULT_KEY)) or {})
    cursor = max(int(snapshot.get("updated_at_ms") or 0), int(snapshot.get("version") or 0))
    return _canonical_life_view(snapshot), max(0, cursor)


def load_security_immune_projection_source(
    *,
    snapshot_reader: Callable[[], Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], int]:
    """Background source: persisted/current Immune snapshot, never an HTTP path."""

    if snapshot_reader is None:
        from host_brain.security_immune.agent import get_security_immune_agent

        snapshot_reader = get_security_immune_agent().export_runtime_state
    payload = dict(snapshot_reader() or {})
    cursor = max(
        int(payload.get("generated_ts_ms") or 0),
        int(payload.get("last_run_ts_ms") or 0),
        max(
            (
                int(item.get("updated_ts_ms") or item.get("observed_ts_ms") or 0)
                for item in [*list(payload.get("assets") or []), *list(payload.get("incidents") or [])]
                if isinstance(item, Mapping)
            ),
            default=0,
        ),
    )
    return payload, max(0, cursor)


def _domain_pack_catalog() -> list[dict[str, Any]]:
    from host_brain.domain_packs.media_translation import build_media_translation_pack
    from host_brain.domain_packs.pentest import build_pentest_pack
    from host_brain.domain_packs.research_browser import build_research_browser_pack

    rows: list[dict[str, Any]] = []
    for pack in (build_research_browser_pack(), build_media_translation_pack(), build_pentest_pack()):
        manifest = pack.manifest
        rows.append(
            {
                "pack_id": manifest.pack_id,
                "version": manifest.version,
                "domain": manifest.domain,
                "enabled": bool(manifest.enabled_by_default),
                "schema_version": manifest.schema_version,
                "required_ports": list(manifest.required_ports),
                "capabilities": [asdict(capability) for capability in manifest.capabilities],
            }
        )
    return rows


def load_skill_pack_catalog_projection_source(
    *,
    registry_provider: Callable[[], Any] | None = None,
    installed_skills_reader: Callable[[], list[dict[str, Any]]] | None = None,
    custom_provider_reader: Callable[..., list[Any]] | None = None,
    domain_pack_reader: Callable[[], list[dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], int]:
    """Background source allowed to inspect registries and supply-chain state."""

    if registry_provider is None:
        from host_brain.composition.process_services import get_skill_registry

        registry_provider = get_skill_registry
    if installed_skills_reader is None:
        from host_brain.skills.hub.hub_installer import list_installed_skills

        installed_skills_reader = list_installed_skills
    if custom_provider_reader is None:
        from host_brain.skills.custom_provider_installer import list_custom_provider_infos

        custom_provider_reader = list_custom_provider_infos
    if domain_pack_reader is None:
        domain_pack_reader = _domain_pack_catalog

    now = int(time.time() * 1000)
    hub_enabled = str(os.environ.get("HOST_SKILL_HUB_ENABLED") or "true").lower() == "true"
    active_skills = list(registry_provider().all()) if hub_enabled else []
    active_names = {str(item.skill_name) for item in active_skills}
    installed = list(installed_skills_reader() or []) if hub_enabled else []
    installed_by_name = {
        str(row.get("name") or "").strip(): dict(row)
        for row in installed
        if isinstance(row, Mapping) and str(row.get("name") or "").strip()
    }
    custom_infos = list(custom_provider_reader(active_skill_names=active_names) or []) if hub_enabled else []
    custom_names = {
        str(name).strip()
        for info in custom_infos
        for name in tuple(getattr(info, "declared_skill_names", ()) or ())
        if str(name).strip()
    }
    failed_count = sum(1 for info in custom_infos if str(getattr(info, "status", "")).upper() == "ERROR")
    skills: list[dict[str, Any]] = []
    for meta in active_skills:
        row = installed_by_name.get(str(meta.skill_name))
        skills.append(
            {
                "name": str(meta.skill_name),
                "version": str(row.get("version") or "") if row else "",
                "author": str(row.get("author") or "") if row else "",
                "clearance": str(meta.clearance or "LOW").upper(),
                "risk_level": int(meta.risk_level or 1),
                "source": (
                    str(row.get("source") or "hub").strip().lower()
                    if row
                    else ("external" if str(meta.skill_name) in custom_names else "builtin")
                ),
                "status": "loaded",
            }
        )
    loaded_names = {row["name"] for row in skills}
    for row in installed:
        name = str(row.get("name") or "").strip() if isinstance(row, Mapping) else ""
        if not name or name in loaded_names:
            continue
        skills.append(
            {
                "name": name,
                "version": str(row.get("version") or ""),
                "author": str(row.get("author") or ""),
                "clearance": "LOW",
                "risk_level": 1,
                "source": str(row.get("source") or "hub").strip().lower() or "hub",
                "status": "installed",
            }
        )
    seen: set[str] = set()
    deduplicated_skills: list[dict[str, Any]] = []
    for row in skills:
        if row["name"] in seen:
            continue
        seen.add(row["name"])
        deduplicated_skills.append(row)
    skills = deduplicated_skills

    external_dirs = {
        str(Path(str(row.get("dir") or "")).parent)
        for row in installed
        if isinstance(row, Mapping) and str(row.get("dir") or "").strip()
    }
    if hub_enabled and str(os.environ.get("HOST_SKILL_SCAN_EXTERNAL") or "true").lower() == "true":
        home = Path.home()
        for path in (home / ".skillhub" / "skills", home / ".openclaw" / "skills", home / ".codebuddy" / "skills"):
            if path.is_dir():
                external_dirs.add(str(path))
    for raw_path in str(os.environ.get("HOST_SKILL_EXTERNAL_DIRS") or "").split(","):
        path = Path(raw_path.strip()).expanduser()
        if raw_path.strip() and path.is_dir():
            external_dirs.add(str(path))

    domain_packs = list(domain_pack_reader() or [])
    payload = {
        "schema_version": "Natsume.operator.skill_pack_catalog.v1",
        "hub_enabled": hub_enabled,
        "total_skills": len(skills) + failed_count,
        "loaded_skills": sum(1 for row in skills if row["status"] == "loaded"),
        "failed_skills": failed_count,
        "skills": skills,
        "external_dirs_scanned": sorted(path for path in external_dirs if path.strip()),
        "domain_pack_count": len(domain_packs),
        "domain_packs": domain_packs,
        "timestamp": now,
    }
    return payload, now


def _store() -> QueryProjectionStore:
    from host_brain.composition.process_services import get_query_projection_store

    return get_query_projection_store()


def _upsert(kind: str, key: str, payload: Mapping[str, Any], cursor: int, source_ref: str) -> None:
    _store().upsert(
        kind,
        key,
        payload,
        source_cursor=cursor,
        source_digest=digest_json(payload),
        source_ref=source_ref,
    )


def refresh_life_evolution_projection() -> dict[str, int]:
    payload, cursor = load_life_evolution_projection_source()
    _upsert(LIFE_EVOLUTION_KIND, LIFE_DEFAULT_KEY, payload, cursor, "canonical-life-background-worker")
    return {"document_count": 1}


def refresh_security_immune_projection() -> dict[str, int]:
    payload, cursor = load_security_immune_projection_source()
    _upsert(SECURITY_IMMUNE_KIND, SECURITY_IMMUNE_KEY, payload, cursor, "security-immune-background-worker")
    return {"document_count": 1}


def refresh_skill_pack_catalog_projection() -> dict[str, int]:
    payload, cursor = load_skill_pack_catalog_projection_source()
    _upsert(SKILL_PACK_CATALOG_KIND, SKILL_PACK_CATALOG_KEY, payload, cursor, "skill-pack-catalog-background-worker")
    return {"document_count": 1}


def read_domain_projection(
    *,
    kind: str,
    key: str,
    max_age_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        document = _store().get(kind, key)
    except Exception as exc:
        return None, {
            "schema_version": "Natsume.operator.query_projection.v1",
            "kind": kind,
            "key": key,
            "available": False,
            "projection_version": 0,
            "projection_cursor": 0,
            "source_cursor": 0,
            "freshness_ts_ms": 0,
            "degraded_state": "SOURCE_UNAVAILABLE",
            "degraded_detail": type(exc).__name__,
            "reason_code": "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE",
        }
    meta = projection_meta(kind, key, document, max_age_ms=max_age_ms)
    meta.setdefault("projection_version", 0)
    meta.setdefault("projection_cursor", 0)
    meta.setdefault("source_cursor", 0)
    meta.setdefault("age_ms", 0)
    meta.setdefault("degraded_detail", None)
    if document is None or not meta["available"]:
        return None, meta
    return dict(document.payload), meta


def _run_if_due(name: str, interval_seconds: float, callback: Callable[[], dict[str, int]]) -> dict[str, int | bool]:
    now = time.monotonic()
    with _job_lock:
        previous = _last_job_run.get(name, 0.0)
        if now - previous < max(0.1, interval_seconds):
            return {"skipped": True}
        _last_job_run[name] = now
    return callback()


def run_life_projection_job() -> dict[str, int | bool]:
    return _run_if_due("life-evolution", 5.0, refresh_life_evolution_projection)


def run_security_immune_projection_job() -> dict[str, int | bool]:
    return _run_if_due("security-immune", 30.0, refresh_security_immune_projection)


def run_skill_pack_catalog_projection_job() -> dict[str, int | bool]:
    return _run_if_due("skill-pack-catalog", 60.0, refresh_skill_pack_catalog_projection)


register_projection_job("life-evolution", run_life_projection_job)
register_projection_job("security-immune", run_security_immune_projection_job)
register_projection_job("skill-pack-catalog", run_skill_pack_catalog_projection_job)


__all__ = [
    "LIFE_DEFAULT_KEY", "LIFE_EVOLUTION_KIND", "LIFE_MAX_AGE_MS",
    "SECURITY_IMMUNE_KEY", "SECURITY_IMMUNE_KIND", "SECURITY_IMMUNE_MAX_AGE_MS",
    "SKILL_PACK_CATALOG_KEY", "SKILL_PACK_CATALOG_KIND", "SKILL_PACK_CATALOG_MAX_AGE_MS",
    "load_life_evolution_projection_source", "load_security_immune_projection_source",
    "load_skill_pack_catalog_projection_source", "read_domain_projection",
    "refresh_life_evolution_projection", "refresh_security_immune_projection",
    "refresh_skill_pack_catalog_projection",
]
