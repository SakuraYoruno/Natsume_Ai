from __future__ import annotations


def get_chroma_collection_safe(chroma_dir: str, collection_name: str):
    """
    返回 Chroma collection；如果 chromadb 不可用则返回 None。
    """
    try:
        import chromadb  # type: ignore
        from chromadb.config import Settings as ChromaSettings  # type: ignore
    except Exception:
        return None

    try:
        client = chromadb.PersistentClient(
            path=chroma_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        return client.get_or_create_collection(name=collection_name)
    except Exception:
        return None
