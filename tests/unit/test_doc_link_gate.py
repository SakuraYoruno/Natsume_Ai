from __future__ import annotations

from pathlib import Path

from tools.doc_lint.doc_link_gate import (
    REASON_MISSING,
    REASON_OUTSIDE,
    scan_repository,
)


def test_current_markdown_local_links_are_valid(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "target.md").write_text("ok", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[local](docs/target.md#section)\n[web](https://example.com)\n",
        encoding="utf-8",
    )

    assert scan_repository(tmp_path) == []


def test_missing_and_outside_links_fail(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "current.md").write_text(
        "[missing](missing.md)\n[outside](../../outside.md)\n",
        encoding="utf-8",
    )

    failures = scan_repository(tmp_path)
    assert [row["reason_code"] for row in failures] == [
        REASON_MISSING,
        REASON_OUTSIDE,
    ]


def test_history_links_are_not_current_authority(tmp_path: Path) -> None:
    history = tmp_path / "docs" / "_history"
    history.mkdir(parents=True)
    (history / "old.md").write_text("[removed](missing.py)", encoding="utf-8")

    assert scan_repository(tmp_path) == []
