"""Read-only tracked-source census; generated reports are not deletion authority."""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_ROOTS = {"host_brain", "edge_client", "natsume_contracts", "desktop_pet"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8")


def body_without_docstring(node):
    body = node.body
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return body[1:]
    return body


def scan() -> dict:
    roots = defaultdict(lambda: {"files": 0, "lines": 0})
    packages = defaultdict(lambda: {"files": 0, "lines": 0, "python_lines": 0})
    metrics = Counter()
    largest, long_args, gates, suffixes, dead_tails = [], [], [], [], []
    wrappers, shapes, initializers, errors = [], [], [], []
    trees = {}
    texts = {}
    tracked = [p for p in git("ls-files", "-z").split("\0") if p]
    for name in tracked:
        path = ROOT / name
        try:
            raw = path.read_bytes()
            if b"\0" in raw:
                metrics["binary_files_excluded"] += 1
                continue
            text = raw.decode("utf-8-sig")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append({"path": name, "error": type(exc).__name__})
            continue
        lines = len(text.splitlines())
        root = name.split("/")[0] if "/" in name else "(root)"
        roots[root]["files"] += 1
        roots[root]["lines"] += lines
        if root == "host_brain":
            package = name.split("/")[1]
            packages[package]["files"] += 1
            packages[package]["lines"] += lines
            if path.suffix == ".py":
                packages[package]["python_lines"] += lines
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(text, filename=name)
        except SyntaxError as exc:
            errors.append({"path": name, "error": str(exc)})
            continue
        trees[name], texts[name] = tree, text
        metrics["python_files"] += 1
        metrics["python_physical_lines"] += lines
        if root not in RUNTIME_ROOTS:
            if name.startswith("tests/") and any(token in text for token in (
                "inspect.getsource", "__bases__", "ast.dump", "splitlines())",
            )):
                shapes.append({"path": name, "lines": lines})
            continue
        largest.append({"path": name, "lines": lines})
        if name.endswith("/__init__.py"):
            imports = [ast.unparse(n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
            if imports:
                initializers.append({"path": name, "lines": lines, "top_level_imports": len(imports), "imports": imports})
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
            count = len([arg for arg in args if arg.arg not in {"self", "cls"}])
            count += bool(node.args.vararg) + bool(node.args.kwarg)
            item = {"path": name, "symbol": node.name, "line": node.lineno,
                    "end": node.end_lineno, "lines": node.end_lineno - node.lineno + 1}
            if root == "host_brain":
                for limit in (10, 15, 20):
                    metrics[f"host_functions_parameters_ge_{limit}"] += count >= limit
                if count >= 15:
                    long_args.append({**item, "parameters": count})
            body = body_without_docstring(node)
            if node.name.startswith("_legacy_") and node.name.endswith("retired"):
                gates.append(item)
            if body and isinstance(body[0], (ast.Return, ast.Raise)) and len(body) > 1:
                dead_tails.append({**item, "suffix_start": body[1].lineno})
            if body and isinstance(body[0], ast.If) and len(body) > 1:
                guard = body[0]
                condition = ast.unparse(guard.test)
                if ("retired" in condition or "uses_canonical_affective_life" in condition):
                    if guard.body and isinstance(guard.body[-1], (ast.Return, ast.Raise)):
                        suffixes.append({**item, "condition": condition,
                                         "suffix_start": body[1].lineno,
                                         "suffix_lines": node.end_lineno - body[1].lineno + 1})
            if root == "host_brain" and len(body) == 1 and isinstance(body[0], (ast.Return, ast.Expr)):
                value = body[0].value
                if isinstance(value, ast.Await):
                    value = value.value
                if isinstance(value, ast.Call):
                    wrappers.append({**item, "callee": ast.unparse(value.func),
                                     "decorators": [ast.unparse(d) for d in node.decorator_list]})
    specs = trees.get("host_brain/pentest/tools/tool_specs.py")
    registered = sorted({n.args[0].value for n in ast.walk(specs)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_register"
        and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)}) if specs else []
    executors = trees.get("host_brain/pentest/tools/tool_executors.py")
    executor_literals = sorted({k.value for n in ast.walk(executors) if isinstance(n, ast.Dict)
        for k in n.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
        and k.value.startswith("pentest_")}) if executors else []
    return {
        "schema": "natsume.cleanup.census.v1", "head": git("rev-parse", "HEAD").strip(),
        "branch": git("branch", "--show-current").strip(),
        "method": "git tracked UTF-8 physical lines; AST candidates are not proven dead; no runtime imports",
        "limitations": ["static imports cannot establish dynamic reachability", "conditional tails are live in noncanonical compositions", "executor string presence is not a wiring or execution test", "docstrings/comments included in physical lines", "new untracked plan artifacts excluded"],
        "totals": dict(metrics), "roots": dict(sorted(roots.items())),
        "host_packages": dict(sorted(packages.items(), key=lambda pair: -pair[1]["lines"])),
        "largest_runtime_python": sorted(largest, key=lambda item: -item["lines"])[:25],
        "long_signatures": sorted(long_args, key=lambda item: -item["parameters"]),
        "retirement_gates": gates, "conditional_tail_candidates": suffixes,
        "unconditional_tail_candidates": dead_tails,
        "one_call_candidates_count": len(wrappers),
        "one_call_candidate_lines": sum(item["lines"] for item in wrappers),
        "one_call_examples": sorted(wrappers, key=lambda item: -item["lines"])[:30],
        "source_shape_test_candidates": shapes,
        "wide_initializers": sorted(initializers, key=lambda item: -item["top_level_imports"])[:20],
        "pentest_static_table": {"registered_ids": registered,
            "missing_executor_literal_candidates": sorted(set(registered) - set(executor_literals))},
        "errors": errors,
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write a generated JSON report inside this plan directory.")
    args = parser.parse_args()
    result = scan()
    if args.output:
        target = args.output.resolve()
        if target.parent != Path(__file__).resolve().parent:
            parser.error("output must be directly inside the plan directory")
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {key: result[key] for key in ("head", "totals", "roots", "one_call_candidates_count", "one_call_candidate_lines", "errors")}
    summary.update(conditional_tail_methods=len(result["conditional_tail_candidates"]),
                   conditional_tail_lines=sum(x["suffix_lines"] for x in result["conditional_tail_candidates"]),
                   unconditional_tails=result["unconditional_tail_candidates"],
                   registered_pentest_tools=len(result["pentest_static_table"]["registered_ids"]),
                   missing_executor_literal_candidates=result["pentest_static_table"]["missing_executor_literal_candidates"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
