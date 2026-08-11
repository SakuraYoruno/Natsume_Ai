#!/usr/bin/env python3
"""Prompt 模板静态 Lint（OPT-6 一期）.

历史背景：docs/_history/quality/OPTIMIZATION_PLAN_20260420.md §10 指出 prompts/templates/
下 YAML 已经按 OPT-6 规划落库，但缺少 CI 级静态校验。任一模板占位符拼写/闭合错
误运行时才发现，是 LLM 应用中最常见且最难定位的 bug。

一期实现三条务实规则（误报率接近 0、立刻产生价值）：

    R1  所有 YAML 必须可被 yaml.safe_load 解析。
    R2  每个 ``{...}`` 必须是合法 Python format 字段：
        - 不允许出现空 ``{}`` 或未闭合 ``{name`` ;
        - 转义花括号 ``{{ }}`` 正确处理；
        - 中文内容里的 ``{...}`` 同样被校验。
    R3  所有 YAML 合并后，``template_id`` 不得重复；否则 PromptTemplateStore
        会静默覆盖，造成难以定位的人设漂移。

未来二期（不在本文件内）再引入 "调用点变量 vs 模板占位符" 交叉比对。

用法::

    python scripts/lint_prompts.py                  # lint 整个 prompts/
    python scripts/lint_prompts.py prompts/foo.yaml # 指定文件
    python scripts/lint_prompts.py --json           # 机器可读输出

退出码：
    0  — 无问题
    1  — 发现至少一个 lint 错误
    2  — 内部错误（如无法解析 CLI 参数）
"""

from __future__ import annotations

