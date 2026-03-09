from __future__ import annotations

import json
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host_brain.integrations.graph_contract import build_model_param_hash
from host_brain.integrations.graph_model_resolve import resolve_model_nodes


def test_model_node_overrides_are_merged_into_resolved_nodes(tmp_path: Path) -> None:
    model_plane_dir = tmp_path / "model_plane"
    model_plane_dir.mkdir(parents=True, exist_ok=True)

    (model_plane_dir / "node_registry.yaml").write_text(
        yaml.safe_dump(
            {
                "nodes": {
                    "renderer_ja_c_style": {
                        "provider": "gemini",
                        "model_id": "gemini-2.5-flash",
                        "temperature": 0.4,
                        "max_output_tokens": 200,
                    }
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (model_plane_dir / "model_profiles.yaml").write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "chat_ja_24x7_v1": {
                        "routing": {
                            "CHAT": {
                                "chain": ["renderer_ja_c_style"],
                                "timeouts_ms": {"renderer_ja_c_style": 1200},
                            }
                        }
                    }
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    def _mock_provider(_payload: dict[str, object]) -> str:
        return json.dumps(
            {
                "narrative": {"text": "ok"},
                "intent_draft": {},
                "meta": {"draft_only": True},
            },
            ensure_ascii=False,
        )

    graph_config = {
        "model_profile_id": "chat_ja_24x7_v1",
        "model_node_overrides": {
            "renderer_ja_c_style": {
                "api_key": "demo-key",
                "available": True,
                "provider_fn": _mock_provider,
            }
        },
    }
    node_bindings = {
        "respond": {
            "node_id": "respond",
            "task": "CHAT",
            "model_node_id": "renderer_ja_c_style",
        }
    }

    resolved = resolve_model_nodes(
        graph_config=graph_config,
        node_bindings=node_bindings,
        model_profile_id="chat_ja_24x7_v1",
        model_plane_dir=model_plane_dir,
    )

    renderer = resolved["renderer_ja_c_style"]
    assert renderer["api_key"] == "demo-key"
    assert renderer["available"] is True
    assert callable(renderer["provider_fn"])

    aliased = resolved["respond"]
    assert aliased["api_key"] == "demo-key"
    assert callable(aliased["provider_fn"])


def test_model_node_overrides_refresh_expected_param_hash(tmp_path: Path) -> None:
    model_plane_dir = tmp_path / "model_plane"
    model_plane_dir.mkdir(parents=True, exist_ok=True)

    (model_plane_dir / "node_registry.yaml").write_text(
        yaml.safe_dump(
            {
                "nodes": {
                    "renderer_ja_c_style": {
                        "provider": "gemini",
                        "model_id": "gemini-2.5-flash",
                        "temperature": 0.4,
                        "max_output_tokens": 200,
                    }
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (model_plane_dir / "model_profiles.yaml").write_text(
        yaml.safe_dump(
            {
                "profiles": {
                    "chat_ja_24x7_v1": {
                        "routing": {
                            "CHAT": {
                                "chain": ["renderer_ja_c_style"],
                                "timeouts_ms": {"renderer_ja_c_style": 1200},
                            }
                        }
                    }
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    graph_config = {
        "model_profile_id": "chat_ja_24x7_v1",
        "model_node_overrides": {
            "renderer_ja_c_style": {
                "model_id": "gemini-2.5-flash-lite",
                "timeout_ms": 4000,
            }
        },
    }
    node_bindings = {
        "respond": {
            "node_id": "respond",
            "task": "CHAT",
            "model_node_id": "renderer_ja_c_style",
        }
    }

    resolved = resolve_model_nodes(
        graph_config=graph_config,
        node_bindings=node_bindings,
        model_profile_id="chat_ja_24x7_v1",
        model_plane_dir=model_plane_dir,
    )

    renderer = resolved["renderer_ja_c_style"]
    assert renderer["model_id"] == "gemini-2.5-flash-lite"
    assert renderer["timeout_ms"] == 4000
    assert renderer["expected_param_hash"] == build_model_param_hash(
        provider="gemini",
        model_id="gemini-2.5-flash-lite",
        temperature=0.4,
        max_output_tokens=200,
        timeout_ms=4000,
    )
