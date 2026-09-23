from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_migration_runner_import_avoids_retention_and_token_services() -> None:
    probe = """
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from host_brain.audit.migration_runner import SQLiteMigrationRunner

assert "host_brain.audit.retention" not in sys.modules
assert "host_brain.governor.tokens" not in sys.modules

with TemporaryDirectory() as directory:
    result = SQLiteMigrationRunner(
        db_path=Path(directory) / "cleanup-audit.sqlite3"
    ).apply_migrations(
        request_id="cleanup-audit-import-boundary",
        ts_ms=2_100_000_000_000,
    )
    assert result.success, result.as_dict()

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
