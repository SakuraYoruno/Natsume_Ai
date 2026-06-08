from __future__ import annotations

import json
from pathlib import Path

from edge_client import config as edge_config


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_load_edge_config_resolves_ref_from_runtime_files(tmp_path: Path, monkeypatch) -> None:
    key_profiles_file = tmp_path / "key_profiles.runtime.json"
    secret_store_file = tmp_path / "secret_store.runtime.local.json"

    _write_json(
        key_profiles_file,
        {
            "current_epoch": 20260301,
            "edge_node_id": "natsu-node-06",
            "profiles": [
                {
                    "provider": "edge_hmac",
                    "node_id": "natsu-node-06",
                    "scope": "edge.rpc.auth",
                    "secret_ref": "sec://edge/natsu-node-06",
                    "rotation_epoch": 20270101,
                    "status": "active",
                }
            ],
        },
    )
    _write_json(secret_store_file, {"sec://edge/natsu-node-06": "edge-hmac-demo-value"})

    monkeypatch.setattr(edge_config, "DEFAULT_KEY_PROFILES", key_profiles_file)
    monkeypatch.setattr(edge_config, "DEFAULT_KEY_PROFILES_FALLBACK", key_profiles_file)
    monkeypatch.setattr(edge_config, "DEFAULT_SECRET_STORE", secret_store_file)
    monkeypatch.setattr(edge_config, "DEFAULT_SECRET_STORE_FALLBACK", secret_store_file)

    result = edge_config.load_edge_config(
        env={
            "Natsume_ENV": "prod",
            "EDGE_ALLOW_INSECURE_AUTH": "false",
            "EDGE_HMAC_SHARED_KEY_REF": "sec://edge/natsu-node-06",
        },
    )

    assert result.success, result.as_dict()
    assert result.config is not None
    assert result.config.hmac_shared_key == "edge-hmac-demo-value"
    assert result.config.key_profile_status == "resolved"
