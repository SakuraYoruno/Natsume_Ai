"""File-backed queue helpers for Edge-side WeChat command dispatch."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid
from typing import Any


DEFAULT_QUEUE_FILENAME = "wechat_command_queue.jsonl"
DEFAULT_RESULT_FILENAME = "wechat_command_result.jsonl"
ALLOWED_WECHAT_COMMAND_ACTIONS = {"send_text", "voice_call", "video_call"}


def resolve_command_file_paths() -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parents[2]
    runtime_root = repo_root / "runtime" / "edge"

    queue_raw = str(os.environ.get("EDGE_WECHAT_COMMAND_QUEUE_FILE") or "").strip()
    result_raw = str(os.environ.get("EDGE_WECHAT_COMMAND_RESULT_FILE") or "").strip()

    queue_file = _resolve_path(queue_raw, repo_root=repo_root, default=runtime_root / DEFAULT_QUEUE_FILENAME)
    result_file = _resolve_path(result_raw, repo_root=repo_root, default=runtime_root / DEFAULT_RESULT_FILENAME)
    return queue_file, result_file


def enqueue_wechat_command(
    *,
    action: str,
    target: str,
    content: str = "",
    request_id: str = "",
    queue_file: Path | None = None,
    ts_ms: int | None = None,
) -> dict[str, Any]:
    queue_path = queue_file or resolve_command_file_paths()[0]
    queue_path.parent.mkdir(parents=True, exist_ok=True)

    now_value = int(ts_ms if ts_ms is not None else time.time() * 1000)
    action_value = str(action or "").strip().lower()
    target_value = str(target or "").strip()
    content_value = str(content or "")
    req_value = str(request_id or "").strip()
    command_id = f"wechatcmd-{now_value}-{uuid.uuid4().hex[:8]}"

    row = {
        "command_id": command_id,
        "request_id": req_value,
        "action": action_value,
        "target": target_value,
        "content": content_value,
        "ts_ms": now_value,
    }
    _append_jsonl_row(queue_path, row)
    return row


def find_wechat_command_result(
    *,
    command_id: str,
    result_file: Path | None = None,
) -> dict[str, Any] | None:
    command_id_value = str(command_id or "").strip()
    if not command_id_value:
        return None

    result_path = result_file or resolve_command_file_paths()[1]
    if not result_path.exists():
        return None

    found: dict[str, Any] | None = None
    for row in _iter_jsonl_rows(result_path):
        if str(row.get("command_id") or "").strip() == command_id_value:
            found = row
    return found


def wait_wechat_command_result(
    *,
    command_id: str,
    timeout_ms: int = 8000,
    poll_interval_s: float = 0.2,
    result_file: Path | None = None,
) -> dict[str, Any] | None:
    command_id_value = str(command_id or "").strip()
    if not command_id_value:
        return None

    timeout_value = max(0, int(timeout_ms))
    poll_value = max(0.05, float(poll_interval_s))
    deadline = time.monotonic() + (float(timeout_value) / 1000.0)

    while time.monotonic() <= deadline:
        row = find_wechat_command_result(command_id=command_id_value, result_file=result_file)
        if row is not None:
            return row
        time.sleep(poll_value)
    return None


def _resolve_path(raw: str, *, repo_root: Path, default: Path) -> Path:
    value = str(raw or "").strip()
    if not value:
        return default
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return path


def _append_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    line = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.write("\n")


def _iter_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                line = str(raw).strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except Exception:
        return []
    return rows

