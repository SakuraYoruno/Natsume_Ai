"""Project distilled persona utterance lines into a Chroma vector collection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from host_brain.memory.chroma_client import load_chroma_runtime_config
from host_brain.retrieval.embedding import (
    EmbeddingServiceContract,
    build_embedding_provider_registry,
    load_embedding_runtime_config,
    resolve_embedding_route,
)

DEFAULT_STYLE_ARTIFACT_ROOT = "runtime/style_canon"
DEFAULT_PERSONA_UTTERANCE_COLLECTION = "persona_utterance"
DEFAULT_CHROMA_BACKEND = "http"
DEFAULT_CHROMA_PATH = "runtime/chroma/data"
DEFAULT_NODE_ID = "natsu-node-01"


@dataclass(frozen=True)
class PersonaUtteranceProjectionDocument:
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


def collect_persona_utterance_projection_documents(
    *,
    style_artifact_root: str | Path = DEFAULT_STYLE_ARTIFACT_ROOT,
    include_ja: bool = True,
    include_cn: bool = True,
) -> tuple[list[PersonaUtteranceProjectionDocument], list[dict[str, Any]]]:
    root = Path(style_artifact_root)
    docs: list[PersonaUtteranceProjectionDocument] = []
    issues: list[dict[str, Any]] = []
    if not root.exists() or not root.is_dir():
        return docs, [
            {
                "code": "STYLE_ARTIFACT_ROOT_MISSING",
                "detail": "style artifact root does not exist",
                "source_path": str(root),
            }
        ]

    files = sorted(root.glob("*_speaker_lines.jsonl"))
    if not files:
        return docs, [
            {
                "code": "STYLE_ARTIFACT_FILES_MISSING",
                "detail": "no *_speaker_lines.jsonl files found",
                "source_path": str(root),
            }
        ]

    for file_path in files:
        stem = file_path.stem.lower()
        lang_from_file = "ja" if "_ja_" in stem else ("cn" if "_cn_" in stem else "")
        if lang_from_file == "ja" and not include_ja:
            continue
        if lang_from_file == "cn" and not include_cn:
            continue

        canon_id = file_path.stem.replace("_speaker_lines", "").strip() or "unknown_canon"
        try:
            rows = file_path.read_text(encoding="utf-8-sig").splitlines()
        except OSError as exc:
            issues.append(
                {
                    "code": "STYLE_ARTIFACT_READ_FAILED",
                    "detail": str(exc),
                    "source_path": str(file_path),
                }
            )
            continue

        for raw in rows:
            line = str(raw).strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                issues.append(
                    {
                        "code": "STYLE_ARTIFACT_JSON_INVALID",
                        "detail": str(exc),
                        "source_path": str(file_path),
                    }
                )
                continue
            if not isinstance(payload, Mapping):
                continue

            jp_text = str(payload.get("jp_text") or "").strip()
            cn_text = str(payload.get("cn_text") or "").strip()
            if lang_from_file == "ja":
                lang = "ja"
                utterance = jp_text or cn_text
                paired_text = cn_text
                speaker = str(payload.get("speaker_jp") or payload.get("speaker_cn") or "").strip()
            elif lang_from_file == "cn":
                lang = "cn"
                utterance = cn_text or jp_text
                paired_text = jp_text
                speaker = str(payload.get("speaker_cn") or payload.get("speaker_jp") or "").strip()
            else:
                # Unknown suffix: prefer Japanese when available.
                lang = "ja" if jp_text else "cn"
                utterance = jp_text or cn_text
                paired_text = cn_text if lang == "ja" else jp_text
                speaker = str(payload.get("speaker_jp") or payload.get("speaker_cn") or "").strip()

            utterance = utterance.strip()
            if not utterance:
                continue

            file_rel_path = str(payload.get("file_rel_path") or "").strip()
            block_id = str(payload.get("block_id") or "").strip()
            line_index = int(payload.get("line_index") or 0)
            identity = f"{canon_id}::{lang}::{file_rel_path}::{block_id}::{line_index}::{utterance}"
            digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:24]
            vector_id = f"persona_utterance::{canon_id}::{lang}::{digest}"
            source_ref = f"persona_utterance://{canon_id}/{lang}/{digest}"
            evidence_ref = f"evd://persona_utterance/{canon_id}/{lang}/{digest}"

            text = (
                "[PERSONA_UTTERANCE]\n"
                f"canon_id: {canon_id}\n"
                f"lang: {lang}\n"
                f"speaker: {speaker or 'unknown'}\n"
                f"scene_ref: {file_rel_path}#{block_id}:{line_index}\n"
                "utterance:\n"
                f"{utterance}\n"
            )
            if paired_text:
                text += f"\npaired_text:\n{paired_text}\n"

            metadata = {
                "store": "persona_utterance",
                "canon_id": canon_id,
                "lang": lang,
                "speaker": speaker or None,
                "file_rel_path": file_rel_path or None,
                "block_id": block_id or None,
                "line_index": line_index,
                "source_file": str(file_path),
                "source_ref": source_ref,
                "evidence_ref": evidence_ref,
            }
            docs.append(
                PersonaUtteranceProjectionDocument(
                    vector_id=vector_id,
                    text=text,
                    metadata=metadata,
                )
            )

    docs.sort(key=lambda row: row.vector_id)
    return docs, issues


def sync_persona_utterance_vector_index(
    *,
    style_artifact_root: str | Path = DEFAULT_STYLE_ARTIFACT_ROOT,
    collection: str = DEFAULT_PERSONA_UTTERANCE_COLLECTION,
    backend: str = DEFAULT_CHROMA_BACKEND,
    chroma_path: str | Path = DEFAULT_CHROMA_PATH,
    include_ja: bool = True,
    include_cn: bool = True,
    env: Mapping[str, Any] | None = None,
    local_overrides: Mapping[str, Any] | None = None,
    node_id: str | None = None,
    chroma_client: Any | None = None,
) -> dict[str, Any]:
    merged_env: dict[str, Any] = dict(env or os.environ)
    if local_overrides:
        merged_env.update(dict(local_overrides))

    chroma_cfg_result = load_chroma_runtime_config(env=merged_env)
    if not chroma_cfg_result.success or chroma_cfg_result.config is None:
        raise RuntimeError(f"chroma runtime config invalid: {chroma_cfg_result.as_dict()}")
    chroma_cfg = chroma_cfg_result.config

    embed_cfg_result = load_embedding_runtime_config(env=merged_env)
    if not embed_cfg_result.success or embed_cfg_result.config is None:
        raise RuntimeError(f"embedding runtime config invalid: {embed_cfg_result.as_dict()}")
    embed_cfg = embed_cfg_result.config

    resolved_node_id = str(node_id or merged_env.get("HOST_RETR_NODE_ID") or DEFAULT_NODE_ID).strip() or DEFAULT_NODE_ID
    provider_registry = build_embedding_provider_registry(
        env=merged_env,
        openai_api_key=str(merged_env.get("OPENAI_API_KEY") or "").strip(),
    )
    embedding_service = EmbeddingServiceContract(
        runtime_config=embed_cfg,
        provider_registry=provider_registry,
        node_id=resolved_node_id,
        available=True,
    )
    active_route = resolve_embedding_route(embed_cfg, node_id=resolved_node_id)
    vector_dim = int(active_route.dim)

    docs, issues = collect_persona_utterance_projection_documents(
        style_artifact_root=style_artifact_root,
        include_ja=include_ja,
        include_cn=include_cn,
    )

    target_collection = str(collection or DEFAULT_PERSONA_UTTERANCE_COLLECTION).strip() or DEFAULT_PERSONA_UTTERANCE_COLLECTION
    client = chroma_client or _build_chroma_client(
        backend=backend,
        endpoint=chroma_cfg.endpoint,
        chroma_path=chroma_path,
    )
    coll = client.get_or_create_collection(name=target_collection)

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []
    embeddings: list[list[float]] = []
    ja_docs = 0
    cn_docs = 0

    for row in docs:
        result = embedding_service.embed(
            request_id=f"persona_sync::{row.vector_id}",
            text=row.text,
        )
        if not result.success:
            raise RuntimeError(
                "persona utterance embedding failed: "
                f"vector_id={row.vector_id} reason={result.reason_code} detail={result.detail}"
            )
        output = dict(result.output or {})
        embedding = [float(v) for v in list(output.get("embedding_vector") or [])]
        lang = str(row.metadata.get("lang") or "").strip().lower()
        if lang == "ja":
            ja_docs += 1
        elif lang == "cn":
            cn_docs += 1

        ids.append(row.vector_id)
        texts.append(row.text)
        metadata = dict(row.metadata)
        metadata["embedding_model_id"] = str(output.get("embedding_model_id") or active_route.model_id)
        metadata["embedding_provider"] = str(output.get("provider") or active_route.provider)
        metadata["embedding_route_id"] = str(output.get("route_id") or active_route.route_id)
        metadata["vector_dim"] = vector_dim
        metadatas.append(metadata)
        embeddings.append(embedding)

    if ids:
        coll.upsert(
            ids=ids,
            documents=texts,
            metadatas=metadatas,
            embeddings=embeddings,
        )

    collection_count = None
    if hasattr(coll, "count"):
        try:
            collection_count = int(coll.count())
        except Exception:
            collection_count = None

    return {
        "success": True,
        "collection": target_collection,
        "backend": str(backend),
        "embedding_provider": active_route.provider,
        "embedding_model_id": active_route.model_id,
        "embedding_route_id": active_route.route_id,
        "embedding_node_id": resolved_node_id,
        "vector_dim": vector_dim,
        "docs_synced": len(ids),
        "ja_docs_synced": ja_docs,
        "cn_docs_synced": cn_docs,
        "issues_count": len(issues),
        "issues": issues,
        "collection_count": collection_count,
    }


def _build_chroma_client(*, backend: str, endpoint: str, chroma_path: str | Path) -> Any:
    mode = str(backend or DEFAULT_CHROMA_BACKEND).strip().lower()
    if mode not in {"http", "persistent"}:
        raise ValueError(f"unsupported chroma backend: {backend!r}")

    try:
        import chromadb
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise RuntimeError(f"chromadb package unavailable: {exc}") from exc

    if mode == "persistent":
        return chromadb.PersistentClient(path=str(Path(chroma_path).resolve()))

    parsed = urlparse(str(endpoint or "http://127.0.0.1:8000").strip())
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 8000)
    ssl = parsed.scheme.lower() == "https"
    return chromadb.HttpClient(host=host, port=port, ssl=ssl)

