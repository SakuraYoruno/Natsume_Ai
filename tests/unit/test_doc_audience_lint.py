from __future__ import annotations

from pathlib import Path

from tools.doc_lint.doc_audience_lint import scan_markdown_files


def test_hidden_audience_metadata_is_valid_for_public_readme(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "<!-- 受众：MASTER -->\n"
        "<!-- 标题：公开项目首页 -->\n\n"
        "# 项目首页\n",
        encoding="utf-8",
    )

    assert scan_markdown_files(tmp_path) == []


def test_nested_document_still_requires_audience_metadata(tmp_path: Path) -> None:
    document = tmp_path / "docs" / "guide.md"
    document.parent.mkdir(parents=True)
    document.write_text("# 缺少元数据\n", encoding="utf-8")

    failures = scan_markdown_files(tmp_path)

    assert len(failures) == 1
    assert failures[0]["reason_code"] == "DOC_AUDIENCE_INVALID"
