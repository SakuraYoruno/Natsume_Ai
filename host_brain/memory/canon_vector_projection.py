"""Project canon SSOT files into Chroma vector index.

Design:
- Style/World canon remain file-based SSOT.
- This module builds vector-search projection documents from SSOT files.
- Projection writes into a dedicated Chroma collection (default: ``canon``).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from host_brain.core.key_profiles import resolve_secret_from_profiles
from host_brain.memory.chroma_client import load_chroma_runtime_config
from host_brain.retrieval.embedding import (
    EmbeddingServiceContract,
    build_embedding_provider_registry,
    load_embedding_runtime_config,
    resolve_embedding_route,
)


DEFAULT_STYLE_ROOT = "host_brain/canon/style"
DEFAULT_WORLD_ROOT = "host_brain/canon/world"
DEFAULT_CANON_COLLECTION = "canon"
DEFAULT_CHROMA_BACKEND = "http"
DEFAULT_CHROMA_PATH = "runtime/chroma/data"
DEFAULT_KEY_PROFILES = Path("configs/secrets/key_profiles.runtime.json")
DEFAULT_KEY_PROFILES_FALLBACK = Path("configs/secrets/key_profiles.example.json")
DEFAULT_SECRET_STORE = Path("configs/secrets/secret_store.runtime.local.json")
DEFAULT_SECRET_STORE_FALLBACK = Path("configs/secrets/secret_store.runtime.example.json")
DEFAULT_HOST_NODE_ID = "natsu-node-01"


@dataclass(frozen=True)
class CanonProjectionDocument:
    vector_id: str
    text: str
    metadata: dict[str, Any]


def load_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return values
    for raw in env_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def collect_canon_projection_documents(
    *,
    style_root: str | Path = DEFAULT_STYLE_ROOT,
    world_root: str | Path = DEFAULT_WORLD_ROOT,
    include_style: bool = True,
    include_world: bool = True,
) -> tuple[list[CanonProjectionDocument], list[dict[str, Any]]]:
    docs: list[CanonProjectionDocument] = []
    issues: list[dict[str, Any]] = []

    if include_style:
        style_docs, style_issues = _collect_style_docs(Path(style_root))
        docs.extend(style_docs)
        issues.extend(style_issues)

    if include_world:
        world_docs, world_issues = _collect_world_docs(Path(world_root))
        docs.extend(world_docs)
        issues.extend(world_issues)

    docs.sort(key=lambda row: row.vector_id)
    return docs, issues


def sync_canon_vector_index(
    *,
    style_root: str | Path = DEFAULT_STYLE_ROOT,
    world_root: str | Path = DEFAULT_WORLD_ROOT,
    collection: str = DEFAULT_CANON_COLLECTION,
    backend: str = DEFAULT_CHROMA_BACKEND,
    chroma_path: str | Path = DEFAULT_CHROMA_PATH,
    collection_dim: int | None = None,
    include_style: bool = True,
    include_world: bool = True,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    node_id: str | None = None,
    key_profiles: Sequence[Mapping[str, Any]] | None = None,
    secret_store: Mapping[str, Any] | None = None,
    current_epoch: int | None = None,
    chroma_client: Any | None = None,
) -> dict[str, Any]:
    merged_env: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged_env.update(dict(local_overrides))

    cfg_result = load_chroma_runtime_config(env=merged_env)
    if not cfg_result.success or cfg_result.config is None:
        raise RuntimeError(f"chroma runtime config invalid: {cfg_result.as_dict()}")
    cfg = cfg_result.config

    embed_cfg_result = load_embedding_runtime_config(env=merged_env)
    if not embed_cfg_result.success or embed_cfg_result.config is None:
        raise RuntimeError(f"embedding runtime config invalid: {embed_cfg_result.as_dict()}")
    embed_cfg = embed_cfg_result.config
    resolved_node_id = _resolve_runtime_node_id(env=merged_env, node_id=node_id)
    resolved_key_profiles, resolved_secret_store, resolved_epoch = _resolve_key_materials(
        key_profiles=key_profiles,
        secret_store=secret_store,
        current_epoch=current_epoch,
    )
    resolved_openai_key = _resolve_openai_api_key(
        env=merged_env,
        node_id=resolved_node_id,
        key_profiles=resolved_key_profiles,
        secret_store=resolved_secret_store,
        current_epoch=resolved_epoch,
    )
    provider_registry = build_embedding_provider_registry(
        env=merged_env,
        openai_api_key=resolved_openai_key,
    )
    embedding_service = EmbeddingServiceContract(
        runtime_config=embed_cfg,
        provider_registry=provider_registry,
        node_id=resolved_node_id,
        available=True,
    )
    active_route = resolve_embedding_route(embed_cfg, node_id=resolved_node_id)

    target_collection = str(collection or DEFAULT_CANON_COLLECTION).strip() or DEFAULT_CANON_COLLECTION
    dim = int(collection_dim) if collection_dim is not None else _resolve_collection_dim(cfg, target_collection)
    if dim <= 0:
        raise ValueError(f"collection_dim must be > 0, got {dim}")
    if int(active_route.dim) != int(dim):
        raise ValueError(
            "embedding/chroma dim mismatch: "
            f"route_dim={int(active_route.dim)} collection_dim={int(dim)} "
            f"(route_id={active_route.route_id}, provider={active_route.provider})"
        )

    documents, issues = collect_canon_projection_documents(
        style_root=style_root,
        world_root=world_root,
        include_style=include_style,
        include_world=include_world,
    )

    client = chroma_client or _build_chroma_client(
        backend=backend,
        endpoint=cfg.endpoint,
        chroma_path=chroma_path,
    )
    collection_obj = client.get_or_create_collection(name=target_collection)

    ids: list[str] = []
    rows: list[str] = []
    metadatas: list[dict[str, Any]] = []
    vectors: list[list[float]] = []
    style_count = 0
    world_count = 0
    for row in documents:
        result = embedding_service.embed(
            request_id=f"canon_sync::{row.vector_id}",
            text=row.text,
        )
        if not result.success:
            raise RuntimeError(
                "canon vector embedding failed: "
                f"vector_id={row.vector_id} reason={result.reason_code} detail={result.detail}"
            )
        output = dict(result.output or {})
        embedding = [float(v) for v in list(output.get("embedding_vector") or [])]
        model_id = str(output.get("embedding_model_id") or active_route.model_id)
        provider = str(output.get("provider") or active_route.provider)
        route_id = str(output.get("route_id") or active_route.route_id)
        ids.append(row.vector_id)
        rows.append(row.text)
        metadata = dict(row.metadata)
        metadata["embedding_model_id"] = model_id
        metadata["embedding_provider"] = provider
        metadata["embedding_route_id"] = route_id
        metadata["vector_dim"] = dim
        metadatas.append(metadata)
        vectors.append(embedding)
        kind = str(metadata.get("canon_kind") or "")
        if kind == "style":
            style_count += 1
        elif kind == "world":
            world_count += 1

    if ids:
        collection_obj.upsert(
            ids=ids,
            documents=rows,
            metadatas=metadatas,
            embeddings=vectors,
        )

    collection_count = None
    if hasattr(collection_obj, "count"):
        try:
            collection_count = int(collection_obj.count())
        except Exception:
            collection_count = None

    return {
        "success": True,
        "collection": target_collection,
        "vector_dim": dim,
        "backend": str(backend),
        "embedding_provider": active_route.provider,
        "embedding_model_id": active_route.model_id,
        "embedding_route_id": active_route.route_id,
        "embedding_node_id": resolved_node_id,
        "style_docs_synced": style_count,
        "world_docs_synced": world_count,
        "docs_synced": len(ids),
        "issues_count": len(issues),
        "issues": issues,
        "collection_count": collection_count,
    }


def _collect_style_docs(style_root: Path) -> tuple[list[CanonProjectionDocument], list[dict[str, Any]]]:
    docs: list[CanonProjectionDocument] = []
    issues: list[dict[str, Any]] = []
    if not style_root.exists():
        issues.append(
            {
                "code": "STYLE_ROOT_MISSING",
                "source_path": str(style_root),
                "detail": "style root path does not exist",
            }
        )
        return docs, issues

    for path in sorted(style_root.glob("*.json")):
        payload, issue = _read_json_file(path)
        if issue is not None:
            issues.append(issue)
            continue
        canon_id = str(payload.get("canon_id") or "").strip()
        content = str(payload.get("content") or "").strip()
        if not canon_id or not content:
            issues.append(
                {
                    "code": "STYLE_SCHEMA_INVALID",
                    "source_path": str(path),
                    "detail": "canon_id/content is required",
                }
            )
            continue

        version = int(payload.get("version") or 0)
        hash_value = str(payload.get("hash") or "")
        tags = payload.get("tags")
        tag_list = [str(v) for v in tags] if isinstance(tags, list) else []
        tags_csv = ",".join(tag_list)
        tone_only = bool(payload.get("tone_only", True))
        unsafe_as_fact = bool(payload.get("unsafe_as_fact", True))
        source_ref = f"canon://style/{canon_id}"
        evidence_ref = f"evd://canon/style/{canon_id}@v{version}"
        vector_id = f"canon_style::{canon_id}"

        text = (
            "[STYLE_CANON]\n"
            f"canon_id: {canon_id}\n"
            f"version: {version}\n"
            f"tone_only: {str(tone_only).lower()}\n"
            f"unsafe_as_fact: {str(unsafe_as_fact).lower()}\n"
            "style_instruction:\n"
            f"{content}"
        )
        metadata = {
            "canon_kind": "style",
            "canon_id": canon_id,
            "version": version,
            "hash": hash_value,
            "tags": tags_csv,
            "tags_count": len(tag_list),
            "tone_only": tone_only,
            "unsafe_as_fact": unsafe_as_fact,
            "source_ref": source_ref,
            "evidence_ref": evidence_ref,
            "source_path": str(path),
        }
        docs.append(CanonProjectionDocument(vector_id=vector_id, text=text, metadata=metadata))
    return docs, issues


def _collect_world_docs(world_root: Path) -> tuple[list[CanonProjectionDocument], list[dict[str, Any]]]:
    docs: list[CanonProjectionDocument] = []
    issues: list[dict[str, Any]] = []
    if not world_root.exists():
        issues.append(
            {
                "code": "WORLD_ROOT_MISSING",
                "source_path": str(world_root),
                "detail": "world root path does not exist",
            }
        )
        return docs, issues

    for path in sorted(world_root.glob("*.json")):
        payload, issue = _read_json_file(path)
        if issue is not None:
            issues.append(issue)
            continue
        doc_id = str(payload.get("doc_id") or "").strip()
        title = str(payload.get("title") or "").strip()
        body = str(payload.get("body") or "").strip()
        if not doc_id or not body:
            issues.append(
                {
                    "code": "WORLD_SCHEMA_INVALID",
                    "source_path": str(path),
                    "detail": "doc_id/body is required",
                }
            )
            continue

        version = int(payload.get("version") or 0)
        hash_value = str(payload.get("hash") or "")
        status = str(payload.get("status") or "active").strip() or "active"
        tags = payload.get("tags")
        tag_list = [str(v) for v in tags] if isinstance(tags, list) else []
        tags_csv = ",".join(tag_list)
        source_ref = f"canon://world/{doc_id}"
        evidence_ref = f"evd://canon/world/{doc_id}@v{version}"
        vector_id = f"canon_world::{doc_id}"

        text = (
            "[WORLD_CANON]\n"
            f"doc_id: {doc_id}\n"
            f"title: {title}\n"
            f"version: {version}\n"
            f"status: {status}\n"
            "body:\n"
            f"{body}"
        )
        metadata = {
            "canon_kind": "world",
            "doc_id": doc_id,
            "title": title,
            "version": version,
            "hash": hash_value,
            "status": status,
            "tags": tags_csv,
            "tags_count": len(tag_list),
            "unsafe_as_fact": True,
            "source_ref": source_ref,
            "evidence_ref": evidence_ref,
            "source_path": str(path),
        }
        docs.append(CanonProjectionDocument(vector_id=vector_id, text=text, metadata=metadata))
    return docs, issues


def _read_json_file(path: Path) -> tuple[dict[str, Any], dict[str, Any] | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return {}, {
            "code": "CANON_JSON_INVALID",
            "source_path": str(path),
            "detail": str(exc),
        }
    except OSError as exc:
        return {}, {
            "code": "CANON_READ_FAILED",
            "source_path": str(path),
            "detail": str(exc),
        }
    if not isinstance(payload, dict):
        return {}, {
            "code": "CANON_JSON_NOT_OBJECT",
            "source_path": str(path),
            "detail": "json payload must be object",
        }
    return payload, None


def _resolve_collection_dim(config: Any, collection: str) -> int:
    profiles = dict(getattr(config, "collection_profiles", {}) or {})
    target = str(collection).strip()
    if target in profiles:
        return int(profiles[target])

    default_collection = str(getattr(config, "default_collection", "") or "").strip()
    if default_collection and default_collection in profiles:
        return int(profiles[default_collection])

    if profiles:
        first_key = sorted(profiles.keys())[0]
        return int(profiles[first_key])
    return 16


def _build_chroma_client(*, backend: str, endpoint: str, chroma_path: str | Path) -> Any:
    mode = str(backend or DEFAULT_CHROMA_BACKEND).strip().lower()
    if mode not in {"http", "persistent"}:
        raise ValueError(f"unsupported chroma backend: {backend!r}")

    try:
        import chromadb
    except Exception as exc:
        raise RuntimeError(f"chromadb package unavailable: {exc}") from exc

    if mode == "persistent":
        return chromadb.PersistentClient(path=str(Path(chroma_path).resolve()))

    parsed = urlparse(str(endpoint or "http://127.0.0.1:8000").strip())
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = parsed.scheme.lower() == "https"
    return chromadb.HttpClient(host=host, port=port, ssl=ssl)


def _resolve_runtime_node_id(*, env: Mapping[str, Any], node_id: str | None) -> str:
    explicit = str(node_id or "").strip()
    if explicit:
        return explicit
    return str(
        env.get("HOST_RETR_NODE_ID")
        or env.get("Natsume_HOST_NODE_ID")
        or DEFAULT_HOST_NODE_ID
    ).strip() or DEFAULT_HOST_NODE_ID


def _resolve_key_materials(
    *,
    key_profiles: Sequence[Mapping[str, Any]] | None,
    secret_store: Mapping[str, Any] | None,
    current_epoch: int | None,
) -> tuple[list[Mapping[str, Any]], dict[str, Any], int]:
    resolved_profiles: list[Mapping[str, Any]] = list(key_profiles or [])
    resolved_store: dict[str, Any] = dict(secret_store or {})
    resolved_epoch = int(current_epoch) if current_epoch is not None else 0

    if not resolved_profiles:
        payload = _load_json_file(_pick_existing_path(DEFAULT_KEY_PROFILES, DEFAULT_KEY_PROFILES_FALLBACK))
        raw_profiles = payload.get("profiles")
        if isinstance(raw_profiles, list):
            resolved_profiles = [row for row in raw_profiles if isinstance(row, Mapping)]
        if resolved_epoch <= 0:
            try:
                resolved_epoch = int(str(payload.get("current_epoch") or "0").strip())
            except (TypeError, ValueError):
                resolved_epoch = 0
    if not resolved_store:
        resolved_store = _load_json_file(_pick_existing_path(DEFAULT_SECRET_STORE, DEFAULT_SECRET_STORE_FALLBACK))
    return resolved_profiles, resolved_store, max(0, int(resolved_epoch))


def _resolve_openai_api_key(
    *,
    env: Mapping[str, Any],
    node_id: str,
    key_profiles: Sequence[Mapping[str, Any]],
    secret_store: Mapping[str, Any],
    current_epoch: int,
) -> str:
    direct = str(env.get("OPENAI_API_KEY") or "").strip()
    if direct:
        return direct

    secret_ref = str(env.get("OPENAI_API_KEY_REF") or "").strip()
    if not secret_ref:
        return ""

    resolved = resolve_secret_from_profiles(
        provider="openai",
        node_id=str(node_id),
        scope="host.llm",
        key_profiles=key_profiles,
        secret_store=secret_store,
        current_epoch=max(0, int(current_epoch)),
    )
    if resolved.success and resolved.secret_value:
        return str(resolved.secret_value).strip()
    return str(secret_store.get(secret_ref) or "").strip()


def _pick_existing_path(primary: Path, fallback: Path) -> Path:
    if primary.exists() and primary.is_file():
        return primary
    return fallback


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            payload = json.loads(path.read_text(encoding=encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}
