from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
HOST_BRAIN_ROOT = ROOT / "host_brain"
APPLICATION_IMPORT = "host_brain.app"
EXEMPT_TOP_LEVEL = frozenset(
    {"app", "composition", "release", "tools", "validation"}
)


@dataclass(frozen=True)
class ImportViolation:
    source: str
    target: str
    line: int


def _python_files(root: Path) -> Iterable[Path]:
    yield from sorted(root.rglob("*.py"))


def _import_targets(tree: ast.AST) -> Iterable[tuple[str, int]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, node.lineno


def _is_policy_boundary(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return path.name == "__main__.py" or relative.parts[0] in EXEMPT_TOP_LEVEL


def find_reverse_dependency_violations(
    root: Path = HOST_BRAIN_ROOT,
) -> list[ImportViolation]:
    violations: list[ImportViolation] = []
    for path in _python_files(root):
        if _is_policy_boundary(path, root):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for target, line in _import_targets(tree):
            if target == APPLICATION_IMPORT or target.startswith(f"{APPLICATION_IMPORT}."):
                violations.append(
                    ImportViolation(
                        source=path.relative_to(ROOT).as_posix(),
                        target=target,
                        line=line,
                    )
                )
    return violations


def _source_package(path: Path, root: Path) -> str:
    return path.relative_to(root).parts[0].removesuffix(".py")


def _target_package(target: str) -> str | None:
    parts = target.split(".")
    if len(parts) < 2 or parts[0] != "host_brain":
        return None
    return parts[1]


def build_package_graph(root: Path = HOST_BRAIN_ROOT) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for path in _python_files(root):
        if _is_policy_boundary(path, root):
            continue
        source = _source_package(path, root)
        graph.setdefault(source, set())
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for target, _line in _import_targets(tree):
            destination = _target_package(target)
            if destination is not None and destination != source:
                graph[source].add(destination)
                graph.setdefault(destination, set())
    return graph


def strongly_connected_components(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indices[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)

        for destination in sorted(graph[node]):
            if destination not in indices:
                visit(destination)
                lowlinks[node] = min(lowlinks[node], lowlinks[destination])
            elif destination in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[destination])

        if lowlinks[node] == indices[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(graph):
        if node not in indices:
            visit(node)
    return sorted(components, key=lambda item: (-len(item), item))


def package_graph_metrics(root: Path = HOST_BRAIN_ROOT) -> dict[str, object]:
    graph = build_package_graph(root)
    cyclic = [component for component in strongly_connected_components(graph) if len(component) > 1]
    return {
        "nodes": len(graph),
        "edges": sum(len(destinations) for destinations in graph.values()),
        "cyclic_scc_count": len(cyclic),
        "cyclic_package_count": sum(map(len, cyclic)),
        "largest_scc_size": max(map(len, cyclic), default=0),
        "cyclic_sccs": cyclic,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject production dependencies on the application composition root."
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()

    violations = find_reverse_dependency_violations()
    report = {
        "policy_exempt_top_level": sorted(EXEMPT_TOP_LEVEL),
        "reverse_dependency_violations": [asdict(item) for item in violations],
        "package_graph": package_graph_metrics(),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"reverse dependency violations: {len(violations)}")
        for item in violations:
            print(f"  {item.source}:{item.line} -> {item.target}")
        print(json.dumps(report["package_graph"], ensure_ascii=False, indent=2))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
