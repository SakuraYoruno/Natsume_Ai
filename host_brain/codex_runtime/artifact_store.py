"""Artifact persistence for Codex runtime v2."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CODEX_RUNS_ROOT = REPO_ROOT / "artifacts" / "codex_runs"


def sanitize_segment(value: str, fallback: str = "codex-run") -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip()).strip(".-")
    return cleaned or fallback


class CodexArtifactStore:
    def __init__(self, runs_root: str | Path | None = None) -> None:
        self.runs_root = Path(runs_root).expanduser() if runs_root else DEFAULT_CODEX_RUNS_ROOT

    def prepare_run_dir(self, work_order_id: str) -> Path:
        run_dir = self.runs_root / sanitize_segment(work_order_id, "work-order")
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    @staticmethod
    def write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text or ""), encoding="utf-8", errors="replace")

    @staticmethod
    def write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return ""


__all__ = ["CodexArtifactStore", "DEFAULT_CODEX_RUNS_ROOT", "REPO_ROOT", "sanitize_segment"]
