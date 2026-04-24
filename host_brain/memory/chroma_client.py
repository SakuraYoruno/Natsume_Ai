"""Chroma runtime config, client bindings, and embedding helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import json
import os
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlparse

from host_brain.core.errors import (
    CHROMA_COLLECTION_MISSING,
    CHROMA_ENDPOINT_UNAVAILABLE,
    CHROMA_VECTOR_DIM_MISMATCH,
    CONFIG_REQUIRED_MISSING,
    CONFIG_VALUE_INVALID,
)


EMBEDDING_MODEL_ID = "hash-embed-v1"
SEMANTIC_EMBEDDING_MODEL_ID = "models/Qwen3-Embedding-0.6B"
S_CHROMA_CONFIG = "S_CHROMA_CONFIG"
S_CHROMA_PROBE = "S_CHROMA_PROBE"

CHROMA_STATUS_UP = "UP"
CHROMA_STATUS_DOWN = "DOWN"
DEFAULT_COLLECTION = "episodic"


@dataclass(frozen=True)
class ChromaEntry:
    memory_id: str
    text: str
    embedding: tuple[float, ...]
    metadata: dict[str, Any]
    collection: str


@dataclass(frozen=True)
class ChromaConfigIssue:
    reason_code: str
    detail: str
    field: str | None = None


@dataclass(frozen=True)
class ChromaRuntimeConfig:
    enabled: bool
    endpoint: str
    tenant: str
    database: str
    collection_profiles: dict[str, int]
    default_collection: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "tenant": self.tenant,
            "database": self.database,
            "collection_profiles": dict(self.collection_profiles),
            "default_collection": self.default_collection,
        }


@dataclass(frozen=True)
class ChromaConfigLoadResult:
    success: bool
    stage: str
    reason_code: str | None
    config: ChromaRuntimeConfig | None
    issues: tuple[ChromaConfigIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stage": self.stage,
            "reason_code": self.reason_code,
            "config": (self.config.as_dict() if self.config is not None else None),
            "issues": [
                {
                    "reason_code": issue.reason_code,
                    "detail": issue.detail,
                    "field": issue.field,
                }
                for issue in self.issues
            ],
        }


@runtime_checkable
class ChromaClientProtocol(Protocol):
    @property
    def collection_profiles(self) -> Mapping[str, int]:
        ...

    @property
    def default_collection(self) -> str:
        ...

    def upsert(
        self,
        *,
        memory_id: str,
        text: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        collection: str | None = None,
    ) -> None:
        ...

    def delete(self, memory_id: str, *, collection: str | None = None) -> None:
        ...

    def get(self, memory_id: str, *, collection: str | None = None) -> ChromaEntry | None:
        ...

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int = 5,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def validate_collection(self, collection: str, *, vector_dim: int | None = None) -> tuple[bool, str | None]:
        ...


class PersistentChromaClient:

    def __init__(
        self,
        *,
        endpoint: str,
        tenant: str,
        database: str,
        collection_profiles: Mapping[str, int] | None = None,
        default_collection: str = DEFAULT_COLLECTION,
    ) -> None:
        parsed = urlparse(str(endpoint).strip() or "http://127.0.0.1:8000")
        self._host = parsed.hostname or "127.0.0.1"
        self._port = int(parsed.port or 8000)
        self._ssl = parsed.scheme.lower() == "https"
        self._tenant = str(tenant or "default_tenant").strip() or "default_tenant"
        self._database = str(database or "default_database").strip() or "default_database"
        self._default_collection = str(default_collection or DEFAULT_COLLECTION)
        self._collection_profiles: dict[str, int] = dict(collection_profiles or {self._default_collection: 16})
        try:
            import chromadb
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(f"chromadb package unavailable: {exc}") from exc
        try:
            self._client = chromadb.HttpClient(
                host=self._host,
                port=self._port,
                ssl=self._ssl,
                tenant=self._tenant,
                database=self._database,
            )
        except Exception:
            self._client = chromadb.HttpClient(
                host=self._host,
                port=self._port,
                ssl=self._ssl,
            )

    @classmethod
    def from_runtime_config(cls, config: ChromaRuntimeConfig) -> "PersistentChromaClient":
        return cls(
            endpoint=config.endpoint,
            tenant=config.tenant,
            database=config.database,
            collection_profiles=config.collection_profiles,
            default_collection=config.default_collection,
        )

    @property
    def collection_profiles(self) -> Mapping[str, int]:
        return dict(self._collection_profiles)

    @property
    def default_collection(self) -> str:
        return self._default_collection

    def upsert(
        self,
        *,
        memory_id: str,
        text: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
        collection: str | None = None,
    ) -> None:
        collection_name = str(collection or self._default_collection)
        self._validate_collection_embedding(collection_name, embedding)
        coll = self._client.get_or_create_collection(name=collection_name)
        coll.upsert(
            ids=[str(memory_id)],
            documents=[str(text)],
            embeddings=[list(float(v) for v in embedding)],
            metadatas=[_sanitize_chroma_metadata(metadata or {})],
        )

    def delete(self, memory_id: str, *, collection: str | None = None) -> None:
        collection_name = str(collection or self._default_collection)
        coll = self._client.get_or_create_collection(name=collection_name)
        coll.delete(ids=[str(memory_id)])

    def get(self, memory_id: str, *, collection: str | None = None) -> ChromaEntry | None:
        collection_name = str(collection or self._default_collection)
        coll = self._client.get_or_create_collection(name=collection_name)
        result = coll.get(ids=[str(memory_id)], include=["documents", "embeddings", "metadatas"])
        ids_raw = result.get("ids")
        ids = list(ids_raw) if ids_raw is not None else []
        if not ids:
            return None
        documents_raw = result.get("documents")
        embeddings_raw = result.get("embeddings")
        metadatas_raw = result.get("metadatas")
        documents = list(documents_raw) if documents_raw is not None else []
        embeddings = list(embeddings_raw) if embeddings_raw is not None else []
        metadatas = list(metadatas_raw) if metadatas_raw is not None else []
        text = str(documents[0]) if documents else ""
        if embeddings and embeddings[0] is not None:
            raw_embedding = list(embeddings[0])
        else:
            raw_embedding = []
        metadata = dict(metadatas[0] or {}) if metadatas and isinstance(metadatas[0], Mapping) else {}
        return ChromaEntry(
            memory_id=str(ids[0]),
            text=text,
            embedding=tuple(float(v) for v in raw_embedding),
            metadata=metadata,
            collection=collection_name,
        )

    def search(
        self,
        *,
        query_embedding: list[float],
        top_k: int = 5,
        collection: str | None = None,
    ) -> list[dict[str, Any]]:
        collection_name = str(collection or self._default_collection)
        self._validate_collection_embedding(collection_name, query_embedding)
        coll = self._client.get_or_create_collection(name=collection_name)
        result = coll.query(
            query_embeddings=[list(float(v) for v in query_embedding)],
            n_results=max(1, int(top_k)),
            include=["documents", "metadatas", "distances"],
        )

        ids = ((result.get("ids") or [[]])[0])
        docs = ((result.get("documents") or [[]])[0])
        metas = ((result.get("metadatas") or [[]])[0])
        distances = ((result.get("distances") or [[]])[0])

        rows: list[dict[str, Any]] = []
        for idx, row_id in enumerate(ids):
            text = str(docs[idx]) if idx < len(docs) and docs[idx] is not None else ""
            metadata = dict(metas[idx] or {}) if idx < len(metas) and isinstance(metas[idx], Mapping) else {}
            distance = 1.0
            if idx < len(distances) and distances[idx] is not None:
                try:
                    distance = float(distances[idx])
                except (TypeError, ValueError):
                    distance = 1.0
            rows.append(
                {
                    "memory_id": str(row_id),
                    "text": text,
                    "score": _distance_to_score(distance),
                    "metadata": metadata,
                    "collection": collection_name,
                }
            )
        return rows

    def validate_collection(self, collection: str, *, vector_dim: int | None = None) -> tuple[bool, str | None]:
        collection_name = str(collection or "").strip()
        if not collection_name:
            return False, CHROMA_COLLECTION_MISSING
        expected_dim = self._collection_profiles.get(collection_name)
        if expected_dim is None:
            return False, CHROMA_COLLECTION_MISSING
        if vector_dim is not None and int(vector_dim) != int(expected_dim):
            return False, CHROMA_VECTOR_DIM_MISMATCH
        return True, None

    def _validate_collection_embedding(self, collection: str, embedding: list[float] | tuple[float, ...]) -> None:
        ok, reason = self.validate_collection(collection, vector_dim=len(embedding))
        if ok:
            return
        if reason == CHROMA_COLLECTION_MISSING:
            raise ValueError(CHROMA_COLLECTION_MISSING)
        raise ValueError(CHROMA_VECTOR_DIM_MISMATCH)


def build_runtime_chroma_client(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> PersistentChromaClient:
    cfg_result = load_chroma_runtime_config(env=env, local_overrides=local_overrides)
    if not cfg_result.success or cfg_result.config is None or not cfg_result.config.enabled:
        raise RuntimeError(f"chroma runtime config invalid: {cfg_result.as_dict()}")
    return PersistentChromaClient.from_runtime_config(cfg_result.config)


def resolve_collection_vector_dim(
    client: ChromaClientProtocol,
    *,
    fallback: int = 16,
) -> int:
    profiles = dict(getattr(client, "collection_profiles", {}) or {})
    default_collection = str(getattr(client, "default_collection", DEFAULT_COLLECTION) or DEFAULT_COLLECTION)
    raw_dim = profiles.get(default_collection)
    if raw_dim is None and profiles:
        raw_dim = next(iter(profiles.values()))
    try:
        return max(1, int(raw_dim))
    except (TypeError, ValueError):
        return max(1, int(fallback))

def hash_embed_text(text: str, *, dim: int = 16) -> tuple[list[float], str]:
    normalized = str(text).strip()
    fallback_dim = int(dim) if dim is not None else 16
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    values = []
    for idx in range(fallback_dim):
        byte_value = digest[idx % len(digest)]
        values.append(round(byte_value / 255.0, 6))
    return values, EMBEDDING_MODEL_ID


def embed_text(text: str, *, dim: int | None = None) -> tuple[list[float], str]:
    """将文本转换为语义嵌入向量。

    优先使用 sentence-transformers 真实语义嵌入模型（默认使用 Qwen3-Embedding-0.6B）。
    若依赖库不可用，则降级为 SHA-256 哈希伪嵌入（仅用于测试/离线场景）。

    Args:
        text: 待嵌入的文本。
        dim: 仅在降级模式下生效，指定哈希嵌入维度（默认 16）。

    Returns:
        (embedding_values, model_id) 元组。
    """
    normalized = str(text).strip()
    # 尝试使用真实语义嵌入模型
    try:
        model = _get_sentence_transformer_model()
        embedding = model.encode(normalized, normalize_embeddings=True)
        values = [round(float(v), 6) for v in embedding]
        if dim is not None:
            requested_dim = max(1, int(dim))
            if len(values) != requested_dim:
                # Callers like EpisodicStore bind vectors to a governed collection dim.
                # If the semantic model does not match that dim, fall back to the
                # deterministic hash embedding instead of emitting a mismatched vector.
                return hash_embed_text(normalized, dim=requested_dim)
        return values, _resolve_semantic_embedding_model_id()
    except Exception:
        pass
    return hash_embed_text(normalized, dim=(int(dim) if dim is not None else 16))

    # 降级：SHA-256 哈希伪嵌入（语义无关，仅用于测试/离线场景）
    fallback_dim = int(dim) if dim is not None else 16
    digest = hashlib.sha256(normalized.encode("utf-8")).digest()
    values = []
    for idx in range(fallback_dim):
        byte_value = digest[idx % len(digest)]
        values.append(round(byte_value / 255.0, 6))
    return values, EMBEDDING_MODEL_ID


# 模型单例缓存（避免每次调用重复加载）
_sentence_transformer_models: dict[str, Any] = {}


def _resolve_semantic_embedding_model_id() -> str:
    preferred = str(os.environ.get("RETR_MODEL_SUITE_EMBED_MODEL_ID") or "").strip()
    if preferred:
        return preferred
    preferred = str(os.environ.get("RETR_EMBED_MODEL_ID") or "").strip()
    if preferred and preferred != EMBEDDING_MODEL_ID:
        return preferred
    return SEMANTIC_EMBEDDING_MODEL_ID


def _get_sentence_transformer_model() -> Any:
    """获取 SentenceTransformer 模型单例（懒加载 + 缓存）。"""
    model_id = _resolve_semantic_embedding_model_id()
    cached = _sentence_transformer_models.get(model_id)
    if cached is not None:
        return cached
    from sentence_transformers import SentenceTransformer  # type: ignore[import-untyped]
    model = SentenceTransformer(model_id)
    _sentence_transformer_models[model_id] = model
    return model


def load_chroma_runtime_config(
    *,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
) -> ChromaConfigLoadResult:
    merged: dict[str, Any]
    if env is None or env is os.environ:
        merged = _load_project_dotenv_values()
        merged.update(dict(os.environ))
    else:
        merged = dict(env)
    if local_overrides:
        merged.update(dict(local_overrides))

    issues: list[ChromaConfigIssue] = []
    enabled = _parse_bool(merged.get("CHROMA_ENABLED"), default=True)
    endpoint = _resolve_chroma_endpoint(merged)
    tenant = str(merged.get("CHROMA_TENANT") or "default_tenant").strip()
    database = str(merged.get("CHROMA_DATABASE") or "default_database").strip()
    default_collection = str(merged.get("CHROMA_DEFAULT_COLLECTION") or DEFAULT_COLLECTION).strip()
    profiles_text = str(
        merged.get("CHROMA_COLLECTION_PROFILES_JSON")
        or json.dumps({DEFAULT_COLLECTION: 16}, ensure_ascii=False)
    ).strip()

    profiles = _parse_collection_profiles(
        profiles_text,
        field_name="CHROMA_COLLECTION_PROFILES_JSON",
        issues=issues,
    )

    if enabled and not endpoint:
        issues.append(
            ChromaConfigIssue(
                reason_code=CONFIG_REQUIRED_MISSING,
                detail="CHROMA_ENDPOINT required when CHROMA_ENABLED=true",
                field="CHROMA_ENDPOINT",
            )
        )
    if enabled and default_collection not in profiles:
        issues.append(
            ChromaConfigIssue(
                reason_code=CHROMA_COLLECTION_MISSING,
                detail=f"default collection not found in profiles: {default_collection}",
                field="CHROMA_DEFAULT_COLLECTION",
            )
        )

    if issues:
        return ChromaConfigLoadResult(
            success=False,
            stage=S_CHROMA_CONFIG,
            reason_code=issues[0].reason_code,
            config=None,
            issues=tuple(issues),
        )

    config = ChromaRuntimeConfig(
        enabled=bool(enabled),
        endpoint=endpoint,
        tenant=tenant,
        database=database,
        collection_profiles=profiles,
        default_collection=default_collection,
    )
    return ChromaConfigLoadResult(
        success=True,
        stage=S_CHROMA_CONFIG,
        reason_code=None,
        config=config,
        issues=(),
    )


def build_chroma_probe_report(
    *,
    config: ChromaRuntimeConfig,
    request_id: str | None,
    probe: Mapping[str, Any] | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    probe_data = dict(probe or {})
    if not config.enabled:
        return {
            "request_id": request_id,
            "stage": S_CHROMA_PROBE,
            "status": CHROMA_STATUS_DOWN,
            "reason_code": CHROMA_ENDPOINT_UNAVAILABLE,
            "failure_class": "endpoint_down",
            "detail": "chroma disabled by config",
            "config": config.as_dict(),
            "ts_ms": int(ts_ms if ts_ms is not None else 0),
        }

    reachable = bool(probe_data.get("reachable", True))
    if not reachable:
        return {
            "request_id": request_id,
            "stage": S_CHROMA_PROBE,
            "status": CHROMA_STATUS_DOWN,
            "reason_code": CHROMA_ENDPOINT_UNAVAILABLE,
            "failure_class": "endpoint_down",
            "detail": str(probe_data.get("detail") or "chroma endpoint unavailable"),
            "config": config.as_dict(),
            "ts_ms": int(ts_ms if ts_ms is not None else 0),
        }

    collections = probe_data.get("collections")
    if isinstance(collections, Mapping):
        for name, expected_dim in sorted(config.collection_profiles.items()):
            observed = collections.get(name)
            if observed is None:
                return {
                    "request_id": request_id,
                    "stage": S_CHROMA_PROBE,
                    "status": CHROMA_STATUS_DOWN,
                    "reason_code": CHROMA_COLLECTION_MISSING,
                    "failure_class": "collection_missing",
                    "detail": f"collection missing: {name}",
                    "config": config.as_dict(),
                    "ts_ms": int(ts_ms if ts_ms is not None else 0),
                }
            observed_dim = int(observed.get("vector_dim", -1)) if isinstance(observed, Mapping) else -1
            if observed_dim != int(expected_dim):
                return {
                    "request_id": request_id,
                    "stage": S_CHROMA_PROBE,
                    "status": CHROMA_STATUS_DOWN,
                    "reason_code": CHROMA_VECTOR_DIM_MISMATCH,
                    "failure_class": "vector_dim_mismatch",
                    "detail": (
                        f"vector dim mismatch for {name}: expected={int(expected_dim)}, got={int(observed_dim)}"
                    ),
                    "config": config.as_dict(),
                    "ts_ms": int(ts_ms if ts_ms is not None else 0),
                }

    return {
        "request_id": request_id,
        "stage": S_CHROMA_PROBE,
        "status": CHROMA_STATUS_UP,
        "reason_code": None,
        "failure_class": None,
        "detail": str(probe_data.get("detail") or "chroma probe ok"),
        "config": config.as_dict(),
        "ts_ms": int(ts_ms if ts_ms is not None else 0),
    }


def build_chroma_dependency_status(probe_report: Mapping[str, Any]) -> dict[str, Any]:
    status = str(probe_report.get("status") or "").strip().upper()
    if status == CHROMA_STATUS_UP:
        return {
            "available": True,
            "detail": "chroma dependency healthy",
        }
    return {
        "available": False,
        "unavailable_kind": "dependency",
        "detail": (
            f"{probe_report.get('reason_code')}: "
            f"{str(probe_report.get('detail') or 'chroma probe failed')}"
        ),
    }


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _parse_collection_profiles(
    raw_text: str,
    *,
    field_name: str,
    issues: list[ChromaConfigIssue],
) -> dict[str, int]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        issues.append(
            ChromaConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be JSON object mapping collection->vector_dim",
                field=field_name,
            )
        )
        return {DEFAULT_COLLECTION: 16}
    if not isinstance(data, dict):
        issues.append(
            ChromaConfigIssue(
                reason_code=CONFIG_VALUE_INVALID,
                detail=f"{field_name} must be JSON object mapping collection->vector_dim",
                field=field_name,
            )
        )
        return {DEFAULT_COLLECTION: 16}

    profiles: dict[str, int] = {}
    for raw_name, raw_dim in data.items():
        name = str(raw_name or "").strip()
        if not name:
            issues.append(
                ChromaConfigIssue(
                    reason_code=CONFIG_VALUE_INVALID,
                    detail=f"{field_name} contains empty collection name",
                    field=field_name,
                )
            )
            continue
        try:
            dim = int(str(raw_dim).strip())
        except (TypeError, ValueError):
            issues.append(
                ChromaConfigIssue(
                    reason_code=CONFIG_VALUE_INVALID,
                    detail=f"{field_name} vector dim for {name} must be integer",
                    field=field_name,
                )
            )
            continue
        if dim <= 0:
            issues.append(
                ChromaConfigIssue(
                    reason_code=CONFIG_VALUE_INVALID,
                    detail=f"{field_name} vector dim for {name} must be > 0",
                    field=field_name,
                )
            )
            continue
        profiles[name] = dim
    if not profiles:
        profiles[DEFAULT_COLLECTION] = 16
    return profiles


def _parse_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _load_project_dotenv_values() -> dict[str, str]:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def _resolve_chroma_endpoint(env: Mapping[str, Any]) -> str:
    explicit = str(env.get("CHROMA_ENDPOINT") or "").strip()
    if explicit:
        return explicit
    host = str(env.get("CHROMA_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    raw_port = env.get("CHROMA_PORT") or env.get("NATSUME_CHROMA_PORT") or 8000
    try:
        port = int(str(raw_port).strip())
    except (TypeError, ValueError):
        port = 8000
    return f"http://{host}:{port}"


def _row_key(collection: str, memory_id: str) -> str:
    return f"{str(collection).strip()}::{str(memory_id).strip()}"


def _distance_to_score(distance: float) -> float:
    value = max(0.0, float(distance))
    return round(max(0.0, min(1.0, 1.0 - value)), 6)


def _sanitize_chroma_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, raw_value in dict(metadata or {}).items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        if isinstance(raw_value, (str, int, float, bool)) or raw_value is None:
            sanitized[normalized_key] = raw_value
            continue
        if isinstance(raw_value, tuple):
            raw_value = list(raw_value)
        if isinstance(raw_value, (list, dict)):
            sanitized[normalized_key] = json.dumps(raw_value, ensure_ascii=False, sort_keys=True)
            continue
        sanitized[normalized_key] = str(raw_value)
    return sanitized
