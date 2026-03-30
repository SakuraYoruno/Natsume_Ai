"""Reranker provider 构建函数（Cross-Encoder、Qwen3 Reranker）。

从 reranker_config.py 拆分，包含硬件模型 provider 工厂函数。
"""

from __future__ import annotations

import math
import threading
from typing import Any

from host_brain.retrieval.reranker_schemas import RerankRouteConfig
from typing import Callable

RerankProvider = Callable[..., tuple[list[float], str]]
def _build_cross_encoder_provider(*, device: str) -> RerankProvider:
    cache: dict[str, Any] = {}
    cache_lock = threading.Lock()
    model_locks: dict[str, threading.Lock] = {}
    runtime_device = str(device or "cpu").strip() or "cpu"

    def _get_model_lock(model_id: str) -> threading.Lock:
        with cache_lock:
            lock = model_locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                model_locks[model_id] = lock
            return lock

    def _provider(
        query: str,
        candidates: list[dict[str, Any]],
        route: RerankRouteConfig | None = None,
    ) -> tuple[list[float], str]:
        model_id = str(route.model_id).strip() if route is not None else "BAAI/bge-reranker-large"
        try:
            from sentence_transformers import CrossEncoder
        except Exception as exc:  # pragma: no cover - guarded by runtime dependency check
            raise RuntimeError(f"sentence-transformers package unavailable: {exc}") from exc

        model = cache.get(model_id)
        if model is None:
            model_lock = _get_model_lock(model_id)
            with model_lock:
                model = cache.get(model_id)
                if model is None:
                    model = CrossEncoder(model_name_or_path=model_id, device=runtime_device)
                    with cache_lock:
                        cache[model_id] = model

        pairs = [
            [query, str(candidate.get("snippet") or candidate.get("text") or "")]
            for candidate in candidates
        ]
        raw_scores = model.predict(pairs)
        normalized_scores = [_sigmoid(float(value)) for value in raw_scores]
        return normalized_scores, model_id

    return _provider


def _sigmoid(value: float) -> float:
    clipped = max(-50.0, min(50.0, float(value)))
    return round(1.0 / (1.0 + math.exp(-clipped)), 6)


