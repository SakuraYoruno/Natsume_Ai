from __future__ import annotations

from host_brain.integrations.graph_contract import pick_api_key


def test_pick_api_key_prefers_model_node_key() -> None:
    state = {
        "model_nodes": {
            "chat_model": {
                "provider": "gemini",
                "api_key": "model-node-key",
            }
        },
        "dependency_statuses": {
            "gemini": {
                "available": True,
                "api_key": "dependency-key",
            }
        },
    }
    assert (
        pick_api_key(state=state, provider_name="gemini", model_node_id="chat_model")
        == "model-node-key"
    )


def test_pick_api_key_falls_back_to_dependency_status_key() -> None:
    state = {
        "model_nodes": {
            "chat_model": {
                "provider": "gemini",
            }
        },
        "dependency_statuses": {
            "gemini": {
                "available": True,
                "api_key": "dependency-key",
            }
        },
    }
    assert (
        pick_api_key(state=state, provider_name="gemini", model_node_id="chat_model")
        == "dependency-key"
    )


def test_pick_api_key_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
    state = {
        "model_nodes": {
            "chat_model": {
                "provider": "gemini",
            }
        },
        "dependency_statuses": {},
    }
    assert (
        pick_api_key(state=state, provider_name="gemini", model_node_id="chat_model")
        == "env-gemini-key"
    )
