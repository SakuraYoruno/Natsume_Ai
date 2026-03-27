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
ALLOWED_WECHAT_COMMAND_ACTIONS = {
    # ── Send actions ──────────────────────────────────────────────────────
    "send_text",
    "send_image",
    "send_file",
    "send_voice",
    "voice_call",       # not supported by wxautox4; returns error
    "video_call",       # not supported by wxautox4; returns error
    # ── Phase 1: Query actions ────────────────────────────────────────────
    "get_my_info",           # wx.GetMyInfo()
    "get_friends",           # wx.GetFriends()
    "get_groups",            # wx.GetRecentGroups()
    "get_recent_chats",      # wx.GetSession()
    "get_history_messages",  # wx.GetHistoryMessage(who, savepath?)
    # ── Phase 1: Listen management ────────────────────────────────────────
    "listen_add",            # wx.AddListenChat(who)
    "listen_remove",         # wx.RemoveListenChat(who)
    "listen_stop",           # wx.StopListening()
    # ── Phase 1: Navigation ───────────────────────────────────────────────
    "chat_with",             # wx.ChatWith(who)
    # ── Phase 2: Window & Session ─────────────────────────────────────────
    "get_sub_window",        # wx.GetSubWindow(who)
    "get_all_sub_windows",   # wx.GetAllSubWindow()
    "get_chat_info",         # wx.ChatInfo()
    "get_dialog",            # wx.GetDialog()
    # ── Phase 2: Messaging ────────────────────────────────────────────────
    "send_url_card",         # wx.SendUrlCard(url, title, desc, who)
    "get_all_messages",      # wx.GetAllMessage()
    # ── Phase 2: Contacts ─────────────────────────────────────────────────
    "get_new_friends",       # wx.GetNewFriends()
    "get_friend_details",    # wx.GetFriendDetails(who)
}

# --- Incremental read state for result file ---
# Tracks (file_path_str -> byte_offset) so repeated lookups skip already-scanned lines.
_RESULT_FILE_OFFSET: dict[str, int] = {}
# In-memory index: command_id -> result row (populated by incremental reads).
_RESULT_INDEX: dict[str, dict[str, Any]] = {}
# Maximum number of bytes to tail-read from the result file on first access
# (avoids reading a huge history when the process first starts).
_RESULT_TAIL_BYTES = 512 * 1024  # 512 KB


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
    target_candidates: list[str] | None = None,
    content: str = "",
    request_id: str = "",
    queue_file: Path | None = None,
    ts_ms: int | None = None,
    extra_fields: dict[str, Any] | None = None,  # Phase 1: arbitrary extra JSONL fields
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
    candidates: list[str] = []
    seen_candidate: set[str] = set()
    for item in list(target_candidates or []):
        value = str(item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen_candidate:
            continue
        seen_candidate.add(key)
        candidates.append(value)
    if candidates:
        row["target_candidates"] = candidates
    # Merge any extra fields (e.g. savepath for get_history_messages)
    if extra_fields:
        for k, v in extra_fields.items():
            if k not in row:
                row[k] = v
    _append_jsonl_row(queue_path, row)
    return row


def find_wechat_command_result(
    *,
    command_id: str,
    result_file: Path | None = None,
) -> dict[str, Any] | None:
    """Look up a command result using an incremental read + in-memory index.

    On first call for a given *result_file*, only the tail portion of the file
    is read (up to ``_RESULT_TAIL_BYTES``).  Subsequent calls only scan bytes
    appended since the last read.  This avoids the O(file_size) penalty that
    plagued the original implementation during long-running sessions.
    """
    command_id_value = str(command_id or "").strip()
    if not command_id_value:
        return None

    result_path = result_file or resolve_command_file_paths()[1]
    if not result_path.exists():
        return None

    _ingest_new_result_rows(result_path)

    return _RESULT_INDEX.get(command_id_value)


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


# ---------------------------------------------------------------------------
# Incremental result file reader
# ---------------------------------------------------------------------------

def _ingest_new_result_rows(result_path: Path) -> None:
    """Read only the bytes appended since the last call and update ``_RESULT_INDEX``.

    On first invocation for a given file the reader starts from
    ``max(0, file_size - _RESULT_TAIL_BYTES)`` so that a very large historical
    file does not cause an O(N) startup penalty.  Subsequent calls seek to the
    stored offset and read only the delta.

    If the file was truncated (e.g. by log rotation), the reader detects the
    shorter size and resets to read from the beginning.
    """
    key = str(result_path)
    try:
        file_size = result_path.stat().st_size
    except OSError:
        return

    prev_offset = _RESULT_FILE_OFFSET.get(key)

    if prev_offset is None:
        # First access — tail-read only the last _RESULT_TAIL_BYTES.
        start = max(0, file_size - _RESULT_TAIL_BYTES)
    elif prev_offset > file_size:
        # File was truncated / rotated — reset.
        start = 0
    else:
        start = prev_offset

    if start >= file_size:
        _RESULT_FILE_OFFSET[key] = file_size
        return

    try:
        with result_path.open("r", encoding="utf-8") as handle:
            handle.seek(start)
            # If we seeked into the middle of a line, skip the partial line.
            if start > 0:
                handle.readline()
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    cid = str(payload.get("command_id") or "").strip()
                    if cid:
                        _RESULT_INDEX[cid] = payload
            _RESULT_FILE_OFFSET[key] = handle.tell()
    except OSError:
        return


def reset_result_cache() -> None:
    """Clear the incremental read cache.  Useful for testing."""
    _RESULT_FILE_OFFSET.clear()
    _RESULT_INDEX.clear()