import argparse
import json
import string
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]
except ImportError as exc:  # pragma: no cover - environment issue
    print(
        "scripts/lint_prompts.py 需要 PyYAML，请先 pip install pyyaml",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCAN_ROOT = REPO_ROOT / "prompts"


def _rel_path(path: Path) -> str:
    """返回相对仓库根的路径；路径在仓库外时退回 as_posix()。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


# ---------------------------------------------------------------------------
# 结果类型
# ---------------------------------------------------------------------------


@dataclass
class LintFinding:
    rule: str          # R1 / R2 / R3
    path: str          # 相对仓库根
    location: str      # YAML 内路径，如 templates[3].default_content.user_prompt_template
    message: str
    severity: str = "error"

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "path": self.path,
            "location": self.location,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class LintReport:
    findings: list[LintFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(f.severity == "error" for f in self.findings)

    def add(self, finding: LintFinding) -> None:
        self.findings.append(finding)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_count": sum(1 for f in self.findings if f.severity == "error"),
            "warn_count": sum(1 for f in self.findings if f.severity != "error"),
            "findings": [f.as_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# R1 / R3：YAML 结构与模板 ID 唯一性
# ---------------------------------------------------------------------------


def _iter_yaml_files(paths: Iterable[Path]) -> Iterator[Path]:
    for p in paths:
        if p.is_dir():
            yield from sorted(p.rglob("*.yaml"))
            yield from sorted(p.rglob("*.yml"))
        elif p.is_file() and p.suffix in (".yaml", ".yml"):
            yield p


def _load_yaml(path: Path, report: LintReport) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        report.add(
            LintFinding(
                rule="R1",
                path=_rel_path(path),
                location="<file>",
                message=f"YAML 解析失败：{exc}",
            )
        )
        return None
    except OSError as exc:
        report.add(
            LintFinding(
                rule="R1",
                path=_rel_path(path),
                location="<file>",
                message=f"无法读取文件：{exc}",
            )
        )
        return None


def _collect_template_ids(data: Any, path: Path, report: LintReport) -> list[tuple[str, str]]:
    """返回 [(template_id, location)]，用于 R3 跨文件去重。"""
    results: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return results
    items = data.get("templates")
    if not isinstance(items, list):
        return results
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        tid = item.get("template_id")
        if isinstance(tid, str) and tid.strip():
            results.append((tid.strip(), f"templates[{idx}]"))
        else:
            report.add(
                LintFinding(
                    rule="R3",
                    path=_rel_path(path),
                    location=f"templates[{idx}]",
                    message="缺失或非字符串的 template_id 字段",
                )
            )
    return results


# ---------------------------------------------------------------------------
# R2：占位符良好性
# ---------------------------------------------------------------------------


_FORMATTER = string.Formatter()


def _lint_placeholders(text: str, path: Path, location: str, report: LintReport) -> None:
    """使用 string.Formatter 解析，验证 ``{...}`` 的合法性。"""
    try:
        parsed = list(_FORMATTER.parse(text))
    except ValueError as exc:
        report.add(
            LintFinding(
                rule="R2",
                path=_rel_path(path),
                location=location,
                message=f"占位符语法错误：{exc}",
            )
        )
        return

    for literal_text, field_name, format_spec, conversion in parsed:
        # Formatter.parse 会把 `{{ }}` 正确转义为 literal_text，不产生 field_name。
        if field_name is None:
            continue
        stripped = field_name.strip()
        if stripped == "":
            # `{}`（位置参数占位符）：在模板中"通常"不允许，因为 format_map 不支持
            # 位置参数。但部分 prompt 文案会在中文描述里写 `({})` 表示"空对象"字面量，
            # 从未被 .format() 渲染。因此降为 warning，让作者主动确认而非阻塞 CI。
            report.add(
                LintFinding(
                    rule="R2",
                    path=_rel_path(path),
                    location=location,
                    message=(
                        "出现空占位符 `{}`。若此字符串会被 .format() 渲染将抛 IndexError；"
                        "若仅是字面量描述，请改用 `{{}}` 双花括号转义。"
                    ),
                    severity="warning",
                )
            )
            continue
        # 进一步用 get_field 验证 `{var.attr}` / `{var[0]}` 的合法结构；
        # 这里传入一个 DummyMapping，返回空对象即认为结构可解析。
        try:
            _FORMATTER.get_field(stripped, args=(), kwargs=_DummyMap())
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            # KeyError 来自 DummyMap 的 __getitem__，是"字段名本身合法"的信号——
            # 应忽略。只有 ValueError 才真正说明结构非法。
            if isinstance(exc, KeyError):
                continue
            report.add(
                LintFinding(
                    rule="R2",
                    path=_rel_path(path),
                    location=location,
                    message=f"占位符 `{{{stripped}}}` 结构非法：{exc}",
                )
            )


class _DummyMap(dict):  # type: ignore[type-arg]
    """format_map 友好的空映射；任何 key 都返回空字符串。"""

    def __getitem__(self, key: str) -> str:  # noqa: D401 - trivial
        # 对 string.Formatter.get_field 的约定：查不到字段抛 KeyError 即可。
        raise KeyError(key)


# ---------------------------------------------------------------------------
# 遍历模板内容，查找字符串字段做占位符 lint
# ---------------------------------------------------------------------------


def _walk_template_strings(
    node: Any,
    *,
    path: Path,
    location: str,
    report: LintReport,
) -> None:
    if isinstance(node, str):
        if "{" in node or "}" in node:
            _lint_placeholders(node, path, location, report)
        return
    if isinstance(node, list):
        for idx, item in enumerate(node):
            _walk_template_strings(
                item, path=path, location=f"{location}[{idx}]", report=report
            )
        return
    if isinstance(node, dict):
        for key, value in node.items():
            _walk_template_strings(
                value, path=path, location=f"{location}.{key}", report=report
            )


def _lint_document(data: Any, path: Path, report: LintReport) -> None:
    if not isinstance(data, dict):
        return
    templates = data.get("templates")
    if not isinstance(templates, list):
        return
    for idx, item in enumerate(templates):
        if not isinstance(item, dict):
            continue
        content = item.get("default_content")
        if content is None:
            continue
        _walk_template_strings(
            content,
            path=path,
            location=f"templates[{idx}].default_content",
            report=report,
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def lint_paths(paths: Iterable[Path]) -> LintReport:
    report = LintReport()

    id_registry: dict[str, tuple[str, str]] = {}

    for yaml_path in _iter_yaml_files(paths):
        data = _load_yaml(yaml_path, report)
        if data is None:
            continue
        # R2：占位符良好性
        _lint_document(data, yaml_path, report)
        # R3：模板 ID 唯一性
        rel = _rel_path(yaml_path)
        for tid, loc in _collect_template_ids(data, yaml_path, report):
            if tid in id_registry:
                prev_file, prev_loc = id_registry[tid]
                report.add(
                    LintFinding(
                        rule="R3",
                        path=rel,
                        location=loc,
                        message=(
                            f"template_id '{tid}' 重复，先前出现于 "
                            f"{prev_file} ({prev_loc})。PromptTemplateStore 会静默"
                            "覆盖，必须重命名其中一处。"
                        ),
                    )
                )
            else:
                id_registry[tid] = (rel, loc)

    return report


def _format_human(report: LintReport) -> str:
    if report.ok and not report.findings:
        return "prompts lint: OK（未发现问题）"
    lines: list[str] = []
    if report.ok:
        lines.append("prompts lint: OK（仅警告）")
    else:
        lines.append(
            f"prompts lint: FAIL（errors={report.as_dict()['error_count']}, "
            f"warnings={report.as_dict()['warn_count']}）"
        )
    for f in report.findings:
        lines.append(f"  [{f.severity.upper()}][{f.rule}] {f.path}::{f.location}  {f.message}")
    return "\n".join(lines)


def _promote_warnings_to_errors(report: LintReport) -> LintReport:
    """返回新的 LintReport，所有 severity=='warning' 的 finding 被升级为 'error'.

    OPT-6 二期前置：供 CI 灰度开关使用。一期我们对"空 ``{}``"等判定放宽为
    warning 避免误伤文档式字面量，二期把 `--strict` 打开后与 CI 一起收严。
    """
    promoted = LintReport()
    for f in report.findings:
        if f.severity != "error":
            promoted.add(
                LintFinding(
                    rule=f.rule,
                    path=f.path,
                    location=f.location,
                    message=f.message + "（--strict 模式下升为 error）",
                    severity="error",
                )
            )
        else:
            promoted.add(f)
    return promoted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "paths",
        nargs="*",
        help="要 lint 的路径；省略则默认扫描 prompts/ 目录",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "严格模式：将所有 warning 升级为 error，退出码反映升级结果。"
            "OPT-6 二期 CI Gate 将默认启用；本地开发请先不加以观察 warning。"
        ),
    )
    args = parser.parse_args(argv)

    roots: list[Path]
    if args.paths:
        roots = [Path(p).resolve() for p in args.paths]
    else:
        roots = [DEFAULT_SCAN_ROOT]

    report = lint_paths(roots)
    if args.strict:
        report = _promote_warnings_to_errors(report)

    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(_format_human(report))

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
