#!/usr/bin/env python3
"""Fail-closed local gate for feature-branch commit and PR metadata."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable, Sequence


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
REQUIRED_PR_SECTIONS = (
    "## 目标",
    "## 生产接线",
    "## 删除或停用的旧路径",
    "## 本地验证",
    "## 远程 CI 状态",
    "## 已知边界",
    "## 风险与回滚",
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_COMMIT_RE = re.compile(
    rf"^(?:{'|'.join(ALLOWED_COMMIT_PREFIXES)})：(?P<result>\S(?:.*\S)?)$"
)
_FALSE_COMPLETION_RE = re.compile(
    r"(?:Full\s+Architecture|完整架构|整体架构|生产闭环|NE-\d{2})"
    r".{0,20}(?:DONE|PASS|完成|已完成|闭环)",
    re.IGNORECASE,
)
_NEGATION_MARKERS = (
    "不宣称",
    "不得",
    "不能",
    "未完成",
    "尚未",
    "非完成",
    "不是",
    "INCOMPLETE",
    "PARTIAL",
    "NOT_RUN",
    "基础资产",
)


@dataclass(frozen=True, slots=True)
class CommitRecord:
    sha: str
    parents: tuple[str, ...]
    subject: str


@dataclass(frozen=True, slots=True)
class GateFinding:
    code: str
    subject: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "subject": self.subject, "detail": self.detail}


def validate_commit_records(records: Iterable[CommitRecord]) -> tuple[GateFinding, ...]:
    findings: list[GateFinding] = []
    for record in records:
        subject = str(record.subject or "")
        ref = str(record.sha or "").strip() or "<unknown>"
        if len(record.parents) > 1:
            findings.append(
                GateFinding(
                    "FEATURE_BRANCH_MERGE_COMMIT_DENIED",
                    ref,
                    "feature 分支只能通过 rebase 同步 dev",
                )
            )
        if subject.startswith("Merge "):
            findings.append(
                GateFinding(
                    "ENGLISH_MERGE_SUBJECT_DENIED",
                    ref,
                    subject,
                )
            )
        if "\\n" in subject:
            findings.append(
                GateFinding("LITERAL_NEWLINE_ESCAPE_DENIED", ref, subject)
            )
        matched = _COMMIT_RE.fullmatch(subject)
        if matched is None or _CJK_RE.search(matched.group("result")) is None:
            findings.append(
                GateFinding(
                    "CHINESE_COMMIT_SUBJECT_REQUIRED",
                    ref,
                    subject,
                )
            )
        if _unqualified_completion_claim(subject):
            findings.append(
                GateFinding("UNQUALIFIED_COMPLETION_CLAIM", ref, subject)
            )
    return tuple(findings)


def validate_pull_request_metadata(
    *,
    title: str,
    body: str,
) -> tuple[GateFinding, ...]:
    findings: list[GateFinding] = []
    normalized_title = str(title or "").strip()
    raw_body = str(body or "")
    if not normalized_title or _CJK_RE.search(normalized_title) is None:
        findings.append(
            GateFinding("CHINESE_PR_TITLE_REQUIRED", "pull_request.title", normalized_title)
        )
    if "\\n" in normalized_title or "\\n" in raw_body:
        findings.append(
            GateFinding(
                "LITERAL_NEWLINE_ESCAPE_DENIED",
                "pull_request",
                "PR 标题或正文包含字面量反斜杠 n",
            )
        )
    headings = tuple(
        line.strip()
        for line in raw_body.splitlines()
        if line.strip().startswith("## ")
    )
    positions: list[int] = []
    for section in REQUIRED_PR_SECTIONS:
        count = headings.count(section)
        if count != 1:
            findings.append(
                GateFinding(
                    "PR_SECTION_REQUIRED",
                    "pull_request.body",
                    f"{section} count={count}",
                )
            )
        elif section in headings:
            positions.append(headings.index(section))
    if len(positions) == len(REQUIRED_PR_SECTIONS) and positions != sorted(positions):
        findings.append(
            GateFinding(
                "PR_SECTION_ORDER_INVALID",
                "pull_request.body",
                "PR 固定章节顺序被改变",
            )
        )
    for line_number, line in enumerate(raw_body.splitlines(), start=1):
        if _unqualified_completion_claim(line):
            findings.append(
                GateFinding(
                    "UNQUALIFIED_COMPLETION_CLAIM",
                    f"pull_request.body:{line_number}",
                    line.strip(),
                )
            )
    return tuple(findings)


def _unqualified_completion_claim(value: str) -> bool:
    text = str(value or "")
    if _FALSE_COMPLETION_RE.search(text) is None:
        return False
    upper = text.upper()
    return not any(marker.upper() in upper for marker in _NEGATION_MARKERS)


def read_commit_records(
    *,
    repository_root: Path,
    base_ref: str,
    head_ref: str,
) -> tuple[CommitRecord, ...]:
    command = (
        "git",
        "-C",
        str(repository_root),
        "log",
        "--format=%H%x1f%P%x1f%s",
        f"{base_ref}..{head_ref}",
    )
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "git log failed")
    records: list[CommitRecord] = []
    for line in completed.stdout.splitlines():
        parts = line.split("\x1f", 2)
        if len(parts) != 3:
            raise RuntimeError("git log metadata record is malformed")
        sha, parents, subject = parts
        records.append(
            CommitRecord(
                sha=sha,
                parents=tuple(item for item in parents.split() if item),
                subject=subject,
            )
        )
    return tuple(records)


def run_gate(
    *,
    repository_root: Path,
    base_ref: str,
    head_ref: str,
    pr_title: str,
    pr_body_file: Path,
) -> tuple[GateFinding, ...]:
    records = read_commit_records(
        repository_root=repository_root,
        base_ref=base_ref,
        head_ref=head_ref,
    )
    body = pr_body_file.read_text(encoding="utf-8")
    return (
        *validate_commit_records(records),
        *validate_pull_request_metadata(title=pr_title, body=body),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref", default="origin/dev")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--pr-title", required=True)
    parser.add_argument("--pr-body-file", type=Path, required=True)
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        findings = run_gate(
            repository_root=args.repository_root.resolve(),
            base_ref=str(args.base_ref),
            head_ref=str(args.head_ref),
            pr_title=str(args.pr_title),
            pr_body_file=args.pr_body_file.resolve(),
        )
    except (OSError, RuntimeError, UnicodeError) as exc:
        findings = (
            GateFinding("DELIVERY_METADATA_GATE_UNAVAILABLE", "gate", str(exc)),
        )
    payload = {
        "schema_version": "natsume.delivery-metadata-gate.v1",
        "status": "PASS" if not findings else "FAIL",
        "findings": [finding.to_dict() for finding in findings],
    }
    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    elif findings:
        for finding in findings:
            print(f"{finding.code}: {finding.subject}: {finding.detail}", file=sys.stderr)
    else:
        print("DELIVERY_METADATA_GATE=PASS")
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
