"""Durable Life, Security Immune and Skill/Domain-Pack query projections.

The loaders in this module are background-worker entry points.  HTTP handlers
must only call :func:`read_domain_projection_from_store` with their app-local
store; they must never call a loader, initialize a domain singleton, or inspect
a supply-chain directory.
"""

from __future__ import annotations

from host_brain.core.runtime_helpers import _copy_mapping as _mapping

from dataclasses import asdict
import time
from collections.abc import Mapping
from typing import Any, Callable

from host_brain.persona.life_evolution_view import build_life_evolution_view

from .projection_runtime import projection_meta
from host_brain.observability.projection_store import QueryProjectionStore


LIFE_EVOLUTION_KIND = "life_evolution"
LIFE_DEFAULT_KEY = "default"
LIFE_MAX_AGE_MS = 30_000
GOVERNANCE_SNAPSHOT_KIND = "governance_snapshot"
GOVERNANCE_SNAPSHOT_KEY = "current"
GOVERNANCE_SNAPSHOT_MAX_AGE_MS = 60_000
SEMANTIC_RECALL_STATS_KIND = "semantic_recall_stats"
SEMANTIC_RECALL_STATS_KEY = "runtime"
SEMANTIC_RECALL_STATS_MAX_AGE_MS = 120_000
SECURITY_IMMUNE_KIND = "security_immune"
SECURITY_IMMUNE_KEY = "runtime"
SECURITY_IMMUNE_MAX_AGE_MS = 10 * 60_000
SKILL_PACK_CATALOG_KIND = "skill_pack_catalog"
SKILL_PACK_CATALOG_KEY = "index"
SKILL_PACK_CATALOG_MAX_AGE_MS = 5 * 60_000

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
        # A missing canonical snapshot has source cursor 0.  Its projection
        # must therefore also be byte-deterministic at cursor 0; binding the
        # payload to wall clock time makes every daemon refresh conflict with
        # the first digest already recorded for that cursor.
        now_ms=updated_at_ms,
    )


def load_life_evolution_projection_source(
    person_id: str = LIFE_DEFAULT_KEY,
    *,
    snapshot_reader: Callable[[str], Mapping[str, Any]],
) -> tuple[dict[str, Any], int]:
    """Background source: canonical durable Life snapshot only."""

    snapshot = dict(snapshot_reader(str(person_id or LIFE_DEFAULT_KEY)) or {})
    cursor = max(int(snapshot.get("updated_at_ms") or 0), int(snapshot.get("version") or 0))
    return _canonical_life_view(snapshot), max(0, cursor)


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
    registry_provider: Callable[[], Any],
    domain_pack_reader: Callable[[], list[dict[str, Any]]],
) -> tuple[dict[str, Any], int]:
    """Project the canonical built-in Skill registry and Domain Pack catalog."""

    now = int(time.time() * 1000)
    active_skills = list(registry_provider().all())
    skills = [
        {
            "name": str(meta.skill_name),
            "version": "",
            "author": "",
            "clearance": str(meta.clearance or "LOW").upper(),
            "risk_level": int(meta.risk_level or 1),
            "source": "builtin",
            "status": "loaded",
        }
        for meta in active_skills
    ]

    domain_packs = list(domain_pack_reader() or [])
    payload = {
        "schema_version": "Natsume.operator.skill_pack_catalog.v1",
        "catalog_available": True,
        "total_skills": len(skills),
        "loaded_skills": len(skills),
        "failed_skills": 0,
        "skills": skills,
        "domain_pack_count": len(domain_packs),
        "domain_packs": domain_packs,
        "timestamp": now,
    }
    return payload, now


def refresh_life_evolution_projection(
    *,
    snapshot_reader: Callable[[str], Mapping[str, Any]],
    store: QueryProjectionStore,
) -> dict[str, int]:
    payload, cursor = load_life_evolution_projection_source(
        snapshot_reader=snapshot_reader,
    )
    store.upsert(
        LIFE_EVOLUTION_KIND,
        LIFE_DEFAULT_KEY,
        payload,
        source_cursor=cursor,
        source_ref="canonical-life-background-worker",
    )
    return {"document_count": 1}


def _unavailable_projection_meta(
    *,
    kind: str,
    key: str,
    exc: BaseException,
) -> dict[str, Any]:
    return {
        "schema_version": "Natsume.operator.query_projection.v1",
        "kind": kind,
        "key": key,
        "available": False,
        "projection_version": 0,
        "projection_cursor": 0,
        "source_cursor": 0,
        "freshness_ts_ms": 0,
        "age_ms": 0,
        "degraded_state": "SOURCE_UNAVAILABLE",
        "degraded_detail": type(exc).__name__,
        "reason_code": "OPERATOR_PROJECTION_SOURCE_UNAVAILABLE",
    }


def read_domain_projection_from_store(
    store: QueryProjectionStore,
    *,
    kind: str,
    key: str,
    max_age_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Read one document through an explicitly supplied application store."""

    try:
        document = store.get(kind, key)
    except Exception as exc:
        return None, _unavailable_projection_meta(kind=kind, key=key, exc=exc)
    meta = projection_meta(kind, key, document, max_age_ms=max_age_ms)
    meta.setdefault("projection_version", 0)
    meta.setdefault("projection_cursor", 0)
    meta.setdefault("source_cursor", 0)
    meta.setdefault("age_ms", 0)
    meta.setdefault("degraded_detail", None)
    if document is None or not meta["available"]:
        return None, meta
    return dict(document.payload), meta


__all__ = [
    "GOVERNANCE_SNAPSHOT_KEY", "GOVERNANCE_SNAPSHOT_KIND", "GOVERNANCE_SNAPSHOT_MAX_AGE_MS",
    "LIFE_DEFAULT_KEY", "LIFE_EVOLUTION_KIND", "LIFE_MAX_AGE_MS",
    "SEMANTIC_RECALL_STATS_KEY", "SEMANTIC_RECALL_STATS_KIND", "SEMANTIC_RECALL_STATS_MAX_AGE_MS",
    "SECURITY_IMMUNE_KEY", "SECURITY_IMMUNE_KIND", "SECURITY_IMMUNE_MAX_AGE_MS",
    "SKILL_PACK_CATALOG_KEY", "SKILL_PACK_CATALOG_KIND", "SKILL_PACK_CATALOG_MAX_AGE_MS",
    "load_life_evolution_projection_source",
    "load_skill_pack_catalog_projection_source",
    "read_domain_projection_from_store",
    "refresh_life_evolution_projection",
]
