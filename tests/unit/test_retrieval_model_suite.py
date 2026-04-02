from __future__ import annotations

import json

from host_brain.retrieval.embedding import load_embedding_runtime_config
from host_brain.retrieval.model_suite import apply_retrieval_model_suite_env_defaults, resolve_retrieval_model_suite
from host_brain.retrieval.reranker import load_reranker_runtime_config


def test_apply_retrieval_model_suite_env_defaults_force_generates_both_route_jsons() -> None:
    env = {
        "RETR_MODEL_SUITE_PROFILE": "qwen3_0_6b_local",
        "RETR_MODEL_SUITE_FORCE": "true",
    }

    merged = apply_retrieval_model_suite_env_defaults(env)

    embed_routes = json.loads(merged["RETR_EMBED_ROUTES_JSON"])
    rerank_routes = json.loads(merged["RETR_RERANK_ROUTES_JSON"])

    assert merged["RETR_MODEL_SUITE_EFFECTIVE_PROFILE"] == "qwen3_0_6b_local"
    assert embed_routes["host_default"]["model_id"] == "models/Qwen3-Embedding-0.6B"
    assert rerank_routes["host_default"]["model_id"] == "models/Qwen3-Reranker-0.6B"
    assert rerank_routes["host_default"]["top_k"] == 8


def test_resolve_retrieval_model_suite_applies_overrides() -> None:
    suite = resolve_retrieval_model_suite({
        "RETR_MODEL_SUITE_PROFILE": "qwen3_0_6b_local",
        "RETR_MODEL_SUITE_DEVICE": "cpu",
        "RETR_MODEL_SUITE_NODE_SELECTOR": "default",
    })

    assert suite is not None
    assert suite.device == "cpu"
    assert suite.node_selector == "default"


def test_load_embedding_and_reranker_runtime_config_can_be_driven_by_suite_only() -> None:
    env = {
        "RETR_MODEL_SUITE_PROFILE": "qwen3_0_6b_local",
        "RETR_MODEL_SUITE_FORCE": "true",
        "RETR_EMBED_ENABLED": "true",
        "RETR_RERANK_ENABLED": "true",
    }

    embed_cfg = load_embedding_runtime_config(env=env)
    rerank_cfg = load_reranker_runtime_config(env=env)

    assert embed_cfg.success is True
    assert rerank_cfg.success is True
    assert embed_cfg.config is not None
    assert rerank_cfg.config is not None
    assert embed_cfg.config.routes[embed_cfg.config.default_route].model_id == "models/Qwen3-Embedding-0.6B"
    assert rerank_cfg.config.routes[rerank_cfg.config.default_route].model_id == "models/Qwen3-Reranker-0.6B"
    assert rerank_cfg.config.routes[rerank_cfg.config.default_route].top_k == 8
