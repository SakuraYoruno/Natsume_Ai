"""Contract tests conftest - auto-configure test environment.

Ensures NATSUME_AUDIT_DB uses in-memory SQLite for test isolation,
regardless of the host environment configuration.
"""
import os

# Force in-memory audit DB for all contract tests (must run before any import)
os.environ.setdefault("NATSUME_AUDIT_DB", ":memory:")
