from __future__ import annotations

from typing import Any, Dict, List

from memory.shared_chroma import get_chroma_collection_safe


class EpisodicMemory:
    """
    Episodic Memory（经历库，Chroma persistent collection）。
    建议文档短一些，并把可解释的字段放到 metadata 里（来源/时间/标签等）。
    """
    def __init__(self, chroma_dir: str, collection_name: str) -> None:
        self.collection_name = collection_name
        self.collection = get_chroma_collection_safe(chroma_dir, collection_name)

    def query(self, query_text: str, top_k: int = 6) -> List[Dict[str, Any]]:
        if self.collection is None:
            return []
        try:
            res = self.collection.query(query_texts=[query_text], n_results=max(1, top_k))
            docs = (res.get("documents") or [[]])[0]
            ids = (res.get("ids") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            out: List[Dict[str, Any]] = []
            for i in range(min(len(docs), len(ids))):
                score = float(dists[i]) if i < len(dists) and dists[i] is not None else 0.0
                out.append({"id": ids[i], "score": score, "text": docs[i], "meta": metas[i] or {}})
            return out
        except Exception:
            return []
