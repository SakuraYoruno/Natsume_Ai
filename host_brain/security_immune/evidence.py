from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class EvidenceStore:
    """Small file-backed evidence cache shared by host and obs processes."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "state.json"
        self.events_file = self.state_dir / "events.jsonl"
        self._lock = threading.Lock()

    def load_snapshot(self) -> dict[str, Any]:
        with self._lock:
            if not self.state_file.exists():
                return {}
            try:
                return json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                return {}

    def save_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp_file = self.state_file.with_suffix(".json.tmp")
            tmp_file.write_text(
                json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2),
                encoding="utf-8",
            )
            tmp_file.replace(self.state_file)

    def append_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            with self.events_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                fh.write("\n")

    def load_recent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(1000, int(limit)))
        with self._lock:
            if not self.events_file.exists():
                return []
            try:
                lines = self.events_file.read_text(encoding="utf-8").splitlines()[-limit:]
            except Exception:
                return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows
