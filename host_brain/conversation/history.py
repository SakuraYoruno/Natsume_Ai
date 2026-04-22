from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Literal

from .config import conversation_summary_max_chars, conversation_summary_threshold
from .schema import Turn
from .store import TurnStore

SummaryRenderLang = Literal["zh", "ja"]

_logger = logging.getLogger("Natsume.conversation.history")


@dataclass(frozen=True)
class ConversationWindow:
    recent_turns: list[Turn] = field(default_factory=list)
    older_summary: str = ""
    fetched_count: int = 0
    injected_count: int = 0
    older_turn_count: int = 0
    skipped_corrupted_count: int = 0
    skipped_empty_count: int = 0
    deduped_same_role_count: int = 0
    oldest_ts_ms: int | None = None
    newest_ts_ms: int | None = None


class ConversationHistory:
    def __init__(self, store: TurnStore) -> None:
        self._store = store

    def fetch_recent_turns(
        self,
        *,
        session_id: str,
        max_turns: int,
        exclude_request_id: str | None = None,
    ) -> list[Turn]:
        return self._store.query_recent(
            session_id=session_id,
            limit=max(1, int(max_turns)),
            exclude_request_id=exclude_request_id,
        )

    def fetch_for_messages(
        self,
        *,
        session_id: str,
        max_turns: int,
        exclude_request_id: str | None = None,
    ) -> list[Turn]:
        return self.fetch_window_for_messages(
            session_id=session_id,
            max_turns=max_turns,
            exclude_request_id=exclude_request_id,
        ).recent_turns

    def fetch_window_for_messages(
        self,
        *,
        session_id: str,
        max_turns: int,
        exclude_request_id: str | None = None,
        summary_threshold: int | None = None,
        render_target_lang: SummaryRenderLang = "zh",
        summary_max_chars: int | None = None,
    ) -> ConversationWindow:
        safe_limit = max(1, int(max_turns))
        raw_turns = self.fetch_recent_turns(
            session_id=session_id,
            max_turns=safe_limit,
            exclude_request_id=exclude_request_id,
        )
        filtered, skipped_corrupted, skipped_empty, deduped_same_role = self._normalize_turns_for_messages(raw_turns)

        older_turn_count = 0
        older_summary = ""
        threshold = int(summary_threshold if summary_threshold is not None else conversation_summary_threshold())
        if filtered and threshold > 0:
            newest_turn_index = int(filtered[0].turn_index)
            older_turn_count = self._store.count_before_turn_index(
                session_id=session_id,
                before_turn_index=newest_turn_index,
                exclude_request_id=exclude_request_id,
            )
            if older_turn_count >= threshold:
                older_summary = self.build_summary_for_older(
                    session_id=session_id,
                    before_turn_index=newest_turn_index,
                    max_chars=(
                        int(summary_max_chars)
                        if summary_max_chars is not None
                        else conversation_summary_max_chars()
                    ),
                    render_target_lang=render_target_lang,
                    exclude_request_id=exclude_request_id,
                )

        oldest_ts_ms = int(filtered[0].ts_ms) if filtered else None
        newest_ts_ms = int(filtered[-1].ts_ms) if filtered else None
        window = ConversationWindow(
            recent_turns=filtered,
            older_summary=older_summary,
            fetched_count=len(raw_turns),
            injected_count=len(filtered),
            older_turn_count=max(0, int(older_turn_count)),
            skipped_corrupted_count=skipped_corrupted,
            skipped_empty_count=skipped_empty,
            deduped_same_role_count=deduped_same_role,
            oldest_ts_ms=oldest_ts_ms,
            newest_ts_ms=newest_ts_ms,
        )
        _logger.info(
            "conversation_history session_id=%s fetched_turns=%s injected_turns=%s older_turn_count=%s summary_present=%s oldest_ts_ms=%s newest_ts_ms=%s excluded_request_id=%s",
            str(session_id or "").strip(),
            window.fetched_count,
            window.injected_count,
            window.older_turn_count,
            bool(window.older_summary),
            window.oldest_ts_ms,
            window.newest_ts_ms,
            str(exclude_request_id or "").strip() or None,
        )
        return window

    def build_summary_for_older(
        self,
        *,
        session_id: str,
        before_turn_index: int,
        max_chars: int,
        render_target_lang: SummaryRenderLang = "zh",
        exclude_request_id: str | None = None,
    ) -> str:
        older_turns = self._store.query_before_turn_index(
            session_id=session_id,
            before_turn_index=before_turn_index,
            limit=6,
            exclude_request_id=exclude_request_id,
        )
        filtered, _sc, _se, _dd = self._normalize_turns_for_messages(older_turns)
        if not filtered:
            return ""

        user_topics: list[str] = []
        assistant_topics: list[str] = []
        for turn in filtered:
            snippet = self._snippet(turn.content, max_chars=24)
            if not snippet:
                continue
            if turn.role == "user":
                user_topics.append(snippet)
            else:
                assistant_topics.append(snippet)

        if str(render_target_lang or "zh").strip().lower() == "ja":
            parts: list[str] = []
            if user_topics:
                parts.append(f"少し前までの流れでは、相手は{self._join_snippets(user_topics, '、')}に触れていた。")
            if assistant_topics:
                parts.append(f"あなたは{self._join_snippets(assistant_topics, '、')}のように返していた。")
            summary = " ".join(parts).strip()
        else:
            parts = []
            if user_topics:
                parts.append(f"更早些时候，对方提到过{self._join_snippets(user_topics, '、')}。")
            if assistant_topics:
                parts.append(f"你当时主要是顺着{self._join_snippets(assistant_topics, '、')}在回应。")
            summary = " ".join(parts).strip()

        return self._trim_to_char_limit(summary, max_chars=max(48, int(max_chars or 0)))

    @staticmethod
    def _normalize_turns_for_messages(raw_turns: list[Turn]) -> tuple[list[Turn], int, int, int]:
        if not raw_turns:
            return [], 0, 0, 0
        filtered: list[Turn] = []
        skipped_corrupted = 0
        skipped_empty = 0
        deduped_same_role = 0
        for turn in raw_turns:
            if turn.corrupted:
                skipped_corrupted += 1
                continue
            if not str(turn.content or "").strip():
                skipped_empty += 1
                continue
            if filtered and filtered[-1].role == turn.role:
                filtered[-1] = turn
                deduped_same_role += 1
            else:
                filtered.append(turn)
        return filtered, skipped_corrupted, skipped_empty, deduped_same_role

    @staticmethod
    def _snippet(text: str, *, max_chars: int) -> str:
        normalized = " ".join(str(text or "").strip().split())
        if not normalized:
            return ""
        if len(normalized) <= max_chars:
            return normalized
        return normalized[: max(1, max_chars - 1)].rstrip() + "…"

    @staticmethod
    def _join_snippets(snippets: list[str], delimiter: str) -> str:
        unique: list[str] = []
        for item in snippets:
            cleaned = str(item or "").strip()
            if cleaned and cleaned not in unique:
                unique.append(cleaned)
        return delimiter.join(unique[:3])

    @staticmethod
    def _trim_to_char_limit(text: str, *, max_chars: int) -> str:
        normalized = str(text or "").strip()
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized
        candidate = normalized[:max_chars].rstrip()
        boundary = max(candidate.rfind(ch) for ch in "。！？.!?；;")
        if boundary >= max_chars // 2:
            return candidate[: boundary + 1].strip()
        return candidate.rstrip("，,、 ").strip()
