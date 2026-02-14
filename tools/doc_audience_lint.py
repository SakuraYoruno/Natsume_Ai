#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档受众门禁：防止 MASTER / PROJECT / CODEX 的“指令”混写

目标：允许在解释里“提到”其它角色，但禁止出现“直接驱动其它角色执行”的命令句式。
实践上，我们主要拦截：
- “请/让 + Codex/Project”
- 以 “Codex:” 或 “Project:” 开头的命令段落（更像对话指令）

规则：
1）每个 .md 首行必须包含：受众：MASTER|CODEX|PROJECT（或 Audience:）
2）路径与受众必须匹配
3）受众对应的“指令型模式”不得出现

用法：
  python tools/doc_audience_lint.py [root_dir]
返回码：
  0 通过；1 失败
"""

from __future__ import annotations
import sys, re
from pathlib import Path

AUD_RE = re.compile(r'^(?:受众|Audience)\s*[:：]\s*(MASTER|CODEX|PROJECT)\s*$', re.IGNORECASE)

PATH_POLICY = [
    ("docs/10_whitepaper", {"MASTER", "PROJECT"}),
    ("docs/20_prd", {"MASTER", "PROJECT"}),
    ("docs/30_devspec", {"CODEX"}),
    ("docs/40_agents", {"CODEX"}),
    ("docs/50_project", {"PROJECT"}),
    ("docs/60_trace", {"MASTER", "PROJECT"}),
    ("prompts", {"MASTER"}),
]

def guess_allowed_audience(rel_posix: str) -> set[str] | None:
    for prefix, allowed in PATH_POLICY:
        if rel_posix.startswith(prefix + "/") or rel_posix == prefix:
            return set(allowed)
    return None

def read_first_nonempty_line(p: Path) -> str:
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                return s
    return ""

FORBIDDEN_PATTERNS: dict[str, list[re.Pattern]] = {
    "MASTER": [
        re.compile(r"(请|让)\s*Codex\b", re.IGNORECASE),
        re.compile(r"(请|让)\s*codex\b", re.IGNORECASE),
        re.compile(r"(请|让)\s*Project\b", re.IGNORECASE),
        re.compile(r"^\s*Codex\s*[:：]", re.IGNORECASE | re.MULTILINE),
        re.compile(r"^\s*Project\s*[:：]", re.IGNORECASE | re.MULTILINE),
    ],
    "PROJECT": [
        re.compile(r"\b(git|commit|patch|diff|pytest|npm|pnpm|yarn|make)\b", re.IGNORECASE),
        re.compile(r"(请|让)\s*Codex\b", re.IGNORECASE),
        re.compile(r"(请|让)\s*codex\b", re.IGNORECASE),
        re.compile(r"^\s*Codex\s*[:：]", re.IGNORECASE | re.MULTILINE),
    ],
    "CODEX": [
        re.compile(r"(请|让)\s*Project\b", re.IGNORECASE),
        re.compile(r"^\s*Project\s*[:：]", re.IGNORECASE | re.MULTILINE),
    ],
}

def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    if not root.exists():
        print(f"[lint] root not found: {root}", file=sys.stderr)
        return 1

    md_files = [
        p for p in root.rglob("*.md")
        if ".git" not in p.parts and "node_modules" not in p.parts
    ]

    errors: list[str] = []
    for p in md_files:
        rel = p.relative_to(root).as_posix()
        first = read_first_nonempty_line(p)
        m = AUD_RE.match(first)
        if not m:
            errors.append(f"{rel}: 首行缺少受众头（应为：受众：MASTER|CODEX|PROJECT）")
            continue

        audience = m.group(1).upper()
        allowed = guess_allowed_audience(rel)
        if allowed is not None and audience not in allowed:
            errors.append(f"{rel}: 受众={audience} 与路径策略不匹配（允许：{sorted(allowed)}）")

        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in FORBIDDEN_PATTERNS.get(audience, []):
            if pat.search(text):
                errors.append(f"{rel}: 受众={audience} 命中禁用模式：{pat.pattern}")

    if errors:
        print("[lint] 失败：发现文档混写/受众不合规\n", file=sys.stderr)
        for e in errors:
            print(" - " + e, file=sys.stderr)
        return 1

    print("[lint] 通过：未发现受众混写问题")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
