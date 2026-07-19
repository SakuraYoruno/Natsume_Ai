from __future__ import annotations

from pathlib import Path
import subprocess

from scripts.ci.check_delivery_metadata import (
    CommitRecord,
    REQUIRED_PR_SECTIONS,
    read_commit_records,
    validate_commit_records,
    validate_pull_request_metadata,
)


def _body(*, boundary: str = "NE-01 未完成") -> str:
    content = {
        "## 目标": "收口本次切片",
        "## 生产接线": "接入真实调用点",
        "## 删除或停用的旧路径": "删除旧入口",
        "## 本地验证": "定向测试通过",
        "## 远程 CI 状态": "REMOTE_CI=NOT_RUN_ACCOUNT_QUOTA",
        "## 已知边界": boundary,
        "## 风险与回滚": "保留回滚路径",
    }
    return "\n\n".join(f"{section}\n\n{content[section]}" for section in REQUIRED_PR_SECTIONS)


def test_chinese_vertical_slice_and_real_markdown_pass() -> None:
    commits = (
        CommitRecord("a" * 40, ("b" * 40,), "重构：建立本地交付元数据门禁"),
    )
    assert validate_commit_records(commits) == ()
    assert validate_pull_request_metadata(
        title="NE-01：收口通用运行时权威",
        body=_body(),
    ) == ()


def test_merge_english_literal_escape_and_non_chinese_subject_fail() -> None:
    findings = validate_commit_records(
        (
            CommitRecord(
                "a" * 40,
                ("b" * 40, "c" * 40),
                r"Merge remote-tracking branch 'origin/dev'\nmore",
            ),
        )
    )
    codes = {item.code for item in findings}
    assert codes == {
        "CHINESE_COMMIT_SUBJECT_REQUIRED",
        "ENGLISH_MERGE_SUBJECT_DENIED",
        "FEATURE_BRANCH_MERGE_COMMIT_DENIED",
        "LITERAL_NEWLINE_ESCAPE_DENIED",
    }


def test_missing_or_reordered_pr_sections_and_literal_escape_fail() -> None:
    body = _body().replace("## 目标", "## 目标缺失").replace(
        "## 风险与回滚",
        r"## 风险与回滚\n",
    )
    findings = validate_pull_request_metadata(title="中文标题", body=body)
    codes = {item.code for item in findings}
    assert "PR_SECTION_REQUIRED" in codes
    assert "LITERAL_NEWLINE_ESCAPE_DENIED" in codes


def test_unqualified_program_completion_claim_fails_but_boundary_text_passes() -> None:
    assert {
        item.code
        for item in validate_pull_request_metadata(
            title="中文标题",
            body=_body(boundary="NE-01 PASS，生产闭环完成"),
        )
    } == {"UNQUALIFIED_COMPLETION_CLAIM"}
    assert validate_pull_request_metadata(
        title="中文标题",
        body=_body(boundary="NE-01 未完成，不宣称生产闭环"),
    ) == ()


def test_pr_title_is_checked_for_unqualified_completion_claim() -> None:
    findings = validate_pull_request_metadata(
        title="NE-01：完整架构完成",
        body=_body(),
    )
    assert {
        (item.code, item.subject)
        for item in findings
    } == {("UNQUALIFIED_COMPLETION_CLAIM", "pull_request.title")}
    assert validate_pull_request_metadata(
        title="NE-01：未完成范围纠偏",
        body=_body(),
    ) == ()


def test_negation_cannot_qualify_a_different_completion_claim_on_same_line() -> None:
    findings = validate_pull_request_metadata(
        title="中文标题",
        body=_body(boundary="NE-01 完成；NE-02 未完成"),
    )
    assert {
        (item.code, item.subject)
        for item in findings
    } == {
        (
            "UNQUALIFIED_COMPLETION_CLAIM",
            "pull_request.body:23",
        )
    }
    assert validate_pull_request_metadata(
        title="中文标题",
        body=_body(boundary="NE-01 未完成；NE-02 未完成"),
    ) == ()


def test_scoped_static_gate_pass_is_not_program_completion() -> None:
    assert validate_pull_request_metadata(
        title="中文标题",
        body=_body(
            boundary=(
                "Full architecture static Gate：PASS；"
                "这只证明静态边界，整体仍为 INCOMPLETE"
            )
        ),
    ) == ()
    assert {
        item.code
        for item in validate_pull_request_metadata(
            title="中文标题",
            body=_body(boundary="Full Architecture PASS；NE-02 未完成"),
        )
    } == {"UNQUALIFIED_COMPLETION_CLAIM"}


def test_git_reader_excludes_base_and_preserves_parent_count(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "gate@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Gate"],
        check=True,
    )
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "文档：建立基线"],
        check=True,
    )
    base = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    tracked.write_text("head\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qam", "测试：验证提交读取"], check=True)

    records = read_commit_records(
        repository_root=tmp_path,
        base_ref=base,
        head_ref="HEAD",
    )

    assert len(records) == 1
    assert records[0].subject == "测试：验证提交读取"
    assert len(records[0].parents) == 1
