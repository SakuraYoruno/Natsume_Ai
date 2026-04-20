"""Prompt 模板 Lint 契约测试（OPT-6）.

背景：docs/35_quality/OPTIMIZATION_PLAN_20260420.md §10 / OPT-6 要求把
scripts/lint_prompts.py 纳入 CI Gate。本契约测试：

  1. T-OPT6-01：扫描仓库内 prompts/templates 不得出现 ERROR 级别问题。
  2. T-OPT6-02：给定一个人造的坏样本（未闭合占位符），lint 脚本必须返回非零退出码。
  3. T-OPT6-03：给定一个人造的合法样本，lint 脚本必须退出码为 0。
  4. T-OPT6-04：template_id 重复能被 R3 识别。

运行：pytest tests/contract/test_prompt_lint_contract.py -v
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LINT_SCRIPT = ROOT / "scripts" / "lint_prompts.py"
PROMPTS_ROOT = ROOT / "prompts" / "templates"


def _load_lint_module() -> Any:
    import sys

    spec = importlib.util.spec_from_file_location("lint_prompts", LINT_SCRIPT)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot load lint module: {LINT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    # Python 3.11 的 @dataclass 在装饰时会访问 sys.modules[cls.__module__]，
    # 因此必须先把模块注册进 sys.modules 再 exec_module，否则会 AttributeError。
    sys.modules["lint_prompts"] = module
    spec.loader.exec_module(module)
    return module


class TestPromptLintContract(unittest.TestCase):
    """OPT-6 契约测试：scripts/lint_prompts.py 与 prompts/templates/ 的约束。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.lint = _load_lint_module()

    # -----------------------------------------------------------------
    # T-OPT6-01：仓库现网模板必须通过 lint（允许 warning）
    # -----------------------------------------------------------------
    def test_t_opt6_01_repo_prompts_lint_clean(self) -> None:
        report = self.lint.lint_paths([PROMPTS_ROOT])
        errors = [f for f in report.findings if f.severity == "error"]
        self.assertEqual(
            [],
            [f.as_dict() for f in errors],
            msg=(
                "prompts/templates 出现 ERROR 级别 lint 问题，"
                "请修复后再提交（详见 docs/35_quality/OPTIMIZATION_PLAN_20260420.md §10）。"
            ),
        )

    # -----------------------------------------------------------------
    # T-OPT6-02：未闭合占位符 → R2 error
    # -----------------------------------------------------------------
    def test_t_opt6_02_unclosed_placeholder_detected(self, tmp_path: Any = None) -> None:
        tmp_dir = self._make_tmp_dir("unclosed")
        (tmp_dir / "bad.yaml").write_text(
            (
                "templates:\n"
                "- template_id: opt6_test_unclosed\n"
                "  default_content:\n"
                "    text: 'hello {user_name'\n"
            ),
            encoding="utf-8",
        )
        report = self.lint.lint_paths([tmp_dir])
        self.assertFalse(report.ok, "未闭合占位符必须被识别为错误")
        codes = {(f.rule, f.severity) for f in report.findings}
        self.assertIn(("R2", "error"), codes)

    # -----------------------------------------------------------------
    # T-OPT6-03：合法样本 → OK
    # -----------------------------------------------------------------
    def test_t_opt6_03_valid_sample_ok(self) -> None:
        tmp_dir = self._make_tmp_dir("valid")
        (tmp_dir / "good.yaml").write_text(
            (
                "templates:\n"
                "- template_id: opt6_test_valid\n"
                "  default_content:\n"
                "    text: 'hello {user_name}'\n"
                "    nested:\n"
                "      greeting: '{{escaped}} and {real_var}'\n"
            ),
            encoding="utf-8",
        )
        report = self.lint.lint_paths([tmp_dir])
        errors = [f for f in report.findings if f.severity == "error"]
        self.assertEqual([], [f.as_dict() for f in errors])
        self.assertTrue(report.ok)

    # -----------------------------------------------------------------
    # T-OPT6-04：template_id 重复 → R3 error
    # -----------------------------------------------------------------
    def test_t_opt6_04_duplicate_template_id_detected(self) -> None:
        tmp_dir = self._make_tmp_dir("dup")
        (tmp_dir / "a.yaml").write_text(
            (
                "templates:\n"
                "- template_id: opt6_dup_id\n"
                "  default_content: {text: 'a'}\n"
            ),
            encoding="utf-8",
        )
        (tmp_dir / "b.yaml").write_text(
            (
                "templates:\n"
                "- template_id: opt6_dup_id\n"
                "  default_content: {text: 'b'}\n"
            ),
            encoding="utf-8",
        )
        report = self.lint.lint_paths([tmp_dir])
        self.assertFalse(report.ok)
        self.assertTrue(
            any(f.rule == "R3" and f.severity == "error" for f in report.findings),
            msg="R3 重复 template_id 规则未生效",
        )

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------
    def _make_tmp_dir(self, tag: str) -> Path:
        import tempfile

        # 用 TestCase 级别的 cleanup：每个测试独立目录，测试退出自动清理。
        tmp = Path(tempfile.mkdtemp(prefix=f"opt6_prompt_lint_{tag}_"))
        self.addCleanup(self._rm_tree, tmp)
        return tmp

    @staticmethod
    def _rm_tree(path: Path) -> None:
        import shutil

        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
