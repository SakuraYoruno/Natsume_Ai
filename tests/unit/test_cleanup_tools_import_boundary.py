from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_tool_contract_import_avoids_broker_audit_and_token_services() -> None:
    probe = """
import sys

from host_brain.tools.contracts import ToolSpec

tool = ToolSpec(
    tool_id="cleanup.tool",
    display_name="Cleanup tool",
    description="import boundary probe",
    parameters={"type": "object"},
)
assert tool.to_llm_schema()["name"] == "cleanup.tool"
assert "host_brain.tools.broker" not in sys.modules
assert "host_brain.audit.retention" not in sys.modules
assert "host_brain.governor.tokens" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", probe],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
