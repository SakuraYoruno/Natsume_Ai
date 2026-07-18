from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "delivery" / "change_metadata_gate.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("change_metadata_gate", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load metadata gate: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_module()


def _valid_body(*, remote_status: str = "NOT_RUN_ACCOUNT_QUOTA") -> str:
    return f"""## 目标
纠正交付元数据。

## 生产接线
接入本地门禁入口。

## 删除或停用的旧路径
停用内联正文参数。

## 本地验证
定向单元测试通过。

## 远程 CI 状态
`{remote_status}`：账户额度不可用。

## 已知边界
不宣称整体完成。

## 风险与回滚
删除门禁提交即可回滚。
"""


def test_valid_chinese_commit_and_pr_metadata_pass() -> None:
    commit_failures = gate.validate_commit_rows(
        [
            {
                "commit": "a" * 40,
                "parents": ("b" * 40,),
                "subject": "构建：增加本地交付元数据门禁",
            }
        ]
    )
    pr_failures = gate.validate_pr_metadata(
        body=_valid_body(),
        title="NE-03：修复上下文持久化边界",
        overall_status="INCOMPLETE",
    )

    assert commit_failures == []
    assert pr_failures == []


def test_merge_commit_and_nonstandard_subject_fail() -> None:
    failures = gate.validate_commit_rows(
        [
            {
                "commit": "a" * 40,
                "parents": ("b" * 40, "c" * 40),
                "subject": "Merge remote-tracking branch origin/dev",
            }
        ]
    )

    assert {row["reason_code"] for row in failures} == {
        "METADATA_MERGE_COMMIT_FORBIDDEN",
        "METADATA_COMMIT_SUBJECT_INVALID",
    }


def test_literal_newline_and_missing_sections_fail() -> None:
    failures = gate.validate_pr_metadata(
        body="## 目标\\n- 这不是 Markdown 换行",
        title="NE-03：修复上下文边界",
        overall_status="INCOMPLETE",
    )

    reasons = {row["reason_code"] for row in failures}
    assert "METADATA_LITERAL_NEWLINE_ESCAPE" in reasons
    assert "METADATA_PR_SECTION_INVALID" in reasons


def test_false_terminal_claim_fails_while_explicit_boundary_passes() -> None:
    invalid = gate.validate_pr_metadata(
        body=_valid_body().replace("不宣称整体完成。", "完整架构已经闭环。"),
        title="NE-03：修复上下文边界",
        overall_status="INCOMPLETE",
    )
    valid = gate.validate_pr_metadata(
        body=_valid_body(),
        title="NE-03：修复上下文边界",
        overall_status="INCOMPLETE",
    )

    assert any(row["reason_code"] == "METADATA_FALSE_TERMINAL_CLAIM" for row in invalid)
    assert not any(row["reason_code"] == "METADATA_FALSE_TERMINAL_CLAIM" for row in valid)


def test_remote_ci_status_must_be_explicit() -> None:
    failures = gate.validate_pr_metadata(
        body=_valid_body(remote_status="稍后再看"),
        title="NE-03：修复上下文边界",
        overall_status="INCOMPLETE",
    )

    assert any(row["reason_code"] == "METADATA_REMOTE_CI_STATUS_MISSING" for row in failures)


def test_commit_message_file_uses_same_subject_rule(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("修复：整理提交信息格式\n\n正文不参与标题校验。\n", encoding="utf-8")

    report = gate.build_commit_message_report(message)

    assert report["status"] == "passed"