def _build_qwen3_reranker_provider(*, device: str) -> RerankProvider:
    """Qwen3-Reranker 生成式重排 provider。

    Qwen3-Reranker 是基于 CausalLM 的生成式重排模型，通过判断 "yes"/"no" token
    的 logit 差值作为相关性分数，不能使用 CrossEncoder 接口。

    支持的 model_id 示例：
        - Qwen/Qwen3-Reranker-0.6B
        - 本地路径：models/Qwen3-Reranker-0.6B
    """
    cache: dict[str, Any] = {}
    cache_lock = threading.Lock()
    model_locks: dict[str, threading.Lock] = {}
    runtime_device = str(device or "cpu").strip() or "cpu"

    _SYSTEM_PROMPT = (
        "Judge whether the Document meets the requirements based on the Query and the Candidate Document, "
        "output your judgement, the answer should be \"yes\" or \"no\"."
    )
    _PREFIX = "<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n"
    _SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    _QUERY_DOC_TEMPLATE = "Query: {query}\nCandidate Document: {doc}"

    def _get_model_lock(model_id: str) -> threading.Lock:
        with cache_lock:
            lock = model_locks.get(model_id)
            if lock is None:
                lock = threading.Lock()
                model_locks[model_id] = lock
            return lock

    def _get_model_and_tokenizer(model_id: str) -> tuple[Any, Any, int, int]:
        """加载并缓存模型与 tokenizer，返回 (model, tokenizer, yes_token_id, no_token_id)。"""
        cached = cache.get(model_id)
        if cached is not None:
            return cached
        model_lock = _get_model_lock(model_id)
        with model_lock:
            cached = cache.get(model_id)
            if cached is not None:
                return cached
            try:
                import torch
                from transformers import AutoModelForCausalLM, AutoTokenizer
            except Exception as exc:
                raise RuntimeError(f"transformers/torch 包不可用: {exc}") from exc

            tokenizer = AutoTokenizer.from_pretrained(model_id, padding_side="left")
            model = AutoModelForCausalLM.from_pretrained(
                model_id,
                torch_dtype=torch.float16 if "cuda" in runtime_device else torch.float32,
            )
            model.to(runtime_device)
            model.eval()

            # 获取 "yes" / "no" 对应的 token id（Qwen3 tokenizer 中为单 token）
            yes_token_id = tokenizer.convert_tokens_to_ids("yes")
            no_token_id = tokenizer.convert_tokens_to_ids("no")
            if yes_token_id == tokenizer.unk_token_id or no_token_id == tokenizer.unk_token_id:
                # 部分 tokenizer 版本使用大写
                yes_token_id = tokenizer.convert_tokens_to_ids("Yes")
                no_token_id = tokenizer.convert_tokens_to_ids("No")

            resolved = (model, tokenizer, yes_token_id, no_token_id)
            with cache_lock:
                cache[model_id] = resolved
            return resolved

    def _provider(
        query: str,
        candidates: list[dict[str, Any]],
        route: RerankRouteConfig | None = None,
    ) -> tuple[list[float], str]:
        model_id = str(route.model_id).strip() if route is not None else "Qwen/Qwen3-Reranker-0.6B"
        if not model_id:
            model_id = "Qwen/Qwen3-Reranker-0.6B"

        try:
            import torch
        except Exception as exc:
            raise RuntimeError(f"torch 包不可用: {exc}") from exc

        model, tokenizer, yes_token_id, no_token_id = _get_model_and_tokenizer(model_id)

        prompts: list[str] = []
        for candidate in candidates:
            doc_text = str(candidate.get("snippet") or candidate.get("text") or "")
            query_doc = _QUERY_DOC_TEMPLATE.format(query=query, doc=doc_text)
            full_prompt = (
                _PREFIX.format(system=_SYSTEM_PROMPT)
                + query_doc
                + _SUFFIX
            )
            prompts.append(full_prompt)

        inputs = tokenizer(
            prompts,
            padding=True,
            truncation=True,
            max_length=4096,
            return_tensors="pt",
        ).to(runtime_device)

        with torch.no_grad():
            outputs = model(**inputs)
            last_logits = outputs.logits[:, -1, :]
            yes_logits = last_logits[:, yes_token_id].float()
            no_logits = last_logits[:, no_token_id].float()
            scores_tensor = torch.softmax(
                torch.stack([yes_logits, no_logits], dim=-1), dim=-1
            )[:, 0]

        scores = [round(float(s), 6) for s in scores_tensor.cpu().tolist()]
        return scores, model_id

    return _provider


def _apply_emotion_boost(
    candidates: list[dict[str, Any]],
    scores: list[float],
    emotion_context: str,
    boost_factor: float = 1.3,
) -> list[float]:
    """对与 emotion_context 匹配的候选项加权 boost_factor（M28-O OPT-40）。

    Args:
        candidates: 候选项列表
        scores: 对应的相关性分数列表
        emotion_context: 当前情感上下文（如 "HAPPY"）
        boost_factor: 情感匹配加权系数，默认 1.3x

    Returns:
        加权后的分数列表（归一化到 [0, 1]）
    """
    if not emotion_context or not candidates or not scores:
        return scores

    normalized_emotion = str(emotion_context).strip().upper()
    boosted = list(scores)

    for i, candidate in enumerate(candidates):
        if i >= len(boosted):
            break
        candidate_emotion = str(
            candidate.get("emotion_tag") or
            candidate.get("metadata", {}).get("emotion_tag") or ""
        ).strip().upper()
        if candidate_emotion == normalized_emotion:
            boosted[i] = min(1.0, boosted[i] * boost_factor)

    return boosted
