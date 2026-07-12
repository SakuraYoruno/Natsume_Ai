from __future__ import annotations

import ast
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "host_brain"
EXCLUDED_PARTS = {"audit", "persistence", "__pycache__"}

# Neo4j's migration loader handles Cypher CREATE INDEX/CONSTRAINT statements;
# it is not an embedded SQLite schema authority.
EXTERNAL_DDL_ADAPTERS = {
    Path("host_brain/bond/neo4j_client.py"),
}

DDL = re.compile(
    r"(?im)^\s*(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX|TRIGGER)\b"
)
SQL_CONNECTION_NAMES = {"conn", "connection", "cursor", "_conn", "db"}


def _production_python_files() -> list[Path]:
    return [
        path
        for path in HOST.rglob("*.py")
        if not EXCLUDED_PARTS.intersection(path.relative_to(HOST).parts)
    ]


def _receiver_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def test_aw03_production_layers_have_no_embedded_sqlite_authority() -> None:
    violations: list[str] = []
    for path in _production_python_files():
        relative = path.relative_to(ROOT)
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=str(relative))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"_conn", "_write_lock"}:
                violations.append(f"{relative}:{node.lineno}: private SQLite authority")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                receiver = _receiver_name(node.func.value)
                if node.func.attr == "connect" and receiver in {"sqlite3", "_sqlite3"}:
                    violations.append(f"{relative}:{node.lineno}: sqlite3.connect")
                if (
                    node.func.attr in {"execute", "executemany", "executescript"}
                    and receiver in SQL_CONNECTION_NAMES
                ):
                    violations.append(
                        f"{relative}:{node.lineno}: connection.{node.func.attr}"
                    )
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and DDL.search(node.value)
                and relative not in EXTERNAL_DDL_ADAPTERS
            ):
                violations.append(f"{relative}:{node.lineno}: inline SQLite DDL")

    assert violations == [], "\n".join(violations)
