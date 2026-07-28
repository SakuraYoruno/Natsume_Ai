from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import shutil

import pytest

from host_brain.contracts.agent_runtime.contract_errors import ContractViolation
from host_brain.app.startup_snapshot import build_startup_snapshot
from host_brain.persona.drift_detector import StyleFingerprint
from host_brain.release.identity_snapshot import (
    IDENTITY_PREVIOUS_RELEASE_MISMATCH,
    IDENTITY_PRIVATE_BODY_REJECTED,
    IDENTITY_RELEASE_MANIFEST_MISMATCH,
    IDENTITY_SNAPSHOT_FEATURE_FLAG,
    IDENTITY_STARTUP_SNAPSHOT_INVALID,
    IdentityDiffSeverity,
    IdentitySnapshot,
    IdentitySourceRef,
    build_identity_rollback_plan,
    build_identity_snapshot,
    diff_identity_snapshots,
    evaluate_identity_release_gate,
    identity_snapshot_gate_enabled,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_SENTINEL = "PRIVATE_BODY_MUST_NEVER_ENTER_RELEASE_IDENTITY_SNAPSHOT"


def _source(name: str, payload: object) -> IdentitySourceRef:
    return IdentitySourceRef.from_payload(ref=f"state://identity/{name}", payload=payload)


def _identity_root(tmp_path: Path) -> Path:
    for relative in (
        "Soul.md",
        "host_brain/canon/character/character_profile.json",
        "host_brain/canon/world/wc-001-system-terms.json",
        "host_brain/canon/world/wc-002-architecture-invariants.json",
        "host_brain/canon/world/wc-003-persona-core.json",
        "host_brain/canon/world/wc-004-persona-growth-rules.json",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    return tmp_path


def _self_model(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "recent_mood_trend": "平稳",
        "emotional_stability": 0.78,
        "dominant_recent_emotion": "CURIOUS",
        "bond_growth_perception": "稳定",
        "shared_memory_count": 12,
        "last_deep_talk_days_ago": 2,
        "personality_changes": [PRIVATE_SENTINEL],
        "growth_milestones": [PRIVATE_SENTINEL],
        "self_identity": PRIVATE_SENTINEL,
        "self_values": [PRIVATE_SENTINEL],
        "self_boundaries": [PRIVATE_SENTINEL],
        "active_intentions": [PRIVATE_SENTINEL],
        "unresolved_tensions": [PRIVATE_SENTINEL],
        "agency_score": 0.72,
        "self_continuity_score": 0.86,
        "self_update_count": 8,
        "event_count": 42,
        "self_directive": {"body": PRIVATE_SENTINEL},
    }
    value.update(overrides)
    return value


def _bond(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "Natsume.bond.projection.v1",
        "projection_id": "bond_projection_safe",
        "person_id": f"user-{PRIVATE_SENTINEL}",
        "status": "AVAILABLE",
        "state_available": True,
        "compatibility": {
            "current_stage": "CLOSE_FRIEND",
            "total_score": 81,
            "event_count": 20,
            "trend": "UP",
            "capability_hint": PRIVATE_SENTINEL,
            "summary": {"private": PRIVATE_SENTINEL},
        },
        "relationship": {
            "vector": {
                "trust": 0.8,
                "affinity": 0.81,
                "safety": 0.82,
                "novelty": 0.3,
                "dependency": 0.4,
                "repair_confidence": 0.7,
                "rupture_load": 0.1,
                "repair_progress": 0.4,
                "bond_stage": "close_friend",
            },
            "rift": {
                "active": True,
                "rift_level": 0.2,
                "repair_progress": 0.4,
                "rift_cause": PRIVATE_SENTINEL,
                "repair_hint": PRIVATE_SENTINEL,
            },
            "repair": {
                "social_repair_progress": 0.5,
                "rupture_load": 0.1,
                "repair_hint": PRIVATE_SENTINEL,
            },
            "texture": {"style_hint": PRIVATE_SENTINEL},
            "boundary": {
                "status": "NONE",
                "explicit_user_boundary": False,
                "strength": 0.0,
                "expression_constraint": "NONE",
                "reason": PRIVATE_SENTINEL,
            },
        },
        "evidence_refs": ["bond-state://safe/ref"],
    }
    value.update(overrides)
    return value


def _boundaries(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "proactive_enabled": True,
        "requires_explicit_consent": True,
        "quiet_hours_enforced": True,
        "max_daily_initiations": 3,
        "minimum_interval_minutes": 120,
        "allowed_channels": ["LOCAL_UI", "DESKTOP_NOTIFICATION"],
    }
    value.update(overrides)
    return value


def _build(
    root: Path,
    *,
    release: str = "release://natsume/v1",
    previous: str | None = None,
    created_at_ms: int = 1_800_000_000_000,
    self_model: dict[str, object] | None = None,
    bond: dict[str, object] | None = None,
    boundaries: dict[str, object] | None = None,
    style: StyleFingerprint | None = None,
    memory_refs: list[object] | None = None,
    startup_hash_override: str | None = None,
    self_source: IdentitySourceRef | None = None,
    release_manifest: dict[str, object] | None = None,
) -> IdentitySnapshot:
    self_value = self_model or _self_model()
    bond_value = bond or _bond()
    boundary_value = boundaries or _boundaries()
    style_value = style or StyleFingerprint(
        warmth=0.7,
        curiosity=0.8,
        humor=0.4,
        sensitivity=0.65,
        assertiveness=0.55,
        playfulness=0.45,
        resilience=0.75,
        ts_ms=created_at_ms,
    )
    startup = build_startup_snapshot(
        {
            "request_id": "boot-1",
            "summary": {
                "boot_status": "READY",
                "private_diagnostic": PRIVATE_SENTINEL,
            },
        },
        migration_guard_report={"private": PRIVATE_SENTINEL},
        ts_ms=created_at_ms,
    )
    if startup_hash_override is not None:
        startup = replace(startup, snapshot_hash=startup_hash_override)
    refs = memory_refs
    if refs is None:
        refs = [
            IdentitySourceRef.from_payload(
                ref="memory://episodic/mem-001",
                payload={"private": PRIVATE_SENTINEL},
            )
        ]
    return build_identity_snapshot(
        repo_root=root,
        release_ref=release,
        previous_release_ref=previous,
        created_at_ms=created_at_ms,
        self_model_snapshot=self_value,
        self_model_source=self_source or _source("self-model", self_value),
        bond_projection=bond_value,
        bond_source=_source("bond", bond_value),
        memory_refs=refs,
        proactive_boundaries=boundary_value,
        proactive_boundary_source=_source("proactive-boundary", boundary_value),
        style_fingerprint=style_value,
        style_source=_source("style", style_value.as_dict()),
        startup_snapshot=startup,
        startup_ref=f"startup://{startup.request_id}/{startup.ts_ms}",
        release_manifest=release_manifest or {"release": release, "private_note": PRIVATE_SENTINEL},
        release_manifest_ref=f"release-manifest://{release.removeprefix('release://')}",
    )


def test_t_nle_20_snapshot_is_comparable_and_contains_only_private_safe_projection(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    first = _build(root)
    second = _build(
        root,
        release="release://natsume/v2",
        previous="release://natsume/v1",
        created_at_ms=1_800_000_100_000,
    )

    assert first.identity_digest == second.identity_digest
    assert first.snapshot_hash != second.snapshot_hash
    assert first.snapshot_ref != second.snapshot_ref
    serialized = json.dumps(second.to_dict(), ensure_ascii=False, sort_keys=True)
    assert PRIVATE_SENTINEL not in serialized
    assert "person_id" not in serialized
    assert "rift_cause" not in serialized
    assert "repair_hint" not in serialized
    assert "service_summary" not in serialized
    assert second.self_model.self_value_count == 1
    assert second.self_model.self_boundary_count == 1
    assert second.memory_refs[0].ref == "memory://episodic/mem-001"
    assert second.style_fingerprint.dimensions[0] == ("warmth", 0.7)
    assert all(item.ref.startswith("repo://") for item in (*second.soul_assets, *second.canon_assets))
    assert all(item.digest.startswith("sha256:") for item in (*second.soul_assets, *second.canon_assets))
    assert IdentitySnapshot.from_mapping(second.to_dict()) == second


def test_t_nle_20_major_canon_difference_is_visible_and_requires_master_review(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    previous = _build(root)
    canon_path = root / "host_brain/canon/world/wc-003-persona-core.json"
    canon_path.write_text(canon_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    current = _build(root, release="release://natsume/v2", previous=previous.release_ref)

    diff = diff_identity_snapshots(previous, current)
    assert diff.severity is IdentityDiffSeverity.MAJOR
    assert any(item.section == "canon" and item.field.endswith("wc-003-persona-core.json") for item in diff.differences)

    enabled_gate = evaluate_identity_release_gate(previous, current, enabled=True)
    assert enabled_gate.status == "REVIEW_REQUIRED"
    assert enabled_gate.publish_allowed is False
    assert enabled_gate.mutation_effect == "NONE"
    assert enabled_gate.review_candidate is not None
    assert enabled_gate.review_candidate.review_lane == "MASTER"
    assert enabled_gate.review_candidate.status == "PENDING"
    assert enabled_gate.review_candidate.automatic_apply is False
    review_item = enabled_gate.review_candidate.to_review_item(
        expires_at=1_900_000_000_000,
        checkpoint_id="checkpoint://release/natsume-v2",
    )
    assert review_item.review_type.value == "PRE_EXECUTION_APPROVAL"
    assert review_item.status.value == "PENDING"
    assert review_item.diff_ref == diff.diff_ref
    assert review_item.requester == "release_identity_snapshot"

    disabled_gate = evaluate_identity_release_gate(previous, current, enabled=False)
    assert disabled_gate.status == "DISABLED_FAIL_CLOSED"
    assert disabled_gate.publish_allowed is False
    assert disabled_gate.review_candidate is not None


@pytest.mark.parametrize(
    ("change", "expected_field"),
    [
        ("style", "warmth"),
        ("self_model", "self_continuity_score"),
        ("boundary", "max_daily_initiations"),
    ],
)
def test_t_nle_20_major_identity_differences_are_review_candidates(
    tmp_path: Path,
    change: str,
    expected_field: str,
) -> None:
    root = _identity_root(tmp_path)
    previous = _build(root)
    kwargs: dict[str, object] = {
        "release": "release://natsume/v2",
        "previous": previous.release_ref,
    }
    if change == "style":
        kwargs["style"] = StyleFingerprint(
            warmth=0.35,
            curiosity=0.8,
            humor=0.4,
            sensitivity=0.65,
            assertiveness=0.55,
            playfulness=0.45,
            resilience=0.75,
        )
    elif change == "self_model":
        kwargs["self_model"] = _self_model(self_continuity_score=0.50)
    else:
        kwargs["boundaries"] = _boundaries(max_daily_initiations=5)
    current = _build(root, **kwargs)  # type: ignore[arg-type]

    gate = evaluate_identity_release_gate(previous, current, enabled=True)
    assert gate.publish_allowed is False
    assert gate.review_candidate is not None
    assert any(
        item.field == expected_field and item.severity is IdentityDiffSeverity.MAJOR
        for item in gate.diff.differences
    )


def test_t_nle_20_bond_and_memory_changes_are_visible_without_copying_bodies(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    previous = _build(root)
    changed_bond = _bond()
    changed_bond["person_id"] = f"another-{PRIVATE_SENTINEL}"
    changed_bond["relationship"] = {
        **dict(changed_bond["relationship"]),  # type: ignore[arg-type]
        "rift": {
            "active": True,
            "rift_level": 0.3,
            "repair_progress": 0.4,
            "rift_cause": PRIVATE_SENTINEL,
        },
    }
    current = _build(
        root,
        release="release://natsume/v2",
        previous=previous.release_ref,
        bond=changed_bond,
        memory_refs=[IdentitySourceRef.from_payload(ref="memory://episodic/mem-002", payload=PRIVATE_SENTINEL)],
    )

    diff = diff_identity_snapshots(previous, current)
    assert any(item.section == "bond" and item.field == "rift_level" for item in diff.differences)
    assert any(item.section == "memory_refs" for item in diff.differences)
    assert PRIVATE_SENTINEL not in json.dumps(diff.to_dict(), ensure_ascii=False)


def test_t_nle_20_private_self_identity_change_with_same_count_is_hash_visible(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    previous = _build(root)
    current = _build(
        root,
        release="release://natsume/v2",
        previous=previous.release_ref,
        self_model=_self_model(self_boundaries=["A different private boundary body"]),
    )

    diff = diff_identity_snapshots(previous, current)
    boundary_digest = next(
        item for item in diff.differences if item.field == "self_boundaries_digest"
    )
    assert boundary_digest.severity is IdentityDiffSeverity.MAJOR
    serialized = json.dumps(current.to_dict(), ensure_ascii=False)
    assert "A different private boundary body" not in serialized
    assert PRIVATE_SENTINEL not in serialized


def test_t_nle_20_memory_body_and_unbounded_boundary_fields_fail_closed(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    with pytest.raises(ContractViolation) as body_error:
        _build(
            root,
            memory_refs=[
                {
                    "ref": "memory://episodic/mem-001",
                    "digest": "sha256:" + "a" * 64,
                    "body": PRIVATE_SENTINEL,
                }
            ],
        )
    assert body_error.value.reason_code == IDENTITY_PRIVATE_BODY_REJECTED

    with pytest.raises(ContractViolation) as boundary_error:
        _build(root, boundaries={**_boundaries(), "narrative": PRIVATE_SENTINEL})
    assert boundary_error.value.reason_code == IDENTITY_PRIVATE_BODY_REJECTED


def test_t_nle_20_tampered_startup_snapshot_and_nonfinite_values_fail_closed(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    with pytest.raises(ContractViolation) as startup_error:
        _build(root, startup_hash_override="0" * 64)
    assert startup_error.value.reason_code == IDENTITY_STARTUP_SNAPSHOT_INVALID

    with pytest.raises(ContractViolation):
        _build(root, self_model=_self_model(agency_score=float("nan")))


def test_t_nle_20_source_and_release_manifest_hashes_must_be_locatable(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    with pytest.raises(ContractViolation):
        _build(
            root,
            self_source=IdentitySourceRef(
                ref="state://identity/self-model",
                digest="sha256:" + "0" * 64,
            ),
        )

    with pytest.raises(ContractViolation) as manifest_error:
        _build(
            root,
            release_manifest={
                "world_canon_asset_hashes": {
                    "host_brain/canon/world/wc-001-system-terms.json": "0" * 64,
                }
            },
        )
    assert manifest_error.value.reason_code == IDENTITY_RELEASE_MANIFEST_MISMATCH


def test_t_nle_20_rollback_selects_exact_previous_release_without_mutation(tmp_path: Path) -> None:
    root = _identity_root(tmp_path)
    previous = _build(root)
    current = _build(root, release="release://natsume/v2", previous=previous.release_ref)
    plan = build_identity_rollback_plan(current, previous)

    assert plan.target_release_ref == previous.release_ref
    assert plan.target_snapshot_ref == previous.snapshot_ref
    assert plan.target_snapshot_hash == previous.snapshot_hash
    assert plan.action == "SELECT_PREVIOUS_RELEASE_SNAPSHOT"
    assert plan.feature_flag == IDENTITY_SNAPSHOT_FEATURE_FLAG
    assert plan.feature_flag_disable_value == "false"
    assert plan.automatic_personality_mutation is False
    assert plan.automatic_identity_migration is False

    unrelated = _build(root, release="release://natsume/unrelated")
    with pytest.raises(ContractViolation) as mismatch:
        build_identity_rollback_plan(current, unrelated)
    assert mismatch.value.reason_code == IDENTITY_PREVIOUS_RELEASE_MISMATCH


def test_t_nle_20_feature_flag_defaults_on_and_snapshot_is_immutable(tmp_path: Path) -> None:
    assert identity_snapshot_gate_enabled({}) is True
    assert identity_snapshot_gate_enabled({IDENTITY_SNAPSHOT_FEATURE_FLAG: "true"}) is True
    assert identity_snapshot_gate_enabled({IDENTITY_SNAPSHOT_FEATURE_FLAG: "false"}) is False
    snapshot = _build(_identity_root(tmp_path))
    with pytest.raises(FrozenInstanceError):
        snapshot.release_ref = "release://tampered"  # type: ignore[misc]

    forbidden = {"apply", "migrate", "mutate", "approve", "write", "save"}
    public_methods = {name for name in dir(IdentitySnapshot) if not name.startswith("_")}
    assert forbidden.isdisjoint(public_methods)


def test_t_nle_20_persisted_snapshot_rejects_private_category_injection(tmp_path: Path) -> None:
    snapshot = _build(_identity_root(tmp_path))
    payload = snapshot.to_dict()
    payload["self_model"]["dominant_recent_emotion"] = PRIVATE_SENTINEL

    with pytest.raises(ContractViolation):
        IdentitySnapshot.from_mapping(payload)
