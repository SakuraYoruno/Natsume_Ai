import json
import unittest
from pathlib import Path

from edge_client.config import export_edge_config_snapshot
from host_brain.app.config import export_host_config_snapshot
from host_brain.core.errors import (
    RELEASE_ASSET_MISSING,
    RELEASE_CHECKSUM_DRIFT,
    RELEASE_FLOW_POINTER_MISMATCH,
    RELEASE_MODEL_PLANE_DIGEST_MISMATCH,
    RELEASE_ROLLBACK_DIGEST_MISSING,
    RELEASE_ROLLBACK_TARGET_MISSING,
)
from host_brain.release.release_manifest import (
    STATUS_FAILED,
    STATUS_READY,
    build_release_checklist,
    build_release_matrix,
    build_rollback_mapping,
    validate_release_bundle,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "release"
CONFIG_FIXTURES = ROOT / "tests" / "fixtures" / "config" / "good"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestReleaseManifestContract(unittest.TestCase):
    def test_t_release_01_m0_m3_checklists_are_stable_and_replayable(self) -> None:
        specs = _load_json(FIXTURES / "good" / "release_specs.json")["specs"]
        rollback_fixture = _load_json(FIXTURES / "good" / "rollback_mapping.json")

        host_snapshot = export_host_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "host_prod_with_key_env.json")["env"]
        )
        edge_snapshot = export_edge_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "edge_prod_with_hmac_env.json")["env"]
        )
        self.assertTrue(host_snapshot.success, msg=host_snapshot.as_dict())
        self.assertTrue(edge_snapshot.success, msg=edge_snapshot.as_dict())
        assert host_snapshot.snapshot is not None
        assert edge_snapshot.snapshot is not None
        snapshot_refs = {
            host_snapshot.snapshot["digest"],
            edge_snapshot.snapshot["digest"],
        }

        checklists: list[dict] = []
        for spec in specs:
            checklist = build_release_checklist(
                root=ROOT,
                milestone=spec["milestone"],
                release_id=spec["release_id"],
                assets=spec["assets"],
                required_services=spec["required_services"],
                config_snapshot_refs=snapshot_refs,
                ts_ms=1_800_090_000_000,
            )
            self.assertEqual(STATUS_READY, checklist["status"])
            self.assertEqual(spec["milestone"], checklist["milestone"])
            self.assertIn("assets", checklist)
            self.assertIn("checksums", checklist)
            self.assertIn("required_services", checklist)
            self.assertIn("model_plane", checklist)
            self.assertIn("asset_digest", checklist["model_plane"])
            self.assertTrue(set(checklist["checksums"].keys()).issuperset(set(checklist["assets"])))
            checklists.append(checklist)

        matrix = build_release_matrix(root=ROOT, release_specs=specs)
        self.assertEqual(4, matrix["summary"]["release_count"])
        self.assertEqual(["M0", "M1", "M2", "M3"], matrix["summary"]["milestones"])

        known_release_ids = {spec["release_id"] for spec in specs}
        rollback_mapping = build_rollback_mapping(
            release_id=rollback_fixture["release_id"],
            rollback_target=rollback_fixture["rollback_target"],
            impact_scope=rollback_fixture["impact_scope"],
            milestone=rollback_fixture["milestone"],
            model_plane_asset_digest=checklists[-1]["model_plane"]["asset_digest"],
            ts_ms=rollback_fixture["ts_ms"],
        )
        result = validate_release_bundle(
            root=ROOT,
            checklist=checklists[-1],
            rollback_mapping=rollback_mapping,
            known_release_ids=known_release_ids,
            known_config_snapshot_refs=snapshot_refs,
            known_model_plane_digests={checklists[-1]["model_plane"]["asset_digest"]},
        )
        self.assertEqual(STATUS_READY, result["status"])
        self.assertIsNone(result["reason_code"])
        self.assertEqual(0, result["summary"]["issue_count"])

    def test_t_release_01_missing_asset_fixture_rejected(self) -> None:
        checklist = _load_json(FIXTURES / "bad" / "missing_asset_checklist.json")
        rollback_mapping = _load_json(FIXTURES / "good" / "rollback_mapping.json")
        result = validate_release_bundle(
            root=ROOT,
            checklist=checklist,
            rollback_mapping=rollback_mapping,
            known_release_ids={
                "rel-m0-2026-03-01",
                "rel-m1-2026-03-01",
                "rel-m2-2026-03-01",
                "rel-m3-2026-03-01",
            },
        )
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(RELEASE_ASSET_MISSING, result["reason_code"])
        self.assertTrue(any(row["reason_code"] == RELEASE_ASSET_MISSING for row in result["issues"]))

    def test_t_release_01_checksum_drift_fixture_rejected(self) -> None:
        checklist = _load_json(FIXTURES / "bad" / "checksum_drift_checklist.json")
        rollback_mapping = _load_json(FIXTURES / "good" / "rollback_mapping.json")
        result = validate_release_bundle(
            root=ROOT,
            checklist=checklist,
            rollback_mapping=rollback_mapping,
            known_release_ids={
                "rel-m0-2026-03-01",
                "rel-m1-2026-03-01",
                "rel-m2-2026-03-01",
                "rel-m3-2026-03-01",
            },
        )
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(RELEASE_CHECKSUM_DRIFT, result["reason_code"])
        self.assertTrue(any(row["reason_code"] == RELEASE_CHECKSUM_DRIFT for row in result["issues"]))

    def test_t_release_01_rollback_target_missing_rejected(self) -> None:
        host_snapshot = export_host_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "host_prod_with_key_env.json")["env"]
        )
        edge_snapshot = export_edge_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "edge_prod_with_hmac_env.json")["env"]
        )
        assert host_snapshot.snapshot is not None
        assert edge_snapshot.snapshot is not None
        checklist = build_release_checklist(
            root=ROOT,
            milestone="M3",
            release_id="rel-m3-2026-03-01",
            assets=["tests/fixtures/release/assets/m3/manifest.txt"],
            required_services=["host.audit.sqlite_ledger", "host.vision.screen_ocr"],
            config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
            ts_ms=1_800_090_000_001,
        )
        rollback_mapping = _load_json(FIXTURES / "bad" / "rollback_target_missing.json")
        result = validate_release_bundle(
            root=ROOT,
            checklist=checklist,
            rollback_mapping=rollback_mapping,
            known_release_ids={
                "rel-m0-2026-03-01",
                "rel-m1-2026-03-01",
                "rel-m2-2026-03-01",
                "rel-m3-2026-03-01",
            },
            known_config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
        )
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(RELEASE_ROLLBACK_TARGET_MISSING, result["reason_code"])
        self.assertTrue(any(row["reason_code"] == RELEASE_ROLLBACK_TARGET_MISSING for row in result["issues"]))

    def test_t_release_01_flow_refs_mismatch_rejected_by_t_flow_02_rule(self) -> None:
        host_snapshot = export_host_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "host_prod_with_key_env.json")["env"]
        )
        edge_snapshot = export_edge_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "edge_prod_with_hmac_env.json")["env"]
        )
        assert host_snapshot.snapshot is not None
        assert edge_snapshot.snapshot is not None
        checklist = build_release_checklist(
            root=ROOT,
            milestone="M1",
            release_id="rel-m1-2026-03-01",
            assets=["tests/fixtures/release/assets/m1/manifest.txt"],
            required_services=["host.audit.sqlite_ledger", "host.integrations.llm_gateway"],
            config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
            ts_ms=1_800_090_000_002,
        )
        checklist["flow_refs"]["dev"] = "flows/exports/mismatch/flow.json"
        rollback_mapping = build_rollback_mapping(
            release_id="rel-m1-2026-03-01",
            rollback_target="rel-m0-2026-03-01",
            impact_scope=["host.audit.sqlite_ledger"],
            milestone="M1",
            model_plane_asset_digest=checklist["model_plane"]["asset_digest"],
        )
        result = validate_release_bundle(
            root=ROOT,
            checklist=checklist,
            rollback_mapping=rollback_mapping,
            known_release_ids={"rel-m0-2026-03-01", "rel-m1-2026-03-01"},
            known_config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
            known_model_plane_digests={checklist["model_plane"]["asset_digest"]},
        )
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(RELEASE_FLOW_POINTER_MISMATCH, result["reason_code"])
        self.assertTrue(any(row["reason_code"] == RELEASE_FLOW_POINTER_MISMATCH for row in result["issues"]))

    def test_t_flow_03_model_plane_digest_mismatch_rejected(self) -> None:
        host_snapshot = export_host_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "host_prod_with_key_env.json")["env"]
        )
        edge_snapshot = export_edge_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "edge_prod_with_hmac_env.json")["env"]
        )
        assert host_snapshot.snapshot is not None
        assert edge_snapshot.snapshot is not None

        checklist = build_release_checklist(
            root=ROOT,
            milestone="M2",
            release_id="rel-m2-2026-03-01",
            assets=["tests/fixtures/release/assets/m2/manifest.txt"],
            required_services=["host.audit.sqlite_ledger", "host.retrieval.embedding"],
            config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
        )
        checklist["model_plane"]["asset_digest"] = "sha256:deadbeef"

        rollback_mapping = build_rollback_mapping(
            release_id="rel-m2-2026-03-01",
            rollback_target="rel-m1-2026-03-01",
            impact_scope=["host.retrieval.embedding"],
            milestone="M2",
            model_plane_asset_digest="sha256:deadbeef",
        )
        result = validate_release_bundle(
            root=ROOT,
            checklist=checklist,
            rollback_mapping=rollback_mapping,
            known_release_ids={"rel-m1-2026-03-01", "rel-m2-2026-03-01"},
            known_config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
            known_model_plane_digests={"sha256:known-good"},
        )
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(RELEASE_MODEL_PLANE_DIGEST_MISMATCH, result["reason_code"])
        self.assertTrue(
            any(row["reason_code"] == RELEASE_MODEL_PLANE_DIGEST_MISMATCH for row in result["issues"])
        )

    def test_t_release_01_rollback_digest_missing_rejected(self) -> None:
        host_snapshot = export_host_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "host_prod_with_key_env.json")["env"]
        )
        edge_snapshot = export_edge_config_snapshot(
            env=_load_json(CONFIG_FIXTURES / "edge_prod_with_hmac_env.json")["env"]
        )
        assert host_snapshot.snapshot is not None
        assert edge_snapshot.snapshot is not None

        checklist = build_release_checklist(
            root=ROOT,
            milestone="M1",
            release_id="rel-m1-2026-03-01",
            assets=["tests/fixtures/release/assets/m1/manifest.txt"],
            required_services=["host.audit.sqlite_ledger"],
            config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
        )
        rollback_mapping = build_rollback_mapping(
            release_id="rel-m1-2026-03-01",
            rollback_target="rel-m0-2026-03-01",
            impact_scope=["host.audit.sqlite_ledger"],
            milestone="M1",
        )
        result = validate_release_bundle(
            root=ROOT,
            checklist=checklist,
            rollback_mapping=rollback_mapping,
            known_release_ids={"rel-m0-2026-03-01", "rel-m1-2026-03-01"},
            known_config_snapshot_refs={
                host_snapshot.snapshot["digest"],
                edge_snapshot.snapshot["digest"],
            },
            known_model_plane_digests={checklist["model_plane"]["asset_digest"]},
        )
        self.assertEqual(STATUS_FAILED, result["status"])
        self.assertEqual(RELEASE_ROLLBACK_DIGEST_MISSING, result["reason_code"])
        self.assertTrue(any(row["reason_code"] == RELEASE_ROLLBACK_DIGEST_MISSING for row in result["issues"]))


if __name__ == "__main__":
    unittest.main()
