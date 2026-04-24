import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC_LINT_SCRIPT = ROOT / "tools" / "doc_lint" / "doc_audience_lint.py"
DELIVERY_GATE_SCRIPT = ROOT / "tools" / "doc_lint" / "delivery_gate.py"
NO_RULE_DEGRADE_SCRIPT = ROOT / "scripts" / "ci" / "check_no_rule_degrade.py"

CI_FIXTURES = ROOT / "tests" / "fixtures" / "ci"
DOC_BAD = CI_FIXTURES / "doc_gate" / "bad"
DOC_GOOD = CI_FIXTURES / "doc_gate" / "good" / "valid_headers.md"
STATIC_BAD_ROOT = CI_FIXTURES / "static_scan" / "bad"

# ─────────────────────────────────────────────────────
# OPTIMIZATION-PLAN-20260420 / OPT-10：shared 模块体积 lint
# ─────────────────────────────────────────────────────
# 硬规则：host_brain/ 下文件名以 _shared.py / _common.py / _utils.py 结尾的
# 模块不得超过 SHARED_MODULE_MAX_LINES 行。防止"垃圾桶文件"反复膨胀。
#
# 历史遗留豁免：
#   - host_brain/app/agent_runtime_shared.py（ARCH_FIX_PLAN P2-3 待执行项，
#     将在 OPT-10 专项 PR 中按职责拆分）
# 新文件落入以上后缀命名时，必须在此阈值内，否则 CI 红。
SHARED_MODULE_MAX_LINES = 500
SHARED_MODULE_SUFFIXES = ("_shared.py", "_common.py", "_utils.py")
SHARED_MODULE_LEGACY_EXEMPT = {
    "host_brain/app/agent_runtime_shared.py",
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCIGateContract(unittest.TestCase):
    def setUp(self) -> None:
        self.doc_lint = _load_module(DOC_LINT_SCRIPT, "doc_audience_lint")
        self.delivery_gate = _load_module(DELIVERY_GATE_SCRIPT, "delivery_gate")
        self.no_rule_degrade = _load_module(NO_RULE_DEGRADE_SCRIPT, "check_no_rule_degrade")

    def test_t_ci_01_doc_gate_rejects_missing_headers(self) -> None:
        failures = self.doc_lint.scan_markdown_files(
            root=ROOT,
            paths=[DOC_BAD / "missing_audience.md", DOC_BAD / "missing_title.md"],
        )
        self.assertEqual(2, len(failures))
        codes = {row["reason_code"] for row in failures}
        self.assertIn("DOC_AUDIENCE_INVALID", codes)
        self.assertIn("DOC_TITLE_HEADER_MISSING", codes)
        for row in failures:
            self.assertEqual("doc_gate", row["check_id"])
            self.assertIsInstance(row["failed_artifact"], str)

    def test_t_ci_01_doc_gate_accepts_valid_headers(self) -> None:
        failures = self.doc_lint.scan_markdown_files(root=ROOT, paths=[DOC_GOOD])
        self.assertEqual([], failures)

    def test_t_ci_01_unified_gate_report_shape(self) -> None:
        report = self.delivery_gate.run_delivery_gate(
            root=ROOT,
            checks=["doc_gate", "static_scan"],
            doc_paths=[DOC_BAD / "missing_audience.md"],
            static_scan_root=STATIC_BAD_ROOT,
            include_tests_in_static_scan=True,
        )
        self.assertEqual("delivery-gate", report["job_id"])
        self.assertEqual("failed", report["status"])
        self.assertEqual(2, report["summary"]["total_checks"])
        self.assertEqual(2, report["summary"]["failed_checks"])
        self.assertGreaterEqual(report["summary"]["failure_count"], 2)
        self.assertGreaterEqual(len(report["failures"]), 2)
        for row in report["failures"]:
            self.assertEqual("delivery-gate", row["job_id"])
            self.assertIn(row["check_id"], {"doc_gate", "static_scan"})
            self.assertIsInstance(row["reason_code"], str)
            self.assertIsInstance(row["failed_artifact"], str)
        self.assertTrue(any(row["check_id"] == "doc_gate" for row in report["failures"]))
        self.assertTrue(any(row["check_id"] == "static_scan" for row in report["failures"]))

    def test_t_ci_01_no_rule_degrade_scan_rejects_hard_disable(self) -> None:
        failures = self.no_rule_degrade.scan_file(STATIC_BAD_ROOT / "no_rule_degrade_ci_example.py")
        self.assertTrue(any(row["reason_code"] == "USE_LLM_DISABLED" for row in failures))
        self.assertTrue(any(str(row["path"]).endswith("no_rule_degrade_ci_example.py") for row in failures))

    def test_t_ci_01_no_rule_degrade_scan_current_repo_clean(self) -> None:
        failures = self.no_rule_degrade.scan_repo(ROOT)
        self.assertEqual([], failures)


class TestSharedModuleLint(unittest.TestCase):
    """OPTIMIZATION-PLAN-20260420 / OPT-10：禁止 shared/common/utils 模块膨胀为垃圾桶文件。"""

    def _iter_shared_modules(self) -> list[Path]:
        host_brain_root = ROOT / "host_brain"
        matches: list[Path] = []
        for suffix in SHARED_MODULE_SUFFIXES:
            matches.extend(host_brain_root.rglob(f"*{suffix}"))
        return sorted(matches)

    def test_t_opt10_01_no_fat_shared_module(self) -> None:
        """新增的 _shared/_common/_utils 模块若超过 SHARED_MODULE_MAX_LINES 行则 CI 红。

        验收点（OPTIMIZATION_PLAN_20260420 §14 / OPT-10）：
          - 仅对 host_brain/ 生效（edge_client/ 与脚本目录暂不治理）；
          - 豁免 SHARED_MODULE_LEGACY_EXEMPT 中列出的历史遗留大文件；
          - 新文件落入豁免名单需要在本测试同步更新，避免无声纵容。
        """
        offenders: list[str] = []
        for path in self._iter_shared_modules():
            rel = path.relative_to(ROOT).as_posix()
            if rel in SHARED_MODULE_LEGACY_EXEMPT:
                continue
            try:
                lines = sum(1 for _ in path.open("r", encoding="utf-8"))
            except OSError as exc:  # pragma: no cover - defensive
                offenders.append(f"{rel}: unreadable ({exc})")
                continue
            if lines > SHARED_MODULE_MAX_LINES:
                offenders.append(f"{rel}: {lines} lines (> {SHARED_MODULE_MAX_LINES})")

        self.assertEqual(
            [],
            offenders,
            msg=(
                "以下 shared/common/utils 模块超过硬阈值 "
                f"{SHARED_MODULE_MAX_LINES} 行，请按职责拆分：\n  - "
                + "\n  - ".join(offenders)
                + "\n（参考 docs/35_quality/OPTIMIZATION_PLAN_20260420.md §14 OPT-10）"
            ),
        )

    def test_t_opt10_02_legacy_exemptions_still_exist(self) -> None:
        """豁免名单中的文件必须实际存在，防止名单腐化。"""
        missing = [
            rel
            for rel in SHARED_MODULE_LEGACY_EXEMPT
            if not (ROOT / rel).is_file()
        ]
        self.assertEqual(
            [],
            missing,
            msg=(
                "SHARED_MODULE_LEGACY_EXEMPT 中的遗留豁免路径不存在，"
                f"请移除：{missing}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
