"""Validate local commit and pull-request metadata before delivery.

This gate intentionally runs without GitHub Actions.  It prevents a feature
branch from hiding merge commits, malformed Chinese subjects, broken Markdown
or architecture completion claims that disagree with the machine status.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Sequence


CHECK_ID = "change_metadata_gate"
REQUIRED_PR_HEADINGS = (
    "目标",
    "生产接线",
    "删除或停用的旧路径",
    "本地验证",
    "远程 CI 状态",
    "已知边界",
    "风险与回滚",
)
ALLOWED_COMMIT_PREFIXES = (
    "重构",
    "修复",
    "功能",
    "测试",
    "文档",
    "性能",
    "安全",
    "构建",
)
COMMIT_SUBJECT_RE = re.compile(
    rf"^(?:{'|'.join(ALLOWED_COMMIT_PREFIXES)})：\S.+"
)
CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
LITERAL_NEWLINE_RE = re.compile(r"\\(?:r\\)?n")
REMOTE_CI_STATUS_RE = re.compile(
    r"\b(?:PASS|FAIL|NOT_RUN|NOT_RUN_ACCOUNT_QUOTA|ACCEPTANCE_PENDING_EXTERNAL)\b"
)
TERMINAL_CLAIM_PATTERNS = (
    re.compile(r"\bFULL\s+ARCHITECTURE\s+(?:DONE|PASS(?:ED)?)\b", re.IGNORECASE),
    re.compile(r"\bPROGRAM_(?:COMPLETE|DONE|PASS(?:ED)?)\b", re.IGNORECASE),
    re.compile(r"(?:整体|完整|全部|全量|最终).{0,16}(?:完成|闭环|通过)"),
)
NEGATION_MARKERS = (
    "未",
    "不",
    "不得",
    "不能",
    "不可",
    "尚未",
    "禁止",
    "INCOMPLETE",
    "NOT ",
    "NO ",
)


def build_report(
    *,
    root: str | Path,
    base_ref: str = "origin/dev",
    head_ref: str = "HEAD",
    pr_body_file: str | Path | None = None,
    pr_title: str | None = None,
    require_pr_metadata: bool = False,
) -> dict[str, Any]:
    repo_root = Path(root).resolve()
    failures: list[dict[str, Any]] = []

    try:
        head_sha = _git(repo_root, "rev-parse", head_ref).strip()
        commits = _read_commits(repo_root, base_ref=base_ref, head_ref=head_ref)
    except RuntimeError as exc:
        return _report(
            head_sha=None,
            base_ref=base_ref,
            head_ref=head_ref,
            failures=[_failure("METADATA_GIT_QUERY_FAILED", "git", str(exc))],
        )

    failures.extend(validate_commit_rows(commits))

    body: str | None = None
    if pr_body_file is not None:
        body_path = Path(pr_body_file)
        if not body_path.is_absolute():
            body_path = repo_root / body_path
        try:
            body = body_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            failures.append(
                _failure(
                    "METADATA_PR_BODY_UNREADABLE",
                    body_path.as_posix(),
                    str(exc),
                )
            )
    elif require_pr_metadata:
        failures.append(
            _failure(
                "METADATA_PR_BODY_REQUIRED",
                "pull_request.body",
                "Ready 前必须通过 --pr-body-file 提供真实 Markdown 正文",
            )
        )

    if require_pr_metadata and not pr_title:
        failures.append(
            _failure(
                "METADATA_PR_TITLE_REQUIRED",
                "pull_request.title",
                "Ready 前必须通过 --pr-title 提供中文标题",
            )
        )

    overall_status = _read_overall_status(repo_root, failures)
    failures.extend(
        validate_pr_metadata(
            body=body,
            title=pr_title,
            overall_status=overall_status,
        )
    )
    return _report(
        head_sha=head_sha,
        base_ref=base_ref,
        head_ref=head_ref,
        failures=failures,
    )


def build_commit_message_report(message_file: str | Path) -> dict[str, Any]:
    path = Path(message_file)
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        return _report(
            head_sha=None,
            base_ref="COMMIT_MSG",
            head_ref="COMMIT_MSG",
            failures=[
                _failure(
                    "METADATA_COMMIT_MESSAGE_UNREADABLE",
                    path.as_posix(),
                    str(exc),
                )
            ],
        )
    subject = lines[0].strip() if lines else ""
    failures = validate_commit_rows(
        [{"commit": path.as_posix(), "parents": (), "subject": subject}]
    )
    return _report(
        head_sha=None,
        base_ref="COMMIT_MSG",
        head_ref="COMMIT_MSG",
        failures=failures,
    )


def validate_commit_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        commit = str(row.get("commit") or "")
        subject = str(row.get("subject") or "")
        parents = tuple(str(item) for item in row.get("parents", ()))
        if len(parents) > 1:
            failures.append(
                _failure(
                    "METADATA_MERGE_COMMIT_FORBIDDEN",
                    commit,
                    "Feature 分支必须 rebase，同 dev 的 merge commit 不允许进入 PR",
                )
            )
        result = subject.split("：", 1)[1] if "：" in subject else ""
        if COMMIT_SUBJECT_RE.fullmatch(subject) is None or CHINESE_RE.search(result) is None:
            failures.append(
                _failure(
                    "METADATA_COMMIT_SUBJECT_INVALID",
                    commit,
                    "提交标题必须使用“重构/修复/功能/测试/文档/性能/安全/构建：中文结果”",
                )
            )
    return failures


def validate_pr_metadata(
    *,
    body: str | None,
    title: str | None,
    overall_status: str | None,
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if title is not None:
        if CHINESE_RE.search(title) is None:
            failures.append(
                _failure(
                    "METADATA_PR_TITLE_NOT_CHINESE",
                    "pull_request.title",
                    "PR 标题必须包含中文结果说明",
                )
            )
        if overall_status != "COMPLETE" and _contains_positive_terminal_claim(title):
            failures.append(
                _failure(
                    "METADATA_FALSE_TERMINAL_CLAIM",
                    "pull_request.title",
                    "机器状态尚未 COMPLETE，PR 标题不得宣称整体 PASS/DONE/完成/闭环",
                )
            )
    if body is None:
        return failures

    literal_match = LITERAL_NEWLINE_RE.search(body)
    if literal_match is not None:
        failures.append(
            _failure(
                "METADATA_LITERAL_NEWLINE_ESCAPE",
                "pull_request.body",
                "PR 正文包含字面量 \\n/\\r\\n；必须使用 Markdown 文件和 --body-file",
                line=_line_number(body, literal_match.start()),
            )
        )

    semantic_body = _scrub_markdown_comments_and_fences(body)
    heading_rows: list[tuple[str, int, int]] = []
    for heading in REQUIRED_PR_HEADINGS:
        pattern = re.compile(rf"(?m)^## {re.escape(heading)}\s*$")
        matches = list(pattern.finditer(semantic_body))
        if len(matches) != 1:
            failures.append(
                _failure(
                    "METADATA_PR_SECTION_INVALID",
                    "pull_request.body",
                    f"PR 正文必须且只能包含一次“## {heading}”",
                )
            )
            continue
        match = matches[0]
        heading_rows.append((heading, match.start(), match.end()))

    visible_headings = [
        match.group(1).strip()
        for match in re.finditer(r"(?m)^## (?!#)(.+?)\s*$", semantic_body)
    ]
    extras = [heading for heading in visible_headings if heading not in REQUIRED_PR_HEADINGS]
    if extras:
        failures.append(
            _failure(
                "METADATA_PR_EXTRA_SECTION",
                "pull_request.body",
                f"PR 正文不允许额外二级章节：{', '.join(extras)}",
            )
        )

    expected_order = list(REQUIRED_PR_HEADINGS)
    actual_order = [row[0] for row in sorted(heading_rows, key=lambda row: row[1])]
    if len(heading_rows) == len(REQUIRED_PR_HEADINGS) and actual_order != expected_order:
        failures.append(
            _failure(
                "METADATA_PR_SECTION_ORDER_INVALID",
                "pull_request.body",
                "PR 正文七个章节的顺序不符合交付协议",
            )
        )

    ordered_rows = sorted(heading_rows, key=lambda row: row[1])
    sections: dict[str, str] = {}
    for index, (heading, _, content_start) in enumerate(ordered_rows):
        content_end = ordered_rows[index + 1][1] if index + 1 < len(ordered_rows) else len(body)
        section = body[content_start:content_end].strip()
        sections[heading] = section
        if not _has_meaningful_content(section):
            failures.append(
                _failure(
                    "METADATA_PR_SECTION_EMPTY",
                    "pull_request.body",
                    f"“## {heading}”不能留空或只保留模板注释",
                    line=_line_number(body, content_start),
                )
            )
        elif CHINESE_RE.search(re.sub(r"<!--.*?-->", "", section, flags=re.DOTALL)) is None:
            failures.append(
                _failure(
                    "METADATA_PR_SECTION_NOT_CHINESE",
                    "pull_request.body",
                    f"“## {heading}”必须包含中文说明，路径或命令不能代替结论",
                    line=_line_number(body, content_start),
                )
            )

    remote_ci = sections.get("远程 CI 状态")
    if remote_ci and REMOTE_CI_STATUS_RE.search(remote_ci) is None:
        failures.append(
            _failure(
                "METADATA_REMOTE_CI_STATUS_MISSING",
                "pull_request.body",
                "远程 CI 章节必须明确写 PASS、FAIL、NOT_RUN 或 NOT_RUN_ACCOUNT_QUOTA",
            )
        )

    if overall_status != "COMPLETE":
        for line_number, line in enumerate(semantic_body.splitlines(), start=1):
            for pattern in TERMINAL_CLAIM_PATTERNS:
                for match in pattern.finditer(line):
                    prefix = line[max(0, match.start() - 24) : match.start()].upper()
                    if any(marker.upper() in prefix for marker in NEGATION_MARKERS):
                        continue
                    failures.append(
                        _failure(
                            "METADATA_FALSE_TERMINAL_CLAIM",
                            "pull_request.body",
                            "机器状态尚未 COMPLETE，PR 不得宣称整体 PASS/DONE/完成/闭环",
                            line=line_number,
                        )
                    )
    return failures


def _read_commits(
    repo_root: Path,
    *,
    base_ref: str,
    head_ref: str,
) -> list[dict[str, Any]]:
    _git(repo_root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    _git(repo_root, "rev-parse", "--verify", f"{head_ref}^{{commit}}")
    merge_base = _git(repo_root, "merge-base", base_ref, head_ref).strip()
    if not merge_base:
        raise RuntimeError(f"cannot determine merge-base for {base_ref} and {head_ref}")
    revision_range = f"{merge_base}..{head_ref}"
    hashes = [
        line.strip()
        for line in _git(repo_root, "rev-list", "--reverse", revision_range).splitlines()
        if line.strip()
    ]
    rows: list[dict[str, Any]] = []
    for commit in hashes:
        parents = tuple(
            item
            for item in _git(repo_root, "show", "-s", "--format=%P", commit).strip().split()
            if item
        )
        subject = _git(repo_root, "show", "-s", "--format=%s", commit).strip()
        rows.append({"commit": commit, "parents": parents, "subject": subject})
    return rows


def _read_overall_status(
    repo_root: Path,
    failures: list[dict[str, Any]],
) -> str | None:
    relative = "configs/release/current_architecture_status.json"
    path = repo_root / relative
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        failures.append(
            _failure(
                "METADATA_MACHINE_STATUS_UNREADABLE",
                relative,
                str(exc),
            )
        )
        return None
    return str(payload.get("overall_status") or "")


def _has_meaningful_content(section: str) -> bool:
    without_comments = re.sub(r"<!--.*?-->", "", section, flags=re.DOTALL)
    return bool(without_comments.strip(" \t\r\n-[]"))


def _contains_positive_terminal_claim(text: str) -> bool:
    for pattern in TERMINAL_CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 24) : match.start()].upper()
            if not any(marker.upper() in prefix for marker in NEGATION_MARKERS):
                return True
    return False


def _scrub_markdown_comments_and_fences(text: str) -> str:
    """Blank comments and fenced code while preserving offsets/newlines."""

    output: list[str] = []
    in_comment = False
    fence: str | None = None
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.lstrip()
        marker = "```" if stripped.startswith("```") else "~~~" if stripped.startswith("~~~") else None
        if marker is not None and not in_comment:
            fence = None if fence == marker else marker if fence is None else fence
            output.append(_blank_preserving_newline(raw_line))
            continue
        if fence is not None:
            output.append(_blank_preserving_newline(raw_line))
            continue

        chars = list(raw_line)
        index = 0
        while index < len(chars):
            if not in_comment and raw_line.startswith("<!--", index):
                in_comment = True
            if in_comment:
                if chars[index] not in "\r\n":
                    chars[index] = " "
                if raw_line.startswith("-->", index):
                    for offset in range(index, min(index + 3, len(chars))):
                        if chars[offset] not in "\r\n":
                            chars[offset] = " "
                    index += 3
                    in_comment = False
                    continue
            index += 1
        output.append("".join(chars))
    return "".join(output)


def _blank_preserving_newline(text: str) -> str:
    return "".join(char if char in "\r\n" else " " for char in text)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "git command failed"
        raise RuntimeError(f"git {' '.join(args)}: {detail}")
    return proc.stdout


def _failure(
    reason_code: str,
    artifact: str,
    message: str,
    *,
    line: int = 0,
) -> dict[str, Any]:
    return {
        "check_id": CHECK_ID,
        "reason_code": reason_code,
        "failed_artifact": artifact,
        "line": int(line),
        "message": message,
    }


def _report(
    *,
    head_sha: str | None,
    base_ref: str,
    head_ref: str,
    failures: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(
        failures,
        key=lambda row: (
            str(row.get("failed_artifact") or ""),
            int(row.get("line") or 0),
            str(row.get("reason_code") or ""),
        ),
    )
    return {
        "check_id": CHECK_ID,
        "status": "failed" if ordered else "passed",
        "reason_code": ordered[0]["reason_code"] if ordered else None,
        "commit_sha": head_sha,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "failures": ordered,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="校验中文提交、无 merge commit 和真实 Markdown PR 元数据",
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--base-ref", default="origin/dev")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--pr-body-file")
    parser.add_argument("--pr-title")
    parser.add_argument("--require-pr-metadata", action="store_true")
    parser.add_argument("--report-file")
    parser.add_argument("--commit-message-file")
    args = parser.parse_args(argv)

    if args.commit_message_file:
        report = build_commit_message_report(args.commit_message_file)
    else:
        report = build_report(
            root=args.root,
            base_ref=args.base_ref,
            head_ref=args.head_ref,
            pr_body_file=args.pr_body_file,
            pr_title=args.pr_title,
            require_pr_metadata=args.require_pr_metadata,
        )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report_file:
        report_path = Path(args.report_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
