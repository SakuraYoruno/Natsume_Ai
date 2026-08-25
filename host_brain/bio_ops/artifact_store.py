"""Artifact store for Bio-Ops reports and candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from host_brain.bio_ops.contracts import BioOpsArtifact
from host_brain.agent_runtime.artifact_store import REPO_ROOT, sanitize_segment


DEFAULT_BIO_OPS_ROOT = REPO_ROOT / "artifacts" / "bio_ops"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return _as_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return {}


class BioOpsArtifactStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser() if root else DEFAULT_BIO_OPS_ROOT

    def prepare_task_dir(self, *, capability: str, task_id: str) -> Path:
        path = self.root / sanitize_segment(capability, "capability") / sanitize_segment(task_id, "task")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_json(self, path: Path, payload: Any, *, artifact_type: str = "json") -> BioOpsArtifact:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return self.write_text(path, text, artifact_type=artifact_type)

    def write_text(self, path: Path, text: str, *, artifact_type: str = "text") -> BioOpsArtifact:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = str(text or "")
        path.write_text(payload, encoding="utf-8", errors="replace")
        digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
        return BioOpsArtifact(
            artifact_id=f"{sanitize_segment(artifact_type)}_{digest[:12]}",
            artifact_type=artifact_type,
            path=str(path),
            digest=digest,
        )


__all__ = ["BioOpsArtifactStore", "DEFAULT_BIO_OPS_ROOT"]
