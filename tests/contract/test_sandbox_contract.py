"""T-Sandbox-01: DRAFT_CODE sandbox execution with isolation, security, and audit stability.

验证：
- 正常 DRAFT_CODE 在沙箱中执行并产出结构化结果；
- 超时场景强制终止且 reason_code 稳定；
- 恶意代码（非零退出码）输出 EXEC_FAILED；
- 资源超限（代码体积超限）稳定拒绝；
- 沙箱不可用时稳定返回 SANDBOX_UNAVAILABLE；
- 不支持的语言稳定拒绝；
- 执行结果写入 sandbox_exec_log 且可按 request_id/sandbox_id/evidence_ref 回溯；
- 沙箱执行不影响主进程状态且不引入旁路执行路径；
- 现有 T-39/T-40 无回归。
"""

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from host_brain.airlock.quarantine import (
    CLASS_DRAFT_CODE,
    CLASS_DRAFT_INSTRUCTION,
    CLASS_FACT,
    quarantine_ingress,
)
from host_brain.airlock.sandbox import (
    SANDBOX_STATUS_EXEC_FAILED,
    SANDBOX_STATUS_OK,
    SANDBOX_STATUS_RESOURCE_EXCEEDED,
    SANDBOX_STATUS_TIMEOUT,
    SANDBOX_STATUS_UNAVAILABLE,
    SandboxConfig,
    SandboxExecResult,
    SandboxExecutor,
)
from host_brain.airlock.sanitizer import gate_egress_payload
from host_brain.audit.sqlite_ledger import SQLiteLedger
from host_brain.core.errors import (
    AIRLOCK_BLOCKED_EGRESS,
    AIRLOCK_BLOCKED_INGRESS,
    SANDBOX_CONTAINER_UNAVAILABLE as E_SANDBOX_CONTAINER_UNAVAILABLE,
    SANDBOX_EXEC_FAILED as E_SANDBOX_EXEC_FAILED,
    SANDBOX_RESOURCE_EXCEEDED as E_SANDBOX_RESOURCE_EXCEEDED,
    SANDBOX_TIMEOUT as E_SANDBOX_TIMEOUT,
    SANDBOX_UNAVAILABLE as E_SANDBOX_UNAVAILABLE,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "airlock"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


class TestSandboxNormalExecution(unittest.TestCase):
    """正常 DRAFT_CODE 沙箱执行。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_003_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_normal_code_execution(self) -> None:
        """简单 Python 代码在沙箱中执行成功。"""
        fixture = _load_json(FIXTURES / "good" / "sandbox_normal_exec.json")
        config = SandboxConfig(
            timeout_s=10,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code=fixture["payload"]["code"],
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            language=fixture["payload"]["language"],
            ts_ms=self.now_ms,
        )

        self.assertEqual(SANDBOX_STATUS_OK, result.status)
        self.assertEqual(0, result.exit_code)
        self.assertIn("result=5", result.stdout)
        self.assertIsNone(result.reason_code)
        self.assertTrue(result.evidence_ref.startswith("sandbox://"))
        self.assertTrue(result.code_digest.startswith("sha256:"))
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_normal_exec_audit_trail(self) -> None:
        """正常执行写入 sandbox_exec_log 且可回溯。"""
        fixture = _load_json(FIXTURES / "good" / "sandbox_normal_exec.json")
        config = SandboxConfig(
            timeout_s=10,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code=fixture["payload"]["code"],
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            language=fixture["payload"]["language"],
            ts_ms=self.now_ms,
        )

        rows = self.ledger.sandbox_exec_log
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(fixture["request_id"], row["request_id"])
        self.assertEqual(fixture["intent_id"], row["intent_id"])
        self.assertEqual(result.sandbox_id, row["sandbox_id"])
        self.assertEqual(SANDBOX_STATUS_OK, row["status"])
        self.assertEqual(0, row["exit_code"])
        self.assertIsNone(row["reason_code"])
        self.assertEqual(result.evidence_ref, row["evidence_ref"])
        self.assertTrue(row["code_digest"].startswith("sha256:"))
        self.assertGreaterEqual(row["duration_ms"], 0)

    def test_result_json_serializable(self) -> None:
        """执行结果可 JSON 序列化。"""
        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code="print('hello')\n",
            request_id="req_json_test",
            ts_ms=self.now_ms,
        )
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        parsed = json.loads(serialized)
        self.assertEqual(SANDBOX_STATUS_OK, parsed["status"])

    def test_evidence_ref_unique(self) -> None:
        """每次执行的 evidence_ref 唯一。"""
        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        r1 = executor.execute(code="print(1)\n", request_id="r1", ts_ms=self.now_ms)
        r2 = executor.execute(code="print(2)\n", request_id="r2", ts_ms=self.now_ms + 1)
        self.assertNotEqual(r1.evidence_ref, r2.evidence_ref)
        self.assertNotEqual(r1.sandbox_id, r2.sandbox_id)


class TestSandboxTimeout(unittest.TestCase):
    """超时场景。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_003_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_timeout_killed(self) -> None:
        """无限循环脚本被超时强杀。"""
        fixture = _load_json(FIXTURES / "bad" / "sandbox_timeout_exec.json")
        config = SandboxConfig(
            timeout_s=2,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code=fixture["payload"]["code"],
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            language=fixture["payload"]["language"],
            ts_ms=self.now_ms,
        )

        self.assertEqual(SANDBOX_STATUS_TIMEOUT, result.status)
        self.assertEqual(E_SANDBOX_TIMEOUT, result.reason_code)
        self.assertIsNone(result.exit_code)
        self.assertIn("timed out", result.stderr)
        self.assertEqual("popen_communicate", result.resource_usage.get("timeout_enforcement"))
        self.assertIn("platform", result.resource_usage)

    def test_timeout_audit(self) -> None:
        """超时场景审计记录。"""
        config = SandboxConfig(
            timeout_s=1,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        executor.execute(
            code="import time\nwhile True:\n    time.sleep(0.1)\n",
            request_id="req_timeout_audit",
            ts_ms=self.now_ms,
        )
        rows = self.ledger.sandbox_exec_log
        self.assertEqual(1, len(rows))
        self.assertEqual(SANDBOX_STATUS_TIMEOUT, rows[0]["status"])
        self.assertEqual(E_SANDBOX_TIMEOUT, rows[0]["reason_code"])


class TestSandboxMaliciousCode(unittest.TestCase):
    """恶意代码 / 非零退出码场景。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_003_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_nonzero_exit_code(self) -> None:
        """非零退出码被标记为 EXEC_FAILED。"""
        fixture = _load_json(FIXTURES / "bad" / "sandbox_malicious_code.json")
        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code=fixture["payload"]["code"],
            request_id=fixture["request_id"],
            intent_id=fixture["intent_id"],
            language=fixture["payload"]["language"],
            ts_ms=self.now_ms,
        )

        self.assertEqual(SANDBOX_STATUS_EXEC_FAILED, result.status)
        self.assertEqual(E_SANDBOX_EXEC_FAILED, result.reason_code)
        self.assertEqual(42, result.exit_code)

    def test_syntax_error_exec_failed(self) -> None:
        """语法错误的代码 → EXEC_FAILED。"""
        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code="def foo(\n",  # 语法错误
            request_id="req_syntax_err",
            ts_ms=self.now_ms,
        )
        self.assertEqual(SANDBOX_STATUS_EXEC_FAILED, result.status)
        self.assertEqual(E_SANDBOX_EXEC_FAILED, result.reason_code)
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("SyntaxError", result.stderr)

    def test_exception_exec_failed(self) -> None:
        """运行时异常的代码 → EXEC_FAILED。"""
        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code="raise ValueError('test error')\n",
            request_id="req_runtime_err",
            ts_ms=self.now_ms,
        )
        self.assertEqual(SANDBOX_STATUS_EXEC_FAILED, result.status)
        self.assertNotEqual(0, result.exit_code)
        self.assertIn("ValueError", result.stderr)


class TestSandboxResourceExceeded(unittest.TestCase):
    """资源超限场景。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_003_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_code_size_exceeded(self) -> None:
        """代码体积超限 → RESOURCE_EXCEEDED。"""
        config = SandboxConfig(
            timeout_s=5,
            max_code_bytes=64,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        big_code = "x = 1\n" * 50  # 远超 64 bytes
        result = executor.execute(
            code=big_code,
            request_id="req_resource_exceeded",
            ts_ms=self.now_ms,
        )

        self.assertEqual(SANDBOX_STATUS_RESOURCE_EXCEEDED, result.status)
        self.assertEqual(E_SANDBOX_RESOURCE_EXCEEDED, result.reason_code)
        self.assertIn("exceeds limit", result.stderr)
        self.assertIsNone(result.exit_code)
        self.assertIn("code_bytes", result.resource_usage)
        self.assertIn("max_code_bytes", result.resource_usage)

    def test_resource_exceeded_audit(self) -> None:
        """资源超限的审计记录。"""
        config = SandboxConfig(
            timeout_s=5,
            max_code_bytes=32,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        executor.execute(
            code="x = 1\n" * 20,
            request_id="req_res_audit",
            ts_ms=self.now_ms,
        )
        rows = self.ledger.sandbox_exec_log
        self.assertEqual(1, len(rows))
        self.assertEqual(SANDBOX_STATUS_RESOURCE_EXCEEDED, rows[0]["status"])
        self.assertEqual(E_SANDBOX_RESOURCE_EXCEEDED, rows[0]["reason_code"])


class TestSandboxUnavailable(unittest.TestCase):
    """沙箱不可用场景。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_003_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_disabled_sandbox(self) -> None:
        """沙箱被禁用 → SANDBOX_UNAVAILABLE。"""
        config = SandboxConfig(enabled=False)
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code="print('hello')\n",
            request_id="req_disabled",
            ts_ms=self.now_ms,
        )

        self.assertEqual(SANDBOX_STATUS_UNAVAILABLE, result.status)
        self.assertEqual(E_SANDBOX_UNAVAILABLE, result.reason_code)
        self.assertIsNone(result.exit_code)

    def test_unsupported_language(self) -> None:
        """不支持的语言 → SANDBOX_UNAVAILABLE。"""
        config = SandboxConfig(
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code="console.log('hello')",
            request_id="req_js",
            language="javascript",
            ts_ms=self.now_ms,
        )

        self.assertEqual(SANDBOX_STATUS_UNAVAILABLE, result.status)
        self.assertEqual(E_SANDBOX_UNAVAILABLE, result.reason_code)
        self.assertIn("unsupported language", result.stderr)

    def test_prod_env_requires_container_isolation(self) -> None:
        config = SandboxConfig(
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        with patch.dict("os.environ", {"Natsume_ENV": "production"}, clear=False):
            result = executor.execute(
                code="print('hello')\n",
                request_id="req_prod_container",
                ts_ms=self.now_ms,
            )

        self.assertEqual(SANDBOX_STATUS_UNAVAILABLE, result.status)
        self.assertEqual(E_SANDBOX_CONTAINER_UNAVAILABLE, result.reason_code)
        self.assertIn("container isolation required", result.stderr)

    def test_unavailable_audit(self) -> None:
        """不可用场景也写审计日志。"""
        config = SandboxConfig(enabled=False)
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        executor.execute(
            code="print(1)\n",
            request_id="req_unavail_audit",
            ts_ms=self.now_ms,
        )
        rows = self.ledger.sandbox_exec_log
        self.assertEqual(1, len(rows))
        self.assertEqual(SANDBOX_STATUS_UNAVAILABLE, rows[0]["status"])
        self.assertEqual(E_SANDBOX_UNAVAILABLE, rows[0]["reason_code"])


class TestSandboxSecurityBoundary(unittest.TestCase):
    """安全边界测试：沙箱执行不影响主进程状态。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_003_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_no_main_process_side_effect(self) -> None:
        """沙箱中的全局变量修改不影响主进程。"""
        import os
        marker = f"SANDBOX_TEST_{self.now_ms}"
        code = f"import os\nos.environ['{marker}'] = 'leaked'\n"
        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code=code,
            request_id="req_no_side_effect",
            ts_ms=self.now_ms,
        )
        self.assertEqual(SANDBOX_STATUS_OK, result.status)
        # 主进程不受影响
        self.assertNotIn(marker, os.environ)

    def test_temp_dir_cleaned_up(self) -> None:
        """沙箱临时目录在执行后被清理。"""
        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code="import os\nprint(os.getcwd())\n",
            request_id="req_cleanup",
            ts_ms=self.now_ms,
        )
        self.assertEqual(SANDBOX_STATUS_OK, result.status)
        # cwd 在 stdout 中会是临时目录路径
        tmpdir_path = result.stdout.strip()
        if tmpdir_path:
            from pathlib import Path
            self.assertFalse(Path(tmpdir_path).exists(), "临时目录应该已被清理")

    def test_quarantine_then_sandbox_integration(self) -> None:
        """DRAFT_CODE 先经 quarantine 分类，再送入沙箱执行。"""
        code = "def hello():\n    return 42\nprint(hello())\n"
        from host_brain.airlock.quarantine import classify_ingress, CLASS_DRAFT_CODE
        classification = classify_ingress(code)
        self.assertEqual(CLASS_DRAFT_CODE, classification)

        config = SandboxConfig(
            timeout_s=5,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        result = executor.execute(
            code=code,
            request_id="req_quarantine_then_sandbox",
            ts_ms=self.now_ms,
        )
        self.assertEqual(SANDBOX_STATUS_OK, result.status)
        self.assertIn("42", result.stdout)


class TestSandboxOutputTruncation(unittest.TestCase):
    """输出截断测试。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_003_600_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_stdout_truncation(self) -> None:
        """超大 stdout 被截断。"""
        config = SandboxConfig(
            timeout_s=5,
            max_stdout_bytes=128,
            python_executable=sys.executable,
        )
        executor = SandboxExecutor(config=config, ledger=self.ledger)
        # 产生大量输出
        code = "print('A' * 1000)\n"
        result = executor.execute(
            code=code,
            request_id="req_truncate",
            ts_ms=self.now_ms,
        )
        self.assertEqual(SANDBOX_STATUS_OK, result.status)
        self.assertTrue(result.truncated_stdout)
        self.assertLessEqual(len(result.stdout.encode("utf-8")), 128 + 10)  # 允许截断点误差


class TestExistingT39T40NoRegression(unittest.TestCase):
    """确认现有 T-39/T-40 测试口径无回归。"""

    def setUp(self) -> None:
        self.now_ms = 1_800_001_200_000
        self.ledger = SQLiteLedger(db_path=":memory:")

    def test_t39_egress_still_works(self) -> None:
        """T-39：缺 sanitization_report 的出站仍被拒绝。"""
        fixture = _load_json(FIXTURES / "bad" / "egress_missing_report.json")
        blocked = gate_egress_payload(
            payload=fixture["payload"],
            sanitization_report_json=None,
            request_id=fixture["request_id"],
            ledger=self.ledger,
            ts_ms=1000,
        )
        self.assertFalse(blocked.allowed)
        self.assertEqual(AIRLOCK_BLOCKED_EGRESS, blocked.reason_code)

    def test_t40_ingress_still_works(self) -> None:
        """T-40：入站内容仍被隔离且不直接执行。"""
        from host_brain.airlock.quarantine import CLASS_DRAFT_INSTRUCTION
        draft_instruction = (FIXTURES / "bad" / "ingress_direct_exec.txt").read_text(encoding="utf-8-sig")
        result = quarantine_ingress(
            draft_instruction,
            request_id="req_t40_regression",
            ledger=self.ledger,
            ts_ms=2000,
        )
        self.assertEqual(CLASS_DRAFT_INSTRUCTION, result.classification)
        self.assertTrue(result.draft_only)
        self.assertFalse(result.allow_execute)
        self.assertEqual(AIRLOCK_BLOCKED_INGRESS, result.reason_code)


if __name__ == "__main__":
    unittest.main()
