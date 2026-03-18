import json
import unittest
from pathlib import Path

from edge_client.config import export_edge_config_snapshot, load_edge_config
from host_brain.app.config import (
    build_host_runtime_statuses,
    export_host_config_snapshot,
    load_host_config,
)
from host_brain.core.errors import (
    KEY_PROFILE_EXPIRED,
    KEY_PROFILE_SCOPE_INVALID,
    KEY_PROFILE_SECRET_REF_MISSING,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "config"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestKeyProfileContract(unittest.TestCase):
    def test_t_key_01_runtime_secret_refs_are_resolved_and_snapshot_is_redacted(self) -> None:
        runtime = _load_json(FIXTURES / "good" / "key_profiles_runtime.json")
        host_fixture = _load_json(FIXTURES / "good" / "host_prod_key_ref_env.json")
        edge_fixture = _load_json(FIXTURES / "good" / "edge_prod_key_ref_env.json")

        host_result = load_host_config(
            env=host_fixture["env"],
            key_profiles=runtime["profiles"],
            secret_store=runtime["secret_store"],
            node_id=runtime["host_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        edge_result = load_edge_config(
            env=edge_fixture["env"],
            key_profiles=runtime["profiles"],
            secret_store=runtime["secret_store"],
            node_id=runtime["edge_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        self.assertTrue(host_result.success, msg=host_result.as_dict())
        self.assertTrue(edge_result.success, msg=edge_result.as_dict())
        assert host_result.config is not None
        assert edge_result.config is not None
        self.assertTrue(host_result.config.openai_api_key)
        self.assertEqual("sec://openai/natsu-node-01", host_result.config.openai_api_key_ref)
        self.assertEqual("resolved", host_result.config.key_profile_status)
        self.assertTrue(edge_result.config.hmac_shared_key)
        self.assertEqual("sec://edge/natsu-node-06", edge_result.config.hmac_shared_key_ref)
        self.assertEqual("resolved", edge_result.config.key_profile_status)

        host_snapshot = export_host_config_snapshot(
            env=host_fixture["env"],
            key_profiles=runtime["profiles"],
            secret_store=runtime["secret_store"],
            node_id=runtime["host_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        edge_snapshot = export_edge_config_snapshot(
            env=edge_fixture["env"],
            key_profiles=runtime["profiles"],
            secret_store=runtime["secret_store"],
            node_id=runtime["edge_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        self.assertTrue(host_snapshot.success, msg=host_snapshot.as_dict())
        self.assertTrue(edge_snapshot.success, msg=edge_snapshot.as_dict())
        assert host_snapshot.snapshot is not None
        assert edge_snapshot.snapshot is not None

        self.assertEqual("<redacted>", host_snapshot.snapshot["values"]["openai_api_key"])
        self.assertEqual("sec://openai/natsu-node-01", host_snapshot.snapshot["values"]["openai_api_key_ref"])
        self.assertIn("OPENAI_API_KEY", host_snapshot.snapshot["redacted_keys"])
        self.assertEqual("<redacted>", edge_snapshot.snapshot["values"]["hmac_shared_key"])
        self.assertEqual("sec://edge/natsu-node-06", edge_snapshot.snapshot["values"]["hmac_shared_key_ref"])
        self.assertIn("EDGE_HMAC_SHARED_KEY", edge_snapshot.snapshot["redacted_keys"])

    def test_t_key_01_missing_secret_ref_rejected_in_prod(self) -> None:
        runtime = _load_json(FIXTURES / "good" / "key_profiles_runtime.json")
        fixture = _load_json(FIXTURES / "bad" / "host_prod_key_ref_missing_secret.json")

        result = load_host_config(
            env=fixture["env"],
            key_profiles=runtime["profiles"],
            secret_store={},
            node_id=runtime["host_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        self.assertFalse(result.success)
        self.assertEqual(KEY_PROFILE_SECRET_REF_MISSING, result.reason_code)
        self.assertTrue(any(issue.field == "OPENAI_API_KEY_REF" for issue in result.issues))

    def test_t_key_01_scope_mismatch_rejected_with_stable_reason(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "host_prod_key_ref_scope_mismatch.json")
        result = load_host_config(
            env=fixture["env"],
            key_profiles=fixture["profiles"],
            secret_store=fixture["secret_store"],
            node_id="natsu-node-01",
            current_epoch=20260301,
        )
        self.assertFalse(result.success)
        self.assertEqual(KEY_PROFILE_SCOPE_INVALID, result.reason_code)

    def test_t_key_01_expired_profile_rejected_with_stable_reason(self) -> None:
        fixture = _load_json(FIXTURES / "bad" / "host_prod_key_ref_expired.json")
        result = load_host_config(
            env=fixture["env"],
            key_profiles=fixture["profiles"],
            secret_store=fixture["secret_store"],
            node_id="natsu-node-01",
            current_epoch=fixture["current_epoch"],
        )
        self.assertFalse(result.success)
        self.assertEqual(KEY_PROFILE_EXPIRED, result.reason_code)

    def test_t_key_01_missing_key_in_dev_degrades_to_cold_start_reason(self) -> None:
        runtime = _load_json(FIXTURES / "good" / "key_profiles_runtime.json")
        fixture = _load_json(FIXTURES / "good" / "host_dev_key_ref_missing_secret_degrade.json")

        result = load_host_config(
            env=fixture["env"],
            key_profiles=runtime["profiles"],
            secret_store={},
            node_id=runtime["host_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        self.assertTrue(result.success, msg=result.as_dict())
        assert result.config is not None
        self.assertIsNone(result.config.openai_api_key)
        self.assertEqual("degraded", result.config.key_profile_status)

        statuses = build_host_runtime_statuses(result.config)
        self.assertEqual(
            "cold_start",
            statuses["host.integrations.llm_gateway"]["unavailable_kind"],
        )

    def test_t_key_01_deepseek_key_ref_is_resolved_for_node_09(self) -> None:
        runtime = {
            "current_epoch": 20260301,
            "host_node_id": "natsu-node-09",
            "profiles": [
                {
                    "provider": "deepseek",
                    "node_id": "natsu-node-09",
                    "scope": "host.llm",
                    "secret_ref": "sec://deepseek/natsu-node-09",
                    "rotation_epoch": 20270101,
                    "status": "active",
                }
            ],
            "secret_store": {
                "sec://deepseek/natsu-node-09": "sk-deepseek-demo-value",
            },
        }
        env = {
            "Natsume_ENV": "prod",
            "HOST_LLM_PROVIDER": "deepseek",
            "HOST_COLD_START_ALLOWED": "false",
            "DEEPSEEK_API_KEY_REF": "sec://deepseek/natsu-node-09",
        }
        result = load_host_config(
            env=env,
            key_profiles=runtime["profiles"],
            secret_store=runtime["secret_store"],
            node_id=runtime["host_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        self.assertTrue(result.success, msg=result.as_dict())
        assert result.config is not None
        self.assertEqual("deepseek", result.config.llm_provider)
        self.assertTrue(result.config.deepseek_api_key)
        self.assertEqual("sec://deepseek/natsu-node-09", result.config.deepseek_api_key_ref)
        self.assertTrue(result.config.llm_api_key)
        self.assertEqual("sec://deepseek/natsu-node-09", result.config.llm_api_key_ref)
        self.assertEqual("resolved", result.config.key_profile_status)

        snapshot = export_host_config_snapshot(
            env=env,
            key_profiles=runtime["profiles"],
            secret_store=runtime["secret_store"],
            node_id=runtime["host_node_id"],
            current_epoch=runtime["current_epoch"],
        )
        self.assertTrue(snapshot.success, msg=snapshot.as_dict())
        assert snapshot.snapshot is not None
        self.assertEqual("<redacted>", snapshot.snapshot["values"]["deepseek_api_key"])
        self.assertEqual("<redacted>", snapshot.snapshot["values"]["llm_api_key"])
        self.assertIn("DEEPSEEK_API_KEY", snapshot.snapshot["redacted_keys"])

    def test_t_key_01_deepseek_and_gemini_key_ref_are_resolved(self) -> None:
        test_cases = [
            (
                "deepseek",
                "natsu-node-04",
                "DEEPSEEK_API_KEY_REF",
                "sec://deepseek/natsu-node-04",
                "deepseek_api_key",
                "DEEPSEEK_API_KEY",
            ),
            (
                "gemini",
                "natsu-node-06",
                "GEMINI_API_KEY_REF",
                "sec://gemini/natsu-node-06",
                "gemini_api_key",
                "GEMINI_API_KEY",
            ),
        ]
        for provider, node_id, ref_key, secret_ref, key_field, redacted_key in test_cases:
            runtime = {
                "current_epoch": 20260301,
                "host_node_id": node_id,
                "profiles": [
                    {
                        "provider": provider,
                        "node_id": node_id,
                        "scope": "host.llm",
                        "secret_ref": secret_ref,
                        "rotation_epoch": 20270101,
                        "status": "active",
                    }
                ],
                "secret_store": {
                    secret_ref: f"sk-{provider}-demo-value",
                },
            }
            env = {
                "Natsume_ENV": "prod",
                "HOST_LLM_PROVIDER": provider,
                "HOST_COLD_START_ALLOWED": "false",
                ref_key: secret_ref,
            }
            result = load_host_config(
                env=env,
                key_profiles=runtime["profiles"],
                secret_store=runtime["secret_store"],
                node_id=runtime["host_node_id"],
                current_epoch=runtime["current_epoch"],
            )
            self.assertTrue(result.success, msg=result.as_dict())
            assert result.config is not None
            self.assertEqual(provider, result.config.llm_provider)
            self.assertTrue(getattr(result.config, key_field))
            self.assertTrue(result.config.llm_api_key)
            self.assertEqual(secret_ref, result.config.llm_api_key_ref)
            self.assertEqual("resolved", result.config.key_profile_status)

            snapshot = export_host_config_snapshot(
                env=env,
                key_profiles=runtime["profiles"],
                secret_store=runtime["secret_store"],
                node_id=runtime["host_node_id"],
                current_epoch=runtime["current_epoch"],
            )
            self.assertTrue(snapshot.success, msg=snapshot.as_dict())
            assert snapshot.snapshot is not None
            self.assertEqual("<redacted>", snapshot.snapshot["values"][key_field])
            self.assertEqual("<redacted>", snapshot.snapshot["values"]["llm_api_key"])
            self.assertIn(redacted_key, snapshot.snapshot["redacted_keys"])


if __name__ == "__main__":
    unittest.main()
