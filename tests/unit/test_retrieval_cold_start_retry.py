from __future__ import annotations


def test_embedding_retries_once_after_slow_cold_start(monkeypatch) -> None:
    from host_brain.retrieval import embedding as embmod

    call_count = 0
    perf_values = [0.0, 0.21, 0.21, 0.25]

    def _fake_perf_counter() -> float:
        return perf_values.pop(0)

    def _provider(text, route):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        return [0.1, 0.2], route.model_id

    runtime_config = embmod.EmbeddingRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": embmod.EmbeddingRouteConfig(
                route_id="trial",
                provider="sentence_transformers",
                model_id="local-embed",
                dim=2,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )
    monkeypatch.setattr(embmod, "perf_counter", _fake_perf_counter)

    service = embmod.EmbeddingServiceContract(
        runtime_config=runtime_config,
        provider_registry={"sentence_transformers": _provider},
        available=True,
    )
    result = service.embed(request_id="req-embed-cold-start", text="hello")

    assert result.success
    assert result.output["warm_retry_used"] is True
    assert call_count == 2


def test_embedding_falls_back_after_repeated_slow_local_calls(monkeypatch) -> None:
    from host_brain.retrieval import embedding as embmod

    call_count = 0
    perf_values = [0.0, 0.21, 0.21, 0.45]

    def _fake_perf_counter() -> float:
        return perf_values.pop(0)

    def _provider(text, route):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        return [0.1, 0.2], route.model_id

    runtime_config = embmod.EmbeddingRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": embmod.EmbeddingRouteConfig(
                route_id="trial",
                provider="sentence_transformers",
                model_id="local-embed",
                dim=4,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )
    monkeypatch.setattr(embmod, "perf_counter", _fake_perf_counter)

    service = embmod.EmbeddingServiceContract(
        runtime_config=runtime_config,
        provider_registry={"sentence_transformers": _provider},
        available=True,
    )
    result = service.embed(request_id="req-embed-fallback", text="hello")

    assert result.success
    assert result.output["warm_retry_used"] is True
    assert result.output["fallback_used"] is True
    assert result.output["effective_provider"] == embmod.DEFAULT_EMBED_PROVIDER
    assert result.output["embedding_model_id"] == "hash-embed-v1"
    assert len(result.output["embedding_vector"]) == 4
    assert call_count == 2


def test_reranker_retries_once_after_slow_cold_start(monkeypatch) -> None:
    from host_brain.retrieval import reranker as rermod

    call_count = 0
    perf_values = [0.0, 0.31, 0.31, 0.35]

    def _fake_perf_counter() -> float:
        return perf_values.pop(0)

    def _provider(query, candidates, route):  # noqa: ANN001
        nonlocal call_count
        call_count += 1
        return [0.9 for _ in candidates], route.model_id

    runtime_config = rermod.RerankRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": rermod.RerankRouteConfig(
                route_id="trial",
                provider="qwen3_reranker",
                model_id="local-rerank",
                top_k=2,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )
    monkeypatch.setattr(rermod, "perf_counter", _fake_perf_counter)

    service = rermod.RerankerServiceContract(
        runtime_config=runtime_config,
        provider_registry={"qwen3_reranker": _provider},
        available=True,
    )
    result = service.rerank(
        request_id="req-rerank-cold-start",
        query="natsume",
        candidates=[
            {"doc_id": "m1", "text": "natsume and youkai"},
            {"doc_id": "m2", "text": "weather forecast"},
        ],
        top_k=1,
    )

    assert result.success
    assert result.output["warm_retry_used"] is True
    assert call_count == 2


def test_embedding_falls_back_after_local_provider_error() -> None:
    from host_brain.core.errors import RETR_EMBED_UNAVAILABLE
    from host_brain.retrieval import embedding as embmod

    def _provider(_text, _route):  # noqa: ANN001
        raise RuntimeError("torch missing")

    runtime_config = embmod.EmbeddingRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": embmod.EmbeddingRouteConfig(
                route_id="trial",
                provider="sentence_transformers",
                model_id="local-embed",
                dim=6,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )

    service = embmod.EmbeddingServiceContract(
        runtime_config=runtime_config,
        provider_registry={"sentence_transformers": _provider},
        available=True,
    )
    result = service.embed(request_id="req-embed-provider-error", text="hello")

    assert result.success
    assert result.output["fallback_used"] is True
    assert result.output["fallback_reason_code"] == RETR_EMBED_UNAVAILABLE
    assert result.output["effective_provider"] == embmod.DEFAULT_EMBED_PROVIDER
    assert result.output["embedding_model_id"] == "hash-embed-v1"
    assert len(result.output["embedding_vector"]) == 6


