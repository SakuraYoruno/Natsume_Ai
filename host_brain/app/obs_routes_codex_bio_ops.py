"""Codex Runtime v2 and BioOps observability endpoint."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fastapi import Query

from host_brain.app.obs_routes_core import router
from host_brain.bio_ops.capabilities import (
    ADAPTIVE_REPAIR_CAPABILITIES,
    AFFECT_LEARNING_CAPABILITIES,
    DRIVE_TRUST_CAPABILITIES,
    GROWTH_CLOSURE_CAPABILITIES,
    INNER_LIFE_CAPABILITIES,
    INNER_MECHANISM_CAPABILITIES,
    MEMORY_NARRATIVE_CAPABILITIES,
    NATURAL_INTERACTION_CAPABILITIES,
    PRESENCE_CHANNEL_CAPABILITIES,
    RELATIONSHIP_LAYER_CAPABILITIES,
    SELF_GOVERNANCE_CAPABILITIES,
    TRUST_FOUNDATION_CAPABILITIES,
    list_capability_specs,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
OBSERVABLE_EFFECTS_ROOT = REPO_ROOT / "artifacts" / "observable_effects"
MAX_JSON_BYTES = 5 * 1024 * 1024

RESULT_CONTRACT_FIELDS = (
    "status",
    "work_order_id",
    "summary",
    "result_text",
    "changed_files",
    "artifacts_dir",
    "thread_id",
    "verification",
    "risk_flags",
    "review_required",
    "next_actions",
    "rollback_manifest_ref",
)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text_list(value: Any, *, limit: int | None = None) -> list[str]:
    items = [str(item).strip() for item in _as_list(value) if str(item).strip()]
    return items[:limit] if limit is not None else items


def _read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            return {"_error": "json_file_too_large", "path": str(path)}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_error": "json_read_failed", "path": str(path), "detail": str(exc)}
    return _as_dict(payload)


def _find_run_dir(run_id: str | None) -> Path | None:
    if not OBSERVABLE_EFFECTS_ROOT.exists():
        return None
    if run_id:
        safe_run_id = str(run_id).strip()
        if not safe_run_id or any(token in safe_run_id for token in ("..", "/", "\\")):
            return None
        candidate = OBSERVABLE_EFFECTS_ROOT / safe_run_id
        return candidate if candidate.is_dir() else None
    runs = [path for path in OBSERVABLE_EFFECTS_ROOT.iterdir() if path.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda path: path.stat().st_mtime)


def _status_counts(*payloads: dict[str, Any]) -> dict[str, int]:
    for payload in payloads:
        counts = _as_dict(payload.get("bio_ops_status_counts") or payload.get("status_counts"))
        if counts:
            return {str(key): int(value or 0) for key, value in counts.items()}
    return {}


def _first_mapping(items: Any) -> dict[str, Any]:
    for item in _as_list(items):
        if isinstance(item, dict):
            return item
    return {}


def _compact_codex_result(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    verification = _as_dict(payload.get("verification"))
    metadata = _as_dict(payload.get("metadata"))
    present_fields = [field for field in RESULT_CONTRACT_FIELDS if field in payload]
    result_text = str(payload.get("result_text") or "")
    return {
        "status": payload.get("status"),
        "work_order_id": payload.get("work_order_id"),
        "summary": payload.get("summary"),
        "result_excerpt": result_text[:480],
        "changed_files_count": len(_as_list(payload.get("changed_files"))),
        "changed_files": _text_list(payload.get("changed_files"), limit=8),
        "artifacts_dir": payload.get("artifacts_dir"),
        "thread_id": payload.get("thread_id"),
        "verification_status": verification.get("status"),
        "risk_flags": _text_list(payload.get("risk_flags"), limit=8),
        "review_required": bool(payload.get("review_required")),
        "reason_code": payload.get("reason_code"),
        "detail": payload.get("detail"),
        "elapsed_seconds": payload.get("elapsed_seconds"),
        "contract_fields_present": present_fields,
        "contract_fields_missing": [field for field in RESULT_CONTRACT_FIELDS if field not in payload],
        "codex_model": metadata.get("codex_model"),
        "prompt_profile": metadata.get("prompt_profile"),
        "sandbox_mode": metadata.get("sandbox_mode"),
        "returncode": metadata.get("returncode"),
    }


def _compact_focus_effect(effect_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    proposal = _first_mapping(payload.get("proposals"))
    finding = _first_mapping(payload.get("findings"))
    metadata = _as_dict(payload.get("metadata"))
    adoption = _as_dict(metadata.get("bio_ops_adoption"))
    adoption_policy = _as_dict(adoption.get("policy"))
    adoption_candidate = _as_dict(adoption.get("candidate"))
    return {
        "id": effect_id,
        "status": payload.get("status"),
        "capability": payload.get("capability"),
        "summary": payload.get("summary"),
        "issue": finding.get("issue") or payload.get("reason_code"),
        "owner_organ": proposal.get("owner_organ") or adoption_policy.get("owner_organ") or adoption_candidate.get("owner_organ"),
        "output_kind": proposal.get("kind") or adoption_candidate.get("output_kind"),
        "write_policy": proposal.get("write_policy"),
        "adoption_status": adoption.get("status"),
        "candidate_id": adoption_candidate.get("candidate_id"),
        "evidence_refs": _text_list(payload.get("evidence_refs"), limit=5),
        "artifact_count": len(_as_list(payload.get("artifacts"))),
        "risk_flags": _text_list(payload.get("risk_flags"), limit=8),
        "review_required": bool(payload.get("review_required")),
        "reason_code": payload.get("reason_code"),
        "detail": payload.get("detail"),
        "next_actions": _text_list(payload.get("next_actions"), limit=4),
    }


def _compact_runtime_trace(trace_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    reflex = _as_dict(payload.get("reflex"))
    attention = _as_dict(payload.get("attention_budget"))
    weather = _as_dict(payload.get("internal_weather"))
    silence = _as_dict(payload.get("active_silence"))
    breathing = _as_dict(payload.get("response_breathing"))
    return {
        "id": trace_id,
        "proposal_only": bool(payload.get("proposal_only", True)),
        "reflex_matched": bool(reflex.get("matched")),
        "reflex_type": reflex.get("reflex_type") or reflex.get("reason_code"),
        "reflex_action": reflex.get("action"),
        "attention_pattern": attention.get("pattern"),
        "overprocessed": bool(attention.get("overprocessed")),
        "forbidden_actions": _text_list(payload.get("forbidden_actions") or attention.get("forbidden_actions"), limit=8),
        "internal_weather": weather.get("internal_weather"),
        "weather_ttl_minutes": weather.get("ttl_minutes"),
        "silence_policy": silence.get("silence_policy"),
        "silence_reason": silence.get("reason_code"),
        "reply_density": breathing.get("reply_density"),
        "breathing_reason": breathing.get("reason_code"),
        "max_sentences": breathing.get("max_sentences"),
    }


def _catalog_from_specs(daily_full: dict[str, Any]) -> dict[str, Any]:
    result_by_capability = {
        str(result.get("capability") or ""): result
        for result in _as_list(daily_full.get("results"))
        if isinstance(result, dict)
    }
    groups: dict[str, dict[str, Any]] = {}
    capabilities: list[dict[str, Any]] = []
    for spec in list_capability_specs():
        result = _as_dict(result_by_capability.get(spec.capability))
        group = groups.setdefault(
            spec.group,
            {
                "group": spec.group,
                "capability_count": 0,
                "status_counts": {},
                "owner_organs": set(),
            },
        )
        status = str(result.get("status") or "NOT_RUN")
        group["capability_count"] += 1
        group["status_counts"][status] = int(group["status_counts"].get(status, 0)) + 1
        group["owner_organs"].add(spec.owner_organ)
        capabilities.append(
            {
                "capability": spec.capability,
                "group": spec.group,
                "owner_organ": spec.owner_organ,
                "output_kind": spec.output_kind,
                "status": status,
                "review_required": bool(result.get("review_required")),
                "risk_flags": _text_list(result.get("risk_flags"), limit=5),
                "summary": result.get("summary"),
            }
        )
    normalized_groups = []
    for group in groups.values():
        normalized = dict(group)
        normalized["owner_organs"] = sorted(group["owner_organs"])
        normalized_groups.append(normalized)
    return {
        "groups": sorted(normalized_groups, key=lambda item: item["group"]),
        "capabilities": capabilities,
    }


def _capability_set_summary(
    capabilities: list[dict[str, Any]],
    capability_ids: tuple[str, ...],
    *,
    activation_rule: str,
) -> dict[str, Any]:
    capability_by_id = {str(item.get("capability") or ""): item for item in capabilities}
    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for capability in capability_ids:
        item = dict(capability_by_id.get(capability) or {"capability": capability, "status": "NOT_REGISTERED"})
        status = str(item.get("status") or "UNKNOWN")
        status_counts[status] = int(status_counts.get(status, 0)) + 1
        rows.append(
            {
                "capability": capability,
                "group": item.get("group"),
                "owner_organ": item.get("owner_organ"),
                "output_kind": item.get("output_kind"),
                "status": status,
                "review_required": bool(item.get("review_required")),
                "risk_flags": _text_list(item.get("risk_flags"), limit=5),
                "summary": item.get("summary"),
            }
        )
    return {
        "capability_count": len(rows),
        "status_counts": status_counts,
        "capabilities": rows,
        "activation_rule": activation_rule,
        "direct_write_allowed": False,
    }


def _inner_mechanism_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        INNER_MECHANISM_CAPABILITIES,
        activation_rule="proposal_or_candidate_only",
    )


def _trust_foundation_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        TRUST_FOUNDATION_CAPABILITIES,
        activation_rule="consent_and_truthfulness_gate",
    )


def _natural_interaction_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        NATURAL_INTERACTION_CAPABILITIES,
        activation_rule="low_risk_trace_policy",
    )


def _relationship_layer_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        RELATIONSHIP_LAYER_CAPABILITIES,
        activation_rule="report_only_relationship_repair",
    )


def _inner_life_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        INNER_LIFE_CAPABILITIES,
        activation_rule="report_only_inner_life",
    )


def _self_governance_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        SELF_GOVERNANCE_CAPABILITIES,
        activation_rule="report_only_self_governance",
    )


def _growth_closure_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        GROWTH_CLOSURE_CAPABILITIES,
        activation_rule="report_only_growth_closure",
    )


def _memory_narrative_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        MEMORY_NARRATIVE_CAPABILITIES,
        activation_rule="report_only_memory_narrative",
    )


def _affect_learning_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        AFFECT_LEARNING_CAPABILITIES,
        activation_rule="report_only_affect_learning",
    )


def _presence_channel_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        PRESENCE_CHANNEL_CAPABILITIES,
        activation_rule="report_only_presence_channel",
    )


def _drive_trust_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        DRIVE_TRUST_CAPABILITIES,
        activation_rule="report_only_drive_trust",
    )


def _adaptive_repair_summary(capabilities: list[dict[str, Any]]) -> dict[str, Any]:
    return _capability_set_summary(
        capabilities,
        ADAPTIVE_REPAIR_CAPABILITIES,
        activation_rule="report_or_owner_candidate_adaptive_repair",
    )


def _pending_adoptions(*payloads: dict[str, Any]) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for payload in payloads:
        if not payload:
            continue
        for result in _as_list(payload.get("results")):
            if isinstance(result, dict):
                pending.extend(_pending_adoptions_from_result(result))
        for result in _as_list(payload.get("sample_capabilities")):
            if isinstance(result, dict):
                pending.extend(_pending_adoptions_from_result(result))
    return pending


def _pending_adoptions_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    adoption = _as_dict(result.get("adoption"))
    if not adoption:
        adoption = _as_dict(_as_dict(result.get("metadata")).get("bio_ops_adoption"))
    candidate = _as_dict(adoption.get("candidate"))
    if not candidate or str(adoption.get("status") or candidate.get("status") or "") != "PENDING_REVIEW":
        return []
    return [
        {
            "candidate_id": candidate.get("candidate_id"),
            "capability": candidate.get("capability") or result.get("capability"),
            "owner_organ": candidate.get("owner_organ"),
            "output_kind": candidate.get("output_kind"),
            "summary": candidate.get("summary") or result.get("summary"),
            "evidence_refs": _text_list(candidate.get("evidence_refs"), limit=5),
            "next_actions": _text_list(candidate.get("next_actions"), limit=4),
            "review_required": bool(candidate.get("review_required", True)),
        }
    ]


def _artifact_refs(run_dir: Path, summary: dict[str, Any]) -> list[dict[str, str]]:
    paths = _as_dict(summary.get("paths"))
    refs: list[dict[str, str]] = []
    preferred = {
        "summary": "观测总览",
        "daily_summary": "BioOps 日循环摘要",
        "daily_full": "BioOps 日循环全量",
        "focused_effects": "关键效果样本",
        "runtime_traces": "运行时 trace",
        "codex_blocked_result": "Codex 写入阻断",
        "codex_inspect_fixed_result": "Codex inspect 执行",
        "hard_cut_scan": "硬切验收扫描",
        "pytest_result": "验证测试结果",
    }
    for key, label in preferred.items():
        raw = paths.get(key)
        path = Path(str(raw)) if raw else run_dir / f"{key}.json"
        if path.exists():
            refs.append({"id": key, "label": label, "path": str(path)})
    return refs


def _build_benefits(
    *,
    summary: dict[str, Any],
    status_counts: dict[str, int],
    codex_inspect: dict[str, Any],
    codex_blocked: dict[str, Any],
    focused_effects: list[dict[str, Any]],
    traces: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    short_ack_trace = next((item for item in traces if item["id"] == "short_ack_chat"), {})
    complex_trace = next((item for item in traces if item["id"] == "complex_code_request"), {})
    sensitive_block = next((item for item in focused_effects if item["id"] == "sensitive_write_blocked"), {})
    candidate_count = int(status_counts.get("CANDIDATE") or 0)
    return [
        {
            "id": "codex_runtime_control",
            "title": "Codex 执行链可控",
            "impact": "代码相关任务现在必须经过 WorkOrder 和 ResultContract，执行、阻断、证据、风险都能复盘。",
            "status": codex_inspect.get("status") or summary.get("codex_inspect_fixed_status") or "UNKNOWN",
            "evidence": [
                f"inspect={codex_inspect.get('status') or summary.get('codex_inspect_fixed_status') or 'UNKNOWN'}",
                f"blocked_write={codex_blocked.get('status') or summary.get('codex_blocked_status') or 'UNKNOWN'}",
                f"contract_fields={len(codex_inspect.get('contract_fields_present') or [])}/{len(RESULT_CONTRACT_FIELDS)}",
            ],
        },
        {
            "id": "bio_ops_owner_gate",
            "title": "高级数字生命能力不直写核心状态",
            "impact": "BioOps 产出 proposal、audit report、candidate；长期状态变更需要 owner organ 明确接管。",
            "status": "OWNER_GATED" if candidate_count else "REPORT_ONLY",
            "evidence": [
                f"capabilities={sum(status_counts.values()) or summary.get('bio_ops_capability_count') or 0}",
                f"candidate={candidate_count}",
                f"sensitive_write={sensitive_block.get('status') or 'UNKNOWN'}",
            ],
        },
        {
            "id": "runtime_behavior_trace",
            "title": "Chat/Planner 低风险行为可观测",
            "impact": "短确认、疲惫安慰、重复询问、渠道故障可走反射；复杂请求保留规划链，避免被轻量规则误截断。",
            "status": "TRACED" if traces else "NO_TRACE",
            "evidence": [
                f"short_ack_reflex={short_ack_trace.get('reflex_matched')}",
                f"complex_reflex={complex_trace.get('reflex_matched')}",
                f"forbidden_actions={len(short_ack_trace.get('forbidden_actions') or [])}",
            ],
        },
        {
            "id": "verification_surface",
            "title": "验收结果可以在项目内查看",
            "impact": "硬切扫描、BioOps 测试和 artifacts 都进入同一个 WebUI 面板，不再只靠一次性静态报告。",
            "status": "PASS" if summary.get("hard_cut_scan_exit_code") == 1 and summary.get("pytest_exit_code") == 0 else "CHECK",
            "evidence": [
                f"hard_cut_scan_exit={summary.get('hard_cut_scan_exit_code')}",
                f"pytest_exit={summary.get('pytest_exit_code')}",
                f"immune_proposal={bool(summary.get('immune_proposal_triggered'))}",
            ],
        },
    ]


@router.get("/codex-bio-ops")
def get_codex_bio_ops_observability(run_id: str | None = Query(default=None)) -> dict[str, Any]:
    run_dir = _find_run_dir(run_id)
    if run_dir is None:
        catalog = _catalog_from_specs({})
        return {
            "ok": False,
            "artifact_available": False,
            "generated_at_ms": _now_ms(),
            "run_id": None,
            "artifact_dir": None,
            "benefits": [],
            "codex_runtime": {},
            "bio_ops": {
                "capability_count": len(catalog["capabilities"]),
                "status_counts": {},
                **catalog,
                "inner_mechanisms": _inner_mechanism_summary(catalog["capabilities"]),
                "trust_foundation": _trust_foundation_summary(catalog["capabilities"]),
                "natural_interaction": _natural_interaction_summary(catalog["capabilities"]),
                "relationship_layer": _relationship_layer_summary(catalog["capabilities"]),
                "inner_life": _inner_life_summary(catalog["capabilities"]),
                "self_governance": _self_governance_summary(catalog["capabilities"]),
                "growth_closure": _growth_closure_summary(catalog["capabilities"]),
                "memory_narrative": _memory_narrative_summary(catalog["capabilities"]),
                "affect_learning": _affect_learning_summary(catalog["capabilities"]),
                "presence_channel": _presence_channel_summary(catalog["capabilities"]),
                "drive_trust": _drive_trust_summary(catalog["capabilities"]),
                "adaptive_repair": _adaptive_repair_summary(catalog["capabilities"]),
                "focused_effects": [],
                "pending_adoptions": [],
            },
            "runtime_traces": [],
            "artifacts": [],
            "message": "No observable effects artifact run is available yet.",
        }

    summary = _read_json_file(run_dir / "summary.json")
    daily_summary = _read_json_file(run_dir / "bio_ops_daily_summary.json")
    daily_full = _read_json_file(run_dir / "bio_ops_daily_full.json")
    focused = _read_json_file(run_dir / "focused_effects.json")
    runtime_traces = _read_json_file(run_dir / "runtime_traces.json")
    codex_blocked_raw = _read_json_file(run_dir / "codex_blocked_write_result.json")
    codex_inspect_raw = _read_json_file(run_dir / "codex_inspect_fixed_result.json") or _read_json_file(run_dir / "codex_inspect_result.json")
    hard_cut_raw = _read_json_file(run_dir / "hard_cut_scan.json")
    pytest_raw = _read_json_file(run_dir / "pytest_result.json")

    status_counts = _status_counts(summary, daily_summary)
    catalog = _catalog_from_specs(daily_full)
    focused_effects = [
        _compact_focus_effect(effect_id, payload)
        for effect_id, payload in focused.items()
        if isinstance(payload, dict)
    ]
    traces = [
        _compact_runtime_trace(trace_id, payload)
        for trace_id, payload in runtime_traces.items()
        if isinstance(payload, dict)
    ]
    codex_inspect = _compact_codex_result(codex_inspect_raw)
    codex_blocked = _compact_codex_result(codex_blocked_raw)

    return {
        "ok": True,
        "artifact_available": True,
        "generated_at_ms": _now_ms(),
        "run_id": run_dir.name,
        "artifact_dir": str(run_dir),
        "benefits": _build_benefits(
            summary=summary,
            status_counts=status_counts,
            codex_inspect=codex_inspect,
            codex_blocked=codex_blocked,
            focused_effects=focused_effects,
            traces=traces,
        ),
        "codex_runtime": {
            "inspect": codex_inspect,
            "blocked_write": codex_blocked,
            "work_order_preview_ok": bool(summary.get("work_order_preview_ok")),
            "immune_proposal_triggered": bool(summary.get("immune_proposal_triggered")),
            "result_contract_fields": list(RESULT_CONTRACT_FIELDS),
            "hard_cut_scan": {
                "passed": hard_cut_raw.get("exit_code") == 1 or summary.get("hard_cut_scan_exit_code") == 1,
                "exit_code": hard_cut_raw.get("exit_code", summary.get("hard_cut_scan_exit_code")),
                "meaning": hard_cut_raw.get("meaning"),
            },
            "pytest": {
                "passed": pytest_raw.get("exit_code") == 0 or summary.get("pytest_exit_code") == 0,
                "exit_code": pytest_raw.get("exit_code", summary.get("pytest_exit_code")),
            },
        },
        "bio_ops": {
            "capability_count": int(summary.get("bio_ops_capability_count") or daily_summary.get("capability_count") or len(catalog["capabilities"])),
            "status_counts": status_counts,
            **catalog,
            "inner_mechanisms": _inner_mechanism_summary(catalog["capabilities"]),
            "trust_foundation": _trust_foundation_summary(catalog["capabilities"]),
            "natural_interaction": _natural_interaction_summary(catalog["capabilities"]),
            "relationship_layer": _relationship_layer_summary(catalog["capabilities"]),
            "inner_life": _inner_life_summary(catalog["capabilities"]),
            "self_governance": _self_governance_summary(catalog["capabilities"]),
            "growth_closure": _growth_closure_summary(catalog["capabilities"]),
            "memory_narrative": _memory_narrative_summary(catalog["capabilities"]),
            "affect_learning": _affect_learning_summary(catalog["capabilities"]),
            "presence_channel": _presence_channel_summary(catalog["capabilities"]),
            "drive_trust": _drive_trust_summary(catalog["capabilities"]),
            "adaptive_repair": _adaptive_repair_summary(catalog["capabilities"]),
            "focused_effects": focused_effects,
            "pending_adoptions": _pending_adoptions(daily_full, daily_summary),
        },
        "runtime_traces": traces,
        "artifacts": _artifact_refs(run_dir, summary),
    }