def test_reranker_falls_back_after_local_provider_error() -> None:
    from host_brain.core.errors import RETR_RERANK_UNAVAILABLE
    from host_brain.retrieval import reranker as rermod

    def _provider(_query, _candidates, _route):  # noqa: ANN001
        raise RuntimeError("torch missing")

    runtime_config = rermod.RerankRuntimeConfig(
        enabled=True,
        default_route="trial",
        routes={
            "trial": rermod.RerankRouteConfig(
                route_id="trial",
                provider="qwen3_reranker",
                model_id="local-rerank",
                top_k=2,
                timeout_ms=100,
                node_selector="default",
            )
        },
    )

    service = rermod.RerankerServiceContract(
        runtime_config=runtime_config,
        provider_registry={"qwen3_reranker": _provider},
        available=True,
    )
    result = service.rerank(
        request_id="req-rerank-provider-error",
        query="natsume",
        candidates=[
            {"doc_id": "m1", "text": "natsume and youkai"},
            {"doc_id": "m2", "text": "weather forecast"},
        ],
        top_k=1,
    )

    assert result.success
    assert result.output["fallback_used"] is True
    assert result.output["fallback_reason_code"] == RETR_RERANK_UNAVAILABLE
    assert result.output["effective_provider"] == rermod.DEFAULT_RERANK_PROVIDER
    assert result.output["rerank_model_id"] == rermod.RERANK_MODEL_ID
    assert len(result.output["topk"]) == 1


def test_qwen3_reranker_provider_serializes_cold_start_load(monkeypatch) -> None:
    import sys
    import threading
    import time
    import types
    from concurrent.futures import ThreadPoolExecutor

    import numpy as np

    from host_brain.retrieval import reranker as rermod

    class _DummyTensor:
        def __init__(self, data):  # noqa: ANN001
            self.arr = np.array(data, dtype=float)

        def __getitem__(self, key):  # noqa: ANN001
            return _DummyTensor(self.arr[key])

        def float(self):
            return self

        def cpu(self):
            return self

        def tolist(self):
            return self.arr.tolist()

    class _NoGrad:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

    class _DummyBatch(dict):
        def to(self, _device):  # noqa: ANN001
            return self

    class _DummyTokenizer:
        unk_token_id = -1

        def __call__(self, prompts, **_kwargs):  # noqa: ANN001
            return _DummyBatch({"input_ids": [[1, 2, 3] for _ in prompts]})

        def convert_tokens_to_ids(self, token: str) -> int:
            return {"no": 0, "No": 0, "yes": 1, "Yes": 1}.get(token, self.unk_token_id)

    class _DummyModel:
        def to(self, _device):  # noqa: ANN001
            return self

        def eval(self):
            return self

        def __call__(self, **inputs):  # noqa: ANN003
            batch_size = len(inputs["input_ids"])
            logits = np.zeros((batch_size, 1, 2), dtype=float)
            logits[:, 0, 0] = 0.1
            logits[:, 0, 1] = 0.9
            return types.SimpleNamespace(logits=_DummyTensor(logits))

    load_count = 0
    load_lock = threading.Lock()

    def _tokenizer_from_pretrained(*_args, **_kwargs):  # noqa: ANN001
        time.sleep(0.02)
        return _DummyTokenizer()

    def _model_from_pretrained(*_args, **_kwargs):  # noqa: ANN001
        nonlocal load_count
        time.sleep(0.05)
        with load_lock:
            load_count += 1
        return _DummyModel()

    torch_mod = types.ModuleType("torch")
    torch_mod.float16 = "float16"
    torch_mod.float32 = "float32"
    torch_mod.no_grad = _NoGrad
    torch_mod.stack = lambda values, dim=-1: _DummyTensor(np.stack([v.arr for v in values], axis=dim))
    torch_mod.softmax = lambda value, dim=-1: _DummyTensor(  # noqa: E731
        np.exp(value.arr - np.max(value.arr, axis=dim, keepdims=True))
        / np.exp(value.arr - np.max(value.arr, axis=dim, keepdims=True)).sum(axis=dim, keepdims=True)
    )

    transformers_mod = types.ModuleType("transformers")
    transformers_mod.AutoTokenizer = types.SimpleNamespace(from_pretrained=_tokenizer_from_pretrained)
    transformers_mod.AutoModelForCausalLM = types.SimpleNamespace(from_pretrained=_model_from_pretrained)

    monkeypatch.setitem(sys.modules, "torch", torch_mod)
    monkeypatch.setitem(sys.modules, "transformers", transformers_mod)

    provider = rermod._build_qwen3_reranker_provider(device="cpu")
    route = rermod.RerankRouteConfig(
        route_id="trial",
        provider="qwen3_reranker",
        model_id="dummy-reranker",
        top_k=2,
        timeout_ms=100,
        node_selector="default",
    )
    candidates = [{"doc_id": "m1", "snippet": "hello"}]

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(provider, "hello", candidates, route) for _ in range(6)]
        results = [future.result(timeout=2) for future in futures]

    assert load_count == 1
    assert len(results) == 6
    assert all(result[0][0] > 0.0 for result in results)
